"""Proposals of contract parameters from the candidate causes (A3).

Stdlib only. The rules read what continued evaluation recorded and the IR;
they never read the ground truth (the injections), which is used only to
judge the proposals afterwards (``compare``).

A change is an *override* of the IR::

    {"contract": "DELIVERY_SLA", "kind": "transition", "id": "accept",
     "field": "deadline_ms", "value": 1500}
    {"contract": "DELIVERY_SLA", "kind": "monitor", "id": "battery_guard",
     "field": "sampling_period_ms", "value": 250}

The same form describes the conditions of a run (``run.ir_overrides``), so a
run can be repeated with the proposals added.

Improvements of a constituent system (a robot) cannot be made by the designer
of the SoS; they are given as an assumption the contract would ask for, and
change nothing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from .violation_trace import (
    CONTINUED,
    apply_overrides,
    TrialView,
    find_contract,
    find_monitor,
    find_transition,
    period_ms,
    watched_states,
)

STEP_MS = 50          # the clock of the reference runtime's demo


# ---------------------------------------------------------------------------
# Overrides of the IR
# ---------------------------------------------------------------------------

def merge_overrides(*lists: Sequence[dict]) -> List[dict]:
    """Later overrides of the same target and field replace earlier ones."""
    out: Dict[Tuple[str, str, str, str], dict] = {}
    for lst in lists:
        for o in lst or []:
            out[(o["contract"], o["kind"], o["id"], o["field"])] = dict(o)
    return list(out.values())


def fmt_ms(ms: Optional[int]) -> str:
    if ms is None:
        return "—"
    ms = int(ms)
    if ms % 1000 == 0:
        return f"{ms // 1000}s"
    return f"{ms}ms"


def cadl_change(o: dict, old: Any) -> str:
    """The change as it would be written in the CADL source."""
    if o["field"] == "deadline_ms":
        return (f"{o['contract']} › lifecycle › {o['id']}: "
                f"deadline: {fmt_ms(old)} → deadline: {fmt_ms(o['value'])}")
    if o["field"] == "sampling_period_ms":
        return (f"{o['contract']} › monitors › {o['id']}: "
                f"sampling: periodic({fmt_ms(old)}) → periodic({fmt_ms(o['value'])})")
    return f"{o['contract']} › {o['id']}: {o['field']}: {old} → {o['value']}"


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------

RULES = {
    "R1": "A deadline is reported, but the candidate cause is another violation: "
          "lengthen that deadline so that the candidate is detected first.",
    "R2": "An instance stayed in the state a periodic monitor watches for less than "
          "the monitor's period: shorten the period to the shortest stay.",
    "R3": "A monitor is reported, but the candidate cause is another periodic monitor: "
          "halve the candidate monitor's period.",
    "A1": "The candidate cause is a violation by the robot: state, as an assumption "
          "of the contract, what the robot is asked to keep.",
}


@dataclass
class Proposal:
    rule: str
    override: Optional[dict]              # None: an assumption, no change of the IR
    old: Any
    reason: str
    instances: List[Tuple[str, str]] = field(default_factory=list)   # (trial, instance)
    assumption: str = ""

    @property
    def key(self) -> str:
        if self.override:
            o = self.override
            return f"{self.rule}:{o['contract']}:{o['kind']}:{o['id']}:{o['field']}"
        return f"{self.rule}:{self.assumption}"

    @property
    def change(self) -> str:
        if self.override:
            return cadl_change(self.override, self.old)
        return f"assume: {self.assumption}"


def _ceil_to(ms: float, unit: int = 100) -> int:
    return int(math.ceil(ms / unit) * unit)


def _floor_to(ms: float, unit: int = STEP_MS) -> int:
    return max(unit, int(math.floor(ms / unit) * unit))


def propose(views: Sequence[TrialView], ir: dict) -> List[Proposal]:
    """Proposals for the trials, one per target, from the candidate causes."""
    found: Dict[str, Proposal] = {}

    def add(p: Proposal, unit: Tuple[str, str], better) -> None:
        old = found.get(p.key)
        if old is None:
            found[p.key] = p
            p.instances = [unit]
            return
        if unit not in old.instances:
            old.instances.append(unit)
        if p.override and better(p.override["value"], old.override["value"]):
            old.override = p.override
            old.reason = p.reason

    base_ir = ir
    for v in views:
        tid = v.trial.trial_id
        ir = v.ir or base_ir
        for s in v.instances:
            unit = (tid, s.instance_id)
            rep, cand = s.reported, s.candidate
            # R1: a deadline masks the candidate.
            if (rep and cand and rep.detected_by.startswith("deadline:")
                    and cand.detected_by != rep.detected_by):
                tr_id = rep.detected_by.split(":", 1)[1]
                tr = find_transition(ir, s.contract_id, tr_id)
                if tr and tr.get("deadline_ms"):
                    old = int(tr["deadline_ms"])
                    new = _ceil_to(old + max(0, cand.t_ms - rep.t_ms) + 100)
                    add(Proposal("R1", {"contract": s.contract_id, "kind": "transition",
                                        "id": tr_id, "field": "deadline_ms", "value": new},
                                 old, f"{s.instance_id}: {rep.detected_by} at {rep.t_ms} ms "
                                      f"was reported; {cand.detected_by} was recorded at "
                                      f"{cand.t_ms} ms and may have begun first"),
                        unit, lambda a, b: a > b)
            # R3: one monitor masks another.
            if (rep and cand and rep.detected_by.startswith("monitor:")
                    and cand.detected_by.startswith("monitor:")
                    and cand.detected_by != rep.detected_by):
                mid = cand.detected_by.split(":", 1)[1]
                p = period_ms(find_monitor(ir, s.contract_id, mid))
                if p and p > STEP_MS:
                    new = _floor_to(p / 2)
                    add(Proposal("R3", {"contract": s.contract_id, "kind": "monitor",
                                        "id": mid, "field": "sampling_period_ms", "value": new},
                                 p, f"{s.instance_id}: {rep.detected_by} was reported; "
                                    f"{cand.detected_by} may have begun earlier"),
                        unit, lambda a, b: a < b)
            # A1: an assumption on the robot.
            if cand:
                text = _assumption(ir, s.contract_id, cand.detected_by)
                if text:
                    add(Proposal("A1", None, None,
                                 f"{s.instance_id}: candidate cause {cand.detected_by}",
                                 assumption=text), unit, lambda a, b: False)
        # R2: stays shorter than a monitor's period (whatever was reported).
        for s in v.instances:
            c = find_contract(ir, s.contract_id) or {}
            for m in c.get("monitors") or []:
                p = period_ms(m)
                states = watched_states(m)
                if not p or not states:
                    continue
                stays = [iv.end - iv.start for iv in v.intervals.get(s.instance_id, [])
                         if iv.track == "reported" and iv.state in states and iv.closed]
                short = [d for d in stays if d < p]
                if not short:
                    continue
                d = min(short)
                if d < STEP_MS:
                    reason = (f"{s.instance_id} stayed {d} ms in {'/'.join(states)}, "
                              f"less than one step of the clock: a periodic monitor cannot "
                              f"see it; consider `sampling: event`")
                    new = STEP_MS
                else:
                    reason = (f"{s.instance_id} stayed {d} ms in {'/'.join(states)}; "
                              f"{m.get('id')} is evaluated every {p} ms")
                    new = _floor_to(d)
                if new >= p:
                    continue
                add(Proposal("R2", {"contract": s.contract_id, "kind": "monitor",
                                    "id": m.get("id"), "field": "sampling_period_ms",
                                    "value": new}, p, reason),
                    (v.trial.trial_id, s.instance_id), lambda a, b: a < b)
    order = {"R1": 0, "R2": 1, "R3": 2, "A1": 3}
    return sorted(found.values(), key=lambda p: (order.get(p.rule, 9), p.key))


def _assumption(ir: dict, contract_id: str, detected_by: str) -> str:
    kind, _, name = detected_by.partition(":")
    if kind == "monitor":
        m = find_monitor(ir, contract_id, name)
        if m and m.get("rule"):
            return f"NOT ({m['rule']})   — kept by ROBOT[i] ({name})"
    if kind == "deadline":
        tr = find_transition(ir, contract_id, name)
        if tr and tr.get("on"):
            return (f"`{tr['on']}` within {fmt_ms(tr.get('deadline_ms'))} "
                    f"of entering {'/'.join(tr.get('from_states') or [])}")
    return ""


# ---------------------------------------------------------------------------
# Before and after (judged with the ground truth)
# ---------------------------------------------------------------------------

def compare(before: Sequence[TrialView], after: Sequence[TrialView],
            proposals: Sequence[Proposal]) -> Tuple[List[dict], Dict[str, Any]]:
    """Instance by instance, and the measures of the plan's A3.

    resolution: of the targeted instances with a fault that the report named
      wrongly before, the share that it names correctly after.
    side effect: of the instances without a fault, the share whose final
      state changed.
    coverage: of the trials with masking, the share with a proposal.
    """
    targeted = {u for p in proposals for u in p.instances}
    after_by = {(v.trial.trial_id, s.instance_id): s for v in after for s in v.instances}
    rows: List[dict] = []
    wrong_before = fixed = 0
    clean = changed = 0
    for v in before:
        for s in v.instances:
            key = (v.trial.trial_id, s.instance_id)
            a = after_by.get(key)
            if a is None:
                continue
            row = {
                "trial": key[0], "instance": key[1],
                "targeted": key in targeted,
                "fault": ", ".join(str(i.get("fault")) for i in s.injections),
                "reported before": s.reported.detected_by if s.reported else "",
                "reported after": a.reported.detected_by if a.reported else "",
                "final before": s.final_state, "final after": a.final_state,
                "matches before": s.reported_matches, "matches after": a.reported_matches,
            }
            rows.append(row)
            if s.expected:
                if key in targeted and not s.reported_matches:
                    wrong_before += 1
                    fixed += 1 if a.reported_matches else 0
            else:
                clean += 1
                changed += 1 if a.final_state != s.final_state else 0
    masked_trials = [v.trial.trial_id for v in before
                     if any(s.masked or s.reported_matches is False for s in v.instances)]
    with_prop = {t for t, _ in targeted}
    covered = [t for t in masked_trials if t in with_prop]
    summary = {
        "resolution": (fixed / wrong_before) if wrong_before else None,
        "resolution k/n": f"{fixed}/{wrong_before}",
        "side effect": (changed / clean) if clean else None,
        "side effect k/n": f"{changed}/{clean}",
        "coverage": (len(covered) / len(masked_trials)) if masked_trials else None,
        "coverage k/n": f"{len(covered)}/{len(masked_trials)}",
        "without a proposal": [t for t in masked_trials if t not in with_prop],
    }
    return rows, summary


__all__ = [
    "CONTINUED", "Proposal", "RULES", "apply_overrides", "cadl_change", "compare",
    "fmt_ms", "merge_overrides", "propose",
]
