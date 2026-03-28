"""Backward-compatibility shim — delegates to backend.services.evaluation_service."""
# flake8: noqa: F401
from backend.services.evaluation_service import evaluate, compare, generate_summary
