"""DEPRECATED — use backend.services.diff_service instead.

This file is a backward-compatibility shim kept only for legacy imports.
It will be removed in a future version.
"""
import warnings as _w
_w.warn("backend.diff_engine is deprecated; use backend.services.diff_service", DeprecationWarning, stacklevel=2)
# flake8: noqa: F401
from backend.services.diff_service import (
    compute_cadl_diff,
    compute_ir_diff,
    compute_config_diff,
    tagged_lines_to_html,
    diff_cadl,
    diff_ir,
    diff_config,
    diff_result,
    DiffResult,
)
