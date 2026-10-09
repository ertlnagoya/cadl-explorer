"""Explorer page — compare two governance designs end to end.

Layout: outcome -> causal chain -> exploration -> reproducibility.
Every section reads from the same two PipelineResults, so the numbers
agree across the page.
"""

import json
from datetime import datetime

import streamlit as st
import yaml

from backend.services.cadl_service import (
    make_config, config_to_yaml_str, parse_cadl_yaml,
    TEMPLATES, MAX_SOURCE_CHARS,
)
from backend.services.experiment_service import run_sweep
from backend.services.pipeline import (
    run_pipeline, run_pipeline_for_config, compare_pipelines,
)
from backend.evaluation.region_analysis import compute_region, compare_regions
from backend.plotting.interactive import (
    scatter_ab, line_rho_effects, per_robot_ab,
)
from backend.plotting.scenario import scenario_svg
from views._guide import WHAT_YOU_CAN_DO, HOW_TO_USE, DEMOS

NUM_SEEDS = 10
SPEC_URL = "https://www.ertl.jp/cadl-spec/"
PROFILES = ["uniform", "linear", "polarized"]
SWEEP_RHO = [0.0, 0.25, 0.5, 0.75, 1.0]

# Short keys keep shared URLs readable.
TEMPLATE_KEYS = {"a": "A-SoS", "c": "C-SoS", "am": "A-SoS + motivation-sensitive"}
TEMPLATE_LABELS = {
    "A-SoS": "A-SoS — directed, central authority",
    "C-SoS": "C-SoS — collaborative, autonomy-oriented",
    "A-SoS + motivation-sensitive": "A-SoS + motivation-sensitive — directed, budgets follow motivation",
}
PROFILE_LABELS = {
    "uniform": "uniform — all agents equally motivated",
    "linear": "linear — motivation rises across the fleet",
    "polarized": "polarized — low and high groups",
}

DEFAULTS = {
    "a_template": "A-SoS", "a_profile": "uniform", "a_rho": 0.0,
    "b_template": "A-SoS + motivation-sensitive", "b_profile": "linear", "b_rho": 0.5,
}
EXAMPLES = {
    "Add motivation sensitivity": DEFAULTS,
    "Directed vs collaborative": {
        "a_template": "A-SoS", "a_profile": "uniform", "a_rho": 0.0,
        "b_template": "C-SoS", "b_profile": "uniform", "b_rho": 0.0,
    },
    "Weak vs strong sensitivity": {
        "a_template": "A-SoS + motivation-sensitive", "a_profile": "polarized", "a_rho": 0.25,
        "b_template": "A-SoS + motivation-sensitive", "b_profile": "polarized", "b_rho": 1.0,
    },
}


# ── State: defaults <- shared URL <- widgets ────────────────────────

def _init_state():
    if "explorer_init" in st.session_state:
        return
    st.session_state.explorer_init = True
    st.session_state.update(DEFAULTS)
    qp = st.query_params
    for side in ("a", "b"):
        if qp.get(side) in TEMPLATE_KEYS:
            st.session_state[f"{side}_template"] = TEMPLATE_KEYS[qp[side]]
        if qp.get(f"{side}p") in PROFILES:
            st.session_state[f"{side}_profile"] = qp[f"{side}p"]
        try:
            rho = float(qp.get(f"{side}r", ""))
        except ValueError:
            continue
        if 0.0 <= rho <= 1.0:
            st.session_state[f"{side}_rho"] = round(rho * 20) / 20


def _apply_example(name):
    st.session_state.update(EXAMPLES[name])


def _sync_url():
    keys = {v: k for k, v in TEMPLATE_KEYS.items()}
    ss = st.session_state
    st.query_params.from_dict({
        "a": keys[ss.a_template], "ap": ss.a_profile, "ar": f"{ss.a_rho:.2f}",
        "b": keys[ss.b_template], "bp": ss.b_profile, "br": f"{ss.b_rho:.2f}",
    })


def _design_controls(side: str, disabled: bool = False):
    """Template / profile / rho selectors for design `side` ("a" or "b")."""
    template = st.radio(
        "Governance template", list(TEMPLATES), key=f"{side}_template",
        format_func=TEMPLATE_LABELS.get, disabled=disabled,
        help=(
            "Preset of the simulator parameters α (autonomy level), "
            "β (centralization level) and λ (exploration probability). "
            "See **About & Glossary** for the values."
        ),
    )
    st.radio(
        "Motivation profile", PROFILES, key=f"{side}_profile",
        format_func=PROFILE_LABELS.get, disabled=disabled,
        help="Distribution of motivation levels across the 5 agents.",
    )
    rho_applies = TEMPLATES[template]["motivation_model"] != "none"
    st.slider(
        "ρ — motivation sensitivity", 0.0, 1.0, step=0.05, key=f"{side}_rho",
        disabled=disabled or not rho_applies,
        help=(
            "How strongly governance decisions respond to agent motivation: "
            "0 ignores it, 1 fully couples budget and wait decisions to it."
        ),
    )
    if not rho_applies:
        st.caption(
            "ρ is not used: this template has no motivation model. "
            "Pick *A-SoS + motivation-sensitive* to vary ρ."
        )
    elif st.session_state[f"{side}_rho"] == 0.0:
        st.caption("At ρ = 0 this template behaves exactly like plain A-SoS.")


# ── Cached computation ──────────────────────────────────────────────

@st.cache_data(show_spinner=False)
def cached_pipeline(template, profile, rho):
    return run_pipeline(template=template, profile=profile, rho=rho, num_seeds=NUM_SEEDS)


@st.cache_data(show_spinner=False)
def cached_pipeline_yaml(yaml_text):
    return run_pipeline_for_config(parse_cadl_yaml(yaml_text), num_seeds=NUM_SEEDS)


@st.cache_data(show_spinner=False)
def cached_sweep(sos_type, profile, rho_values):
    return run_sweep(sos_type, profile, list(rho_values), num_seeds=NUM_SEEDS)


def _parse_custom(text):
    """Return (config, error message) for the custom CADL text area."""
    try:
        return parse_cadl_yaml(text), None
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        if mark is not None:
            return None, (
                f"YAML syntax error at line {mark.line + 1}, "
                f"column {mark.column + 1}: {getattr(e, 'problem', str(e))}"
            )
        return None, f"YAML syntax error: {e}"
    except ValueError as e:
        return None, f"Invalid CADL config: {e}"
    except Exception as e:
        return None, f"Could not build CADL config ({type(e).__name__}): {e}"


def _is_dark() -> bool:
    theme = getattr(st.context, "theme", None)
    return getattr(theme, "type", None) == "dark"


def _raw_diff(sd) -> str:
    return "\n".join(line for _, line in sd.syntactic.tagged_lines)


# ── Sidebar ─────────────────────────────────────────────────────────

_init_state()

with st.sidebar:
    st.header("Compare two designs")

    st.caption("Start from an example")
    for example in EXAMPLES:
        st.button(example, on_click=_apply_example, args=(example,),
                  width="stretch")

    with st.expander("Advanced: custom CADL YAML for B"):
        st.caption(
            "Paste a CADL motivation-config YAML to use as design B. "
            "Leave empty to use the selectors below."
        )
        custom_text = st.text_area(
            "CADL YAML", height=200, max_chars=MAX_SOURCE_CHARS,
            label_visibility="collapsed", key="custom_cadl",
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

    custom_config, custom_error = (None, None)
    if custom_text.strip():
        custom_config, custom_error = _parse_custom(custom_text)
        if custom_error:
            st.error(custom_error)
        else:
            st.success(
                f"Design B is the custom CADL **{custom_config.name}**. "
                "The B selectors below are ignored."
            )

    st.subheader("B — design under study")
    _design_controls("b", disabled=custom_config is not None)

    with st.expander("A — baseline", expanded=False):
        _design_controls("a")

    st.divider()
    btn_save = st.button(
        "Save this comparison", type="primary", width="stretch",
        help="Adds the current A/B metrics to the saved comparisons at the bottom of the page.",
    )

_sync_url()
ss = st.session_state

# ── Run both pipelines (single source for every section) ────────────

try:
    pa = cached_pipeline(ss.a_template, ss.a_profile, float(ss.a_rho))
    if custom_config is not None:
        pb = cached_pipeline_yaml(custom_text)
    else:
        pb = cached_pipeline(ss.b_template, ss.b_profile, float(ss.b_rho))
except Exception as e:
    import traceback
    st.error(
        f"**Pipeline failed** ({type(e).__name__}): {e}\n\n"
        "The synthetic model supports the uniform, linear and polarized "
        "profiles with 5 agents. Adjust the design and try again."
    )
    with st.expander("Stack trace (for bug reports)"):
        st.code(traceback.format_exc(), language="text")
    st.stop()

comparison = compare_pipelines(pa, pb)
identical = pa.cadl_id == pb.cadl_id

# ── Header ──────────────────────────────────────────────────────────

st.title("CADL Explorer")
st.markdown(
    "Change a governance design and trace how the change propagates: "
    "**CADL** (institution) → **IR** (governance structure) → "
    "**Config** (simulator settings) → **Result** (behaviour)."
)
st.caption(
    f":material/science: Results come from a **synthetic model**, not from "
    f"measurements of a real system — {NUM_SEEDS} seeds per design. "
    f"[CADL specification and hands-on]({SPEC_URL}) · "
    "terms are explained in **About & Glossary**."
)

with st.expander("Getting started — what this tool does, how to use it, and a demo", expanded=True):
    col_what, col_how = st.columns(2, gap="large")
    with col_what:
        st.markdown("##### What you can do")
        st.markdown(WHAT_YOU_CAN_DO)
    with col_how:
        st.markdown("##### How to use it")
        st.markdown(HOW_TO_USE)

    st.markdown("##### Demo — three comparisons to try")
    st.caption(
        "Each button loads a ready-made A/B pair into the sidebar and "
        "updates the page below."
    )
    for col, (example, (compares, look_at)) in zip(st.columns(len(DEMOS)), DEMOS.items()):
        with col.container(border=True):
            st.markdown(f"**{example}**")
            st.markdown(compares)
            st.markdown(f"*What to look at:* {look_at}")
            st.button(
                "Load this demo", key=f"demo_{example}", width="stretch",
                on_click=_apply_example, args=(example,),
            )

# ── 1. Outcome ──────────────────────────────────────────────────────

st.header("1. Outcome", divider="gray")
st.markdown(f"**A** `{pa.name}`  →  **B** `{pb.name}`")

if identical:
    st.info(
        "A and B are the same design, so nothing changes. Pick a different "
        "template, profile or ρ for B, or start from an example in the sidebar."
    )

ea, eb = pa.evaluation, pb.evaluation
col_m, col_plot = st.columns([2, 3], gap="large")

with col_m:
    for label, ma, mb, fmt, help_text in [
        ("Throughput", ea.throughput, eb.throughput, ".1f",
         "Total deliveries completed by the fleet in one run."),
        ("System autonomy", ea.autonomy, eb.autonomy, ".2f",
         "Autonomy index (0–1); higher means agents are less constrained by the central authority."),
        ("Fairness", ea.fairness, eb.fairness, ".2f",
         "1 − normalised variance of per-robot deliveries; 1 is perfectly even."),
    ]:
        delta = mb.mean - ma.mean
        pct = f" ({delta / ma.mean:+.1%})" if abs(ma.mean) > 1e-9 else ""
        st.metric(
            f"{label} — B", f"{mb.mean:{fmt}} ± {mb.std:{fmt}}",
            f"{delta:+{fmt}}{pct} vs A ({ma.mean:{fmt}})",
            delta_color="off" if abs(delta) < 0.005 else "normal",
            help=help_text,
        )

    result_labels = comparison.result.labels
    if result_labels:
        st.markdown("**In short**")
        for lbl in result_labels:
            st.markdown(f"- {lbl.summary.replace('->', '→')}")
    elif not identical:
        st.markdown("**In short** — no notable change in behaviour.")

with col_plot:
    references = {}
    used = {pa.cadl.get("sos_type", "").lower(), pb.cadl.get("sos_type", "").lower()}
    if "directed" not in used:
        references["A-SoS reference"] = cached_pipeline("A-SoS", "uniform", 0.0).results
    if "collaborative" not in used:
        references["C-SoS reference"] = cached_pipeline("C-SoS", "uniform", 0.0).results
    st.plotly_chart(
        scatter_ab(pa.results, pb.results, "A", "B", references),
        width="stretch",
    )
    region_cmp = compare_regions(
        compute_region(pa.results, "A"), compute_region(pb.results, "B"),
    )
    st.caption(
        "Each point is one seed; the dashed outline is the region a design "
        "reaches and the arrow joins the centres of A and B. "
        f"From A to B: {region_cmp['summary']}."
    )

# ── 2. Why: causal chain ────────────────────────────────────────────

st.header("2. Why — the causal chain", divider="gray")
st.caption(
    "The same change seen at each stage of the pipeline. Interpreted "
    "changes come first; the raw diff is available under each stage."
)

STAGES = [
    ("CADL", "Institution design", "What the designer wrote.", comparison.cadl, "yaml"),
    ("IR", "Governance structure", "Who decides, how it is coordinated, what the planner does.", comparison.ir, "json"),
    ("Config", "Simulator settings", "What the simulator is told to execute.", comparison.config, "json"),
    ("Result", "Behaviour", "What the fleet ends up doing.", comparison.result, "json"),
]

cols = st.columns(len(STAGES))
for i, (col, (short, title, _, sd, _)) in enumerate(zip(cols, STAGES), 1):
    n = len(sd.labels)
    with col.container(border=True):
        st.markdown(f"**{i}. {short}**" + ("  →" if i < len(STAGES) else ""))
        st.caption(title)
        st.markdown(f"**{n}** change{'s' if n != 1 else ''}" if n else "no change")

for i, (short, title, blurb, sd, _) in enumerate(STAGES, 1):
    with st.container(border=True):
        st.markdown(f"#### {i}. {short} — {title}")
        st.caption(blurb)
        if not sd.labels:
            st.markdown("*No change at this stage.*")
        else:
            main = [l for l in sd.labels if l.magnitude != "minimal"]
            minor = [l for l in sd.labels if l.magnitude == "minimal"]
            for lbl in main:
                st.markdown(f"- **{lbl.category}** — {lbl.summary.replace('->', '→')}")
            if minor:
                st.caption("Also changed: " + ", ".join(l.field for l in minor))
        raw = _raw_diff(sd)
        if raw:
            with st.expander(f"Raw diff ({len(raw.splitlines())} lines)"):
                st.code(raw, language="diff")

# ── 3. Explore ──────────────────────────────────────────────────────

st.header("3. Explore", divider="gray")

b_sos_type = pb.cadl.get("sos_type", "").lower()
tab_rho, tab_robot, tab_scenario = st.tabs(["Effect of ρ", "Per-robot view", "Scenario"])

with tab_rho:
    if b_sos_type != "directed":
        st.info(
            "The ρ sweep applies to directed (A-SoS) designs. In the "
            "synthetic model a collaborative design does not depend on ρ."
        )
    else:
        sweep = []
        for prof in PROFILES:
            sweep.extend(cached_sweep("directed", prof, tuple(SWEEP_RHO)))
        st.plotly_chart(
            line_rho_effects(sweep, current_rho=pb.rho, highlight_profile=pb.profile),
            width="stretch",
        )
        st.caption(
            f"Directed design swept over ρ (mean ± std over {NUM_SEEDS} seeds). "
            f"The highlighted line is B's motivation profile ({pb.profile}); "
            f"the dotted line marks B's effective ρ = {pb.rho:.2f}. "
            "Grey lines are the other profiles."
        )

with tab_robot:
    st.plotly_chart(per_robot_ab(pa.results, pb.results, "A", "B"), width="stretch")
    st.caption(
        f"Mean ± std per robot over {NUM_SEEDS} seeds. Freedom is 1 − the "
        "constrained share of a robot's behaviour. Hover a bar for the "
        "robot's motivation value."
    )

with tab_scenario:
    col_sa, col_sb = st.columns(2)
    for col, name, pr in [(col_sa, "A", pa), (col_sb, "B", pb)]:
        layer1 = pr.ir.get("layer1_institution", {})
        with col:
            st.markdown(f"**{name}** `{pr.name}`")
            st.markdown(
                scenario_svg(
                    layer1.get("agent_motivation_values"),
                    layer1.get("decision_authority"),
                    dark=_is_dark(),
                ),
                unsafe_allow_html=True,
            )
    st.caption(
        "Fixed 11-node road graph with 5 robots. Robots are shaded by "
        "motivation (darker is higher). Dashed lines show a central "
        "authority directing the robots; a verifier-only arbitrator has none."
    )

# ── 4. Reproduce ────────────────────────────────────────────────────

st.header("4. Reproduce & export", divider="gray")

st.dataframe(
    [
        {
            "design": name, "name": pr.name, "cadl id": pr.cadl_id,
            "ir id": pr.ir_id, "config id": pr.config_id,
            "seeds": f"0–{len(pr.seeds) - 1}",
        }
        for name, pr in [("A", pa), ("B", pb)]
    ],
    width="stretch", hide_index=True,
)
st.caption(
    "IDs are content hashes of each stage. The same CADL id and seeds "
    "always give the same results."
)

with st.expander("CADL source of A and B"):
    col_ya, col_yb = st.columns(2)
    for col, name, pr in [(col_ya, "A", pa), (col_yb, "B", pb)]:
        with col:
            st.markdown(f"**{name}** `{pr.name}`")
            st.code(yaml.dump(pr.cadl, default_flow_style=False, sort_keys=False),
                    language="yaml")

col_d1, col_d2, col_d3, col_d4 = st.columns(4)
col_d1.download_button(
    "B: CADL (YAML)", yaml.dump(pb.cadl, default_flow_style=False, sort_keys=False),
    file_name=f"{pb.name}.cadl.yaml", mime="text/yaml", width="stretch",
)
col_d2.download_button(
    "B: IR (JSON)", json.dumps(pb.ir, indent=2),
    file_name=f"{pb.name}.ir.json", mime="application/json", width="stretch",
)
col_d3.download_button(
    "B: simulator config (JSON)", json.dumps(pb.config, indent=2),
    file_name=f"{pb.name}.cadl_config.json", mime="application/json",
    width="stretch",
)
col_d4.download_button(
    "A vs B comparison (JSON)", json.dumps(comparison.to_dict(), indent=2),
    file_name="cadl_explorer_comparison.json", mime="application/json",
    width="stretch",
)

# Saved comparisons (session-scoped)
if "run_history" not in ss:
    ss.run_history = []

if btn_save:
    ss.run_history.append({
        "time": datetime.now().strftime("%H:%M:%S"),
        "A": pa.name,
        "B": pb.name,
        "throughput A": round(ea.throughput.mean, 2),
        "throughput B": round(eb.throughput.mean, 2),
        "autonomy A": round(ea.autonomy.mean, 3),
        "autonomy B": round(eb.autonomy.mean, 3),
        "fairness A": round(ea.fairness.mean, 3),
        "fairness B": round(eb.fairness.mean, 3),
        "cadl id A": pa.cadl_id,
        "cadl id B": pb.cadl_id,
        "seeds": len(pb.seeds),
    })
    ss.run_history = ss.run_history[-20:]
    st.toast("Comparison saved — see the bottom of the page.")

st.subheader("Saved comparisons")
history = ss.run_history
if not history:
    st.caption(
        "Nothing saved yet. **Save this comparison** in the sidebar keeps "
        "the current A/B metrics here for this session (up to 20)."
    )
else:
    import pandas as pd
    df = pd.DataFrame(history)
    st.dataframe(df, width="stretch", hide_index=True)
    col_csv, col_json, col_clear = st.columns(3)
    col_csv.download_button(
        "Download CSV", df.to_csv(index=False).encode("utf-8"),
        file_name="cadl_explorer_comparisons.csv", mime="text/csv",
        width="stretch",
    )
    col_json.download_button(
        "Download JSON", json.dumps(history, indent=2).encode("utf-8"),
        file_name="cadl_explorer_comparisons.json", mime="application/json",
        width="stretch",
    )
    if col_clear.button("Clear saved comparisons", width="stretch"):
        ss.run_history = []
        st.rerun()
