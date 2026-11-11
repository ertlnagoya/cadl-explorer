"""The extensions of cadl-lang 0.3.23 to 0.3.25: games 0.1 and grounding 0.1,
quantified and wildcard predicates in norms, `cadl norms-check` and the
norms monitors of the OPA and Solidity targets."""
import copy
import io
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


@pytest.mark.skipif(not hasattr(__import__("sys"), "set_int_max_str_digits"),
                    reason="this Python has no limit on the digits of an integer")
def test_the_crash_reproducer_of_game_check():
    # cadl 0.3.25 raises in games_eval on this file (more than 4300 digits)
    crash = ds.game_check((DATA / "game_check_crash.cadl").read_text(encoding="utf-8"))
    assert crash["failure"] == ("`cadl game-check` failed with an internal error; "
                                "nothing is claimed.")
    assert crash["text"] == "" and crash["errors"] == ""     # no traceback, no path


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
    assert small["report"]["instance"]["bindings"] == {"i": 2}


@needs_prism
def test_norms_check_respects_the_timeout():
    result = ds.norms_check(CONFLICT, timeout=0.01)
    assert result["report"] is None
    assert "the model checker did not finish within --timeout" in result["failure"]
    assert result["failure"].endswith("nothing is claimed.")


def test_norms_check_of_a_heavy_design_is_bounded():
    # building the model of this design takes minutes and hundreds of MB, which
    # --timeout does not bound; the subprocess is stopped after timeout + budget
    heavy = (DATA / "norms_check_heavy.cadl").read_text(encoding="utf-8")
    start = time.monotonic()
    result = ds.norms_check(heavy, "STATUS_REPORTING", ["i=1"], timeout=1, budget=4)
    assert time.monotonic() - start < 15
    assert result["report"] is None and result["zip"] is None
    assert result["failure"].startswith("`cadl norms-check` did not finish within 5 s; "
                                        "nothing is claimed.")


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
    # a quantified predicate is not translated; the norm is reported with the reason
    report = ds.norms_check(QUANTIFIED)["report"]
    assert any(n["id"] == "no_double_assignment" and n["status"] == "untranslated"
               and "quantifier" in n["reason"] for n in report["norms"])
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
