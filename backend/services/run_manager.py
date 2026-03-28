"""Run manager — creates and manages experiment output directories."""

import os
import json
import yaml
from datetime import datetime
from pathlib import Path
from typing import Optional


RUNS_DIR = os.path.join(os.path.dirname(__file__), "..", "..", "runs")


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
            # Assume plotly figure
            fig_or_path.write_image(str(dest))

    def save_report(self, text: str, filename: str = "summary.md"):
        with open(self.path / "report" / filename, "w") as f:
            f.write(text)

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
