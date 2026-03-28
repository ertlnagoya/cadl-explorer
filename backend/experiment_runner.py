"""Backward-compatibility shim — delegates to backend runners and models."""
# flake8: noqa: F401
from backend.models.experiment_result import SingleResult
from backend.runners.synthetic_runner import (
    run_single as run_single_synthetic,
    run_sweep,
    run_comparison_sweep,
)
