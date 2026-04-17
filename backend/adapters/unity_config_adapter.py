"""Unity config adapter — generates cadl_config.json for Unity simulator."""

import json
from pathlib import Path
from typing import Optional

import sys, os
_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)
from cadl_sim.generators.unity.config_generator import generate_unity_config


def generate_config(config) -> dict:
    """Generate Unity config dict from a CADLMotivationConfig."""
    return generate_unity_config(config)


def write_config(config, output_path: Path) -> Path:
    """Generate and write Unity config to disk."""
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    config_dict = generate_config(config)
    with open(output_path, "w") as f:
        json.dump(config_dict, f, indent=2)
    return output_path
