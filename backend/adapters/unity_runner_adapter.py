"""Unity runner adapter — executes Unity in headless batch mode."""

import os
import subprocess
import shutil
from pathlib import Path
from typing import Optional


def is_available(unity_path: Optional[str] = None) -> bool:
    """Check whether Unity executable is accessible."""
    path = unity_path or os.environ.get("UNITY_PATH", "")
    if not path:
        return False
    return os.path.isfile(path) and os.access(path, os.X_OK)


def run_headless(
    config_path: str,
    duration: float = 300.0,
    output_dir: str = "./runs",
    unity_path: Optional[str] = None,
    timeout: int = 600,
) -> Path:
    """Execute Unity in headless batch mode.

    Returns path to the output metrics directory.
    Raises RuntimeError if execution fails.
    """
    exe = unity_path or os.environ.get("UNITY_PATH", "")
    if not exe:
        raise RuntimeError("UNITY_PATH not set and unity_path not provided.")

    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Copy config to Unity's StreamingAssets
    streaming = Path(exe).parent / "Assets" / "streamingAssets"
    if streaming.exists():
        shutil.copy2(config_path, streaming / "cadl_config.json")

    cmd = [
        exe, "-batchmode", "-nographics",
        "-executeMethod", "CADLConfig.BatchRunner.RunHeadless",
        "-batch-duration", str(int(duration)),
        "-batch-output", str(output_path),
        "-quit",
    ]

    try:
        subprocess.run(cmd, timeout=timeout, capture_output=True, text=True, check=True)
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"Unity timed out after {timeout}s")
    except subprocess.CalledProcessError as e:
        raise RuntimeError(f"Unity failed: {e.stderr}")

    return output_path
