"""CADL design tools as an MCP server.

Lets an AI assistant (Claude Desktop, Claude Code or any MCP client)
design a System of Systems with a person: it asks what is missing,
proposes small structured changes, has them checked by the CADL
toolchain, and reads the result back in plain language. The assistant
never edits the source directly, and nothing changes until a proposal
is applied.

Run:    python mcp_server.py            (stdio transport)
Config: CADL_WORKSPACE=/path/to/folder  where designs are kept
        (default: ~/cadl-designs)

The Designer page shows the same folder when the app is started with
the same CADL_WORKSPACE.
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:  # mcp 2.x
    from mcp.server.mcpserver import MCPServer as _Server
except ImportError:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as _Server

from backend.plotting.architecture import architecture_to_dot, regimes_to_dot
from backend.plotting.sequence import sequence_svg
from backend.services import design_ops as ops
from backend.services import design_readback as readback
from backend.services import design_service as ds
from backend.services import design_workspace as workspace
from cadl_sim.sos_dsl import build_lifecycle_view, lifecycle_to_dot

INSTRUCTIONS = """\
These tools design a System of Systems in CADL together with a person.

Work in small steps and keep the person in charge:
1. Start with list_designs, then create_design or get_design.
2. Call open_questions and ask the person the most important one or two.
   Do not invent answers to them.
3. Turn what they say into changes and call propose_changes. Nothing is
   applied yet. Show the person the returned changes and any problems
   introduced, in plain words.
4. Call apply_proposal only after the person agrees, and pass their
   reason (and any assumptions they state): it goes into the decision
   record. If the proposal adds an error, revise it instead.
5. Confirm meaning, not just consistency: use describe_contracts,
   actor_view and lifecycle_stories to read the design back and ask
   "is this what you meant?". The checks cannot tell you that.
6. check_design passing means the design is consistent, not that it is
   right. Say so when you report results.

Values marked `# @amend:<name>` (Robot Town, Course D) are changed with
list_amendables and propose_amendment, which keep the comments and check
the constitution. When the constitution leaves a change to a person (a
contract or regulation layer, a safety-related rule, or a value whose
`decided_by` names someone), do not decide it: lay out what it changes and
for whom, give your recommendation and what you do not know, and let the
person choose. A tool's numbers are measurements; your explanation is not
evidence. In a design that declares the amendment extension, an amendment
is also the next version (sos.version, amendment.previous, effective
period and the declared value). To go back to an earlier version's values
after several amendments, use propose_reversion: one new version that
declares amendment.reverts, instead of stepping each value back.

Before deciding an amendment, try it when a trial is available: run_trial
runs the workspace's trial command (a simulator), or record_trial keeps the
numbers of a trial run elsewhere. When the person turns a proposal down,
call discard_proposal with their reason: rejections stay in the decision
record too. get_history lists the versions and how the values moved.

Never write CADL source by hand; use propose_changes. Expressions in
assume / guarantee are CADL expressions such as `delivery_time <= 300s`
or `ROBOT[i].battery > 20`.
"""

mcp = _Server("cadl-design", instructions=INSTRUCTIONS)


def _fail(error: Exception) -> Dict[str, Any]:
    return {"error": str(error)}


def _findings(analysis: ds.DesignAnalysis, levels=("error", "warning", "info")) -> List[dict]:
    return [
        {"level": f.level, "check": f.stage, "title": f.title, "message": f.message,
         "about": " → ".join(x for x in (f.section, f.item) if x)}
        for f in analysis.findings if f.level in levels
    ]


def _outline(analysis: ds.DesignAnalysis) -> Dict[str, Any]:
    ir = analysis.ir or {}
    institution = ir.get("institution") or {}
    return {
        "name": analysis.name, "type": analysis.sos_type,
        "actors": [str(a.get("id")) for a in institution.get("actors") or []],
        "contracts": [str(c.get("id")) for c in institution.get("contracts") or []],
        "protocols": [str(p.get("id")) for p in (ir.get("protocol") or {}).get("protocols") or []],
        "algorithms": [str(a.get("name")) for a in (ir.get("algorithm") or {}).get("algorithms") or []],
        "regime_transitions": [f"{t.get('from_regime')}->{t.get('to_regime')}"
                               for t in ir.get("transitions") or []],
        "metrics": [str(m.get("id")) for m in ir.get("metrics") or []],
        "errors": analysis.count("error"), "warnings": analysis.count("warning"),
    }


@mcp.tool()
def list_designs() -> Dict[str, Any]:
    """List the designs in the workspace, and the bundled examples a new design can start from."""
    return {
        "workspace": str(workspace.root()),
        "designs": workspace.list_designs(),
        "examples": list(ds.list_examples()),
        "contract_templates": {k: v[0] for k, v in ds.CONTRACT_TEMPLATES.items()},
        "lifecycle_presets": list(ds.LIFECYCLE_PRESETS),
    }


@mcp.tool()
def create_design(name: str, start: str = "empty") -> Dict[str, Any]:
    """Create a design in the workspace.

    `start` is one of:
      "empty"    — only the name; nothing is invented. Use this when the
                   person describes their own system. open_questions then
                   tells you what to ask first.
      "template" — a small two-actor design. Its names, numbers and terms
                   are placeholders, NOT the person's intent: confirm or
                   replace every one of them.
      an example name from list_designs — a complete design to study or adapt.
    """
    try:
        source = workspace.create(name, start)
    except ValueError as e:
        return _fail(e)
    result = {"created": name, "outline": _outline(ds.analyze(source))}
    if start != "empty":
        result["note"] = (
            "This design already contains terms nobody in this conversation chose. Read it "
            "with describe_contracts and confirm or replace them with the person.")
    return result


@mcp.tool()
def get_design(name: str, include_source: bool = False) -> Dict[str, Any]:
    """Return a design's outline, its problems and what is waiting for review.

    Set include_source to also get the CADL text; the outline is usually enough.
    """
    try:
        source = workspace.read(name)
    except ValueError as e:
        return _fail(e)
    analysis = ds.analyze(source)
    result = {
        "outline": _outline(analysis),
        "problems": _findings(analysis, ("error", "warning")),
        "pending_proposals": list(workspace.proposals(name)),
        "unreviewed_changes": [u["summary"] for u in workspace.unreviewed(name)],
    }
    if include_source:
        result["source"] = source
    return result


@mcp.tool()
def check_design(name: str) -> Dict[str, Any]:
    """Run every check on a design and say what each check covers.

    A design that passes is consistent; that does not make it what the
    designer intended. `not_checked` lists what no check looks at.
    """
    try:
        analysis = ds.analyze(workspace.read(name))
    except ValueError as e:
        return _fail(e)
    return {
        "passes": analysis.ok,
        "findings": _findings(analysis),
        "checks": {stage: ds.STAGE_SCOPE[stage] for stage in ds.STAGES},
        "not_checked": ds.NOT_CHECKED,
    }


@mcp.tool()
def open_questions(name: str, limit: int = 8) -> Dict[str, Any]:
    """Questions to ask the designer next, most important first.

    Each comes with the reason it matters. Ask the person; do not answer
    for them. Priority 1 blocks a sound design, 2 is a gap, 3 a refinement.
    """
    try:
        questions = ops.open_questions(workspace.read(name))
    except ValueError as e:
        return _fail(e)
    return {
        "total": len(questions),
        "questions": [
            {"priority": q.priority, "question": q.question, "why": q.why,
             "about": " → ".join(x for x in (q.section, q.item) if x)}
            for q in questions[: max(1, min(limit, 30))]
        ],
    }


@mcp.tool()
def propose_changes(name: str, changes: List[Dict[str, Any]], rationale: str = "") -> Dict[str, Any]:
    """Check a set of changes against a design WITHOUT applying them.

    Actor ids: define a group as "ROBOT[1..N]" and a single actor as
    "DISPATCHER". Refer to the group as "ROBOT[*]" (all members, e.g. in
    parties) or "ROBOT[i]" (one member, in expressions and steps). For the
    `id` of an op, "ROBOT" and "ROBOT[1..N]" address the same actor.
    Outlines and messages show the bare name "ROBOT".

    Expressions (assume, guarantee, violation.detect, when, rule) are
    checked. Durations are written 500ms, 600s, 10min or 2h; there is no
    `%` or day unit, so write a percentage as a plain number. In contrast
    violation.action, violation.escalation, incentive rules, information
    views and responsibilities are free text: nothing checks them, so
    write them as plain statements of what should happen.

    A duty of one particular party goes in the contract's
    "responsibilities": {"DISPATCHER": ["never assign to a robot at or
    below 20 battery"]}. assume / guarantee bind all parties together.

    A lifecycle ends in its terminal states. If something should continue
    after a violation (for example reassignment), model it as states and
    transitions, or say it is a new contract instance — violation.action
    alone does not make the lifecycle continue.

    When a limit appears in several places (a guarantee, violation.detect,
    a transition deadline), change all of them together.

    Each change is a mapping with an "op":
      {"op": "set_system", "fields": {"name": "DepotSoS", "description": "...",
       "type": "Directed" | "Acknowledged" | "Collaborative" | "Virtual"}}
      {"op": "upsert", "section": "actors", "id": "ROBOT[1..N]",
       "fields": {"role": "delivery_agent", "autonomy": "high", "capabilities": ["deliver"],
                  "interface": {"input": ["route_assignment"], "output": ["position_report"]}}}
      {"op": "upsert", "section": "contracts", "id": "DELIVERY_SLA",
       "fields": {"parties": ["DISPATCHER", "ROBOT[*]"], "assume": ["ROBOT[i].battery > 20"],
                  "guarantee": ["delivery_time <= 300s"],
                  "authority": {"decision_holder": "DISPATCHER", "beta": 0.8},
                  "information": {"alpha": 0.9}, "incentives": {"type": "task_completion", "lambda": 0.5},
                  "violation": {"detect": "delivery_time > 300s", "action": "notify(DISPATCHER)"}}}
      {"op": "add_contract_from_template", "template": "Safety", "id": "NO_COLLISION",
       "parties": ["DISPATCHER", "ROBOT[*]"]}
      {"op": "set_lifecycle", "contract": "DELIVERY_SLA", "preset": "Propose → accept → fulfil"}
      {"op": "set_lifecycle", "contract": "C", "fields": {"states": ["A", "B"], "initial": "A", "terminal": ["B"]}}
      {"op": "upsert_transition", "contract": "C", "id": "finish",
       "fields": {"from": "A", "to": "B", "on": "work_done", "deadline": "60s",
                  "on_violation": {"transition": "Violated", "severity": "Major"}}}
      {"op": "upsert_monitor", "contract": "C", "id": "battery_guard",
       "fields": {"observe": "ROBOT[i].battery", "sampling": "periodic(500ms)",
                  "rule": "ROBOT[i].battery < 20", "on_match": {"transition": "Violated", "severity": "Major"}}}
      {"op": "upsert", "section": "protocols", "id": "TASK_DISPATCH",
       "fields": {"trigger": "new_request", "steps": ["DISPATCHER -> ROBOT[i] : route_assignment",
                  "ROBOT[i] : follow_path"], "timing": {"max_total": "5s"}}}
      {"op": "upsert", "section": "algorithms", "id": "pathfinding", "fields": {"central": "ECBS", "local": "none"}}
      {"op": "upsert", "section": "regimes", "id": "NORMAL->CONGESTED",
       "fields": {"condition": "load > 0.8", "protocol": "TASK_DISPATCH"}}
      {"op": "upsert", "section": "metrics", "id": "delivery_time",
       "fields": {"formula": "avg(delivery_times)", "target": "<= 300s"}}
      {"op": "remove", "section": "actors", "id": "CUSTOMER"}
      {"op": "rename", "section": "actors", "id": "WORKER", "new_id": "ROBOT"}
          (renames the actor everywhere it is referred to: parties, decision
           holders, expressions, lifecycle events and protocol steps)
      {"op": "rename", "section": "contracts", "id": "OLD", "new_id": "NEW"}
      {"op": "remove_transition" | "remove_monitor", "contract": "C", "id": "..."}

    `fields` merge into the item; a null value removes a key. The result
    lists what would change, problems it would introduce or resolve,
    `contracts_would_read` (each touched contract restated in plain
    sentences as it would be after the change) and a proposal_id. Show
    it to the person, then call apply_proposal.
    Keep proposals small: a few related changes at a time.
    """
    try:
        return workspace.add_proposal(name, changes, rationale)
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def apply_proposal(name: str, proposal_id: str, allow_errors: bool = False,
                   reason: str = "", assumptions: Optional[List[str]] = None,
                   decided_by: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Apply a proposal the person has agreed to, and record their decision.

    The changes are recorded as not yet reviewed, so the Designer page
    shows them for confirmation. undo_last_change takes them back.

    reason: the person's reason for the decision, in their words (not
    yours). assumptions: what they take for granted in deciding. Both go
    into the decision record (`<name>.decisions.md`). An amendment the
    constitution leaves to a person is refused without a reason.

    decided_by: who decided, {"kind": "person" | "rule" | "assistant",
    "name": "..."} (name optional, e.g. "FACILITY_MANAGER" or "council rule
    substitute"). Give it when you know: "person" when the person decided,
    "rule" when a fixed rule or script decided in their place, "assistant"
    when you decided. It is recorded with the decision; left out, the
    record says nothing about who decided.

    A proposal that adds an error is refused. Set allow_errors only when
    the person, having seen the error, asked to apply it anyway (for
    example to fix it in a following step).
    """
    try:
        result = workspace.apply_proposal(name, proposal_id, allow_errors, reason, assumptions,
                                          decided_by)
    except ValueError as e:
        return _fail(e)
    result["outline"] = _outline(ds.analyze(workspace.read(name)))
    return result


@mcp.tool()
def list_amendables(name: str) -> Dict[str, Any]:
    """The values the design marks `# @amend:<name>`, and what the constitution says.

    For each name: its value, the lines that hold it, and — when the
    workspace has `<name>.constitution.yaml` or `constitution.yaml` — its
    layer (rule, contract; safety), range and step, and whether a change
    is decided by the checks or by a person. `problems` lists lines that
    disagree and values outside the constitution.
    """
    try:
        return workspace.amendables(name)
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def propose_amendment(name: str, values: Dict[str, float], rationale: str = "",
                      version: Optional[str] = "auto",
                      effective_period: Optional[int] = None) -> Dict[str, Any]:
    """Propose new values for `# @amend:` names, without applying them.

    Every line marked with a name gets the new value, written in place, so
    the comments and the rest of the file stay as they are (propose_changes
    would rewrite the file without its comments). The result says whether
    the constitution allows it (only marked numbers change, one name, one
    step, within the range) and whether a person must decide it (and who,
    when the constitution names them in `decided_by`). Then run_trial or
    record_trial if the change is to be tried, and apply_proposal, with the
    person's reason, or discard_proposal.

    When the design declares the amendment extension (`amendment:`), the
    proposal is the next version: `version` "auto" numbers it (2 → 2.1 →
    2.2, or as `amendment.versioning.scheme` says), a version string names
    it, and an empty string keeps the version. `effective_period` sets
    `amendment.effective.period`; by default it moves on by
    `amendment.versioning.period_step`, else the constitution's
    `trial_periods`. A version or period that breaks the design's
    `amendment.versioning` is reported in `versioning_problems`
    (`version_not_next`, `period_too_soon`) and cannot be applied.
    """
    try:
        return workspace.add_amendment(name, values, rationale, version or None, effective_period)
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def propose_reversion(name: str, to_version: str, rationale: str = "",
                      version: Optional[str] = "auto",
                      effective_period: Optional[int] = None) -> Dict[str, Any]:
    """Propose one new version that restores the values of an earlier version, without applying it.

    Use it to undo a chain of adopted amendments (2 → 2.1 → 2.2 → 2.3) in
    one step instead of walking each value back one step at a time. Every
    `# @amend:` value goes back to its value in `to_version` (read from the
    workspace's git history, or from its snapshots in `<name>.versions.json`
    when git is off), written in
    place so the comments stay. The new version is declared with
    `amendment.previous` the current version and `amendment.reverts`
    `to_version`; `version` "auto" numbers it (2.3 → 2.4) and a version
    string names it. `effective_period` sets `amendment.effective.period`;
    by default it moves on by the constitution's `trial_periods`.

    The constitution's one-name and one-step rules do not hold for a value
    that returns to its value in `to_version` (its range still does), and the
    proposal is checked with `cadl amend-diff current new --base <to_version>`
    (`amend_diff` in the result; needs cadl-lang 0.3.44). The result says
    whether the constitution allows it and whether a person must decide it.
    Then run_trial or record_trial, and apply_proposal with the person's
    reason, or discard_proposal, as for propose_amendment.
    """
    try:
        return workspace.add_reversion(name, to_version, rationale, version or "auto", effective_period)
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def get_decisions(name: str) -> Dict[str, Any]:
    """The decision record: each applied or rejected proposal (`outcome`), why it was
    proposed, its trial results, and the person's reason."""
    try:
        workspace.read(name)
        return {"decisions": workspace.decisions(name)}
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def discard_proposal(name: str, proposal_id: str, reason: str = "",
                     assumptions: Optional[List[str]] = None, record: bool = True,
                     decided_by: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Drop a pending proposal the person does not want.

    The proposal is kept in the decision record as rejected, with the
    person's reason (in their words), their assumptions and any trial
    results. Set record to false only for a proposal made by mistake.
    decided_by says who decided, as for apply_proposal.
    """
    try:
        return workspace.discard_proposal(name, proposal_id, reason, assumptions, record, decided_by)
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def record_trial(name: str, proposal_id: str, results: Dict[str, float],
                 passed: Optional[bool] = None, note: str = "", by: str = "") -> Dict[str, Any]:
    """Keep the numbers of a trial of a pending proposal (a simulation, a pilot).

    results maps names to numbers (for example {"late_rate": 0.012}).
    passed says whether the trial met its criteria, when it has any. The
    trial goes into the decision record when the proposal is applied or
    discarded. Report only numbers the trial produced.
    """
    try:
        return {"proposal_id": proposal_id,
                "trial": workspace.record_trial(name, proposal_id, results, passed, note, by)}
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def run_trial(name: str, proposal_id: str) -> Dict[str, Any]:
    """Try a pending proposal with the workspace's trial command, and keep its numbers.

    The command is set by whoever runs this server (CADL_TRIAL_COMMAND),
    for example a simulator. It gets the design before and after the
    proposal and prints its results; they are recorded as with record_trial.
    """
    try:
        return {"proposal_id": proposal_id, "trial": workspace.run_trial(name, proposal_id)}
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def get_history(name: str, limit: int = 50) -> Dict[str, Any]:
    """The design's history: its commits (with CADL_WORKSPACE_GIT=1), its
    versions, who decided each (`decided_by`, when recorded), and how the
    `# @amend:` values moved from version to version.

    Without git the versions come from the snapshots in `<name>.versions.json`.
    """
    try:
        workspace.read(name)
        return workspace.version_history(name, limit)
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def undo_last_change(name: str) -> Dict[str, Any]:
    """Restore the design as it was before the last applied change."""
    try:
        source = workspace.undo(name)
    except ValueError as e:
        return _fail(e)
    return {"undone": True, "outline": _outline(ds.analyze(source))}


@mcp.tool()
def describe_contracts(name: str, contract: Optional[str] = None,
                       proposal_id: Optional[str] = None) -> Dict[str, Any]:
    """Read contracts back in plain sentences, for the person to confirm.

    Generated by fixed rules from the design, so it says what the design
    says — use it to check that this is what the person meant. Give a
    proposal_id to read the design as it would be after that pending proposal.
    """
    try:
        described = readback.describe_design(workspace.source_of(name, proposal_id))
    except ValueError as e:
        return _fail(e)
    if contract:
        if contract not in described:
            return _fail(ValueError(f"No contract `{contract}`. Contracts: {', '.join(described)}."))
        described = {contract: described[contract]}
    return {"contracts": described}


@mcp.tool()
def actor_view(name: str, actor: str, proposal_id: Optional[str] = None) -> Dict[str, Any]:
    """What the design means for one actor: its duties, authority, messages and open issues.

    Useful for asking a stakeholder "is this what you agreed to?". Give a
    proposal_id to read the design as it would be after that pending proposal.
    """
    try:
        return readback.actor_view(workspace.source_of(name, proposal_id), actor)
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def lifecycle_stories(name: str, contract: str, proposal_id: Optional[str] = None) -> Dict[str, Any]:
    """Tell the distinct ways one instance of a contract can run, start to end.

    Each story is a short narrative with its outcome. Read them to the
    person and ask whether each one should be possible. Give a
    proposal_id to tell the stories as they would be after that pending
    proposal — do this when the person asks about changes not yet applied.
    """
    try:
        stories = readback.lifecycle_stories(workspace.source_of(name, proposal_id), contract)
    except ValueError as e:
        return _fail(e)
    return {"contract": contract, "stories": [
        {"outcome": s["outcome"], "path": s["path"], "story": s["story"]} for s in stories]}


@mcp.tool()
def step_lifecycle(name: str, contract: str, path: Optional[List[str]] = None,
                   proposal_id: Optional[str] = None) -> Dict[str, Any]:
    """Follow one contract instance step by step.

    `path` is the list of states visited so far (omit it to start). The
    result tells the story so far and lists what can happen next; append
    one of the `to` states to continue. proposal_id reads a pending proposal.
    """
    try:
        return readback.narrate_path(workspace.source_of(name, proposal_id), contract, path or [])
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def explain_verification(name: str) -> Dict[str, Any]:
    """Say in plain words what the verifier concluded about each contract, with counterexamples."""
    try:
        return {"results": readback.explain_verification(workspace.read(name))}
    except ValueError as e:
        return _fail(e)


@mcp.tool()
def get_diagram(name: str, kind: str = "architecture", item: Optional[str] = None) -> Dict[str, Any]:
    """Return a diagram of the design as text.

    kind: "architecture" (actors and contracts, Graphviz DOT), "lifecycle"
    (one contract's state machine, DOT; `item` = contract id), "protocol"
    (one protocol's sequence diagram, SVG; `item` = protocol id) or
    "regimes" (DOT). Items with problems are outlined in red or orange.
    """
    try:
        analysis = ds.analyze(workspace.read(name))
    except ValueError as e:
        return _fail(e)
    if not analysis.parsed:
        return _fail(ValueError(analysis.findings[0].message))
    ir = analysis.ir
    if kind == "architecture":
        return {"format": "dot", "diagram": architecture_to_dot(ir, flags=ds.flagged(analysis))}
    if kind == "regimes":
        marks = {f.element for f in analysis.findings if f.section == "Regimes" and f.element}
        return {"format": "dot", "diagram": regimes_to_dot(ir, flagged=marks)}
    if kind == "lifecycle":
        contracts = {str(c.get("id")): c for c in ir["institution"].get("contracts") or []}
        if item not in contracts:
            return _fail(ValueError(f"Give `item` as one of: {', '.join(contracts) or 'no contracts'}."))
        view = build_lifecycle_view(contracts[item])
        if view is None:
            return _fail(ValueError(f"Contract `{item}` has no lifecycle."))
        marks = {f.element for f in analysis.findings
                 if f.stage == "Lifecycle" and f.item == item and f.element}
        return {"format": "dot", "diagram": lifecycle_to_dot(view, flagged=marks)}
    if kind == "protocol":
        protocols = {str(p.get("id")): p for p in (ir.get("protocol") or {}).get("protocols") or []}
        if item not in protocols:
            return _fail(ValueError(f"Give `item` as one of: {', '.join(protocols) or 'no protocols'}."))
        marks = {f.element for f in analysis.findings
                 if f.section == "Protocols" and f.item == item and f.element}
        return {"format": "svg", "diagram": sequence_svg(protocols[item], flagged=marks)}
    return _fail(ValueError("kind must be architecture, lifecycle, protocol or regimes."))


@mcp.tool()
def export_design(name: str, what: str = "report") -> Dict[str, Any]:
    """Write an export next to the design and return its path.

    what: "report" (a printable HTML design report) or "ir" (simulator IR as JSON).
    """
    try:
        source = workspace.read(name)
    except ValueError as e:
        return _fail(e)
    analysis = ds.analyze(source)
    if what == "report":
        path = workspace.root() / f"{name}.report.html"
        path.write_text(ds.build_report(source, analysis), encoding="utf-8")
    elif what == "ir":
        if not analysis.parsed:
            return _fail(ValueError(analysis.findings[0].message))
        path = workspace.root() / f"{name}.ir.json"
        path.write_text(ds.export_ir_json(analysis), encoding="utf-8")
    else:
        return _fail(ValueError("what must be report or ir."))
    return {"written": str(path)}


@mcp.prompt()
def design_interview(name: str = "my-design") -> str:
    """Interview the user to design a System of Systems step by step."""
    return (
        f"Help me design a System of Systems as the CADL design `{name}`.\n\n"
        "Create it if it does not exist. Then work as an interviewer: call open_questions, "
        "ask me the one or two most important questions in plain language, and turn my answers "
        "into a small propose_changes. Show me what would change and any problems it "
        "introduces before applying it, and apply only when I agree.\n\n"
        "Every few steps, read the design back to me with describe_contracts, actor_view or "
        "lifecycle_stories and ask whether that is what I meant. Do not fill gaps with your "
        "own assumptions; ask me. Passing checks means the design is consistent, not that it "
        "is right — say so."
    )


@mcp.prompt()
def draft_from_document(name: str = "my-design") -> str:
    """Draft a design from a requirements document, SLA or similar text the user provides."""
    return (
        f"I will give you a document. Draft the CADL design `{name}` from it.\n\n"
        "1. List the actors and agreements you find, quoting the sentence each one comes from. "
        "Ask me to confirm the list before changing anything.\n"
        "2. Build the design with propose_changes in small proposals: actors first, then one "
        "contract at a time. In each proposal's rationale, quote the source sentence.\n"
        "3. Where the document is silent or ambiguous, do not guess: note it and ask me, "
        "using open_questions as a checklist.\n"
        "4. Finish by reading the contracts back with describe_contracts and listing what in "
        "the design is not stated in the document."
    )


if __name__ == "__main__":
    mcp.run()
