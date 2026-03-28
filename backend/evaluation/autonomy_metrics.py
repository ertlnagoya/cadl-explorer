"""Autonomy metrics: system-level autonomy, per-robot freedom."""

import numpy as np
from backend.models.evaluation_result import MetricValue


def compute_autonomy(results: list) -> MetricValue:
    """Compute system autonomy across runs."""
    if not results:
        return MetricValue()
    vals = [r.avg_autonomy for r in results]
    return MetricValue(mean=float(np.mean(vals)), std=float(np.std(vals)), n=len(vals))
