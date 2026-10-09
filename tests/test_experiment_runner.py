"""Tests for experiment runners."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.runners.synthetic_runner import run_single, run_sweep, run_comparison_sweep
from backend.services.experiment_service import run_experiment, run_comparison_sweep as svc_comparison
from backend.models.experiment_result import SingleResult


def test_run_single_directed():
    result = run_single("directed", 0.5, "linear", seed=42)
    assert isinstance(result, SingleResult)
    assert result.sos_type == "directed"
    assert result.rho == 0.5
    assert result.throughput > 0
    assert 0 <= result.avg_autonomy <= 1
    assert 0 <= result.fairness <= 1
    assert len(result.per_robot) == 5


def test_run_single_collaborative():
    result = run_single("collaborative", 0.0, "uniform", seed=0)
    assert result.sos_type == "collaborative"
    assert result.avg_autonomy > 0.5  # C-SoS has higher autonomy


def test_run_sweep():
    results = run_sweep("directed", "linear", [0.0, 1.0], num_seeds=3)
    assert len(results) == 6  # 2 rho * 3 seeds
    rho_values = set(r.rho for r in results)
    assert rho_values == {0.0, 1.0}


def test_run_comparison_sweep():
    comparison = run_comparison_sweep("uniform", [0.0, 0.5], num_seeds=2)
    assert "d_sos" in comparison
    assert "c_sos" in comparison
    assert len(comparison["d_sos"]) == 4  # 2 rho * 2 seeds
    assert len(comparison["c_sos"]) == 2  # 1 rho * 2 seeds


def test_experiment_service_synthetic():
    result = run_experiment(mode="synthetic", sos_type="directed", rho=0.3, profile="polarized", seed=1)
    assert isinstance(result, SingleResult)
    assert result.motivation_profile == "polarized"


def test_deterministic_seeds():
    r1 = run_single("directed", 0.5, "linear", seed=99)
    r2 = run_single("directed", 0.5, "linear", seed=99)
    assert r1.throughput == r2.throughput
    assert r1.fairness == r2.fairness
