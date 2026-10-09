"""About page — what the explorer shows, glossary, scenario and model notes."""

import streamlit as st

from backend.services.cadl_service import TEMPLATES

SPEC_URL = "https://www.ertl.jp/cadl-spec/"
REPO_URL = "https://github.com/ertlnagoya/cadl-explorer"


def _scenario_svg() -> str:
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
        '<svg viewBox="0 0 700 460" style="max-width:700px;width:100%;'
        'background:#fafafa;border:1px solid #e0e0e0;border-radius:8px;">',
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
    for i, nid in enumerate([0, 2, 4, 6, 8]):
        x, y = positions[nid]
        rx, ry = x + 25, y - 20
        svg.append(
            f'<rect x="{rx-10}" y="{ry-10}" width="20" height="20" rx="4" fill="#4caf50" stroke="#2e7d32" stroke-width="1.5"/>'
            f'<text x="{rx}" y="{ry+4}" text-anchor="middle" font-size="9" fill="white" font-weight="bold">R{i}</text>'
        )
    svg.append(
        '<rect x="290" y="415" width="120" height="30" rx="6" fill="#ff9800" stroke="#e65100" stroke-width="1.5"/>'
        '<text x="350" y="435" text-anchor="middle" font-size="11" fill="white">Arbitrator</text>'
    )
    svg.append("</svg>")
    return "\n".join(svg)


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
st.markdown(f"[CADL specification and hands-on]({SPEC_URL}) · [Source code]({REPO_URL})")

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
    "- **A-SoS** is this app's label for a configuration with a strong "
    "central authority. Its `sos_type` is `Directed`.\n"
    "- **C-SoS** is a collaborative, autonomy-oriented configuration.\n"
    "- **A-SoS + motivation-sensitive** is A-SoS whose authority adjusts "
    "per-agent budgets and waits according to agent motivation (`hybrid` "
    "model). It only differs from A-SoS when ρ > 0."
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
    "graph with 5 robots (R0–R4) and one arbitrator. Under A-SoS the "
    "arbitrator acts as the central authority; under C-SoS it only verifies."
)
st.markdown(_scenario_svg(), unsafe_allow_html=True)
