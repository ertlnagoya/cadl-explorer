"""
Governance Pipeline Demo — Streamlit App

Visualizes: CADL diff → IR diff → Config diff → Experiment result diff → Governance evaluation
"""

import streamlit as st
import time
import json
import sys
import os
import yaml
import hashlib
from datetime import datetime


def _config_hash(config) -> str:
    """Short stable hash of a CADLMotivationConfig for reproducibility."""
    raw = json.dumps(config.to_dict(), sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:10]

sys.path.insert(0, os.path.dirname(__file__))

from backend.services.cadl_service import (
    make_config, make_baseline_config, config_to_yaml_str,
    build_ir, ir_to_json_str, generate_unity_config_dict, unity_config_to_json_str,
    parse_cadl_yaml,
    TEMPLATES, MAX_SOURCE_CHARS,
)
from cadl_sim.schema.motivation_schema import CADLMotivationConfig
from backend.services.diff_service import (
    compute_cadl_diff, compute_ir_diff, compute_config_diff, tagged_lines_to_html,
    semantic_diff_cadl, semantic_diff_ir, semantic_diff_config, semantic_diff_result,
    SemanticDiffResult,
)
from backend.services.experiment_service import run_sweep, run_comparison_sweep
from backend.services.evaluation_service import evaluate, evaluate_full, generate_summary
from backend.services.pipeline import run_pipeline, compare_pipelines, ComparisonResult
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
    help=(
        "Target application domain. Both services share the same 5-agent "
        "fleet topology; the choice affects labelling and the service-view "
        "diagram only."
    ),
)

template = st.sidebar.radio(
    "Governance Template",
    list(TEMPLATES.keys()),
    index=2,
    help=(
        "Preset combination of the governance parameters α, β, λ:\n\n"
        "• **A-SoS** (directed, α=0.3, β=0.7, λ=0.0): strong central authority.\n"
        "• **C-SoS** (collaborative, α=0.7, β=0.3, λ=0.3): autonomy-oriented.\n"
        "• **A-SoS + motivation-sensitive** (directed, α=0.3, β=0.7, "
        "λ=0.0, hybrid model): central authority that adjusts budgets based "
        "on agent motivation.\n\n"
        "α = autonomy level, β = centralization level, λ = exploration "
        "probability. These are simulator parameters; they differ from the "
        "per-contract α/β/λ of the CADL language specification."
    ),
)

profile = st.sidebar.radio(
    "Motivation Profile",
    ["uniform", "linear", "polarized"],
    index=1,
    help=(
        "Distribution of agent motivation levels across the fleet.\n\n"
        "• **uniform** — all agents equally motivated.\n"
        "• **linear** — motivation increases linearly across agents.\n"
        "• **polarized** — bimodal split between low- and high-motivation "
        "agents."
    ),
)

rho = st.sidebar.slider(
    "ρ (motivation sensitivity)",
    0.0, 1.0, 0.5, 0.05,
    help=(
        "How strongly governance decisions respond to agent motivation. "
        "ρ=0 ignores motivation entirely (equivalent to a fixed template); "
        "ρ=1 fully couples budget/wait decisions to each agent's motivation "
        "score. Effective only when the template uses a motivation model."
    ),
)

# ── Custom CADL input (optional) ────────────────────────────────────
with st.sidebar.expander("Advanced: Custom CADL YAML"):
    st.caption(
        "Paste a CADL motivation-config YAML to override the template above. "
        "Leave empty to use the sidebar selections."
    )
    custom_cadl_text = st.text_area(
        "CADL YAML",
        value="",
        height=200,
        max_chars=MAX_SOURCE_CHARS,
        label_visibility="collapsed",
        placeholder=(
            "name: my-custom-config\n"
            "sos_type: directed\n"
            "governance:\n"
            "  alpha: 0.3\n"
            "  beta: 0.7\n"
            "  lambda: 0.0\n"
            "motivation:\n"
            "  agent:\n"
            "    profile: linear\n"
            "  governance:\n"
            "    model: hybrid\n"
            "    rho: 0.5\n"
        ),
    )

st.sidebar.markdown("---")
btn_run_pipeline = st.sidebar.button(
    "Run Governance Pipeline Demo", type="primary", use_container_width=True,
)


# ── Build configs (always computed from current sidebar params) ─────
baseline = make_baseline_config()

_custom_config = None
if custom_cadl_text.strip():
    try:
        # Route through the parser seam so the upstream `cadl` package can
        # later replace this path without touching the UI.
        _custom_config = parse_cadl_yaml(custom_cadl_text)
    except yaml.YAMLError as _e:
        # Surface line/column from PyYAML if available.
        _mark = getattr(_e, "problem_mark", None)
        if _mark is not None:
            st.sidebar.error(
                f"YAML syntax error at line {_mark.line + 1}, "
                f"column {_mark.column + 1}: {getattr(_e, 'problem', str(_e))}"
            )
        else:
            st.sidebar.error(f"YAML syntax error: {_e}")
    except ValueError as _e:
        # Schema validation errors already include field path.
        st.sidebar.error(f"Invalid CADL config: {_e}")
    except Exception as _e:
        st.sidebar.error(
            f"Could not build CADL config ({type(_e).__name__}): {_e}"
        )
    else:
        st.sidebar.success(
            f"Using custom CADL: {_custom_config.name} "
            f"(hash: {_config_hash(_custom_config)})"
        )

selected = _custom_config if _custom_config is not None else make_config(template, profile, rho)

# When a custom CADL is active, drive the experiment runner using its
# profile / rho (and pick the nearest built-in template by sos_type).
if _custom_config is not None:
    _pipeline_template = next(
        (name for name, t in TEMPLATES.items() if t["sos_type"] == _custom_config.sos_type),
        template,
    )
    _pipeline_profile = _custom_config.agent_motivation.profile
    _pipeline_rho = float(_custom_config.governance_motivation.rho)
else:
    _pipeline_template = template
    _pipeline_profile = profile
    _pipeline_rho = float(rho)

baseline_ir = build_ir(baseline)
selected_ir = build_ir(selected)

baseline_unity = generate_unity_config_dict(baseline)
selected_unity = generate_unity_config_dict(selected)


# ── Main content ────────────────────────────────────────────────────
st.title("CADL -> IR -> Config -> Results -> Governance")
st.caption(f"Comparing **A-SoS baseline** vs **{selected.name}**")

# Pipeline animation (only when button pressed)
if btn_run_pipeline:
    stage_labels = ["1. CADL diff", "2. IR diff", "3. Config diff", "4. Experiment", "5. Evaluation"]
    progress = st.progress(0, text="Running governance pipeline...")
    for i, label in enumerate(stage_labels, 1):
        progress.progress(i * 20, text=f"Step {i}/5: {label}")
        time.sleep(0.3)
    progress.progress(100, text="Pipeline complete")
    time.sleep(0.5)
    progress.empty()

# ── Run pipelines ──────────────────────────────────────────────────
@st.cache_data
def cached_run_pipeline(template, profile, rho, num_seeds=5):
    return run_pipeline(template=template, profile=profile, rho=rho, num_seeds=num_seeds)

try:
    pipeline_baseline = cached_run_pipeline("A-SoS", "uniform", 0.0)
    pipeline_selected = cached_run_pipeline(_pipeline_template, _pipeline_profile, _pipeline_rho)
except Exception as e:
    import traceback
    st.error(
        f"**Pipeline failed** ({type(e).__name__}): {e}\n\n"
        "This is usually caused by an invalid combination of template, "
        "motivation profile, and ρ. Try a different template or reset ρ "
        "to 0.5, or press **Clear history** to drop stale cached runs."
    )
    with st.expander("Stack trace (for bug reports)"):
        st.code(traceback.format_exc(), language="text")
    st.stop()

# ── Run history (session-scoped) ────────────────────────────────────
if "run_history" not in st.session_state:
    st.session_state.run_history = []

if btn_run_pipeline:
    st.session_state.run_history.append({
        "timestamp": datetime.now().strftime("%H:%M:%S"),
        "name": pipeline_selected.name,
        "template": _pipeline_template,
        "profile": _pipeline_profile,
        "rho": _pipeline_rho,
        "config_hash": _config_hash(selected),
        "num_seeds": len(pipeline_selected.seeds),
        "seeds": list(pipeline_selected.seeds),
        "throughput": pipeline_selected.evaluation.throughput.mean,
        "autonomy": pipeline_selected.evaluation.autonomy.mean,
        "fairness": pipeline_selected.evaluation.fairness.mean,
    })
    # cap at 20 entries
    st.session_state.run_history = st.session_state.run_history[-20:]

# ── Tabs (all always visible) ──────────────────────────────────────
tab_chain, tab_service, tab_cadl, tab_config, tab_results, tab_history = st.tabs([
    "Causal Chain",
    "Service View",
    "CADL / IR Diff",
    "Simulator Config Diff",
    "Results & Evaluation",
    "Run History",
])

# ── Tab 0: Causal Chain ────────────────────────────────────────────
with tab_chain:
    st.subheader("CADL -> IR -> Config -> Result : Causal Traceability")

    # Traceability IDs
    col_id1, col_id2 = st.columns(2)
    _baseline_hash = _config_hash(baseline)
    _selected_hash = _config_hash(selected)
    with col_id1:
        st.caption(
            f"Baseline: `{pipeline_baseline.name}`  |  "
            f"cadl:`{pipeline_baseline.cadl_id}`  "
            f"ir:`{pipeline_baseline.ir_id}`  "
            f"hash:`{_baseline_hash}`  "
            f"seeds:`{list(pipeline_baseline.seeds)}`"
        )
    with col_id2:
        st.caption(
            f"Selected: `{pipeline_selected.name}`  |  "
            f"cadl:`{pipeline_selected.cadl_id}`  "
            f"ir:`{pipeline_selected.ir_id}`  "
            f"hash:`{_selected_hash}`  "
            f"seeds:`{list(pipeline_selected.seeds)}`"
        )
    st.caption(
        ":information_source: *`hash` is a SHA-256 fingerprint of the full "
        "CADL config — identical hash + identical seeds → identical results.*"
    )

    # Compare using ComparisonResult
    comparison = compare_pipelines(pipeline_baseline, pipeline_selected)

    # Display chain as 4 stages
    stages = [
        ("1. CADL (Institution Design)", comparison.cadl),
        ("2. IR (Governance Structure)", comparison.ir),
        ("3. Config (Execution Settings)", comparison.config),
        ("4. Result (Behavioral Outcome)", comparison.result),
    ]

    for title, sd in stages:
        with st.expander(title, expanded=True):
            st.markdown(sd.to_html(), unsafe_allow_html=True)

    # Region analysis
    from backend.evaluation.region_analysis import compute_region, compare_regions
    region_base = compute_region(pipeline_baseline.results, "Baseline")
    region_sel = compute_region(pipeline_selected.results, "Selected")
    region_cmp = compare_regions(region_base, region_sel)
    if region_cmp["interpretation"]:
        with st.expander("5. Region Shift (Performance-Autonomy Plane)", expanded=True):
            st.markdown(f"**{region_cmp['summary']}**")
            col_r1, col_r2 = st.columns(2)
            col_r1.metric("Baseline area", f"{region_base.area:.2f}")
            col_r2.metric("Selected area", f"{region_sel.area:.2f}", f"{region_cmp['area_ratio']:.1f}x")

    # Overall pipeline summary
    st.markdown("---")
    st.markdown("### Pipeline Summary")
    all_labels = comparison.all_labels

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


# ── Tab 5: Run History ──────────────────────────────────────────────
with tab_history:
    st.subheader("Run History (this session)")
    st.caption(
        "Each press of **Run Governance Pipeline Demo** is recorded below. "
        "Use this to compare how throughput / autonomy / fairness evolve as "
        "you change governance parameters."
    )

    history = st.session_state.get("run_history", [])
    if not history:
        st.info(
            "No runs yet. Press **Run Governance Pipeline Demo** in the "
            "sidebar to record entries here."
        )
    else:
        import pandas as pd
        df = pd.DataFrame(history)
        st.dataframe(df, use_container_width=True, hide_index=True)

        if len(history) >= 2:
            st.markdown("#### Metric trajectory")
            import plotly.graph_objects as go
            fig = go.Figure()
            x = list(range(1, len(history) + 1))
            for metric in ["throughput", "autonomy", "fairness"]:
                fig.add_trace(go.Scatter(
                    x=x,
                    y=[h[metric] for h in history],
                    mode="lines+markers",
                    name=metric,
                ))
            fig.update_layout(
                xaxis_title="Run #",
                yaxis_title="Value",
                height=350,
                margin=dict(l=10, r=10, t=30, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)

            st.markdown("#### Pairwise comparison")
            col_a, col_b = st.columns(2)
            labels = [f"#{i+1}: {h['name']}" for i, h in enumerate(history)]
            with col_a:
                idx_a = st.selectbox("Run A", range(len(history)),
                                     format_func=lambda i: labels[i],
                                     index=max(0, len(history) - 2))
            with col_b:
                idx_b = st.selectbox("Run B", range(len(history)),
                                     format_func=lambda i: labels[i],
                                     index=len(history) - 1)
            if idx_a != idx_b:
                ha, hb = history[idx_a], history[idx_b]
                c1, c2, c3 = st.columns(3)
                c1.metric("Throughput", f"{hb['throughput']:.2f}",
                          f"{hb['throughput'] - ha['throughput']:+.2f}")
                c2.metric("Autonomy", f"{hb['autonomy']:.2f}",
                          f"{hb['autonomy'] - ha['autonomy']:+.2f}")
                c3.metric("Fairness", f"{hb['fairness']:.2f}",
                          f"{hb['fairness'] - ha['fairness']:+.2f}")

        st.markdown("#### Export")
        col_csv, col_json, col_clear = st.columns(3)
        with col_csv:
            st.download_button(
                "Download CSV",
                data=df.to_csv(index=False).encode("utf-8"),
                file_name="cadl_explorer_run_history.csv",
                mime="text/csv",
                use_container_width=True,
            )
        with col_json:
            st.download_button(
                "Download JSON",
                data=json.dumps(history, indent=2).encode("utf-8"),
                file_name="cadl_explorer_run_history.json",
                mime="application/json",
                use_container_width=True,
            )
        with col_clear:
            if st.button("Clear history", use_container_width=True):
                st.session_state.run_history = []
                st.rerun()
