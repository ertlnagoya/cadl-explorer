"""What the Designer takes from cadl 0.3.35 and 0.3.36: the versions of a run in
`cadl agents-run` (Appendix K, K.11), `cadl codegen --version` for the targets
opa, solidity and unity-csharp, and `cadl norms-check --version` with `keep`
and `switch` with `in_states:`."""

from __future__ import annotations

import hashlib
import os

import pytest

from tests.conftest import AppTest

from backend.services import design_runtime as runtime  # noqa: E402
from backend.services import design_service as ds  # noqa: E402

if not runtime.supports_codegen_versions():     # cadl 0.3.36 or later
    pytest.skip("cadl is older than 0.3.36", allow_module_level=True)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AGENTS_V1 = ds.load_example("agents versions v1")
AGENTS_V2 = ds.load_example("agents versions v2")
NORMS_V1 = ds.load_example("amendment norms v1")
NORMS_V2 = ds.load_example("amendment norms v2")
with open(os.path.join(ROOT, "designs", "traces", "agents_versions_v1_a_switch.jsonl"),
          encoding="utf-8") as f:
    AGENTS_TRACE = f.read()


def test_the_new_examples_pass_the_checks():
    for source in (AGENTS_V1, AGENTS_V2):
        assert not [f for f in ds.analyze(source).findings if f.level == "error"]


def test_agents_run_switches_at_the_effective_time():
    out = runtime.agents_run(AGENTS_V1, AGENTS_TRACE, [("v2", AGENTS_V2)], "3600s")
    assert not out["failure"]
    report = out["report"]
    assert [s["result"]["decision"] for s in report["steps"]] == ["deny", "deny", "allow", "allow"]
    assert [(a["file"], a["contracts"]) for a in report["amendments"]] == [
        ("version 2", ["ASSISTED_REPORTING"])]
    # without a time base the first version stays in force, and cadl says so
    report = runtime.agents_run(AGENTS_V1, AGENTS_TRACE, [("v2", AGENTS_V2)])["report"]
    assert [s["result"]["decision"] for s in report["steps"]] == ["deny"] * 4
    assert any("no time base" in w for w in report["warnings"])
    # without versions: as before
    assert runtime.agents_run(AGENTS_V1, AGENTS_TRACE)["report"]["amendments"] == []


def test_codegen_follows_the_versions_of_the_run():
    notes = ds.norms_monitor_notes(NORMS_V2, "opa")
    assert notes["files"] == [] and "cadl codegen --version" in notes["warnings"][0]
    notes = ds.norms_monitor_notes(NORMS_V2, "opa", (("v1", NORMS_V1),))
    assert notes["files"] == ["norms_monitor_delivery_sla.rego"] and not notes["warnings"]
    with pytest.raises(Exception, match="switch to it"):
        ds.export_codegen_zip(NORMS_V2, "unity-csharp")
    assert ds.export_codegen_zip(NORMS_V2, "unity-csharp", (("v1", NORMS_V1),))
    with pytest.raises(ValueError, match="python"):
        ds.export_codegen_zip(NORMS_V2, "python", (("v1", NORMS_V1),))
    with pytest.raises(ValueError, match="^broken: "):
        ds.export_codegen_zip(NORMS_V2, "opa", (("broken", "sos: ["),))


def test_norms_check_takes_a_switch_with_in_states():
    out = ds.norms_check(NORMS_V1, "DELIVERY_SLA", versions=[("v2", NORMS_V2)], timeout=60)
    assert not out["failure"], out["failure"]
    assert out["report"]["instance"]["versions"]
    if not out["report"].get("checked"):
        return                          # no PRISM here: the model is still built
    w = out["report"]["norms"][0]["violable"]["witness"]
    assert {r.get("version") for r in map(__import__("json").loads, w["trace"])} == {"1", "2"}


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


def test_agents_offers_the_other_version_and_its_period():
    at = _designer(AGENTS_V1, design_stage="Verify", design_view="Agents",
                   design_agents_trace=AGENTS_TRACE)
    assert not at.exception
    versions = next(m for m in at.multiselect if m.key == "design_agents_run_versions")
    assert versions.value == ["example:agents_versions_v2"]
    assert next(x for x in at.text_input if x.key == "design_agents_run_period").value == "3600s"
    steps = next(d.value for d in at.dataframe if "decision" in d.value.columns)
    assert list(steps["decision"]) == ["deny", "deny", "allow", "allow"]
    assert any("Versions and amendments during the run" in m.value for m in at.markdown)


def test_export_passes_the_versions_to_unity():
    at = _designer(NORMS_V2, design_stage="Deliver", design_view="Export",
                   design_code_target="unity-csharp")
    assert not at.exception
    versions = next(m for m in at.multiselect if m.key == "design_code_versions")
    assert versions.value == ["example:amendment_norms_v1"]
    assert not [e for e in at.error if "Cannot generate" in e.value]
    # no versions: Unity C# refuses the file, with the reason
    at.multiselect(key="design_code_versions").set_value([]).run()
    assert any("Cannot generate" in e.value for e in at.error)


def test_export_without_versions_for_python():
    at = _designer(NORMS_V2, design_stage="Deliver", design_view="Export",
                   design_code_target="python")
    assert not at.exception
    assert not any(m.key == "design_code_versions" for m in at.multiselect)


def test_checks_offers_the_example_version():
    at = _designer(NORMS_V1, design_stage="Verify", design_view="Checks")
    assert not at.exception
    assert at.multiselect(key="design_nc_versions").options == ["example: amendment_norms_v2"]


class _State(dict):
    __getattr__ = dict.__getitem__
    __setattr__ = dict.__setitem__


def _witness_state(monkeypatch):
    from views._design import trace, verify
    state = _State(design_src=NORMS_V1)
    monkeypatch.setattr(verify, "ss", state)
    monkeypatch.setattr(trace, "ss", state)
    return verify, state


def test_a_witness_with_versions_opens_under_traces_with_them(monkeypatch):
    verify, state = _witness_state(monkeypatch)
    verify._to_traces('{"t": 0, "version": "1"}', "DELIVERY_SLA", ("example:amendment_norms_v2",))
    assert state["design_view"] == "Traces"
    assert state["design_trace_versions"] == ["example:amendment_norms_v2"]
    assert state["design_trace_period"] == ""
    assert state["design_trace_design"] == hashlib.sha256(NORMS_V1.encode()).hexdigest()


# ── Regressions of the review ─────────────────────────────────────────

def test_a_witness_without_versions_opens_under_traces_without_them(monkeypatch):
    # norms-check ran on the design alone: the example's version 2 is not applied
    verify, state = _witness_state(monkeypatch)
    verify._to_traces('{"t": 0}', "DELIVERY_SLA", ())
    assert state["design_trace_versions"] == [] and state["design_trace_period"] == ""
    assert state["design_trace_design"] == hashlib.sha256(NORMS_V1.encode()).hexdigest()


def test_the_choices_come_back_after_another_target():
    at = _designer(NORMS_V2, design_stage="Deliver", design_view="Export",
                   design_code_target="unity-csharp")
    at.selectbox(key="design_code_target").set_value("python").run()
    at.selectbox(key="design_code_target").set_value("unity-csharp").run()
    assert not at.exception
    assert at.multiselect(key="design_code_versions").value == ["example:amendment_norms_v1"]
    assert not [e for e in at.error if "Cannot generate" in e.value]


def test_the_choices_come_back_after_another_view():
    at = _designer(AGENTS_V1, design_stage="Verify", design_view="Agents",
                   design_agents_trace=AGENTS_TRACE)
    at.session_state["design_agents_run_period"] = "1800s"     # the person's own choice
    at = at.run()
    at.session_state["design_view"] = "Traces"
    at = at.run()
    at.session_state["design_view"] = "Agents"
    at = at.run()
    assert not at.exception
    assert at.multiselect(key="design_agents_run_versions").value == ["example:agents_versions_v2"]
    assert at.text_input(key="design_agents_run_period").value == "1800s"


def test_the_name_of_an_example_version_does_not_depend_on_the_language(monkeypatch):
    from views._design import trace
    monkeypatch.setattr(trace, "ss", {})
    candidates = trace._version_candidates(NORMS_V1)
    assert candidates["example:amendment_norms_v2"][0] == "amendment_norms_v2"
    assert trace.version_label("example:amendment_norms_v2", candidates) == \
        "example: amendment_norms_v2"
