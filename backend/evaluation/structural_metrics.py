"""Structural metrics: IR-level + result correlation + variance analysis."""

import numpy as np
from backend.models.evaluation_result import MetricValue


def compute_structural(ir_a=None, ir_b=None) -> dict:
    """Compare structural properties of two IRs."""
    result = {}
    if ir_a is not None and ir_b is not None:
        d_a = ir_a.to_dict()
        d_b = ir_b.to_dict()
        diff_count = _count_diffs(d_a, d_b)
        result["structural_distance"] = MetricValue(mean=float(diff_count), std=0.0, n=1)
    return result


def compute_motivation_outcome_correlation(results: list) -> MetricValue:
    """Compute Pearson correlation between motivation and deliveries.

    Positive correlation: higher-motivated robots deliver more.
    Negative: governance throttles high-motivation robots.
    """
    if not results:
        return MetricValue()

    correlations = []
    for r in results:
        if not r.per_robot or len(r.per_robot) < 3:
            continue
        motivations = [robot["motivation"] for robot in r.per_robot]
        deliveries = [robot["deliveries"] for robot in r.per_robot]
        if np.std(motivations) < 1e-6 or np.std(deliveries) < 1e-6:
            continue
        corr = float(np.corrcoef(motivations, deliveries)[0, 1])
        correlations.append(corr)

    if not correlations:
        return MetricValue()
    return MetricValue(
        mean=float(np.mean(correlations)),
        std=float(np.std(correlations)),
        n=len(correlations),
    )


def compute_variance_structure(results: list) -> dict:
    """Analyze delivery variance: total, between-robot, within-robot."""
    if not results:
        return {}

    all_deliveries = []
    robot_means = {}
    for r in results:
        for robot in r.per_robot:
            rid = robot["robot_id"]
            d = robot["deliveries"]
            all_deliveries.append(d)
            robot_means.setdefault(rid, []).append(d)

    if not all_deliveries:
        return {}

    total_var = float(np.var(all_deliveries))
    between_var = float(np.var([np.mean(v) for v in robot_means.values()]))
    within_var = float(np.mean([np.var(v) for v in robot_means.values()]))

    return {
        "total_variance": MetricValue(mean=total_var, std=0.0, n=len(all_deliveries)),
        "between_robot_variance": MetricValue(mean=between_var, std=0.0, n=len(robot_means)),
        "within_robot_variance": MetricValue(mean=within_var, std=0.0, n=len(robot_means)),
    }


def compute_region_extent(results: list) -> dict:
    """Compute the extent (spread) of the performance-autonomy region."""
    if not results:
        return {}

    tp = [r.throughput for r in results]
    au = [r.avg_autonomy for r in results]

    return {
        "throughput_range": MetricValue(mean=float(np.ptp(tp)), std=0.0, n=len(tp)),
        "autonomy_range": MetricValue(mean=float(np.ptp(au)), std=0.0, n=len(au)),
        "throughput_centroid": MetricValue(mean=float(np.mean(tp)), std=float(np.std(tp)), n=len(tp)),
        "autonomy_centroid": MetricValue(mean=float(np.mean(au)), std=float(np.std(au)), n=len(au)),
    }


def _count_diffs(a: dict, b: dict, depth: int = 0) -> int:
    count = 0
    all_keys = set(list(a.keys()) + list(b.keys()))
    for key in all_keys:
        va = a.get(key)
        vb = b.get(key)
        if isinstance(va, dict) and isinstance(vb, dict):
            count += _count_diffs(va, vb, depth + 1)
        elif va != vb:
            count += 1
    return count
