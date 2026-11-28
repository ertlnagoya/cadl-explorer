"""A council's amendment loop run end to end in the workspace: the next version is
declared with the value, a trial's numbers are kept, rejections stay in the decision
record, `decided_by` is read from the constitution, and the history is in git."""
import json
import shutil
import subprocess
import sys

import pytest

pytest.importorskip("cadl")

from backend.services import design_amend as amend
from backend.services import design_service as ds
from backend.services import design_workspace as workspace

# Robot Town v1 declares the amendment extension (version "1", no `previous:`,
# `effective: { period: 1 }`); the deadline of ROAD_USE.release is the declared
# location of `hold_limit`, which this test marks for the council.
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
CONSTITUTION_BY_OPERATOR = CONSTITUTION + "    decided_by: OPERATOR\n"


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("CADL_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("CADL_WORKSPACE_GIT", raising=False)
    monkeypatch.delenv("CADL_TRIAL_COMMAND", raising=False)
    return tmp_path / "ws"


def _town(ws, constitution=CONSTITUTION):
    workspace.root()
    (ws / "town.constitution.yaml").write_text(constitution, encoding="utf-8")
    workspace.write("town", V1, note="version 1")


# ── decided_by ──────────────────────────────────────────────────────


def test_decided_by_in_the_constitution_leaves_the_change_to_that_person():
    assert not amend.needs_human({"layer": "rule"})
    assert not amend.needs_human({"layer": "rule", "decided_by": "COUNCIL"})
    assert amend.needs_human({"layer": "rule", "decided_by": "FACILITY_MANAGER"})
    assert amend.decider({"decided_by": "FACILITY_MANAGER"}) == "FACILITY_MANAGER"
    assert amend.decider({"layer": "rule"}) is None
    assert "decided by FACILITY_MANAGER" in amend.layer_of({"layer": "rule", "decided_by": "FACILITY_MANAGER"})


def test_the_workspace_names_who_decides(ws):
    _town(ws, CONSTITUTION_BY_OPERATOR)
    hold = workspace.amendables("town")["parameters"][0]
    assert hold["name"] == "hold_limit" and hold["decided_by"] == "OPERATOR"
    p = workspace.add_amendment("town", {"hold_limit": 70})
    assert p["decided_by_a_person"] == ["hold_limit"] and p["decided_by"] == {"hold_limit": "OPERATOR"}
    assert "OPERATOR" in p["next"]
    with pytest.raises(workspace.WorkspaceError, match="needs their decision"):
        workspace.apply_proposal("town", p["proposal_id"])
    workspace.apply_proposal("town", p["proposal_id"], reason="the operator agreed")
    record = workspace.decisions("town")[-1]
    assert record["decided_by"] == {"hold_limit": "OPERATOR"}
    assert "hold_limit (OPERATOR)" in (ws / "town.decisions.md").read_text(encoding="utf-8")


# ── The next version ────────────────────────────────────────────────


def test_next_version_numbers():
    assert [amend.next_version(v) for v in ("2", "2.1", "2.9", "v3", "1.0.4", "", None)] == \
        ["2.1", "2.2", "2.10", "v4", "1.0.5", "1", "1"]


def test_declare_writes_the_version_with_the_value():
    tagged = amend.set_values(V1, {"hold_limit": 70})
    new = amend.declare(tagged, {"hold_limit": 70}, "1.1", "1", 4)
    sos = ds.yaml.safe_load(new)["sos"]
    assert sos["version"] == "1.1"
    assert sos["amendment"]["previous"] == "1" and sos["amendment"]["effective"] == {"period": 4}
    hold = next(p for p in sos["amendment"]["parameters"] if p["id"] == "hold_limit")
    assert hold["value"] == "70s"                   # in the unit of the old value
    assert "deadline: 70s   # @amend:hold_limit" in new
    assert "# seconds a robot may stop on the road" in new     # comments stay
    # only the declaration and the tagged number changed, so the gate passes
    gate = amend.check_amendment(V1, new, amend.load_constitution(CONSTITUTION))
    assert gate.ok, gate.reasons
    # an existing `previous:` is replaced, not repeated
    again = amend.declare(amend.set_values(new, {"hold_limit": 80}), {"hold_limit": 80}, "1.2", "1.1", 7)
    assert again.count("previous:") == 1 and ds.yaml.safe_load(again)["sos"]["amendment"]["previous"] == "1.1"
    assert amend.declare("sos:\n  name: X\n", {"x": 1}, "2", "1") == "sos:\n  name: X\n"


def test_an_amendment_is_the_next_version_and_amend_diff_reads_it(ws):
    _town(ws)
    p = workspace.add_amendment("town", {"hold_limit": 70}, "fewer road violations")
    assert p["acceptable"], (p["constitution_problems"], p["problems_introduced"])
    # the effective period moves on by the constitution's trial_periods
    assert p["version"] == {"from": "1", "to": "1.1", "effective_period": 4}
    after = workspace.source_of("town", p["proposal_id"])
    report = ds.amend_diff(V1, after)
    if report is None:
        pytest.skip("this cadl has no amend-diff")
    assert report["ok"], report
    assert report["new_version"] == "1.1"
    workspace.apply_proposal("town", p["proposal_id"], reason="B and C agreed")
    assert workspace.decisions("town")[-1]["version"]["to"] == "1.1"
    assert "Version 1.1 (previous 1, effective from period 4)." in \
        (ws / "town.decisions.md").read_text(encoding="utf-8")
    # the next one numbers on, and the version can be named or kept
    assert workspace.add_amendment("town", {"hold_limit": 80})["version"]["to"] == "1.2"
    assert workspace.add_amendment("town", {"hold_limit": 80}, version="2")["version"]["to"] == "2"
    kept = workspace.add_amendment("town", {"hold_limit": 80}, version=None)
    assert "version" not in kept
    with pytest.raises(workspace.WorkspaceError, match="must differ"):
        workspace.add_amendment("town", {"hold_limit": 80}, version="1.1")


# ── Trials ──────────────────────────────────────────────────────────


def test_an_evaluator_tries_the_proposal_and_its_numbers_are_recorded(ws):
    _town(ws)
    seen = []

    def simulator(before, after):
        seen.append((amend.values(before)["hold_limit"], amend.values(after)["hold_limit"]))
        return {"results": {"violations": 3, "late_rate": 0.012}, "passed": True, "note": "5 seeds"}

    p = workspace.add_amendment("town", {"hold_limit": 70}, evaluator=simulator)
    assert seen == [(60.0, 70.0)]
    assert p["trial"]["results"] == {"violations": 3.0, "late_rate": 0.012}
    assert p["trial"]["passed"] is True and p["trial"]["by"] == "simulator"
    applied = workspace.apply_proposal("town", p["proposal_id"], reason="the trial met the criteria")
    assert applied["trial"]["results"]["violations"] == 3.0
    assert workspace.decisions("town")[-1]["trial"]["note"] == "5 seeds"
    md = (ws / "town.decisions.md").read_text(encoding="utf-8")
    assert "**Trial** by simulator — passed:" in md and "| late_rate | 0.012 |" in md


def test_record_trial_checks_the_numbers(ws):
    _town(ws)
    p = workspace.add_amendment("town", {"hold_limit": 70})
    t = workspace.record_trial("town", p["proposal_id"], {"violations": 4}, passed=False, by="pilot")
    assert t["results"] == {"violations": 4.0} and t["passed"] is False
    with pytest.raises(workspace.WorkspaceError, match="is a number"):
        workspace.record_trial("town", p["proposal_id"], {"violations": "many"})
    with pytest.raises(workspace.WorkspaceError, match="No pending proposal"):
        workspace.record_trial("town", "nope", {"x": 1})
    with pytest.raises(workspace.WorkspaceError, match="The trial failed"):
        workspace.add_amendment("town", {"hold_limit": 70}, evaluator=lambda a, b: 1 / 0)


def test_run_trial_runs_the_trial_command(ws, tmp_path, monkeypatch):
    _town(ws)
    script = tmp_path / "sim.py"
    script.write_text(
        "import json, os, sys\n"
        "before, after = (open(p).read() for p in sys.argv[1:3])\n"
        "print('warming up')\n"
        "print(json.dumps({'results': {'lines_changed': sum(a != b for a, b in zip(before.splitlines(),"
        " after.splitlines()))}, 'passed': True, 'note': os.environ['CADL_PROPOSAL_ID']}))\n",
        encoding="utf-8")
    p = workspace.add_amendment("town", {"hold_limit": 70})
    with pytest.raises(workspace.WorkspaceError, match="CADL_TRIAL_COMMAND"):
        workspace.run_trial("town", p["proposal_id"])
    monkeypatch.setenv("CADL_TRIAL_COMMAND", f"{sys.executable} {script}")
    t = workspace.run_trial("town", p["proposal_id"])
    assert t["passed"] is True and t["note"] == p["proposal_id"] and t["results"]["lines_changed"] >= 3
    monkeypatch.setenv("CADL_TRIAL_COMMAND", f"{sys.executable} -c 'import sys; sys.exit(3)'")
    with pytest.raises(workspace.WorkspaceError, match="status 3"):
        workspace.run_trial("town", p["proposal_id"])


# ── Rejections ──────────────────────────────────────────────────────


def test_a_rejected_proposal_stays_in_the_decision_record(ws):
    _town(ws)
    p = workspace.add_amendment("town", {"hold_limit": 50}, "shorter stops")
    workspace.record_trial("town", p["proposal_id"], {"late_rate": 0.04}, passed=False)
    out = workspace.discard_proposal("town", p["proposal_id"], reason="late rate above 3 %",
                                     assumptions=["demand as in period 1"])
    assert out["recorded"] and out["trial"]["passed"] is False
    assert p["proposal_id"] not in workspace.proposals("town")
    d = workspace.decisions("town")[-1]
    assert d["outcome"] == "rejected" and d["reason"] == "late rate above 3 %"
    assert d["rationale"] == "shorter stops" and d["trial"]["results"] == {"late_rate": 0.04}
    assert d["version"]["to"] == "1.1"
    md = (ws / "town.decisions.md").read_text(encoding="utf-8")
    assert f"proposal {p['proposal_id']} (rejected)" in md and "— not adopted." in md
    assert "late rate above 3 %" in md and "— failed:" in md
    # the design did not change; a proposal discarded with record=False leaves no trace
    assert workspace.read("town") == V1
    q = workspace.add_amendment("town", {"hold_limit": 70})
    workspace.discard_proposal("town", q["proposal_id"], record=False)
    assert len(workspace.decisions("town")) == 1


# ── History in git ──────────────────────────────────────────────────


def _git_or_skip():
    if shutil.which("git") is None:
        pytest.skip("git is not installed")


def test_a_council_loop_end_to_end_in_git(ws, monkeypatch):
    """Three rounds: try the candidates, adopt the best, reject the rest, and read the
    versions back from git."""
    _git_or_skip()
    monkeypatch.setenv("CADL_WORKSPACE_GIT", "1")
    _town(ws)

    def simulator(before, after):        # a stand-in model: fewer violations near 80 s
        v = amend.values(after)["hold_limit"]
        return {"results": {"violations": abs(v - 80) / 10}, "passed": abs(v - 80) < 30}

    for _ in range(3):
        current = amend.values(workspace.read("town"))["hold_limit"]
        tried = [workspace.add_amendment("town", {"hold_limit": current + d}, f"try {current + d:g}",
                                         evaluator=simulator) for d in (-10, 10)]
        score = {p["proposal_id"]: p["trial"]["results"]["violations"] for p in tried}
        best = min(tried, key=lambda p: score[p["proposal_id"]])
        if score[best["proposal_id"]] >= abs(current - 80) / 10:
            for p in tried:
                workspace.discard_proposal("town", p["proposal_id"], reason="no improvement")
            continue
        for p in tried:
            if p is not best:
                workspace.discard_proposal("town", p["proposal_id"], reason="the other value did better")
        workspace.apply_proposal("town", best["proposal_id"], reason="the trial improved violations")

    final = workspace.read("town")
    assert amend.values(final)["hold_limit"] == 80
    assert ds.yaml.safe_load(final)["sos"]["version"] == "1.2"
    outcomes = [d["outcome"] for d in workspace.decisions("town")]
    assert outcomes.count("applied") == 2 and outcomes.count("rejected") == 4

    hist = workspace.version_history("town")
    assert [v["version"] for v in hist["versions"]] == ["1", "1.1", "1.2"]
    assert hist["values"] == [{"parameter": "hold_limit", "1": "60", "1.1": "70 ←", "1.2": "80 ←"}]
    assert hist["movement"][0]["steps in all"] == "2" and hist["movement"][0]["reversals"] == "0"
    assert sum("rejected" in c["message"] for c in hist["commits"]) == 4
    body = subprocess.run(["git", "-C", str(ws), "log", "-1", "--format=%B",
                           next(c["commit"] for c in hist["commits"] if "rejected" not in c["message"])],
                          capture_output=True, text=True).stdout
    assert "Reason: the trial improved violations" in body and "Trial: violations = " in body
    # each version passes amend-diff against the one before
    texts = [workspace.version_source("town", v["commit"]) for v in hist["versions"]]
    for old, new in zip(texts, texts[1:]):
        report = ds.amend_diff(old, new)
        assert report is None or report["ok"], report
    # the constitution is committed with the design
    tracked = subprocess.run(["git", "-C", str(ws), "ls-files"], capture_output=True, text=True).stdout
    assert "town.constitution.yaml" in tracked and "town.decisions.md" in tracked
    with pytest.raises(workspace.WorkspaceError):
        workspace.version_source("town", "not-a-commit")


def test_version_history_without_git(ws):
    _town(ws)
    hist = workspace.version_history("town")
    assert hist["commits"] == [] and hist["from"] == "snapshots" and "CADL_WORKSPACE_GIT" in hist["note"]
    assert [v["version"] for v in hist["versions"]] == ["1"] and hist["versions"][0]["commit"] is None


# ── The MCP tools ───────────────────────────────────────────────────


def test_the_mcp_tools_run_the_loop(ws, monkeypatch):
    pytest.importorskip("mcp")
    _git_or_skip()
    monkeypatch.setenv("CADL_WORKSPACE_GIT", "1")
    import mcp_server

    _town(ws, CONSTITUTION_BY_OPERATOR)
    p = mcp_server.propose_amendment("town", {"hold_limit": 70}, "fewer violations")
    assert p["version"]["to"] == "1.1" and p["decided_by"] == {"hold_limit": "OPERATOR"}
    assert mcp_server.record_trial("town", p["proposal_id"], {"violations": 2.0}, True)["trial"]["passed"]
    assert "error" in mcp_server.record_trial("town", p["proposal_id"], {"violations": "two"})
    assert "error" in mcp_server.run_trial("town", p["proposal_id"])     # no command set
    applied = mcp_server.apply_proposal("town", p["proposal_id"], reason="the operator agreed")
    assert applied["applied"] and applied["version"]["to"] == "1.1"
    q = mcp_server.propose_amendment("town", {"hold_limit": 80}, version="")
    assert "version" not in q
    assert mcp_server.discard_proposal("town", q["proposal_id"], reason="not now")["recorded"]
    decisions = mcp_server.get_decisions("town")["decisions"]
    assert [d["outcome"] for d in decisions] == ["applied", "rejected"]
    hist = mcp_server.get_history("town")
    assert [v["version"] for v in hist["versions"]] == ["1", "1.1"]
    assert json.dumps(hist)          # the result goes over the wire as JSON


# ── The Designer's sidebar ──────────────────────────────────────────


def test_the_sidebar_shows_the_versions_in_git(ws, monkeypatch):
    pytest.importorskip("streamlit.testing.v1")
    from streamlit.testing.v1 import AppTest
    import os
    _git_or_skip()
    monkeypatch.setenv("CADL_WORKSPACE_GIT", "1")
    _town(ws)
    for value in (70, 80):
        p = workspace.add_amendment("town", {"hold_limit": value})
        workspace.apply_proposal("town", p["proposal_id"], reason="the trial improved it")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    at = AppTest.from_file(os.path.join(root, "views", "designer.py"), default_timeout=60).run()
    next(b for b in at.button if b.label == "Open").click()
    at = at.run()
    assert not at.exception
    assert any(e.label.startswith("Versions in git (3)") for e in at.expander)
    tables = [d.value for d in at.dataframe]
    assert any(list(t.columns) == ["parameter", "1", "1.1", "1.2"] for t in tables)
