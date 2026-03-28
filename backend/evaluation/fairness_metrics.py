"""Fairness metrics: CV-based fairness, Gini coefficient, min-share ratio."""

import numpy as np
from backend.models.evaluation_result import MetricValue


def compute_fairness(results: list) -> MetricValue:
    """Compute fairness (1 - normalized variance) across runs."""
    if not results:
        return MetricValue()
    vals = [r.fairness for r in results]
    return MetricValue(mean=float(np.mean(vals)), std=float(np.std(vals)), n=len(vals))


def compute_gini(results: list) -> MetricValue:
    """Compute Gini coefficient of per-robot deliveries (lower = fairer)."""
    if not results:
        return MetricValue()
    ginis = []
    for r in results:
        deliveries = [robot["deliveries"] for robot in r.per_robot]
        if not deliveries or sum(deliveries) == 0:
            ginis.append(0.0)
            continue
        arr = np.array(sorted(deliveries), dtype=float)
        n = len(arr)
        index = np.arange(1, n + 1)
        gini = (2 * np.sum(index * arr) - (n + 1) * np.sum(arr)) / (n * np.sum(arr))
        ginis.append(float(gini))
    return MetricValue(mean=float(np.mean(ginis)), std=float(np.std(ginis)), n=len(ginis))
