"""Contract Lifecycle — Streamlit page (Appendix E demonstrator).

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
   the JSON or pick one of the bundled examples.
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


st.title("Contract Lifecycle")
st.markdown(
    "Where the Explorer follows one design change through the pipeline, "
    "this page looks inside a single institution: each contract's "
    "`lifecycle:` section (SoS-DSL extension, Appendix E) is drawn as a "
    "state machine, with its `monitors:` listed below."
)
st.caption(
    "Input is a simulator IR produced by the `cadl` compiler: "
    "`cadl sim-ir <file>.cadl --format json`. "
    "[CADL specification and hands-on](https://www.ertl.jp/cadl-spec/)"
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
            "Bundled example",
            [p.name for p in BUNDLED],
            disabled=upload is not None,
            help="Used when no file is uploaded.",
        )

# --- Load IR ---------------------------------------------------------------

ir_doc: dict | None = None
source_label = ""
try:
    if upload is not None:
        ir_doc = json.loads(upload.read().decode("utf-8"))
        source_label = f"upload: {upload.name}"
    elif bundled_pick:
        path = EXAMPLES_DIR / bundled_pick
        ir_doc = json.loads(path.read_text(encoding="utf-8"))
        source_label = f"bundled: {bundled_pick}"
except json.JSONDecodeError as e:
    st.error(f"Failed to parse JSON: {e}")
    st.stop()

if ir_doc is None:
    st.info(
        "Upload a Sim-IR JSON in the sidebar. The IR document must "
        "contain `institution.contracts[*].lifecycle`."
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
        _theme = getattr(st.context, "theme", None)
        dot = lifecycle_to_dot(view, dark=getattr(_theme, "type", None) == "dark")
        st.graphviz_chart(dot, width="stretch")
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
    st.dataframe(mons, width="stretch", hide_index=True)
