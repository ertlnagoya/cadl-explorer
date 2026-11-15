"""The left column of the Designer: the source editor and the forms."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime

import streamlit as st

from backend.plotting.architecture import architecture_to_dot, regimes_to_dot
from backend.plotting.sequence import participants, sequence_svg
from backend.services import design_graph as graph
from backend.services import design_amend as amend
from backend.services import design_contracts as dcon
from backend.services import design_stamp as stamp
from backend.services import design_readback as readback
from backend.services import design_service as ds
from backend.services import design_workspace as workspace
from backend.services.cadl_service import MAX_SOURCE_CHARS
from cadl_sim.sos_dsl import build_lifecycle_view, lifecycle_to_dot, monitors_summary
from views._design.common import *  # noqa: F401,F403
from views._design.common import _is_dark, _record, _on_edit, _redo, _set_source, _undo
from views._designer_forms import render_forms
from views._design import editor_component
from backend.services import design_snippets as snippets
from views._i18n import t


@st.cache_data(show_spinner=False, max_entries=16)
def _names(source: str, ir) -> list:
    return snippets.names(source, ir)


def _on_applied():
    state = ss.get("design_cc_editor") or {}
    applied = getattr(state, "applied", None)
    if applied is None and isinstance(state, dict):
        applied = state.get("applied")
    if not isinstance(applied, dict):
        return
    # typed over a source that was replaced meanwhile (Undo, an example, a version)
    if applied.get("version") != str(ss.get("design_ver", 0)):
        return
    text = applied.get("text")
    if not isinstance(text, str) or text == ss.design_src:
        return
    if len(text) > MAX_SOURCE_CHARS:
        ss.design_too_long = True
        return
    _record()
    ss.design_src = text

try:
    from streamlit_ace import st_ace
except ImportError:  # the plain text area is the fallback editor
    st_ace = None


def render(c):
    source, analysis, flags, src_key = c.source, c.analysis, c.flags, c.src_key
    # A horizontal container keeps the buttons at their natural width and
    # wraps them on a narrow screen instead of squeezing their labels.
    with st.container(horizontal=True, vertical_alignment="center"):
        mode = st.segmented_control(
            t("Edit as"), ["Source", "Forms"], key="design_mode", label_visibility="collapsed", format_func=t,
        ) or "Source"
        st.button(
            t("Undo"), on_click=_undo, disabled=not ss.get("design_history"),
            help=t("Take back the last change to the design."),
        )
        st.button(t("Redo"), on_click=_redo, disabled=not ss.get("design_redo"))

    if mode == "Source":
        if editor_component.available():
            editor_component.cadl_editor(
                source, version=str(ss.design_ver), annotations=analysis.annotations,
                names=_names(source, analysis.ir),
                snippets=[{**s, "label": t(s["label"]), "where": t(s["where"])} for s in snippets.snippets()],
                labels={"apply": t("Apply"), "insert": t("Insert…"), "unsaved": t("Not applied yet"),
                        "line": t("Line"), "inserted": t("Inserted; it goes into {where}."),
                        "snippetTitle": t("Insert a snippet at the cursor"),
                        "titles": {"apply": t("Apply (Ctrl/⌘ + Enter)"),
                                   "indent": t("Indent the line or the selection (Tab)"),
                                   "outdent": t("Outdent (Shift + Tab)"),
                                   "comment": t("Comment or uncomment the lines")}},
                # shown once: a later visit of the editor does not jump to the line again
                height=600, focus=ss.pop("design_focus", None), key="design_cc_editor",
                on_applied=_on_applied)
            if ss.get("design_too_long"):
                st.error(t("The design is limited to {n:,} characters.").format(n=MAX_SOURCE_CHARS))
                ss.design_too_long = False
            st.caption(t(
                "Press **Apply**, Ctrl/⌘ + Enter, or leave the editor to check your changes. "
                "Names of the design are completed as you type (↑ ↓ and Enter, or a tap); "
                "**Insert…** puts the beginning of a contract, a norm, a lifecycle or a model "
                "at the cursor. A red or orange line number marks a problem; tap it to read it."))
        elif st_ace is not None:
            edited = st_ace(
                value=source, language="yaml", key=f"design_ace_{ss.design_ver}",
                theme="tomorrow_night" if _is_dark() else "chrome",
                height=640, font_size=13, tab_size=2, wrap=False, show_gutter=True,
                auto_update=False, annotations=analysis.annotations or None,
            )
            if edited is not None and edited != source:
                if len(edited) > MAX_SOURCE_CHARS:
                    st.error(f"The design is limited to {MAX_SOURCE_CHARS:,} characters.")
                else:
                    _record()
                    ss.design_src = edited
                    st.rerun()
            st.caption(t("Press **Apply** or Ctrl/⌘ + Enter to check your changes."))
        else:
            st.markdown(
                t("<style>textarea{font-family:ui-monospace,SFMono-Regular,Menlo,monospace !important;"
                "font-size:13px !important;}</style>"),
                unsafe_allow_html=True,
            )
            st.text_area(
                t("CADL source"), value=source, height=640, max_chars=MAX_SOURCE_CHARS,
                key=f"design_editor_{ss.design_ver}", on_change=_on_edit,
                label_visibility="collapsed",
            )
            st.caption(t("Changes are checked when you click outside the editor or press Ctrl/⌘ + Enter."))
    else:
        try:
            doc = ds.load_doc(source)
        except ValueError as e:
            doc = None
            st.warning(f"Forms need a readable design. Fix the source first: {e}")

        if doc is not None:
            if ds.has_amend_tags(source):
                st.caption(t(
                    ":material/info: This design marks amendable values with `# @amend:` "
                    "comments (Course D). A form keeps the comments and the lines it does not "
                    "change; to change a marked value within the constitution, use **Amendable "
                    "parameters** under **Versions**."))
            elif ds.has_comments(source):
                st.caption(t(
                    ":material/info: Applying a form keeps the `#` comments and the lines it "
                    "does not change. **Undo** takes a change back."))

            def _apply(message: str):
                _set_source(ds.merge_doc(source, doc))
                st.toast(message)
                st.rerun()

            render_forms(doc, src_key, _apply, flags, analysis.findings)
