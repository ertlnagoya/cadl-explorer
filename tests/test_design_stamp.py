"""STAMP control structure inferred from a design, and the STPA table."""
import pytest

pytest.importorskip("cadl")

from backend.services import design_service as ds
from backend.services import design_stamp as stamp

BASE = """
sos:
  name: Plant
  type: Directed
  actors:
    - {id: OPS, role: operator, autonomy: low}
    - {id: CELL, role: cell, autonomy: medium}
    - {id: SENSOR, role: sensor, autonomy: low}
    - {id: PEER, role: peer, autonomy: high}
  contracts:
    - id: RUN
      parties: [OPS, CELL]
      guarantee: ["x > 0"]
      authority: {decision_holder: OPS}
      lifecycle:
        states: [Idle, Running, Done, Bad]
        initial: Idle
        terminal: [Done, Bad]
        transitions:
          - {id: start, from: Idle, to: Running, on: "OPS -> CELL : start_cmd"}
          - id: finish
            from: Running
            to: Done
            on: "CELL -> OPS : done_report"
            deadline: 30s
            on_violation: {transition: Bad, severity: Major}
  protocols:
    - id: P
      trigger: go
      steps: ["OPS -> CELL : start_cmd", "CELL -> OPS : done_report", "PEER -> CELL : hint"]
"""


def _cs(source):
    analysis = ds.analyze(source)
    assert analysis.parsed, [f.message for f in analysis.findings if f.level == "error"]
    return analysis.ir, stamp.control_structure(analysis.ir)


def test_holder_controls_and_messages_split_into_control_and_feedback():
    ir, cs = _cs(BASE)
    assert cs.levels["OPS"] < cs.levels["CELL"]
    loop = cs.loop("OPS", "CELL")
    assert loop.control == ["start_cmd"] and loop.feedback == ["done_report"] and loop.status == "closed"
    assert ("PEER", "CELL", "hint") in cs.coordination


def test_open_loop_is_reported():
    source = BASE.replace(', "CELL -> OPS : done_report"', "").replace(
        'on: "CELL -> OPS : done_report"', 'on: "CELL.state == finished"')
    ir, cs = _cs(source)
    assert cs.loop("OPS", "CELL").status == "open: no feedback"
    assert any(n.startswith("Open loop: OPS controls CELL") for n in stamp.findings(cs))


def test_a_monitor_is_feedback():
    source = BASE.replace(', "CELL -> OPS : done_report"', "").replace(
        'on: "CELL -> OPS : done_report"', 'on: "CELL.state == finished"').replace(
        "  protocols:", """      monitors:
        - id: temp_watch
          observe: "CELL.temperature"
          sampling: periodic(1s)
          rule: "CELL.temperature > 80"
          on_match: {transition: Bad, severity: Critical}
  protocols:""")
    ir, cs = _cs(source)
    assert cs.loop("OPS", "CELL").feedback == ["monitor temp_watch"]
    assert cs.loop("OPS", "CELL").status == "closed"


def test_uca_rows_carry_the_deadline_that_follows_the_action():
    ir, cs = _cs(BASE)
    rows = stamp.uca_rows(ir, cs)
    assert [r["control action"] for r in rows] == ["start_cmd"]
    assert rows[0]["deadlines in the CADL"] == "then finish within 30s, else Bad"
    assert all(rows[0][g] == "" for g in stamp.GUIDE_WORDS)


def test_no_decision_holder_means_no_control_structure():
    ir, cs = _cs(BASE.replace("      authority: {decision_holder: OPS}\n", ""))
    assert cs.loops == [] and "No contract names a decision holder" in stamp.findings(cs)[0]


def test_controllers_in_a_cycle_share_a_level():
    source = BASE.replace("  protocols:", """    - id: BACK
      parties: [CELL, OPS]
      guarantee: ["y > 0"]
      authority: {decision_holder: CELL}
  protocols:""")
    ir, cs = _cs(source)
    assert cs.levels["OPS"] == cs.levels["CELL"]


@pytest.mark.parametrize("name", list(ds.list_examples()))
def test_every_example(name):
    ir, cs = _cs(ds.load_example(name))
    dot = stamp.control_structure_dot(cs, dark=True)
    assert dot.startswith("digraph") and dot.rstrip().endswith("}")
    stamp.findings(cs)
    stamp.uca_rows(ir, cs)
