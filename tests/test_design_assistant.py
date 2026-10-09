"""Structured changes, read-back, the shared workspace and the MCP server."""
import asyncio
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("cadl")

from backend.services import design_ops as ops
from backend.services import design_readback as readback
from backend.services import design_service as ds
from backend.services import design_workspace as workspace

DELIVERY = ds.load_example("sos dsl robot delivery")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("CADL_WORKSPACE", str(tmp_path))
    return tmp_path


# ── Change operations ───────────────────────────────────────────────

def test_ops_build_a_design_step_by_step():
    proposal = ops.propose(ds.NEW_DESIGN, [
        {"op": "set_system", "fields": {"description": "A depot", "type": "Directed"}},
        {"op": "upsert", "section": "actors", "id": "AUDITOR",
         "fields": {"role": "auditor", "autonomy": "medium"}},
        {"op": "upsert", "section": "contracts", "id": "SERVICE_AGREEMENT",
         "fields": {"parties": ["COORDINATOR", "WORKER[*]", "AUDITOR"],
                    "authority": {"beta": 0.9}, "assume": None}},
        {"op": "add_contract_from_template", "template": "Safety", "id": "NO_COLLISION",
         "parties": ["COORDINATOR", "WORKER[*]"]},
        {"op": "upsert_transition", "contract": "SERVICE_AGREEMENT", "id": "abort",
         "fields": {"from": ["Proposed", "Active"], "to": "Violated", "on": "abort_requested"}},
        {"op": "upsert_monitor", "contract": "SERVICE_AGREEMENT", "id": "watch",
         "fields": {"observe": "time", "rule": "now > deadline", "on_match": {"transition": "Violated"}}},
        {"op": "upsert", "section": "protocols", "id": "ASSIGN",
         "fields": {"trigger": "new_task", "steps": ["COORDINATOR -> WORKER[i] : task_assignment"]}},
        {"op": "upsert", "section": "algorithms", "id": "allocation", "fields": {"central": "hungarian"}},
        {"op": "upsert", "section": "metrics", "id": "completion_time",
         "fields": {"formula": "avg(times)", "target": "<= 300s"}},
    ])
    assert proposal.acceptable and proposal.errors_after == 0
    assert [c.summary for c in proposal.changes][:3] == [
        "Set system description, type", "Added actor AUDITOR",
        "Updated contract SERVICE_AGREEMENT (parties, authority, assume)"]
    sos = ds.load_doc(proposal.source)["sos"]
    contract = sos["contracts"][0]
    assert "assume" not in contract                      # null removes a key
    assert contract["authority"] == {"decision_holder": "COORDINATOR", "beta": 0.9}  # merged
    assert {"Actors", "Contracts", "Lifecycle", "Monitors", "Protocols"} <= set(proposal.diff)
    assert (proposal.changes[1].section, proposal.changes[1].item) == ("Actors", "AUDITOR")


def test_ops_update_remove_and_rename():
    source, _ = ops.apply_ops(ds.NEW_DESIGN, [
        {"op": "upsert", "section": "actors", "id": "WORKER", "fields": {"role": "picker"}},
        {"op": "rename", "section": "contracts", "id": "SERVICE_AGREEMENT", "new_id": "PICKING_SLA"},
        {"op": "remove_transition", "contract": "PICKING_SLA", "id": "finish"},
        {"op": "upsert", "section": "regimes", "id": "NORMAL->BUSY", "fields": {"condition": "load > 0.8"}},
        {"op": "upsert", "section": "regimes", "id": "BUSY -> NORMAL", "fields": {"condition": "load < 0.5"}},
        {"op": "remove", "section": "regimes", "id": "BUSY->NORMAL"},
    ])
    sos = ds.load_doc(source)["sos"]
    # `WORKER` addresses the actor written `WORKER[1..N]`, which keeps its id.
    assert [a["id"] for a in sos["actors"]] == ["COORDINATOR", "WORKER[1..N]"]
    assert sos["actors"][1]["role"] == "picker"
    assert sos["contracts"][0]["id"] == "PICKING_SLA"
    assert [t["id"] for t in sos["contracts"][0]["lifecycle"]["transitions"]] == ["start"]
    assert sos["transitions"] == [{"from": "NORMAL", "to": "BUSY", "condition": "load > 0.8"}]


@pytest.mark.parametrize("bad, message", [
    ([{"op": "fly"}], "Unknown op `fly`"),
    ([{"op": "upsert", "section": "nope", "id": "x", "fields": {}}], "Unknown section `nope`"),
    ([{"op": "remove", "section": "actors", "id": "GHOST"}], "No actor `GHOST` to remove"),
    ([{"op": "upsert", "section": "actors", "id": "A"}], "needs `fields`"),
    ([{"op": "upsert_monitor", "contract": "NOPE", "id": "m", "fields": {}}], "No contract `NOPE`"),
    ([{"op": "set_system", "fields": {"contracts": []}}], "cannot set contracts"),
    ([{"op": "set_lifecycle", "contract": "SERVICE_AGREEMENT", "preset": "x"}], "Unknown lifecycle preset"),
    ([{"op": "upsert", "section": "regimes", "id": "NOARROW", "fields": {}}], "FROM->TO"),
    ([], "non-empty list"),
])
def test_ops_reject_bad_requests_with_a_reason(bad, message):
    with pytest.raises(ops.OpError, match=message.replace("(", r"\(")):
        ops.apply_ops(ds.NEW_DESIGN, bad)


def test_ops_number_the_failing_change_and_apply_nothing():
    changes = [
        {"op": "upsert", "section": "actors", "id": "A", "fields": {"role": "x"}},
        {"op": "remove", "section": "actors", "id": "GHOST"},
    ]
    with pytest.raises(ops.OpError, match="Change 2:"):
        ops.apply_ops(ds.NEW_DESIGN, changes)


def test_proposal_reports_problems_introduced_and_resolved():
    fix = ops.propose(DELIVERY, [
        {"op": "upsert_transition", "contract": "DELIVERY_SLA", "id": "terminate",
         "fields": {"from": "Proposed", "to": "Terminated", "on": "cancel_requested"}}])
    assert fix.acceptable and not fix.introduced
    assert any("Terminated" in f.message for f in fix.resolved)

    harm = ops.propose(DELIVERY, [
        {"op": "upsert", "section": "contracts", "id": "DELIVERY_SLA",
         "fields": {"guarantee": ["delivery_time <= 300s", "delivery_time > 400s"]}}])
    assert not harm.acceptable
    assert any(f.level == "error" for f in harm.introduced)


def test_open_questions_follow_the_gaps():
    questions = ops.open_questions(ds.NEW_DESIGN)
    assert [q.priority for q in questions] == sorted(q.priority for q in questions)
    assert any("breach of SERVICE_AGREEMENT" in q.question for q in questions)
    assert any("no protocols" in q.why for q in questions)

    closed, _ = ops.apply_ops(ds.NEW_DESIGN, [
        {"op": "upsert", "section": "contracts", "id": "SERVICE_AGREEMENT",
         "fields": {"violation": {"detect": "completion_time > 300s", "action": "notify(COORDINATOR)"}}}])
    assert not any("breach of SERVICE_AGREEMENT" in q.question for q in ops.open_questions(closed))

    with_loner, _ = ops.apply_ops(ds.NEW_DESIGN, [
        {"op": "upsert", "section": "actors", "id": "LONER", "fields": {"role": "observer"}}])
    assert any(q.item == "LONER" and "obliged" in q.question for q in ops.open_questions(with_loner))
    assert ops.open_questions("sos: [")[0].priority == 1


# ── Read-back ───────────────────────────────────────────────────────

def test_contracts_read_back_in_sentences():
    lines = readback.describe_design(DELIVERY)["DELIVERY_SLA"]
    text = " ".join(lines)
    assert lines[0] == "DELIVERY_SLA is an agreement between DISPATCHER, ROBOT[*] and CUSTOMER[*]."
    assert "As long as `ROBOT[i].battery > 20` holds" in text
    assert "DISPATCHER decides; decisions are strongly centralised (β = 0.8)." in text
    assert "starts in Proposed and ends in Completed, Violated or Terminated" in text
    assert "Monitor battery_guard watches" in text


def test_actor_view_states_duties_and_messages():
    view = readback.actor_view(ds.load_example("a sos robot delivery"), "ROBOT[i]")
    assert view["actor"] == "ROBOT"
    assert any("must follow DISPATCHER's decisions" in line for line in view["contracts"])
    assert any("TASK_DISPATCH" in line and "receives route_assignment from DISPATCHER" in line
               for line in view["protocols"])
    with pytest.raises(ValueError, match="No actor"):
        readback.actor_view(DELIVERY, "NOBODY")


def test_lifecycle_stories_and_stepping():
    stories = readback.lifecycle_stories(DELIVERY, "DELIVERY_SLA")
    assert 1 < len(stories) <= readback.MAX_STORIES
    assert all(s["path"][0] == "Proposed" and s["ended"] for s in stories)
    happy = next(s for s in stories if s["path"][-1] == "Completed")
    assert happy["outcome"] == "ends in Completed"
    assert "`assign` takes it to Assigned" in happy["story"]
    missed = next(s for s in stories if "missed deadline" in s["outcome"])
    assert "misses its deadline" in missed["story"] or "monitor" in missed["story"]

    start = readback.narrate_path(DELIVERY, "DELIVERY_SLA", [])
    assert start["path"] == ["Proposed"] and not start["ended"]
    assert {"to": "Assigned", "event": "assign"}.items() <= start["next"][0].items()
    with pytest.raises(ValueError, match="Nothing takes an instance from `Proposed` to `Completed`"):
        readback.narrate_path(DELIVERY, "DELIVERY_SLA", ["Proposed", "Completed"])


def test_verification_is_explained_with_the_counterexample():
    rows = readback.explain_verification(ds.NEW_DESIGN)
    entailment = next(r for r in rows if "entailment" in r["check"])
    assert "real obligations" in entailment["meaning"] and "For instance" in entailment["meaning"]


# ── Workspace ───────────────────────────────────────────────────────

def test_workspace_proposal_apply_review_and_undo(ws):
    workspace.create("depot", "template")
    original = workspace.read("depot")
    result = workspace.add_proposal("depot", [
        {"op": "upsert", "section": "actors", "id": "AUDITOR", "fields": {"role": "auditor"}}],
        rationale="the user said audits are required")
    assert result["applied"] is False and workspace.read("depot") == original
    assert result["changes"] == ["Added actor AUDITOR"]

    applied = workspace.apply_proposal("depot", result["proposal_id"])
    assert applied["applied"] and "AUDITOR" in workspace.read("depot")
    marks = workspace.unreviewed("depot")
    assert marks[0]["summary"] == "Added actor AUDITOR"
    assert marks[0]["rationale"] == "the user said audits are required"
    assert workspace.mark_reviewed("depot", [0]) == 0

    assert workspace.undo("depot") == original
    with pytest.raises(workspace.WorkspaceError, match="Nothing to undo"):
        workspace.undo("depot")


def test_workspace_refuses_stale_and_unknown_proposals(ws):
    workspace.create("depot", "template")
    result = workspace.add_proposal("depot", [
        {"op": "upsert", "section": "actors", "id": "A", "fields": {"role": "x"}}])
    workspace.write("depot", workspace.read("depot") + "\n# edited by hand\n")
    with pytest.raises(workspace.WorkspaceError, match="No pending proposal"):
        workspace.apply_proposal("depot", result["proposal_id"])
    with pytest.raises(workspace.WorkspaceError, match="Unknown op"):
        workspace.add_proposal("depot", [{"op": "fly"}])


@pytest.mark.parametrize("name", ["../evil", "a/b", "", ".hidden", "x" * 65, "a b"])
def test_workspace_rejects_names_that_leave_the_folder(ws, name):
    with pytest.raises(workspace.WorkspaceError):
        workspace.create(name)
    assert list(ws.iterdir()) == []


# ── MCP server ──────────────────────────────────────────────────────

def test_mcp_tools_cover_the_design_loop(ws):
    pytest.importorskip("mcp")
    import mcp_server as server

    assert server.create_design("depot", "sos dsl robot delivery")["outline"]["contracts"] == ["DELIVERY_SLA"]
    assert "error" in server.create_design("depot")            # already exists
    assert server.open_questions("depot")["questions"]
    proposed = server.propose_changes("depot", [
        {"op": "upsert_transition", "contract": "DELIVERY_SLA", "id": "terminate",
         "fields": {"from": "Proposed", "to": "Terminated", "on": "cancel_requested"}}])
    assert proposed["acceptable"] and proposed["problems_resolved"]
    assert server.get_design("depot")["pending_proposals"] == [proposed["proposal_id"]]
    assert server.apply_proposal("depot", proposed["proposal_id"])["applied"]
    assert server.get_design("depot")["unreviewed_changes"] == ["Added transition terminate in DELIVERY_SLA"]
    assert server.check_design("depot")["passes"]
    assert "DELIVERY_SLA" in server.describe_contracts("depot")["contracts"]
    assert server.actor_view("depot", "ROBOT")["actor"] == "ROBOT"
    assert server.lifecycle_stories("depot", "DELIVERY_SLA")["stories"]
    assert server.step_lifecycle("depot", "DELIVERY_SLA")["next"]
    assert server.explain_verification("depot")["results"]
    assert server.get_diagram("depot", "architecture")["format"] == "dot"
    assert server.get_diagram("depot", "lifecycle", "DELIVERY_SLA")["diagram"].startswith("digraph")
    assert "error" in server.get_diagram("depot", "protocol", "NONE")
    assert server.export_design("depot")["written"].endswith("depot.report.html")
    assert server.undo_last_change("depot")["undone"]
    for call in (server.get_design("nope"), server.propose_changes("depot", [{"op": "fly"}]),
                 server.apply_proposal("depot", "zzz"), server.actor_view("depot", "NOBODY")):
        assert set(call) == {"error"}

    tools = asyncio.run(server.mcp.list_tools())
    assert {"propose_changes", "apply_proposal", "open_questions", "lifecycle_stories"} <= {
        t.name for t in tools}
    assert all(t.description for t in tools)


def test_mcp_server_speaks_the_protocol_over_stdio(ws):
    pytest.importorskip("mcp")
    from mcp.client.session import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client

    root = os.path.join(os.path.dirname(__file__), "..")

    async def run():
        params = StdioServerParameters(
            command=sys.executable, args=[os.path.join(root, "mcp_server.py")],
            env={**os.environ, "CADL_WORKSPACE": str(ws)})
        async with stdio_client(params) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                created = await session.call_tool("create_design", {"name": "wire"})
                prompt = await session.get_prompt("design_interview", {"name": "wire"})
                return names, json.loads(created.content[0].text), prompt.messages[0].content.text

    names, created, prompt = asyncio.run(run())
    assert "propose_changes" in names
    assert created["created"] == "wire" and (ws / "wire.cadl").exists()
    assert "open_questions" in prompt


def test_renaming_an_actor_updates_every_reference():
    proposal = ops.propose(ds.NEW_DESIGN, [
        {"op": "rename", "section": "actors", "id": "WORKER", "new_id": "ROBOT"},
        {"op": "rename", "section": "actors", "id": "COORDINATOR[i]", "new_id": "DISPATCHER"},
    ])
    assert proposal.acceptable and proposal.errors_after == 0 and not proposal.introduced
    assert "WORKER" not in proposal.source and "COORDINATOR" not in proposal.source
    sos = ds.load_doc(proposal.source)["sos"]
    assert [a["id"] for a in sos["actors"]] == ["DISPATCHER", "ROBOT[1..N]"]   # keeps [1..N]
    contract = sos["contracts"][0]
    assert contract["parties"] == ["DISPATCHER", "ROBOT[*]"]
    assert contract["authority"]["decision_holder"] == "DISPATCHER"
    assert contract["lifecycle"]["transitions"][0]["on"] == "DISPATCHER -> ROBOT[i] : task_assignment"
    # The proposal says how the touched contract would read afterwards.
    assert proposal.readback["SERVICE_AGREEMENT"][0] == \
        "SERVICE_AGREEMENT is an agreement between DISPATCHER and ROBOT[*]."


def test_renaming_a_protocol_updates_regime_transitions():
    source, _ = ops.apply_ops(ds.load_example("a sos robot delivery"), [
        {"op": "rename", "section": "protocols", "id": "TASK_DISPATCH", "new_id": "DISPATCH"}])
    sos = ds.load_doc(source)["sos"]
    assert sos["transitions"][0]["protocol"] == "DISPATCH"
    assert not [f for f in ds.analyze(source).findings if "undefined protocol" in f.title]


def test_an_empty_design_starts_with_the_basic_questions(ws):
    source = workspace.create("depot")            # "empty" is the default
    assert ds.load_doc(source) == {"sos": {"name": "depot"}}
    questions = ops.open_questions(source)
    assert [q.priority for q in questions[:2]] == [1, 1]
    assert "governed" in questions[0].question
    assert "Which systems or organisations take part" in questions[1].question
    assert "one sentence" in questions[-1].question
    assert "SERVICE_AGREEMENT" in workspace.create("with-placeholders", "template")
    with pytest.raises(workspace.WorkspaceError, match="Use `empty`, `template`"):
        workspace.create("x", "nonsense")


def test_limits_written_in_several_places_are_compared():
    source, _ = ops.apply_ops(ds.NEW_DESIGN, [
        {"op": "upsert", "section": "contracts", "id": "SERVICE_AGREEMENT",
         "fields": {"guarantee": ["completion_time <= 120s"],
                    "violation": {"detect": "completion_time > 300s", "action": "notify"}}}])
    titles = [f.title for f in ds.analyze(source).findings
              if f.stage == "Consistency" and f.level == "warning"]
    assert "Contract 'SERVICE_AGREEMENT' detects violations at a different limit than it guarantees" in titles
    # The template's `finish` deadline is 300s, longer than the 120s now guaranteed.
    assert any("transition 'finish' may take longer" in t for t in titles)
    assert not [f for f in ds.analyze(ds.NEW_DESIGN).findings
                if f.stage == "Consistency" and f.level == "warning"]


def test_responsibilities_and_violation_response_are_read_back():
    source, _ = ops.apply_ops(ds.NEW_DESIGN, [
        {"op": "upsert", "section": "contracts", "id": "SERVICE_AGREEMENT",
         "fields": {"violation": {"detect": "completion_time > 300s",
                                  "action": "notify the coordinator and reassign"},
                    "responsibilities": {"COORDINATOR": ["never assign to a worker at or below 20 battery"]}}}])
    assert ds.analyze(source).ok
    lines = readback.describe_design(source)["SERVICE_AGREEMENT"]
    assert "COORDINATOR is specifically responsible for: never assign to a worker at or below 20 battery." in lines
    assert "Its own responsibility" in readback.actor_view(source, "COORDINATOR")["contracts"][0]
    assert "Its own responsibility" not in readback.actor_view(source, "WORKER")["contracts"][0]

    violated = next(s for s in readback.lifecycle_stories(source, "SERVICE_AGREEMENT")
                    if s["path"][-1] == "Violated")
    assert violated["outcome"] == "ends in Violated after a missed deadline"   # no monitor here
    assert "prescribes `notify the coordinator and reassign`" in violated["story"]
    assert "does not continue" in violated["story"]


def test_read_back_tools_can_read_a_pending_proposal(ws):
    pytest.importorskip("mcp")
    import mcp_server as server

    server.create_design("depot", "template")
    proposed = server.propose_changes("depot", [
        {"op": "upsert", "section": "contracts", "id": "SERVICE_AGREEMENT",
         "fields": {"guarantee": ["completion_time <= 600s"]}},
        {"op": "upsert_transition", "contract": "SERVICE_AGREEMENT", "id": "finish",
         "fields": {"deadline": "600s"}}])
    pid = proposed["proposal_id"]
    assert "`completion_time <= 600s`" in " ".join(proposed["contracts_would_read"]["SERVICE_AGREEMENT"])

    applied_story = server.lifecycle_stories("depot", "SERVICE_AGREEMENT")["stories"][-1]["story"]
    pending_story = server.lifecycle_stories("depot", "SERVICE_AGREEMENT", pid)["stories"][-1]["story"]
    assert "300s" in applied_story and "600s" in pending_story
    assert "600s" in " ".join(server.describe_contracts("depot", proposal_id=pid)["contracts"]["SERVICE_AGREEMENT"])
    assert "error" in server.describe_contracts("depot", proposal_id="zzz")
    assert "Designer page" in server.apply_proposal("depot", pid)["note"]
    assert server.create_design("blank-start")["outline"]["actors"] == []      # empty by default
    assert "note" in server.create_design("with-terms", "template")
