"""What the Designer takes from cadl 0.3.32: the regime bindings (A.7.1) and the
safety invariants of transitions, the amendment extension and `cadl amend-diff`
(Appendix K), and `norms-check --max-states` (F.12)."""

from __future__ import annotations

import os

import pytest

from tests.conftest import AppTest

pytest.importorskip("cadl.amendment")     # cadl 0.3.32 or later

from backend.services import design_service as ds  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REGIMES = open(os.path.join(ROOT, "tests", "data", "regime_bindings.cadl"), encoding="utf-8").read()
V1 = ds.load_example("amendment robot town v1")
V2 = ds.load_example("amendment robot town v2")


def test_the_extensions_are_recognised():
    assert "amendment" in ds.extensions_in_effect(V1)
    assert "regimes" in ds.extensions_in_effect(REGIMES)
    assert not {"amendment", "regimes"} & ds.extensions_in_effect(ds.load_example("robot town"))


def test_amend_diff_accepts_the_second_version_and_rejects_the_reverse():
    report = ds.amend_diff(V1, V2)
    assert report["ok"]
    assert [p["id"] for p in report["parameters"] if p["changed"]] == ["block_k"]
    assert report["contracts"][0]["id"] == "ROAD_USE"
    back = ds.amend_diff(V2, V1)
    assert not back["ok"] and any("takes effect" in e for e in back["errors"])


def test_amend_diff_needs_the_declaration_and_a_readable_old_version():
    robot_town = ds.load_example("robot town")
    assert ds.amend_diff(robot_town, robot_town) is None
    broken = ds.amend_diff(V1.replace("sos:", "sos: [", 1), V2)
    assert broken["invalid_old"] and not broken["ok"]


def test_a_change_that_no_parameter_explains_is_an_error():
    # a guarantee of a contract is a rule that no declared parameter holds
    changed = V2.replace("guarantee:", "guarantee:\n        - \"robots_on_road >= 0\"", 1)
    if changed == V2:
        pytest.skip("the example has no guarantee to change")
    report = ds.amend_diff(V1, changed)
    assert not report["ok"]


def test_the_regime_bindings_and_safety_invariants_reach_the_ir():
    analysis = ds.analyze(REGIMES)
    bindings = {b["name"]: b for b in ds.regime_bindings(analysis.ir)}
    assert bindings["DISTRIBUTED"]["suspended_contracts"] == ["BAY_ASSIGNMENT"]
    assert bindings["CENTRALIZED"]["parameters"]["ROUTING.beta"] == 0.9
    assert analysis.ir["transitions"][0]["safety_invariant"] == "robots_on_road == 0"
    assert ds.regime_bindings(ds.analyze(ds.load_example("robot town")).ir) is None


def test_a_broken_binding_is_shown_on_the_regimes_view():
    analysis = ds.analyze(REGIMES.replace("ROUTING.beta: 0.9", "ROUTING.beta: 1.9"))
    errors = [f for f in analysis.findings if f.level == "error"]
    assert errors and all(f.section == "Regimes" for f in errors)
    assert errors[0].item == "CENTRALIZED" and "(R-4)" in errors[0].title
    assert ds.view_of(errors[0]) == ("Regimes", None)
    # a regime that the file does not have names no item
    unknown = ds.analyze(REGIMES.replace("    CENTRALIZED:\n      parameters",
                                         "    PEAK: {}\n    CENTRALIZED:\n      parameters"))
    error = next(f for f in unknown.findings if f.level == "error")
    assert error.section == "Regimes" and error.item == "" and error.element == "PEAK"


def test_norms_check_passes_the_limit_on_the_states(monkeypatch):
    seen = {}

    def fake(args, source, wall, workdir=None, cancel=None):
        seen["args"] = args
        return {"failure": "stopped", "transient": False}

    monkeypatch.setattr(ds, "_run_cadl", fake)
    ds.norms_check(ds.load_example("norms check conflict"), max_states=200_000)
    assert seen["args"][seen["args"].index("--max-states") + 1] == "200000"
    ds.norms_check(ds.load_example("norms check conflict"))
    assert "--max-states" not in seen["args"]


# ── The pages ─────────────────────────────────────────────────────────

def _designer(source):
    if AppTest is None:
        pytest.skip("this Streamlit has no AppTest")
    at = AppTest.from_file(os.path.join(ROOT, "views", "designer.py"), default_timeout=60).run()
    at.session_state.design_src = source
    at.session_state.design_ver += 1
    return at.run()


def _set(at, **values):
    for key, value in values.items():
        at.session_state[key] = value
    return at.run()


def test_the_regimes_view_lists_the_bindings():
    at = _set(_designer(REGIMES), design_view="Regimes")
    assert not at.exception
    assert any("What each regime binds" in m.value for m in at.markdown)
    assert any("Safety invariants of the transitions" in m.value for m in at.markdown)
    listing = next(m.value for m in at.markdown if m.value.startswith("- **DISTRIBUTED**"))
    assert "suspended: BAY_ASSIGNMENT" in listing.splitlines()[0]
    assert "`ROUTING.beta` = 0.9" in listing.splitlines()[1]
    assert "in force: ROUTING, BAY_ASSIGNMENT" in listing.splitlines()[1]
    assert at.dataframe[0].value["safety invariant"][0] == "robots_on_road == 0"


def test_the_versions_view_lists_the_declaration_and_runs_amend_diff():
    at = _set(_designer(V2), design_view="Versions",
              design_cmp_a="Example: amendment robot town v1", design_cmp_b="Current design")
    assert not at.exception
    assert any("Declared amendable parameters" in m.value for m in at.markdown)
    assert any("The changes respect the declared amendability." in s.value for s in at.success)
    at = _set(at, design_cmp_a="Current design", design_cmp_b="Example: amendment robot town v1")
    assert not at.exception
    assert any("do not respect the declared amendability" in e.value for e in at.error)


def test_the_checks_offer_the_limit_on_the_states():
    at = _set(_designer(ds.load_example("norms check conflict")), design_view="Checks",
              design_stage="Verify")
    assert not at.exception
    assert any(n.key == "design_nc_max_states" for n in at.number_input)


# ── Findings of the review ────────────────────────────────────────────

def test_the_rule_about_the_regimes_of_a_model_is_not_a_binding():
    # R-7 is about `model.regimes:` of stochastic 0.1, not about the bindings
    model = ds.Finding("error", "Type check", "model.regimes.CENTRALIZED gives 'p' 0.4, but the "
                       "binding of ROUTING.beta gives 0.9 (R-7)")
    ds._locate(model)
    assert model.section != "Regimes"
    binding = ds.Finding("error", "Type check", "regimes.CENTRALIZED.parameters gives "
                         "'ROUTING.beta' the value 1.9, outside [0, 1] (R-4)")
    ds._locate(binding)
    assert (binding.section, binding.item) == ("Regimes", "CENTRALIZED")


def test_a_fractional_value_keeps_its_unit_and_the_authority_is_translated():
    from views._design.change import _authority_text, _quantity
    assert _quantity(2.5, "m") == "2.5 m"
    assert _quantity(60000, "ms") == "1min"
    assert _quantity(3, None) == "3"
    assert _authority_text("COUNCIL by vote, quorum 2") == "COUNCIL by vote, quorum 2"
    assert _authority_text("OPERATOR") == "OPERATOR"


def test_an_unreadable_a_is_not_called_undeclared():
    at = _set(_designer(V2), design_view="Versions", design_cmp_a="Current design",
              design_cmp_b="Example: amendment robot town v1")
    at.session_state.design_src = V2.replace("sos:", "sos: [", 1)
    at.session_state.design_ver += 1
    at = at.run()
    assert not at.exception
    assert not any("does not declare amendment 0.1" in i.value for i in at.info)
