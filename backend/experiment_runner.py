"""DEPRECATED — use backend.runners.synthetic_runner or backend.services.experiment_service instead.

This file is a backward-compatibility shim kept only for legacy imports.
It will be removed in a future version.
"""
import warnings as _w
_w.warn("backend.experiment_runner is deprecated; use backend.services.experiment_service", DeprecationWarning, stacklevel=2)
# flake8: noqa: F401
from backend.models.experiment_result import SingleResult
from backend.runners.synthetic_runner import (
    run_single as run_single_synthetic,
    run_sweep,
    run_comparison_sweep,
)
