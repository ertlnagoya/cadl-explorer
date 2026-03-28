"""
Governance evaluation: performance, autonomy, fairness summary.
"""

from typing import List, Dict
import numpy as np


def evaluate(results: list) -> dict:
    """Compute aggregate governance metrics from a list of SingleResult."""
    if not results:
        return {"throughput": 0, "autonomy": 0, "fairness": 0, "summary": "No data"}

    tp = np.mean([r.throughput for r in results])
    au = np.mean([r.avg_autonomy for r in results])
    fa = np.mean([r.fairness for r in results])
    tp_std = np.std([r.throughput for r in results])
    au_std = np.std([r.avg_autonomy for r in results])
    fa_std = np.std([r.fairness for r in results])

    return {
        "throughput": float(tp),
        "throughput_std": float(tp_std),
        "autonomy": float(au),
        "autonomy_std": float(au_std),
        "fairness": float(fa),
        "fairness_std": float(fa_std),
        "n": len(results),
    }


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

    # Qualitative summary
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

    if findings:
        lines.append("### Key Findings")
        for f in findings:
            lines.append(f"- {f}")
    else:
        lines.append("### Key Findings")
        lines.append("- Minimal differences between the two configurations.")

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
