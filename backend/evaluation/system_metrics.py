"""System-level performance metrics: throughput, collision_rate, retry_rate."""

import numpy as np
from backend.models.evaluation_result import MetricValue


def compute_throughput(results: list) -> MetricValue:
    """Compute throughput (total deliveries) across runs."""
    if not results:
        return MetricValue()
    vals = [r.throughput for r in results]
    return MetricValue(mean=float(np.mean(vals)), std=float(np.std(vals)), n=len(vals))
