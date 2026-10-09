"""Tests for cadl_service."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.cadl_service import (
    make_config, make_baseline_config, build_ir, config_to_yaml_str,
    generate_unity_config_dict, load_experiment_config, TEMPLATES,
)


def test_make_baseline_config():
    cfg = make_baseline_config()
    assert cfg.sos_type == "directed"
    assert cfg.governance_motivation.rho == 0.0
    assert cfg.agent_motivation.profile == "uniform"


def test_make_config_all_templates():
    for name in TEMPLATES:
        cfg = make_config(name, "linear", 0.5)
        assert cfg.name
        assert cfg.sos_type in ("directed", "collaborative")


def test_build_ir():
    cfg = make_config("D-SoS + motivation-sensitive", "linear", 0.5)
    ir = build_ir(cfg)
    assert ir.institution.sos_type == "directed"
    assert ir.institution.motivation_interpretation.rho == 0.5
    d = ir.to_dict()
    assert "layer1_institution" in d
    assert "layer2_protocol" in d
    assert "layer3_algorithm" in d


def test_generate_unity_config():
    cfg = make_config("C-SoS", "polarized", 0.0)
    uc = generate_unity_config_dict(cfg)
    assert uc["simulatorConfig"]["sosType"] == "collaborative"
    assert "agentTemplates" in uc
    assert "communicationSetup" in uc
    assert "motivationConfig" in uc


def test_config_to_yaml_str():
    cfg = make_baseline_config()
    yaml_str = config_to_yaml_str(cfg)
    # YAML is emitted with CADL-canonical Capitalized sos_type per
    # Appendix A §A.1, independent of the lowercase internal form.
    assert "sos_type: Directed" in yaml_str
    assert "alpha:" in yaml_str


def test_sos_type_canonical_roundtrip():
    """Accept both lowercase and Capitalized sos_type; emit Capitalized.

    Aligns the demo with CADL Appendix A §A.1 (see cadl-spec audit).
    """
    import pytest
    from cadl_sim.schema.motivation_schema import (
        CADLMotivationConfig, _normalize_sos_type,
    )
    for lc, cap in [
        ("directed", "Directed"),
        ("DIRECTED", "Directed"),
        ("Directed", "Directed"),
        ("collaborative", "Collaborative"),
    ]:
        cfg = CADLMotivationConfig.from_dict({"sos_type": lc})
        assert cfg.sos_type == lc.lower()  # internal lowercase
        assert cfg.to_dict()["sos_type"] == cap  # canonical emit
    with pytest.raises(ValueError):
        CADLMotivationConfig.from_dict({"sos_type": "bogus"})


def test_load_experiment_config():
    path = os.path.join(os.path.dirname(__file__), "..", "experiments", "d_sos_baseline.yaml")
    if os.path.exists(path):
        cfg = load_experiment_config(path)
        assert cfg.sos_type == "directed"
        assert cfg.name == "D-SoS-Baseline"


# ── Input limits on pasted CADL YAML ────────────────────────────────

import pytest

from backend.services.cadl_service import (
    parse_cadl_yaml, MAX_ROBOTS, MAX_SOURCE_CHARS,
)


def test_parse_accepts_nested_layout():
    cfg = parse_cadl_yaml(
        "name: ok\nsos_type: directed\n"
        "environment:\n  num_robots: 5\n"
        "governance:\n  alpha: 0.3\n  beta: 0.7\n  lambda: 0.0\n"
        "motivation:\n  agent:\n    profile: linear\n"
        "  governance:\n    model: hybrid\n    rho: 0.5\n"
    )
    assert cfg.num_robots == 5
    assert cfg.beta == 0.7
    assert cfg.governance_motivation.rho == 0.5


@pytest.mark.parametrize("text, fragment", [
    (f"environment:\n  num_robots: {MAX_ROBOTS + 1}\n", "environment.num_robots"),
    ("environment:\n  num_robots: many\n", "environment.num_robots"),
    ("environment:\n  num_robots: 0\n", "environment.num_robots"),
    ("governance:\n  beta: 1.5\n", "governance.beta"),
    ("motivation:\n  agent:\n    profile: bogus\n", "motivation.agent.profile"),
    ("motivation:\n  governance:\n    rho: abc\n", "motivation.governance.rho"),
    ("name: 5\n", "name"),
    ("environment: 3\n", "environment"),
    ("a: &x [1, 2]\nb: *x\n", "anchors"),
    ("name: " + "x" * MAX_SOURCE_CHARS + "\n", "too long"),
])
def test_parse_rejects_out_of_range_input(text, fragment):
    with pytest.raises(ValueError) as exc:
        parse_cadl_yaml(text)
    assert fragment in str(exc.value)


def test_template_names_before_the_rename_are_still_accepted():
    from backend.services.cadl_service import make_config, resolve_template, TEMPLATES
    from backend.services.pipeline import run_pipeline

    assert "A-SoS" not in TEMPLATES and "D-SoS" in TEMPLATES
    assert resolve_template("A-SoS") == "D-SoS"
    assert make_config("A-SoS + motivation-sensitive", "linear", 0.5).name == \
        make_config("D-SoS + motivation-sensitive", "linear", 0.5).name
    old, new = run_pipeline("A-SoS", "uniform", 0.0, num_seeds=2), run_pipeline("D-SoS", "uniform", 0.0, num_seeds=2)
    assert old.template == "D-SoS" and old.cadl_id == new.cadl_id
    try:
        resolve_template("Z-SoS")
    except ValueError as e:
        assert "D-SoS" in str(e)
    else:
        raise AssertionError("unknown template accepted")
