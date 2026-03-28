"""Backward-compatibility shim — delegates to backend.plotting.interactive."""
# flake8: noqa: F401
from backend.plotting.interactive import (
    scatter_performance_autonomy,
    line_rho_effects,
    bar_comparison,
    individual_robot_scatter,
)
