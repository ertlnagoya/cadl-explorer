"""PipelineResult — captures the full causal chain of a governance pipeline run.

CADL → IR → Config → Experiment → Evaluation, with all intermediates preserved.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional
import json
import yaml

from backend.models.experiment_result import SingleResult
from backend.models.evaluation_result import EvaluationResult


@dataclass
class PipelineResult:
    """Complete result of one governance pipeline execution.

    Preserves every intermediate artifact for reproducibility and diff.
    """
    # Identity
    name: str = ""
    template: str = ""
    profile: str = ""
    rho: float = 0.0
    seeds: List[int] = field(default_factory=list)

    # Stage 1: CADL config (serializable dict)
    cadl: Optional[dict] = None

    # Stage 2: 3-layer IR (serializable dict)
    ir: Optional[dict] = None

    # Stage 3: Unity config (serializable dict)
    config: Optional[dict] = None

    # Stage 4: Raw experiment results
    results: List[SingleResult] = field(default_factory=list)

    # Stage 5: Aggregated evaluation
    evaluation: Optional[EvaluationResult] = None

    def to_dict(self) -> dict:
        """Serialize full pipeline result for JSON storage."""
        return {
            "name": self.name,
            "template": self.template,
            "profile": self.profile,
            "rho": self.rho,
            "seeds": self.seeds,
            "cadl": self.cadl,
            "ir": self.ir,
            "config": self.config,
            "results": [
                {
                    "sos_type": r.sos_type, "rho": r.rho,
                    "motivation_profile": r.motivation_profile, "seed": r.seed,
                    "throughput": r.throughput, "avg_autonomy": r.avg_autonomy,
                    "fairness": r.fairness, "total_deliveries": r.total_deliveries,
                    "goal_min": r.goal_min, "per_robot": r.per_robot,
                }
                for r in self.results
            ],
            "evaluation": self.evaluation.to_dict() if self.evaluation else None,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent)
