"""The sidebar of the Designer: the workspace, opening and saving a `.cadl` file or a
project (`.zip` with versions, decisions and the record of the checks), the examples,
and a first draft written by the AI from a description."""

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
from backend.services import design_project as project
from views._i18n import t
from views._design.common import (_load_example, _new_design, _open_workspace, _save_workspace,
    _set_source)


def _project_zip(name: str, source: str) -> bytes:
    from backend.services.design_runtime import toolchain
    return project.build(
        name, source, ss.get("design_versions") or [], ss.get("design_decisions") or [],
        ss.get("design_constitution"), ss.get("design_constitution_name"),
        ss.get("design_uca_entries") or {}, ss.get("design_runs") or [], toolchain()["cadl"])


def _open_project(opened: dict, fallback: str):
    _set_source(opened["source"])
    ss.design_file_name = opened["name"] or fallback
    ss.design_versions = opened["versions"]
    ss.design_decisions = opened["decisions"]
    if opened["constitution"]:
        ss.design_constitution = opened["constitution"]
        ss.design_constitution_name = opened["constitution_name"] or "constitution.yaml"
    else:
        ss.pop("design_constitution", None)
        ss.pop("design_constitution_name", None)
    ss.design_uca_entries = opened["uca"]
    ss.design_runs = opened["runs"]
    ss.pop("design_rerun", None)
    m = opened["manifest"]
    ss.design_project_opened = t(
        "Opened the project with {v} version(s), {d} decision(s) and {r} recorded run(s); "
        "saved by CADL Explorer {e} with cadl {c}.").format(
        v=len(opened["versions"]), d=len(opened["decisions"]), r=len(opened["runs"]),
        e=m.get("explorer") or "?", c=m.get("cadl") or "?")


def render(c):
    source, file_stem = c.source, c.file_stem
    with st.sidebar:
        # Shown when the app is started with CADL_WORKSPACE: the folder an AI
        # assistant edits through the MCP server (mcp_server.py).
        if workspace.enabled():
            st.header(t("Workspace"))
            ws_designs = workspace.list_designs()
            if not ws_designs:
                st.caption(t("No designs yet in `{path}`.").format(path=workspace.root()))
            else:
                if ss.get("design_ws_pick") not in ws_designs:
                    ss.design_ws_pick = ss.get("design_ws_name") if ss.get("design_ws_name") in ws_designs \
                        else ws_designs[0]
                st.selectbox(t("Design shared with the assistant"), ws_designs, key="design_ws_pick")
                st.button(t("Open"), on_click=_open_workspace, width="stretch",
                          help=t("Load this design from the workspace into the editor."))
            ws_name = ss.get("design_ws_name")
            if ws_name and workspace.exists(ws_name):
                on_disk = workspace.read(ws_name)
                if on_disk != source:
                    st.warning(t(
                        "`{name}` in the workspace differs from the editor. The assistant "
                        "may have changed it, or you have unsaved edits.").format(name=ws_name))
                    st.button(t("Reload from workspace"), on_click=_open_workspace, width="stretch",
                              help=t("Replace the editor content with the workspace file. Undo takes it back."))
                    st.button(t("Save to workspace"), on_click=_save_workspace, width="stretch",
                              type="primary", help=t("Write the editor content to the workspace file."))
                else:
                    st.caption(t("Editing `{name}`; in sync with the workspace.").format(name=ws_name))
                commits = workspace.history(ws_name)
                if commits:
                    with st.expander(t("History in git") + f" ({len(commits)})"):
                        for entry in commits:
                            st.caption(f"`{entry['commit']}` {entry['time']} — {entry['message']}")
                    hist = workspace.version_history(ws_name)
                    if hist["values"] and len(hist["versions"]) > 1:
                        with st.expander(t("Versions in git") + f" ({len(hist['versions'])})"):
                            st.caption(t("The amendable values of each version; ← marks a change "
                                         "from the version before."))
                            st.dataframe(hist["values"], hide_index=True, width="stretch")
                            if hist["movement"]:
                                st.dataframe(hist["movement"], hide_index=True, width="stretch")
                elif not workspace.git_enabled():
                    st.caption(t("Start the app with `CADL_WORKSPACE_GIT=1` to commit every change "
                                 "of the workspace to git."))

        st.header(t("File"))
        upload = st.file_uploader(
            t("Open a CADL file or a project"), type=["cadl", "yaml", "yml", "zip"],
            help=t("A `.cadl` file replaces the design in the editor. A project (`.zip`, saved "
                   "below) also brings back its versions, decisions, constitution and the record "
                   "of the checks."),
        )
        if upload is not None and ss.get("design_upload_id") != upload.file_id:
            ss.design_upload_id = upload.file_id
            data = upload.getvalue()
            if project.is_project(data):
                try:
                    opened = project.read(data)
                except project.ProjectError as e:
                    st.error(f"{upload.name}: {e}")
                else:
                    _open_project(opened, upload.name.rsplit(".", 1)[0])
                    st.rerun()
            elif upload.size > MAX_SOURCE_CHARS * 4:
                st.error(t("The file is too large (limit {n:,} characters).").format(n=MAX_SOURCE_CHARS))
            else:
                try:
                    text = data.decode("utf-8-sig")
                except UnicodeDecodeError:
                    st.error(t("The file is not UTF-8 text."))
                else:
                    if len(text) > MAX_SOURCE_CHARS:
                        st.error(t("The file has {n:,} characters; the limit is {m:,}.")
                                 .format(n=len(text), m=MAX_SOURCE_CHARS))
                    else:
                        _set_source(text)
                        ss.design_file_name = upload.name.rsplit(".", 1)[0]
                        st.rerun()
        if ss.get("design_project_opened"):
            st.success(ss.pop("design_project_opened"))

        if not ss.get("design_file_name"):
            ss.design_file_name = file_stem
        save_name = st.text_input(t("File name"), key="design_file_name").strip() or file_stem
        st.download_button(
            t("Save as .cadl"), source, file_name=f"{save_name}.cadl",
            mime="text/yaml", width="stretch", type="primary",
            help=t("Downloads the design exactly as it is in the editor."),
        )
        st.download_button(
            t("Save the project (.zip)"), _project_zip(save_name, source),
            file_name=f"{save_name}.project.zip", mime="application/zip", width="stretch",
            icon=":material/inventory_2:",
            help=t("The design with its saved versions, decision record, constitution, STPA "
                   "entries and the record of the checks, to open here again later."),
        )
        st.caption(t("The design is kept in this browser session only. Save it, or the whole "
                     "project, to keep it."))

        st.header(t("Start from"))
        examples = ds.list_examples()
        if examples:
            st.selectbox(t("Example design"), list(examples), key="design_example")
            st.button(t("Load example"), on_click=_load_example, width="stretch",
                      help=t("Replaces the design in the editor."))
        st.button(t("New design"), on_click=_new_design, width="stretch",
                  help=t("Replaces the design in the editor with a small starting template."))

        with st.expander(t("Draft from a description")):
            unavailable = ds.ai_available()
            if unavailable:
                st.caption(t(
                    "Writes a first CADL draft from a plain-language description, using "
                    "the upstream `cadl ai` generator. Not enabled here: {reason}. "
                    "To use it, run the app locally with `pip install anthropic` and "
                    "`ANTHROPIC_API_KEY` set.").format(reason=unavailable)
                )
            else:
                st.caption(
                    t("Describe the system in plain language (English or Japanese). The "
                    "description is sent to the Anthropic API; the draft replaces the "
                    "design in the editor and is checked like any other.")
                )
                description = st.text_area(
                    t("Description"), key="design_ai_text", height=140,
                    max_chars=ds.MAX_DESCRIPTION_CHARS, label_visibility="collapsed",
                    placeholder=t("A warehouse where a dispatcher assigns delivery tasks to robots…"),
                )
                if st.button(t("Write a draft"), width="stretch", disabled=not description.strip()):
                    try:
                        with st.spinner(t("Writing a draft…")):
                            draft, draft_errors = ds.draft_from_description(description)
                    except Exception as e:
                        st.error(t("Could not write a draft: {e}").format(e=e))
                    else:
                        _set_source(draft)
                        ss.design_file_name = ""
                        if draft_errors:
                            st.toast(t("The draft has problems; see the checks."))
                        st.rerun()
