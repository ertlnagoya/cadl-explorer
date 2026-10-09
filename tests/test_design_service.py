"""Design service: analysis, form round trips, comparison and exports."""
import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

pytest.importorskip("cadl")

from backend.plotting.architecture import architecture_to_dot
from backend.services import design_service as ds
from backend.services.cadl_service import parse_cadl_yaml

ALL_SOURCES = [ds.load_example(name) for name in ds.list_examples()] + [ds.NEW_DESIGN]


def _levels(analysis, stage):
    return [f.level for f in analysis.findings if f.stage == stage]


def test_examples_are_bundled():
    assert len(ds.list_examples()) >= 3


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_bundled_designs_pass(source):
    analysis = ds.analyze(source)
    assert analysis.ok
    assert analysis.count("error") == 0
    assert analysis.ir["institution"]["actors"]


@pytest.mark.parametrize("source", ["", "sos: [", "foo: 1", "- a\n- b\n"])
def test_unparseable_source_is_a_finding_not_an_exception(source):
    analysis = ds.analyze(source)
    assert not analysis.parsed and not analysis.ok
    assert _levels(analysis, "Parse") == ["error"]


def test_undefined_party_is_reported():
    source = ds.NEW_DESIGN.replace('- "WORKER[*]"', '- "GHOST[*]"')
    analysis = ds.analyze(source)
    assert not analysis.ok
    assert any("GHOST" in f.title for f in analysis.findings if f.level == "error")


def test_input_limits():
    assert not ds.analyze("sos:\n  name: x\n" + "#" * (ds.MAX_SOURCE_CHARS + 1)).parsed
    assert not ds.analyze("a: &x 1\nsos:\n  name: *x\n").parsed  # anchors / aliases
    many = ds.load_doc(ds.NEW_DESIGN)
    contract = many["sos"]["contracts"][0]
    many["sos"]["contracts"] = [dict(contract, id=f"C{i}") for i in range(ds.MAX_CONTRACTS + 1)]
    assert "Too many contracts" in ds.analyze(ds.dump_doc(many)).findings[0].message


def test_lifecycle_checks():
    good = ds.analyze(ds.NEW_DESIGN)
    assert _levels(good, "Lifecycle") == ["pass"]

    bad = ds.analyze(
        ds.NEW_DESIGN
        .replace("initial: Proposed", "initial: Nowhere")
        .replace("to: Completed", "to: Ghost")
    )
    messages = [f.message for f in bad.findings if f.stage == "Lifecycle" and f.level == "error"]
    assert any("Nowhere" in m for m in messages)
    assert any("Ghost" in m for m in messages)
    assert not bad.ok

    orphan = ds.analyze(ds.NEW_DESIGN.replace(
        "states: [Proposed, Active, Completed, Violated]",
        "states: [Proposed, Active, Completed, Violated, Orphan]"))
    warnings = [f.message for f in orphan.findings if f.stage == "Lifecycle" and f.level == "warning"]
    assert any("Orphan" in m and "cannot be reached" in m for m in warnings)
    assert orphan.ok  # warnings do not fail the design


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_form_round_trip_keeps_the_design(source):
    """Reading every table and writing it back must not change the IR."""
    doc = ds.load_doc(source)
    sos = doc["sos"]
    ds.rows_to_actors(sos, ds.actors_to_rows(sos))
    for contract in sos.get("contracts") or []:
        if contract.get("lifecycle"):
            ds.rows_to_transitions(contract["lifecycle"], ds.transitions_to_rows(contract["lifecycle"]))
        ds.rows_to_monitors(contract, ds.monitors_to_rows(contract))
    assert ds.analyze(ds.dump_doc(doc)).ir == ds.analyze(source).ir


def test_actor_rows_edit():
    doc = ds.load_doc(ds.NEW_DESIGN)
    sos = doc["sos"]
    rows = ds.actors_to_rows(sos)
    rows[0]["role"] = "boss"
    rows.append({"id": "AUDITOR", "role": "auditor", "autonomy": "medium",
                 "capabilities": "audit, report", "input": None, "output": ""})
    rows.append({"id": "  ", "role": "ignored"})  # blank id rows are dropped
    ds.rows_to_actors(sos, rows)
    by_id = {a["id"]: a for a in sos["actors"]}
    assert by_id["COORDINATOR"]["role"] == "boss"
    assert by_id["AUDITOR"]["capabilities"] == ["audit", "report"]
    assert "interface" not in by_id["AUDITOR"]
    assert len(sos["actors"]) == 3
    assert ds.analyze(ds.dump_doc(doc)).ok


def test_transition_rows_edit():
    doc = ds.load_doc(ds.NEW_DESIGN)
    lifecycle = doc["sos"]["contracts"][0]["lifecycle"]
    rows = ds.transitions_to_rows(lifecycle)
    rows.append({"id": "abort", "from": "Proposed, Active", "to": "Violated", "on": "abort_requested"})
    ds.rows_to_transitions(lifecycle, rows)
    added = lifecycle["transitions"][-1]
    assert added["from"] == ["Proposed", "Active"] and "on_violation" not in added
    # A single source state stays a scalar, as authors write it.
    assert lifecycle["transitions"][0]["from"] == "Proposed"
    assert ds.analyze(ds.dump_doc(doc)).ok


def test_new_contract_is_valid_and_unique():
    doc = ds.load_doc(ds.NEW_DESIGN)
    sos = doc["sos"]
    first = ds.new_contract(sos)
    sos["contracts"].append(first)
    second = ds.new_contract(sos)
    sos["contracts"].append(second)
    assert first["id"] != second["id"]
    assert first["parties"] == ["COORDINATOR", "WORKER[*]"]
    assert ds.analyze(ds.dump_doc(doc)).ok


def test_diff_designs():
    base = ds.analyze(ds.NEW_DESIGN)
    assert not any(ds.diff_designs(base.ir, base.ir).values())

    doc = ds.load_doc(ds.NEW_DESIGN)
    sos = doc["sos"]
    sos["actors"].append({"id": "AUDITOR", "role": "auditor", "autonomy": "medium"})
    sos["contracts"][0]["authority"]["beta"] = 0.9
    sos["contracts"][0]["lifecycle"]["transitions"].pop()
    changed = ds.analyze(ds.dump_doc(doc))
    diff = ds.diff_designs(base.ir, changed.ir)
    assert "added actor AUDITOR" in diff["Actors"]
    assert any("governance.beta: 0.7 → 0.9" in c for c in diff["Contracts"])
    assert any("removed transition finish" in c for c in diff["Lifecycle"])
    assert ds.source_diff(ds.NEW_DESIGN, ds.dump_doc(doc)).startswith("---")


@pytest.mark.parametrize("source", ALL_SOURCES)
def test_to_explorer_yaml_is_accepted_by_the_explorer(source):
    config = parse_cadl_yaml(ds.to_explorer_yaml(source))
    assert config.sos_type in ("directed", "collaborative")
    assert 0.0 <= config.alpha <= 1.0


def test_to_explorer_yaml_maps_directed():
    directed = ds.NEW_DESIGN.replace("type: Acknowledged", "type: Directed")
    assert parse_cadl_yaml(ds.to_explorer_yaml(directed)).sos_type == "directed"
    assert parse_cadl_yaml(ds.to_explorer_yaml(ds.NEW_DESIGN)).sos_type == "collaborative"


def test_exports():
    source = ds.load_example(next(iter(ds.list_examples())))
    for target in ds.SIM_TARGETS:
        assert ds.export_sim_config(source, target).strip()
    for target in ds.CODEGEN_TARGETS:
        names = zipfile.ZipFile(io.BytesIO(ds.export_codegen_zip(source, target))).namelist()
        assert names and all(not n.startswith("/") and ".." not in n for n in names)
    assert ds.iec62853_report(source)["sos_name"]
    assert ds.regime_map(ds.NEW_DESIGN) is None  # no top-level transitions


def test_architecture_dot():
    ir = ds.analyze(ds.NEW_DESIGN).ir
    dot = architecture_to_dot(ir)
    assert '"actor:COORDINATOR" -> "contract:SERVICE_AGREEMENT" [label="decides"' in dot
    assert '"actor:WORKER" -> "contract:SERVICE_AGREEMENT" [dir=none]' in dot
    assert "β 0.7" in dot and "4 states" in dot
    assert architecture_to_dot(ir, dark=True) != dot

    undefined = ds.analyze(ds.NEW_DESIGN.replace('- "WORKER[*]"', '- "GHOST[*]"')).ir
    assert "(undefined)" in architecture_to_dot(undefined)


FULL = ds.load_example("a sos robot delivery")


def test_protocol_steps_text_round_trip():
    plain = ["A -> B : msg", "B : work"]
    text, structured = ds.steps_to_text(plain)
    assert not structured and ds.text_to_steps(text + "\n\n", structured) == plain

    nested = ["A -> B : msg", {"if x > 1": ["B : work"]}]
    text, structured = ds.steps_to_text(nested)
    assert structured and ds.text_to_steps(text, structured) == nested
    with pytest.raises(ValueError):
        ds.text_to_steps("not: a list", True)


def test_new_protocol_is_valid():
    doc = ds.load_doc(ds.NEW_DESIGN)
    protocol = ds.new_protocol(doc["sos"])
    doc["sos"]["protocols"] = [protocol]
    analysis = ds.analyze(ds.dump_doc(doc))
    assert analysis.ok
    steps = analysis.ir["protocol"]["protocols"][0]["steps"]
    assert [s["type"] for s in steps] == ["message", "compute"]


def test_mapping_and_record_helpers():
    env = {"grid_size": 30, "ratio": 0.5, "name": "unity-mcp", "debug": True}
    assert ds.rows_to_mapping(ds.mapping_to_rows(env), typed=True) == env
    assert ds.rows_to_mapping([{"name": " ", "value": "x"}, {"name": "a", "value": None}]) == {"a": ""}

    previous = [{"id": "m1", "formula": "a / b", "target": "<= 1", "unit": "s"}]
    rows = [{"id": "m1", "formula": "a / c", "target": ""}, {"id": "", "formula": "dropped"},
            {"id": "m2", "formula": "x", "target": ">= 2"}]
    records = ds.rows_to_records(previous, rows, ["id", "formula", "target"], ["id"])
    assert records == [{"id": "m1", "formula": "a / c", "unit": "s"},
                       {"id": "m2", "formula": "x", "target": ">= 2"}]


def test_all_sections_round_trip_on_the_full_example():
    doc = ds.load_doc(FULL)
    sos = doc["sos"]
    ds.rows_to_algorithms(sos, ds.algorithms_to_rows(sos))
    regime_cols = ["from", "to", "condition", "protocol", "safety_invariant"]
    sos["transitions"] = ds.rows_to_records(
        sos["transitions"], ds.records_to_rows(sos["transitions"], regime_cols),
        regime_cols, ["from", "to"], ["from", "to"])
    metric_cols = ["id", "formula", "target"]
    sos["metrics"] = ds.rows_to_records(
        sos["metrics"], ds.records_to_rows(sos["metrics"], metric_cols), metric_cols, ["id"])
    for protocol in sos["protocols"]:
        protocol["steps"] = ds.text_to_steps(*ds.steps_to_text(protocol["steps"]))
        protocol["timing"] = ds.rows_to_mapping(ds.mapping_to_rows(protocol.get("timing")))
    env = sos["context"]["environment"]
    sos["context"]["environment"] = ds.rows_to_mapping(ds.mapping_to_rows(env), typed=True)
    assert ds.analyze(ds.dump_doc(doc)).ir == ds.analyze(FULL).ir


def test_diff_covers_protocols_algorithms_regimes_metrics():
    base = ds.analyze(FULL)
    doc = ds.load_doc(FULL)
    sos = doc["sos"]
    sos["protocols"][0]["steps"].append("DISPATCHER : log")
    sos["protocols"].append(ds.new_protocol(sos))
    sos["algorithms"]["pathfinding"]["central"] = "CBS"
    sos["transitions"].pop()
    sos["metrics"].append({"id": "energy", "formula": "sum(energy)", "target": "<= 100"})
    diff = ds.diff_designs(base.ir, ds.analyze(ds.dump_doc(doc)).ir)
    assert any("steps changed (5 → 6)" in c for c in diff["Protocols"])
    assert "added protocol PROTOCOL_1" in diff["Protocols"]
    assert any("central: ECBS → CBS" in c for c in diff["Algorithms"])
    assert any(c.startswith("removed transition") for c in diff["Regimes"])
    assert "added metric energy" in diff["Metrics"]


def test_sequence_and_regime_diagrams():
    import xml.dom.minidom

    from backend.plotting.architecture import regimes_to_dot
    from backend.plotting.sequence import participants, sequence_svg

    ir = ds.analyze(FULL).ir
    protocol = ir["protocol"]["protocols"][0]
    assert participants(protocol) == ["CUSTOMER", "DISPATCHER", "ROBOT"]
    svg = sequence_svg(protocol)
    xml.dom.minidom.parseString(svg)
    assert "1. delivery_request" in svg and "2. assign_tasks" in svg
    assert sequence_svg(protocol, dark=True) != svg

    # Markers and text that needs escaping must still give well-formed SVG.
    odd = {"steps": [{"type": "condition", "content": "if", "condition": "x < 1 & y > 2"},
                     {"type": "barrier", "content": "all_done"}]}
    xml.dom.minidom.parseString(sequence_svg(odd))
    xml.dom.minidom.parseString(sequence_svg({"steps": []}))

    dot = regimes_to_dot(ir)
    assert '"NORMAL" -> "CONGESTED"' in dot and "via TASK_DISPATCH" in dot
    assert ds.export_ir_yaml(ds.analyze(FULL)).startswith("name:")


def _problems(analysis, stage):
    return [f for f in analysis.findings if f.stage == stage and f.level in ("error", "warning")]


def test_malformed_expressions_are_reported():
    doc = ds.load_doc(ds.NEW_DESIGN)
    contract = doc["sos"]["contracts"][0]
    contract["guarantee"] = ["x >", "completion_time <= 300s"]
    contract["assume"] = ["(a"]
    doc["sos"]["protocols"] = [{"id": "P", "trigger": "go(", "steps": ["COORDINATOR : work"]}]
    problems = _problems(ds.analyze(ds.dump_doc(doc)), "Expressions")
    titles = [f.title for f in problems]
    assert "Contract 'SERVICE_AGREEMENT' guarantee is not a valid expression" in titles
    assert "Contract 'SERVICE_AGREEMENT' assume is not a valid expression" in titles
    assert "Protocol 'P' trigger is not a valid expression" in titles
    assert "verification does not take it into account" in problems[0].message
    assert (problems[0].section, problems[0].item) == ("Contracts", "SERVICE_AGREEMENT")
    assert not _problems(ds.analyze(ds.NEW_DESIGN), "Expressions")


def test_interface_and_value_checks():
    doc = ds.load_doc(ds.NEW_DESIGN)
    sos = doc["sos"]
    sos["actors"][0]["autonomy"] = "extreme"
    sos["protocols"] = [{"id": "P", "trigger": "t", "steps": [
        "COORDINATOR -> WORKER[i] : task_assignment", "WORKER[i] -> COORDINATOR : surprise"]}]
    analysis = ds.analyze(ds.dump_doc(doc))
    problems = _problems(analysis, "Interfaces")
    assert any(f.level == "error" and "unknown autonomy" in f.title for f in problems)
    undeclared = [f for f in problems if "does not declare" in f.title]
    assert len(undeclared) == 2 and all("surprise" in f.message for f in undeclared)
    assert not analysis.ok


def test_consistency_checks():
    doc = ds.load_doc(ds.NEW_DESIGN)
    sos = doc["sos"]
    sos["actors"].append({"id": "LONER", "role": "observer", "autonomy": "low"})
    sos["contracts"][0]["authority"]["decision_holder"] = "LONER"
    sos["contracts"][0]["guarantee"] = ["completion_time <= 3s"]
    sos["protocols"] = [{
        "id": "P", "trigger": "t", "steps": ["COORDINATOR -> WORKER[i] : task_assignment"],
        "timing": {"max_total": "5s", "max_planning": "9s"},
    }]
    titles = [f.title for f in _problems(ds.analyze(ds.dump_doc(doc)), "Consistency")]
    assert "Actor 'LONER' is not a party to any contract" in titles
    assert "Contract 'SERVICE_AGREEMENT' decision holder is not a party" in titles
    assert "Protocol 'P' may outlast a guarantee of contract 'SERVICE_AGREEMENT'" in titles
    assert "Protocol 'P' has inconsistent time bounds" in titles
    assert ds.duration_ms("200ms") == 200 and ds.duration_ms("5s") == 5000
    assert ds.duration_ms("soon") is None


def test_contradiction_names_the_conflicting_pair():
    source = ds.NEW_DESIGN.replace(
        '- "completion_time <= 300s"',
        '- "completion_time <= 300s"\n        - "completion_time > 400s"')
    errors = [f for f in ds.analyze(source).findings if f.level == "error"]
    assert len(errors) == 1
    assert "`completion_time <= 300s` and `completion_time > 400s`" in errors[0].message


def test_findings_are_located_and_not_repeated():
    doc = ds.load_doc(ds.NEW_DESIGN)
    doc["sos"]["contracts"][0]["parties"] = ["COORDINATOR", "GHOST[*]"]
    analysis = ds.analyze(ds.dump_doc(doc))
    ghost = [f for f in analysis.findings if "GHOST" in f.title + f.message]
    assert len(ghost) == 1  # the IR validator's repeat is dropped
    assert (ghost[0].section, ghost[0].item) == ("Contracts", "SERVICE_AGREEMENT")
    flags = ds.flagged(analysis)
    assert flags["Contracts"] == {"SERVICE_AGREEMENT": "error"}
    assert flags["Actors"] == {"WORKER": "warning"}  # no longer in any contract

    dot = architecture_to_dot(analysis.ir, flags=flags)
    assert 'penwidth=3' in dot


def test_yaml_errors_give_line_and_gutter_marker():
    analysis = ds.analyze("sos:\n  name: x\n type: Directed\n")
    message = analysis.findings[0].message
    assert message.startswith("Line 3, column 2:") and "3 |  type: Directed" in message
    assert analysis.annotations == [
        {"row": 2, "column": 1, "type": "error", "text": analysis.annotations[0]["text"]}]


def test_every_stage_has_a_scope():
    assert set(ds.STAGE_SCOPE) == set(ds.STAGES)
    stages = {f.stage for f in ds.analyze(FULL).findings}
    assert stages <= set(ds.STAGES)


def test_lifecycle_moves():
    contract = ds.analyze(ds.load_example("sos dsl robot delivery")).ir["institution"]["contracts"][0]
    moves = ds.lifecycle_moves(contract, "Assigned")
    kinds = {(m.kind, m.target) for m in moves}
    assert ("transition", "Accepted") in kinds
    assert ("deadline", "Violated") in kinds
    assert ("monitor", "Violated") in kinds
    assert "5s" in next(m.detail for m in moves if m.kind == "deadline")
    assert ds.lifecycle_moves(contract, "Completed") == []


def test_contract_templates_and_lifecycle_presets_are_valid():
    doc = ds.load_doc(ds.NEW_DESIGN)
    sos = doc["sos"]
    for template in ds.CONTRACT_TEMPLATES:
        sos["contracts"].append(ds.contract_from_template(sos, template))
    ids = [c["id"] for c in sos["contracts"]]
    assert len(set(ids)) == len(ids)
    for preset in ds.LIFECYCLE_PRESETS.values():
        sos["contracts"][0]["lifecycle"] = preset
        analysis = ds.analyze(ds.dump_doc(doc))
        assert analysis.ok
        assert not [f for f in analysis.findings if f.level == "warning"]


def test_lifecycle_dot_marks_a_trace():
    from cadl_sim.sos_dsl import build_lifecycle_view, lifecycle_to_dot

    contract = ds.analyze(ds.NEW_DESIGN).ir["institution"]["contracts"][0]
    view = build_lifecycle_view(contract)
    plain = lifecycle_to_dot(view)
    traced = lifecycle_to_dot(view, current="Active", visited=["Proposed", "Active"],
                              flagged=["Violated"])
    assert plain != traced
    assert traced.count("penwidth") == 3


def test_report_is_self_contained_html():
    for source in (FULL, ds.NEW_DESIGN, "sos: ["):
        analysis = ds.analyze(source)
        report = ds.build_report(source, analysis, "2026-01-01 00:00")
        assert report.startswith("<!doctype html>") and report.rstrip().endswith("</html>")
    full = ds.build_report(FULL, ds.analyze(FULL))
    assert "TASK_DISPATCH" in full and full.count("<svg") == 3
    assert "What the checks cover" in full and 'id="graph0"' in full
    # Source text is escaped, and embedded DOT cannot close the script element.
    hostile = ds.NEW_DESIGN.replace('"Describe the system of systems here."',
                                    '"</script><script>alert(1)</script>"')
    report = ds.build_report(hostile, ds.analyze(hostile))
    assert "<script>alert(1)" not in report


def test_ai_draft_is_gated(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert ds.ai_available()
    with pytest.raises(ValueError):
        ds.draft_from_description("a warehouse with robots")


def test_metrics_are_checked_against_contracts():
    doc = ds.load_doc(FULL)
    sos = doc["sos"]
    sos["contracts"][1]["guarantee"] = ["makespan <= 300s", "throughput >= 5", "collision_rate == 0"]
    sos["metrics"][3]["target"] = "<= 2"                      # throughput
    sos["metrics"].append({"id": "energy", "formula": "sum(e)"})
    found = {f.title: f for f in ds.analyze(ds.dump_doc(doc)).findings if f.stage == "Consistency"}
    weaker = found["Metric 'makespan' target is weaker than contract 'FLEET_SAFETY'"]
    assert weaker.level == "warning" and "<= 600s" in weaker.message
    assert (weaker.section, weaker.item) == ("Metrics", "makespan")
    assert found["Metric 'throughput' target contradicts contract 'FLEET_SAFETY'"].level == "warning"
    assert found["Metric 'energy' has no target"].level == "info"
    # collision_rate == 0 against a metric target of == 0 is consistent.
    assert not any("collision_rate" in title for title in found)
    unmeasured = found["Contract 'DELIVERY_SLA' guarantees quantities no metric measures"]
    assert unmeasured.level == "info" and "delivery_time" in unmeasured.message


def test_diagrams_mark_flagged_messages_and_regimes():
    from backend.plotting.architecture import regimes_to_dot
    from backend.plotting.sequence import sequence_svg

    doc = ds.load_doc(FULL)
    doc["sos"]["transitions"].append(
        {"from": "EMERGENCY", "to": "HALT", "condition": "x >", "protocol": "NOPE"})
    analysis = ds.analyze(ds.dump_doc(doc))

    protocol = analysis.ir["protocol"]["protocols"][0]
    messages = {f.element for f in analysis.findings
                if f.section == "Protocols" and f.item == protocol["id"] and f.element}
    assert messages == {"delivery_request", "delivery_notification"}
    assert sequence_svg(protocol, flagged=messages).count("url(#seq-arrow-bad)") == 2
    assert "url(#seq-arrow-bad)" not in sequence_svg(protocol)

    regimes = {f.element for f in analysis.findings if f.section == "Regimes" and f.element}
    assert {"HALT", "NOPE", "EMERGENCY → HALT"} <= regimes
    assert regimes_to_dot(analysis.ir, flagged=regimes).count("penwidth") == 2
    assert "penwidth" not in regimes_to_dot(analysis.ir)

    views = {ds.view_of(f)[0] for f in analysis.findings
             if f.level == "warning" and ds.view_of(f)}
    assert views == {"Protocols", "Regimes"}


def test_unclosed_bracket_gives_a_readable_error_and_marker():
    broken = ds.load_example("sos dsl robot delivery").replace("type: Acknowledged", "type: [oops")
    analysis = ds.analyze(broken)
    message = analysis.findings[0].message
    assert message.startswith("Line ") and "<unicode string>" not in message
    assert "unclosed" in message
    assert analysis.annotations and analysis.annotations[0]["type"] == "error"


def test_the_on_key_survives_forms_and_rewrites():
    """YAML 1.1 reads `on:` as the boolean True; forms must still see and keep it."""
    doc = ds.load_doc(ds.NEW_DESIGN)
    lifecycle = doc["sos"]["contracts"][0]["lifecycle"]
    assert lifecycle["transitions"][0]["on"] == "COORDINATOR -> WORKER[i] : task_assignment"
    rows = ds.transitions_to_rows(lifecycle)
    assert rows[0]["on"] == "COORDINATOR -> WORKER[i] : task_assignment"

    rows[0]["on"] = "COORDINATOR -> WORKER[i] : go"
    ds.rows_to_transitions(lifecycle, rows)
    text = ds.dump_doc(doc)
    assert "true:" not in text and "'on':" not in text
    assert "on: 'COORDINATOR -> WORKER[i] : go'" in text
    assert ds.analyze(text).ir["institution"]["contracts"][0]["lifecycle"]["transitions"][0]["on"] \
        == "COORDINATOR -> WORKER[i] : go"


def test_a_design_without_a_type_is_reported_not_crashed():
    analysis = ds.analyze("sos:\n  name: X\n")
    assert analysis.parsed and analysis.sos_type == ""
    assert any(f.title == "The SoS has no type" for f in analysis.findings)
    assert ds.build_report("sos:\n  name: X\n", analysis).startswith("<!doctype html>")


def test_lifecycle_events_naming_undefined_actors_are_reported():
    doc = ds.load_doc(ds.NEW_DESIGN)
    transitions = doc["sos"]["contracts"][0]["lifecycle"]["transitions"]
    transitions[0]["on"] = "BOSS -> WORKER[i] : go"
    transitions[1]["on"] = "DRONE[i].status == Done"
    messages = [f.message for f in ds.analyze(ds.dump_doc(doc)).findings
                if "names an undefined actor" in f.title]
    assert len(messages) == 2 and "`BOSS`" in messages[0] and "`DRONE`" in messages[1]
    for name in ds.list_examples():
        assert not [f for f in ds.analyze(ds.load_example(name)).findings
                    if "names an undefined actor" in f.title]
