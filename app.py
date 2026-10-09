"""
CADL Explorer — Streamlit entry point.

Routes between the pages in views/:
  Explorer            — compare two governance designs end to end
  Contract Lifecycle  — SoS-DSL contract lifecycle state machines
  About               — glossary, scenario and model notes
"""

import os
import sys

import streamlit as st

sys.path.insert(0, os.path.dirname(__file__))

st.set_page_config(page_title="CADL Explorer", layout="wide")

page = st.navigation([
    st.Page("views/explorer.py", title="Explorer", default=True),
    st.Page("views/lifecycle.py", title="Contract Lifecycle", url_path="lifecycle"),
    st.Page("views/about.py", title="About & Glossary", url_path="about"),
])
page.run()
