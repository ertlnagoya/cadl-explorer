"""Core services for the CADL governance pipeline.

Central entry point: GovernancePipeline (pipeline.py)
  CADL -> IR -> Config -> Simulation -> Evaluation -> PipelineResult
"""
from backend.services.pipeline import (  # noqa: F401
    GovernancePipeline,
    run_pipeline,
    compare_pipelines,
)
