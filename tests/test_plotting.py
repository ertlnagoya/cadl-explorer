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
    fig = scatter_performance_autonomy(data["d_sos"], data["c_sos"])
    assert isinstance(fig, go.Figure)


def test_line_rho_returns_figure():
    results = run_sweep("directed", "linear", [0.0, 0.5, 1.0], num_seeds=3)
    fig = line_rho_effects(results)
    assert isinstance(fig, go.Figure)


def test_bar_comparison_returns_figure():
    data = _get_data()
    ev_a = evaluate(data["d_sos"])
    ev_b = evaluate(data["c_sos"])
    fig = bar_comparison(ev_a, ev_b, "D-SoS", "C-SoS")
    assert isinstance(fig, go.Figure)


def test_individual_robot_returns_figure():
    data = _get_data()
    fig = individual_robot_scatter(data["d_sos"][:3])
    assert isinstance(fig, go.Figure)


def test_scatter_ab_traces():
    from backend.plotting.interactive import scatter_ab
    from backend.runners.synthetic_runner import run_sweep

    a = run_sweep("directed", "uniform", [0.0], num_seeds=5)
    b = run_sweep("directed", "linear", [0.5], num_seeds=5)
    ref = run_sweep("collaborative", "uniform", [0.0], num_seeds=5)
    fig = scatter_ab(a, b, "A", "B", {"C-SoS reference": ref})
    names = [t.name for t in fig.data]
    assert "A" in names and "B" in names and "C-SoS reference" in names


def test_per_robot_ab_groups_by_design():
    from backend.plotting.interactive import per_robot_ab
    from backend.runners.synthetic_runner import run_sweep

    a = run_sweep("directed", "uniform", [0.0], num_seeds=3)
    b = run_sweep("directed", "linear", [0.5], num_seeds=3)
    fig = per_robot_ab(a, b)
    assert len(fig.data) == 4  # deliveries + freedom for each design
    assert list(fig.data[0].x) == ["R0", "R1", "R2", "R3", "R4"]


def test_line_rho_effects_highlights_profile():
    from backend.plotting.interactive import line_rho_effects, COLOR_B
    from backend.runners.synthetic_runner import run_sweep

    sweep = []
    for prof in ["uniform", "linear"]:
        sweep.extend(run_sweep("directed", prof, [0.0, 0.5], num_seeds=3))
    fig = line_rho_effects(sweep, current_rho=0.5, highlight_profile="linear")
    highlighted = [t for t in fig.data if t.legendgroup == "linear"]
    assert all(t.line.color == COLOR_B for t in highlighted)


def test_scenario_svg_reflects_design():
    from backend.plotting.scenario import scenario_svg, motivation_color

    central = scenario_svg([0.2, 0.4, 0.6, 0.8, 1.0], "central")
    verifier = scenario_svg([0.5] * 5, "hybrid", dark=True)
    assert "central authority" in central and "stroke-dasharray" in central
    assert "verifier only" in verifier and "stroke-dasharray" not in verifier
    assert motivation_color(0.2) in central and motivation_color(1.0) in central
    assert motivation_color(0.0) != motivation_color(1.0)
