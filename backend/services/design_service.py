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
import functools
import html
import importlib
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
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
# The boundary policy of the agent governance extension (agents 0.1, cadl 0.3.20+);
# offered under Export only for a design that declares it.
AGENT_CODEGEN_TARGETS = {
    "agent-rego": "Agent policy (Rego, agents 0.1)",
    "agent-cedar": "Agent policy (Cedar, agents 0.1)",
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
    "The verdicts of norms (norms 0.1) on a run; `cadl norms-eval` computes them on a "
    "recorded trace.",
    "The probabilities and rewards of a stochastic model (stochastic 0.1); `cadl model-check` "
    "evaluates them.",
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
    """The IR as `cadl sim-ir` writes it (`lambda`, counts-as `if`, the agent
    fields only for a design that declares agents 0.1)."""
    try:
        from cadl.cli import _ir_to_dict as cadl_ir_to_dict
    except ImportError:         # an older cadl: rename lambda_ as it did
        d = asdict(ir)
        for c in d.get("institution", {}).get("contracts", []):
            gov = c.get("governance") or {}
            if "lambda_" in gov:
                gov["lambda"] = gov.pop("lambda_")
        return d
    return cadl_ir_to_dict(ir)


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
    result.sos_type = sos.type.value if sos.type else ""
    result.has_regimes = bool(sos.transitions)
    result.findings.append(Finding("pass", "Parse", "Syntax is valid"))
    if not sos.type:
        result.findings.append(Finding(
            "warning", "Type check", "The SoS has no type",
            f"Set `type` to one of {', '.join(SOS_TYPES)}.", "System"))
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
        ir_warnings = 0
        for err in validate_ir(ir):
            names = re.findall(r"'([^']+)'", err)
            if not (names and names[0] in reported):
                result.findings.append(Finding("warning", "Simulator IR", err))
                ir_warnings += 1
        if not ir_warnings:
            result.findings.append(
                Finding("pass", "Simulator IR", "The design converts to the simulator IR"))
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


def _try_expr(text: Any, rule: bool = False) -> Optional[str]:
    """None when ``text`` parses as a CADL expression, else a short reason.

    ``rule`` selects the rule grammar of Appendix E, E.3 (``when:`` and a
    monitor's ``rule:``), which adds ``x IN [a, b]``. cadl-lang provides it
    from 0.3.10; with an older release the expression grammar is used.
    """
    from cadl import parser

    parse = getattr(parser, "parse_rule", None) if rule else None
    try:
        (parse or parser.parse_expr)(str(text))
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

    def check(text: Any, where: str, section: str, item: str, verified: bool = False,
              rule: bool = False):
        if text in (None, ""):
            return
        reason = _try_expr(text, rule)
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
                      "Contracts", cid, rule=True)
        for m in c.get("monitors") or []:
            if isinstance(m, dict):
                check(m.get("rule"), f"Contract '{cid}' monitor '{m.get('id')}' rule",
                      "Contracts", cid, rule=True)
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

    # Lifecycle events and monitor rules name actors too, but the upstream
    # type checker only looks at parties, assume / guarantee and protocols.
    for c in (ir.get("institution") or {}).get("contracts") or []:
        cid = str(c.get("id", ""))
        texts = [(f"transition '{t.get('id')}'", f"{t.get('on') or ''} {t.get('when') or ''}")
                 for t in (c.get("lifecycle") or {}).get("transitions") or []]
        texts += [(f"monitor '{m.get('id')}'", f"{m.get('rule') or ''} {join_list(m.get('observe'))}")
                  for m in c.get("monitors") or []]
        for where, text in texts:
            named = set(re.findall(r"\b([A-Za-z_]\w*)\[", text))
            arrow = re.match(r"\s*([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*->\s*([A-Za-z_]\w*)", text)
            if arrow:
                named.update(arrow.groups())
            for name in sorted(named - set(actors)):
                findings.append(Finding(
                    "warning", "Interfaces", f"Contract '{cid}' {where} names an undefined actor",
                    f"`{name}` is not an actor of this design ({', '.join(actors) or 'none defined'}).",
                    "Contracts", cid))

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

        # The same limit is often written three times: in a guarantee, in
        # the violation detector and as a lifecycle deadline.
        bounds = _guaranteed_bounds(c)
        detect = re.fullmatch(r"\s*([A-Za-z_]\w*)\s*((?:<=|>=|==|<|>).*)", str(c.get("violation_detect") or ""))
        detect_bound = _bound(detect.group(2)) if detect else None
        for quantity, op, value, text in bounds:
            if detect and detect_bound and detect.group(1) == quantity and detect_bound[1] != value:
                findings.append(Finding(
                    "warning", "Consistency",
                    f"Contract '{cid}' detects violations at a different limit than it guarantees",
                    f"The guarantee is `{text}`, but a violation is detected when "
                    f"`{c.get('violation_detect')}`.", "Contracts", cid))
        # Time bounds as Monitoring coverage reads them: `ROBOT[i].pickup_time <= 120s`,
        # `120s >= pickup_time`, and each part of an AND.
        from backend.services.design_contracts import _time_bounds
        timed = [(quantity, duration_ms(bound))
                 for text in c.get("guarantee") or [] for quantity, bound in _time_bounds(str(text))
                 if duration_ms(bound) is not None]
        transitions = (c.get("lifecycle") or {}).get("transitions") or []

        def step_of(quantity: str, tid: str) -> bool:
            return bool(tid) and bool(re.search(rf"(^|_){re.escape(tid)}(_|$)", quantity))

        ids = [str(t.get("id") or "") for t in transitions]
        # A guarantee no transition is named in bounds the whole contract.
        whole = [value for quantity, value in timed if not any(step_of(quantity, i) for i in ids)]
        if timed:
            overall = min(value for _, value in timed)
            for t in transitions:
                deadline = t.get("deadline_ms")
                # A step bounded by a guarantee of its own (`pickup_time <= 120s`
                # for transition `pickup`) is held to that guarantee and to those
                # of the whole contract, not to the other steps' guarantees; a
                # step without one, to the tightest time the contract guarantees.
                own = [value for quantity, value in timed if step_of(quantity, str(t.get("id") or ""))]
                limit = min(own + whole) if own else overall
                # A shorter deadline is normal (one step of several); a longer
                # one lets a step run past what the contract promises.
                if deadline is not None and int(deadline) > int(limit):
                    findings.append(Finding(
                        "warning", "Consistency",
                        f"Contract '{cid}' transition '{t.get('id')}' may take longer than the "
                        "contract guarantees",
                        f"Its deadline is {_format_ms(int(deadline))}, but the contract guarantees "
                        f"{_format_ms(int(limit))}.", "Contracts", cid))

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

        for n in c.get("norms") or []:
            target = n.get("on_violation_transition")
            if target and target not in known:
                error(f"Norm '{n.get('id', '?')}' requests a move to undeclared state '{target}'.")

        if initial in known:
            # Monitors can move the contract from any state to their target; the
            # violation of a norm (norms 0.1, `on_violation: transition:`) from any
            # state that is not terminal, as `cadl norms-eval` requests it.
            monitor_targets = {
                m.get("on_match_transition") for m in c.get("monitors") or []
            } & known
            norm_targets = {n.get("on_violation_transition") for n in c.get("norms") or []} & known
            reached, frontier = {initial}, [initial]
            while frontier:
                node = frontier.pop()
                for nxt in edges[node] | monitor_targets | (norm_targets if node not in terminal else set()):
                    if nxt not in reached:
                        reached.add(nxt)
                        frontier.append(nxt)
            for s in states:
                if s not in reached:
                    warn(f"State '{s}' cannot be reached from the initial state '{initial}'.", s)
                elif s not in terminal and not edges[s] and not monitor_targets and not norm_targets:
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
    return _restore_on_keys(doc)


def _restore_on_keys(node: Any) -> Any:
    """Undo YAML 1.1 reading the key `on` as the boolean True.

    CADL uses `on:` for lifecycle events, and PyYAML loads that key as
    True. Left alone, forms would not see the event and the design would
    be written back with a `true:` key.
    """
    if isinstance(node, list):
        return [_restore_on_keys(item) for item in node]
    if isinstance(node, dict):
        return {("on" if key is True else key): _restore_on_keys(value)
                for key, value in node.items()}
    return node


class _NoAliasDumper(yaml.SafeDumper):
    # The source checker rejects anchors and aliases, so never emit them.
    def ignore_aliases(self, data):
        return True


def dump_doc(doc: dict) -> str:
    text = yaml.dump(
        doc, Dumper=_NoAliasDumper, sort_keys=False, allow_unicode=True,
        default_flow_style=False, width=100,
    )
    # PyYAML quotes the key `on` because YAML 1.1 would read it as a boolean;
    # CADL writes it bare, and its parser accepts either.
    return re.sub(r"(?m)^(\s*(?:- )?)'on':", r"\1on:", text)


def has_comments(source: str) -> bool:
    return any(line.lstrip().startswith("#") for line in source.splitlines())


_AMEND_TAG = re.compile(r"#\s*@amend:")


def has_amend_tags(source: str) -> bool:
    """True when values are marked ``# @amend:<name>`` (Robot Town, Course D).

    A council program finds the values it may change by these comments, so
    rewriting the source (forms, proposals) without them breaks the next
    amendment.
    """
    return bool(_AMEND_TAG.search(source or ""))


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
        "sos_type": "directed" if sos.type and sos.type.value == "Directed" else "collaborative",
        "description": f"From CADL design {sos.name} (type {sos.type.value if sos.type else 'unset'})",
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


@functools.lru_cache(maxsize=64)
def extensions_in_effect(source: str) -> frozenset:
    """The extensions that cadl reads in this design: "norms" (norms 0.1),
    "agents" (agents 0.1 together with norms 0.1), "stochastic"
    (stochastic 0.1), "games" (games 0.1 together with stochastic 0.1) and
    "grounding" (grounding 0.1 together with norms 0.1). Decided by cadl
    itself; a design that does not parse has none."""
    try:
        sos = _parse(source or "")
    except Exception:
        return frozenset()
    found = set()
    for name, module, test in (("norms", "cadl.norms", "norms_declared"),
                               ("agents", "cadl.agents", "agents_active"),
                               ("stochastic", "cadl.stochastic", "stochastic_declared"),
                               ("games", "cadl.games", "games_declared"),
                               ("grounding", "cadl.grounding", "grounding_active")):
        try:
            if getattr(importlib.import_module(module), test)(sos):
                found.add(name)
        except (ImportError, AttributeError):
            pass                # a cadl without the extension
    if "stochastic" not in found:
        found.discard("games")  # the strategic model is a `model:` block of Appendix H
    return frozenset(found)


def norms_diff(old_source: str, new_source: str) -> Optional[dict]:
    """What amending `old_source` (A) to `new_source` (B) does to their norms, as
    `cadl norms-diff A B` reports it.

    None when neither declares norms 0.1. The result has `contracts`
    (`id`, `findings` with `level` and `text`), `errors` and `ok`. As in
    cadl, B that cannot take effect (not parsed, no `norms: 0.1`, type-check
    errors) is an error of the amendment; A that cannot be read is reported
    under `invalid_old`, since then there is nothing to amend.
    """
    if not ({"norms"} & (extensions_in_effect(old_source) | extensions_in_effect(new_source))):
        return None
    from cadl.norms_diff import diff_versions
    from cadl.norms_eval import NormsEvalError, load_version

    with tempfile.TemporaryDirectory() as tmp:
        paths = {"A": Path(tmp) / "A.cadl", "B": Path(tmp) / "B.cadl"}

        def load(label: str, text: str):
            _check_source_text(text)
            paths[label].write_text(text, encoding="utf-8")
            try:
                return load_version(paths[label])
            except NormsEvalError as e:
                raise ValueError(str(e).replace(str(paths[label]), label)) from None

        try:
            old = load("A", old_source)
        except ValueError as e:
            return {"contracts": [], "errors": [str(e)], "ok": False, "invalid_old": True}
        try:
            new = load("B", new_source)
        except ValueError as e:
            return {"contracts": [], "errors": [str(e)], "ok": False}
        try:
            return diff_versions(old, new)
        except NormsEvalError as e:
            return {"contracts": [], "errors": [str(e)], "ok": False}


def prism_files(source: str) -> Dict[str, str]:
    """The PRISM model and properties files of a stochastic model (`cadl model-export`).

    Raises ValueError when the design has no interpreted `model:` block.
    """
    from cadl.stochastic import ExportError, analyse, export_prism, stochastic_declared
    from cadl.type_checker import type_check

    sos = _parse(source)
    if sos.model is None or not stochastic_declared(sos):
        raise ValueError("The design has no `model:` block under `stochastic: 0.1`.")
    check = type_check(sos)
    if not check.ok:
        raise ValueError("The model is not interpreted while the design has type-check errors.")
    try:
        return export_prism(analyse(sos).model)
    except ExportError as e:
        raise ValueError(str(e)) from e


# Limits of a cadl command run for the page (game-check, norms-check): the
# commands run in a subprocess, so a heavy design cannot hold the server.
CADL_MEMORY_LIMIT = 1 << 30         # address space of the cadl process, bytes (POSIX)
CADL_FILE_LIMIT = 100 << 20         # largest file the command may write, bytes
MAX_OUTPUT_BYTES = 5 << 20          # largest report read back
MAX_EXPORT_BYTES = 10 << 20         # largest model and witnesses offered for download
BUILD_BUDGET = 30.0                 # seconds for norms-check besides its --timeout
MEMORY_WATCH_LIMIT = 3 << 29       # resident memory of a run, all its processes (1.5 GiB)
WATCH_INTERVAL = 0.5                # seconds between two looks of the memory watchdog
PRISM_JAVA_HEAP = "1g"              # CADL_PRISM_JAVAMAXMEM, unless the environment sets it

_CHILD = """import os
import sys
try:
    import resource
except ImportError:
    resource = None
if resource is not None:
    # soft limits only: the processes that cadl starts raise them again (_unbounded)
    for name, value in (("RLIMIT_CPU", {cpu}), ("RLIMIT_AS", {memory}), ("RLIMIT_FSIZE", {fsize})):
        limit = getattr(resource, name, None)
        if limit is None or not value:
            continue
        try:
            soft, hard = resource.getrlimit(limit)
            if hard != resource.RLIM_INFINITY:
                value = min(value, hard)
            resource.setrlimit(limit, (value, hard))
        except (ValueError, OSError):
            pass


def _unbounded():
    # in a process that cadl starts (PRISM): its Java VM reserves more address space
    # and spends CPU time in several threads; the heap (CADL_PRISM_JAVAMAXMEM), the
    # wall clock and the memory watchdog of the page bound it
    for name in ("RLIMIT_AS", "RLIMIT_CPU"):
        limit = getattr(resource, name, None)
        if limit is None:
            continue
        try:
            soft, hard = resource.getrlimit(limit)
            resource.setrlimit(limit, (hard, hard))
        except (ValueError, OSError):
            pass


import subprocess
_Popen = subprocess.Popen


class _Recorded(_Popen):
    # cadl starts PRISM in a session of its own; the page kills its group too
    def __init__(self, *args, **kwargs):
        if resource is not None and os.name == "posix":
            inner = kwargs.get("preexec_fn")

            def preexec():
                _unbounded()
                if inner is not None:
                    inner()
            kwargs["preexec_fn"] = preexec
        super().__init__(*args, **kwargs)
        try:
            with open({pids!r}, "a") as f:
                f.write(str(self.pid) + "\\n")
        except OSError:
            pass


subprocess.Popen = _Recorded
from cadl.cli import main
sys.exit(main())
"""

# At most this many cadl commands run at once for all sessions of the server; a
# further request is refused (each may take 1 GiB and minutes).
MAX_CONCURRENT_RUNS = 2
_RUN_SLOTS = threading.BoundedSemaphore(MAX_CONCURRENT_RUNS)
BUSY = "The server is running other checks; try again shortly. Nothing is claimed."


def _run_cadl(args: List[str], source: str, wall: float, workdir: Optional[str] = None) -> dict:
    """Run `cadl ARGS[0] design.cadl ARGS[1:]` in a subprocess with the design in a
    temporary file and with bounds: `wall` seconds of wall-clock time (the process
    group is killed after it), files of at most CADL_FILE_LIMIT bytes, and as soft
    limits of the cadl process (POSIX), as many seconds of CPU time and
    CADL_MEMORY_LIMIT bytes of address space. The processes that cadl starts, such
    as PRISM, raise the soft limits of CPU time and address space to the hard ones
    (a Java VM reserves more address space and spends CPU time in several
    threads); PRISM gets a Java heap of PRISM_JAVA_HEAP. cadl starts PRISM in a
    session of its own, so the child records the pid of every process it starts,
    and each of their process groups is killed with the child's when the child does
    not end by itself. A watchdog kills them all when their resident memory exceeds
    MEMORY_WATCH_LIMIT (POSIX; see _watch).
    At most MAX_CONCURRENT_RUNS commands run at once; a further one is refused.

    Returns {"status", "stdout", "stderr", "failure", "transient"}: `failure` is
    None when cadl ran to its exit status, otherwise a sentence that says nothing
    is claimed; `transient` is True for a failure that may not recur (the server
    busy, a timeout, a limit), which a caller should not cache. The temporary path
    is replaced by "the design"; tracebacks are not returned.
    """
    _parse(source)                  # the input limits of the page
    if not _RUN_SLOTS.acquire(timeout=1):
        return {"status": None, "stdout": "", "stderr": "", "failure": BUSY, "transient": True}
    try:
        return _run_cadl_slot(args, source, wall, workdir)
    finally:
        _RUN_SLOTS.release()


def _run_cadl_slot(args: List[str], source: str, wall: float, workdir: Optional[str]) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "design.cadl"
        path.write_text(source, encoding="utf-8")
        pids = Path(tmp) / "pids"
        code = _CHILD.format(cpu=int(wall) + 1, memory=CADL_MEMORY_LIMIT,
                             fsize=CADL_FILE_LIMIT, pids=str(pids))
        command = [sys.executable, "-c", code, args[0], str(path), *args[1:]]
        out_path, err_path = Path(tmp) / "stdout", Path(tmp) / "stderr"
        with open(out_path, "wb") as out, open(err_path, "wb") as err:
            env = dict(os.environ)
            # cadl 0.3.26 gives PRISM a Java heap of 4g (or half the memory); less here
            env.setdefault("CADL_PRISM_JAVAMAXMEM", PRISM_JAVA_HEAP)
            proc = subprocess.Popen(command, stdout=out, stderr=err, cwd=workdir or tmp, env=env,
                                    start_new_session=(os.name == "posix"))
            timed_out, over_memory = _watch(proc, pids, wall)
            if timed_out or proc.returncode != 0:
                # also after a crash of cadl, which can leave PRISM running
                _kill_recorded(pids)
        name = f"`cadl {args[0]}`"
        result = {"status": proc.returncode, "stdout": "", "stderr": "", "failure": None,
                  "transient": False}
        stderr = _read_capped(err_path).replace(str(path), "the design")
        if workdir:
            stderr = stderr.replace(workdir, "")
        if timed_out:
            result["failure"] = f"{name} did not finish within {wall:g} s; nothing is claimed."
            result["transient"] = True
        elif over_memory:
            result["failure"] = (f"{name} was stopped at the memory limit of this app "
                                 f"({MEMORY_WATCH_LIMIT >> 20} MB); nothing is claimed.")
            result["transient"] = True
        elif proc.returncode is not None and proc.returncode < 0:
            result["failure"] = (f"{name} was stopped at a limit of CPU time, memory or file size "
                                 f"of this app; nothing is claimed.")
            result["transient"] = True
        elif "Traceback (most recent call last)" in stderr:
            result["transient"] = "MemoryError" in stderr
            result["failure"] = (f"{name} ran out of the memory this app allows; nothing is claimed."
                                 if "MemoryError" in stderr else
                                 f"{name} failed with an internal error; nothing is claimed.")
        elif out_path.stat().st_size > MAX_OUTPUT_BYTES:
            result["failure"] = (f"The report of {name} is larger than "
                                 f"{MAX_OUTPUT_BYTES >> 20} MB and is not shown.")
        if result["failure"] is None:
            result["stdout"] = out_path.read_text(encoding="utf-8", errors="replace").replace(
                str(path), "the design")
            result["stderr"] = stderr
        return result


def _watch(proc: subprocess.Popen, pids: Path, wall: float) -> tuple:
    """Wait for the child for at most `wall` seconds; every WATCH_INTERVAL seconds
    (POSIX) add up the resident memory of its process group and of the groups of
    the processes it recorded, and kill the child above MEMORY_WATCH_LIMIT: macOS
    does not take RLIMIT_AS, and PRISM runs without it. (timed out, over memory)."""
    deadline = time.monotonic() + wall
    while True:
        left = deadline - time.monotonic()
        try:
            proc.wait(timeout=max(0.0, min(WATCH_INTERVAL, left)))
            return False, False
        except subprocess.TimeoutExpired:
            pass
        if time.monotonic() >= deadline:
            _kill(proc)
            return True, False
        if os.name == "posix" and _groups_rss({proc.pid} | _recorded(pids)) > MEMORY_WATCH_LIMIT:
            _kill(proc)
            return False, True


def _recorded(pids: Path) -> set:
    try:
        return {int(x) for x in pids.read_text().split() if x.isdigit()}
    except OSError:
        return set()


def _groups_rss(groups: set) -> int:
    """Resident memory, in bytes, of the processes in the process groups `groups`:
    from /proc where it exists (Linux), else from one run of `ps` (macOS)."""
    total = 0
    proc_dir = Path("/proc")
    if (proc_dir / "self" / "stat").exists():
        page = os.sysconf("SC_PAGE_SIZE")
        for entry in proc_dir.iterdir():
            if not entry.name.isdigit():
                continue
            try:
                stat = (entry / "stat").read_text()
            except OSError:
                continue
            fields = stat[stat.rfind(")") + 2:].split()     # after "pid (comm) "
            if len(fields) > 21 and int(fields[2]) in groups:
                total += int(fields[21]) * page
        return total
    try:
        listing = subprocess.run(["ps", "-A", "-o", "pid=,pgid=,rss="], capture_output=True,
                                 text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return 0
    for line in listing.splitlines():
        parts = line.split()
        if len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit() and int(parts[1]) in groups:
            total += int(parts[2]) * 1024           # kilobytes
    return total


def _kill(proc: subprocess.Popen) -> None:
    """Kill the child and its process group (the processes it starts in its own
    session, such as PRISM, are killed by _kill_recorded)."""
    try:
        if os.name == "posix":
            os.killpg(proc.pid, 9)
        else:
            proc.kill()
    except (ProcessLookupError, PermissionError, OSError):
        pass
    proc.wait()


def _kill_recorded(pids: Path) -> None:
    """Kill the process group of every process that the child recorded."""
    recorded = sorted(_recorded(pids))
    own_group = os.getpgid(0) if os.name == "posix" else None
    for pid in recorded:
        if pid == os.getpid():
            continue
        if os.name == "posix":
            # cadl starts PRISM in its own session, so a recorded process that is
            # still ours leads its own group; a reused pid of an unrelated process
            # (or of this app's group) is left alone
            try:
                group = os.getpgid(pid)
            except (ProcessLookupError, PermissionError, OSError):
                continue
            if group != pid or group == own_group:
                continue
        for kill in ((os.killpg, os.kill) if os.name == "posix" else (os.kill,)):
            try:
                kill(pid, 9)
            except (ProcessLookupError, PermissionError, OSError):
                pass


def _read_capped(path: Path, limit: int = 1 << 16) -> str:
    with open(path, "rb") as f:
        return f.read(limit).decode("utf-8", errors="replace")


def norms_indices(source: str) -> dict:
    """The derived indices of grounding 0.1 (Appendix J, J.4), as `cadl norms-indices`
    reports them: α̂, β̂ and χ per contract, and the indices of the SoS for two or
    more contracts. {"report": the JSON of the command, "text": its text}.

    Raises ValueError for a design without grounding 0.1 and norms 0.1, or with
    type-check errors.
    """
    from cadl.grounding import GroundingError, format_report_text, report
    from cadl.type_checker import type_check

    sos = _parse(source)
    try:
        result = report(sos)
    except GroundingError as e:
        raise ValueError(str(e)) from None
    check = type_check(sos)
    if not check.ok:
        raise ValueError(f"The indices are not computed while the design has "
                         f"{len(check.errors)} type-check error(s).")
    result = {"file": "the design", **result}
    return {"report": result, "text": format_report_text(result)}


GAME_CHECK_TIMEOUT = 120.0


def game_check(source: str, certify: bool = False, timeout: float = GAME_CHECK_TIMEOUT) -> dict:
    """The equilibrium checks of a strategic model (games 0.1, Appendix I, I.8) and,
    with `certify`, its equilibrium maps (I.9), as `cadl game-check [--certify]`
    reports them, run in a bounded subprocess (see _run_cadl).

    Returns {"status", "text", "errors", "failure"}. Exit status 0: every
    requested result was computed; 1: a map did not reach its resolution or a
    query was not evaluated; 2: nothing can be claimed (`errors` says why).
    `failure` is set, and nothing is claimed, when the command did not finish,
    hit a limit or failed with an internal error. Never raises for a design
    that parses.
    """
    run = _run_cadl(["game-check"] + (["--certify"] if certify else []), source, timeout)
    return {"status": run["status"], "text": run["stdout"].rstrip(),
            "errors": _usage_error(run["stderr"]), "failure": run["failure"],
            "transient": run["transient"]}


def _usage_error(stderr: str) -> str:
    """stderr of a command, with argparse's usage text cut to its last line."""
    text = stderr.strip()
    return text.splitlines()[-1] if text.startswith("usage:") else text


NORMS_CHECK_TIMEOUT = 60.0
MAX_NORMS_CHECK_TIMEOUT = 120.0     # the largest --timeout the page passes


def prism_available() -> bool:
    """Whether PRISM, which `cadl norms-check` runs, is on PATH (it is not on
    Streamlit Community Cloud)."""
    try:
        from cadl.norms_check import find_checker
    except ImportError:         # a cadl before 0.3.25
        return False
    return find_checker() is not None


def norms_contracts(source: str) -> List[str]:
    """The contracts with norms (norms 0.1) that `cadl norms-check` can be asked about."""
    if "norms" not in extensions_in_effect(source):
        return []
    return [c.id for c in _parse(source).contracts if getattr(c, "norms", None)]


def norms_check(source: str, contract: Optional[str] = None, bindings: Optional[List[str]] = None,
                members: Optional[List[str]] = None, step: Optional[str] = None,
                horizon: Optional[int] = None, timeout: float = NORMS_CHECK_TIMEOUT,
                budget: float = BUILD_BUDGET, domains: Optional[List[str]] = None,
                instances: Any = None, versions: Optional[List[tuple]] = None) -> dict:
    """`cadl norms-check --format json --export DIR` (Appendix F, F.12): the norms of
    one contract instance, translated into a model for PRISM and checked over every
    behaviour up to the horizon. `bindings` and `members` are "name=n" texts, as on
    the command line; `timeout` bounds building the model and the runs of PRISM
    (`--timeout`; at most MAX_NORMS_CHECK_TIMEOUT). cadl 0.3.26 adds `domains`
    ("Q=lo..hi" texts, `--domain`), `instances` (a number, `--instances N`, or a
    list of "i=n,..." texts, one `--instance` each; at most 4) and `versions`
    ([(name, source)], later versions to amend to, `--version FILE` each, F.6).
    The command runs in a subprocess (see _run_cadl) that is stopped after
    `timeout + budget` seconds.

    Returns {"report", "text", "zip", "files", "notice", "failure"}: the JSON
    report of the command and its text, the model, property files and witnesses
    as one zip (None when they exceed MAX_EXPORT_BYTES, which `notice` says), and
    `failure` instead of a report when the design cannot be checked, PRISM
    exceeded the timeout, or the command did not finish, hit a limit or failed;
    `transient` as in _run_cadl.
    When PRISM is not on PATH, `report["checked"]` is False and nothing was
    checked. Never raises for a design that parses.
    """
    from cadl.norms_check import format_text

    timeout = min(float(timeout), MAX_NORMS_CHECK_TIMEOUT)
    out = {"report": None, "text": "", "zip": None, "files": [], "notice": None, "failure": None,
           "transient": False}
    with tempfile.TemporaryDirectory() as tmp:
        export_dir = Path(tmp) / "export"
        args = ["norms-check", "--format", "json", "--export", str(export_dir),
                "--timeout", f"{timeout:g}"]
        if contract:
            args += ["--contract", contract]
        for value in bindings or []:
            args += ["--bindings", value]
        for value in members or []:
            args += ["--members", value]
        if step:
            args += ["--step", step]
        if horizon:
            args += ["--horizon", str(int(horizon))]
        for value in domains or []:
            args += ["--domain", value]
        if isinstance(instances, int) and instances:
            args += ["--instances", str(instances)]
        elif isinstance(instances, (list, tuple)):
            for value in instances:
                args += ["--instance", value]
        for k, (name, text) in enumerate(versions or [], 1):
            _parse(text)            # the input limits of the page
            stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name)).strip("._") or "version"
            # in the export directory, so that the exported witnesses, which name a
            # version by its path relative to it, and the zip hold it
            export_dir.mkdir(exist_ok=True)
            version = export_dir / f"v{k}_{stem}.cadl"
            version.write_text(text, encoding="utf-8")
            args += ["--version", str(version)]
        run = _run_cadl(args, source, timeout + budget,
                        workdir=tmp)
        if run["failure"]:
            out["failure"], out["transient"] = run["failure"], run["transient"]
            if "did not finish" in run["failure"]:
                out["failure"] += (f" The limit is the timeout and {budget:g} s more; a coarser "
                                   "step, a smaller horizon, fewer members or instances make the "
                                   "model smaller, or run `cadl norms-check` on your machine.")
            return out
        if run["status"] == 4:      # cadl 0.3.26: an internal error of norms-check
            out["failure"] = "`cadl norms-check` failed with an internal error; nothing is claimed."
            return out
        if run["status"] == 2 or not run["stdout"].strip():
            errors = [line[len("Error: "):] if line.startswith("Error: ") else line
                      for line in _usage_error(run["stderr"]).splitlines()]
            message = " ".join(errors).replace(str(export_dir) + os.sep, "")
            message = message.replace(str(tmp) + os.sep, "").strip()
            out["failure"] = (message or "`cadl norms-check` gave no report") + "; nothing is claimed."
            return out
        report = json.loads(run["stdout"])
        report["file"] = "the design"
        for key in ("exported",):
            report.pop(key, None)
        for item in [e for n in report.get("norms", []) for e in n.values() if isinstance(e, dict)] + \
                [w for c in report.get("conflicts", []) for w in c.get("witnesses", [])]:
            w = item.get("witness", item)
            if isinstance(w, dict):
                w.pop("file", None)     # the text shows the choices, not a temporary path
        out["report"] = report
        out["text"] = format_text(report)
        files = sorted(p for p in export_dir.rglob("*") if p.is_file()) if export_dir.is_dir() else []
        out["files"] = [p.relative_to(export_dir).as_posix() for p in files]
        size = sum(p.stat().st_size for p in files)
        if size > MAX_EXPORT_BYTES:
            out["notice"] = (f"The model, property files and witnesses take {size / (1 << 20):.0f} MB, "
                             f"more than {MAX_EXPORT_BYTES >> 20} MB, so they are not offered for "
                             f"download; run `cadl norms-check --export DIR` on your machine.")
        elif files:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in files:
                    zf.write(p, p.relative_to(export_dir).as_posix())
            out["zip"] = buffer.getvalue()
    return out


def norms_monitor_notes(source: str, target: str) -> dict:
    """The norms monitors that the OPA or Solidity target generates for a design
    with norms 0.1 (Appendix D, D.5; cadl 0.3.25 and 0.3.26), and what `cadl codegen` warns
    about them: {"files": [monitor files], "warnings": [texts]}. Empty for other
    targets and designs."""
    out: dict = {"files": [], "warnings": []}
    try:
        from cadl.codegen import norms_monitors
        monitors = norms_monitors(_parse(source), target)
    except Exception:           # an older cadl, or a design that does not generate
        return out
    for m in monitors:
        cid, name = m["contract"], m["file"]
        if m["text"] is None:
            out["warnings"].append(
                f"Contract {cid}: no norms monitor is generated for this target, so its norms, "
                f"counts-as rules and items are not monitored ({'; '.join(m['reasons'])}); the "
                f"Python target monitors them (norms_monitor.py).")
            continue
        out["files"].append(name)
        for key, pos, text, reason in m["left_out"]:
            out["warnings"].append(
                f"The {key} item {pos} of contract {cid} is not monitored by {name} ({reason}): "
                f"`{text}`.")
    return out


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


_STATE_GATE = re.compile(r"\bstate\s*==\s*['\"]?([A-Za-z_]\w*)['\"]?")
_STATE_IN = re.compile(r"\bstate\s+IN\s*\[([^\]]*)\]")   # the rule grammar writes IN in capitals


def monitor_states(rule: str) -> Optional[set]:
    """Lifecycle states in which a monitor rule can match, or None for any.

    Only the plain form is read: a rule that is a conjunction containing
    ``state == X`` can match in X alone, and one containing
    ``state IN [X, Y]`` in X or Y. A rule with OR or NOT, with no such
    term, or with gates no state satisfies, is not restricted (the answer errs on the side of showing a
    move that cannot happen rather than hiding one that can).
    """
    text = str(rule or "")
    if re.search(r"\b(OR|NOT)\b|\|\||!", text):
        return None
    gates = [{s} for s in _STATE_GATE.findall(text)]
    gates += [{s.strip() for s in group.split(",") if s.strip()}
              for group in _STATE_IN.findall(text)]
    if not gates:
        return None
    states = set.intersection(*gates)   # AND: every gate must hold
    # A rule no state satisfies (`state IN []`, `state == A AND state == B`)
    # is shown as unrestricted rather than silently dropped.
    return states or None


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
        gate = monitor_states(m.get("rule"))
        if m.get("on_match_transition") and (gate is None or state in gate):
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
