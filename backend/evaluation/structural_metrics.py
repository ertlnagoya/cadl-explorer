"""Structural metrics: IR-level governance structure comparison.

Placeholder for future metrics such as:
- protocol complexity (number of steps)
- governance parameter distance
- layer coupling index
"""

from backend.models.evaluation_result import MetricValue


def compute_structural(ir_a=None, ir_b=None) -> dict:
    """Compare structural properties of two IRs.

    Returns dict of metric_name -> MetricValue.
    Currently a placeholder for future extensions.
    """
    result = {}
    if ir_a is not None and ir_b is not None:
        d_a = ir_a.to_dict()
        d_b = ir_b.to_dict()
        # Count fields that differ across all layers
        diff_count = _count_diffs(d_a, d_b)
        result["structural_distance"] = MetricValue(mean=float(diff_count), std=0.0, n=1)
    return result


def _count_diffs(a: dict, b: dict, depth: int = 0) -> int:
    """Recursively count differing leaf values."""
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
