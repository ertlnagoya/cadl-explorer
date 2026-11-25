"""Changes proposed by the Violation Timeline, written into a CADL source.

A proposal of the Violation Timeline is an override of the simulator IR
(cadl_sim/sos_dsl/contract_tuning.py)::

    {"contract": "DELIVERY_SLA", "kind": "transition", "id": "accept",
     "field": "deadline_ms", "value": 1500}
    {"contract": "DELIVERY_SLA", "kind": "monitor", "id": "battery_guard",
     "field": "sampling_period_ms", "value": 250}

``apply_to_source`` writes such overrides into the source of the design. It
rewrites the one line (`deadline:` of the transition, `sampling:` of the
monitor) and keeps the rest of the text, comments included. When the line
cannot be found, it falls back to the structured changes of design_ops,
which rewrite the whole document without comments; it does not when the design
marks values with `# @amend:`, which would be lost. A value marked
`# @amend:<name>` is never written here: the constitution gives it its unit,
range and step, so it is amended on the Change stage's amendable parameters.
Either way the result is checked: the IR of the new source must hold the new
values.
"""

from __future__ import annotations

import re
from typing import List, Optional, Tuple

from backend.services import design_amend as amend
from backend.services import design_ops as ops
from backend.services import design_service as ds
from cadl_sim.sos_dsl.contract_tuning import fmt_ms
from cadl_sim.sos_dsl.violation_trace import find_monitor, find_transition

FIELDS = {"deadline_ms": ("transition", "deadline"),
          "sampling_period_ms": ("monitor", "sampling")}


class TuningError(ValueError):
    """A proposal that cannot be written into the source; the message says why."""


def cadl_value(override: dict) -> str:
    if override["field"] == "deadline_ms":
        return fmt_ms(override["value"])
    if override["field"] == "sampling_period_ms":
        return f"periodic({fmt_ms(override['value'])})"
    raise TuningError(f"cannot write the field {override['field']}")


def parameter(override: dict) -> str:
    """The changed value, named as in the source: DELIVERY_SLA.lifecycle.accept.deadline."""
    where = "lifecycle" if override["kind"] == "transition" else "monitors"
    return f"{override['contract']}.{where}.{override['id']}.{FIELDS[override['field']][1]}"


def op_for(override: dict) -> dict:
    kind, key = FIELDS[override["field"]]
    return {"op": "upsert_transition" if kind == "transition" else "upsert_monitor",
            "contract": override["contract"], "id": override["id"],
            "fields": {key: cadl_value(override)}}


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _block_end(lines: List[str], start: int, indent: int) -> int:
    """The index after the block of the list item at ``start``."""
    for j in range(start + 1, len(lines)):
        text = lines[j].strip()
        if text and not text.startswith("#") and _indent(lines[j]) <= indent:
            return j
    return len(lines)


def _item(lines: List[str], lo: int, hi: int, item_id: str) -> Optional[Tuple[int, int]]:
    pat = re.compile(r"^(\s*)-\s*id:\s*[\"']?" + re.escape(item_id) + r"[\"']?\s*(#.*)?$")
    for i in range(lo, hi):
        m = pat.match(lines[i].rstrip("\r\n"))
        if m:
            return i, _block_end(lines, i, len(m.group(1)))
    return None


def _rewrite_line(source: str, override: dict) -> Optional[str]:
    """The source with the one line rewritten, or None when it is not found."""
    lines = source.splitlines(keepends=True)
    contract = _item(lines, 0, len(lines), override["contract"])
    if contract is None:
        return None
    section = "lifecycle" if override["kind"] == "transition" else "monitors"
    sec_pat = re.compile(r"^(\s*)" + section + r":\s*(#.*)?$")
    lo, hi = contract[0] + 1, contract[1]
    for i in range(lo, hi):                      # only inside the contract's section
        m = sec_pat.match(lines[i].rstrip("\r\n"))
        if m:
            lo, hi = i + 1, _block_end(lines, i, len(m.group(1)))
            break
    else:
        return None
    item = _item(lines, lo, hi, override["id"])
    if item is None:
        return None
    key = FIELDS[override["field"]][1]
    item_indent = _indent(lines[item[0]])
    pat = re.compile(r"^(\s*)" + key + r":\s*[^#\r\n]*?(\s*#.*)?(\r?\n)?$")
    for j in range(item[0] + 1, item[1]):
        m = pat.match(lines[j])
        if m and _indent(lines[j]) > item_indent:
            tag = re.search(r"#\s*@amend:\s*([\w.\-]+)", lines[j])
            if tag:
                # The constitution gives the amendable value its unit, range and step,
                # and the value may stand on other lines too: amend it there.
                raise TuningError(
                    f"{parameter(override)} is marked `# @amend:{tag.group(1)}`; amend "
                    f"{tag.group(1)} under Amendable parameters, where the constitution "
                    "checks it")
            lines[j] = f"{m.group(1)}{key}: {cadl_value(override)}{m.group(2) or ''}{m.group(3) or ''}"
            return "".join(lines)
    return None


def current(ir: Optional[dict], override: dict):
    """The value the design has now for the field of ``override``, or None."""
    find = find_transition if override["kind"] == "transition" else find_monitor
    return (find(ir, override["contract"], override["id"]) or {}).get(override["field"])


def _holds(ir: Optional[dict], override: dict) -> bool:
    if override["kind"] == "transition":
        found = find_transition(ir, override["contract"], override["id"]) or {}
    else:
        found = find_monitor(ir, override["contract"], override["id"]) or {}
    return found.get(override["field"]) == override["value"]


def apply_to_source(source: str, overrides: List[dict]) -> Tuple[str, bool]:
    """(the new source, whether the comments were kept). Raises TuningError."""
    if not overrides:
        raise TuningError("no change is chosen")
    text, kept = source, True
    before = ds.analyze(source)
    for o in overrides:
        if o.get("field") not in FIELDS:
            raise TuningError(f"cannot write the field {o.get('field')}")
        find = find_transition if o["kind"] == "transition" else find_monitor
        if find(before.ir, o["contract"], o["id"]) is None:     # change, never add
            raise TuningError(f"the design has no {o['kind']} {o['contract']}.{o['id']}")
        rewritten = _rewrite_line(text, o)
        if rewritten is None:
            if amend.amendables(text):
                raise TuningError(
                    f"{parameter(o)} has no line to rewrite, and rewriting the whole design "
                    "would drop its `# @amend:` marks; change the value in the source")
            kept = False
            try:
                rewritten, _ = ops.apply_ops(text, [op_for(o)])
            except ops.OpError as e:
                raise TuningError(str(e)) from e
        text = rewritten
    analysis = ds.analyze(text)
    if not analysis.parsed:
        raise TuningError("the changed design cannot be read")
    missing = [parameter(o) for o in overrides if not _holds(analysis.ir, o)]
    if missing:
        raise TuningError("the IR of the changed design does not hold " + ", ".join(missing))
    return text, kept
