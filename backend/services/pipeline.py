"""GovernancePipeline — end-to-end causal chain execution.

CADL → IR → Config → Experiment → Evaluation → PipelineResult

Each stage's output is preserved in PipelineResult for traceability.
"""

import uuid
from datetime import datetime
from dataclasses import dataclass, field
from typing import List, Optional, Dict

from backend.models.pipeline_result import PipelineResult
from backend.models.evaluation_result import EvaluationResult
from backend.services.cadl_service import (
    make_config, build_ir, generate_unity_config_dict, resolve_template,
)
from backend.services.evaluation_service import evaluate_full
from backend.runners.synthetic_runner import run_single, run_sweep


class GovernancePipeline:
    """Executes the full governance causal chain."""

    def __init__(
        self,
        template: str = "D-SoS",
        profile: str = "uniform",
        rho: float = 0.0,
        num_seeds: int = 10,
        duration: float = 300.0,
        mode: str = "synthetic",
        config=None,
    ):
        # An explicit CADLMotivationConfig takes precedence over the
        # template / profile / rho triple (used for custom CADL input).
        self.config = config
        if config is None:
            template = resolve_template(template)
        self.template = template
        self.profile = profile
        self.rho = rho
        self.num_seeds = num_seeds
        self.duration = duration
        self.mode = mode

    def run(self) -> PipelineResult:
        """Execute the full pipeline and return PipelineResult."""
        # Stage 1: CADL
        if self.config is not None:
            cadl_config = self.config
            self.profile = cadl_config.agent_motivation.profile
        else:
            cadl_config = make_config(self.template, self.profile, self.rho)
        # The experiment must run what the CADL config declares: rho only
        # takes effect when a motivation model is present.
        gov = cadl_config.governance_motivation
        self.rho = float(gov.rho) if gov.motivation_model != "none" else 0.0

        # Stage 2: IR
        ir = build_ir(cadl_config)

        # Stage 3: Config
        unity_config = generate_unity_config_dict(cadl_config)

        # Stage 4: Experiment
        results = run_sweep(
            sos_type=cadl_config.sos_type,
            profile=self.profile,
            rho_values=[self.rho],
            num_seeds=self.num_seeds,
            duration=self.duration,
        )

        # Stage 5: Evaluation
        evaluation = evaluate_full(results)

        pr = PipelineResult(
            name=cadl_config.name,
            template=self.template,
            profile=self.profile,
            rho=self.rho,
            seeds=list(range(self.num_seeds)),
            experiment_id=uuid.uuid4().hex[:8],
            timestamp=datetime.now().isoformat(),
            cadl=cadl_config.to_dict(),
            ir=ir.to_dict(),
            config=unity_config,
            results=results,
            evaluation=evaluation,
        )
        pr.compute_traces()
        return pr


def run_pipeline(
    template: str = "D-SoS",
    profile: str = "uniform",
    rho: float = 0.0,
    num_seeds: int = 10,
    **kwargs,
) -> PipelineResult:
    """Convenience function to run a single pipeline."""
    return GovernancePipeline(
        template=template, profile=profile, rho=rho,
        num_seeds=num_seeds, **kwargs,
    ).run()


def run_pipeline_for_config(config, num_seeds: int = 10, **kwargs) -> PipelineResult:
    """Run the pipeline for an explicit CADLMotivationConfig."""
    return GovernancePipeline(
        template=kwargs.pop("template", "custom"),
        num_seeds=num_seeds, config=config, **kwargs,
    ).run()


# ── Pipeline comparison ──────────────────────────────────────────────


@dataclass
class ComparisonResult:
    """Structured comparison of two PipelineResults across all stages."""
    pipeline_a: str = ""
    pipeline_b: str = ""
    cadl: "SemanticDiffResult" = None
    ir: "SemanticDiffResult" = None
    config: "SemanticDiffResult" = None
    result: "SemanticDiffResult" = None

    @property
    def all_labels(self) -> list:
        labels = []
        for stage in [self.cadl, self.ir, self.config, self.result]:
            if stage:
                labels.extend(stage.labels)
        return labels

    @property
    def summary(self) -> str:
        parts = []
        for name, stage in [("CADL", self.cadl), ("IR", self.ir),
                            ("Config", self.config), ("Result", self.result)]:
            if stage and stage.labels:
                parts.append(f"{name}: {stage.summary}")
        return " | ".join(parts) if parts else "No differences"

    def to_dict(self) -> dict:
        return {
            "pipeline_a": self.pipeline_a,
            "pipeline_b": self.pipeline_b,
            "stages": {
                "cadl": {"summary": self.cadl.summary if self.cadl else "", "n_labels": len(self.cadl.labels) if self.cadl else 0},
                "ir": {"summary": self.ir.summary if self.ir else "", "n_labels": len(self.ir.labels) if self.ir else 0},
                "config": {"summary": self.config.summary if self.config else "", "n_labels": len(self.config.labels) if self.config else 0},
                "result": {"summary": self.result.summary if self.result else "", "n_labels": len(self.result.labels) if self.result else 0},
            },
            "all_labels": [
                {"category": l.category, "field": l.field, "direction": l.direction, "summary": l.summary}
                for l in self.all_labels
            ],
        }


def compare_pipelines(a: PipelineResult, b: PipelineResult) -> ComparisonResult:
    """Compare two pipeline results across all stages.

    Returns ComparisonResult with semantic diffs at each stage.
    """
    from backend.services.diff_service import (
        semantic_diff_cadl, semantic_diff_ir,
        semantic_diff_config, semantic_diff_result,
    )

    return ComparisonResult(
        pipeline_a=a.name,
        pipeline_b=b.name,
        cadl=semantic_diff_cadl(a.cadl, b.cadl, a.name, b.name),
        ir=semantic_diff_ir(a.ir, b.ir, a.name, b.name),
        config=semantic_diff_config(a.config, b.config, a.name, b.name),
        result=semantic_diff_result(
            a.evaluation.to_dict() if a.evaluation else {},
            b.evaluation.to_dict() if b.evaluation else {},
            a.name, b.name,
        ),
    )
