"""What the Designer takes from cadl 0.3.33 and 0.3.34: the regimes of a run (the
key `regime` of a trace, A.7.1, F.5), the versions of a run (`norms-eval
--version`, the time base, Appendix K, K.11) and `cadl metrics-eval`."""

from __future__ import annotations

import json
import os

import pytest

from tests.conftest import AppTest

pytest.importorskip("cadl.metrics_eval")     # cadl 0.3.34 or later

from backend.services import design_runtime as runtime  # noqa: E402
from backend.services import design_service as ds  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DESIGNS = os.path.join(ROOT, "designs")
NORMS_V1 = ds.load_example("amendment norms v1")
NORMS_V2 = ds.load_example("amendment norms v2")
TOWN_V1 = ds.load_example("amendment robot town v1")
TOWN_V2 = ds.load_example("amendment robot town v2")


def _read(*parts):
    with open(os.path.join(DESIGNS, *parts), encoding="utf-8") as f:
        return f.read()


SWITCH = _read("traces", "amendment_norms_v1_a_switch.jsonl")
KEEP = _read("traces", "amendment_norms_v1_b_keep.jsonl")
MEASUREMENTS = _read("measurements", "amendment_robot_town_v1_measurements.jsonl")


def test_the_versions_of_a_run_change_the_verdict():
    one = runtime.norms_eval(NORMS_V1, SWITCH)["report"]
    two = runtime.norms_eval(NORMS_V1, SWITCH, None, [("v2", NORMS_V2)], "3600s")
    assert not two["failure"]
    report = two["report"]
    assert report["versions"]["sequence"] == ["1", "2"]
    assert [e["event"] for e in report["versions"]["events"]] == ["switch"]
    verdict = {n["id"]: n["verdict"] for n in report["norms"]}
    assert verdict["pick_up"] == "violated"            # the deadline of version 2, 90 s
    assert {n["id"]: n["verdict"] for n in one["norms"]}["pick_up"] != "violated"
    kept = runtime.norms_eval(NORMS_V1, KEEP, None, [("v2", NORMS_V2)], "3600s")["report"]
    assert kept["versions"]["follows"] == "1"


def test_a_failure_names_the_version_and_the_trace_as_the_page_does():
    out = runtime.norms_eval(NORMS_V1, SWITCH, None, [("saved v2", TOWN_V2)], "3600s")
    assert out["failure"].startswith("saved v2 does not declare 'norms: 0.1'")
    out = runtime.norms_eval(NORMS_V1, SWITCH, None, [("v2", NORMS_V2)], "an hour")
    assert "not a duration: 'an hour'" in out["failure"]
    out = runtime.metrics_eval(TOWN_V1, MEASUREMENTS)
    assert "the measurements: line 1" in out["failure"]
    assert "/" not in out["failure"].split(":")[1]


def test_metrics_eval_judges_each_measurement():
    out = runtime.metrics_eval(TOWN_V1, MEASUREMENTS, [("v2", TOWN_V2)], "3600s")
    report = out["report"]
    assert report["counts"] == {"met": 3, "not met": 2, "not judged": 1}
    assert report["file"] == "the design"
    judged = report["measurements"]
    assert any(m["verdict"] == "not judged" and "changes inside the window" in m["reason"]
               for m in judged)
    assert any(m["decision"] == "K5" for m in judged)


def test_a_trace_may_name_the_regime():
    # the clock of a contract stops while a regime suspends it (cadl 0.3.33)
    source = ds.load_example("norms robot delivery").rstrip("\n") + """
  transitions:
    - { from: NORMAL, to: STORM, condition: "wind >= 20" }
    - { from: STORM, to: NORMAL, condition: "wind < 20" }

  regimes:
    STORM:
      contracts: { suspended: [DELIVERY_SLA] }
"""
    records = [
        {"t": 0, "bindings": {"i": 2}, "values": {"DISPATCHER.is_operational": True,
                                                  "ROBOT[2].battery": 80,
                                                  "ROBOT[2].status": "Idle"}},
        {"t": 1, "messages": [{"from": "DISPATCHER", "to": "ROBOT[2]",
                               "name": "route_assignment"}]},
        {"t": 3, "regime": "STORM"}, {"t": 60, "regime": "NORMAL"}, {"t": 70}]
    trace = "\n".join(json.dumps(r) for r in records) + "\n"
    report = runtime.norms_eval(source, trace, "DELIVERY_SLA")["report"]
    ack = next(n for n in report["norms"] if n["id"] == "acknowledge")
    assert "passed at t=63" in ack["reason"]           # 2 s before STORM, 3 s after it


# ── The pages ─────────────────────────────────────────────────────────

def _designer(source, **state):
    if AppTest is None:
        pytest.skip("this Streamlit has no AppTest")
    at = AppTest.from_file(os.path.join(ROOT, "views", "designer.py"), default_timeout=60).run()
    at.session_state.design_src = source
    at.session_state.design_ver += 1
    for key, value in state.items():
        at.session_state[key] = value
    return at.run()


def test_traces_offers_the_other_version_of_the_example_and_its_period():
    at = _designer(NORMS_V1, design_stage="Verify", design_view="Traces",
                   design_trace=SWITCH)
    assert not at.exception
    versions = next(m for m in at.multiselect if m.key == "design_trace_versions")
    assert versions.value == ["example:amendment_norms_v2"]
    assert next(x for x in at.text_input if x.key == "design_trace_period").value == "3600s"
    assert any("**Versions of the run**" in m.value for m in at.markdown)
    events = next(d.value for d in at.dataframe if "what happened" in d.value.columns)
    assert list(events["event"]) == ["switch"]


def test_traces_without_the_extension_has_no_versions():
    at = _designer(ds.load_example("norms robot delivery"), design_stage="Verify",
                   design_view="Traces")
    assert not at.exception
    assert not any(m.key == "design_trace_versions" for m in at.multiselect)


def test_the_measurements_view_judges_the_example():
    # the period of the robot town is filled in, so the example works as it is
    at = _designer(TOWN_V1, design_stage="Verify", design_view="Measurements",
                   design_measure=MEASUREMENTS)
    assert not at.exception
    assert [m.value for m in at.metric] == ["3", "2", "1"]
    table = next(d.value for d in at.dataframe if "verdict" in d.value.columns)
    assert len(table) == 6


def test_the_measurements_view_without_metrics():
    at = _designer(ds.load_example("norms robot delivery"), design_stage="Verify",
                   design_view="Measurements")
    assert not at.exception
    assert any("declares no metrics" in i.value for i in at.info)


# ── Regressions of the review ─────────────────────────────────────────

def test_the_origin_is_passed_exactly():
    assert runtime.time_base("1h", 1763337600.0) == ["--period", "1h", "--origin", "1763337600"]
    assert runtime.time_base(None, 0.5, True) == ["--origin", "0.5", "--epoch"]


def test_a_version_that_cannot_be_read_is_a_failure():
    out = runtime.norms_eval(NORMS_V1, SWITCH, None, [("broken", "sos: [")], "3600s")
    assert out["failure"].startswith("broken: ")
    assert out["failure"].endswith("nothing is claimed.")


def test_the_warnings_of_cadl_reach_the_page():
    out = runtime.norms_eval(NORMS_V1, SWITCH, None, [("v2", NORMS_V2)])
    assert any("no time base" in n for n in out["notes"])
    at = _designer(NORMS_V1, design_stage="Verify", design_view="Traces", design_trace=SWITCH)
    at.session_state["design_trace_period"] = ""
    at = at.run()
    assert any("no time base" in w.value for w in at.warning)


def test_the_choices_follow_the_design_and_not_the_language():
    # the page in Japanese labels the example 例：…; the chosen id stays the same
    at = _designer(NORMS_V1, lang="日本語", design_stage="Verify", design_view="Traces",
                   design_trace=SWITCH)
    assert not at.exception
    versions = next(m for m in at.multiselect if m.key == "design_trace_versions")
    assert versions.value == ["example:amendment_norms_v2"]


def test_the_choices_start_again_for_another_design():
    # in English: the AppTest of Streamlit 1.50 cannot rerun the Japanese stage picker
    at = _designer(NORMS_V1, design_stage="Verify", design_view="Traces", design_trace=SWITCH)
    at.session_state.design_src = NORMS_V2         # another design: the choices start again
    at.session_state.design_ver += 1
    at = at.run()
    versions = next(m for m in at.multiselect if m.key == "design_trace_versions")
    assert versions.value == ["example:amendment_norms_v1"]
    assert at.session_state["design_trace_period"] == ""   # v2 names no period


def test_summary_records_a_run_with_other_versions_again(monkeypatch):
    from views._design import trace
    seen = []
    monkeypatch.setattr("views._design.verify.record_run",
                        lambda *a, **k: seen.append(a), raising=True)
    monkeypatch.setattr(trace, "ss", {})
    report = {"norms": [{"verdict": "violated"}]}
    trace._record("norms-eval", NORMS_V1, SWITCH, report, ("C", (), (None, 0.0, False)))
    trace._record("norms-eval", NORMS_V1, SWITCH, report, ("C", (), (None, 0.0, False)))
    trace._record("norms-eval", NORMS_V1, SWITCH, report,
                  ("C", (("v2", NORMS_V2),), ("3600s", 0.0, False)))
    assert len(seen) == 2
