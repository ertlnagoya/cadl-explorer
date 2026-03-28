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
    cfg = make_config("A-SoS + motivation-sensitive", "linear", 0.5)
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
    assert "sos_type: directed" in yaml_str
    assert "alpha:" in yaml_str


def test_load_experiment_config():
    path = os.path.join(os.path.dirname(__file__), "..", "experiments", "a_sos_baseline.yaml")
    if os.path.exists(path):
        cfg = load_experiment_config(path)
        assert cfg.sos_type == "directed"
        assert cfg.name == "A-SoS-Baseline"
