"""Backward-compatibility shim — delegates to backend.services.diff_service."""
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
