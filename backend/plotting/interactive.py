"""Interactive Plotly charts for Streamlit UI."""

import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from typing import List, Dict, Optional


def scatter_performance_autonomy(
    d_sos_results: list,
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

    if d_sos_results:
        _add_region(d_sos_results, "rgb(70,130,180)", "D-SoS")
        x = [r.avg_autonomy for r in d_sos_results]
        y = [r.throughput for r in d_sos_results]
        fig.add_trace(go.Scatter(
            x=x, y=y, mode="markers", name="D-SoS baseline",
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

    # Arrow from the centre of A to the centre of B.
    if results_a and results_b:
        ca = compute_region(results_a).centroid
        cb = compute_region(results_b).centroid
        if abs(cb[0] - ca[0]) > 0.02 or abs(cb[1] - ca[1]) > 0.5:
            fig.add_annotation(
                x=cb[0], y=cb[1], ax=ca[0], ay=ca[1],
                xref="x", yref="y", axref="x", ayref="y",
                showarrow=True, arrowhead=3, arrowsize=1.2, arrowwidth=1.5,
                arrowcolor="gray", opacity=0.9, text="",
            )

    fig.update_layout(
        xaxis_title="System autonomy (0-1)",
        yaxis_title="Throughput (total deliveries)",
        xaxis=dict(range=[-0.05, 1.05]), height=420,
        margin=dict(l=60, r=20, t=20, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    return fig


def line_rho_effects(
    sweep_results: list,
    current_rho: Optional[float] = None,
    highlight_profile: Optional[str] = None,
) -> go.Figure:
    """rho vs throughput / autonomy / fairness.

    With ``highlight_profile`` that profile is drawn in the design-B colour
    with error bars and the others recede to grey; otherwise each profile
    gets its own colour.
    """
    fig = make_subplots(rows=1, cols=3, subplot_titles=["Throughput", "Autonomy", "Fairness"])

    profiles = sorted(set(r.motivation_profile for r in sweep_results))
    colors = {"uniform": "steelblue", "linear": "seagreen", "polarized": "crimson"}
    dashes = {"uniform": "solid", "linear": "dash", "polarized": "dot"}
    # Draw the highlighted profile last so it sits on top.
    profiles.sort(key=lambda p: p == highlight_profile)

    for profile in profiles:
        subset = [r for r in sweep_results if r.motivation_profile == profile]
        rho_vals = sorted(set(r.rho for r in subset))
        highlighted = profile == highlight_profile
        if highlight_profile is None:
            color, dash, width = colors.get(profile, "gray"), "solid", 2
        elif highlighted:
            color, dash, width = COLOR_B, "solid", 3
        else:
            color, dash, width = COLOR_REF, dashes.get(profile, "dash"), 1.5

        for col, metric in enumerate(["throughput", "avg_autonomy", "fairness"], 1):
            means, stds = [], []
            for rho in rho_vals:
                vals = [getattr(r, metric) for r in subset if r.rho == rho]
                means.append(np.mean(vals) if vals else 0)
                stds.append(np.std(vals) if vals else 0)

            fig.add_trace(go.Scatter(
                x=rho_vals, y=means, mode="lines+markers",
                name=f"{profile} (B)" if highlighted else profile,
                legendgroup=profile, showlegend=(col == 1),
                line=dict(color=color, dash=dash, width=width),
                marker=dict(color=color, size=7 if highlighted else 5),
                error_y=dict(
                    type="data", array=stds, color=color,
                    visible=highlighted or highlight_profile is None,
                ),
            ), row=1, col=col)

    for col in (1, 2, 3):
        fig.update_xaxes(title_text="ρ", row=1, col=col)
        if current_rho is not None:
            fig.add_vline(x=current_rho, line_dash="dot", line_color="gray", row=1, col=col)
    fig.update_layout(
        height=350, margin=dict(l=50, r=20, t=50, b=50),
        legend=dict(orientation="h", yanchor="bottom", y=1.12, x=0),
    )
    return fig


def per_robot_ab(
    results_a: list, results_b: list, label_a: str = "A", label_b: str = "B",
) -> go.Figure:
    """Per-robot deliveries and freedom for two designs (mean ± std over seeds)."""
    fig = make_subplots(
        rows=1, cols=2,
        subplot_titles=["Deliveries per robot", "Freedom per robot (0–1)"],
    )

    for results, label, color in [(results_a, label_a, COLOR_A), (results_b, label_b, COLOR_B)]:
        if not results:
            continue
        robot_ids = sorted({rb["robot_id"] for r in results for rb in r.per_robot})
        rows = {
            rid: [rb for r in results for rb in r.per_robot if rb["robot_id"] == rid]
            for rid in robot_ids
        }
        x = [f"R{rid}" for rid in robot_ids]
        motivation = [rows[rid][0]["motivation"] for rid in robot_ids]
        for col, field in enumerate(["deliveries", "freedom"], 1):
            fig.add_trace(go.Bar(
                x=x,
                y=[float(np.mean([rb[field] for rb in rows[rid]])) for rid in robot_ids],
                error_y=dict(
                    type="data", thickness=1,
                    array=[float(np.std([rb[field] for rb in rows[rid]])) for rid in robot_ids],
                ),
                name=label, legendgroup=label, showlegend=(col == 1),
                marker_color=color, customdata=motivation,
                hovertemplate="%{x} · motivation %{customdata:.2f}<br>%{y:.2f}<extra>" + label + "</extra>",
            ), row=1, col=col)

    fig.update_yaxes(range=[0, 1], row=1, col=2)
    fig.update_layout(
        barmode="group", height=360, margin=dict(l=50, r=20, t=50, b=40),
        legend=dict(orientation="h", yanchor="bottom", y=1.12, x=0),
    )
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
