"""The parts added in v1.0.0: background jobs, the record of the checks, lines of
findings, forms that keep comments, projects, traces, the agent boundary, the
editor's names and snippets, the workspace in git, and the diagram of a change."""

import io
import os
import shutil
import threading
import time
import zipfile

import pytest
import yaml

from tests.conftest import AppTest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TRACES = os.path.join(ROOT, "designs", "traces")


def _trace(name):
    with open(os.path.join(TRACES, name), encoding="utf-8") as f:
        return f.read()


# ── Jobs in the background ──────────────────────────────────────────

def test_a_job_runs_in_the_background_and_stops_when_cancelled(monkeypatch):
    from backend.services import design_jobs as jobs
    monkeypatch.delenv("CADL_JOBS_SYNC", raising=False)
    started = threading.Event()

    def slow(x, cancel=None):
        started.set()
        cancel.wait(10)
        return {"failure": "stopped" if cancel.is_set() else None, "transient": cancel.is_set()}

    job = jobs.start("slow", ("a",), slow, "a")
    assert started.wait(5) and job.running
    assert jobs.start("slow", ("a",), slow, "a") is job          # the same run, not a second one
    jobs.cancel(job.id)
    for _ in range(100):
        if not job.running:
            break
        time.sleep(0.05)
    assert not job.running and job.result["failure"] == "stopped"
    again = jobs.start("slow", ("a",), lambda x, cancel=None: {"ok": True}, "a")
    assert again is not job                                     # a cancelled run is run again
    for _ in range(100):
        if not again.running:
            break
        time.sleep(0.05)
    assert again.result == {"ok": True}
    assert jobs.start("slow", ("a",), slow, "a") is again       # a finished run is kept


def test_a_job_reports_an_exception(monkeypatch):
    from backend.services import design_jobs as jobs

    def broken(cancel=None):
        raise RuntimeError("broken")

    job = jobs.start("broken", (), broken)
    assert not job.running and job.error == "broken"


def test_a_running_cadl_command_can_be_cancelled(monkeypatch):
    pytest.importorskip("cadl")
    from backend.services import design_service as ds
    monkeypatch.setattr(ds, "_CHILD", "import time\ntime.sleep(30)\n")
    cancel = threading.Event()
    threading.Timer(0.5, cancel.set).start()
    begun = time.monotonic()
    run = ds._run_cadl(["check"], ds.NEW_DESIGN, 30, cancel=cancel)
    assert time.monotonic() - begun < 10
    assert run["failure"] == "`cadl check` was stopped before it finished; nothing is claimed."
    assert run["transient"]


# ── Lines of findings, merged forms ─────────────────────────────────

def test_findings_name_their_line_and_mark_the_editor():
    pytest.importorskip("cadl")
    from backend.services import design_service as ds
    source = ds.load_example("sos dsl robot delivery").replace("- id: DISPATCHER", "- id: DISPATCHR", 1)
    analysis = ds.analyze(source)
    unused = next(f for f in analysis.findings if "DISPATCHR" in f.title)
    assert source.splitlines()[unused.line - 1].strip() == "- id: DISPATCHR"
    assert any(a["row"] == unused.line - 1 for a in analysis.annotations)
    assert ds.find_line(source, "NOBODY") == 0


def test_a_parse_error_names_its_line():
    pytest.importorskip("cadl")
    from backend.services import design_service as ds
    analysis = ds.analyze("sos:\n  name: x\n  actors: [a\n")
    assert analysis.findings[0].level == "error" and analysis.findings[0].line >= 3


def test_forms_keep_comments_and_unchanged_lines():
    pytest.importorskip("ruamel.yaml")
    from backend.services import design_service as ds
    for name in ds.list_examples():
        source = ds.load_example(name)
        assert ds.merge_doc(source, ds.load_doc(source)) == source, name
    source = ds.load_example("robot town")
    doc = ds.load_doc(source)
    doc["sos"]["actors"].append({"id": "NEW", "role": "x", "autonomy": "low"})
    merged = ds.merge_doc(source, doc)
    assert ds.load_doc(merged) == doc
    assert merged.count("# @amend:") == source.count("# @amend:")
    import difflib
    added = [d for d in difflib.ndiff(source.splitlines(), merged.splitlines()) if d.startswith("+ ")]
    assert added == ["+     - id: NEW", "+       role: x", "+       autonomy: low"]


# ── Projects ────────────────────────────────────────────────────────

def test_a_project_comes_back_as_it_was_saved():
    from backend.services import design_project as project
    versions = [{"name": "v1 / first", "time": "10:00:00", "source": "sos:\n  name: a\n"}]
    decisions = [{"time": "10:01:00", "change": "x: 1 → 2", "layer": "rule", "reason": "faster"}]
    runs = [{"check": "game-check", "result": "passed", "summary": "", "source_sha256": "ab",
             "cadl": "0.3.31", "time": "t", "seconds": 1.0}]
    data = project.build("demo", "sos:\n  name: b\n", versions, decisions, "c: 1\n", "c.yaml",
                         {"A → B : m": {"not provided": "x"}}, runs, "0.3.31")
    assert project.is_project(data)
    opened = project.read(data)
    assert opened["source"] == "sos:\n  name: b\n" and opened["name"] == "demo"
    assert opened["versions"] == versions and opened["decisions"] == decisions
    assert opened["constitution"] == "c: 1\n" and opened["constitution_name"] == "c.yaml"
    assert opened["uca"] == {"A → B : m": {"not provided": "x"}} and opened["runs"] == runs
    assert "faster" in zipfile.ZipFile(io.BytesIO(data)).read("decisions.md").decode()


def test_a_zip_that_is_no_project_is_refused():
    from backend.services import design_project as project
    from backend.services.cadl_service import MAX_SOURCE_CHARS
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr("readme.txt", "hello")
    with pytest.raises(project.ProjectError, match="not a project"):
        project.read(buffer.getvalue())
    big = project.build("x", "#" * (MAX_SOURCE_CHARS + 1), [], [], None, None, {}, [], None)
    with pytest.raises(project.ProjectError, match="the limit is"):
        project.read(big)
    with pytest.raises(project.ProjectError, match="not a zip"):
        project.read(b"PK\x03\x04 broken")


# ── Traces and the agent boundary ───────────────────────────────────

def test_a_trace_must_be_json_lines():
    from backend.services import design_runtime as runtime
    assert runtime.read_trace('{"t": 0}\n\n{"t": 1}\n') == [{"t": 0}, {"t": 1}]
    for bad, why in (("", "no records"), ('{"t": 0}\nnot json\n', "Line 2 is not JSON"),
                     ("[1, 2]\n", "not a JSON object")):
        with pytest.raises(runtime.TraceError, match=why):
            runtime.read_trace(bad)


def test_norms_eval_gives_the_verdicts_of_a_trace():
    pytest.importorskip("cadl.norms_eval")
    from backend.services import design_runtime as runtime
    from backend.services import design_service as ds
    out = runtime.norms_eval(ds.load_example("norms robot delivery"),
                             _trace("norms_robot_delivery_c_violated.jsonl"))
    assert out["failure"] is None
    verdicts = {n["id"]: n["verdict"] for n in out["report"]["norms"]}
    assert verdicts["acknowledge"] == "violated" and verdicts["no_low_battery_driving"] == "fulfilled"
    assert out["report"]["trace"] == "the trace"


def test_agents_run_decides_the_messages_and_its_log_verifies():
    pytest.importorskip("cadl.norms_eval")
    from backend.services import design_runtime as runtime
    from backend.services import design_service as ds
    source = ds.load_example("agents tool governance")
    out = runtime.agents_run(source, _trace("agents_tool_governance_b_rejected.jsonl"))
    assert out["failure"] is None
    assert out["report"]["summary"]["escalate"] == 2 and out["report"]["summary"]["holds"]["rejected"] == 1
    assert runtime.audit_verify(source, out["audit"])["valid"]
    other = runtime.audit_verify(source + "\n", out["audit"])
    assert other["valid"] is False and "another CADL file" in other["report"]["error"]["what"]


def test_sim_validate_and_the_toolchain():
    pytest.importorskip("cadl")
    from backend.services import design_runtime as runtime
    from backend.services import design_service as ds
    out = runtime.sim_validate(ds.load_example("sos dsl robot delivery"))
    assert out["ok"] and out["text"].startswith("Validation OK: the design")
    assert runtime.toolchain()["cadl"]


def test_a_trace_becomes_a_sequence_of_messages():
    from backend.plotting.sequence import sequence_svg, trace_protocol
    records = [{"t": 0, "bindings": {"i": 1}, "messages": [{"from": "A", "to": "B[1]", "name": "go"}]},
               {"t": 2}, {"t": 4}, {"t": 6, "end": True}]
    steps = trace_protocol(records, ["deny"])["steps"]
    assert [s["content"] for s in steps] == ["t = 0 · i = 1", "go — deny", "t = 2 … 4: no message",
                                             "t = 6 · end"]
    assert "go — deny" in sequence_svg({"steps": steps})


# ── Names and snippets of the editor ────────────────────────────────

def test_the_editor_offers_the_names_of_the_design():
    pytest.importorskip("cadl")
    from backend.services import design_service as ds
    from backend.services import design_snippets as snippets
    source = ds.load_example("norms robot delivery")
    names = {n["name"]: n["kind"] for n in snippets.names(source, ds.analyze(source).ir)}
    assert names["DISPATCHER"] == "actor" and names["ROBOT[i]"] == "actor"
    assert names["route_assignment"] == "message" and names["acknowledge"] == "id"
    assert names["throughout"] == "key"


@pytest.mark.parametrize("label", [s["label"] for s in __import__(
    "backend.services.design_snippets", fromlist=["snippets"]).snippets()])
def test_every_snippet_is_yaml(label):
    from backend.services import design_snippets as snippets
    snippet = next(s for s in snippets.snippets() if s["label"] == label)
    assert yaml.safe_load(snippet["text"]) is not None


def test_snippets_make_a_design_that_checks():
    """The contract and the norm snippets, put where they say, give a design that parses."""
    pytest.importorskip("cadl")
    from backend.services import design_service as ds
    from backend.services import design_snippets as snippets
    by_label = {s["label"]: s["text"] for s in snippets.snippets()}
    doc = ds.load_doc(ds.NEW_DESIGN)
    doc["sos"]["actors"] += [yaml.safe_load(by_label["Actor"])[0]]
    contract = yaml.safe_load(by_label["Contract"])[0]
    contract.update(yaml.safe_load(by_label["Lifecycle"]))
    contract["norms"] = [yaml.safe_load(by_label[k])[0] for k in by_label if k.startswith("Norm:")]
    doc["sos"]["contracts"].append(contract)
    doc["sos"]["extensions"] = [{"sos-dsl": 0.1}, {"norms": 0.1}]
    analysis = ds.analyze(ds.dump_doc(doc))
    assert analysis.parsed, [f.title for f in analysis.findings if f.level == "error"]


# ── The workspace in git ────────────────────────────────────────────

@pytest.mark.skipif(shutil.which("git") is None, reason="git is not installed")
def test_the_workspace_commits_every_change(tmp_path, monkeypatch):
    from backend.services import design_workspace as workspace
    monkeypatch.setenv("CADL_WORKSPACE", str(tmp_path))
    monkeypatch.setenv("CADL_WORKSPACE_GIT", "1")
    workspace.create("demo", "template")
    workspace.write("demo", workspace.read("demo").replace("MySoS", "Demo"), note="renamed")
    workspace.undo("demo")
    assert [c["message"] for c in workspace.history("demo")] == [
        "demo: undo the last change", "demo: renamed", "demo: edited"]
    monkeypatch.delenv("CADL_WORKSPACE_GIT")
    assert workspace.history("demo") == []


# ── The diagram of a change ─────────────────────────────────────────

def test_the_architecture_of_a_change_marks_what_changed():
    from backend.plotting.architecture import architecture_to_dot, diff_ir
    a = {"institution": {"actors": [{"id": "A"}, {"id": "B"}], "contracts": [{"id": "C", "parties": ["A"]}]}}
    b = {"institution": {"actors": [{"id": "A", "role": "new"}, {"id": "D"}], "contracts": []}}
    union, flags = diff_ir(a, b)
    assert flags["Diff"] == {"Actors:A": "changed", "Actors:D": "added", "Actors:B": "removed",
                             "Contracts:C": "removed"}
    dot = architecture_to_dot(union, flags=flags)
    assert 'xlabel="removed"' in dot and 'xlabel="added"' in dot


# ── The pages ───────────────────────────────────────────────────────

def _designer():
    pytest.importorskip("cadl")
    if AppTest is None:
        pytest.skip("this Streamlit has no AppTest")
    return AppTest.from_file(os.path.join(ROOT, "views", "designer.py"), default_timeout=60).run()


def _set(at, **values):
    for key, value in values.items():
        at.session_state[key] = value
    return at.run()


def _design(at, name):
    from backend.services import design_service as ds
    at.session_state.design_src = ds.load_example(name)
    at.session_state.design_ver += 1
    return at.run()


def test_the_stages_select_their_views():
    at = _designer()
    assert at.session_state.design_stage == "Read"
    at = _set(at, design_view="Export")
    assert at.session_state.design_stage == "Deliver" and not at.exception
    at = _set(at, design_view="Summary")
    assert at.session_state.design_stage == "Verify"
    assert any(d.value["check"].iloc[0] == "cadl check" for d in at.dataframe if "check" in d.value)


def test_one_column_shows_the_editor_in_the_write_stage():
    at = _designer()
    if not hasattr(at, "segmented_control"):
        pytest.skip("this Streamlit's AppTest has no segmented_control")
    at.segmented_control(key="design_layout").set_value("One column").run()
    assert "Write" in at.pills(key="design_stage").options
    at.pills(key="design_stage").set_value("Write").run()
    assert not at.exception
    assert any(b.label == "Undo" for b in at.button)
    assert not any("Every change is checked" in m.value and "Summary" in m.value for m in at.markdown)
    at = _set(at, design_view="Checks", design_stage="Verify")
    assert at.session_state.design_stage == "Verify" and not any(b.label == "Undo" for b in at.button)


def test_the_summary_records_a_check_and_marks_it_when_the_design_changes():
    pytest.importorskip("cadl.norms_check")
    at = _set(_design(_designer(), "games grounding robot delivery"), design_view="Checks")
    at = next(b for b in at.button if b.label == "Run game-check").click().run()
    assert not at.exception
    assert [r["check"] for r in at.session_state.design_runs][-1] == "game-check"
    at = _set(at, design_view="Summary")
    table = next(d.value for d in at.dataframe if "check" in d.value)
    assert "cadl game-check" in list(table["check"])
    assert bool(table[table["check"] == "cadl game-check"]["up to date"].iloc[0])
    at.session_state.design_src = at.session_state.design_src.replace("0.05", "0.06", 1)
    at.session_state.design_ver += 1
    at.run()
    table = next(d.value for d in at.dataframe if "check" in d.value)
    assert not bool(table[table["check"] == "cadl game-check"]["up to date"].iloc[0])
    assert any("older results" in m.value for m in at.markdown)
    at = next(b for b in at.button if "again on the current design" in b.label).click().run()
    assert at.session_state.design_view == "Checks" and not at.exception
    assert [r["check"] for r in at.session_state.design_runs].count("game-check") == 2


def test_the_traces_view_evaluates_an_example_trace():
    pytest.importorskip("cadl.norms_eval")
    at = _set(_design(_designer(), "norms robot delivery"), design_view="Traces",
              design_trace=_trace("norms_robot_delivery_c_violated.jsonl"))
    assert not at.exception and not at.error
    verdicts = next(d.value for d in at.dataframe if "verdict" in d.value)
    assert list(verdicts["norm"])[:2] == ["acknowledge", "report_failure"]
    assert "violated" in verdicts["verdict"].iloc[0]
    assert at.session_state.design_runs[-1]["check"] == "norms-eval"


def test_the_agents_view_replays_an_example_trace():
    pytest.importorskip("cadl.norms_eval")
    at = _set(_design(_designer(), "agents tool governance"), design_view="Agents",
              design_agents_trace=_trace("agents_tool_governance_a_approved.jsonl"))
    assert not at.exception and not at.error
    steps = next(d.value for d in at.dataframe if "decision" in d.value)
    assert "escalate" in list(steps["decision"])
    at = next(b for b in at.button if b.label == "Verify its hash chain").click().run()
    assert any("The audit log verifies" in s.value for s in at.success)


def test_the_views_say_when_a_design_has_no_norms_or_agents():
    at = _set(_designer(), design_view="Traces")
    assert any("declares no norms" in i.value for i in at.info)
    at = _set(at, design_view="Agents")
    assert any("agent governance extension" in i.value for i in at.info)


def test_the_export_validates_and_previews():
    at = _set(_designer(), design_view="Export")
    assert not at.exception
    assert any("`cadl sim-validate`:" in c.value for c in at.caption)
    assert any(b.label == "Open this design in the Explorer" for b in at.button)
    assert at.selectbox(key="design_code_file_python").options


def test_the_sidebar_saves_the_project():
    at = _designer()
    labels = [b.label for b in at.get("download_button")]
    assert "Save the project (.zip)" in labels


def test_the_pages_speak_japanese():
    from views._i18n_ja import JA
    if "Write" not in JA:
        pytest.skip("no translation yet")
    at = _set(_designer(), lang="日本語")
    assert not at.exception
    assert at.title[0].value == JA.get("Designer", "Designer")
    assert any(b.label == JA["Apply"] for b in at.button) or JA.get("Undo") in [b.label for b in at.button]


# ── Findings of the review of v1.0.0 ───────────────────────────────

def _zip(members):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, text in members.items():
            zf.writestr(name, text)
    return buffer.getvalue()


def test_a_project_cannot_list_one_file_many_times_or_hold_odd_contents():
    import json
    from backend.services import design_project as project
    manifest = json.dumps({"format": project.FORMAT, "format_version": 1})
    many = json.dumps([{"name": str(i), "file": "design.cadl"} for i in range(1000)])
    with pytest.raises(project.ProjectError, match="more than 20 versions"):
        project.read(_zip({"manifest.json": manifest, "design.cadl": "sos: {}\n", "versions.json": many}))
    twice = json.dumps([{"name": "a", "file": "design.cadl"}, {"name": "b", "file": "design.cadl"}])
    with pytest.raises(project.ProjectError, match="does not list"):
        project.read(_zip({"manifest.json": manifest, "design.cadl": "sos: {}\n", "versions.json": twice}))
    for members in ({"manifest.json": json.dumps({"format": project.FORMAT, "format_version": "x"}),
                     "design.cadl": "x"},
                    {"manifest.json": manifest, "design.cadl": "x", "versions.json": "5"}):
        with pytest.raises(project.ProjectError):
            project.read(_zip(members))
    runs = json.dumps([{"check": "game-check", "source_sha256": "ab"}, {"check": ["x"], "source_sha256": "c"}])
    opened = project.read(_zip({"manifest.json": manifest, "design.cadl": "x", "runs.json": runs}))
    assert opened["runs"] == [{"check": "game-check", "source_sha256": "ab", "result": "", "summary": "",
                               "time": "", "cadl": None, "seconds": 0.0}]


def test_the_report_escapes_the_record_of_the_checks():
    pytest.importorskip("cadl")
    from backend.services import design_service as ds
    source = ds.load_example("sos dsl robot delivery")
    html = ds.build_report(source, ds.analyze(source), runs=[{
        "check": "game-check", "result": "failed", "summary": "<script>alert(1)</script>",
        "source_sha256": "x", "time": "t", "cadl": "0.3.31"}])
    assert "<script>alert(1)</script>" not in html and "&lt;script&gt;" in html


def test_the_version_of_prism_is_read_from_any_casing(monkeypatch):
    from backend.services import design_runtime as runtime

    class Done:
        stdout = "Version: 4.11\n"

    monkeypatch.setattr(runtime.subprocess, "run", lambda *a, **k: Done())
    import cadl.norms_check
    monkeypatch.setattr(cadl.norms_check, "find_checker", lambda: "/bin/prism")
    runtime.toolchain.cache_clear()
    try:
        assert runtime.toolchain()["prism"] == "4.11"
    finally:
        runtime.toolchain.cache_clear()


def test_a_stopped_run_is_not_taken_again(monkeypatch):
    from backend.services import design_jobs as jobs
    monkeypatch.delenv("CADL_JOBS_SYNC", raising=False)
    gate = threading.Event()

    def slow(cancel=None):
        gate.wait(5)
        return {}

    job = jobs.start("stop", ("k",), slow)
    jobs.cancel(job.id)
    again = jobs.start("stop", ("k",), slow)
    assert again is not job
    gate.set()


def test_reruns_follow_their_run_after_old_runs_are_dropped():
    pytest.importorskip("cadl.norms_check")
    at = _set(_design(_designer(), "games grounding robot delivery"), design_view="Checks")
    at = next(b for b in at.button if b.label == "Run game-check").click().run()
    from views._design import verify
    for _ in range(verify.MAX_RUNS + 3):
        at.session_state.design_src = at.session_state.design_src + "\n# edit\n"
        at.session_state.design_ver += 1
        at.run()
    runs = at.session_state.design_runs
    assert len(runs) == verify.MAX_RUNS
    # the game-check run fell out of the record, and its re-run with it
    assert "game-check" not in [r["check"] for r in runs]
    assert set(at.session_state["design_rerun"]) <= {r["id"] for r in runs}
