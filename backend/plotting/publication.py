"""Publication-quality plots (Matplotlib) for papers — PNG/PDF output."""

import numpy as np
from pathlib import Path
from typing import List, Optional

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Polygon
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False


def _ensure_matplotlib():
    if not HAS_MATPLOTLIB:
        raise ImportError("matplotlib required for publication plots. pip install matplotlib")


def pub_performance_autonomy(
    d_sos_results: list,
    c_sos_results: list,
    output_path: str = "performance_autonomy.pdf",
    selected_results: list = None,
    selected_label: str = "",
) -> str:
    """Performance-autonomy plane for paper figures."""
    _ensure_matplotlib()
    fig, ax = plt.subplots(1, 1, figsize=(8, 6))

    if d_sos_results:
        x = [r.avg_autonomy for r in d_sos_results]
        y = [r.throughput for r in d_sos_results]
        ax.scatter(x, y, c="tab:blue", alpha=0.4, s=30, label="D-SoS baseline", zorder=2)

    if c_sos_results:
        x = [r.avg_autonomy for r in c_sos_results]
        y = [r.throughput for r in c_sos_results]
        ax.scatter(x, y, c="tab:orange", alpha=0.4, s=30, label="C-SoS", zorder=2)

    if selected_results:
        x = [r.avg_autonomy for r in selected_results]
        y = [r.throughput for r in selected_results]
        ax.scatter(x, y, c="tab:red", s=60, marker="D", label=selected_label, zorder=3)

    ax.set_xlabel("System Autonomy", fontsize=13)
    ax.set_ylabel("Throughput (total deliveries)", fontsize=13)
    ax.set_xlim(-0.05, 1.05)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3)
    fig.tight_layout()

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return output_path


def pub_rho_effects(
    sweep_results: list,
    output_path: str = "rho_effects.pdf",
) -> str:
    """rho sweep plot for paper: throughput / autonomy / fairness."""
    _ensure_matplotlib()
    fig, axes = plt.subplots(1, 3, figsize=(14, 4.5))

    profiles = sorted(set(r.motivation_profile for r in sweep_results))
    colors = {"uniform": "tab:blue", "linear": "tab:green", "polarized": "tab:red"}
    metrics = [("throughput", "Throughput"), ("avg_autonomy", "Autonomy"), ("fairness", "Fairness")]

    for ax, (metric, ylabel) in zip(axes, metrics):
        for profile in profiles:
            subset = [r for r in sweep_results if r.motivation_profile == profile]
            rho_vals = sorted(set(r.rho for r in subset))
            means, stds = [], []
            for rho in rho_vals:
                vals = [getattr(r, metric) for r in subset if r.rho == rho]
                means.append(np.mean(vals) if vals else 0)
                stds.append(np.std(vals) if vals else 0)
            color = colors.get(profile, "gray")
            ax.errorbar(rho_vals, means, yerr=stds, marker="o", label=profile,
                        color=color, capsize=4, linewidth=1.5)
        ax.set_xlabel(r"$\rho$ (motivation sensitivity)", fontsize=12)
        ax.set_ylabel(ylabel, fontsize=12)
        ax.legend(fontsize=9)
        ax.grid(True, alpha=0.3)

    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return output_path


def pub_fairness_comparison(
    results_a: list,
    results_b: list,
    label_a: str = "D-SoS",
    label_b: str = "C-SoS",
    output_path: str = "fairness_comparison.pdf",
) -> str:
    """Fairness comparison bar chart for paper."""
    _ensure_matplotlib()
    from backend.evaluation.fairness_metrics import compute_fairness, compute_gini

    fa_a = compute_fairness(results_a)
    fa_b = compute_fairness(results_b)
    gi_a = compute_gini(results_a)
    gi_b = compute_gini(results_b)

    fig, axes = plt.subplots(1, 2, figsize=(10, 4.5))
    x = [0, 1]
    labels_x = [label_a, label_b]

    axes[0].bar(x, [fa_a.mean, fa_b.mean], yerr=[fa_a.std, fa_b.std],
                color=["tab:blue", "tab:orange"], capsize=5)
    axes[0].set_xticks(x)
    axes[0].set_xticklabels(labels_x)
    axes[0].set_ylabel("Fairness (1 - CV)", fontsize=12)
    axes[0].set_title("Fairness")

    axes[1].bar(x, [gi_a.mean, gi_b.mean], yerr=[gi_a.std, gi_b.std],
                color=["tab:blue", "tab:orange"], capsize=5)
    axes[1].set_xticks(x)
    axes[1].set_xticklabels(labels_x)
    axes[1].set_ylabel("Gini Coefficient", fontsize=12)
    axes[1].set_title("Gini Index")

    fig.tight_layout()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=300, bbox_inches="tight")
    plt.close(fig)
    return output_path
