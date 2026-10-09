"""Experiment service — unified facade for synthetic and Unity runners."""

from typing import List, Dict, Optional

from backend.models.experiment_result import SingleResult
from backend.runners import synthetic_runner


def run_experiment(
    mode: str = "synthetic",
    sos_type: str = "directed",
    rho: float = 0.0,
    profile: str = "uniform",
    seed: int = 0,
    duration: float = 300.0,
    **kwargs,
) -> SingleResult:
    """Run a single experiment (synthetic or unity)."""
    if mode == "synthetic":
        return synthetic_runner.run_single(
            sos_type=sos_type, rho=rho, profile=profile,
            seed=seed, duration=duration, **kwargs,
        )
    elif mode == "unity":
        from backend.runners import unity_runner
        config = kwargs.get("config")
        if config is None:
            raise ValueError("Unity mode requires 'config' kwarg (CADLMotivationConfig)")
        return unity_runner.run_single(config=config, seed=seed, duration=duration, **kwargs)
    else:
        raise ValueError(f"Unknown mode: {mode}. Use 'synthetic' or 'unity'.")


def run_sweep(
    sos_type: str = "directed",
    profile: str = "linear",
    rho_values: Optional[List[float]] = None,
    num_seeds: int = 10,
    mode: str = "synthetic",
    **kwargs,
) -> List[SingleResult]:
    """Run a parameter sweep."""
    if rho_values is None:
        rho_values = [0.0, 0.25, 0.5, 0.75, 1.0]

    if mode == "synthetic":
        return synthetic_runner.run_sweep(sos_type, profile, rho_values, num_seeds)
    else:
        raise NotImplementedError("Unity sweep not yet implemented. Use synthetic mode.")


def run_comparison_sweep(
    profile: str = "linear",
    rho_values: Optional[List[float]] = None,
    num_seeds: int = 10,
    mode: str = "synthetic",
) -> Dict[str, List[SingleResult]]:
    """Run both D-SoS and C-SoS sweeps for comparison."""
    if mode == "synthetic":
        return synthetic_runner.run_comparison_sweep(profile, rho_values, num_seeds)
    else:
        raise NotImplementedError("Unity comparison sweep not yet implemented.")
