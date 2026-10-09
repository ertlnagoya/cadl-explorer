"""Tests for GovernancePipeline and semantic diffs."""
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.services.pipeline import run_pipeline, compare_pipelines, GovernancePipeline, ComparisonResult
from backend.models.pipeline_result import PipelineResult, StageTrace
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
    cmp = compare_pipelines(pr_a, pr_b)
    assert isinstance(cmp, ComparisonResult)
    assert cmp.cadl is not None
    assert cmp.ir is not None
    assert cmp.config is not None
    assert cmp.result is not None
    assert isinstance(cmp.cadl, SemanticDiffResult)
    assert cmp.cadl.labels  # Should have changes


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


def test_pipeline_has_traces():
    pr = run_pipeline("A-SoS", "uniform", 0.0, num_seeds=3)
    assert len(pr.traces) == 5
    assert pr.traces[0].stage == "cadl"
    assert pr.traces[1].parent_id == pr.traces[0].content_id  # ir.parent = cadl
    assert pr.traces[2].parent_id == pr.traces[1].content_id  # config.parent = ir


def test_pipeline_metadata():
    pr = run_pipeline("A-SoS", "uniform", 0.0, num_seeds=2)
    assert pr.experiment_id  # non-empty
    assert pr.timestamp  # non-empty
    assert pr.pipeline_version  # non-empty
    assert pr.cadl_id  # content hash


def test_compare_returns_comparison_result():
    pr_a = run_pipeline("A-SoS", "uniform", 0.0, num_seeds=3)
    pr_b = run_pipeline("A-SoS + motivation-sensitive", "linear", 0.5, num_seeds=3)
    cmp = compare_pipelines(pr_a, pr_b)
    assert isinstance(cmp, ComparisonResult)
    assert cmp.pipeline_a == pr_a.name
    assert cmp.pipeline_b == pr_b.name
    assert cmp.all_labels  # should have labels
    assert cmp.summary  # non-empty
    d = cmp.to_dict()
    assert "stages" in d
    assert "all_labels" in d


def test_ir_diff_governance_interpretation():
    pr_a = run_pipeline("A-SoS", "uniform", 0.0, num_seeds=2)
    pr_b = run_pipeline("A-SoS + motivation-sensitive", "linear", 0.5, num_seeds=2)
    sd = semantic_diff_ir(pr_a.ir, pr_b.ir, pr_a.name, pr_b.name)
    # Should have governance-level labels, not just field counts
    categories = [lbl.category for lbl in sd.labels]
    assert any("Institution" in c for c in categories)
    assert any("Protocol" in c for c in categories)
    assert any("Algorithm" in c for c in categories)


def test_region_analysis():
    from backend.evaluation.region_analysis import compute_region, compare_regions
    pr_a = run_pipeline("A-SoS", "uniform", 0.0, num_seeds=5)
    pr_b = run_pipeline("A-SoS + motivation-sensitive", "linear", 0.5, num_seeds=5)
    ra = compute_region(pr_a.results, "A-SoS")
    rb = compute_region(pr_b.results, "Selected")
    assert ra.area >= 0
    assert rb.area >= 0
    assert len(ra.hull_vertices) >= 3
    cmp = compare_regions(ra, rb)
    assert "centroid_shift" in cmp
    assert "summary" in cmp


def test_rho_ignored_without_motivation_model():
    # Templates without a motivation model declare rho=0 in CADL; the
    # experiment must run the same thing the CADL config says.
    plain = run_pipeline(template="A-SoS", profile="linear", rho=0.0, num_seeds=3)
    with_rho = run_pipeline(template="A-SoS", profile="linear", rho=0.8, num_seeds=3)
    assert with_rho.rho == 0.0
    assert with_rho.cadl_id == plain.cadl_id
    assert with_rho.evaluation.to_dict() == plain.evaluation.to_dict()


def test_run_pipeline_for_config_matches_template():
    from backend.services.cadl_service import make_config
    from backend.services.pipeline import run_pipeline_for_config

    template = "A-SoS + motivation-sensitive"
    by_template = run_pipeline(template=template, profile="polarized", rho=0.5, num_seeds=3)
    by_config = run_pipeline_for_config(make_config(template, "polarized", 0.5), num_seeds=3)
    assert by_config.profile == "polarized"
    assert by_config.rho == 0.5
    assert by_config.cadl_id == by_template.cadl_id
    assert by_config.evaluation.to_dict() == by_template.evaluation.to_dict()
