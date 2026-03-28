"""GovernancePipeline — end-to-end causal chain execution.

CADL → IR → Config → Experiment → Evaluation → PipelineResult

Each stage's output is preserved in PipelineResult for traceability.
"""

from typing import List, Optional

from backend.models.pipeline_result import PipelineResult
from backend.models.evaluation_result import EvaluationResult
from backend.services.cadl_service import make_config, build_ir, generate_unity_config_dict
from backend.services.evaluation_service import evaluate_full
from backend.runners.synthetic_runner import run_single, run_sweep


class GovernancePipeline:
    """Executes the full governance causal chain."""

    def __init__(
        self,
        template: str = "A-SoS",
        profile: str = "uniform",
        rho: float = 0.0,
        num_seeds: int = 10,
        duration: float = 300.0,
        mode: str = "synthetic",
    ):
        self.template = template
        self.profile = profile
        self.rho = rho
        self.num_seeds = num_seeds
        self.duration = duration
        self.mode = mode

    def run(self) -> PipelineResult:
        """Execute the full pipeline and return PipelineResult."""
        # Stage 1: CADL
        cadl_config = make_config(self.template, self.profile, self.rho)

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

        return PipelineResult(
            name=cadl_config.name,
            template=self.template,
            profile=self.profile,
            rho=self.rho,
            seeds=list(range(self.num_seeds)),
            cadl=cadl_config.to_dict(),
            ir=ir.to_dict(),
            config=unity_config,
            results=results,
            evaluation=evaluation,
        )


def run_pipeline(
    template: str = "A-SoS",
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


def compare_pipelines(a: PipelineResult, b: PipelineResult) -> dict:
    """Compare two pipeline results across all stages.

    Returns dict with semantic diffs at each stage.
    """
    from backend.services.diff_service import (
        semantic_diff_cadl, semantic_diff_ir,
        semantic_diff_config, semantic_diff_result,
    )

    return {
        "cadl": semantic_diff_cadl(a.cadl, b.cadl, a.name, b.name),
        "ir": semantic_diff_ir(a.ir, b.ir, a.name, b.name),
        "config": semantic_diff_config(a.config, b.config, a.name, b.name),
        "result": semantic_diff_result(
            a.evaluation.to_dict() if a.evaluation else {},
            b.evaluation.to_dict() if b.evaluation else {},
            a.name, b.name,
        ),
    }
