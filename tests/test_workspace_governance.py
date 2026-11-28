"""Three things the decision record of the workspace keeps: who decided (`decided_by`),
the versions of a design without git (`<name>.versions.json`), and the versioning rule a
design declares itself (`amendment.versioning`)."""
import json
import shutil
import subprocess

import pytest

from backend.services import design_amend as amend
from backend.services import design_service as ds
from backend.services import design_workspace as workspace

V1 = (ds.DESIGNS_DIR / "amendment_robot_town_v1.cadl").read_text(encoding="utf-8").replace(
    "            deadline: 60s\n", "            deadline: 60s   # @amend:hold_limit\n", 1)
CONSTITUTION = """\
trial_periods: 3
amendable:
  hold_limit:
    layer: rule
    min: 10
    max: 120
    step: 10
"""
BY_MANAGER = CONSTITUTION + "    decided_by: FACILITY_MANAGER\n"
RULE = {"kind": "rule", "name": "council rule substitute"}


def _versioned(source, scheme="dotted", period_step=5):
    """``source`` with ``amendment.versioning`` declared right under ``amendment:``."""
    lines = [f"    versioning:\n", f"      scheme: {scheme}\n"] + \
        ([f"      period_step: {period_step}\n"] if period_step is not None else [])
    return source.replace("  amendment:\n", "  amendment:\n" + "".join(lines), 1)


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("CADL_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("CADL_WORKSPACE_GIT", raising=False)
    monkeypatch.delenv("CADL_TRIAL_COMMAND", raising=False)
    return tmp_path / "ws"


def _town(ws, source=V1, constitution=CONSTITUTION):
    workspace.root()
    (ws / "town.constitution.yaml").write_text(constitution, encoding="utf-8")
    workspace.write("town", source, note="version 1")


def _git_or_skip(monkeypatch):
    if shutil.which("git") is None:
        pytest.skip("git is not installed")
    monkeypatch.setenv("CADL_WORKSPACE_GIT", "1")


# ── 1. Who decided ──────────────────────────────────────────────────


def test_decided_by_is_checked_and_normalised():
    assert workspace._decider(None) is None and workspace._decider({}) is None
    assert workspace._decider("Person") == {"kind": "person"}
    assert workspace._decider({"kind": "rule", "name": " council rule substitute "}) == RULE
    assert workspace.decider_text(RULE) == "rule (council rule substitute)"
    with pytest.raises(workspace.WorkspaceError, match="one of person, rule, assistant"):
        workspace._decider({"kind": "council"})
    with pytest.raises(workspace.WorkspaceError, match="not who"):
        workspace._decider({"kind": "person", "who": "me"})


def test_apply_records_who_decided(ws):
    _town(ws, constitution=BY_MANAGER)
    p = workspace.add_amendment("town", {"hold_limit": 70}, "fewer overstays")
    applied = workspace.apply_proposal("town", p["proposal_id"], reason="the rule says so",
                                       decided_by=RULE)
    assert applied["decided_by"] == RULE
    decision = json.loads((ws / "town.review.json").read_text(encoding="utf-8"))["decisions"][-1]
    assert decision["decider"] == RULE
    assert decision["decided_by"] == {"hold_limit": "FACILITY_MANAGER"}   # the constitution's, kept
    md = (ws / "town.decisions.md").read_text(encoding="utf-8")
    assert "Decided by: rule (council rule substitute)" in md
    assert "Left to a person under the constitution: hold_limit (FACILITY_MANAGER)." in md
    versions = workspace.version_history("town")["versions"]
    assert [(v["version"], v["decided_by"]) for v in versions] == [("1", None), ("1.1", RULE)]


def test_discard_records_who_decided(ws):
    _town(ws)
    p = workspace.add_amendment("town", {"hold_limit": 70})
    out = workspace.discard_proposal("town", p["proposal_id"], reason="no",
                                     decided_by={"kind": "person", "name": "FACILITY_MANAGER"})
    assert out["decided_by"] == {"kind": "person", "name": "FACILITY_MANAGER"}
    assert workspace.decisions("town")[-1]["decider"]["name"] == "FACILITY_MANAGER"
    assert "Decided by: person (FACILITY_MANAGER)" in (ws / "town.decisions.md").read_text(encoding="utf-8")


def test_without_decided_by_nothing_new_is_recorded(ws):
    _town(ws, constitution=BY_MANAGER)
    p = workspace.add_amendment("town", {"hold_limit": 70})
    applied = workspace.apply_proposal("town", p["proposal_id"], reason="agreed")
    assert "decided_by" not in applied and "decider" not in workspace.decisions("town")[-1]
    md = (ws / "town.decisions.md").read_text(encoding="utf-8")
    assert "Decided by:" not in md
    assert "Decided by a person under the constitution: hold_limit (FACILITY_MANAGER)." in md
    assert workspace.version_history("town")["versions"][-1]["decided_by"] is None


def test_who_decided_goes_into_the_commit(ws, monkeypatch):
    _git_or_skip(monkeypatch)
    _town(ws)
    p = workspace.add_amendment("town", {"hold_limit": 70})
    workspace.apply_proposal("town", p["proposal_id"], reason="by rule", decided_by=RULE)
    q = workspace.add_amendment("town", {"hold_limit": 80})
    workspace.discard_proposal("town", q["proposal_id"], reason="not now", decided_by="assistant")
    log = subprocess.run(["git", "-C", str(ws), "log", "--format=%B%x00"], capture_output=True,
                         text=True).stdout.split("\0")
    assert "Decided by: assistant" in log[0] and "rejected" in log[0]
    assert "Decided by: rule (council rule substitute)" in log[1]
    hist = workspace.version_history("town")
    assert hist["from"] == "git" and hist["versions"][-1]["decided_by"] == RULE
    tracked = subprocess.run(["git", "-C", str(ws), "ls-files"], capture_output=True, text=True).stdout
    assert "town.versions.json" in tracked


def test_the_mcp_tools_take_decided_by(ws):
    pytest.importorskip("mcp")
    import mcp_server

    _town(ws)
    p = mcp_server.propose_amendment("town", {"hold_limit": 70})
    out = mcp_server.apply_proposal("town", p["proposal_id"], reason="by rule", decided_by=RULE)
    assert out["applied"] and out["decided_by"] == RULE
    q = mcp_server.propose_amendment("town", {"hold_limit": 80})
    out = mcp_server.discard_proposal("town", q["proposal_id"], reason="no", decided_by={"kind": "person"})
    assert out["decided_by"] == {"kind": "person"}
    assert "error" in mcp_server.apply_proposal("town", "x", decided_by={"kind": "robot"})
    assert mcp_server.get_history("town")["versions"][-1]["decided_by"] == RULE


# ── 2. History without git ──────────────────────────────────────────


def test_each_version_is_kept_as_a_snapshot(ws):
    _town(ws)
    for v in (70, 80):
        p = workspace.add_amendment("town", {"hold_limit": v})
        workspace.apply_proposal("town", p["proposal_id"], reason="agreed")
    doc = json.loads((ws / "town.versions.json").read_text(encoding="utf-8"))
    assert doc["format"] == 1 and doc["design"] == "town"
    assert list(doc["versions"]) == ["1", "1.1", "1.2"]
    assert doc["versions"]["1"]["source"] == V1 and doc["versions"]["1"]["message"] == "version 1"
    assert doc["versions"]["1.2"]["source"] == workspace.read("town")
    assert workspace.source_at_version("town", "1.1") == doc["versions"]["1.1"]["source"]
    hist = workspace.version_history("town")
    assert hist["from"] == "snapshots" and hist["commits"] == []
    assert [v["version"] for v in hist["versions"]] == ["1", "1.1", "1.2"]
    assert hist["values"] == [{"parameter": "hold_limit", "1": "60", "1.1": "70 ←", "1.2": "80 ←"}]
    with pytest.raises(workspace.WorkspaceError, match="No version `9`.*Versions: 1, 1.1, 1.2"):
        workspace.source_at_version("town", "9")


def test_an_undo_drops_the_version_it_undid(ws):
    _town(ws)
    p = workspace.add_amendment("town", {"hold_limit": 70})
    workspace.apply_proposal("town", p["proposal_id"], reason="agreed")
    assert list(workspace.snapshots("town")) == ["1", "1.1"]
    workspace.undo("town")
    assert list(workspace.snapshots("town")) == ["1"]
    p = workspace.add_amendment("town", {"hold_limit": 50})
    workspace.apply_proposal("town", p["proposal_id"], reason="the other way")
    assert amend.values(workspace.source_at_version("town", "1.1"))["hold_limit"] == 50


def test_a_design_without_a_version_has_no_snapshots(ws):
    workspace.root()
    workspace.create("plain")
    assert not (ws / "plain.versions.json").exists()
    assert workspace.version_history("plain")["versions"] == []


def test_a_reversion_works_without_git(ws):
    _town(ws)
    for v in (70, 80):
        p = workspace.add_amendment("town", {"hold_limit": v})
        workspace.apply_proposal("town", p["proposal_id"], reason="agreed")
    r = workspace.add_reversion("town", "1", "back to the start")
    assert r["version"]["to"] == "1.3" and r["version"]["reverts"] == "1"
    assert r["restored"] == {"hold_limit": 60.0} and r["constitution_allows"], r["constitution_problems"]
    workspace.apply_proposal("town", r["proposal_id"], reason="the council agreed", decided_by=RULE)
    assert amend.values(workspace.read("town"))["hold_limit"] == 60
    hist = workspace.version_history("town")
    assert [v["version"] for v in hist["versions"]] == ["1", "1.1", "1.2", "1.3"]
    assert hist["versions"][-1]["decided_by"] == RULE


def test_with_git_the_snapshots_fill_in_what_git_lacks(ws, monkeypatch):
    _town(ws)                       # version 1 kept before git was turned on
    _git_or_skip(monkeypatch)
    p = workspace.add_amendment("town", {"hold_limit": 70})
    workspace.apply_proposal("town", p["proposal_id"], reason="agreed")
    assert [v["version"] for v in workspace.version_history("town")["versions"]] == ["1.1"]
    assert workspace.source_at_version("town", "1") == V1


# ── 3. Versioning declared in the design ────────────────────────────


def test_declared_versioning_is_read_from_the_design():
    assert amend.declared_versioning(V1) is None
    assert amend.declared_versioning(_versioned(V1)) == {"scheme": "dotted", "period_step": 5}
    assert amend.declared_versioning(_versioned(V1, "integer", None)) == {"scheme": "integer",
                                                                         "period_step": None}
    assert amend.declared_version(_versioned(V1))["version"] == "1"   # the rest reads as before


def test_next_version_by_scheme():
    assert [amend.next_version(v, "dotted") for v in ("2", "2.1", "2.9", "2.1.3", "v3")] == \
        ["2.1", "2.2", "2.10", "2.1.4", "v3.1"]
    assert amend.next_version("4", "integer") == "5"
    with pytest.raises(ValueError, match="not a whole number"):
        amend.next_version("2.1", "integer")
    assert amend.next_version("v3") == "v4"      # no scheme: as before


def test_the_declared_rule_numbers_and_moves_the_period(ws):
    _town(ws, _versioned(V1, "dotted", 5))       # trial_periods 3 in the constitution
    p = workspace.add_amendment("town", {"hold_limit": 70})
    assert p["version"] == {"from": "1", "to": "1.1", "effective_period": 6}
    assert p["acceptable"] and "versioning_problems" not in p
    workspace.apply_proposal("town", p["proposal_id"], reason="agreed")
    p = workspace.add_amendment("town", {"hold_limit": 80})
    assert p["version"] == {"from": "1.1", "to": "1.2", "effective_period": 11}


def test_the_integer_scheme(ws):
    _town(ws, _versioned(V1, "integer", None))
    p = workspace.add_amendment("town", {"hold_limit": 70})
    # no period_step: the constitution's trial_periods is the fallback
    assert p["version"] == {"from": "1", "to": "2", "effective_period": 4} and p["acceptable"]


def test_without_a_declaration_trial_periods_and_next_version_still_apply(ws):
    _town(ws)
    p = workspace.add_amendment("town", {"hold_limit": 70})
    assert p["version"] == {"from": "1", "to": "1.1", "effective_period": 4}


def test_an_explicit_version_or_period_that_breaks_the_rule_is_gated(ws):
    _town(ws, _versioned(V1, "dotted", 5))
    p = workspace.add_amendment("town", {"hold_limit": 70}, version="1.5")
    assert [v["code"] for v in p["versioning_problems"]] == ["version_not_next"]
    assert not p["acceptable"] and p["constitution_allows"]
    with pytest.raises(workspace.WorkspaceError, match="version_not_next"):
        workspace.apply_proposal("town", p["proposal_id"], reason="agreed")
    p = workspace.add_amendment("town", {"hold_limit": 70}, effective_period=3)
    assert [v["code"] for v in p["versioning_problems"]] == ["period_too_soon"]
    with pytest.raises(workspace.WorkspaceError, match="period_too_soon"):
        workspace.apply_proposal("town", p["proposal_id"], reason="agreed")
    p = workspace.add_amendment("town", {"hold_limit": 70}, version="1.1", effective_period=6)
    assert p["acceptable"] and "versioning_problems" not in p


def test_check_versioning_alone():
    before = _versioned(V1, "integer", 2)
    ok = amend.declare(amend.set_values(before, {"hold_limit": 70}), {"hold_limit": 70}, "2", "1", 3)
    assert amend.check_versioning(before, ok) == []
    bad = amend.declare(amend.set_values(before, {"hold_limit": 70}), {"hold_limit": 70}, "1.1", "1", 2)
    assert [c for c, _ in amend.check_versioning(before, bad)] == ["version_not_next", "period_too_soon"]
    assert amend.check_versioning(V1, amend.declare(V1, {}, "7", "1", 1)) == []   # no rule declared


def test_the_mcp_tool_reports_versioning_problems(ws):
    pytest.importorskip("mcp")
    import mcp_server

    _town(ws, _versioned(V1, "dotted", 5))
    p = mcp_server.propose_amendment("town", {"hold_limit": 70}, version="2")
    assert p["versioning_problems"][0]["code"] == "version_not_next" and not p["acceptable"]
