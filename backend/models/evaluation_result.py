"""Data models for governance evaluation results."""

from dataclasses import dataclass, field
from typing import Dict, Optional


@dataclass
class MetricValue:
    """A single metric with mean, std, and sample count."""
    mean: float = 0.0
    std: float = 0.0
    n: int = 0


@dataclass
class EvaluationResult:
    """Aggregated governance evaluation across multiple runs."""
    throughput: MetricValue = field(default_factory=MetricValue)
    autonomy: MetricValue = field(default_factory=MetricValue)
    fairness: MetricValue = field(default_factory=MetricValue)
    extra: Dict[str, MetricValue] = field(default_factory=dict)

    def to_dict(self) -> dict:
        """Flat dict for backward compatibility with old evaluate() API."""
        return {
            "throughput": self.throughput.mean,
            "throughput_std": self.throughput.std,
            "autonomy": self.autonomy.mean,
            "autonomy_std": self.autonomy.std,
            "fairness": self.fairness.mean,
            "fairness_std": self.fairness.std,
            "n": self.throughput.n,
            **{k: v.mean for k, v in self.extra.items()},
        }
