"""
CADL Explorer — Streamlit entry point.

Routes between the pages in views/:
  Explorer            — compare two governance designs end to end
  Designer            — author and check a full CADL design
  Contract Lifecycle  — SoS-DSL contract lifecycle state machines
  About               — glossary, scenario and model notes
"""

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))

st.set_page_config(page_title="CADL Explorer", layout="wide")

# Streamlit drops the state of widgets that a run does not render, so a
# page's selections would be lost when another page is opened. Writing a
# key back to itself keeps it.
PERSISTENT_KEYS = [
    "a_template", "a_profile", "a_rho", "b_template", "b_profile", "b_rho",
    "custom_cadl", "custom_cadl_a",
    "design_mode", "design_view", "design_section", "design_example",
    "design_file_name", "design_contract", "design_protocol",
    "design_life_contract", "design_view_protocol", "design_ws_pick",
    "design_readback_actor",
]
for _key in PERSISTENT_KEYS:
    if _key in st.session_state:
        st.session_state[_key] = st.session_state[_key]

page = st.navigation([
    st.Page("views/explorer.py", title="Explorer", default=True),
    st.Page("views/designer.py", title="Designer", url_path="designer"),
    st.Page("views/lifecycle.py", title="Contract Lifecycle", url_path="lifecycle"),
    st.Page("views/about.py", title="About & Glossary", url_path="about"),
])
page.run()
