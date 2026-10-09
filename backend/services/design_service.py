"""Design service — author, check and export full CADL designs.

Wraps the upstream ``cadl`` package (PyPI: ``cadl-lang``): parsing, type
checking, verification, deadlock detection and lowering to the simulator
IR. The Designer page goes through this module only.

Three groups of functions:
  analysis  — analyze(), list_examples(), new_design()
  editing   — load_doc() / dump_doc() and the table helpers used by forms
  hand-off  — diff_designs(), to_explorer_yaml(), export_*()
"""

from __future__ import annotations

import copy
import difflib
import html
import io
import json
import os
import re
import tempfile
import zipfile
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

from backend.services.cadl_service import MAX_SOURCE_CHARS, _check_source_text

DESIGNS_DIR = Path(__file__).resolve().parents[2] / "designs"

SOS_TYPES = ["Directed", "Acknowledged", "Collaborative", "Virtual"]
AUTONOMY_LEVELS = ["low", "medium", "high"]
SEVERITIES = ["Minor", "Major", "Critical"]
CODEGEN_TARGETS = {
    "python": "Python runtime",
    "unity-csharp": "Unity C#",
    "opa": "OPA policy (Rego)",
    "solidity": "Solidity contracts",
}
SIM_TARGETS = {"unity": "Unity", "python": "Python", "go": "Go"}
VERIFICATION_TYPES = ["consistency", "deadlock", "safety", "liveness"]
VERIFICATION_METHODS = ["smt", "model_check", "simulation", "proof"]

# The text comes from a public page, so the number of solver calls is
# bounded as well as the text size (each Z3 query has a 5 s timeout).
MAX_CONTRACTS = 20
MAX_ACTORS = 50

NEW_DESIGN = """\
sos:
  name: "MySoS"
  type: Acknowledged
  version: "0.1.0"
  description: "Describe the system of systems here."

  actors:
    - id: COORDINATOR
      role: "coordinator"
      autonomy: low
      capabilities: [assign_tasks]
      interface:
        input:  [status_report]
        output: [task_assignment]

    - id: "WORKER[1..N]"
      role: "worker"
      autonomy: high
      capabilities: [do_task]
      interface:
        input:  [task_assignment]
        output: [status_report]

  contracts:
    - id: SERVICE_AGREEMENT
      parties:
        - COORDINATOR
        - "WORKER[*]"
      assume:
        - "WORKER[i].battery > 20"
      guarantee:
        - "completion_time <= 300s"
      authority:
        decision_holder: COORDINATOR
        beta: 0.7
      information:
        alpha: 0.5
      incentives:
        type: task_completion
        lambda: 0.3
      lifecycle:
        states: [Proposed, Active, Completed, Violated]
        initial: Proposed
        terminal: [Completed, Violated]
        transitions:
          - id: start
            from: Proposed
            to: Active
            on: "COORDINATOR -> WORKER[i] : task_assignment"
          - id: finish
            from: Active
            to: Completed
            on: "WORKER[i].status == Done"
            deadline: 300s
            on_violation:
              transition: Violated
              severity: Major
"""


# ── Analysis ────────────────────────────────────────────────────────


@dataclass
class Finding:
    """One diagnostic from any stage of the toolchain."""
    level: str    # "error" | "warning" | "info" | "pass"
    stage: str    # one of STAGES
    title: str
    message: str = ""
    section: str = ""   # form section that fixes it, e.g. "Contracts"
    item: str = ""      # id within that section, e.g. a contract id
    element: str = ""   # finer element, e.g. a lifecycle state name


STAGES = [
    "Parse", "Type check", "Expressions", "Verification", "Lifecycle",
    "Interfaces", "Consistency", "Deadlock", "Simulator IR",
]

# What each stage looks at, shown next to the results so a green page
# is not read as more than it is.
STAGE_SCOPE = {
    "Parse": "The source is valid YAML with the CADL sections in the right shape.",
    "Type check": "Actors, contracts and protocols refer to things that exist; ids are unique; "
                  "α / β / λ are within 0–1.",
    "Expressions": "Every assume, guarantee, condition, rule and formula parses as a CADL "
                   "expression. One that does not is kept as text and skipped by verification.",
    "Verification": "For each contract, Z3 checks that assume and guarantee can hold together "
                    "and that the assumptions are satisfiable.",
    "Lifecycle": "Each contract lifecycle uses only declared states, every state is reachable "
                 "and no non-terminal state is a dead end.",
    "Interfaces": "Messages sent in protocols are declared in the sender's output and the "
                  "receiver's input; autonomy and severity values are known ones.",
    "Consistency": "Cross-checks between sections: protocol time bounds against contract "
                   "guarantees, decision holders, actors outside every contract.",
    "Deadlock": "Protocols have no circular wait between actors.",
    "Simulator IR": "The design can be lowered to the simulator IR.",
}

NOT_CHECKED = [
    "Whether a guarantee actually holds when the system runs — verification checks that "
    "contracts are satisfiable, not that an implementation meets them.",
    "Free-text fields: incentive rules, information views, escalation, fallback actions.",
    "Functions used in expressions, such as `all_routes_conflict_free()`: they are treated "
    "as unknown predicates.",
    "Timing of lifecycle deadlines against protocol time bounds.",
]


@dataclass
class DesignAnalysis:
    """Result of running the CADL toolchain on one source text."""
    findings: List[Finding] = field(default_factory=list)
    ir: Optional[dict] = None       # simulator IR as a plain dict
    name: str = ""
    sos_type: str = ""
    has_regimes: bool = False       # top-level `transitions:` present
    # Editor gutter markers: {"row": 0-based line, "type": "error"|"warning", "text": ...}
    annotations: List[dict] = field(default_factory=list)

    @property
    def parsed(self) -> bool:
        return self.ir is not None

    def count(self, level: str, stage: Optional[str] = None) -> int:
        return sum(
            1 for f in self.findings
            if f.level == level and (stage is None or f.stage == stage)
        )

    @property
    def ok(self) -> bool:
        return self.parsed and self.count("error") == 0


def _ir_to_dict(ir) -> dict:
    d = asdict(ir)
    # Rename lambda_ back to lambda, as `cadl sim-ir` does.
    for c in d.get("institution", {}).get("contracts", []):
        gov = c.get("governance") or {}
        if "lambda_" in gov:
            gov["lambda"] = gov.pop("lambda_")
    return d


def _parse(source: str):
    """Parse with input limits. Raises ValueError / CADLParseError."""
    from cadl.parser import parse

    if not source.strip():
        raise ValueError("The design is empty.")
    try:
        # The anchor check scans the text, so it meets syntax errors first.
        _check_source_text(source)
        yaml.safe_load(source)
    except yaml.YAMLError as e:
        raise ValueError(_describe_yaml_error(e, source)) from e
    sos = parse(source)
    if len(sos.contracts) > MAX_CONTRACTS:
        raise ValueError(f"Too many contracts ({len(sos.contracts)}; limit {MAX_CONTRACTS}).")
    if len(sos.actors) > MAX_ACTORS:
        raise ValueError(f"Too many actors ({len(sos.actors)}; limit {MAX_ACTORS}).")
    return sos


def _describe_yaml_error(error: yaml.YAMLError, source: str) -> str:
    """A YAML error as 'Line N, column M: problem' followed by the offending line."""
    mark = getattr(error, "problem_mark", None)
    problem = getattr(error, "problem", None) or str(error)
    if mark is None:
        return f"YAML syntax error: {problem}"
    lines = source.splitlines()
    text = lines[mark.line].rstrip() if mark.line < len(lines) else ""
    hint = ""
    if "mapping values are not allowed" in problem or "expected <block end>" in problem:
        hint = " Check the indentation, and quote values that contain `: `."
    elif "expected '<document start>'" in problem:
        hint = " This line is not indented under a key; check the indentation above it."
    elif "alias" in str(error) or "expected ',' or ']'" in problem or "flow" in str(error):
        hint = (" An unclosed `[`, `{` or quote on an earlier line can cause this; "
                "the mistake may be above the line shown.")
    return (f"Line {mark.line + 1}, column {mark.column + 1}: {problem}.{hint}"
            + (f"\n\n    {mark.line + 1} | {text}" if text.strip() else ""))


def analyze(source: str) -> DesignAnalysis:
    """Parse, type-check, verify and lower a CADL source text.

    Never raises: every problem becomes a Finding.
    """
    from cadl.deadlock import detect_deadlocks
    from cadl.sim import lower_to_ir, validate_ir
    from cadl.type_checker import type_check
    from cadl.verifier import verify

    result = DesignAnalysis()
    try:
        sos = _parse(source)
    except Exception as e:
        result.findings.append(Finding("error", "Parse", "Cannot parse the design", str(e)))
        mark = getattr(e.__cause__, "problem_mark", None)
        if mark is not None:
            result.annotations.append({
                "row": mark.line, "column": mark.column, "type": "error",
                "text": getattr(e.__cause__, "problem", None) or "YAML syntax error",
            })
        return result

    result.name = sos.name
    result.sos_type = sos.type.value
    result.has_regimes = bool(sos.transitions)
    result.findings.append(Finding("pass", "Parse", "Syntax is valid"))
    doc = yaml.safe_load(source)
    sos_doc = doc.get("sos") if isinstance(doc.get("sos"), dict) else {}

    def _loc(item) -> str:
        return f" (line {item.loc.line})" if getattr(item, "loc", None) else ""

    tc = type_check(sos)
    for e in tc.errors:
        result.findings.append(Finding("error", "Type check", e.message + _loc(e)))
    for w in tc.warnings:
        result.findings.append(Finding("warning", "Type check", w.message + _loc(w)))
    for item, kind in [(e, "error") for e in tc.errors] + [(w, "warning") for w in tc.warnings]:
        if getattr(item, "loc", None):
            result.annotations.append({
                "row": max(item.loc.line - 1, 0), "column": 0, "type": kind, "text": item.message})
    if tc.ok:
        result.findings.append(Finding("pass", "Type check", "References and types are consistent"))

    expression_findings = check_expressions(sos_doc)
    result.findings.extend(expression_findings or [
        Finding("pass", "Expressions", "All expressions parse")])

    # Verification and deadlock detection assume a well-typed design.
    if tc.ok:
        levels = {"passed": "pass", "failed": "error", "unknown": "warning",
                  "info": "info", "not_supported": "info", "warning": "warning"}
        try:
            for v in verify(sos):
                message = v.message
                if v.counterexample:
                    message += f" Counterexample: {v.counterexample}"
                level = levels.get(v.status, "info")
                if level == "error" and "consistency" in v.check_name:
                    pair = _conflicting_pair(doc, v.check_name)
                    if pair:
                        message += f" These two cannot both hold: `{pair[0]}` and `{pair[1]}`."
                result.findings.append(Finding(level, "Verification", v.check_name, message))
        except Exception as e:
            result.findings.append(
                Finding("warning", "Verification", "Verification could not run", str(e)))
        try:
            for d in detect_deadlocks(sos):
                message = d.message
                if d.details:
                    message += " " + "; ".join(d.details)
                result.findings.append(
                    Finding(levels.get(d.status, "info"), "Deadlock", d.check_name, message))
        except Exception as e:
            result.findings.append(
                Finding("warning", "Deadlock", "Deadlock detection could not run", str(e)))

    try:
        ir = lower_to_ir(sos)
        # The IR validator repeats what the type checker already reported.
        reported = {
            name for f in result.findings if f.stage == "Type check"
            for name in re.findall(r"'([^']+)'", f.title)
        }
        for err in validate_ir(ir):
            names = re.findall(r"'([^']+)'", err)
            if not (names and names[0] in reported):
                result.findings.append(Finding("warning", "Simulator IR", err))
        result.ir = _ir_to_dict(ir)
        result.findings.extend(check_lifecycles(result.ir))
        interface_findings = check_interfaces(sos_doc, result.ir)
        result.findings.extend(interface_findings or [
            Finding("pass", "Interfaces", "Messages and values match their declarations")])
        consistency_findings = check_consistency(sos_doc, result.ir)
        result.findings.extend(consistency_findings or [
            Finding("pass", "Consistency", "No conflicts between sections")])
    except Exception as e:
        result.findings.append(
            Finding("error", "Simulator IR", "Cannot lower the design to the simulator IR", str(e)))
    for finding in result.findings:
        _locate(finding)
    return result


def _locate(finding: Finding) -> None:
    """Fill in the form section and item an upstream finding is about."""
    if finding.section:
        return
    text = f"{finding.title} {finding.message}"
    patterns = [
        (r"^(?:Transition|Regime)\b", "Regimes"),
        (r"[Cc]ontract '([^']+)'", "Contracts"),
        (r"[Pp]rotocol '([^']+)'", "Protocols"),
        (r"Metric '([^']+)'", "Metrics"),
        (r"[Aa]ctor(?: ID:)? '([^']+)'", "Actors"),
    ]
    for pattern, section in patterns:
        match = re.search(pattern, text)
        if match:
            finding.section = section
            finding.item = match.group(1) if match.groups() else ""
            if section == "Regimes":
                # The transition, regime or protocol the finding names, for the regime map.
                edge = re.match(r"Transition (\S+?)->(\S+)", text)
                named = re.search(r"'([^']+)'", text)
                finding.element = (f"{edge.group(1)} → {edge.group(2)}" if edge
                                   else named.group(1) if named else "")
            return


# Where each kind of finding is best seen: (view, session key that selects the item).
_VIEW_OF = {
    "Actors": ("Architecture", None),
    "Contracts": ("Architecture", None),
    "Protocols": ("Protocols", "design_view_protocol"),
    "Regimes": ("Regimes", None),
    "Metrics": ("Algorithms & metrics", None),
    "Algorithms": ("Algorithms & metrics", None),
}


def view_of(finding: Finding) -> Optional[tuple]:
    """(view, selection key or None) showing the finding in a diagram or table."""
    if finding.stage == "Lifecycle" and finding.item:
        return "Lifecycle", "design_life_contract"
    return _VIEW_OF.get(finding.section)


def flagged(analysis: DesignAnalysis) -> Dict[str, Dict[str, str]]:
    """Items with problems, per section: {"Contracts": {"C1": "error"}}."""
    out: Dict[str, Dict[str, str]] = {}
    for f in analysis.findings:
        if f.level in ("error", "warning") and f.section and f.item:
            current = out.setdefault(f.section, {}).get(f.item)
            if current != "error":
                out[f.section][f.item] = f.level
    return out


def _try_expr(text: Any) -> Optional[str]:
    """None when ``text`` parses as a CADL expression, else a short reason."""
    from cadl.parser import parse_expr

    try:
        parse_expr(str(text))
        return None
    except Exception as e:
        reason = str(e).strip().splitlines()[0] if str(e).strip() else type(e).__name__
        return reason[:120]


def check_expressions(sos: dict) -> List[Finding]:
    """Report expressions the CADL parser cannot read.

    The upstream parser keeps such text as an opaque string, so the
    verifier silently skips it; a contract can then look verified while
    one of its conditions was never considered.
    """
    findings: List[Finding] = []

    def check(text: Any, where: str, section: str, item: str, verified: bool = False):
        if text in (None, ""):
            return
        reason = _try_expr(text)
        if reason is None:
            return
        consequence = (
            " It is kept as text, so verification does not take it into account."
            if verified else " It is kept as text.")
        findings.append(Finding(
            "warning", "Expressions", f"{where} is not a valid expression",
            f"`{text}` — {reason}.{consequence}", section, item))

    for c in sos.get("contracts") or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id", ""))
        for key in ("assume", "guarantee"):
            for text in c.get(key) or []:
                check(text, f"Contract '{cid}' {key}", "Contracts", cid, verified=True)
        violation = c.get("violation") if isinstance(c.get("violation"), dict) else {}
        check(violation.get("detect"), f"Contract '{cid}' violation detect", "Contracts", cid)
        lifecycle = c.get("lifecycle") if isinstance(c.get("lifecycle"), dict) else {}
        for t in lifecycle.get("transitions") or []:
            if isinstance(t, dict):
                check(t.get("when"), f"Contract '{cid}' transition '{t.get('id')}' when",
                      "Contracts", cid)
        for m in c.get("monitors") or []:
            if isinstance(m, dict):
                check(m.get("rule"), f"Contract '{cid}' monitor '{m.get('id')}' rule",
                      "Contracts", cid)
    for p in sos.get("protocols") or []:
        if not isinstance(p, dict):
            continue
        pid = str(p.get("id", ""))
        for key in ("trigger", "precondition", "postcondition", "safety_invariant"):
            check(p.get(key), f"Protocol '{pid}' {key.replace('_', ' ')}", "Protocols", pid)
    return findings


def _message_name(content: Any) -> str:
    match = re.match(r"[A-Za-z_]\w*", str(content or ""))
    return match.group(0) if match else str(content or "")


def check_interfaces(sos: dict, ir: dict) -> List[Finding]:
    """Messages against actor interfaces, and enumerated values."""
    findings: List[Finding] = []

    for a in sos.get("actors") or []:
        if not isinstance(a, dict):
            continue
        autonomy = a.get("autonomy")
        if autonomy is not None and str(autonomy) not in AUTONOMY_LEVELS:
            findings.append(Finding(
                "error", "Interfaces", f"Actor '{a.get('id')}' has an unknown autonomy level",
                f"`{autonomy}` is not one of {', '.join(AUTONOMY_LEVELS)}; it is read as `medium`.",
                "Actors", actor_base(a.get("id", ""))))

    for c in sos.get("contracts") or []:
        if not isinstance(c, dict):
            continue
        cid = str(c.get("id", ""))
        lifecycle = c.get("lifecycle") if isinstance(c.get("lifecycle"), dict) else {}
        blocks = [(t.get("on_violation"), f"transition '{t.get('id')}'")
                  for t in lifecycle.get("transitions") or [] if isinstance(t, dict)]
        blocks += [(m.get("on_match"), f"monitor '{m.get('id')}'")
                   for m in c.get("monitors") or [] if isinstance(m, dict)]
        for block, where in blocks:
            severity = block.get("severity") if isinstance(block, dict) else None
            if severity is not None and str(severity) not in SEVERITIES:
                findings.append(Finding(
                    "warning", "Interfaces", f"Contract '{cid}' {where} has an unknown severity",
                    f"`{severity}` is not one of {', '.join(SEVERITIES)}.", "Contracts", cid))

    actors = {str(a.get("id")): a for a in (ir.get("institution") or {}).get("actors") or []}
    for p in (ir.get("protocol") or {}).get("protocols") or []:
        pid = str(p.get("id", ""))
        seen = set()
        for step in p.get("steps") or []:
            if step.get("type") != "message":
                continue
            name = _message_name(step.get("content"))
            for ref, side, word in ((step.get("sender"), "outputs", "output"),
                                    (step.get("receiver"), "inputs", "input")):
                actor = actors.get(actor_base(ref or ""))
                declared = (actor or {}).get(side) or []
                if actor and declared and name not in declared and (actor["id"], side, name) not in seen:
                    seen.add((actor["id"], side, name))
                    findings.append(Finding(
                        "warning", "Interfaces",
                        f"Protocol '{pid}' uses a message {actor['id']} does not declare",
                        f"`{name}` is not in the {word} messages of {actor['id']} "
                        f"({', '.join(declared)}). Add it to the actor's interface or fix the step.",
                        "Protocols", pid, name))
    return findings


_DURATION_UNITS = {"ms": 1, "s": 1000, "sec": 1000, "min": 60_000, "m": 60_000, "h": 3_600_000}


def duration_ms(text: Any) -> Optional[int]:
    """'5s' -> 5000, '200ms' -> 200; None when it is not a duration."""
    match = re.fullmatch(r"\s*(\d+(?:\.\d+)?)\s*(ms|sec|min|s|m|h)\s*", str(text or ""))
    return int(float(match.group(1)) * _DURATION_UNITS[match.group(2)]) if match else None


def _format_ms(ms: int) -> str:
    return f"{ms // 1000}s" if ms % 1000 == 0 else f"{ms}ms"


def check_consistency(sos: dict, ir: dict) -> List[Finding]:
    """Cross-checks between sections of the design."""
    findings: List[Finding] = []
    institution = ir.get("institution") or {}
    actors = [str(a.get("id")) for a in institution.get("actors") or []]
    contracts = institution.get("contracts") or []
    protocols = (ir.get("protocol") or {}).get("protocols") or []

    in_contract = {actor_base(p) for c in contracts for p in c.get("parties") or []}
    for actor in actors:
        if contracts and actor not in in_contract:
            findings.append(Finding(
                "warning", "Consistency", f"Actor '{actor}' is not a party to any contract",
                "No contract constrains what it may or must do.", "Actors", actor))

    participants: Dict[str, set] = {}
    for p in protocols:
        participants[str(p.get("id"))] = {
            actor_base(ref) for step in p.get("steps") or []
            for ref in (step.get("sender"), step.get("receiver")) if ref
        }
    in_protocol = set().union(*participants.values()) if participants else set()
    for actor in actors:
        if protocols and actor not in in_protocol:
            findings.append(Finding(
                "info", "Consistency", f"Actor '{actor}' takes part in no protocol",
                "It never sends, receives or computes in any protocol step.", "Actors", actor))

    for c in contracts:
        cid = str(c.get("id", ""))
        parties = {actor_base(p) for p in c.get("parties") or []}
        holder = actor_base((c.get("governance") or {}).get("decision_holder") or "")
        if holder and holder not in parties:
            findings.append(Finding(
                "warning", "Consistency", f"Contract '{cid}' decision holder is not a party",
                f"`{holder}` decides under this contract but is not bound by it.", "Contracts", cid))
        if not holder and len(parties) > 1:
            findings.append(Finding(
                "info", "Consistency", f"Contract '{cid}' names no decision holder",
                "Nobody is identified as deciding under this contract.", "Contracts", cid))

        # A guarantee "x <= 300s" against the time bounds of protocols run
        # entirely by this contract's parties.
        for text in c.get("guarantee") or []:
            match = re.fullmatch(r"\s*([A-Za-z_][\w.\[\]]*)\s*(<=|<)\s*(\S+)\s*", str(text))
            bound = duration_ms(match.group(3)) if match else None
            if bound is None:
                continue
            for p in protocols:
                pid = str(p.get("id"))
                if not participants.get(pid) or not participants[pid] <= parties:
                    continue
                total = duration_ms((p.get("timing") or {}).get("max_total"))
                if total is not None and total > bound:
                    findings.append(Finding(
                        "warning", "Consistency",
                        f"Protocol '{pid}' may outlast a guarantee of contract '{cid}'",
                        f"`{text}` allows {_format_ms(bound)}, but the protocol's `max_total` is "
                        f"{_format_ms(total)}.", "Protocols", pid))

    for p in protocols:
        pid = str(p.get("id"))
        timing = p.get("timing") or {}
        total = duration_ms(timing.get("max_total"))
        for name, value in timing.items():
            part = duration_ms(value)
            if name != "max_total" and total is not None and part is not None and part > total:
                findings.append(Finding(
                    "warning", "Consistency", f"Protocol '{pid}' has inconsistent time bounds",
                    f"`{name}` ({_format_ms(part)}) is longer than `max_total` ({_format_ms(total)}).",
                    "Protocols", pid))

    findings.extend(_check_metrics(contracts, ir.get("metrics") or []))

    used = set()
    for t in ir.get("transitions") or []:
        if t.get("protocol"):
            used.add(str(t["protocol"]))
    if (ir.get("transitions") or []) and protocols:
        for p in protocols:
            pid = str(p.get("id"))
            if pid not in used and not p.get("trigger"):
                findings.append(Finding(
                    "info", "Consistency", f"Protocol '{pid}' is never started",
                    "It has no trigger and no regime transition refers to it.", "Protocols", pid))
    return findings


_BOUND = re.compile(r"\s*(<=|>=|==|<|>)\s*(-?\d+(?:\.\d+)?)\s*([A-Za-z%]*)")


def _bound(text: Any) -> Optional[tuple]:
    """'<= 300s' -> ('<=', 300000.0); None when it is not a simple bound.

    Durations are converted to milliseconds so `5s` and `5000ms` compare.
    """
    match = _BOUND.match(str(text or ""))
    if not match:
        return None
    op, number, unit = match.group(1), float(match.group(2)), match.group(3)
    return op, number * _DURATION_UNITS.get(unit, 1)


def _guaranteed_bounds(contract: dict) -> List[tuple]:
    """(quantity, op, value, text) for each guarantee of the form `quantity op value`."""
    out = []
    for text in contract.get("guarantee") or []:
        match = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*((?:<=|>=|==|<|>).*)", str(text))
        bound = _bound(match.group(2)) if match else None
        if bound:
            out.append((match.group(1), bound[0], bound[1], str(text)))
    return out


def _check_metrics(contracts: list, metrics: list) -> List[Finding]:
    """Metrics against what the contracts guarantee."""
    findings: List[Finding] = []
    by_id = {str(m.get("id")): m for m in metrics}

    for m in metrics:
        mid = str(m.get("id", ""))
        if not m.get("target"):
            findings.append(Finding(
                "info", "Consistency", f"Metric '{mid}' has no target",
                "It is measured but nothing says what value is acceptable.", "Metrics", mid))

    for c in contracts:
        cid = str(c.get("id", ""))
        unmeasured = []
        for quantity, op, value, text in _guaranteed_bounds(c):
            metric = by_id.get(quantity)
            if metric is None:
                # A metric whose formula mentions the quantity also measures it.
                if not any(re.search(rf"\b{re.escape(quantity)}\b", str(m.get("formula") or ""))
                           for m in metrics):
                    unmeasured.append(quantity)
                continue
            target = _bound(metric.get("target"))
            if target is None:
                continue
            t_op, t_value = target
            upper, lower = ("<=", "<"), (">=", ">")
            weaker = (
                (op in upper and t_op in upper and t_value > value)
                or (op in lower and t_op in lower and t_value < value)
                or (op == "==" and t_op != "==" and t_value != value)
            )
            opposed = (op in upper and t_op in lower and t_value > value) or (
                op in lower and t_op in upper and t_value < value)
            if opposed:
                findings.append(Finding(
                    "warning", "Consistency",
                    f"Metric '{quantity}' target contradicts contract '{cid}'",
                    f"The contract guarantees `{text}`, but the metric's target is "
                    f"`{metric.get('target')}`. Both cannot be met.", "Metrics", quantity))
            elif weaker:
                findings.append(Finding(
                    "warning", "Consistency",
                    f"Metric '{quantity}' target is weaker than contract '{cid}'",
                    f"The contract guarantees `{text}`, but the metric accepts "
                    f"`{metric.get('target')}`; a run can meet the target and still break "
                    "the contract.", "Metrics", quantity))
        if unmeasured and metrics:
            findings.append(Finding(
                "info", "Consistency", f"Contract '{cid}' guarantees quantities no metric measures",
                ", ".join(f"`{q}`" for q in unmeasured)
                + " — no metric has that id or uses it in its formula.", "Contracts", cid))
    return findings


def _conflicting_pair(doc: dict, check_name: str) -> Optional[tuple]:
    """Two conditions of a contract that cannot hold together, if a pair explains it."""
    from cadl.parser import parse
    from cadl.verifier import verify

    match = re.search(r"'([^']+)'", check_name)
    contracts = (doc.get("sos") or {}).get("contracts") or []
    contract = next((c for c in contracts
                     if isinstance(c, dict) and match and str(c.get("id")) == match.group(1)), None)
    if contract is None:
        return None
    conditions = [str(x) for x in (contract.get("assume") or []) + (contract.get("guarantee") or [])]
    if not 2 <= len(conditions) <= 8:
        return None
    for i in range(len(conditions)):
        for j in range(i + 1, len(conditions)):
            probe = copy.deepcopy(doc)
            probe["sos"]["contracts"] = [
                {"id": contract["id"], "parties": contract.get("parties") or [],
                 "guarantee": [conditions[i], conditions[j]]}
            ]
            for key in ("protocols", "transitions", "metrics", "verification"):
                probe["sos"].pop(key, None)
            try:
                results = verify(parse(dump_doc(probe)))
            except Exception:
                return None
            if any(r.status == "failed" and "consistency" in r.check_name for r in results):
                return conditions[i], conditions[j]
    return None


def check_lifecycles(ir: dict) -> List[Finding]:
    """Structural checks on each contract's lifecycle state machine.

    The upstream toolchain accepts any state names, so undefined,
    unreachable and dead-end states are reported here.
    """
    findings: List[Finding] = []
    for c in (ir.get("institution") or {}).get("contracts") or []:
        lc = c.get("lifecycle")
        if not lc:
            continue
        cid = c.get("id", "?")
        states = [str(s) for s in lc.get("states") or []]
        known = set(states)
        terminal = {str(s) for s in lc.get("terminal") or []}
        initial = lc.get("initial")
        transitions = lc.get("transitions") or []
        problems = 0

        def error(message: str, state: str = ""):
            nonlocal problems
            problems += 1
            findings.append(Finding("error", "Lifecycle", f"Contract '{cid}' lifecycle", message,
                                    "Contracts", str(cid), str(state or "")))

        def warn(message: str, state: str = ""):
            nonlocal problems
            problems += 1
            findings.append(Finding("warning", "Lifecycle", f"Contract '{cid}' lifecycle", message,
                                    "Contracts", str(cid), str(state or "")))

        if not states:
            error("No states are declared.")
            continue
        if not initial:
            error("No initial state is set.")
        elif initial not in known:
            error(f"Initial state '{initial}' is not one of the declared states.")
        for s in sorted(terminal - known):
            error(f"Terminal state '{s}' is not one of the declared states.")

        edges: Dict[str, set] = {s: set() for s in states}
        for t in transitions:
            tid = t.get("id", "?")
            targets = [t.get("to_state"), t.get("on_violation_transition")]
            for target in [x for x in targets if x]:
                if target not in known:
                    error(f"Transition '{tid}' goes to undeclared state '{target}'.")
            for src in t.get("from_states") or []:
                if src not in known:
                    error(f"Transition '{tid}' starts from undeclared state '{src}'.")
                    continue
                if src in terminal:
                    warn(f"Transition '{tid}' leaves terminal state '{src}'.", src)
                edges[src].update(x for x in targets if x in known)
        for m in c.get("monitors") or []:
            target = m.get("on_match_transition")
            if target and target not in known:
                error(f"Monitor '{m.get('id', '?')}' moves to undeclared state '{target}'.")

        if initial in known:
            # Monitors can move the contract from any state to their target.
            monitor_targets = {
                m.get("on_match_transition") for m in c.get("monitors") or []
            } & known
            reached, frontier = {initial}, [initial]
            while frontier:
                for nxt in edges[frontier.pop()] | monitor_targets:
                    if nxt not in reached:
                        reached.add(nxt)
                        frontier.append(nxt)
            for s in states:
                if s not in reached:
                    warn(f"State '{s}' cannot be reached from the initial state '{initial}'.", s)
                elif s not in terminal and not edges[s] and not monitor_targets:
                    warn(f"State '{s}' is not terminal but has no way out.", s)
        if not terminal & known:
            warn("No terminal state is declared, so a contract instance never ends.")

        if not problems:
            findings.append(Finding(
                "pass", "Lifecycle", f"Contract '{cid}' lifecycle",
                f"{len(states)} states, all reachable, every transition uses declared states"))
    return findings


def list_examples() -> Dict[str, Path]:
    """Bundled example designs, keyed by display name."""
    if not DESIGNS_DIR.exists():
        return {}
    return {p.stem.replace("_", " "): p for p in sorted(DESIGNS_DIR.glob("*.cadl"))}


def load_example(name: str) -> str:
    return list_examples()[name].read_text(encoding="utf-8")


# ── Editing: the YAML document behind the forms ─────────────────────
#
# CADL source is YAML, so forms edit the parsed document and write it
# back with dump_doc(). Comments in the source do not survive that.


def load_doc(source: str) -> dict:
    """Parse the source into its YAML document. Raises ValueError if unusable."""
    _check_source_text(source)
    try:
        doc = yaml.safe_load(source)
    except yaml.YAMLError as e:
        raise ValueError(f"YAML syntax error: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("sos"), dict):
        raise ValueError("The design needs a top-level `sos:` mapping.")
    return doc


class _NoAliasDumper(yaml.SafeDumper):
    # The source checker rejects anchors and aliases, so never emit them.
    def ignore_aliases(self, data):
        return True


def dump_doc(doc: dict) -> str:
    return yaml.dump(
        doc, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True,
        default_flow_style=False, width=100,
    )


def has_comments(source: str) -> bool:
    return any(line.lstrip().startswith("#") for line in source.splitlines())


def split_list(text: Any) -> List[str]:
    """'a, b' -> ['a', 'b']; also accepts a list or None."""
    if text is None:
        return []
    if isinstance(text, (list, tuple)):
        return [str(x).strip() for x in text if str(x).strip()]
    return [part.strip() for part in str(text).split(",") if part.strip()]


def join_list(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return ", ".join(str(x) for x in value)
    return str(value)


def _clean(text: Any) -> str:
    """Normalise a table cell: None / NaN / whitespace -> ''."""
    if text is None or text != text:  # NaN != NaN
        return ""
    if isinstance(text, bool):
        return "true" if text else "false"
    return str(text).strip()


def put(mapping: dict, key: str, value: Any) -> None:
    """Set key, or remove it when the value is empty."""
    if value in ("", None, [], {}):
        mapping.pop(key, None)
    else:
        mapping[key] = value


def actor_base(ref: str) -> str:
    """'ROBOT[1..N]' / 'ROBOT[*]' -> 'ROBOT'."""
    return str(ref).split("[", 1)[0].strip()


def actors_to_rows(sos: dict) -> List[dict]:
    rows = []
    for a in sos.get("actors") or []:
        iface = a.get("interface") or {}
        rows.append({
            "id": a.get("id", ""),
            "role": a.get("role", ""),
            "autonomy": a.get("autonomy", "medium"),
            "capabilities": join_list(a.get("capabilities")),
            "input": join_list(iface.get("input")),
            "output": join_list(iface.get("output")),
        })
    return rows


def rows_to_actors(sos: dict, rows: List[dict]) -> None:
    """Write actor rows back, keeping keys the table does not show."""
    previous = {a.get("id"): a for a in sos.get("actors") or []}
    actors = []
    for row in rows:
        actor_id = _clean(row.get("id"))
        if not actor_id:
            continue
        actor = copy.deepcopy(previous.get(actor_id, {}))
        actor["id"] = actor_id
        put(actor, "role", _clean(row.get("role")))
        put(actor, "autonomy", _clean(row.get("autonomy")) or "medium")
        put(actor, "capabilities", split_list(row.get("capabilities")))
        iface = dict(actor.get("interface") or {})
        put(iface, "input", split_list(row.get("input")))
        put(iface, "output", split_list(row.get("output")))
        put(actor, "interface", iface)
        actors.append(actor)
    put(sos, "actors", actors)


def transitions_to_rows(lifecycle: dict) -> List[dict]:
    rows = []
    for t in (lifecycle or {}).get("transitions") or []:
        viol = t.get("on_violation") or {}
        rows.append({
            "id": t.get("id", ""),
            "from": join_list(t.get("from")),
            "to": t.get("to", ""),
            "on": t.get("on", ""),
            "when": t.get("when", ""),
            "deadline": _clean(t.get("deadline")),
            "on violation → state": viol.get("transition", ""),
            "severity": viol.get("severity", ""),
        })
    return rows


def rows_to_transitions(lifecycle: dict, rows: List[dict]) -> None:
    previous = {t.get("id"): t for t in lifecycle.get("transitions") or []}
    transitions = []
    for row in rows:
        tid = _clean(row.get("id"))
        to_state = _clean(row.get("to"))
        if not tid or not to_state:
            continue
        t = copy.deepcopy(previous.get(tid, {}))
        t["id"] = tid
        sources = split_list(row.get("from"))
        put(t, "from", sources[0] if len(sources) == 1 else sources)
        t["to"] = to_state
        put(t, "on", _clean(row.get("on")))
        put(t, "when", _clean(row.get("when")))
        put(t, "deadline", _clean(row.get("deadline")))
        viol = dict(t.get("on_violation") or {})
        put(viol, "transition", _clean(row.get("on violation → state")))
        put(viol, "severity", _clean(row.get("severity")) if viol.get("transition") else "")
        put(t, "on_violation", viol)
        transitions.append(t)
    put(lifecycle, "transitions", transitions)


def monitors_to_rows(contract: dict) -> List[dict]:
    rows = []
    for m in contract.get("monitors") or []:
        match = m.get("on_match") or {}
        rows.append({
            "id": m.get("id", ""),
            "observe": join_list(m.get("observe")),
            "sampling": _clean(m.get("sampling")),
            "rule": m.get("rule", ""),
            "on match → state": match.get("transition", ""),
            "on match → violation": match.get("violation", ""),
            "severity": match.get("severity", ""),
        })
    return rows


def rows_to_monitors(contract: dict, rows: List[dict]) -> None:
    previous = {m.get("id"): m for m in contract.get("monitors") or []}
    monitors = []
    for row in rows:
        mid = _clean(row.get("id"))
        if not mid:
            continue
        m = copy.deepcopy(previous.get(mid, {}))
        m["id"] = mid
        observe = split_list(row.get("observe"))
        put(m, "observe", observe[0] if len(observe) == 1 else observe)
        put(m, "sampling", _clean(row.get("sampling")))
        put(m, "rule", _clean(row.get("rule")))
        match = dict(m.get("on_match") or {})
        put(match, "transition", _clean(row.get("on match → state")))
        put(match, "violation", _clean(row.get("on match → violation")))
        put(match, "severity", _clean(row.get("severity")))
        put(m, "on_match", match)
        monitors.append(m)
    put(contract, "monitors", monitors)


def new_contract(sos: dict) -> dict:
    """A minimal contract between the first two actors, with a unique id."""
    existing = {c.get("id") for c in sos.get("contracts") or []}
    n = 1
    while f"CONTRACT_{n}" in existing:
        n += 1
    parties = []
    for a in (sos.get("actors") or [])[:2]:
        ref = str(a.get("id", ""))
        parties.append(actor_base(ref) + "[*]" if "[" in ref else ref)
    contract = {"id": f"CONTRACT_{n}", "parties": parties, "guarantee": ["true"]}
    if parties:
        contract["authority"] = {"decision_holder": actor_base(parties[0]), "beta": 0.5}
    return contract


def new_protocol(sos: dict) -> dict:
    """A minimal protocol between the first two actors, with a unique id."""
    existing = {p.get("id") for p in sos.get("protocols") or []}
    n = 1
    while f"PROTOCOL_{n}" in existing:
        n += 1
    refs = []
    for a in (sos.get("actors") or [])[:2]:
        ref = str(a.get("id", ""))
        refs.append(actor_base(ref) + "[i]" if "[" in ref else ref)
    steps = [f"{refs[0]} -> {refs[1]} : request", f"{refs[1]} : handle_request"] if len(refs) == 2 else []
    return {"id": f"PROTOCOL_{n}", "trigger": "event_occurred", "steps": steps}


def steps_to_text(steps: Any) -> tuple:
    """Protocol steps as editable text: (text, structured).

    Plain steps ("A -> B : msg", "A : compute") are shown one per line.
    Steps that nest (if / parallel / barrier) are shown as YAML instead,
    and ``structured`` is True.
    """
    steps = steps or []
    if all(isinstance(x, str) for x in steps):
        return "\n".join(steps), False
    return dump_doc(steps), True


def text_to_steps(text: str, structured: bool) -> list:
    """Inverse of steps_to_text. Raises ValueError on unusable YAML."""
    if not structured:
        return [line.strip() for line in text.splitlines() if line.strip()]
    _check_source_text(text)
    try:
        steps = yaml.safe_load(text) or []
    except yaml.YAMLError as e:
        raise ValueError(f"Steps are not valid YAML: {e}") from e
    if not isinstance(steps, list):
        raise ValueError("Steps must be a YAML list.")
    return steps


def _scalar(text: Any) -> Any:
    """'30' -> 30, 'true' -> True; anything else stays text."""
    text = _clean(text)
    try:
        value = yaml.safe_load(text)
    except yaml.YAMLError:
        return text
    return value if isinstance(value, (int, float, bool)) else text


def mapping_to_rows(mapping: Any, key: str = "name", value: str = "value") -> List[dict]:
    """{'a': 1} -> [{'name': 'a', 'value': '1'}]."""
    if not isinstance(mapping, dict):
        return []
    return [{key: str(k), value: _clean(v)} for k, v in mapping.items()]


def rows_to_mapping(rows: List[dict], key: str = "name", value: str = "value",
                    typed: bool = False) -> dict:
    """Inverse of mapping_to_rows; rows without a key are dropped."""
    out = {}
    for row in rows:
        k = _clean(row.get(key))
        if k:
            out[k] = _scalar(row.get(value)) if typed else _clean(row.get(value))
    return out


def records_to_rows(records: Any, columns: List[str]) -> List[dict]:
    """A list of mappings as table rows restricted to ``columns``."""
    return [
        {col: _clean(r.get(col)) for col in columns}
        for r in records or [] if isinstance(r, dict)
    ]


def rows_to_records(previous: Any, rows: List[dict], columns: List[str],
                    required: List[str], match: Optional[List[str]] = None) -> List[dict]:
    """Table rows back to a list of mappings.

    Rows missing a ``required`` column are dropped. Keys the table does
    not show are kept from the previous record with the same ``match``
    columns (default: the first required column).
    """
    match = match or required[:1]

    def ident(record: dict) -> tuple:
        return tuple(_clean(record.get(c)) for c in match)

    kept = {ident(r): r for r in previous or [] if isinstance(r, dict)}
    out = []
    for row in rows:
        if not all(_clean(row.get(c)) for c in required):
            continue
        record = copy.deepcopy(kept.get(ident(row), {}))
        for col in columns:
            put(record, col, _clean(row.get(col)))
        out.append(record)
    return out


def algorithms_to_rows(sos: dict) -> List[dict]:
    rows = []
    algorithms = sos.get("algorithms")
    for name, opts in (algorithms.items() if isinstance(algorithms, dict) else []):
        opts = opts if isinstance(opts, dict) else {}
        rows.append({"function": str(name), "central": _clean(opts.get("central")),
                     "local": _clean(opts.get("local"))})
    return rows


def rows_to_algorithms(sos: dict, rows: List[dict]) -> None:
    previous = sos.get("algorithms") if isinstance(sos.get("algorithms"), dict) else {}
    algorithms = {}
    for row in rows:
        name = _clean(row.get("function"))
        if not name:
            continue
        opts = copy.deepcopy(previous.get(name)) if isinstance(previous.get(name), dict) else {}
        put(opts, "central", _clean(row.get("central")))
        put(opts, "local", _clean(row.get("local")))
        algorithms[name] = opts
    put(sos, "algorithms", algorithms)


# ── Hand-off: compare, send to the Explorer, export ─────────────────


def _index(items: list) -> Dict[str, dict]:
    return {str(i.get("id")): i for i in items or []}


def _field_changes(a: dict, b: dict, path: str = "") -> List[str]:
    changes = []
    for key in sorted(set(a) | set(b)):
        va, vb = a.get(key), b.get(key)
        full = f"{path}.{key}" if path else key
        if isinstance(va, dict) and isinstance(vb, dict):
            changes.extend(_field_changes(va, vb, full))
        elif va != vb:
            changes.append(f"{full}: {_short(va)} → {_short(vb)}")
    return changes


def _short(value: Any) -> str:
    if value in (None, "", [], {}):
        return "—"
    text = json.dumps(value, ensure_ascii=False, default=str) if isinstance(value, (list, dict)) else str(value)
    return text if len(text) <= 80 else text[:77] + "…"


def diff_designs(ir_a: dict, ir_b: dict) -> Dict[str, List[str]]:
    """Structural differences between two simulator IRs, grouped by topic."""
    out: Dict[str, List[str]] = {
        "SoS": [], "Actors": [], "Contracts": [], "Lifecycle": [], "Monitors": [],
        "Protocols": [], "Algorithms": [], "Regimes": [], "Metrics": [],
    }

    for key, label in (("name", "name"), ("sos_type", "type")):
        if ir_a.get(key) != ir_b.get(key):
            out["SoS"].append(f"{label}: {_short(ir_a.get(key))} → {_short(ir_b.get(key))}")

    actors_a = _index(ir_a.get("institution", {}).get("actors"))
    actors_b = _index(ir_b.get("institution", {}).get("actors"))
    for aid in sorted(set(actors_b) - set(actors_a)):
        out["Actors"].append(f"added actor {aid}")
    for aid in sorted(set(actors_a) - set(actors_b)):
        out["Actors"].append(f"removed actor {aid}")
    for aid in sorted(set(actors_a) & set(actors_b)):
        out["Actors"].extend(f"{aid} — {c}" for c in _field_changes(actors_a[aid], actors_b[aid]))

    contracts_a = _index(ir_a.get("institution", {}).get("contracts"))
    contracts_b = _index(ir_b.get("institution", {}).get("contracts"))
    for cid in sorted(set(contracts_b) - set(contracts_a)):
        out["Contracts"].append(f"added contract {cid}")
    for cid in sorted(set(contracts_a) - set(contracts_b)):
        out["Contracts"].append(f"removed contract {cid}")
    for cid in sorted(set(contracts_a) & set(contracts_b)):
        ca, cb = dict(contracts_a[cid]), dict(contracts_b[cid])
        la, lb = ca.pop("lifecycle", None) or {}, cb.pop("lifecycle", None) or {}
        ma, mb = _index(ca.pop("monitors", None)), _index(cb.pop("monitors", None))
        out["Contracts"].extend(f"{cid} — {c}" for c in _field_changes(ca, cb))

        for key in ("states", "initial", "terminal"):
            if la.get(key) != lb.get(key):
                out["Lifecycle"].append(f"{cid} — {key}: {_short(la.get(key))} → {_short(lb.get(key))}")
        ta, tb = _index(la.get("transitions")), _index(lb.get("transitions"))
        for tid in sorted(set(tb) - set(ta)):
            out["Lifecycle"].append(f"{cid} — added transition {tid}")
        for tid in sorted(set(ta) - set(tb)):
            out["Lifecycle"].append(f"{cid} — removed transition {tid}")
        for tid in sorted(set(ta) & set(tb)):
            out["Lifecycle"].extend(
                f"{cid} — transition {tid}: {c}" for c in _field_changes(ta[tid], tb[tid]))

        for mid in sorted(set(mb) - set(ma)):
            out["Monitors"].append(f"{cid} — added monitor {mid}")
        for mid in sorted(set(ma) - set(mb)):
            out["Monitors"].append(f"{cid} — removed monitor {mid}")
        for mid in sorted(set(ma) & set(mb)):
            out["Monitors"].extend(
                f"{cid} — monitor {mid}: {c}" for c in _field_changes(ma[mid], mb[mid]))

    def _keyed(topic: str, noun: str, items_a: Dict[str, dict], items_b: Dict[str, dict]):
        for key in sorted(set(items_b) - set(items_a)):
            out[topic].append(f"added {noun} {key}")
        for key in sorted(set(items_a) - set(items_b)):
            out[topic].append(f"removed {noun} {key}")
        for key in sorted(set(items_a) & set(items_b)):
            a, b = dict(items_a[key]), dict(items_b[key])
            steps_a, steps_b = a.pop("steps", None), b.pop("steps", None)
            out[topic].extend(f"{key} — {c}" for c in _field_changes(a, b))
            if steps_a != steps_b:
                out[topic].append(
                    f"{key} — steps changed ({len(steps_a or [])} → {len(steps_b or [])})")

    _keyed("Protocols", "protocol",
           _index((ir_a.get("protocol") or {}).get("protocols")),
           _index((ir_b.get("protocol") or {}).get("protocols")))
    _keyed("Algorithms", "algorithm",
           {str(x.get("name")): x for x in (ir_a.get("algorithm") or {}).get("algorithms") or []},
           {str(x.get("name")): x for x in (ir_b.get("algorithm") or {}).get("algorithms") or []})
    _keyed("Metrics", "metric", _index(ir_a.get("metrics")), _index(ir_b.get("metrics")))

    def _regimes(ir: dict) -> Dict[str, dict]:
        return {f"{t.get('from_regime')} → {t.get('to_regime')}": t for t in ir.get("transitions") or []}

    _keyed("Regimes", "transition", _regimes(ir_a), _regimes(ir_b))
    return out


def source_diff(source_a: str, source_b: str, label_a: str = "A", label_b: str = "B") -> str:
    return "".join(difflib.unified_diff(
        source_a.splitlines(keepends=True), source_b.splitlines(keepends=True),
        fromfile=label_a, tofile=label_b,
    ))


def to_explorer_yaml(source: str) -> str:
    """Reduce a full CADL design to the motivation-config YAML the Explorer runs.

    The Explorer's synthetic model reads only the SoS type, the motivation
    profile and rho. alpha / beta / lambda are averaged over the contracts
    and carried along, but do not change its results.
    """
    sos = _parse(source)

    def _mean(values: list) -> Optional[float]:
        values = [v for v in values if isinstance(v, (int, float))]
        return round(sum(values) / len(values), 3) if values else None

    from cadl.sim import lower_to_ir

    ir = _ir_to_dict(lower_to_ir(sos))
    govs = [c.get("governance") or {} for c in ir["institution"]["contracts"]]
    governance = {}
    for key in ("alpha", "beta", "lambda"):
        mean = _mean([g.get(key) for g in govs])
        if mean is not None:
            governance[key] = min(1.0, max(0.0, mean))

    config: Dict[str, Any] = {
        "name": sos.name,
        # The synthetic model knows directed and collaborative behaviour only.
        "sos_type": "directed" if sos.type.value == "Directed" else "collaborative",
        "description": f"From CADL design {sos.name} (type {sos.type.value})",
    }
    if governance:
        config["governance"] = governance
    mot = sos.motivation
    if mot is not None:
        block: Dict[str, Any] = {}
        if mot.agent is not None and mot.agent.profile in ("uniform", "linear", "polarized"):
            block["agent"] = {"profile": mot.agent.profile}
        if mot.governance is not None:
            block["governance"] = {"model": mot.governance.model, "rho": float(mot.governance.rho)}
        if block:
            config["motivation"] = block
    return yaml.safe_dump(config, sort_keys=False, allow_unicode=True)


def export_ir_json(analysis: DesignAnalysis) -> str:
    return json.dumps(analysis.ir, indent=2, ensure_ascii=False, default=str)


def export_ir_yaml(analysis: DesignAnalysis) -> str:
    return yaml.dump(analysis.ir, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True)


def export_sim_config(source: str, target: str) -> str:
    """Simulator config for `target` (see SIM_TARGETS)."""
    from cadl.sim import generate_config, lower_to_ir

    return generate_config(lower_to_ir(_parse(source)), target)


def export_codegen_zip(source: str, target: str) -> bytes:
    """Generated runtime code for `target` (see CODEGEN_TARGETS) as a zip archive."""
    from cadl.codegen import generate

    sos = _parse(source)
    with tempfile.TemporaryDirectory() as tmp:
        out_dir = Path(tmp) / "generated"
        generate(sos, out_dir, target=target)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(out_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, path.relative_to(out_dir).as_posix())
        return buffer.getvalue()


def regime_map(source: str) -> Optional[dict]:
    """Regime map of the top-level `transitions:`; None when there are none."""
    from cadl.regime_map import RegimeMap

    rm = RegimeMap.from_sos(_parse(source))
    if not rm.states:
        return None
    return {"dot": rm.to_dot(), "text": rm.to_text(), "json": rm.to_json()}


def iec62853_report(source: str) -> dict:
    """IEC 62853-oriented dependability summary (informative, not a conformance assessment)."""
    from cadl.iec62853 import generate_iec62853_report

    return generate_iec62853_report(_parse(source))


# ── Lifecycle trace ─────────────────────────────────────────────────


@dataclass
class Move:
    """One way a contract instance can leave its current lifecycle state."""
    kind: str      # "transition" | "deadline" | "monitor"
    label: str
    target: str
    detail: str = ""


def lifecycle_moves(contract_ir: dict, state: str) -> List[Move]:
    """Everything that can happen to a contract instance in ``state``."""
    lifecycle = contract_ir.get("lifecycle") or {}
    if state in (lifecycle.get("terminal") or []):
        return []
    moves: List[Move] = []
    for t in lifecycle.get("transitions") or []:
        if state not in (t.get("from_states") or []):
            continue
        tid = str(t.get("id", "?"))
        detail = str(t.get("on") or "")
        if t.get("when"):
            detail += f"  [when {t['when']}]"
        if t.get("to_state"):
            moves.append(Move("transition", tid, str(t["to_state"]), detail))
        if t.get("on_violation_transition"):
            deadline = t.get("deadline_ms")
            moves.append(Move(
                "deadline", f"{tid} misses its deadline", str(t["on_violation_transition"]),
                (f"not done within {_format_ms(int(deadline))}" if deadline is not None
                 else "violation") + f" — {t.get('on_violation_severity') or 'Major'}"))
    for m in contract_ir.get("monitors") or []:
        if m.get("on_match_transition"):
            moves.append(Move(
                "monitor", f"monitor {m.get('id', '?')} fires", str(m["on_match_transition"]),
                f"{m.get('rule') or ''} — {m.get('on_match_severity') or 'Major'}"))
    return moves


# ── Templates ───────────────────────────────────────────────────────

LIFECYCLE_PRESETS: Dict[str, dict] = {
    "Propose → accept → fulfil": {
        "states": ["Proposed", "Accepted", "InProgress", "Completed", "Violated", "Cancelled"],
        "initial": "Proposed",
        "terminal": ["Completed", "Violated", "Cancelled"],
        "transitions": [
            {"id": "accept", "from": "Proposed", "to": "Accepted", "on": "offer_accepted",
             "deadline": "10s", "on_violation": {"transition": "Cancelled", "severity": "Minor"}},
            {"id": "start", "from": "Accepted", "to": "InProgress", "on": "work_started"},
            {"id": "complete", "from": "InProgress", "to": "Completed", "on": "work_done",
             "deadline": "300s", "on_violation": {"transition": "Violated", "severity": "Major"}},
            {"id": "cancel", "from": ["Proposed", "Accepted"], "to": "Cancelled",
             "on": "cancel_requested"},
        ],
    },
    "Active until violated": {
        "states": ["Active", "Suspended", "Violated", "Terminated"],
        "initial": "Active",
        "terminal": ["Violated", "Terminated"],
        "transitions": [
            {"id": "suspend", "from": "Active", "to": "Suspended", "on": "suspend_requested"},
            {"id": "resume", "from": "Suspended", "to": "Active", "on": "resume_requested",
             "deadline": "60s", "on_violation": {"transition": "Terminated", "severity": "Minor"}},
            {"id": "breach", "from": ["Active", "Suspended"], "to": "Violated",
             "on": "breach_detected"},
            {"id": "end", "from": "Active", "to": "Terminated", "on": "end_requested"},
        ],
    },
    "Request → grant → revoke": {
        "states": ["Requested", "Granted", "Revoked", "Denied", "Expired"],
        "initial": "Requested",
        "terminal": ["Revoked", "Denied", "Expired"],
        "transitions": [
            {"id": "grant", "from": "Requested", "to": "Granted", "on": "access_granted",
             "deadline": "5s", "on_violation": {"transition": "Denied", "severity": "Minor"}},
            {"id": "deny", "from": "Requested", "to": "Denied", "on": "access_denied"},
            {"id": "revoke", "from": "Granted", "to": "Revoked", "on": "access_revoked"},
            {"id": "expire", "from": "Granted", "to": "Expired", "on": "now > grant.expiry"},
        ],
    },
}

# name -> (description, contract body, lifecycle preset). Parties and the
# decision holder are filled in from the design's actors.
CONTRACT_TEMPLATES: Dict[str, tuple] = {
    "Blank": ("Parties and one placeholder guarantee.", {"guarantee": ["true"]}, None),
    "Service-level agreement": (
        "A provider commits to completing work in time; lateness is a violation.",
        {
            "assume": ["provider_available == true"],
            "guarantee": ["completion_time <= 300s", "success_rate >= 0.95"],
            "information": {"alpha": 0.7},
            "incentives": {"type": "task_completion", "lambda": 0.5,
                           "rules": ["reward on on_time_completion", "penalty on late_completion"]},
            "violation": {"detect": "completion_time > 300s",
                          "action": "notify_decision_holder AND log_violation"},
        },
        "Propose → accept → fulfil",
    ),
    "Safety": (
        "Hard limits that must hold at all times, watched by a monitor.",
        {
            "guarantee": ["collision_count == 0", "min_separation >= 0.5"],
            "information": {"alpha": 0.9},
            "violation": {"detect": "min_separation < 0.5",
                          "action": "emergency_stop AND notify_decision_holder"},
            "monitors": [{
                "id": "separation_watch", "observe": "min_separation",
                "sampling": "periodic(100ms)", "rule": "min_separation < 0.5",
                "on_match": {"transition": "Violated", "severity": "Critical"},
            }],
        },
        "Active until violated",
    ),
    "Data sharing": (
        "One party shares data under purpose and retention limits.",
        {
            "assume": ["consent_given == true"],
            "guarantee": ["retention_seconds <= 86400", "purpose_violations == 0"],
            "information": {"alpha": 0.3},
            "violation": {"detect": "purpose_violations > 0",
                          "action": "revoke_access AND notify_decision_holder"},
        },
        "Request → grant → revoke",
    ),
}


def contract_from_template(sos: dict, template: str) -> dict:
    """A new contract with a unique id, built from CONTRACT_TEMPLATES."""
    _, body, preset = CONTRACT_TEMPLATES[template]
    contract = new_contract(sos)
    holder = (contract.get("authority") or {}).get("decision_holder")
    contract.update(copy.deepcopy(body))
    if template != "Blank":
        stem = re.sub(r"[^A-Za-z]+", "_", template).strip("_").upper()
        existing = {c.get("id") for c in sos.get("contracts") or []}
        n = 1
        while f"{stem}_{n}" in existing:
            n += 1
        contract["id"] = f"{stem}_{n}"
    if holder:
        contract["authority"] = {"decision_holder": holder, "beta": 0.7}
    if preset:
        contract["lifecycle"] = copy.deepcopy(LIFECYCLE_PRESETS[preset])
    return contract


# ── Draft from a description (optional, needs an API key) ───────────


def ai_available() -> Optional[str]:
    """None when drafting from a description can run, else why it cannot."""
    try:
        import anthropic  # noqa: F401
    except ImportError:
        return "the `anthropic` package is not installed"
    if not os.environ.get("ANTHROPIC_API_KEY"):
        return "`ANTHROPIC_API_KEY` is not set"
    return None


MAX_DESCRIPTION_CHARS = 4000


def draft_from_description(description: str) -> tuple:
    """Ask the upstream `cadl ai` generator for a design: (source, errors).

    Calls the Anthropic API with the server's key; raises ValueError when
    that is not possible.
    """
    reason = ai_available()
    if reason:
        raise ValueError(f"Drafting is not available: {reason}.")
    description = description.strip()
    if not description:
        raise ValueError("Describe the system first.")
    if len(description) > MAX_DESCRIPTION_CHARS:
        raise ValueError(f"The description is too long (limit {MAX_DESCRIPTION_CHARS} characters).")
    from cadl.ai import generate_cadl

    result = generate_cadl(description)
    return result.cadl_source[:MAX_SOURCE_CHARS], list(result.errors)


# ── Design report ───────────────────────────────────────────────────

_REPORT_CSS = """
body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif;
     max-width:980px;margin:32px auto;padding:0 24px;color:#222;line-height:1.5}
h1{margin-bottom:4px} h2{margin-top:36px;border-bottom:1px solid #ddd;padding-bottom:4px}
h3{margin-top:24px} .meta{color:#666} code,pre{font-family:ui-monospace,Menlo,monospace;font-size:12.5px}
pre{background:#f6f6f6;padding:12px;border-radius:6px;overflow-x:auto}
table{border-collapse:collapse;width:100%;margin:8px 0;font-size:14px}
th,td{border:1px solid #ddd;padding:5px 9px;text-align:left;vertical-align:top} th{background:#f3f3f3}
.error{color:#b3261e;font-weight:600}.warning{color:#9a6700;font-weight:600}
.info{color:#555}.pass{color:#1a7f37}
.diagram{margin:12px 0;overflow-x:auto} .note{color:#666;font-size:13px}
@media print{h2{break-after:avoid} .diagram,table{break-inside:avoid}}
"""


def _table(headers: List[str], rows: List[list]) -> str:
    if not rows:
        return '<p class="note">None.</p>'
    head = "".join(f"<th>{html.escape(str(h))}</th>" for h in headers)
    body = "".join(
        "<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _e(value: Any) -> str:
    return html.escape("—" if value in (None, "", []) else str(value))


def build_report(source: str, analysis: DesignAnalysis, generated: str = "") -> str:
    """A self-contained HTML report of the design and its checks.

    Sequence diagrams are inline SVG. Graph diagrams are embedded as DOT
    and drawn by the page with Viz.js from a CDN, so they need a network
    connection when the report is opened; without one the DOT text shows.
    """
    from backend.plotting.architecture import architecture_to_dot, regimes_to_dot
    from backend.plotting.sequence import sequence_svg
    from cadl_sim.sos_dsl import build_lifecycle_view, lifecycle_to_dot

    ir = analysis.ir or {}
    institution = ir.get("institution") or {}
    parts: List[str] = []
    graphs: List[str] = []

    def graph(dot: str) -> str:
        graphs.append(dot)
        return (f'<div class="diagram" id="graph{len(graphs) - 1}">'
                f"<pre>{html.escape(dot)}</pre></div>")

    counts = {level: analysis.count(level) for level in ("error", "warning", "info")}
    verdict = ("has errors" if counts["error"] else
               "passes with warnings" if counts["warning"] else "passes all checks")
    parts.append(f"<h1>{_e(analysis.name or 'CADL design')}</h1>")
    parts.append(
        f'<p class="meta">{_e(analysis.sos_type)} system of systems'
        + (f" · report generated {html.escape(generated)}" if generated else "") + "</p>")
    if ir.get("description"):
        parts.append(f"<p>{_e(ir['description'])}</p>")
    parts.append(
        f"<p>This design <strong>{verdict}</strong>: {counts['error']} error(s), "
        f"{counts['warning']} warning(s), {counts['info']} note(s).</p>")

    parts.append("<h2>Checks</h2>")
    rows = [
        [f'<span class="{f.level}">{f.level}</span>', _e(f.stage), _e(f.title), _e(f.message)]
        for f in analysis.findings
    ]
    parts.append(_table(["Result", "Check", "Subject", "Detail"], rows))
    parts.append("<h3>What the checks cover</h3>")
    parts.append(_table(["Check", "Covers"], [
        [_e(stage), _e(STAGE_SCOPE[stage])] for stage in STAGES]))
    parts.append("<p class=\"note\">Not checked: " + " ".join(_e(x) for x in NOT_CHECKED) + "</p>")

    if ir:
        parts.append("<h2>Architecture</h2>")
        parts.append(graph(architecture_to_dot(ir)))
        parts.append("<h3>Actors</h3>")
        parts.append(_table(["Id", "Role", "Autonomy", "Capabilities", "Inputs", "Outputs"], [
            [_e(a.get("id")), _e(a.get("role")), _e(a.get("autonomy")),
             _e(join_list(a.get("capabilities"))), _e(join_list(a.get("inputs"))),
             _e(join_list(a.get("outputs")))]
            for a in institution.get("actors") or []]))

        parts.append("<h2>Contracts</h2>")
        for c in institution.get("contracts") or []:
            gov = c.get("governance") or {}
            parts.append(f"<h3>{_e(c.get('id'))}</h3>")
            parts.append(_table(["Item", "Value"], [
                ["Parties", _e(join_list(c.get("parties")))],
                ["Assume", "<br>".join(_e(x) for x in c.get("assume") or []) or "—"],
                ["Guarantee", "<br>".join(_e(x) for x in c.get("guarantee") or []) or "—"],
                ["Decision holder", _e(gov.get("decision_holder"))],
                ["α / β / λ", _e(" / ".join(
                    "—" if gov.get(k) is None else f"{gov[k]:g}" for k in ("alpha", "beta", "lambda")))],
                ["Violation detect", _e(c.get("violation_detect"))],
                ["Violation action", _e(c.get("violation_action"))],
            ]))
            view = build_lifecycle_view(c)
            if view is not None:
                parts.append(graph(lifecycle_to_dot(view)))
                parts.append(_table(["Transition", "From", "To", "On", "When", "Deadline", "If missed"], [
                    [_e(t.get("id")), _e(join_list(t.get("from_states"))), _e(t.get("to_state")),
                     _e(t.get("on")), _e(t.get("when")),
                     _e(_format_ms(int(t["deadline_ms"])) if t.get("deadline_ms") is not None else None),
                     _e(t.get("on_violation_transition"))]
                    for t in view.transitions]))
            if c.get("monitors"):
                parts.append(_table(["Monitor", "Observes", "Rule", "On match"], [
                    [_e(m.get("id")), _e(join_list(m.get("observe"))), _e(m.get("rule")),
                     _e(m.get("on_match_transition") or m.get("on_match_violation"))]
                    for m in c["monitors"]]))

        protocols = (ir.get("protocol") or {}).get("protocols") or []
        if protocols:
            parts.append("<h2>Protocols</h2>")
            for p in protocols:
                parts.append(f"<h3>{_e(p.get('id'))}</h3>")
                parts.append(f"<p>Trigger: <code>{_e(p.get('trigger'))}</code></p>")
                parts.append(f'<div class="diagram">{sequence_svg(p)}</div>')
                facts = [["precondition", _e(p.get("precondition"))],
                         ["postcondition", _e(p.get("postcondition"))]]
                facts += [[f"timing · {_e(k)}", _e(v)] for k, v in (p.get("timing") or {}).items()]
                facts += [[f"fallback · {_e(k)}", _e(v)] for k, v in (p.get("fallback") or {}).items()]
                parts.append(_table(["Item", "Value"], [f for f in facts if f[1] != "—"]))

        if ir.get("transitions"):
            parts.append("<h2>Regimes</h2>")
            parts.append(graph(regimes_to_dot(ir)))

        algorithms = (ir.get("algorithm") or {}).get("algorithms") or []
        if algorithms:
            parts.append("<h2>Algorithms</h2>")
            parts.append(_table(["Function", "Central", "Local"], [
                [_e(a.get("name")), _e(a.get("central")), _e(a.get("local"))] for a in algorithms]))
        if ir.get("metrics"):
            parts.append("<h2>Metrics</h2>")
            parts.append(_table(["Id", "Formula", "Target"], [
                [_e(m.get("id")), _e(m.get("formula")), _e(m.get("target"))] for m in ir["metrics"]]))

    parts.append("<h2>CADL source</h2>")
    parts.append(f"<pre>{html.escape(source)}</pre>")

    script = (
        '<script src="https://cdn.jsdelivr.net/npm/@viz-js/viz@3.11.0/lib/viz-standalone.js"></script>\n'
        "<script>\nconst GRAPHS = " + json.dumps(graphs).replace("</", "<\\/") + ";\n"
        "if (window.Viz) Viz.instance().then(viz => GRAPHS.forEach((dot, i) => {\n"
        "  try { const el = document.getElementById('graph' + i);\n"
        "        el.replaceChildren(viz.renderSVGElement(dot)); } catch (e) {}\n"
        "}));\n</script>"
    ) if graphs else ""
    title = html.escape(analysis.name or "CADL design")
    return (
        "<!doctype html>\n<html lang=\"en\"><head><meta charset=\"utf-8\">"
        f"<title>{title} — design report</title><style>{_REPORT_CSS}</style></head>\n<body>\n"
        + "\n".join(parts) + "\n" + script + "\n</body></html>\n"
    )
