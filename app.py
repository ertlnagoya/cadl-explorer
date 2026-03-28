"""
Governance Pipeline Demo — Streamlit App

Visualizes: CADL diff → IR diff → Config diff → Experiment result diff → Governance evaluation
"""

import streamlit as st
import time
import json
import sys
import os

sys.path.insert(0, os.path.dirname(__file__))

from backend.services.cadl_service import (
    make_config, make_baseline_config, config_to_yaml_str,
    build_ir, ir_to_json_str, generate_unity_config_dict, unity_config_to_json_str,
    TEMPLATES,
)
from backend.services.diff_service import (
    compute_cadl_diff, compute_ir_diff, compute_config_diff, tagged_lines_to_html,
    semantic_diff_cadl, semantic_diff_ir, semantic_diff_config, semantic_diff_result,
    SemanticDiffResult,
)
from backend.services.experiment_service import run_sweep, run_comparison_sweep
from backend.services.evaluation_service import evaluate, evaluate_full, generate_summary
from backend.services.pipeline import run_pipeline, compare_pipelines
from backend.plotting.interactive import (
    scatter_performance_autonomy, line_rho_effects,
    bar_comparison, individual_robot_scatter,
)

st.set_page_config(page_title="Governance Pipeline Demo", layout="wide")


# ── Helper: Service SVG ─────────────────────────────────────────────

def _build_service_svg(template_name: str) -> str:
    positions = {
        0: (100, 200), 1: (200, 100), 2: (350, 80),
        3: (500, 100), 4: (600, 200), 5: (550, 320),
        6: (400, 380), 7: (200, 350), 8: (250, 230),
        9: (450, 300), 10: (500, 140),
    }
    edges = [
        (0,1),(1,2),(2,3),(3,4),(4,5),(5,6),(6,7),(7,0),
        (7,8),(0,8),(1,8),(8,9),(6,9),(5,9),(4,10),(3,10),(2,10),
    ]

    svg = [
        '<svg width="700" height="450" style="background:#fafafa;border:1px solid #e0e0e0;border-radius:8px;">',
    ]

    for s, d in edges:
        x1, y1 = positions[s]
        x2, y2 = positions[d]
        svg.append(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="#bbb" stroke-width="2"/>')

    for nid, (x, y) in positions.items():
        svg.append(
            f'<circle cx="{x}" cy="{y}" r="16" fill="#e3f2fd" stroke="#1976d2" stroke-width="2"/>'
            f'<text x="{x}" y="{y+5}" text-anchor="middle" font-size="12" fill="#333">{nid}</text>'
        )

    robot_nodes = [0, 2, 4, 6, 8]
    for i, nid in enumerate(robot_nodes):
        x, y = positions[nid]
        rx, ry = x + 25, y - 20
        svg.append(
            f'<rect x="{rx-10}" y="{ry-10}" width="20" height="20" rx="4" fill="#4caf50" stroke="#2e7d32" stroke-width="1.5"/>'
            f'<text x="{rx}" y="{ry+4}" text-anchor="middle" font-size="9" fill="white" font-weight="bold">R{i}</text>'
        )

    is_central = "A-SoS" in template_name
    arb_x, arb_y = 350, 430
    color = "#f44336" if is_central else "#ff9800"
    label = "Arbitrator (central)" if is_central else "Arbitrator (verifier)"
    svg.append(
        f'<rect x="{arb_x-60}" y="{arb_y-15}" width="120" height="30" rx="6" fill="{color}" stroke="#b71c1c" stroke-width="1.5"/>'
        f'<text x="{arb_x}" y="{arb_y+5}" text-anchor="middle" font-size="11" fill="white">{label}</text>'
    )

    svg.append(f'<text x="10" y="20" font-size="13" fill="#333" font-weight="bold">{template_name}</text>')
    svg.append("</svg>")
    return "\n".join(svg)


# ── CSS ─────────────────────────────────────────────────────────────
st.markdown("""
<style>
    .diff-container { max-height: 500px; overflow-y: auto; border: 1px solid #e0e0e0; border-radius: 4px; }
</style>
""", unsafe_allow_html=True)

# ── Sidebar ─────────────────────────────────────────────────────────
st.sidebar.title("Governance Pipeline Demo")

service = st.sidebar.selectbox(
    "Service",
    ["Robotaxi (5-agent fleet)", "Delivery Robot (5-agent fleet)"],
)

template = st.sidebar.radio(
    "Governance Template",
    list(TEMPLATES.keys()),
    index=2,
)

profile = st.sidebar.radio(
    "Motivation Profile",
    ["uniform", "linear", "polarized"],
    index=1,
)

rho = st.sidebar.slider("rho (motivation sensitivity)", 0.0, 1.0, 0.5, 0.05)

st.sidebar.markdown("---")
run_pipeline = st.sidebar.button(
    "Run Governance Pipeline Demo", type="primary", use_container_width=True,
)


# ── Build configs (always computed from current sidebar params) ─────
baseline = make_baseline_config()
selected = make_config(template, profile, rho)

baseline_ir = build_ir(baseline)
selected_ir = build_ir(selected)

baseline_unity = generate_unity_config_dict(baseline)
selected_unity = generate_unity_config_dict(selected)


# ── Main content ────────────────────────────────────────────────────
st.title("CADL -> IR -> Config -> Results -> Governance")
st.caption(f"Comparing **A-SoS baseline** vs **{selected.name}**")

# Pipeline animation (only when button pressed)
if run_pipeline:
    stage_labels = ["1. CADL diff", "2. IR diff", "3. Config diff", "4. Experiment", "5. Evaluation"]
    progress = st.progress(0, text="Running governance pipeline...")
    for i, label in enumerate(stage_labels, 1):
        progress.progress(i * 20, text=f"Step {i}/5: {label}")
        time.sleep(0.3)
    progress.progress(100, text="Pipeline complete")
    time.sleep(0.5)
    progress.empty()

# ── Run pipelines ──────────────────────────────────────────────────
pipeline_baseline = run_pipeline(template="A-SoS", profile="uniform", rho=0.0, num_seeds=10)
pipeline_selected = run_pipeline(template=template, profile=profile, rho=rho, num_seeds=10)

# ── Tabs (all always visible) ──────────────────────────────────────
tab_chain, tab_service, tab_cadl, tab_config, tab_results = st.tabs([
    "Causal Chain",
    "Service View",
    "CADL / IR Diff",
    "Simulator Config Diff",
    "Results & Evaluation",
])

# ── Tab 0: Causal Chain ────────────────────────────────────────────
with tab_chain:
    st.subheader("CADL -> IR -> Config -> Result : Causal Traceability")

    # Compute semantic diffs for the full chain
    sd_cadl = semantic_diff_cadl(
        pipeline_baseline.cadl, pipeline_selected.cadl,
        pipeline_baseline.name, pipeline_selected.name,
    )
    sd_ir = semantic_diff_ir(
        pipeline_baseline.ir, pipeline_selected.ir,
        pipeline_baseline.name, pipeline_selected.name,
    )
    sd_config = semantic_diff_config(
        pipeline_baseline.config, pipeline_selected.config,
        pipeline_baseline.name, pipeline_selected.name,
    )
    sd_result = semantic_diff_result(
        pipeline_baseline.evaluation.to_dict() if pipeline_baseline.evaluation else {},
        pipeline_selected.evaluation.to_dict() if pipeline_selected.evaluation else {},
        pipeline_baseline.name, pipeline_selected.name,
    )

    # Display chain as 4 stages
    stages = [
        ("1. CADL (Institution Design)", sd_cadl),
        ("2. IR (Governance Structure)", sd_ir),
        ("3. Config (Execution Settings)", sd_config),
        ("4. Result (Behavioral Outcome)", sd_result),
    ]

    for title, sd in stages:
        with st.expander(title, expanded=True):
            st.markdown(sd.to_html(), unsafe_allow_html=True)

    # Overall pipeline summary
    st.markdown("---")
    st.markdown("### Pipeline Summary")
    all_labels = []
    for _, sd in stages:
        all_labels.extend(sd.labels)

    if all_labels:
        for lbl in all_labels:
            icon = {"increased": "+", "decreased": "-", "changed": "~"}.get(lbl.direction, "?")
            st.markdown(f"- **[{icon}] {lbl.category}**: {lbl.summary}")
    else:
        st.info("No differences detected — try changing the governance template or rho.")


# ── Tab 1: Service View ────────────────────────────────────────────
with tab_service:
    st.subheader(service)
    st.markdown(f"**Governance**: {template} | **Profile**: {profile} | **rho** = {rho}")

    st.markdown(_build_service_svg(template), unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Baseline (A-SoS)**")
        st.code(config_to_yaml_str(baseline), language="yaml")
    with col2:
        st.markdown(f"**Selected ({template})**")
        st.code(config_to_yaml_str(selected), language="yaml")


# ── Tab 2: CADL / IR Diff ──────────────────────────────────────────
with tab_cadl:
    cadl_tab, l1_tab, l2_tab, l3_tab = st.tabs([
        "CADL", "Layer 1: Institution", "Layer 2: Protocol", "Layer 3: Algorithm",
    ])

    with cadl_tab:
        st.subheader("CADL YAML Diff")
        diff_lines = compute_cadl_diff(baseline, selected)
        if diff_lines:
            st.markdown(
                f'<div class="diff-container">{tagged_lines_to_html(diff_lines)}</div>',
                unsafe_allow_html=True,
            )
        else:
            st.info("Configs are identical — try changing the governance template or rho.")

    ir_diffs = compute_ir_diff(baseline_ir, selected_ir)
    for tab_obj, layer_name in zip(
        [l1_tab, l2_tab, l3_tab],
        ["Layer 1: Institution", "Layer 2: Protocol", "Layer 3: Algorithm"],
    ):
        with tab_obj:
            st.subheader(f"{layer_name} Diff")
            layer_diff = ir_diffs.get(layer_name, [])
            if layer_diff:
                st.markdown(
                    f'<div class="diff-container">{tagged_lines_to_html(layer_diff)}</div>',
                    unsafe_allow_html=True,
                )
            else:
                st.info("No differences in this layer.")


# ── Tab 3: Config Diff ─────────────────────────────────────────────
with tab_config:
    st.subheader("Unity cadl_config.json Diff")
    config_diff = compute_config_diff(
        baseline_unity, selected_unity, baseline.name, selected.name,
    )
    if config_diff:
        st.markdown(
            f'<div class="diff-container">{tagged_lines_to_html(config_diff)}</div>',
            unsafe_allow_html=True,
        )
    else:
        st.info("Configs are identical.")

    st.markdown("### Key Config Fields")
    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Baseline**")
        st.json({
            "sosType": baseline_unity.get("simulatorConfig", {}).get("sosType"),
            "governance": baseline_unity.get("communicationSetup", {}).get("governance"),
            "motivationConfig": baseline_unity.get("motivationConfig"),
        })
    with col2:
        st.markdown("**Selected**")
        st.json({
            "sosType": selected_unity.get("simulatorConfig", {}).get("sosType"),
            "governance": selected_unity.get("communicationSetup", {}).get("governance"),
            "motivationConfig": selected_unity.get("motivationConfig"),
        })


# ── Tab 4: Results & Evaluation ─────────────────────────────────────
with tab_results:
    rho_values = [0.0, 0.25, 0.5, 0.75, 1.0]
    if rho not in rho_values:
        rho_values = sorted(set(rho_values + [rho]))

    with st.spinner("Running synthetic experiments..."):
        comparison = run_comparison_sweep(profile, rho_values, num_seeds=10)
        a_sos_results = comparison["a_sos"]
        c_sos_results = comparison["c_sos"]

        selected_results = (
            [r for r in a_sos_results if abs(r.rho - rho) < 0.01 and r.motivation_profile == profile]
            if selected.sos_type == "directed"
            else c_sos_results
        )

    res_scatter, res_rho, res_fair, res_summary = st.tabs([
        "Scatter", "rho Effects", "Per-Robot", "Summary",
    ])

    with res_scatter:
        fig = scatter_performance_autonomy(
            [r for r in a_sos_results if r.rho == 0.0],
            c_sos_results,
            selected_results,
            selected.name,
        )
        st.plotly_chart(fig, use_container_width=True)

    with res_rho:
        all_sweep = []
        for prof in ["uniform", "linear", "polarized"]:
            all_sweep.extend(run_sweep("directed", prof, rho_values, num_seeds=10))
        fig = line_rho_effects(all_sweep)
        st.plotly_chart(fig, use_container_width=True)

    with res_fair:
        fig = individual_robot_scatter(selected_results)
        st.plotly_chart(fig, use_container_width=True)

    with res_summary:
        eval_base = evaluate([r for r in a_sos_results if r.rho == 0.0])
        eval_sel = evaluate(selected_results)

        col1, col2, col3 = st.columns(3)
        col1.metric(
            "Throughput", f"{eval_sel['throughput']:.1f}",
            f"{eval_sel['throughput'] - eval_base['throughput']:+.1f}",
        )
        col2.metric(
            "Autonomy", f"{eval_sel['autonomy']:.2f}",
            f"{eval_sel['autonomy'] - eval_base['autonomy']:+.2f}",
        )
        col3.metric(
            "Fairness", f"{eval_sel['fairness']:.2f}",
            f"{eval_sel['fairness'] - eval_base['fairness']:+.2f}",
        )

        fig = bar_comparison(eval_base, eval_sel, "A-SoS baseline", selected.name)
        st.plotly_chart(fig, use_container_width=True)

        summary_text = generate_summary(
            [r for r in a_sos_results if r.rho == 0.0],
            selected_results,
            "A-SoS baseline",
            selected.name,
        )
        st.markdown(summary_text)
