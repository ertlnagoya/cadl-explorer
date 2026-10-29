"""Amendable parameters (`# @amend:`), the constitution, and the decision record."""
import shutil

import pytest

pytest.importorskip("cadl")

from backend.services import design_amend as amend
from backend.services import design_service as ds
from backend.services import design_workspace as workspace

TOWN = ds.load_example("robot town")
CONSTITUTION = (ds.DESIGNS_DIR / "robot_town.constitution.yaml").read_text(encoding="utf-8")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("CADL_WORKSPACE", str(tmp_path))
    return tmp_path


def test_amendables_of_robot_town():
    params = {a.name: a for a in amend.amendables(TOWN)}
    assert list(params) == ["pickup_deadline", "dropoff_deadline", "sanction_lambda", "hold_limit",
                            "block_k", "to_central", "to_distributed"]
    assert params["pickup_deadline"].value == 120 and len(params["pickup_deadline"].occurrences) == 2
    assert params["block_k"].value == 3      # the number inside the rule string
    assert all(a.consistent for a in params.values())
    assert amend.findings(TOWN, amend.load_constitution(CONSTITUTION)) == []


def test_findings_name_disagreeing_lines_and_the_constitution():
    text = TOWN.replace("deadline: 120s            # @amend:pickup_deadline",
                        "deadline: 150s            # @amend:pickup_deadline")
    notes = amend.findings(text)
    assert any("`pickup_deadline` has different values" in n for n in notes)
    const = amend.load_constitution(CONSTITUTION)
    del const["block_k"]
    const["ghost"] = {"layer": "rule", "min": 0, "max": 1, "step": 1}
    notes = amend.findings(amend.set_values(TOWN, {"hold_limit": 65}), const)
    assert any("`block_k` is marked amendable but the constitution does not list it" in n for n in notes)
    assert any("`ghost`" in n for n in notes)
    assert any("`hold_limit` = 65 is not a whole number of steps" in n for n in notes)
    assert any("outside" in n for n in amend.findings(amend.set_values(TOWN, {"hold_limit": 200}),
                                                       amend.load_constitution(CONSTITUTION)))


def test_set_values_keeps_the_text_and_comments():
    new = amend.set_values(TOWN, {"pickup_deadline": 150, "sanction_lambda": 1})
    changed = [(a, b) for a, b in zip(TOWN.splitlines(), new.splitlines()) if a != b]
    assert changed == [
        ('        - "pickup_time <= 120s"         # @amend:pickup_deadline',
         '        - "pickup_time <= 150s"         # @amend:pickup_deadline'),
        ("            deadline: 120s            # @amend:pickup_deadline",
         "            deadline: 150s            # @amend:pickup_deadline"),
        ("        lambda: 0.5                   # @amend:sanction_lambda",
         "        lambda: 1.0                   # @amend:sanction_lambda"),
    ]
    assert ds.analyze(new).parsed
    with pytest.raises(ValueError):
        amend.set_values(TOWN, {"nothing": 1})


def test_the_constitution_gate():
    const = amend.load_constitution(CONSTITUTION)
    ok = amend.check_amendment(TOWN, amend.set_values(TOWN, {"hold_limit": 70}), const)
    assert ok.ok and ok.changed == [("hold_limit", 60, 70)] and ok.for_a_person == []
    contract = amend.check_amendment(TOWN, amend.set_values(TOWN, {"pickup_deadline": 150}), const)
    assert contract.ok and contract.for_a_person == ["pickup_deadline"]
    safety = amend.check_amendment(TOWN, amend.set_values(TOWN, {"to_central": 24}), const)
    assert safety.for_a_person == ["to_central"]
    two = amend.check_amendment(TOWN, amend.set_values(TOWN, {"hold_limit": 70, "block_k": 2}), const)
    assert not two.ok and "one per amendment" in two.reasons[0]
    far = amend.check_amendment(TOWN, amend.set_values(TOWN, {"hold_limit": 80}), const)
    assert not far.ok and "moves by 20" in far.reasons[0]
    out = amend.check_amendment(TOWN, amend.set_values(TOWN, {"block_k": 5}), const)
    assert any("outside" in r for r in out.reasons)
    edited = amend.check_amendment(TOWN, TOWN.replace("num_robots:   6", "num_robots:   7"), const)
    assert any("other than the numbers" in r for r in edited.reasons)
    # without a constitution only the text and the consistency are checked
    assert amend.check_amendment(TOWN, amend.set_values(TOWN, {"hold_limit": 80, "block_k": 2})).ok


def test_history_marks_what_changed():
    v2 = amend.set_values(TOWN, {"block_k": 2})
    v3 = amend.set_values(v2, {"hold_limit": 70})
    rows = {r["parameter"]: r for r in amend.history([("v1", TOWN), ("v2", v2), ("v3", v3)])}
    assert rows["block_k"] == {"parameter": "block_k", "v1": "3", "v2": "2 ←", "v3": "2"}
    assert rows["hold_limit"]["v3"] == "70 ←"


def test_workspace_amendment_and_decision_record(ws):
    workspace.create("town", "robot town")
    assert workspace.amendables("town")["constitution"] is None
    shutil.copy(ds.DESIGNS_DIR / "robot_town.constitution.yaml", ws / "constitution.yaml")
    listed = workspace.amendables("town")
    assert listed["constitution"] == "constitution.yaml" and listed["problems"] == []
    hold = next(p for p in listed["parameters"] if p["name"] == "hold_limit")
    assert hold["layer"] == "rule" and hold["decided_by"] == "the checks" and hold["step"] == 10

    proposal = workspace.add_amendment("town", {"hold_limit": 70}, "fewer road violations")
    assert proposal["acceptable"] and proposal["constitution_allows"]
    assert workspace.read("town") == TOWN                       # nothing applied yet
    applied = workspace.apply_proposal("town", proposal["proposal_id"], reason="B and C agreed",
                                       assumptions=["demand stays as in period 1", " "])
    assert applied["applied"] and "# @amend:hold_limit" in workspace.read("town")
    assert amend.values(workspace.read("town"))["hold_limit"] == 70
    record = (ws / "town.decisions.md").read_text(encoding="utf-8")
    assert "Amended hold_limit: 60 → 70" in record and "B and C agreed" in record
    assert "demand stays as in period 1" in record
    assert workspace.decisions("town")[0]["assumptions"] == ["demand stays as in period 1"]

    contract = workspace.add_amendment("town", {"pickup_deadline": 150})
    assert contract["acceptable"] and contract["decided_by_a_person"] == ["pickup_deadline"]
    with pytest.raises(workspace.WorkspaceError, match="needs their decision"):
        workspace.apply_proposal("town", contract["proposal_id"])
    workspace.apply_proposal("town", contract["proposal_id"], reason="customers accept 150 s")
    assert "Decided by a person under the constitution: pickup_deadline" in \
        (ws / "town.decisions.md").read_text(encoding="utf-8")

    bad = workspace.add_amendment("town", {"block_k": 1, "sanction_lambda": 1})
    assert not bad["acceptable"] and "one per amendment" in bad["constitution_problems"][0]
    with pytest.raises(workspace.WorkspaceError, match="not applied"):
        workspace.apply_proposal("town", bad["proposal_id"], reason="x")
    with pytest.raises(workspace.WorkspaceError):
        workspace.add_amendment("town", {"ghost": 1})


def test_a_design_specific_constitution_wins(ws):
    workspace.create("town", "robot town")
    (ws / "constitution.yaml").write_text("amendable: {}\n", encoding="utf-8")
    shutil.copy(ds.DESIGNS_DIR / "robot_town.constitution.yaml", ws / "town.constitution.yaml")
    assert workspace.amendables("town")["constitution"] == "town.constitution.yaml"
    (ws / "town.constitution.yaml").write_text("not: [a constitution\n", encoding="utf-8")
    with pytest.raises(workspace.WorkspaceError, match="town.constitution.yaml"):
        workspace.amendables("town")


def test_mcp_amendment_tools(ws):
    pytest.importorskip("mcp")
    import asyncio
    import mcp_server as server
    server.create_design("town", "robot town")
    shutil.copy(ds.DESIGNS_DIR / "robot_town.constitution.yaml", ws / "constitution.yaml")
    assert len(server.list_amendables("town")["parameters"]) == 7
    proposal = server.propose_amendment("town", {"pickup_deadline": 150}, "more on-time deliveries")
    assert proposal["decided_by_a_person"] == ["pickup_deadline"]
    assert "error" in server.apply_proposal("town", proposal["proposal_id"])
    assert server.apply_proposal("town", proposal["proposal_id"], reason="the operator agreed")["applied"]
    assert server.get_decisions("town")["decisions"][0]["reason"] == "the operator agreed"
    assert set(server.propose_amendment("town", {"ghost": 1})) == {"error"}
    tools = {t.name for t in asyncio.run(server.mcp.list_tools())}
    assert {"list_amendables", "propose_amendment", "get_decisions"} <= tools
