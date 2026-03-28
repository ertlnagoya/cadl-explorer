"""Tests for evaluation service and metric modules."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.runners.synthetic_runner import run_sweep
from backend.services.evaluation_service import evaluate, evaluate_full, compare, generate_summary
from backend.evaluation.system_metrics import compute_throughput
from backend.evaluation.autonomy_metrics import compute_autonomy
from backend.evaluation.fairness_metrics import compute_fairness, compute_gini
from backend.evaluation.structural_metrics import compute_structural
from backend.services.cadl_service import make_config, make_baseline_config, build_ir
from backend.models.evaluation_result import EvaluationResult


def _get_results():
    return run_sweep("directed", "linear", [0.0, 0.5], num_seeds=5)


def test_compute_throughput():
    results = _get_results()
    mv = compute_throughput(results)
    assert mv.mean > 0
    assert mv.n == len(results)


def test_compute_autonomy():
    results = _get_results()
    mv = compute_autonomy(results)
    assert 0 <= mv.mean <= 1


def test_compute_fairness():
    results = _get_results()
    mv = compute_fairness(results)
    assert 0 <= mv.mean <= 1


def test_compute_gini():
    results = _get_results()
    mv = compute_gini(results)
    assert 0 <= mv.mean <= 1


def test_evaluate_returns_dict():
    results = _get_results()[:5]
    ev = evaluate(results)
    assert isinstance(ev, dict)
    assert "throughput" in ev
    assert "autonomy" in ev
    assert "fairness" in ev
    assert "n" in ev


def test_evaluate_full_returns_structured():
    results = _get_results()[:5]
    ev = evaluate_full(results)
    assert isinstance(ev, EvaluationResult)
    assert ev.throughput.mean > 0
    assert ev.throughput.n == 5


def test_compare():
    results = _get_results()
    ev_a = evaluate([r for r in results if r.rho == 0.0])
    ev_b = evaluate([r for r in results if r.rho == 0.5])
    text = compare(ev_a, ev_b, "baseline", "rho=0.5")
    assert "Governance Comparison" in text
    assert "Throughput" in text


def test_generate_summary():
    results = _get_results()
    base = [r for r in results if r.rho == 0.0]
    sel = [r for r in results if r.rho == 0.5]
    text = generate_summary(base, sel, "baseline", "rho=0.5")
    assert "Governance Comparison" in text


def test_structural_metrics():
    a = make_baseline_config()
    b = make_config("C-SoS", "linear", 0.0)
    ir_a = build_ir(a)
    ir_b = build_ir(b)
    result = compute_structural(ir_a, ir_b)
    assert "structural_distance" in result
    assert result["structural_distance"].mean > 0


def test_evaluate_empty():
    ev = evaluate([])
    assert ev["throughput"] == 0
