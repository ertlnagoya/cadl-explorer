"""Tests of the Violation Timeline (research plan A1–A3).

The analysis and the proposals are tested on small logs written here and on
the bundled example logs, which were made with the unchanged SoS-DSL runtime
(see cadl_sim/sos_dsl/examples/violations/README.md). The runner itself is
tested only where the runtime is on this machine (CADL_RUNTIME_PATH).
"""

import json
import os
from pathlib import Path

import pytest

from cadl_sim.sos_dsl import contract_tuning as tuning
from cadl_sim.sos_dsl import violation_trace as vt

ROOT = Path(__file__).parent.parent
EXAMPLES = ROOT / "cadl_sim" / "sos_dsl" / "examples"
IR = json.loads((EXAMPLES / "sos_dsl_robot_delivery.ir.json").read_text(encoding="utf-8"))


def _log(name):
    recs, problems = vt.parse_ndjson((EXAMPLES / "violations" / name).read_text(encoding="utf-8"))
    assert not problems
    return recs


def _view(name, rule="earliest_onset"):
    (trial,) = vt.split_trials(_log(name), name)
    return vt.analyze_trial(trial, IR, rule)


def _inst(view, iid):
    return next(s for s in view.instances if s.instance_id == iid)


def L(iid, frm, to, t, track=None):
    r = {"kind": "lifecycle", "contract_id": "DELIVERY_SLA", "instance_id": iid,
         "transition_id": "x", "from_state": frm, "to_state": to, "t_ms": t, "cause": "event"}
    if track:
        r["track"] = track
    return r


def V(iid, by, t, track=None):
    r = {"kind": "violation", "contract_id": "DELIVERY_SLA", "instance_id": iid,
         "norm": by.split(":")[1], "severity": "Major", "t_ms": t, "detected_by": by}
    if track:
        r["track"] = track
    return r


# --- reading ---------------------------------------------------------------

def test_parse_ndjson_reports_bad_lines():
    recs, problems = vt.parse_ndjson('{"kind": "lifecycle"}\n\nnot json\n{"no_kind": 1}\n')
    assert len(recs) == 1
    assert len(problems) == 2


def test_plain_runtime_log_is_read_as_the_report():
    """A log of `multi_robot_demo --log`, without any of the additions."""
    recs = [r for r in _log("demo_accept_5s.ndjson")
            if r["kind"] in ("lifecycle", "violation") and "track" not in r]
    recs = [{k: v for k, v in r.items() if k != "trial"} for r in recs]
    (trial,) = vt.split_trials(recs, "plain")
    view = vt.analyze_trial(trial, IR)
    assert not trial.has_continued
    r2 = _inst(view, "robot-2-1")
    assert r2.reported.detected_by == "monitor:battery_guard"
    assert r2.unreported == [] and r2.expected == []
    assert _inst(view, "robot-0-1").final_state == "Completed"


def test_split_trials_by_trial_field():
    trials = vt.split_trials(_log("batch_example.ndjson"), "b")
    assert len(trials) == 40
    assert all(tr.run and tr.run["scenario"] == "baseline" for tr in trials)


def test_truth_from_a_separate_file():
    recs = [L("a", "", "Proposed", 0), L("a", "Proposed", "Assigned", 100),
            V("a", "deadline:accept", 1100), L("a", "Assigned", "Violated", 1100)]
    (trial,) = vt.split_trials(recs, "t")
    trial = vt.merge_truth(trial, [{"instance_id": "a", "t_ms": 400, "fault": "battery_drop"}])
    s = vt.analyze_trial(trial, IR).instances[0]
    assert s.expected == ["monitor:battery_guard"]
    assert s.reported_matches is False


# --- continued evaluation and the candidate cause ----------------------------

def test_masking_in_the_plan_figure():
    """accept = 300ms: the deadline expires before battery_guard sees the battery
    drop of robot-2-1 (the plan's Figure 1)."""
    view = _view("demo_accept_300ms.ndjson")
    r2 = _inst(view, "robot-2-1")
    assert r2.reported.detected_by == "deadline:accept"
    assert [d.detected_by for d in r2.unreported] == ["monitor:battery_guard"]
    assert r2.masked
    # battery_guard is periodic (500ms): recorded at 550, may have begun after 50.
    bg = r2.unreported[0]
    assert (bg.t_ms, bg.onset_lo) == (550, 50)
    assert r2.candidate.detected_by == "monitor:battery_guard"
    assert r2.candidate_ambiguous          # 400 lies inside (50, 550]
    assert r2.reported_matches is False and r2.candidate_matches is True
    assert r2.reasons == ["deadline_before_fault"]
    # the run's own IR is used: accept is 300ms there
    assert vt.find_transition(view.ir, "DELIVERY_SLA", "accept")["deadline_ms"] == 300


def test_earliest_record_rule_names_the_deadline():
    r2 = _inst(_view("demo_accept_300ms.ndjson", "earliest_record"), "robot-2-1")
    assert r2.candidate.detected_by == "deadline:accept"
    assert r2.candidate_matches is False


def test_accept_1s_keeps_battery_guard_as_the_report():
    """With accept = 1s the Python demo still reports battery_guard for robot-2-1
    (the exchange seen in Unity is not reproduced); the deadline is masked."""
    r2 = _inst(_view("demo_accept_1s.ndjson"), "robot-2-1")
    assert r2.reported.detected_by == "monitor:battery_guard"
    assert [(d.detected_by, d.t_ms) for d in r2.unreported] == [("deadline:accept", 1100)]
    assert r2.reported_matches is True


def test_intervals_of_both_tracks():
    view = _view("demo_accept_300ms.ndjson")
    ivs = view.intervals["robot-2-1"]
    rep = [(i.state, i.start) for i in ivs if i.track == "reported"]
    con = [(i.state, i.start, i.end) for i in ivs if i.track == vt.CONTINUED]
    assert rep[-1] == ("Violated", 400)
    assert con[-1][0] == "Assigned" and con[-1][2] == view.trial.end_ms


def test_wilson_interval():
    p, lo, hi = vt.wilson(4, 5)
    assert p == 0.8 and round(lo, 2) == 0.38 and round(hi, 2) == 0.96
    assert vt.wilson(0, 0) == (None, None, None)


# --- A1 ----------------------------------------------------------------------

def test_measures_of_the_bundled_batch():
    views = [vt.analyze_trial(tr, IR) for tr in vt.split_trials(_log("batch_example.ndjson"), "b")]
    rows = {r["condition"]: r for r in vt.measure(views)}
    short = rows["deadline:accept=5000, dwell_ms=100, fault=battery_drop"]
    assert short["recall k/n"] == "0/5"        # Assigned for 100ms, battery_guard every 500ms
    acks = rows["deadline:accept=5000, dwell_ms=700, fault=ack_missing"]
    assert acks["reported match k/n"] == "5/5"
    reasons = {(r["condition"], r["reason"]) for r in vt.reason_counts(views)}
    assert ("deadline:accept=5000, dwell_ms=100, fault=battery_drop",
            "dwell_shorter_than_period") in reasons
    units = vt.unit_rows(views)
    assert len(units) == 40


# --- A3 ----------------------------------------------------------------------

def test_r1_lengthens_the_deadline_of_the_run():
    view = _view("demo_accept_300ms.ndjson")
    props = tuning.propose([view], IR)
    r1 = next(p for p in props if p.rule == "R1")
    assert r1.override == {"contract": "DELIVERY_SLA", "kind": "transition", "id": "accept",
                           "field": "deadline_ms", "value": 600}
    assert r1.old == 300
    assert "deadline: 300ms → deadline: 600ms" in r1.change
    assert ("demo_accept_300ms", "robot-2-1") in r1.instances


def test_r2_shortens_a_period_longer_than_a_stay():
    views = [vt.analyze_trial(tr, IR) for tr in vt.split_trials(_log("batch_example.ndjson"), "b")]
    r2 = next(p for p in tuning.propose(views, IR) if p.rule == "R2")
    assert r2.override["id"] == "battery_guard"
    assert r2.override["value"] == 100 and r2.old == 500


def test_assumptions_change_nothing():
    props = tuning.propose([_view("demo_accept_300ms.ndjson")], IR)
    a = [p for p in props if p.rule == "A1"]
    assert a and all(p.override is None for p in a)
    assert any("battery < 20" in p.assumption for p in a)


def test_apply_overrides_copies_the_ir():
    o = {"contract": "DELIVERY_SLA", "kind": "monitor", "id": "battery_guard",
         "field": "sampling_period_ms", "value": 100}
    out = vt.apply_overrides(IR, [o])
    assert vt.find_monitor(out, "DELIVERY_SLA", "battery_guard")["sampling_period_ms"] == 100
    assert vt.find_monitor(IR, "DELIVERY_SLA", "battery_guard")["sampling_period_ms"] == 500
    with pytest.raises(KeyError):
        vt.apply_overrides(IR, [dict(o, id="nope")])


def test_compare_counts_resolution_and_side_effects():
    before = [L("a", "", "Proposed", 0), L("a", "Proposed", "Assigned", 100),
              {"kind": "injection", "instance_id": "a", "t_ms": 300, "fault": "battery_drop"},
              V("a", "deadline:accept", 400), L("a", "Assigned", "Violated", 400),
              L("b", "", "Proposed", 0), L("b", "Proposed", "Completed", 900)]
    after = [L("a", "", "Proposed", 0), L("a", "Proposed", "Assigned", 100),
             {"kind": "injection", "instance_id": "a", "t_ms": 300, "fault": "battery_drop"},
             V("a", "monitor:battery_guard", 550), L("a", "Assigned", "Violated", 550),
             L("b", "", "Proposed", 0), L("b", "Proposed", "Violated", 900)]
    vb = [vt.analyze_trial(tr, IR) for tr in vt.split_trials(before, "x")]
    va = [vt.analyze_trial(tr, IR) for tr in vt.split_trials(after, "x")]
    p = tuning.Proposal("R1", {"contract": "DELIVERY_SLA", "kind": "transition", "id": "accept",
                               "field": "deadline_ms", "value": 600}, 300, "", [("x", "a")])
    rows, summary = tuning.compare(vb, va, [p])
    assert summary["resolution k/n"] == "1/1"
    assert summary["side effect k/n"] == "1/1"       # b changed its final state
    assert len(rows) == 2


# --- the runner (needs the runtime) --------------------------------------------

RUNTIME = os.environ.get("CADL_RUNTIME_PATH")


@pytest.mark.skipif(not RUNTIME, reason="CADL_RUNTIME_PATH is not set")
def test_runner_report_equals_the_runtime_alone():
    """The report of a run here is the log the runtime writes by itself."""
    from cadl_sim.sos_dsl import runtime_runner as runner
    handle = runner.load_runtime(RUNTIME)
    rt = handle.engine.ContractRuntime(IR)
    handle.demo.run_scenario(rt)
    alone = sorted(json.dumps(r, sort_keys=True) for r in rt.log)
    recs = runner.run_trial(handle, IR, scenario="demo", trial="t")
    here = sorted(json.dumps({k: v for k, v in r.items() if k != "trial"}, sort_keys=True)
                  for r in recs if r["kind"] in ("lifecycle", "violation") and "track" not in r)
    assert here == alone


@pytest.mark.skipif(not RUNTIME, reason="CADL_RUNTIME_PATH is not set")
def test_runner_rerun_with_r1_resolves_the_masking():
    from cadl_sim.sos_dsl import runtime_runner as runner
    handle = runner.load_runtime(RUNTIME)
    view = _view("demo_accept_300ms.ndjson")
    r1 = next(p for p in tuning.propose([view], IR) if p.rule == "R1")
    after = runner.rerun(handle, IR, view.trial.run, [r1.override])
    (trial,) = vt.split_trials(after, "a")
    r2 = _inst(vt.analyze_trial(trial, IR), "robot-2-1")
    assert r2.reported.detected_by == "monitor:battery_guard"


@pytest.mark.skipif(not RUNTIME, reason="CADL_RUNTIME_PATH is not set")
def test_runner_injects_faults_into_the_baseline():
    from cadl_sim.sos_dsl import runtime_runner as runner
    handle = runner.load_runtime(RUNTIME)
    recs = runner.run_trial(handle, IR, params=runner.BaselineParams(dwell_ms=700),
                            injections=[runner.Injection("robot-1-1", 700, "battery_drop",
                                                         {"level": 10.0}),
                                        runner.Injection("robot-3-1", 0, "ack_missing",
                                                         {"transition": "accept"})])
    (trial,) = vt.split_trials(recs, "t")
    view = vt.analyze_trial(trial, IR)
    assert _inst(view, "robot-1-1").reported.detected_by == "monitor:battery_guard"
    assert _inst(view, "robot-3-1").reported.detected_by == "deadline:accept"
    assert _inst(view, "robot-0-1").final_state == "Completed"


# --- A2: what the chart draws is what the log says --------------------------------

@pytest.mark.parametrize("name", ["demo_accept_5s.ndjson", "demo_accept_1s.ndjson",
                                  "demo_accept_300ms.ndjson"])
def test_timeline_chart_matches_the_log(name):
    """Every violation and injection of the log is a mark at its time, and every
    state of an instance begins a band at the time of its lifecycle record."""
    pytest.importorskip("altair")
    from views import _violations as draw

    view = _view(name)
    spec = draw.timeline_chart(view, [s.instance_id for s in view.instances]).to_dict()
    rows = [r for data in spec["datasets"].values() for r in data]
    bars = {(r["lane"], r["state"], r["from (ms)"]) for r in rows if "state" in r and r["lane"]}
    marks = {(r["lane"], r["mark"], r["what"], r["t"]) for r in rows if "mark" in r}
    recs = _log(name)
    for r in recs:
        if r["kind"] == "lifecycle":
            lane = draw.lane_label(r["instance_id"], r.get("track", "reported"))
            assert (lane, r["to_state"], r["t_ms"]) in bars, r
    reported = {(r["instance_id"], r["detected_by"]) for r in recs
                if r["kind"] == "violation" and "track" not in r}
    for r in recs:
        if r["kind"] == "violation":
            iid, by = r["instance_id"], r["detected_by"]
            if "track" not in r:
                key = (draw.lane_label(iid, "reported"), draw.mark_label("reported"), by, r["t_ms"])
            elif (iid, by) in reported:
                continue              # the same violation, already marked as reported
            else:
                key = (draw.lane_label(iid, vt.CONTINUED), draw.mark_label("unreported"), by,
                       r["t_ms"])
            assert key in marks, r
        elif r["kind"] == "injection":
            key = (draw.lane_label(r["instance_id"], "reported"), draw.mark_label("injected"),
                   r["fault"], r["t_ms"])
            assert key in marks, r
