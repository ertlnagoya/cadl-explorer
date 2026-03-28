"""Tests for GovernancePipeline and semantic diffs."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.pipeline import run_pipeline, compare_pipelines, GovernancePipeline
from backend.models.pipeline_result import PipelineResult
from backend.services.diff_service import (
    semantic_diff_cadl, semantic_diff_ir, semantic_diff_config, semantic_diff_result,
    SemanticDiffResult, SemanticLabel,
)
from backend.evaluation.structural_metrics import (
    compute_motivation_outcome_correlation, compute_variance_structure, compute_region_extent,
)


def test_pipeline_run():
    pr = run_pipeline("A-SoS", "uniform", 0.0, num_seeds=3)
    assert isinstance(pr, PipelineResult)
    assert pr.cadl is not None
    assert pr.ir is not None
    assert pr.config is not None
    assert len(pr.results) == 3
    assert pr.evaluation is not None
    assert pr.evaluation.throughput.mean > 0


def test_pipeline_serialization():
    pr = run_pipeline("A-SoS", "linear", 0.5, num_seeds=2)
    d = pr.to_dict()
    assert d["template"] == "A-SoS"
    assert d["cadl"] is not None
    assert len(d["results"]) == 2
    j = pr.to_json()
    assert '"template"' in j


def test_compare_pipelines():
    pr_a = run_pipeline("A-SoS", "uniform", 0.0, num_seeds=3)
    pr_b = run_pipeline("A-SoS + motivation-sensitive", "linear", 0.5, num_seeds=3)
    diffs = compare_pipelines(pr_a, pr_b)
    assert "cadl" in diffs
    assert "ir" in diffs
    assert "config" in diffs
    assert "result" in diffs
    assert isinstance(diffs["cadl"], SemanticDiffResult)
    assert diffs["cadl"].labels  # Should have changes


def test_semantic_diff_result_labels():
    pr_a = run_pipeline("A-SoS", "uniform", 0.0, num_seeds=5)
    pr_b = run_pipeline("A-SoS + motivation-sensitive", "linear", 0.5, num_seeds=5)
    sd = semantic_diff_result(
        pr_a.evaluation.to_dict(), pr_b.evaluation.to_dict(),
        pr_a.name, pr_b.name,
    )
    assert any(lbl.field == "autonomy" for lbl in sd.labels)
    assert any("increased" in lbl.direction or "decreased" in lbl.direction for lbl in sd.labels)
    assert sd.summary


def test_semantic_diff_html():
    sd = semantic_diff_result(
        {"throughput": 50.0, "autonomy": 0.15, "fairness": 0.98},
        {"throughput": 40.0, "autonomy": 0.35, "fairness": 0.95},
        "A", "B",
    )
    html = sd.to_html()
    assert "Throughput" in html or "Performance" in html


def test_motivation_outcome_correlation():
    pr = run_pipeline("A-SoS + motivation-sensitive", "linear", 0.5, num_seeds=5)
    corr = compute_motivation_outcome_correlation(pr.results)
    assert corr.n > 0
    assert -1 <= corr.mean <= 1


def test_variance_structure():
    pr = run_pipeline("A-SoS + motivation-sensitive", "linear", 0.5, num_seeds=5)
    vs = compute_variance_structure(pr.results)
    assert "total_variance" in vs
    assert "between_robot_variance" in vs
    assert "within_robot_variance" in vs


def test_region_extent():
    pr = run_pipeline("A-SoS + motivation-sensitive", "linear", 0.5, num_seeds=5)
    re = compute_region_extent(pr.results)
    assert "throughput_range" in re
    assert "autonomy_range" in re
    assert re["throughput_centroid"].mean > 0
