"""Run manager — creates and manages experiment output directories.

Each run preserves the full pipeline state for reproducibility:
  cadl/ ir/ configs/ logs/ metrics/ plots/ report/ manifest.json
"""

import os
import json
import yaml
from datetime import datetime
from pathlib import Path
from typing import Optional


RUNS_DIR = os.path.realpath(os.path.join(os.path.dirname(__file__), "..", "..", "runs"))


class RunDir:
    """Manages a single experiment run's output directory."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self._ensure_subdirs()

    def _ensure_subdirs(self):
        for sub in ["cadl", "ir", "configs", "logs", "metrics", "plots", "report"]:
            (self.path / sub).mkdir(parents=True, exist_ok=True)

    def save_cadl(self, config, filename: str = "config.yaml"):
        with open(self.path / "cadl" / filename, "w") as f:
            yaml.dump(config.to_dict(), f, default_flow_style=False, sort_keys=False)

    def save_ir(self, ir, filename: str = "ir.json"):
        with open(self.path / "ir" / filename, "w") as f:
            json.dump(ir.to_dict(), f, indent=2)

    def save_config(self, config_dict: dict, filename: str = "cadl_config.json"):
        with open(self.path / "configs" / filename, "w") as f:
            json.dump(config_dict, f, indent=2)

    def save_metrics(self, metrics: dict, filename: str = "metrics.json"):
        with open(self.path / "metrics" / filename, "w") as f:
            json.dump(metrics, f, indent=2)

    def save_plot(self, fig_or_path, filename: str = "plot.png"):
        dest = self.path / "plots" / filename
        if isinstance(fig_or_path, (str, Path)):
            import shutil
            shutil.copy2(str(fig_or_path), str(dest))
        else:
            fig_or_path.write_image(str(dest))

    def save_report(self, text: str, filename: str = "summary.md"):
        with open(self.path / "report" / filename, "w") as f:
            f.write(text)

    def save_pipeline_result(self, pipeline_result):
        """Save a complete PipelineResult with replay manifest."""
        pr = pipeline_result

        # Save all artifacts
        if pr.cadl:
            with open(self.path / "cadl" / "config.yaml", "w") as f:
                yaml.dump(pr.cadl, f, default_flow_style=False, sort_keys=False)
        if pr.ir:
            with open(self.path / "ir" / "ir.json", "w") as f:
                json.dump(pr.ir, f, indent=2)
        if pr.config:
            with open(self.path / "configs" / "cadl_config.json", "w") as f:
                json.dump(pr.config, f, indent=2)
        if pr.evaluation:
            with open(self.path / "metrics" / "evaluation.json", "w") as f:
                json.dump(pr.evaluation.to_dict(), f, indent=2)

        # Save raw results
        if pr.results:
            raw = [
                {"sos_type": r.sos_type, "rho": r.rho, "profile": r.motivation_profile,
                 "seed": r.seed, "throughput": r.throughput, "autonomy": r.avg_autonomy,
                 "fairness": r.fairness, "deliveries": r.total_deliveries,
                 "goal_min": r.goal_min, "per_robot": r.per_robot}
                for r in pr.results
            ]
            with open(self.path / "metrics" / "raw_results.json", "w") as f:
                json.dump(raw, f, indent=2)

        # Save replay manifest
        manifest = {
            "created_at": datetime.now().isoformat(),
            "name": pr.name,
            "template": pr.template,
            "profile": pr.profile,
            "rho": pr.rho,
            "seeds": pr.seeds,
            "num_results": len(pr.results),
            "mode": "synthetic",
        }
        with open(self.path / "manifest.json", "w") as f:
            json.dump(manifest, f, indent=2)

    def load_manifest(self) -> Optional[dict]:
        """Load the replay manifest if it exists."""
        manifest_path = self.path / "manifest.json"
        if manifest_path.exists():
            with open(manifest_path) as f:
                return json.load(f)
        return None

    def __str__(self):
        return str(self.path)


def create_run(name: str = "", base_dir: Optional[str] = None) -> RunDir:
    """Create a new timestamped run directory."""
    base = Path(base_dir or RUNS_DIR)
    timestamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dir_name = f"{timestamp}_{name}" if name else timestamp
    run_path = base / dir_name
    run_path.mkdir(parents=True, exist_ok=True)
    return RunDir(run_path)


def get_latest_run(base_dir: Optional[str] = None) -> Optional[RunDir]:
    """Get the most recent run directory."""
    base = Path(base_dir or RUNS_DIR)
    if not base.exists():
        return None
    dirs = sorted([d for d in base.iterdir() if d.is_dir() and d.name != ".gitkeep"], reverse=True)
    return RunDir(dirs[0]) if dirs else None


def replay_run(run_dir: str) -> "PipelineResult":
    """Re-execute a run from its manifest.

    Reads manifest.json and re-runs the pipeline with identical parameters.
    """
    from backend.services.pipeline import run_pipeline

    rd = RunDir(Path(run_dir))
    manifest = rd.load_manifest()
    if not manifest:
        raise FileNotFoundError(f"No manifest.json in {run_dir}")

    return run_pipeline(
        template=manifest["template"],
        profile=manifest["profile"],
        rho=manifest["rho"],
        num_seeds=len(manifest.get("seeds", [10])),
    )
