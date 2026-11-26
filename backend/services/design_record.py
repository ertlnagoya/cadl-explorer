"""The Designer's decisions as a decision record of Appendix L (decisions.jsonl).

The Designer keeps, in the session, every change decided on the Change stage:
an amendment of a `# @amend:` value, or a change of deadlines and monitor
periods proposed by the Violation Timeline. Each entry is a dict::

    {"time": "14:03:12", "at": "2026-11-26T05:03:12Z",
     "change": "block_k: 3 → 2", "layer": "rule", "reason": "...",
     "by": "designer" | "violation-timeline",
     "changes": [{"parameter": ..., "from": ..., "to": ..., "layer": ...}],
     "before": <source>, "after": <source>}

Entries saved by an older version of the app have only time, change, layer
and reason; they become a proposal and a decision without a version.

``to_decisions`` writes them as a record that ``cadl decisions-verify`` and the
Decision Evidence page read: a version entry for the design before the first
decision, and for each decision a proposal, the decision and, when the
sources are known, the version it made. The versions are returned as files
(v1.cadl, v2.cadl, …) to check L-9 to L-11.
"""

from __future__ import annotations

import hashlib
import re
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

DESIGNER = "designer"
VIOLATION_TIMELINE = "violation-timeline"
LAYERS = ("rule", "contract", "regulation")
NO_REASON = "No reason was written in the Designer."

PARTIES = {
    DESIGNER: {"id": DESIGNER, "kind": "human", "roles": ["owner", "proposer", "decider"],
               "name": "the designer (CADL Explorer, Designer)", "actor": None, "model": None,
               "on_behalf_of": None, "layers": None},
    VIOLATION_TIMELINE: {"id": VIOLATION_TIMELINE, "kind": "tool", "roles": ["proposer"],
                         "name": "CADL Explorer, Violation Timeline",
                         "actor": None, "model": "cadl-explorer contract_tuning",
                         "on_behalf_of": None, "layers": None},
}


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sos_version(source: str) -> Optional[str]:
    """The `sos.version` of a design, or None."""
    try:
        import yaml
        doc = yaml.safe_load(source) or {}
    except Exception:
        return None
    value = (doc.get("sos") or {}).get("version") if isinstance(doc, dict) else None
    return None if value is None else str(value)


def system_name(source: str) -> str:
    """The `sos.name` of a design, or "design"."""
    try:
        import yaml
        doc = yaml.safe_load(source) or {}
        return str((doc.get("sos") or {}).get("name") or "design")
    except Exception:
        return "design"


def to_zip(text: str, files: Dict[str, bytes]) -> bytes:
    """decisions.jsonl with the files of its versions, as a .zip."""
    import io
    import zipfile
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("decisions.jsonl", text)
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


def with_version(source: str, name: str) -> str:
    """The source with `sos.version` set to ``name``: the line is rewritten and
    the rest of the text, comments included, is kept. Only a direct key of
    `sos:` is rewritten, never a `version:` of a nested mapping."""
    lines = source.splitlines(keepends=True)
    head = next((i for i, line in enumerate(lines) if re.match(r"sos:\s*(#.*)?$", line)), None)
    if head is None:
        return source
    child = None                                        # the indent of the keys of sos
    for i in range(head + 1, len(lines)):
        line = lines[i]
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        indent = len(line) - len(line.lstrip(" "))
        if indent == 0:
            break                                       # the next top-level key
        child = indent if child is None else child
        m = re.match(r"(\s*)version:\s*[^#\r\n]*?(\s*#.*)?(\r?\n)?$", line)
        if m and indent == child:
            lines[i] = f'{m.group(1)}version: "{name}"{m.group(2) or ""}{m.group(3) or ""}'
            return "".join(lines)
    lines.insert(head + 1, f'{" " * (child or 2)}version: "{name}"\n')   # no version: add one
    return "".join(lines)


def _layer(value: Optional[str]) -> Optional[str]:
    return value if value in LAYERS else None


def _text(value) -> Optional[str]:
    return value if isinstance(value, str) and value else None


OUTSIDE = ("The design was changed outside the decisions recorded here: an Undo, a restored "
           "version, an edit of the source or the forms, or another design opened.")


def to_decisions(name: str, decisions: List[dict]) -> Tuple[str, Dict[str, bytes]]:
    """(the text of decisions.jsonl, {file name: content} of the versions).

    ``name`` names the model when no decision knows its design. The versions start at the design before the first decision that knows it
    (entries of an older session do not). When a decision starts from another
    design than the one the previous decision left, the change in between is
    recorded as its own proposal and decision, made outside this record."""
    from cadl.decisions import DecisionChain

    decisions = [d for d in decisions or [] if isinstance(d, dict)]
    if not decisions:
        raise ValueError("there is no decision to write")
    start = next((d["at"] for d in decisions if _text(d.get("at"))), None) or now_iso()
    base = next((d["before"] for d in decisions if _text(d.get("before"))), None)
    files: Dict[str, bytes] = {}
    parties = [PARTIES[DESIGNER]]
    if any(d.get("by") == VIOLATION_TIMELINE for d in decisions):
        parties.append(PARTIES[VIOLATION_TIMELINE])

    chain = DecisionChain()
    first_name = (sos_version(base) or "1") if base else "1"
    lines = [chain.header(
        model={"name": (system_name(base) if base else name) or "design",
               "version": first_name,
               "source_sha256": _sha(base if sos_version(base) else with_version(base, "1"))
               if base else None},
        parties=parties, started=start,
        time_base="the time of the Designer's session (UTC)")]

    def common(entry_id: str, kind: str, at: str, by: str, basis: list, attachments=()):
        return {"id": entry_id, "type": kind, "point": {"at": at}, "written": at, "by": by,
                "basis": basis, "fulfils": [], "attachments": list(attachments),
                "generated": None}

    # A version is named by the `sos.version` of its file (L-9). An amendment in
    # the Designer keeps that value, so a version whose file repeats an earlier
    # name is exported with `<first>+<n>` written into its `sos.version`.
    state = {"no": 0, "last": None, "names": [], "sha": None, "n": 0}

    def name_for(source: str) -> Tuple[str, str]:
        name = sos_version(source)
        if not state["names"]:
            name = name or "1"
            return name, source if sos_version(source) else with_version(source, name)
        if name is None or name in state["names"]:
            name = f"{state['names'][0]}+{state['no']}"
            source = with_version(source, name)
        return name, source

    def version(source: str, at: str, decision: Optional[str], original: str) -> str:
        name, source = name_for(source)
        state["no"] += 1
        path, v_id = f"v{state['no']}.cadl", f"V{state['no']}"
        files[path] = source.encode("utf-8")
        lines.append(chain.entry({
            **common(v_id, "version", at, DESIGNER, [decision] if decision else [],
                     [{"path": path, "sha256": _sha(source), "media_type": "application/yaml"}]),
            "version": name, "previous": state["names"][-1] if state["names"] else None,
            "effective": {"at": at}, "source_sha256": _sha(source), "decision": decision}))
        state["names"].append(name)
        state["last"], state["sha"] = v_id, _sha(original)
        return name

    def decide(at: str, proposer: str, title: str, changes: list, proposal_reason: str,
               reason: str, question: str, after: Optional[str]) -> None:
        state["n"] += 1
        p_id, d_id = f"P{state['n']}", f"D{state['n']}"
        basis = [state["last"]] if state["last"] else []
        result = name_for(after)[0] if after else None
        lines.append(chain.entry({
            **common(p_id, "proposal", at, proposer, basis),
            "kind": "amendment", "title": title, "changes": changes, "objective": None,
            "measure": [], "affected": [], "reason": proposal_reason,
            "assumptions": [], "unknowns": [], "predictions": []}))
        lines.append(chain.entry({
            **common(d_id, "decision", at, DESIGNER, basis + [p_id]),
            "question": question, "options": [p_id], "choice": p_id, "procedure": "single",
            "rule": None, "affected": [], "result": result, "reason": reason,
            "assumptions": [], "unknowns": [], "predictions": []}))
        if after:
            version(after, at, d_id, after)

    last_at = start
    for d in decisions:
        at = _text(d.get("at")) or last_at        # an older entry has no time: the one before it
        last_at = at
        before, after = _text(d.get("before")), _text(d.get("after"))
        if before and state["last"] is None:
            version(before, at, None, before)
        elif before and _sha(before) != state["sha"]:
            nxt = name_for(before)[0]
            decide(at, DESIGNER, "Changes made outside the recorded decisions",
                   [{"parameter": "the design", "from": state["names"][-1], "to": nxt,
                     "layer": None}], OUTSIDE, OUTSIDE,
                   "Record the design as it was before the next decision?", before)
        proposer = d.get("by") if d.get("by") in PARTIES else DESIGNER
        reason = (_text(d.get("reason")) or "").strip() or NO_REASON
        changes = [c for c in d.get("changes") or [] if isinstance(c, dict)] or [
            {"parameter": str(d.get("change", "")), "from": None, "to": None,
             "layer": _layer(d.get("layer"))}]
        changes = [{"parameter": str(c.get("parameter", "")),
                    "from": None if c.get("from") is None else str(c.get("from")),
                    "to": None if c.get("to") is None else str(c.get("to")),
                    "layer": _layer(c.get("layer"))} for c in changes]
        change = str(d.get("change", ""))
        decide(at, proposer, _text(d.get("title")) or change, changes,
               ((_text(d.get("proposal_reason")) or "").strip() or reason)
               if proposer != DESIGNER else reason,
               reason, f"Adopt {change}?" if change else "Adopt the proposal?",
               after if state["last"] else None)
    lines.append(chain.end())
    return "\n".join(lines) + "\n", files
