"""SoS-DSL Lifecycle View — Streamlit page (Appendix E demonstrator).

This page renders the per-instance contract lifecycle that the
SoS-DSL extension promotes to a first-class CADL construct. It
consumes a CADL Sim-IR JSON document (the output of `cadl sim-ir`)
and draws each contract's lifecycle as a state machine, with deadline
edges and on_violation lifts highlighted.

Usage
-----
1. Generate IR JSON from a CADL source that uses the lifecycle: /
   monitors: keys (Appendix E):

       cadl sim-ir examples/sos_dsl_robot_delivery.cadl --format json \\
           > sos_dsl_delivery.ir.json

2. Open this page from the cadl-explorer sidebar and either upload
   the JSON or pick a path from the dropdown of bundled examples.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import streamlit as st

# Ensure the bundled cadl_sim package is importable when this page is
# launched standalone (Streamlit multipage discovery may not put the
# repo root on sys.path).
_HERE = Path(__file__).resolve().parent
_ROOT = _HERE.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cadl_sim.sos_dsl import (  # noqa: E402
    build_lifecycle_view,
    lifecycle_to_dot,
    monitors_summary,
)


st.set_page_config(page_title="SoS-DSL Lifecycle", layout="wide")

st.title("SoS-DSL — Contract Lifecycle View")
st.caption(
    "Renders the per-instance contract lifecycle introduced by the "
    "SoS-DSL extension (Appendix E). Each contract's lifecycle: "
    "section is shown as a state machine; monitors: are listed below."
)

# --- Sample data discovery -------------------------------------------------

EXAMPLES_DIR = _ROOT / "cadl_sim" / "sos_dsl" / "examples"
BUNDLED = sorted(
    EXAMPLES_DIR.glob("*.ir.json")
) if EXAMPLES_DIR.exists() else []

with st.sidebar:
    st.header("IR source")
    upload = st.file_uploader(
        "Upload a CADL Sim-IR JSON",
        type=["json"],
        accept_multiple_files=False,
    )
    bundled_pick = None
    if BUNDLED:
        bundled_pick = st.selectbox(
            "…or pick a bundled example",
            ["(none)"] + [p.name for p in BUNDLED],
        )

# --- Load IR ---------------------------------------------------------------

ir_doc: dict | None = None
source_label = ""
try:
    if upload is not None:
        ir_doc = json.loads(upload.read().decode("utf-8"))
        source_label = f"upload: {upload.name}"
    elif bundled_pick and bundled_pick != "(none)":
        path = EXAMPLES_DIR / bundled_pick
        ir_doc = json.loads(path.read_text(encoding="utf-8"))
        source_label = f"bundled: {bundled_pick}"
except json.JSONDecodeError as e:
    st.error(f"Failed to parse JSON: {e}")
    st.stop()

if ir_doc is None:
    st.info(
        "Pick a bundled example or upload a Sim-IR JSON. The IR "
        "document must contain `institution.contracts[*].lifecycle`."
    )
    st.stop()

contracts = ir_doc.get("institution", {}).get("contracts", []) or []
if not contracts:
    st.warning("This IR has no `institution.contracts`.")
    st.stop()

# --- Per-contract render ---------------------------------------------------

st.markdown(f"**Source:** `{source_label}`  •  **SoS:** `{ir_doc.get('name', '')}`")

contract_ids = [c.get("id", f"contract_{i}") for i, c in enumerate(contracts)]
picked = st.radio("Contract", contract_ids, horizontal=True)
contract = next(c for c in contracts if c.get("id") == picked)

view = build_lifecycle_view(contract)
if view is None:
    st.warning(
        f"Contract `{picked}` does not declare a `lifecycle:` section. "
        "Add one (see Appendix E.6) and re-emit the IR."
    )
else:
    col_graph, col_meta = st.columns([3, 2])

    with col_graph:
        st.subheader(f"Lifecycle — {view.contract_id}")
        dot = lifecycle_to_dot(view)
        st.graphviz_chart(dot, use_container_width=True)
        with st.expander("Show DOT source"):
            st.code(dot, language="dot")

    with col_meta:
        st.subheader("Lifecycle metadata")
        st.json({
            "states": view.states,
            "initial": view.initial,
            "terminal": view.terminal,
            "transition_count": len(view.transitions),
        })

# --- Monitors panel --------------------------------------------------------

st.divider()
st.subheader("Monitors")
mons = monitors_summary(contract)
if not mons:
    st.caption("No `monitors:` declared on this contract.")
else:
    st.dataframe(mons, use_container_width=True, hide_index=True)
