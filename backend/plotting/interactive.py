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
    show_regions: bool = True,
) -> go.Figure:
    """Performance-autonomy scatter with feasible regions (convex hull overlay)."""
    from backend.evaluation.region_analysis import compute_region

    fig = go.Figure()

    def _add_region(results, color, name):
        if not results or not show_regions:
            return
        region = compute_region(results, name)
        if len(region.hull_vertices) >= 3:
            hx = [v[0] for v in region.hull_vertices] + [region.hull_vertices[0][0]]
            hy = [v[1] for v in region.hull_vertices] + [region.hull_vertices[0][1]]
            fig.add_trace(go.Scatter(
                x=hx, y=hy, mode="lines", fill="toself",
                fillcolor=color.replace(")", ",0.1)").replace("rgb", "rgba"),
                line=dict(color=color, width=1.5, dash="dash"),
                name=f"{name} region", showlegend=True,
            ))

    if a_sos_results:
        _add_region(a_sos_results, "rgb(70,130,180)", "A-SoS")
        x = [r.avg_autonomy for r in a_sos_results]
        y = [r.throughput for r in a_sos_results]
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="markers", name="A-SoS baseline",
            marker=dict(color="steelblue", size=6, opacity=0.5),
        ))

    if c_sos_results:
        _add_region(c_sos_results, "rgb(255,140,0)", "C-SoS")
        x = [r.avg_autonomy for r in c_sos_results]
        y = [r.throughput for r in c_sos_results]
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="markers", name="C-SoS",
            marker=dict(color="darkorange", size=6, opacity=0.5),
        ))

    if selected_results:
        _add_region(selected_results, "rgb(220,20,60)", "Selected")
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


COLOR_A = "rgb(70,130,180)"
COLOR_B = "rgb(255,140,0)"
COLOR_REF = "rgb(150,150,150)"


def scatter_ab(
    results_a: list,
    results_b: list,
    label_a: str = "A",
    label_b: str = "B",
    references: Optional[Dict[str, list]] = None,
) -> go.Figure:
    """Performance-autonomy plane for two designs, with optional grey reference regions."""
    from backend.evaluation.region_analysis import compute_region

    fig = go.Figure()

    def _rgba(color, alpha):
        return color.replace(")", f",{alpha})").replace("rgb", "rgba")

    def _add(results, color, name, symbol, size, hull_only=False):
        if not results:
            return
        region = compute_region(results, name)
        if len(region.hull_vertices) >= 3:
            hx = [v[0] for v in region.hull_vertices] + [region.hull_vertices[0][0]]
            hy = [v[1] for v in region.hull_vertices] + [region.hull_vertices[0][1]]
            fig.add_trace(go.Scatter(
                x=hx, y=hy, mode="lines", fill="toself",
                fillcolor=_rgba(color, 0.12),
                line=dict(color=color, width=1.5, dash="dash"),
                name=name if hull_only else f"{name} region",
                showlegend=hull_only, hoverinfo="skip",
            ))
        if hull_only:
            return
        fig.add_trace(go.Scatter(
            x=[r.avg_autonomy for r in results],
            y=[r.throughput for r in results],
            mode="markers", name=name,
            marker=dict(color=color, size=size, symbol=symbol, opacity=0.8),
            hovertemplate="seed %{customdata}<br>autonomy %{x:.2f}<br>throughput %{y:.0f}<extra>" + name + "</extra>",
            customdata=[r.seed for r in results],
        ))

    for ref_name, ref_results in (references or {}).items():
        _add(ref_results, COLOR_REF, ref_name, "circle", 6, hull_only=True)
    _add(results_a, COLOR_A, label_a, "circle", 9)
    _add(results_b, COLOR_B, label_b, "diamond", 10)

    fig.update_layout(
        xaxis_title="System autonomy (0-1)",
        yaxis_title="Throughput (total deliveries)",
        xaxis=dict(range=[-0.05, 1.05]), height=420,
        margin=dict(l=60, r=20, t=20, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig


def line_rho_effects(sweep_results: list, current_rho: Optional[float] = None) -> go.Figure:
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
    if current_rho is not None:
        fig.add_vline(x=current_rho, line_dash="dot", line_color="gray")
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
    first = True
    for r in results[:5]:
        for robot in r.per_robot:
            fig.add_trace(go.Scatter(
                x=[robot["freedom"]], y=[robot["deliveries"]], mode="markers",
                marker=dict(
                    size=10, color=robot["motivation"], colorscale="RdYlGn",
                    cmin=0, cmax=1, opacity=0.7, line=dict(width=0.5, color="gray"),
                    showscale=first, colorbar=dict(title="motivation"),
                ),
                showlegend=False,
                hovertext=f"Robot {robot['robot_id']}: m={robot['motivation']:.2f}",
            ))
            first = False

    fig.update_layout(
        title="Individual Robot: Freedom vs Deliveries",
        xaxis_title="Freedom (1 - constrained)", yaxis_title="Deliveries",
        height=400, margin=dict(l=60, r=20, t=50, b=50),
    )
    return fig
