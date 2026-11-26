"""Violations of contract instances, read from the NDJSON log of the runtime.

Stdlib only, like ``lifecycle_view``; the page ``views/violations.py`` draws
what this module computes, and the tests exercise it without Streamlit.

The log
-------
The SoS-DSL reference runtime (``cadl.runtime.engine``, run by
``multi_robot_demo --log``) writes one JSON object per line::

    {"kind": "lifecycle", "contract_id", "instance_id", "transition_id",
     "from_state", "to_state", "t_ms", "cause"}
    {"kind": "violation", "contract_id", "instance_id", "norm", "severity",
     "t_ms", "detected_by"}

Such a log is read as it is: it holds the violations the runtime *reported*.
Three additions are optional, and a log without them stays valid:

``"track": "continued"`` on a lifecycle or violation record
    The record comes from continued evaluation: the instance is assumed not to
    have entered its violation state (the *assumed state*), and deadlines and
    monitors go on being evaluated. A violation is written the first time each
    ``detected_by`` matches for the instance. Records without ``track`` are
    the runtime's own report.

``{"kind": "injection", "instance_id", "t_ms", "fault", "params"?, "expected"?}``
    A fault that was injected: the ground truth. ``expected`` lists the
    ``detected_by`` values that would name this fault correctly; without it,
    ``FAULT_DETECTIONS`` is used. Ground truth is used to *evaluate* only.

``{"kind": "run", "trial", "scenario", "condition", ...}``
    What the run was, so that it can be grouped (A1) and run again (A3).

``"trial"`` on any record splits one file into several runs.
"""

from __future__ import annotations

import copy
import json
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

CONTINUED = "continued"

# The detections that name each kind of fault correctly. "{transition}" is
# filled from the injection's params (default: accept).
FAULT_DETECTIONS: Dict[str, List[str]] = {
    "battery_drop": ["monitor:battery_guard"],
    "over_speed": ["monitor:over_speed"],
    "ack_delay": ["deadline:{transition}"],
    "ack_missing": ["deadline:{transition}"],
    "delivery_stall": ["monitor:deadline_watch"],
}

# How a candidate cause is chosen among the detections of continued evaluation.
CANDIDATE_RULES = ("earliest_onset", "earliest_record")


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------

def parse_ndjson(text: str) -> Tuple[List[dict], List[str]]:
    """Records of an NDJSON text, and a note for every line that is not one."""
    records: List[dict] = []
    problems: List[str] = []
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError as e:
            problems.append(f"line {n}: {e.msg}")
            continue
        if not isinstance(rec, dict) or "kind" not in rec:
            problems.append(f"line {n}: not a record with a `kind`")
            continue
        records.append(rec)
    return records, problems


def to_ndjson(records: Iterable[dict]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in records)


@dataclass
class Trial:
    """One run: its records and, when the log says, what the run was."""
    trial_id: str
    records: List[dict]
    run: Optional[dict] = None

    @property
    def condition(self) -> Dict[str, Any]:
        return dict((self.run or {}).get("condition") or {})

    @property
    def injections(self) -> List[dict]:
        return [r for r in self.records if r.get("kind") == "injection"]

    @property
    def has_continued(self) -> bool:
        return any(r.get("track") == CONTINUED for r in self.records)

    @property
    def end_ms(self) -> int:
        end = (self.run or {}).get("end_ms")
        ts = [int(r.get("t_ms") or 0) for r in self.records if "t_ms" in r]
        return int(max([end or 0] + ts))


def split_trials(records: Sequence[dict], label: str) -> List[Trial]:
    """The trials of one log: by its ``trial`` field, or the whole log as one."""
    order: List[str] = []
    groups: Dict[str, List[dict]] = {}
    runs: Dict[str, dict] = {}
    for r in records:
        tid = str(r["trial"]) if r.get("trial") is not None else label
        if tid not in groups:
            order.append(tid)
            groups[tid] = []
        if r.get("kind") == "run":
            runs[tid] = r
        else:
            groups[tid].append(r)
    return [Trial(tid, groups[tid], runs.get(tid)) for tid in order]


def merge_truth(trial: Trial, truth: Sequence[dict]) -> Trial:
    """The trial with injection records from a separate ground-truth file.

    Records of the file with a ``trial`` field are given only to that trial.
    """
    extra = [dict(r, kind="injection") for r in truth
             if r.get("trial") is None or str(r.get("trial")) == trial.trial_id]
    return Trial(trial.trial_id, list(trial.records) + extra, trial.run)


# ---------------------------------------------------------------------------
# The IR: monitors, deadlines and the states a monitor watches
# ---------------------------------------------------------------------------

def contracts(ir: Optional[dict]) -> List[dict]:
    return list(((ir or {}).get("institution") or {}).get("contracts") or [])


def find_contract(ir: Optional[dict], contract_id: str) -> Optional[dict]:
    for c in contracts(ir):
        if c.get("id") == contract_id:
            return c
    return None


def find_monitor(ir: Optional[dict], contract_id: str, monitor_id: str) -> Optional[dict]:
    c = find_contract(ir, contract_id) or {}
    for m in c.get("monitors") or []:
        if m.get("id") == monitor_id:
            return m
    return None


def find_transition(ir: Optional[dict], contract_id: str, transition_id: str) -> Optional[dict]:
    c = find_contract(ir, contract_id) or {}
    for tr in ((c.get("lifecycle") or {}).get("transitions")) or []:
        if tr.get("id") == transition_id:
            return tr
    return None


_STATE_RE = re.compile(r"\bstate\s*==\s*([A-Za-z_]\w*)")


def watched_states(monitor: dict) -> List[str]:
    """The states a monitor's rule requires (``state == Assigned``), if any."""
    return _STATE_RE.findall(monitor.get("rule") or "")


def period_ms(monitor: Optional[dict]) -> Optional[int]:
    if not monitor or monitor.get("sampling_kind") != "periodic":
        return None
    p = monitor.get("sampling_period_ms")
    return int(p) if p else None


def apply_overrides(ir: dict, overrides: Sequence[dict]) -> dict:
    """A copy of the IR with overrides such as
    ``{"contract", "kind": "transition"|"monitor", "id", "field", "value"}`` set.
    Unknown targets raise KeyError."""
    out = copy.deepcopy(ir)
    for o in overrides or []:
        if find_contract(out, o["contract"]) is None:
            raise KeyError(f"no contract {o['contract']}")
        if o["kind"] == "transition":
            target = find_transition(out, o["contract"], o["id"])
        else:
            target = find_monitor(out, o["contract"], o["id"])
        if target is None:
            raise KeyError(f"no {o['kind']} {o['id']} in {o['contract']}")
        target[o["field"]] = o["value"]
    return out


def trial_ir(base_ir: Optional[dict], trial: "Trial") -> Optional[dict]:
    """The IR the trial ran with: the base IR and the run's ``ir_overrides``."""
    overrides = (trial.run or {}).get("ir_overrides") or []
    if base_ir is None or not overrides:
        return base_ir
    try:
        return apply_overrides(base_ir, overrides)
    except KeyError:
        return base_ir


# ---------------------------------------------------------------------------
# Instances
# ---------------------------------------------------------------------------

@dataclass
class Interval:
    state: str
    start: int
    end: int
    track: str          # "reported" or "continued"
    closed: bool        # False: the state lasts until the end of the run


@dataclass
class Detection:
    """A violation in the log, with the time its condition may have begun.

    A periodic monitor sees its condition only when it is evaluated, so the
    condition began after the previous evaluation: ``onset_lo < t <= t_ms``
    with ``onset_lo = t_ms - period``. A deadline and an event monitor are
    exact: ``onset_lo == t_ms``.
    """
    detected_by: str
    t_ms: int
    onset_lo: int
    norm: str = ""
    severity: str = ""
    track: str = "reported"

    @property
    def exact(self) -> bool:
        return self.onset_lo == self.t_ms


@dataclass
class InstanceSummary:
    instance_id: str
    contract_id: str
    final_state: str
    reported: Optional[Detection]
    detections: List[Detection]          # of continued evaluation (or the report)
    unreported: List[Detection]
    candidate: Optional[Detection]
    candidate_ambiguous: bool
    injections: List[dict]
    expected: List[str]
    has_continued: bool
    reasons: List[str] = field(default_factory=list)

    @property
    def masked(self) -> bool:
        return bool(self.unreported)

    @property
    def injected(self) -> bool:
        return bool(self.injections)

    @property
    def reported_matches(self) -> Optional[bool]:
        """Whether the runtime's report names an injected fault (None: no fault)."""
        if not self.expected:
            return None
        return bool(self.reported and self.reported.detected_by in self.expected)

    @property
    def candidate_matches(self) -> Optional[bool]:
        if not self.expected:
            return None
        return bool(self.candidate and self.candidate.detected_by in self.expected)

    @property
    def recalled(self) -> Optional[bool]:
        """Whether the record (continued evaluation) holds a violation for the fault."""
        if not self.expected:
            return None
        return any(d.detected_by in self.expected for d in self.detections)


def expected_detections(injection: dict) -> List[str]:
    if injection.get("expected") is not None:
        exp = injection["expected"]
        return [exp] if isinstance(exp, str) else list(exp)
    params = dict(injection.get("params") or {})
    params.setdefault("transition", "accept")
    out = []
    for pattern in FAULT_DETECTIONS.get(str(injection.get("fault")), []):
        try:
            out.append(pattern.format(**params))
        except (KeyError, IndexError):
            out.append(pattern)
    return out


def _detection(rec: dict, ir: Optional[dict]) -> Detection:
    t = int(rec.get("t_ms") or 0)
    by = str(rec.get("detected_by") or "")
    lo = t
    if by.startswith("monitor:"):
        p = period_ms(find_monitor(ir, str(rec.get("contract_id") or ""), by.split(":", 1)[1]))
        if p:
            lo = t - p
    return Detection(by, t, lo, str(rec.get("norm") or ""), str(rec.get("severity") or ""),
                     CONTINUED if rec.get("track") == CONTINUED else "reported")


def _intervals(recs: List[dict], track: str, end_ms: int, terminal: Sequence[str]) -> List[Interval]:
    out: List[Interval] = []
    for r in sorted(recs, key=lambda r: int(r.get("t_ms") or 0)):
        t = int(r.get("t_ms") or 0)
        if out:
            out[-1].end, out[-1].closed = t, True
        out.append(Interval(str(r.get("to_state") or "?"), t, end_ms, track, False))
    # A terminal state ends the instance; draw it as a short stub.
    if out and out[-1].state in terminal:
        out[-1].end = out[-1].start
    return [iv for iv in out if iv.end > iv.start or iv.state in terminal]


def choose_candidate(detections: Sequence[Detection], rule: str = "earliest_onset"
                     ) -> Tuple[Optional[Detection], bool]:
    """The candidate cause, and whether its order against another is unsure.

    ``earliest_onset``: the detection whose condition may have begun first
    (the smallest ``onset_lo``). ``earliest_record``: the first one recorded.
    The order is unsure when another detection's window overlaps the chosen
    one's: within a monitor period the log cannot tell which came first.
    No ground truth is used.
    """
    if not detections:
        return None, False
    if rule == "earliest_record":
        cand = min(detections, key=lambda d: (d.t_ms, d.onset_lo))
    else:
        cand = min(detections, key=lambda d: (d.onset_lo, d.t_ms))
    ambiguous = any(
        d is not cand and d.detected_by != cand.detected_by
        and d.onset_lo < cand.t_ms and cand.onset_lo < d.t_ms
        for d in detections)
    return cand, ambiguous


@dataclass
class TrialView:
    trial: Trial
    instances: List[InstanceSummary]
    intervals: Dict[str, List[Interval]]
    ir: Optional[dict] = None           # the IR the trial ran with

    @property
    def masked_instances(self) -> List[InstanceSummary]:
        return [s for s in self.instances if s.masked]


def analyze_trial(trial: Trial, ir: Optional[dict] = None,
                  rule: str = "earliest_onset") -> TrialView:
    """The trial instance by instance. ``ir`` is the base IR; the run's own
    overrides (``run.ir_overrides``) are applied to it."""
    ir = trial_ir(ir, trial)
    end = trial.end_ms
    ids: List[str] = []
    by_inst: Dict[str, List[dict]] = {}
    for r in trial.records:
        iid = r.get("instance_id")
        if iid is None:
            continue
        iid = str(iid)
        if iid not in by_inst:
            ids.append(iid)
            by_inst[iid] = []
        by_inst[iid].append(r)

    summaries: List[InstanceSummary] = []
    intervals: Dict[str, List[Interval]] = {}
    for iid in ids:
        recs = by_inst[iid]
        contract_id = next((str(r["contract_id"]) for r in recs if r.get("contract_id")), "")
        terminal = ((find_contract(ir, contract_id) or {}).get("lifecycle") or {}).get("terminal") \
            or ["Completed", "Violated", "Terminated"]
        life = [r for r in recs if r.get("kind") == "lifecycle"]
        rep_life = [r for r in life if r.get("track") != CONTINUED]
        con_life = [r for r in life if r.get("track") == CONTINUED]
        ivs = _intervals(rep_life, "reported", end, terminal)
        if con_life:
            ivs += _intervals(con_life, CONTINUED, end, terminal)
        intervals[iid] = ivs

        viol = [_detection(r, ir) for r in recs if r.get("kind") == "violation"]
        reported_all = sorted([d for d in viol if d.track == "reported"], key=lambda d: d.t_ms)
        continued = sorted([d for d in viol if d.track == CONTINUED], key=lambda d: d.t_ms)
        has_cont = bool(con_life or continued) or trial.has_continued
        detections = continued if has_cont else reported_all
        reported = reported_all[0] if reported_all else None
        reported_names = {d.detected_by for d in reported_all}
        unreported = [d for d in detections if d.detected_by not in reported_names]
        cand, ambiguous = choose_candidate(detections, rule)
        injections = [r for r in recs if r.get("kind") == "injection"]
        expected: List[str] = []
        for inj in injections:
            for e in expected_detections(inj):
                if e not in expected:
                    expected.append(e)
        final = str(rep_life[-1].get("to_state")) if rep_life else "?"
        s = InstanceSummary(iid, contract_id, final, reported, detections, unreported,
                            cand, ambiguous, injections, expected, has_cont)
        s.reasons = masking_reasons(s, ivs, ir)
        summaries.append(s)
    return TrialView(trial, summaries, intervals, ir)


# ---------------------------------------------------------------------------
# Why the report misses the fault (A1: the conditions of masking)
# ---------------------------------------------------------------------------

REASONS = {
    "deadline_before_fault": "a deadline expired before the fault's violation was detected",
    "other_monitor_first": "another monitor matched before the fault's monitor",
    "other_deadline_first": "another deadline expired before the fault's",
    "dwell_shorter_than_period": "the fault held in the watched state for less than the monitor's period",
    "fault_outside_watched_state": "the fault began after the instance had left the state the monitor watches",
    "not_detected": "no violation names the fault, even in continued evaluation",
    "nothing_reported": "the runtime reported no violation",
}


def masking_reasons(s: InstanceSummary, intervals: Sequence[Interval],
                    ir: Optional[dict]) -> List[str]:
    """Why the report does not name the injected fault (ground truth needed)."""
    if not s.expected or s.reported_matches:
        return []
    reasons: List[str] = []
    rep = s.reported
    if rep is not None:
        exp_kinds = {e.split(":", 1)[0] for e in s.expected}
        if rep.detected_by.startswith("deadline:"):
            reasons.append("deadline_before_fault" if "monitor" in exp_kinds
                           else "other_deadline_first")
        elif rep.detected_by.startswith("monitor:"):
            reasons.append("other_monitor_first")
    if not s.recalled:
        reasons.append(_why_not_detected(s, intervals, ir))
    if not reasons:
        reasons.append("nothing_reported")
    return reasons


def _why_not_detected(s: InstanceSummary, intervals: Sequence[Interval],
                      ir: Optional[dict]) -> str:
    """For a periodic monitor that watches a state: how long the fault and the
    state held together. Shorter than the period, the monitor may not see it."""
    t0 = min(int(i.get("t_ms") or 0) for i in s.injections)
    track = CONTINUED if s.has_continued else "reported"
    for e in s.expected:
        if not e.startswith("monitor:"):
            continue
        m = find_monitor(ir, s.contract_id, e.split(":", 1)[1])
        p = period_ms(m)
        states = watched_states(m or {})
        if not (p and states):
            continue
        together = [iv.end - max(iv.start, t0)
                    for iv in intervals if iv.track == track and iv.state in states]
        together = [d for d in together if d > 0]
        if not together:
            return "fault_outside_watched_state"
        if max(together) < p:
            return "dwell_shorter_than_period"
    return "not_detected"


# ---------------------------------------------------------------------------
# Measurements over many trials (A1)
# ---------------------------------------------------------------------------

def wilson(k: int, n: int, z: float = 1.96) -> Tuple[Optional[float], Optional[float], Optional[float]]:
    """The proportion k/n and its Wilson score interval (95% by default)."""
    if n <= 0:
        return None, None, None
    p = k / n
    d = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / d
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return p, max(0.0, centre - half), min(1.0, centre + half)


def condition_label(cond: Dict[str, Any]) -> str:
    if not cond:
        return "(all)"
    return ", ".join(f"{k}={v}" for k, v in sorted(cond.items()))


def unit_rows(views: Sequence[TrialView]) -> List[dict]:
    """One row per injected instance: what was injected, reported and recorded."""
    rows = []
    for v in views:
        for s in v.instances:
            if not s.expected:
                continue
            rows.append({
                "trial": v.trial.trial_id,
                "condition": condition_label(v.trial.condition),
                "instance": s.instance_id,
                "fault": ", ".join(str(i.get("fault")) for i in s.injections),
                "injected at (ms)": min(int(i.get("t_ms") or 0) for i in s.injections),
                "expected": ", ".join(s.expected),
                "reported": s.reported.detected_by if s.reported else "",
                "candidate": s.candidate.detected_by if s.candidate else "",
                "recalled": s.recalled,
                "reported matches": s.reported_matches,
                "candidate matches": s.candidate_matches,
                "masking": ", ".join(s.reasons),
            })
    return rows


def measure(views: Sequence[TrialView]) -> List[dict]:
    """The A1 measures for each condition, with 95% Wilson intervals.

    The unit of the recall and of the two match rates is an instance with an
    injected fault whose correct detection is known; the masking rate is over
    all violations of continued evaluation, injected or not.
    """
    groups: Dict[str, List[TrialView]] = {}
    for v in views:
        groups.setdefault(condition_label(v.trial.condition), []).append(v)
    out = []
    for label, vs in groups.items():
        units = [s for v in vs for s in v.instances if s.expected]
        n = len(units)
        rec = sum(1 for s in units if s.recalled)
        rep = sum(1 for s in units if s.reported_matches)
        cand = sum(1 for s in units if s.candidate_matches)
        dets = sum(len(s.detections) for v in vs for s in v.instances if s.has_continued)
        unrep = sum(len(s.unreported) for v in vs for s in v.instances if s.has_continued)
        row: Dict[str, Any] = {"condition": label, "trials": len(vs), "units": n}
        for name, k, m in (("recall", rec, n), ("reported match", rep, n),
                           ("candidate match", cand, n), ("masking", unrep, dets)):
            p, lo, hi = wilson(k, m)
            row[name] = p
            row[f"{name} 95% CI"] = (f"[{lo:.2f}, {hi:.2f}]" if p is not None else "")
            row[f"{name} k/n"] = f"{k}/{m}"
        out.append(row)
    return out


def reason_counts(views: Sequence[TrialView]) -> List[dict]:
    counts: Dict[Tuple[str, str], int] = {}
    for v in views:
        label = condition_label(v.trial.condition)
        for s in v.instances:
            for r in s.reasons:
                counts[(label, r)] = counts.get((label, r), 0) + 1
    return [{"condition": c, "reason": r, "explanation": REASONS.get(r, r), "count": n}
            for (c, r), n in sorted(counts.items())]
