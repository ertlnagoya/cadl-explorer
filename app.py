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

from backend.version import RELEASES_URL, __version__  # noqa: E402
from backend.services import design_runtime  # noqa: E402
from backend.services import prism_setup  # noqa: E402
from views._i18n import LANGUAGES, default_language, t  # noqa: E402

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
    "design_readback_actor", "design_stage", "design_layout", "lang",
    # the options of the checks of cadl, so that a result stays with them
    "design_nc_contract", "design_nc_bindings", "design_nc_members", "design_nc_step",
    "design_nc_horizon", "design_nc_timeout", "design_nc_domains", "design_nc_instances",
    "design_nc_starts", "design_nc_versions", "design_nc_witness", "design_game_certify",
    "design_trace_contract", "design_code_target", "design_sim_target",
    # choices and typed text that should survive a switch of view or page
    "design_cmp_a", "design_cmp_b", "design_structure_authority", "design_amend_name",
    "design_ai_text", "design_version_name", "design_version_prefix",
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
if st.session_state.get("lang") not in LANGUAGES:
    st.session_state.lang = default_language()
# Before page.run(): a page may end the run with st.stop().
with st.sidebar:
    st.segmented_control(t("Language"), LANGUAGES, key="lang", label_visibility="collapsed",
                         on_change=lambda: st.session_state.lang or st.session_state.update(
                             lang=LANGUAGES[0]))
    # On Streamlit Community Cloud, PRISM is downloaded in the background.
    setup = prism_setup.ensure_prism()
    tools = design_runtime.toolchain()
    st.caption(
        f"CADL Explorer v{__version__} · [{t('release notes')}]({RELEASES_URL})  \n"
        f"cadl {tools['cadl'] or '?'} · PRISM "
        + (tools["prism"] or (t("being installed") if setup == "installing"
                              else t("not installed"))),
        help=t("The cadl that checks the designs here, and the PRISM that `cadl norms-check` "
               "uses. Without PRISM, norms-check writes the model but checks nothing."))
page.run()
