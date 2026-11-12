"""The extensions of cadl-lang 0.3.23 to 0.3.25: games 0.1 and grounding 0.1,
quantified and wildcard predicates in norms, `cadl norms-check` and the
norms monitors of the OPA and Solidity targets."""
import copy
import io
import os
import time
import zipfile
from pathlib import Path

import pytest

pytest.importorskip("cadl.norms_check")

from backend.services import design_readback as rb
from backend.services import design_service as ds

GAMES = ds.load_example("games grounding robot delivery")
GROUNDING = ds.load_example("grounding robot delivery")
QUANTIFIED = ds.load_example("norms no double assignment")
CONFLICT = ds.load_example("norms check conflict")
NORMS = ds.load_example("norms robot delivery")
AGENTS = ds.load_example("agents tool governance")

DATA = Path(__file__).parent / "data"
needs_prism = pytest.mark.skipif(not ds.prism_available(), reason="PRISM (prism) is not on PATH")


# ── games 0.1 and grounding 0.1 ─────────────────────────────────────

def test_the_new_examples_have_no_errors():
    for source in (GAMES, GROUNDING, QUANTIFIED, CONFLICT):
        analysis = ds.analyze(source)
        assert analysis.parsed
        assert not [f for f in analysis.findings if f.level == "error"]


def test_extensions_in_effect_include_games_and_grounding():
    assert ds.extensions_in_effect(GAMES) == {"stochastic", "games", "norms", "grounding"}
    assert ds.extensions_in_effect(GROUNDING) == {"norms", "grounding"}
    # games needs stochastic; grounding needs norms (as cadl decides)
    assert "games" not in ds.extensions_in_effect(GAMES.replace("    - stochastic: 0.1\n", ""))
    assert "grounding" not in ds.extensions_in_effect(GROUNDING.replace("    - norms: 0.1\n", ""))


def test_read_back_states_the_strategic_model():
    lines = rb.describe_model(GAMES)
    assert lines[0] == ("The `model:` block is a turn-based stochastic game (games 0.1) between "
                        "R (ROBOT[*]) and D (DISPATCHER).")
    assert "R chooses `careful` or `careless`." in lines
    assert "No player owns `settle`." in lines
    assert "Profile comply (the compliance profile) chooses `careful`." in lines
    assert ("Profile shortcut_first chooses `careless` when `attempts == 0`; otherwise `careful`."
            in lines)
    assert ("The payoff of R is the sum of 10 × fee, -1 × effort and (-2 * lambda) × shortcuts, "
            "accumulated until `settled`.") in lines
    assert any(x.startswith("The weight of shortcuts is minus the magnitude of the sanction of norm "
                            "DELIVERY.no_shortcut") for x in lines)
    assert any(x.startswith("Query comply_by_lambda asks whether profile comply is a Nash "
                            "equilibrium over lambda in [0, 1]") for x in lines)
    # not for a model of Appendix H, nor for a design without one
    assert rb.describe_model(ds.load_example("stochastic robot delivery")) == []
    assert rb.describe_model(NORMS) == []


def test_read_back_states_the_grounding():
    lines = rb.describe_design(GROUNDING)["DELIVERY_SLA"]
    assert ("Its information items (grounding 0.1) are route, from DISPATCHER, status, from "
            "ROBOT[*], battery, from ROBOT[*] and failure, from ROBOT[*].") in lines
    assert any(x.startswith("Pairs of an item and a party other than its origin") for x in lines)
    assert any("a declared 0 or 1 is tested by J-6 instead" in x for x in lines)
    assert any("5 of 8 (α̂ = 5/8 = 0.625)" in x for x in lines)
    assert any("1 of 2 (β̂ = 1/2 = 0.5)" in x for x in lines)
    assert any("1 of 3 (χ = 1/3 = 0.333)" in x for x in lines)
    assert any(x.startswith("Norm report_failure (obligation)") and
               "Its message gives the information `failure` to the receiver." in x for x in lines)
    assert any("the sanction “removed from the dispatch pool for one period” of magnitude 4" in x
               for x in lines)
    rewarded = GROUNDING.replace("          shares: [failure]\n",
                                 "          shares: [failure]\n"
                                 "          on_fulfilment: { reward: \"a bonus\", magnitude: 1 }\n")
    assert rewarded != GROUNDING
    assert any("Fulfilling it brings the reward “a bonus” of magnitude 1." in x
               for x in rb.describe_design(rewarded)["DELIVERY_SLA"])
    # the read-back of a design without grounding is unchanged
    assert not any("grounding" in x or "α̂" in x for x in rb.describe_design(NORMS)["DELIVERY_SLA"])


def test_norms_indices():
    result = ds.norms_indices(GROUNDING)
    assert result["text"].startswith("Derived indices of the design (grounding 0.1, J.4)")
    assert "alpha^ (information sharing) = 5/8 = 0.625" in result["text"]
    contract = result["report"]["contracts"][0]
    assert contract["alpha"]["exact"] == "5/8"
    assert "(J-7)" in result["text"]
    # the indices of the SoS for two or more contracts (cadl 0.3.24)
    doc = ds.load_doc(GROUNDING)
    second = copy.deepcopy(doc["sos"]["contracts"][0])
    second["id"] = "RETURNS"
    second["norms"] = [n for n in second["norms"] if n["kind"] != "power"]
    doc["sos"]["contracts"].append(second)
    two = ds.norms_indices(ds.dump_doc(doc))
    assert "SoS: all 2 contracts (DELIVERY_SLA, RETURNS)" in two["text"]
    assert two["report"]["sos"]
    with pytest.raises(ValueError):
        ds.norms_indices(NORMS)              # no grounding 0.1


def test_game_check_at_the_fixed_values_and_as_a_map():
    fixed = ds.game_check(GAMES)
    assert fixed["status"] == 0 and fixed["failure"] is None
    assert "--- Equilibrium check 'comply_nash': profile 'comply', epsilon = 0 ---" in fixed["text"]
    assert "status: not_equilibrium; deviators: R" in fixed["text"]
    assert "Equilibrium maps not computed (give --certify)" in fixed["text"]
    assert "assumption (J.7)" in fixed["text"]
    certified = ds.game_check(GAMES, certify=True)
    assert certified["status"] == 0
    assert "lambda in [0.15625, 1]: equilibrium, from_every_state: true" in certified["text"]
    assert "end points (I.9): condition A holds for 'lambda'" in certified["text"]
    # nothing can be claimed for a design that is not a strategic model
    plain = ds.game_check(ds.load_example("stochastic robot delivery"))
    assert plain["status"] == 2 and "not a strategic model" in plain["errors"]
    late = ds.game_check(GAMES, certify=True, timeout=0.5)
    assert late["failure"] == "`cadl game-check` did not finish within 0.5 s; nothing is claimed."
    assert late["text"] == ""


CRASH = 'raise ValueError("an internal error in /home/someone/cadl/games_eval.py")\n'


def test_a_crash_of_cadl_is_not_a_result(monkeypatch):
    monkeypatch.setattr(ds, "_CHILD", CRASH)
    for check in (ds.game_check(GAMES), ds.norms_check(CONFLICT)):
        assert check["failure"].endswith("failed with an internal error; nothing is claimed.")
        assert "someone" not in str(check) and "Traceback" not in str(check)


def test_the_long_model_of_game_check_is_exact():
    # cadl 0.3.25 stopped on this file (an exact result of more than 4300 digits);
    # 0.3.26 writes such numbers, so it finishes, or meets the time limit cleanly
    long = ds.game_check((DATA / "game_check_crash.cadl").read_text(encoding="utf-8"))
    if long["failure"] is not None:
        assert long["failure"].startswith("`cadl game-check` did not finish within")
        return
    assert long["status"] == 0
    assert "status: not_equilibrium; deviators: R" in long["text"]
    assert "Traceback" not in long["errors"]


# ── Quantifiers and wildcard references in norms ────────────────────

WILDCARD = NORMS.replace('content: "ROBOT[i].battery < 10 AND ROBOT[i].status == InTransit"',
                         'content: "NOT ROBOT[*].battery > 10"')


def test_quantified_norms_are_read_back_as_written():
    lines = rb.describe_design(QUANTIFIED)["DELIVERY_SLA"]
    assert any("`exists j in ROBOT: exists k in ROBOT: j != k AND ROBOT[j].delivery == request.id "
               "AND ROBOT[k].delivery == request.id`" in x for x in lines)
    assert not any("is read as" in x for x in lines)        # no wildcard to rewrite


def test_wildcard_references_are_read_as_quantifiers_and_n15_is_shown():
    assert WILDCARD != NORMS
    analysis = ds.analyze(WILDCARD)
    assert not [f for f in analysis.findings if f.level == "error"]
    n15 = [f for f in analysis.findings if f.title.endswith("(N-15)")]
    assert len(n15) == 1 and n15[0].level == "warning" and "under NOT" in n15[0].title
    lines = rb.describe_design(WILDCARD)["DELIVERY_SLA"]
    assert ("`NOT ROBOT[*].battery > 10` is read as "
            "`NOT (for all *1 in ROBOT[*]: ROBOT[*1].battery > 10)`.") in lines
    assert any(x.startswith("Check that this is meant (N-15): the content: of norm "
                            "'no_low_battery_driving'") for x in lines)
    count = NORMS.replace('content: "ROBOT[i].battery < 10 AND ROBOT[i].status == InTransit"',
                          'content: "count(ROBOT[*].battery < 10) >= 2"')
    assert ("`count(ROBOT[*].battery < 10) >= 2` is read as "
            "`count(ROBOT[*1].battery < 10 for *1 in ROBOT[*]) >= 2`.") in rb.describe_design(count)["DELIVERY_SLA"]
    # the read-back of the bundled norms example is unchanged
    assert not any("is read as" in x or "N-15" in x for x in rb.describe_design(NORMS)["DELIVERY_SLA"])


# ── cadl norms-check ────────────────────────────────────────────────

def _without_prism() -> str:
    """PATH without the directories that hold PRISM (also for the subprocess)."""
    import os
    import shutil
    return os.pathsep.join(d for d in os.environ.get("PATH", "").split(os.pathsep)
                           if not shutil.which("prism", path=d))


def test_norms_contracts():
    assert ds.norms_contracts(CONFLICT) == ["STATUS_REPORTING"]
    assert ds.norms_contracts(ds.load_example("supply chain")) == []


@needs_prism
def test_norms_check_answers_conflicts_and_witnesses():
    result = ds.norms_check(CONFLICT)
    assert result["failure"] is None and result["notice"] is None
    report = result["report"]
    assert report["checked"] and report["contract"] == "STATUS_REPORTING"
    assert sorted(report["findings"]) == sorted([
        "apologise can be activated in a state from which every continuation violates it",
        "the power audit is never exercised",
        "report_status and radio_silence are in conflict",
        "apologise and radio_silence are in conflict"])
    norms = {n["id"]: n for n in report["norms"]}
    assert norms["apologise"]["keepable"]["answer"] is False
    assert norms["audit"]["exercisable"]["answer"] is False
    assert norms["report_status"]["violable"]["witness"]["confirmed"]
    assert all(w["confirmed"] for c in report["conflicts"] for w in c["witnesses"])
    assert report["unconfirmed"] == []
    assert "Witnesses (each one a trace that norms-eval evaluates)" in result["text"]
    assert "the design" in report["file"]
    assert "STATUS_REPORTING.prism" in result["files"]
    assert any(name.startswith("witness_conflict_") for name in result["files"])
    assert "/" not in result["text"].split("Witnesses", 1)[1].split("Findings")[0].replace(" / ", "")
    assert sorted(zipfile.ZipFile(io.BytesIO(result["zip"])).namelist()) == result["files"]
    # the options of the command line
    small = ds.norms_check(CONFLICT, bindings=["i=2"], members=["ROBOT=2"], step="1s", horizon=4)
    assert small["report"]["instance"]["horizon"] == 4
    assert small["report"]["instance"]["bindings"] == {"N": 2, "i": 2}   # N bound (0.3.26)


@needs_prism
def test_norms_check_respects_the_timeout():
    result = ds.norms_check(CONFLICT, timeout=0.01)
    assert result["report"] is None
    # cadl 0.3.26 bounds building the model by --timeout too
    assert "did not finish within --timeout" in result["failure"]
    assert result["failure"].endswith("nothing is claimed.")


def test_norms_check_of_a_heavy_design_is_bounded():
    # building the model of this design took minutes and hundreds of MB in cadl 0.3.25;
    # 0.3.26 stops it at --timeout, and the subprocess is stopped after timeout + budget
    heavy = (DATA / "norms_check_heavy.cadl").read_text(encoding="utf-8")
    start = time.monotonic()
    result = ds.norms_check(heavy, "STATUS_REPORTING", ["i=1"], timeout=1, budget=4)
    assert time.monotonic() - start < 15
    assert result["report"] is None and result["zip"] is None
    assert "did not finish within" in result["failure"]
    assert result["failure"].endswith("nothing is claimed.") or "run `cadl norms-check`" in result["failure"]


def test_norms_check_does_not_offer_a_large_export(monkeypatch):
    monkeypatch.setattr(ds, "MAX_EXPORT_BYTES", 1000)
    result = ds.norms_check(CONFLICT)
    assert result["zip"] is None and result["report"] is not None
    assert "not offered for download" in result["notice"]


def test_norms_check_without_prism_checks_nothing(monkeypatch):
    monkeypatch.setenv("PATH", _without_prism())
    assert not ds.prism_available()
    result = ds.norms_check(CONFLICT)
    assert result["report"]["checked"] is False
    assert "no model checker found" in result["report"]["skipped"]
    assert "Not checked:" in result["text"]
    assert "STATUS_REPORTING.prism" in result["files"]   # the model is still written


def test_norms_check_refusals():
    # arithmetic without a domain is not translated (cadl 0.3.26); the reason names --domain
    arithmetic = (DATA / "norms_check_arithmetic.cadl").read_text(encoding="utf-8")
    report = ds.norms_check(arithmetic, members=["ROBOT=2"])["report"]
    assert any(n["id"] == "over_capacity" and n["status"] == "untranslated"
               and "--domain" in n["reason"] for n in report["norms"])
    assert ds.norms_check(CONFLICT, contract="NOPE")["failure"] == (
        "no contract 'NOPE' in the file; nothing is claimed.")
    assert "does not declare 'norms: 0.1'" in ds.norms_check(ds.load_example("supply chain"))["failure"]


# ── Norms monitors of the OPA and Solidity targets ──────────────────

def test_norms_monitors_in_the_opa_and_solidity_code():
    opa = zipfile.ZipFile(io.BytesIO(ds.export_codegen_zip(CONFLICT, "opa"))).namelist()
    assert "norms_monitor_status_reporting.rego" in opa
    sol = zipfile.ZipFile(io.BytesIO(ds.export_codegen_zip(CONFLICT, "solidity"))).namelist()
    assert "StatusReportingNormsMonitor.sol" in sol
    assert ds.norms_monitor_notes(CONFLICT, "opa") == {
        "files": ["norms_monitor_status_reporting.rego"], "warnings": []}
    # a quantifier in a norm: no monitor, with the reason
    notes = ds.norms_monitor_notes(QUANTIFIED, "solidity")
    assert notes["files"] == []
    assert notes["warnings"][0].startswith("Contract DELIVERY_SLA: no norms monitor is generated")
    assert "quantifier" in notes["warnings"][0]
    # an item left out of a monitor
    guarded = GROUNDING.replace('"within(300s, ROBOT[i].status == Delivered)"',
                                '"within(300s, ROBOT[i].status == Delivered)"\n        - "ROBOT[i].load * 2 < 9"')
    assert guarded != GROUNDING
    left_out = ds.norms_monitor_notes(guarded, "solidity")["warnings"]
    assert any("is not monitored by DeliverySlaNormsMonitor.sol" in w for w in left_out)
    # other targets and designs without norms have none
    assert ds.norms_monitor_notes(CONFLICT, "python") == {"files": [], "warnings": []}
    assert ds.norms_monitor_notes(ds.load_example("supply chain"), "opa") == {"files": [], "warnings": []}


# ── norms-check of cadl 0.3.26: domains, instances, amendments ──────

SMALL = (DATA / "norms_check_small.cadl").read_text(encoding="utf-8")


@needs_prism
def test_norms_check_with_a_domain():
    arithmetic = (DATA / "norms_check_arithmetic.cadl").read_text(encoding="utf-8")
    result = ds.norms_check(arithmetic, members=["ROBOT=2"],
                            domains=["ROBOT[*].load=0..3", "DEPOT.capacity=2..4"])
    assert result["failure"] is None and result["report"]["checked"]
    assert any(n.startswith("DEPOT.capacity takes the integers 2..4 of --domain")
               for n in result["report"]["notes"])
    assert all(n["status"] == "model" for n in result["report"]["norms"])


@needs_prism
@pytest.mark.parametrize("instances", [2, ["i=1", "i=2"]])
def test_norms_check_of_several_instances(instances):
    result = ds.norms_check(SMALL, instances=instances)
    report = result["report"]
    assert result["failure"] is None
    assert [n["id"] for n in report["norms"]] == ["answer@1", "keep_quiet@1", "answer@2",
                                                  "keep_quiet@2"]
    assert "answer@2 and keep_quiet@2 are in conflict" in report["findings"]
    assert any("instances start together" in n for n in report["notes"])


@needs_prism
def test_norms_check_with_an_amendment():
    later = SMALL.replace("deadline: 2s", "deadline: 4s")
    result = ds.norms_check(SMALL, bindings=["i=1"], versions=[("v2 (later)", later)])
    assert result["failure"] is None
    assert "answer and keep_quiet are in conflict" in result["report"]["findings"]
    traces = [line for c in result["report"]["conflicts"] for w in c["witnesses"]
              for line in w["trace"]]
    assert any('"amend": "v1_v2_later.cadl"' in line for line in traces)
    # the download holds the version that its witnesses name
    archive = zipfile.ZipFile(io.BytesIO(result["zip"]))
    assert "v1_v2_later.cadl" in archive.namelist()
    witness = next(n for n in archive.namelist() if n.startswith("witness_conflict_"))
    assert '"amend": "v1_v2_later.cadl"' in archive.read(witness).decode()
    assert "amend" in result["text"]


def test_read_back_keeps_a_numeric_deadline_exact():
    exact = NORMS.replace("deadline: 5s", "deadline: 0.0005", 1)
    assert exact != NORMS
    assert any("within 0.0005 s." in x for x in rb.describe_design(exact)["DELIVERY_SLA"])
    assert any("within 5 s." in x for x in rb.describe_design(NORMS)["DELIVERY_SLA"])


def test_norms_check_too_many_instances_is_refused():
    result = ds.norms_check(SMALL, instances=5)
    assert result["report"] is None and result["failure"].endswith("nothing is claimed.")


def test_norms_check_internal_error_is_status_4(monkeypatch):
    monkeypatch.setattr(ds, "_CHILD", 'import sys\nsys.stderr.write("Internal error of '
                                      'norms-check (KeyError: x)\\n")\nsys.exit(4)\n')
    result = ds.norms_check(SMALL)
    assert result["failure"] == ("`cadl norms-check` failed with an internal error; "
                                 "nothing is claimed.")
    assert not result["transient"]


# ── Bounds of the commands on a shared server ───────────────────────

@pytest.mark.skipif(os.name != "posix", reason="process groups")
def test_prism_does_not_survive_a_timeout(tmp_path, monkeypatch):
    # cadl starts PRISM in a session of its own; a fake prism records its pid and waits
    pid_file = tmp_path / "prism.pid"
    fake = tmp_path / "bin" / "prism"
    fake.parent.mkdir()
    fake.write_text(f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 47\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    assert ds.prism_available()
    result = ds.norms_check(CONFLICT, "STATUS_REPORTING", ["i=1"], timeout=5, budget=-1)
    assert result["failure"].startswith("`cadl norms-check` did not finish within 4 s")
    assert result["transient"]
    pid = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        pytest.fail("the fake PRISM survived the timeout")


def test_at_most_two_commands_run_at_once():
    assert ds.MAX_CONCURRENT_RUNS == 2
    held = [ds._RUN_SLOTS.acquire(timeout=1) for _ in range(ds.MAX_CONCURRENT_RUNS)]
    try:
        assert all(held)
        busy = ds.game_check(GAMES)
        assert busy["failure"] == ("The server is running other checks; try again shortly. "
                                   "Nothing is claimed.")
        assert busy["transient"] and busy["text"] == ""
        assert ds.norms_check(CONFLICT)["failure"] == busy["failure"]
    finally:
        for _ in held:
            ds._RUN_SLOTS.release()
    assert ds.game_check(GAMES)["failure"] is None


def test_norms_check_timeout_is_at_most_120_s():
    # the wall-clock limit is the (capped) timeout and the budget
    late = ds.norms_check(CONFLICT, timeout=10_000, budget=-119.5)
    assert late["failure"].startswith("`cadl norms-check` did not finish within 0.5 s")


def test_a_usage_error_is_one_line():
    failure = ds.norms_check(CONFLICT, members=["NOBODY=2"])["failure"]
    assert failure == "--members NOBODY: NOBODY is not a declared set of actors; nothing is claimed."


# ── Memory: the cadl process and the processes it starts ────────────

@pytest.mark.skipif(os.name != "posix", reason="resource limits")
def test_the_cadl_process_has_a_soft_memory_limit(monkeypatch):
    probe = ds._CHILD.replace(
        "from cadl.cli import main\nsys.exit(main())",
        "print(resource.getrlimit(resource.RLIMIT_AS)[0], resource.getrlimit(resource.RLIMIT_CPU)[0])"
        "\nsys.exit(0)")
    assert probe != ds._CHILD
    monkeypatch.setattr(ds, "_CHILD", probe)
    soft_as, soft_cpu = map(int, ds.game_check(GAMES, timeout=30)["text"].split())
    import resource
    import sys
    hard_as = resource.getrlimit(resource.RLIMIT_AS)[1]
    if sys.platform.startswith("linux"):    # macOS does not take RLIMIT_AS (the watchdog does)
        assert soft_as == (ds.CADL_MEMORY_LIMIT if hard_as == resource.RLIM_INFINITY
                           else min(ds.CADL_MEMORY_LIMIT, hard_as))
    assert 0 < soft_cpu <= 31


@pytest.mark.skipif(os.name != "posix", reason="resource limits")
def test_the_processes_that_cadl_starts_raise_the_soft_limits(tmp_path, monkeypatch):
    # a fake prism writes its soft and hard limits of address space and CPU time
    limits = tmp_path / "limits"
    fake = tmp_path / "bin" / "prism"
    fake.parent.mkdir()
    fake.write_text(f"#!/bin/sh\n(ulimit -S -v; ulimit -H -v; ulimit -S -t; ulimit -H -t) > {limits}\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    ds.norms_check(CONFLICT, "STATUS_REPORTING", ["i=1"], timeout=10)
    soft_v, hard_v, soft_t, hard_t = limits.read_text().split()
    assert soft_v == hard_v and soft_t == hard_t


@pytest.mark.skipif(os.name != "posix", reason="process groups")
def test_the_memory_watchdog_stops_the_cadl_process(monkeypatch):
    monkeypatch.setattr(ds, "MEMORY_WATCH_LIMIT", 100 << 20)
    monkeypatch.setattr(ds, "_CHILD", 'import time\nx = b"x" * (400 << 20)\ntime.sleep(30)\n')
    start = time.monotonic()
    run = ds.game_check(GAMES, timeout=30)
    assert time.monotonic() - start < 10
    assert run["failure"] == ("`cadl game-check` was stopped at the memory limit of this app "
                              "(100 MB); nothing is claimed.")
    assert run["transient"]


@pytest.mark.skipif(os.name != "posix", reason="process groups")
def test_the_memory_watchdog_counts_prism(tmp_path, monkeypatch):
    # a fake prism, started by cadl in a session of its own, takes 400 MB
    import sys
    pid_file = tmp_path / "prism.pid"
    fake = tmp_path / "bin" / "prism"
    fake.parent.mkdir()
    fake.write_text(f"#!{sys.executable}\nimport os, time\n"
                    f"open({str(pid_file)!r}, 'w').write(str(os.getpid()))\n"
                    f"x = b'x' * (400 << 20)\ntime.sleep(30)\n")
    fake.chmod(0o755)
    monkeypatch.setenv("PATH", f"{fake.parent}{os.pathsep}{os.environ.get('PATH', '')}")
    monkeypatch.setattr(ds, "MEMORY_WATCH_LIMIT", 300 << 20)
    start = time.monotonic()
    result = ds.norms_check(CONFLICT, "STATUS_REPORTING", ["i=1"], timeout=30)
    assert time.monotonic() - start < 20
    assert "was stopped at the memory limit of this app" in result["failure"]
    pid = int(pid_file.read_text())
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.1)
    else:
        os.kill(pid, 9)
        pytest.fail("the fake PRISM survived the memory limit")
