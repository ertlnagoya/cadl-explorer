"""Data models for experiment results."""

from dataclasses import dataclass, field
from typing import List, Dict


@dataclass
class SingleResult:
    """Result from a single experiment condition (one run)."""
    sos_type: str
    rho: float
    motivation_profile: str
    seed: int
    throughput: float
    avg_autonomy: float
    fairness: float
    total_deliveries: int
    goal_min: int
    per_robot: List[Dict] = field(default_factory=list)
