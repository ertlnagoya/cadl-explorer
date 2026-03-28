"""Interactive Plotly charts for Streamlit UI."""

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from typing import List, Dict, Optional


def scatter_performance_autonomy(
    a_sos_results: list,
    c_sos_results: list,
    selected_results: list = None,
    selected_label: str = "",
) -> go.Figure:
    """Performance-autonomy scatter with feasible regions."""
    fig = go.Figure()

    if a_sos_results:
        x = [r.avg_autonomy for r in a_sos_results]
        y = [r.throughput for r in a_sos_results]
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="markers", name="A-SoS baseline",
            marker=dict(color="steelblue", size=6, opacity=0.5),
        ))

    if c_sos_results:
        x = [r.avg_autonomy for r in c_sos_results]
        y = [r.throughput for r in c_sos_results]
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="markers", name="C-SoS",
            marker=dict(color="darkorange", size=6, opacity=0.5),
        ))

    if selected_results:
        x = [r.avg_autonomy for r in selected_results]
        y = [r.throughput for r in selected_results]
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="markers", name=selected_label or "Selected",
            marker=dict(color="crimson", size=10, symbol="diamond", opacity=0.8),
        ))

    fig.update_layout(
        title="Performance-Autonomy Plane",
        xaxis_title="System Autonomy", yaxis_title="Throughput (total deliveries)",
        xaxis=dict(range=[-0.05, 1.05]), height=450,
        margin=dict(l=60, r=20, t=50, b=50),
    )
    return fig


def line_rho_effects(sweep_results: list) -> go.Figure:
    """rho vs throughput / autonomy / fairness."""
    fig = make_subplots(rows=1, cols=3, subplot_titles=["Throughput", "Autonomy", "Fairness"])

    profiles = sorted(set(r.motivation_profile for r in sweep_results))
    colors = {"uniform": "steelblue", "linear": "seagreen", "polarized": "crimson"}

    for profile in profiles:
        subset = [r for r in sweep_results if r.motivation_profile == profile]
        rho_vals = sorted(set(r.rho for r in subset))

        for col, metric in enumerate(["throughput", "avg_autonomy", "fairness"], 1):
            means, stds = [], []
            for rho in rho_vals:
                vals = [getattr(r, metric) for r in subset if r.rho == rho]
                means.append(np.mean(vals) if vals else 0)
                stds.append(np.std(vals) if vals else 0)

            color = colors.get(profile, "gray")
            fig.add_trace(go.Scatter(
                x=rho_vals, y=means, mode="lines+markers",
                name=profile if col == 1 else None, showlegend=(col == 1),
                line=dict(color=color), marker=dict(color=color, size=6),
                error_y=dict(type="data", array=stds, visible=True, color=color),
            ), row=1, col=col)

    fig.update_xaxes(title_text="rho", row=1, col=1)
    fig.update_xaxes(title_text="rho", row=1, col=2)
    fig.update_xaxes(title_text="rho", row=1, col=3)
    fig.update_layout(height=350, margin=dict(l=50, r=20, t=50, b=50))
    return fig


def bar_comparison(eval_a: dict, eval_b: dict, label_a: str, label_b: str) -> go.Figure:
    """Bar chart comparing two governance evaluations."""
    metrics = ["throughput", "autonomy", "fairness"]
    labels = ["Throughput", "Autonomy", "Fairness"]

    fig = go.Figure()
    fig.add_trace(go.Bar(
        name=label_a, x=labels,
        y=[eval_a.get(m, 0) for m in metrics],
        error_y=dict(type="data", array=[eval_a.get(f"{m}_std", 0) for m in metrics]),
        marker_color="steelblue",
    ))
    fig.add_trace(go.Bar(
        name=label_b, x=labels,
        y=[eval_b.get(m, 0) for m in metrics],
        error_y=dict(type="data", array=[eval_b.get(f"{m}_std", 0) for m in metrics]),
        marker_color="crimson",
    ))
    fig.update_layout(barmode="group", title="Governance Metric Comparison",
                      height=350, margin=dict(l=50, r=20, t=50, b=50))
    return fig


def individual_robot_scatter(results: list) -> go.Figure:
    """Per-robot freedom vs deliveries, colored by motivation."""
    fig = go.Figure()
    for r in results[:5]:
        for robot in r.per_robot:
            fig.add_trace(go.Scatter(
                x=[robot["freedom"]], y=[robot["deliveries"]], mode="markers",
                marker=dict(
                    size=10, color=robot["motivation"], colorscale="RdYlGn",
                    cmin=0, cmax=1, opacity=0.7, line=dict(width=0.5, color="gray"),
                ),
                showlegend=False,
                hovertext=f"Robot {robot['robot_id']}: m={robot['motivation']:.2f}",
            ))

    fig.update_layout(
        title="Individual Robot: Freedom vs Deliveries",
        xaxis_title="Freedom (1 - constrained)", yaxis_title="Deliveries",
        height=400, margin=dict(l=60, r=20, t=50, b=50),
    )
    return fig
