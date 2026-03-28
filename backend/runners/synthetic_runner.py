"""Synthetic experiment runner using an analytical model.

No Unity installation required. Generates plausible results
calibrated against real simulation observations.
"""

import random
from typing import List, Dict, Optional

from backend.models.experiment_result import SingleResult

import sys, os
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from cadl.schema.motivation_schema import AgentMotivation


def run_single(
    sos_type: str,
    rho: float,
    profile: str,
    seed: int,
    duration: float = 300.0,
    kappa: float = 5.0,
    budget_base: int = 3,
) -> SingleResult:
    """Run one synthetic experiment condition."""
    random.seed(seed + hash(f"{sos_type}_{rho}_{profile}") % 10000)

    num_robots = 5
    motivation_values = AgentMotivation(profile=profile).resolve(num_robots)

    CALIBRATION_DURATION_S = 120.0
    duration_scale = duration / CALIBRATION_DURATION_S

    if sos_type == "directed":
        base_tp = 4.0 * duration_scale
        base_au = 0.15
    else:
        base_tp = 3.5 * duration_scale
        base_au = 0.65

    rho_tp = -0.75 * rho * duration_scale
    rho_au = 0.35 * rho

    robot_goals = []
    per_robot = []
    for i, m_i in enumerate(motivation_values):
        if sos_type == "directed":
            budget_i = budget_base + kappa * m_i
            throttle = rho * max(0, base_tp - budget_i) * 0.05
            goals = max(1, int(
                (base_tp + rho_tp)
                * (1 + 0.3 * (m_i - 0.5))
                - throttle
                + random.gauss(0, 0.5 * duration_scale)
            ))
            freedom = max(0.0, min(1.0, 0.15 + 0.3 * rho * m_i + random.gauss(0, 0.05)))
        else:
            goals = max(1, int(
                base_tp * (0.5 + 0.5 * m_i)
                + random.gauss(0, 0.6 * duration_scale)
            ))
            freedom = max(0.0, min(1.0, 0.65 + 0.2 * (1 - m_i) + random.gauss(0, 0.05)))

        robot_goals.append(goals)
        per_robot.append({
            "robot_id": i,
            "motivation": m_i,
            "deliveries": goals,
            "freedom": freedom,
        })

    goal_sum = sum(robot_goals)
    goal_min = min(robot_goals)
    avg_autonomy = max(0.0, min(1.0, base_au + rho_au + random.gauss(0, 0.02)))

    if len(robot_goals) > 1:
        mean_g = goal_sum / len(robot_goals)
        var_g = sum((g - mean_g) ** 2 for g in robot_goals) / len(robot_goals)
        fairness = max(0.0, 1.0 - (var_g / (mean_g ** 2 + 1e-6)))
    else:
        fairness = 1.0

    return SingleResult(
        sos_type=sos_type,
        rho=rho,
        motivation_profile=profile,
        seed=seed,
        throughput=float(goal_sum),
        avg_autonomy=avg_autonomy,
        fairness=fairness,
        total_deliveries=goal_sum,
        goal_min=goal_min,
        per_robot=per_robot,
    )


def run_sweep(
    sos_type: str,
    profile: str,
    rho_values: List[float],
    num_seeds: int = 10,
    duration: float = 300.0,
) -> List[SingleResult]:
    """Run a sweep across rho values."""
    results = []
    for rho in rho_values:
        for seed in range(num_seeds):
            results.append(run_single(
                sos_type=sos_type, rho=rho, profile=profile,
                seed=seed, duration=duration,
            ))
    return results


def run_comparison_sweep(
    profile: str = "linear",
    rho_values: Optional[List[float]] = None,
    num_seeds: int = 10,
) -> Dict[str, List[SingleResult]]:
    """Run both A-SoS and C-SoS sweeps for comparison."""
    if rho_values is None:
        rho_values = [0.0, 0.25, 0.5, 0.75, 1.0]
    return {
        "a_sos": run_sweep("directed", profile, rho_values, num_seeds),
        "c_sos": run_sweep("collaborative", profile, [0.0], num_seeds),
    }
