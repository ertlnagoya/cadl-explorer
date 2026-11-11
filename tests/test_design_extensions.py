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


def test_extensions_in_effect_as_cadl_reads_them():
    assert ds.extensions_in_effect(NORMS) == {"norms"}
    assert ds.extensions_in_effect(AGENTS) == {"norms", "agents"}
    assert ds.extensions_in_effect(STOCHASTIC) == {"stochastic"}
    assert ds.extensions_in_effect(ds.load_example("supply chain")) == set()
    assert ds.extensions_in_effect("sos: [") == set()
    # another version is not norms 0.1; agents needs norms
    assert ds.extensions_in_effect(NORMS.replace("- norms: 0.1", "- norms: 0.2")) == set()
    assert ds.extensions_in_effect(AGENTS.replace("    - norms: 0.1\n", "")) == set()


def test_a_norm_can_move_the_contract_to_a_state():
    # report_failure's violation requests the move to Violated (on_violation: transition)
    lifecycle = [f for f in ds.analyze(NORMS).findings if f.stage == "Lifecycle"]
    assert [f.level for f in lifecycle] == ["pass"]
    # a norm acts only while the contract is not in a terminal state
    terminal_start = NORMS.replace("initial:  Proposed", "initial:  Completed")
    assert any("State 'Violated' cannot be reached" in f.message
               for f in ds.analyze(terminal_start).findings)
    broken = NORMS.replace("transition: Violated", "transition: Lost")
    assert any("Norm 'report_failure' requests a move to undeclared state 'Lost'" in f.title + f.message
               for f in ds.analyze(broken).findings)


def test_the_norms_monitor_watches_every_guarantee():
    # norms_monitor.py (cadl 0.3.22) decides every assume and guarantee item
    extra = NORMS.replace('"within(300s, ROBOT[i].status == Delivered)"',
                          '"within(300s, ROBOT[i].status == Delivered)"\n        - "ROBOT[i].battery > 5"')
    assert extra != NORMS
    ir = ds.analyze(extra).ir
    promises = dcon.coverage(ir, extra)
    assert all(p.watched_by == ["norms monitor (generated Python)"]
               for p in promises if p.kind == "guarantee")
    assert dcon.coverage_findings(ir, promises) == []
    # not without norms 0.1: cadl generates no monitor then
    for other in (extra.replace("- norms: 0.1", "- norms: 0.2"), extra.replace("    - norms: 0.1\n", "")):
        assert not any(p.watched_by for p in dcon.coverage(ds.analyze(other).ir, other)
                       if p.kind == "guarantee")


def test_read_back_states_the_norms():
    lines = rb.describe_design(NORMS)["DELIVERY_SLA"]
    assert ("Norm acknowledge (obligation): ROBOT[i] must send `ack(accepted)` to DISPATCHER, after "
            "`DISPATCHER -> ROBOT[i] : route_assignment`, within 5 s. Violating it brings a Major "
            "violation and the reparation report_failure.") in lines
    assert any(x.startswith("Norm no_low_battery_driving (prohibition): ROBOT[i] must not bring about")
               for x in lines)
    assert "Norm reassign (power): DISPATCHER can bring about `reassignment`." in lines
    assert "`DISPATCHER -> ROBOT[i] : replan_command` counts as reassignment." in lines
    # a message sent by someone other than the bearer, and a counts-as condition
    other = NORMS.replace('content: "ROBOT[i] -> DISPATCHER : failure_report"',
                          'content: "DISPATCHER -> CUSTOMER[1] : notice"')
    other = other.replace("creates: reassignment", "creates: reassignment\n          if: \"ROBOT[i].battery > 20\"")
    lines = rb.describe_design(other)["DELIVERY_SLA"]
    assert any(x.startswith("Norm report_failure (obligation): ROBOT[i] must bring about "
                            "`DISPATCHER -> CUSTOMER[1] : notice`") for x in lines)
    assert ("`DISPATCHER -> ROBOT[i] : replan_command` counts as reassignment when "
            "`ROBOT[i].battery > 20` holds.") in lines


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
    # B without norms 0.1 cannot take effect; A with type-check errors cannot be amended
    dropped = ds.norms_diff(NORMS, NORMS.replace("    - norms: 0.1\n", ""))
    assert not dropped["ok"] and "B does not declare 'norms: 0.1'" in dropped["errors"][0]
    invalid = ds.norms_diff(NORMS.replace("reparation: report_failure", "reparation: nope"), NORMS)
    assert invalid["invalid_old"] and not invalid["ok"]
    assert ds.norms_diff(ds.load_example("supply chain"), ds.load_example("supply chain")) is None


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
