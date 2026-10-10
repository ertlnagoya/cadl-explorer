"""PipelineResult — captures the full causal chain of a governance pipeline run.

CADL → IR → Config → Experiment → Evaluation, with all intermediates preserved.
Each stage has a content-hash ID for traceability.
"""

import hashlib
import json
import yaml
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional

from backend.models.experiment_result import SingleResult
from backend.models.evaluation_result import EvaluationResult

PIPELINE_VERSION = "0.5.1"


def _content_hash(obj) -> str:
    """Deterministic short hash of a JSON-serializable object."""
    raw = json.dumps(obj, sort_keys=True, default=str).encode()
    return hashlib.sha256(raw).hexdigest()[:12]


@dataclass
class StageTrace:
    """Traceability record for one pipeline stage."""
    stage: str          # "cadl", "ir", "config", "result", "evaluation"
    content_id: str     # content hash
    parent_id: str = "" # content hash of the upstream stage


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

    # Metadata
    experiment_id: str = ""
    timestamp: str = ""
    pipeline_version: str = PIPELINE_VERSION

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

    # Traceability
    traces: List[StageTrace] = field(default_factory=list)

    def compute_traces(self):
        """Compute content-hash IDs for each stage and link parent relations."""
        self.traces = []
        cadl_id = _content_hash(self.cadl) if self.cadl else ""
        ir_id = _content_hash(self.ir) if self.ir else ""
        config_id = _content_hash(self.config) if self.config else ""
        result_id = _content_hash([r.__dict__ for r in self.results]) if self.results else ""
        eval_id = _content_hash(self.evaluation.to_dict()) if self.evaluation else ""

        self.traces = [
            StageTrace(stage="cadl", content_id=cadl_id, parent_id=""),
            StageTrace(stage="ir", content_id=ir_id, parent_id=cadl_id),
            StageTrace(stage="config", content_id=config_id, parent_id=ir_id),
            StageTrace(stage="result", content_id=result_id, parent_id=config_id),
            StageTrace(stage="evaluation", content_id=eval_id, parent_id=result_id),
        ]

    @property
    def cadl_id(self) -> str:
        return _content_hash(self.cadl) if self.cadl else ""

    @property
    def ir_id(self) -> str:
        return _content_hash(self.ir) if self.ir else ""

    @property
    def config_id(self) -> str:
        return _content_hash(self.config) if self.config else ""

    def to_dict(self) -> dict:
        """Serialize full pipeline result for JSON storage."""
        return {
            "name": self.name,
            "template": self.template,
            "profile": self.profile,
            "rho": self.rho,
            "seeds": self.seeds,
            "experiment_id": self.experiment_id,
            "timestamp": self.timestamp,
            "pipeline_version": self.pipeline_version,
            "traces": [
                {"stage": t.stage, "content_id": t.content_id, "parent_id": t.parent_id}
                for t in self.traces
            ],
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
