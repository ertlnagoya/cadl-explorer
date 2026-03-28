"""Smoke tests for plotting modules."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import plotly.graph_objects as go
from backend.runners.synthetic_runner import run_sweep, run_comparison_sweep
from backend.services.evaluation_service import evaluate
from backend.plotting.interactive import (
    scatter_performance_autonomy, line_rho_effects,
    bar_comparison, individual_robot_scatter,
)


def _get_data():
    return run_comparison_sweep("linear", [0.0, 0.5], num_seeds=3)


def test_scatter_returns_figure():
    data = _get_data()
    fig = scatter_performance_autonomy(data["a_sos"], data["c_sos"])
    assert isinstance(fig, go.Figure)


def test_line_rho_returns_figure():
    results = run_sweep("directed", "linear", [0.0, 0.5, 1.0], num_seeds=3)
    fig = line_rho_effects(results)
    assert isinstance(fig, go.Figure)


def test_bar_comparison_returns_figure():
    data = _get_data()
    ev_a = evaluate(data["a_sos"])
    ev_b = evaluate(data["c_sos"])
    fig = bar_comparison(ev_a, ev_b, "A-SoS", "C-SoS")
    assert isinstance(fig, go.Figure)


def test_individual_robot_returns_figure():
    data = _get_data()
    fig = individual_robot_scatter(data["a_sos"][:3])
    assert isinstance(fig, go.Figure)
