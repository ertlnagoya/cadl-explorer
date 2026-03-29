"""DEPRECATED — use backend.services.cadl_service instead.

This file is a backward-compatibility shim kept only for legacy imports.
It will be removed in a future version.
"""
import warnings as _w
_w.warn("backend.cadl_bridge is deprecated; use backend.services.cadl_service", DeprecationWarning, stacklevel=2)
# flake8: noqa: F401,F403
from backend.services.cadl_service import *
from backend.services.cadl_service import TEMPLATES
