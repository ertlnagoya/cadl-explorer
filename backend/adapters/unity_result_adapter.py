"""Unity result adapter — parses metrics_report.txt from Unity batch runs."""

import os
from pathlib import Path
from typing import Dict, List, Optional

from backend.models.experiment_result import SingleResult


def parse_metrics_report(path: str) -> Dict:
    """Parse a metrics_report.txt into structured dict."""
    result = {
        "duration": 0.0,
        "num_robots": 5,
        "timeseries": [],
        "final": {},
        "targets": {},
    }

    if not os.path.exists(path):
        return result

    with open(path, "r") as f:
        for line in f:
            line = line.strip()
            if line.startswith("# Duration:"):
                result["duration"] = float(line.split(":")[1].strip().rstrip("s"))
            elif line.startswith("# Robots:"):
                result["num_robots"] = int(line.split(":")[1].strip())
            elif line.startswith("#   "):
                parts = line[4:].split("->")
                if len(parts) == 2:
                    metric_name = parts[0].strip().split("=")[0].strip()
                    status = parts[1].strip()
                    result["targets"][metric_name] = status
            elif not line.startswith("#") and line:
                parts = line.split("\t")
                if len(parts) >= 5:
                    try:
                        row = {
                            "time": float(parts[0]),
                            "goal_sum": int(parts[1]),
                            "goal_min": int(parts[2]),
                            "collision_rate": float(parts[3]),
                            "retry_rate": float(parts[4]),
                        }
                        result["timeseries"].append(row)
                    except (ValueError, IndexError):
                        pass

    if result["timeseries"]:
        result["final"] = result["timeseries"][-1]

    return result


def parse_run_directory(output_dir, config=None) -> SingleResult:
    """Parse a Unity run output directory into a SingleResult.

    Args:
        output_dir: Path to run output containing metrics_report.txt
        config: Optional CADLMotivationConfig for metadata
    """
    output_dir = Path(output_dir)
    metrics_path = output_dir / "metrics_report.txt"
    parsed = parse_metrics_report(str(metrics_path))

    final = parsed.get("final", {})
    goal_sum = final.get("goal_sum", 0)
    goal_min = final.get("goal_min", 0)
    num_robots = parsed.get("num_robots", 5)
    duration = parsed.get("duration", 300.0)

    # Extract metadata from config if available
    sos_type = config.sos_type if config else "unknown"
    rho = config.governance_motivation.rho if config else 0.0
    profile = config.agent_motivation.profile if config else "unknown"

    return SingleResult(
        sos_type=sos_type,
        rho=rho,
        motivation_profile=profile,
        seed=0,
        throughput=float(goal_sum),
        avg_autonomy=0.0,  # Not available from metrics_report alone
        fairness=0.0,
        total_deliveries=goal_sum,
        goal_min=goal_min,
        per_robot=[],
    )
