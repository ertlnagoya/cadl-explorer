"""About page — what the explorer shows, glossary, scenario and model notes."""

import streamlit as st

from backend.plotting.scenario import scenario_svg
from backend.services.cadl_service import TEMPLATES
from backend.version import RELEASES_URL, __version__
from views._guide import WHAT_YOU_CAN_DO, OTHER_PAGES, HOW_TO_USE, HOW_TO_DESIGN, DEMOS

SPEC_URL = "https://www.ertl.jp/cadl-spec/"
REPO_URL = "https://github.com/ertlnagoya/cadl-explorer"


st.title("About & Glossary")

st.markdown(
    "CADL Explorer is research infrastructure for tracing the causal "
    "structure of governance design in Systems of Systems (SoS). You pick "
    "two governance designs, A and B, and the Explorer shows how the "
    "difference between them propagates through four stages:"
)
st.markdown(
    "| Stage | What it is | Artifact |\n"
    "|---|---|---|\n"
    "| 1. CADL | Institutional design parameters | YAML |\n"
    "| 2. IR | Three-layer governance structure: Institution / Protocol / Algorithm | JSON |\n"
    "| 3. Config | Simulator execution settings | Unity `cadl_config.json` |\n"
    "| 4. Result | Behaviour over several seeds, aggregated into metrics | throughput, autonomy, fairness |"
)
st.warning(
    "The results are produced by a **synthetic model** "
    "(`backend/runners/synthetic_runner.py`), not by measurements of a real "
    "system. Use them to follow the causal chain, not as performance figures.",
    icon=":material/science:",
)
st.markdown(
    f"Version **{__version__}** ([release notes]({RELEASES_URL})) · "
    f"[CADL specification and hands-on]({SPEC_URL}) · [Source code]({REPO_URL})"
)

st.header("What you can do", divider="gray")
st.markdown(WHAT_YOU_CAN_DO + OTHER_PAGES)

st.header("How to use the Explorer", divider="gray")
st.markdown(HOW_TO_USE)

st.header("Demo", divider="gray")
st.markdown(
    "The Explorer has three ready-made comparisons. Press the example's "
    "button in the Explorer sidebar, or **Load this demo** under "
    "*Getting started*."
)
st.markdown(
    "| Demo | Compares | What to look at |\n|---|---|---|\n"
    + "\n".join(
        f"| **{name}** | {compares} | {look_at} |"
        for name, (compares, look_at) in DEMOS.items()
    )
)

st.header("How to use the Designer", divider="gray")
st.markdown(HOW_TO_DESIGN)
st.markdown(
    "The Designer works on full CADL, the language of the "
    f"[specification]({SPEC_URL}), through the `cadl-lang` toolchain. The "
    "Explorer's synthetic model is much smaller: it reads only the SoS type, "
    "the motivation profile and ρ, so structural edits made in the Designer "
    "do not change the Explorer's metrics."
)

st.header("Contract Lifecycle page", divider="gray")
st.markdown(
    "1. Open **Contract Lifecycle** in the navigation. A bundled example is shown straight away.\n"
    "2. Pick a contract to see its lifecycle as a state machine. Dashed red edges are "
    "violation transitions; its monitors are listed below the graph.\n"
    "3. To view your own design, compile it with `cadl sim-ir <file>.cadl --format json` "
    "and upload the JSON in the sidebar, or open the `.cadl` file in the **Designer**, "
    "which draws the same diagram from the source."
)

st.header("Governance templates", divider="gray")
st.dataframe(
    [
        {
            "template": name, "sos_type": t["sos_type"].capitalize(),
            "α": t["alpha"], "β": t["beta"], "λ": t["lambda_param"],
            "motivation model": t["motivation_model"],
        }
        for name, t in TEMPLATES.items()
    ],
    width="stretch", hide_index=True,
)
st.markdown(
    "- **D-SoS** (Directed SoS) is a configuration with a strong "
    "central authority. Its `sos_type` is `Directed`.\n"
    "- **C-SoS** is a collaborative, autonomy-oriented configuration.\n"
    "- **D-SoS + motivation-sensitive** is D-SoS whose authority adjusts "
    "per-agent budgets and waits according to agent motivation (`hybrid` "
    "model). It only differs from D-SoS when ρ > 0."
)

st.header("Parameters", divider="gray")
st.markdown(
    "| Symbol | Meaning in this simulator |\n"
    "|---|---|\n"
    "| α | autonomy level |\n"
    "| β | centralization level |\n"
    "| λ | exploration probability |\n"
    "| ρ | motivation sensitivity: 0 ignores agent motivation, 1 fully couples budget and wait decisions to it |\n"
    "\n"
    "α / β / λ here are simulator parameters. They **differ from the "
    "per-contract α / β / λ of the CADL language specification**.\n"
    "\n"
    "**Motivation profile** — how motivation is distributed over the 5 agents: "
    "*uniform* (all 0.5), *linear* (0.2 rising to 1.0), "
    "*polarized* (two agents at 0.2, three at 0.9)."
)

st.header("Metrics", divider="gray")
st.markdown(
    "| Metric | Definition |\n"
    "|---|---|\n"
    "| Throughput | total deliveries completed by the fleet in one 300 s run |\n"
    "| System autonomy | autonomy index in 0–1; higher means agents are less constrained by the central authority |\n"
    "| Fairness | 1 − variance / mean² of per-robot deliveries; 1 is perfectly even |\n"
    "\n"
    "Each design is run over 10 seeds; the Explorer reports mean ± standard deviation."
)

st.header("Scenario", divider="gray")
st.markdown(
    "All designs run on the same fixed scenario: an 11-node, 17-edge road "
    "graph with 5 robots (R0–R4) and one arbitrator. Under D-SoS the "
    "arbitrator acts as the central authority; under C-SoS it only verifies. "
    "The Explorer's **Scenario** tab shows the same graph for designs A and B, "
    "with robots shaded by motivation."
)
_theme = getattr(st.context, "theme", None)
st.markdown(
    scenario_svg(dark=getattr(_theme, "type", None) == "dark"),
    unsafe_allow_html=True,
)
