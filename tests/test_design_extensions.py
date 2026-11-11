"""The extensions of cadl-lang 0.3.19 to 0.3.22: norms 0.1, agents 0.1, stochastic 0.1."""
import io
import zipfile

import pytest

pytest.importorskip("cadl.norms_diff")

from backend.services import design_contracts as dcon
from backend.services import design_readback as rb
from backend.services import design_service as ds

NORMS = ds.load_example("norms robot delivery")
AGENTS = ds.load_example("agents tool governance")
STOCHASTIC = ds.load_example("stochastic robot delivery")


def test_declared_extensions():
    assert ds.declared_extensions(NORMS) == {"sos-dsl": "0.1", "norms": "0.1"}
    assert ds.declared_extensions(AGENTS) == {"norms": "0.1", "agents": "0.1"}
    assert ds.declared_extensions(ds.load_example("supply chain")) == {}
    assert ds.declared_extensions("sos: [") == {}


def test_a_norm_can_move_the_contract_to_a_state():
    # report_failure's violation requests the move to Violated (on_violation: transition)
    lifecycle = [f for f in ds.analyze(NORMS).findings if f.stage == "Lifecycle"]
    assert [f.level for f in lifecycle] == ["pass"]
    broken = NORMS.replace("transition: Violated", "transition: Lost")
    assert any("Norm 'report_failure' requests a move to undeclared state 'Lost'" in f.title + f.message
               for f in ds.analyze(broken).findings)


def test_a_temporal_guarantee_is_watched_by_the_norms_monitor():
    ir = ds.analyze(NORMS).ir
    promises = dcon.coverage(ir, NORMS)
    within = next(p for p in promises if p.text.startswith("within("))
    assert within.watched_by == ["norms monitor (within, norms 0.1)"]
    assert dcon.coverage_findings(ir, promises) == []
    # without the declaration `within` is an ordinary function name
    plain = NORMS.replace("    - norms: 0.1\n", "")
    assert not next(p for p in dcon.coverage(ds.analyze(plain).ir, plain)
                    if p.text.startswith("within(")).watched_by


def test_read_back_states_the_norms():
    lines = rb.describe_design(NORMS)["DELIVERY_SLA"]
    assert ("Norm acknowledge (obligation): ROBOT[i] must send `ack(accepted)` to DISPATCHER, after "
            "`DISPATCHER -> ROBOT[i] : route_assignment`, within 5 s. Violating it brings a Major "
            "violation and the reparation report_failure.") in lines
    assert any(x.startswith("Norm no_low_battery_driving (prohibition): ROBOT[i] must not bring about")
               for x in lines)
    assert "Norm reassign (power): DISPATCHER can bring about reassignment." in lines
    assert "`DISPATCHER -> ROBOT[i] : replan_command` counts as reassignment." in lines


def test_norms_diff_of_an_amendment():
    v2 = NORMS.replace("deadline: 5s", "deadline: 3s", 1)
    result = ds.norms_diff(NORMS, v2)
    assert result["ok"]
    texts = [f["text"] for c in result["contracts"] for f in c["findings"]]
    assert any(t.startswith("acknowledge: carries over, and deadline changes from '5s' to '3s'")
               for t in texts)
    # a transform target the new version lacks prevents the amendment
    bad = NORMS.replace("on_amendment: discharge", "on_amendment: {transform: nothing_like_it}")
    assert not ds.norms_diff(bad, NORMS)["ok"]
    # only between two versions with norms
    assert ds.norms_diff(NORMS, ds.load_example("supply chain")) is None


def test_agent_policies_and_prism_files():
    for target in ds.AGENT_CODEGEN_TARGETS:
        names = zipfile.ZipFile(io.BytesIO(ds.export_codegen_zip(AGENTS, target))).namelist()
        assert names, target
    assert "agent_policy.cedar" in zipfile.ZipFile(
        io.BytesIO(ds.export_codegen_zip(AGENTS, "agent-cedar"))).namelist()
    python = zipfile.ZipFile(io.BytesIO(ds.export_codegen_zip(NORMS, "python"))).namelist()
    assert any(n.endswith("norms_monitor.py") for n in python)
    files = ds.prism_files(STOCHASTIC)
    assert sorted(files) == ["RobotDeliveryStochastic_CENTRALIZED.prism",
                             "RobotDeliveryStochastic_CENTRALIZED.props",
                             "RobotDeliveryStochastic_DISTRIBUTED.prism",
                             "RobotDeliveryStochastic_DISTRIBUTED.props"]
    with pytest.raises(ValueError):
        ds.prism_files(NORMS)


def test_the_extension_examples_have_no_errors():
    for source in (NORMS, AGENTS, STOCHASTIC):
        analysis = ds.analyze(source)
        assert analysis.parsed
        assert not [f for f in analysis.findings if f.level == "error"]
