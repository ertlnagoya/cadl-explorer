"""DEPRECATED — use backend.plotting.interactive instead.

This file is a backward-compatibility shim kept only for legacy imports.
It will be removed in a future version.
"""
import warnings as _w
_w.warn("backend.plot_builder is deprecated; use backend.plotting.interactive", DeprecationWarning, stacklevel=2)
# flake8: noqa: F401
from backend.plotting.interactive import (
    scatter_performance_autonomy,
    line_rho_effects,
    bar_comparison,
    individual_robot_scatter,
)
