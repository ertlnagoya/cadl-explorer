"""Evaluation service — aggregates all governance metric evaluators."""

from typing import List, Optional
import numpy as np

from backend.models.experiment_result import SingleResult
from backend.models.evaluation_result import EvaluationResult, MetricValue
from backend.evaluation.system_metrics import compute_throughput
from backend.evaluation.autonomy_metrics import compute_autonomy
from backend.evaluation.fairness_metrics import compute_fairness


def evaluate(results: list) -> dict:
    """Compute aggregate governance metrics.

    Returns flat dict for backward compatibility.
    Use evaluate_full() for structured EvaluationResult.
    """
    er = evaluate_full(results)
    return er.to_dict()


def evaluate_full(results: list) -> EvaluationResult:
    """Compute aggregate governance metrics as structured EvaluationResult."""
    if not results:
        return EvaluationResult()

    return EvaluationResult(
        throughput=compute_throughput(results),
        autonomy=compute_autonomy(results),
        fairness=compute_fairness(results),
    )


def compare(eval_a: dict, eval_b: dict, label_a: str, label_b: str) -> str:
    """Generate a text comparison of two governance evaluations."""
    lines = []
    lines.append(f"## Governance Comparison: {label_a} vs {label_b}")
    lines.append("")
    lines.append(f"| Metric | {label_a} | {label_b} | Delta |")
    lines.append("|--------|-----------|-----------|-------|")

    for metric, label in [
        ("throughput", "Throughput"),
        ("autonomy", "Autonomy"),
        ("fairness", "Fairness"),
    ]:
        va = eval_a.get(metric, 0)
        vb = eval_b.get(metric, 0)
        sa = eval_a.get(f"{metric}_std", 0)
        sb = eval_b.get(f"{metric}_std", 0)
        delta = vb - va
        sign = "+" if delta >= 0 else ""
        lines.append(
            f"| {label} | {va:.2f} +/- {sa:.2f} | {vb:.2f} +/- {sb:.2f} | {sign}{delta:.2f} |"
        )

    lines.append("")

    tp_a, tp_b = eval_a.get("throughput", 0), eval_b.get("throughput", 0)
    au_a, au_b = eval_a.get("autonomy", 0), eval_b.get("autonomy", 0)
    fa_a, fa_b = eval_a.get("fairness", 0), eval_b.get("fairness", 0)

    findings = []
    if tp_b < tp_a * 0.95:
        findings.append(f"Throughput decreased by {(1 - tp_b / max(tp_a, 1e-6)) * 100:.1f}%")
    elif tp_b > tp_a * 1.05:
        findings.append(f"Throughput increased by {(tp_b / max(tp_a, 1e-6) - 1) * 100:.1f}%")
    if au_b > au_a + 0.05:
        findings.append(f"Autonomy improved ({au_a:.2f} -> {au_b:.2f})")
    elif au_b < au_a - 0.05:
        findings.append(f"Autonomy decreased ({au_a:.2f} -> {au_b:.2f})")
    if fa_b < fa_a - 0.05:
        findings.append(f"Fairness decreased ({fa_a:.2f} -> {fa_b:.2f})")

    lines.append("### Key Findings")
    if findings:
        for f in findings:
            lines.append(f"- {f}")
    else:
        lines.append("- Minimal differences between configurations.")

    return "\n".join(lines)


def generate_summary(
    baseline_results: list,
    selected_results: list,
    baseline_label: str,
    selected_label: str,
) -> str:
    """Full governance pipeline summary."""
    eval_base = evaluate(baseline_results)
    eval_sel = evaluate(selected_results)
    return compare(eval_base, eval_sel, baseline_label, selected_label)
