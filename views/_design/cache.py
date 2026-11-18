"""Cached calls of cadl for the Designer."""

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

@st.cache_data(show_spinner=False, max_entries=64)
def cached_analyze(source: str, _version: int = 5) -> ds.DesignAnalysis:
    return ds.analyze(source)


@st.cache_data(show_spinner=False, max_entries=32)
def cached_codegen(source: str, target: str, versions: tuple = ()) -> bytes:
    return ds.export_codegen_zip(source, target, versions)


@st.cache_data(show_spinner=False, max_entries=16)
def cached_prism(source: str) -> dict:
    return ds.prism_files(source)


@st.cache_data(show_spinner=False, max_entries=32)
def cached_norms_diff(old_source: str, new_source: str):
    return ds.norms_diff(old_source, new_source)


@st.cache_data(show_spinner=False, max_entries=32)
def cached_amend_diff(old_source: str, new_source: str):
    return ds.amend_diff(old_source, new_source)


@st.cache_data(show_spinner=False, max_entries=32)
def cached_monitor_notes(source: str, target: str, versions: tuple = ()) -> dict:
    return ds.norms_monitor_notes(source, target, versions)


@st.cache_data(show_spinner=False, max_entries=16)
def cached_norms_indices(source: str) -> dict:
    return ds.norms_indices(source)


class _NotCached(Exception):
    """A failure that may not recur (the server busy, a timeout, a limit): raised
    out of a cached function so that st.cache_data does not keep it."""

    def __init__(self, result: dict):
        super().__init__(result.get("failure"))
        self.result = result


def _uncached(result: dict) -> dict:
    if result.get("transient"):
        raise _NotCached(result)
    return result
