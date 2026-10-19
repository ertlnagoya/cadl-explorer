"""Structure view: message flow, authority, centrality, N² chart, responsibilities."""
import pytest

pytest.importorskip("cadl")

from backend.services import design_graph as graph
from backend.services import design_readback as readback
from backend.services import design_service as ds

CHAIN = """
sos:
  name: Chain
  type: Collaborative
  actors:
    - {id: A, role: a, autonomy: low, interface: {input: [], output: [m1]}}
    - {id: B, role: b, autonomy: low, interface: {input: [m1], output: [m2]}}
    - {id: C, role: c, autonomy: low, interface: {input: [m2], output: []}}
    - {id: LONE, role: d, autonomy: low}
  contracts:
    - id: K
      parties: [A, B, C]
      guarantee: ["x > 0"]
      authority: {decision_holder: B}
      responsibilities: {B: ["relay every m1 as m2"]}
  protocols:
    - id: P
      trigger: go
      steps: ["A -> B : m1", "B -> C : m2", "C -> A : m3", "B -> C : m9"]
"""


def _ir(source):
    analysis = ds.analyze(source)
    assert analysis.parsed
    return analysis.ir


def test_message_edges_and_declarations():
    edges = graph.message_edges(_ir(CHAIN))
    assert [(e.sender, e.receiver, e.message, e.declared) for e in edges] == [
        ("A", "B", "m1", True), ("B", "C", "m2", True),
        ("C", "A", "m3", True),      # C lists no outputs and A no inputs: nothing to check against
        ("B", "C", "m9", False)]     # B lists outputs [m2], which leaves m9 out


def test_betweenness_of_a_cycle_and_a_relay():
    rows = {r["actor"]: r for r in graph.centrality(_ir(CHAIN))}
    # A→B→C→A is a cycle: each actor is on one of the two-step paths between the others.
    assert rows["A"]["betweenness"] == rows["B"]["betweenness"] == rows["C"]["betweenness"] > 0
    assert rows["LONE"]["betweenness"] == 0 and rows["LONE"]["partners"] == 0
    assert rows["B"]["decides"] == 1 and rows["B"]["contracts"] == 1


def test_relay_has_the_highest_betweenness():
    star = CHAIN.replace('"C -> A : m3"', '"B -> A : m4"')
    rows = graph.centrality(_ir(star))
    # Of the 6 ordered pairs among the other three actors (LONE included), only A→C
    # has a shortest path, and it passes through B.
    assert rows[0]["actor"] == "B" and rows[0]["betweenness"] == round(1 / 6, 3)


def test_n2_and_responsibilities():
    ir = _ir(CHAIN)
    matrix = graph.n2_matrix(ir)
    assert [e.message for e in matrix["A"]["B"]] == ["m1"] and matrix["B"]["A"] == []
    table = graph.responsibility_table(ir, readback._responsibilities(CHAIN))
    assert table["B"]["K"] == ["decides", "party", "duties (1)"]
    assert table["LONE"]["K"] == []


def test_structure_gaps_name_what_is_missing():
    gaps = graph.structure_gaps(_ir(CHAIN), readback._responsibilities(CHAIN))
    assert "LONE takes part in no contract." in gaps
    assert "LONE sends and receives no message." in gaps
    no_holder = CHAIN.replace("      authority: {decision_holder: B}\n", "")
    assert "Nobody decides under K." in graph.structure_gaps(_ir(no_holder))


def test_gap_names_the_interface_that_leaves_a_message_out():
    gaps = graph.structure_gaps(_ir(CHAIN))
    # C lists inputs [m2] as well, so both sides leave m9 out
    assert "B → C : m9 is missing from the outputs of B and the inputs of C." in gaps


UNDECLARED_EVENT = """
sos:
  name: Undeclared
  type: Acknowledged
  actors:
    - {id: DISPATCHER, role: d, autonomy: low, interface: {input: [position_report], output: [route_assignment]}}
    - {id: "ROBOT[1..N]", role: r, autonomy: high, interface: {input: [route_assignment], output: [position_report]}}
  contracts:
    - id: SLA
      parties: [DISPATCHER, "ROBOT[*]"]
      guarantee: ["delivery_time <= 300s"]
      lifecycle:
        states: [Assigned, Accepted, Violated]
        initial: Assigned
        terminal: [Accepted, Violated]
        transitions:
          - id: accept
            from: Assigned
            to: Accepted
            on: "ROBOT[i] -> DISPATCHER : ack(accepted)"
            deadline: 5s
            on_violation: {transition: Violated}
"""


def test_undeclared_lifecycle_event_is_flagged():
    # ROBOT sends ack(accepted) in a lifecycle event but lists only position_report
    # among its outputs (the bundled example had this gap until cadl#26).
    ir = _ir(UNDECLARED_EVENT)
    bad = [(e.sender, e.receiver, e.message) for e in graph.message_edges(ir) if not e.declared]
    assert ("ROBOT", "DISPATCHER", "ack") in bad


def test_bundled_delivery_example_declares_every_message():
    ir = _ir(ds.load_example("sos dsl robot delivery"))
    assert [e for e in graph.message_edges(ir) if not e.declared] == []
    assert graph.structure_gaps(ir) == []


@pytest.mark.parametrize("name", list(ds.list_examples()))
def test_every_example_renders(name):
    ir = _ir(ds.load_example(name))
    dot = graph.message_flow_dot(ir, show_authority=True)
    assert dot.startswith("digraph") and dot.rstrip().endswith("}")
    assert len(graph.centrality(ir)) == len(ir["institution"]["actors"])


def test_layout_keeps_larger_graphs_fast():
    many = "\n".join(f"    - {{id: A{i}, role: r, autonomy: low}}" for i in range(12))
    source = "sos:\n  name: M\n  type: Collaborative\n  actors:\n" + many + "\n"
    dot = graph.message_flow_dot(_ir(source))
    assert "layout=neato" in dot and "splines=true" in dot      # curved splines are very slow with neato
    assert "layout=dot" in graph.message_flow_dot(_ir(CHAIN))           # 4 actors
    five = CHAIN.replace("    - {id: LONE, role: d, autonomy: low}", "    - {id: LONE, role: d, autonomy: low}\n    - {id: E, role: e, autonomy: low}")
    assert "splines=curved" in graph.message_flow_dot(_ir(five))


def test_a_pair_with_a_decision_and_an_undeclared_message_keeps_both():
    source = CHAIN.replace('"B -> C : m9"', '"B -> A : m9"')   # B decides K; A lists inputs [] → declared
    source = source.replace("{input: [], output: [m1]}", "{input: [m0], output: [m1]}")
    dot = graph.message_flow_dot(_ir(source), show_authority=True)
    line = next(l for l in dot.splitlines() if l.strip().startswith('"B" -> "A"'))
    assert "style=dashed" in line and "penwidth=2.2" in line and "decides K" in line
