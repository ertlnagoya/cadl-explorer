"""Tests for diff_service."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.cadl_service import make_config, make_baseline_config, build_ir, generate_unity_config_dict
from backend.services.diff_service import diff_cadl, diff_ir, diff_config, diff_result, DiffResult
from backend.services.evaluation_service import evaluate
from backend.runners.synthetic_runner import run_comparison_sweep


def test_diff_cadl():
    a = make_baseline_config()
    b = make_config("A-SoS + motivation-sensitive", "linear", 0.5)
    result = diff_cadl(a, b)
    assert isinstance(result, DiffResult)
    assert result.category == "cadl"
    assert result.has_changes
    assert len(result.tagged_lines) > 0


def test_diff_cadl_identical():
    a = make_baseline_config()
    b = make_baseline_config()
    result = diff_cadl(a, b)
    assert not result.has_changes


def test_diff_ir():
    a = make_baseline_config()
    b = make_config("C-SoS", "linear", 0.0)
    ir_a = build_ir(a)
    ir_b = build_ir(b)
    result = diff_ir(ir_a, ir_b)
    assert "Layer 1: Institution" in result
    assert "Layer 2: Protocol" in result
    assert "Layer 3: Algorithm" in result
    assert result["Layer 1: Institution"].has_changes


def test_diff_config():
    a = make_baseline_config()
    b = make_config("C-SoS", "linear", 0.0)
    uc_a = generate_unity_config_dict(a)
    uc_b = generate_unity_config_dict(b)
    result = diff_config(uc_a, uc_b, a.name, b.name)
    assert result.category == "config"
    assert result.has_changes


def test_diff_result():
    comparison = run_comparison_sweep("linear", [0.0, 0.5], num_seeds=3)
    ev_a = evaluate([r for r in comparison["a_sos"] if r.rho == 0.0])
    ev_b = evaluate([r for r in comparison["a_sos"] if r.rho == 0.5])
    result = diff_result(ev_a, ev_b, "baseline", "rho=0.5")
    assert result.category == "result"
    assert isinstance(result.to_html(), str)


def test_diff_result_to_html():
    a = make_baseline_config()
    b = make_config("A-SoS + motivation-sensitive", "linear", 0.5)
    result = diff_cadl(a, b)
    html = result.to_html()
    assert "<div" in html
    assert "monospace" in html
