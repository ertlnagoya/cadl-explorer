"""Region analysis for the performance-autonomy plane.

Computes feasible regions (convex hull, bounding box) for governance
configurations in the throughput-autonomy space.
"""

import numpy as np
from dataclasses import dataclass, field
from typing import List, Tuple, Optional


@dataclass
class Region:
    """A region in the performance-autonomy plane."""
    label: str = ""
    points: List[Tuple[float, float]] = field(default_factory=list)
    hull_vertices: List[Tuple[float, float]] = field(default_factory=list)
    bbox: Tuple[float, float, float, float] = (0, 0, 0, 0)  # (au_min, au_max, tp_min, tp_max)
    centroid: Tuple[float, float] = (0, 0)  # (au, tp)
    area: float = 0.0

    def to_dict(self) -> dict:
        return {
            "label": self.label,
            "n_points": len(self.points),
            "bbox": {"au_min": self.bbox[0], "au_max": self.bbox[1],
                     "tp_min": self.bbox[2], "tp_max": self.bbox[3]},
            "centroid": {"autonomy": self.centroid[0], "throughput": self.centroid[1]},
            "area": self.area,
            "hull_n_vertices": len(self.hull_vertices),
        }


def compute_region(results: list, label: str = "") -> Region:
    """Compute the feasible region from experiment results."""
    if not results:
        return Region(label=label)

    points = [(r.avg_autonomy, r.throughput) for r in results]
    arr = np.array(points)

    au_min, tp_min = arr.min(axis=0)
    au_max, tp_max = arr.max(axis=0)
    centroid = (float(arr[:, 0].mean()), float(arr[:, 1].mean()))

    hull_vertices = _convex_hull_2d(arr)
    area = _polygon_area(hull_vertices) if len(hull_vertices) >= 3 else 0.0

    return Region(
        label=label,
        points=[(float(x), float(y)) for x, y in points],
        hull_vertices=[(float(x), float(y)) for x, y in hull_vertices],
        bbox=(float(au_min), float(au_max), float(tp_min), float(tp_max)),
        centroid=centroid,
        area=float(area),
    )


def compare_regions(region_a: Region, region_b: Region) -> dict:
    """Compare two regions and describe the shift."""
    da = region_a.centroid
    db = region_b.centroid

    au_shift = db[0] - da[0]
    tp_shift = db[1] - da[1]

    interpretation = []
    if au_shift > 0.05:
        interpretation.append(f"autonomy shifted right (+{au_shift:.2f})")
    elif au_shift < -0.05:
        interpretation.append(f"autonomy shifted left ({au_shift:.2f})")
    if tp_shift > 2:
        interpretation.append(f"throughput shifted up (+{tp_shift:.1f})")
    elif tp_shift < -2:
        interpretation.append(f"throughput shifted down ({tp_shift:.1f})")

    area_ratio = region_b.area / max(region_a.area, 1e-6)
    if area_ratio > 1.2:
        interpretation.append(f"region expanded ({area_ratio:.1f}x)")
    elif area_ratio < 0.8:
        interpretation.append(f"region contracted ({area_ratio:.1f}x)")

    return {
        "centroid_shift": {"autonomy": au_shift, "throughput": tp_shift},
        "area_a": region_a.area,
        "area_b": region_b.area,
        "area_ratio": area_ratio,
        "interpretation": interpretation,
        "summary": "; ".join(interpretation) if interpretation else "regions are similar",
    }


def _convex_hull_2d(points: np.ndarray) -> List[Tuple[float, float]]:
    """Simple 2D convex hull (Graham scan). Returns hull vertices in order."""
    if len(points) < 3:
        return [(float(x), float(y)) for x, y in points]

    try:
        from scipy.spatial import ConvexHull
        hull = ConvexHull(points)
        return [(float(points[i, 0]), float(points[i, 1])) for i in hull.vertices]
    except (ImportError, Exception):
        pass

    # Fallback: bounding box corners
    xmin, ymin = points.min(axis=0)
    xmax, ymax = points.max(axis=0)
    return [(float(xmin), float(ymin)), (float(xmax), float(ymin)),
            (float(xmax), float(ymax)), (float(xmin), float(ymax))]


def _polygon_area(vertices: list) -> float:
    """Shoelace formula for polygon area."""
    n = len(vertices)
    if n < 3:
        return 0.0
    area = 0.0
    for i in range(n):
        j = (i + 1) % n
        area += vertices[i][0] * vertices[j][1]
        area -= vertices[j][0] * vertices[i][1]
    return abs(area) / 2.0
