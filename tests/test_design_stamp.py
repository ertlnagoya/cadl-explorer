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


def _controls(pairs):
    nodes = sorted({x for p in pairs for x in p})
    controls = {n: set() for n in nodes}
    for a, b in pairs:
        controls[a].add(b)
    return nodes, controls


@pytest.mark.parametrize("pairs", [
    [("A", "B"), ("B", "A"), ("B", "C"), ("C", "D")],          # a cycle with a chain below it
    [("a1", "a0"), ("a0", "a1"), ("a0", "a2")],
    [("a1", "a3"), ("a3", "a1"), ("a3", "a2"), ("a2", "a4")],
])
def test_controlled_is_below_its_controller_even_under_a_cycle(pairs):
    nodes, controls = _controls(pairs)
    level = stamp._levels(nodes, controls)
    comp = stamp._components(nodes, controls)
    for a, b in pairs:
        assert level[a] < level[b] or comp[a] == comp[b], (a, b, level)


def test_levels_hold_on_random_graphs_and_do_not_depend_on_order():
    import random
    rng = random.Random(7)
    for _ in range(3000):
        n = rng.randint(2, 7)
        nodes = [f"n{i}" for i in range(n)]
        pairs = [(a, b) for a in nodes for b in nodes if a != b and rng.random() < 0.3]
        controls = {x: set() for x in nodes}
        for a, b in pairs:
            controls[a].add(b)
        level = stamp._levels(nodes, controls)
        comp = stamp._components(nodes, controls)
        for a, b in pairs:
            assert level[a] < level[b] or comp[a] == comp[b]
        shuffled = nodes[:]
        rng.shuffle(shuffled)
        assert stamp._levels(shuffled, controls) == level


MUTUAL = BASE.replace("  protocols:", """    - id: BACK
      parties: [CELL, OPS]
      guarantee: ["y > 0"]
      authority: {decision_holder: CELL}
  protocols:""")


@pytest.mark.parametrize("source", [MUTUAL, MUTUAL.replace(
    'on: "OPS -> CELL : start_cmd"', 'on: "OPS.state == ready"').replace(
    'on: "CELL -> OPS : done_report"', 'on: "CELL.state == finished"')])
def test_mutual_control_classifies_each_message_once_as_control_and_once_as_feedback(source):
    # The same result whether a message appears once (protocol) or twice (protocol and lifecycle).
    ir, cs = _cs(source)
    down, up = cs.loop("OPS", "CELL"), cs.loop("CELL", "OPS")
    assert (down.control, down.feedback) == (["start_cmd"], ["done_report"])
    assert (up.control, up.feedback) == (["done_report"], ["start_cmd"])
    assert down.status == up.status == "closed"


def test_hints_follow_the_direction_of_the_action():
    source = BASE.replace('          - {id: start, from: Idle, to: Running, on: "OPS -> CELL : start_cmd"}',
                          '          - {id: start, from: Idle, to: Running, on: "OPS -> CELL : start_cmd"}\n'
                          '          - {id: echo, from: Idle, to: Bad, on: "CELL -> OPS : start_cmd", deadline: 7s,'
                          ' on_violation: {transition: Bad, severity: Minor}}')
    ir, cs = _cs(source)
    hint = stamp.uca_rows(ir, cs)[0]["deadlines in the CADL"]
    assert "echo" not in hint and hint == "then finish within 30s, else Bad"


def test_feedback_only_loop_keeps_the_controller_above():
    source = BASE.replace('"OPS -> CELL : start_cmd", ', "").replace(
        'on: "OPS -> CELL : start_cmd"', 'on: "OPS.state == ready"')
    ir, cs = _cs(source)
    assert cs.loop("OPS", "CELL").status == "no control action"
    assert '"OPS" -> "CELL" [style=invis];' in stamp.control_structure_dot(cs)


def test_entries_stay_with_their_control_action():
    rows = [{"controller": "A", "controlled": "B", "control action": "go", **{g: "" for g in stamp.GUIDE_WORDS}},
            {"controller": "A", "controlled": "C", "control action": "stop", **{g: "" for g in stamp.GUIDE_WORDS}}]
    keys = [stamp.uca_key(r) for r in rows]
    typed = [{stamp.GUIDE_WORDS[0]: "B idles"}, {}]
    entries = stamp.update_entries({}, keys, typed)
    assert entries == {"A → B : go": {**{g: "" for g in stamp.GUIDE_WORDS}, stamp.GUIDE_WORDS[0]: "B idles"}}
    # the design changes: the order is different and another action is added
    later = [rows[1], {"controller": "A", "controlled": "D", "control action": "go",
                       **{g: "" for g in stamp.GUIDE_WORDS}}, rows[0]]
    filled = stamp.apply_entries(later, entries)
    assert [r[stamp.GUIDE_WORDS[0]] for r in filled] == ["", "", "B idles"]
