"""Unity experiment runner — executes headless Unity simulations.

Delegates to unity adapters for config generation, execution, and result parsing.
"""

from typing import List, Optional
from pathlib import Path

from backend.models.experiment_result import SingleResult
from backend.adapters.unity_runner_adapter import is_available, run_headless
from backend.adapters.unity_config_adapter import write_config
from backend.adapters.unity_result_adapter import parse_run_directory


def run_single(
    config,
    seed: int = 0,
    duration: float = 300.0,
    unity_path: Optional[str] = None,
    output_dir: str = "./runs",
) -> SingleResult:
    """Run a single Unity simulation.

    Raises RuntimeError if Unity is not available.
    """
    if not is_available(unity_path):
        raise RuntimeError(
            "Unity is not available. Use synthetic runner or install Unity. "
            "Set UNITY_PATH environment variable to the Unity executable."
        )

    config_path = write_config(config, Path(output_dir) / "cadl_config.json")
    metrics_path = run_headless(
        config_path=str(config_path),
        duration=duration,
        output_dir=output_dir,
        unity_path=unity_path,
    )
    return parse_run_directory(metrics_path, config)
