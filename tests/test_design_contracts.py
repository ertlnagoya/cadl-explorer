"""What watches each assumption and guarantee, and how contracts depend on each other."""
import pytest

pytest.importorskip("cadl")

from backend.services import design_contracts as dcon
from backend.services import design_service as ds

CHAIN = """
sos:
  name: Chain
  type: Collaborative
  actors:
    - {id: MILL, role: mill, autonomy: medium}
    - {id: DEPOT, role: depot, autonomy: medium}
    - {id: SHOP, role: shop, autonomy: high}
    - {id: AUDIT, role: auditor, autonomy: low}
  contracts:
    - id: SUPPLY
      parties: [MILL, DEPOT]
      guarantee: ["DEPOT.stock_level >= 10", "restock_time <= 2h"]
      violation: {detect: "DEPOT.stock_level < 5"}
    - id: RETAIL
      parties: [DEPOT, SHOP]
      assume: ["DEPOT.stock_level >= 5", "SHOP.open == true"]
      guarantee: ["serve_time <= 10min"]
      monitors:
        - {id: queue_watch, observe: "SHOP.queue_length", sampling: periodic(1s),
           rule: "SHOP.queue_length > 20", on_match: {severity: Minor}}
    - id: BOOKS
      parties: [AUDIT]
      guarantee: ["ledger_balanced()"]
"""


def _ir(source):
    return ds.analyze(source).ir


@pytest.mark.parametrize("expr, names", [
    ("ROBOT[i].battery < 20 AND state == Assigned", {"battery"}),
    ("ROBOT.blocking >= 3 AND state IN [Holding, Overstay]", {"blocking"}),
    ("delivery_time <= 300s", {"delivery_time"}),
    ("x > -0.5 OR NOT ready", {"x", "ready"}),
    ("min_dist(ROBOT[i].position, OBST.positions) < 0.05", {"position", "positions"}),
    ("all_routes_conflict_free()", {"all_routes_conflict_free"}),
    ("now > request.deadline", {"deadline"}),
])
def test_quantities(expr, names):
    assert dcon.quantities(expr, exclude={"Assigned"}) == names


def test_amend_deadlines_name_the_transition():
    source = ("        transitions:\n          - id: release\n            from: Holding\n"
              "            deadline: 60s             # @amend:hold_limit\n"
              "          - id: other\n            deadline: soon  # @amend:bad\n")
    assert dcon.amend_deadlines(source) == {"hold_limit": ("release", 60000)}


def test_coverage_of_the_bundled_examples():
    town = ds.load_example("robot town")
    rows = {(p.contract, p.text): p for p in dcon.coverage(_ir(town), town)}
    assert rows[("DELIVERY_SLA", "pickup_time <= 120s")].watched_by == ["deadline of pickup (120s)"]
    assert rows[("ROAD_USE", "hold_time <= hold_limit")].watched_by == \
        ["deadline of release (60s, @amend:hold_limit)"]
    # without the source the @amend name cannot be resolved
    assert not {(p.contract, p.text): p for p in dcon.coverage(_ir(town))}[
        ("ROAD_USE", "hold_time <= hold_limit")].watched_by

    supply = _ir(ds.load_example("supply chain"))
    rows = {p.text: p for p in dcon.coverage(supply)}
    assert rows["order_fulfillment_rate >= 0.95"].watched_by == ["violation.detect"]
    assert rows["order_fulfillment_rate >= 0.95"].measured_by == ["metric order_fulfillment_rate"]
    assert rows["damage_rate <= 0.01"].status == "not watched"

    demo = _ir(ds.load_example("sos dsl robot delivery"))
    rows = {p.text: p for p in dcon.coverage(demo)}
    assert rows["ROBOT[i].battery > 20"].watched_by == ["monitor battery_guard"]
    notes = dcon.coverage_findings(demo, dcon.coverage(demo))
    assert any(n.startswith("Nothing watches the guarantee `delivery_time <= 300s`") for n in notes)
    assert any(n.startswith("Monitor collision_watch of DELIVERY_SLA watches") for n in notes)


def test_a_deadline_must_match_the_bound_and_the_transition():
    ir = _ir(ds.load_example("sos dsl robot delivery"))       # deadline of accept: 5s
    rows = {p.text: p for p in dcon.coverage(ir)}
    assert not any(w.startswith("deadline") for w in rows["delivery_time <= 300s"].watched_by)


def test_coverage_in_a_chain():
    ir = _ir(CHAIN)
    rows = {(p.contract, p.text): p for p in dcon.coverage(ir)}
    assert rows[("SUPPLY", "DEPOT.stock_level >= 10")].watched_by == ["violation.detect"]
    assert rows[("SUPPLY", "restock_time <= 2h")].status == "not watched"
    notes = dcon.coverage_findings(ir, list(rows.values()))
    assert any("Monitor queue_watch of RETAIL watches `queue_length`" in n for n in notes)


def test_dependencies_in_a_chain():
    ir = _ir(CHAIN)
    deps = dcon.dependencies(ir)
    assert dcon.Dependency("SUPPLY", "RETAIL", "shared parties", ("DEPOT",)) in deps
    assert dcon.Dependency("SUPPLY", "RETAIL", "assumes what it guarantees", ("stock_level",)) in deps
    assert not any("BOOKS" in (d.source, d.target) for d in deps)
    sources = {r["assumption"]: r["guaranteed by"] for r in dcon.assumption_sources(ir, deps)}
    assert sources == {"DEPOT.stock_level >= 5": "SUPPLY", "SHOP.open == true": ""}
    notes = dcon.dependency_findings(ir, deps)
    assert any(n.startswith("BOOKS shares no party") for n in notes)
    assert "1 of 2 assumptions is guaranteed by another contract" in " ".join(notes)
    dot = dcon.dependency_dot(ir)
    assert '"SUPPLY" -- "RETAIL" [label="DEPOT" style=dashed];' in dot
    assert '"SUPPLY" -- "RETAIL" [label="stock_level" dir=forward' in dot
    assert dcon.dependency_dot(ir, dark=True) != dot


def test_bundled_examples_render():
    for name in ds.list_examples():
        source = ds.load_example(name)
        ir = _ir(source)
        promises = dcon.coverage(ir, source)
        dcon.coverage_findings(ir, promises)
        deps = dcon.dependencies(ir)
        dcon.dependency_findings(ir, deps)
        assert dcon.dependency_dot(ir).startswith("graph ")


def test_one_contract_has_no_dependency_findings():
    ir = _ir(ds.load_example("sos dsl robot delivery"))
    assert dcon.dependencies(ir) == [] and dcon.dependency_findings(ir, []) == []
