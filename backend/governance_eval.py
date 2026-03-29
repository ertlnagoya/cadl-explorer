"""DEPRECATED — use backend.services.evaluation_service instead.

This file is a backward-compatibility shim kept only for legacy imports.
It will be removed in a future version.
"""
import warnings as _w
_w.warn("backend.governance_eval is deprecated; use backend.services.evaluation_service", DeprecationWarning, stacklevel=2)
# flake8: noqa: F401
from backend.services.evaluation_service import evaluate, compare, generate_summary
