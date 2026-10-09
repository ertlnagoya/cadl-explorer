"""CADL service facade — config creation, IR generation, Unity config generation.

Thin facade over cadl/ core modules. UI code should use this
instead of importing cadl.* directly.
"""

import sys
import os
import json
import yaml

_REPO_ROOT = os.path.join(os.path.dirname(__file__), "..", "..")
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from cadl_sim.schema.motivation_schema import (
    CADLMotivationConfig,
    AgentMotivation,
    GovernanceMotivation,
)
from cadl_sim.ir.three_layer_ir import build_ir_from_config, ThreeLayerIR, ir_to_text
from cadl_sim.generators.unity.config_generator import generate_unity_config as _gen_unity


# ── Governance templates ────────────────────────────────────────────

# sos_type stored internally as lowercase for legacy compatibility;
# CADLMotivationConfig.to_dict() emits the CADL-canonical Capitalized
# form (Appendix A §A.1) in generated YAML.
TEMPLATES = {
    "A-SoS": {
        "sos_type": "directed",
        "alpha": 0.3,
        "beta": 0.7,
        "lambda_param": 0.0,
        "motivation_model": "none",
    },
    "C-SoS": {
        "sos_type": "collaborative",
        "alpha": 0.7,
        "beta": 0.3,
        "lambda_param": 0.3,
        "motivation_model": "none",
    },
    "A-SoS + motivation-sensitive": {
        "sos_type": "directed",
        "alpha": 0.3,
        "beta": 0.7,
        "lambda_param": 0.0,
        "motivation_model": "hybrid",
    },
}


def make_config(
    template: str,
    profile: str = "uniform",
    rho: float = 0.0,
) -> CADLMotivationConfig:
    """Create a CADLMotivationConfig from UI selections."""
    t = TEMPLATES[template]
    model = t["motivation_model"]
    if model != "none" and rho == 0.0:
        model = "none"
    effective_rho = 0.0 if model == "none" else rho

    return CADLMotivationConfig(
        name=f"{template.replace(' ', '-')}_{profile}_rho{effective_rho:.2f}",
        sos_type=t["sos_type"],
        description=f"{template} with {profile} profile, rho={effective_rho}",
        alpha=t["alpha"],
        beta=t["beta"],
        lambda_param=t["lambda_param"],
        agent_motivation=AgentMotivation(profile=profile),
        governance_motivation=GovernanceMotivation(
            motivation_model=model,
            rho=effective_rho,
            kappa=5.0,
            budget_base=3,
            wait_scale=3.0,
        ),
    )


def make_baseline_config() -> CADLMotivationConfig:
    """A-SoS baseline for diff comparison."""
    return make_config("A-SoS", "uniform", 0.0)


def load_experiment_config(yaml_path: str) -> CADLMotivationConfig:
    """Load a config from an experiment YAML file."""
    return CADLMotivationConfig.from_yaml(yaml_path)


# ── Parser swap point ───────────────────────────────────────────────
#
# Everything below is the single seam that will swap from the bundled
# ``cadl_sim`` schema to the upstream ``cadl`` package once it is
# published (see https://github.com/ertlnagoya/cadl).  UI code must route through
# these two functions instead of calling CADLMotivationConfig directly,
# so that the migration becomes a one-file change.

_USE_UPSTREAM_PARSER = False  # flip to True after `pip install cadl`


def parse_cadl_yaml(text: str) -> CADLMotivationConfig:
    """Parse a CADL YAML source string into a CADLMotivationConfig.

    Currently delegates to the bundled ``cadl_sim`` schema. When the
    upstream ``cadl`` package is available, flip ``_USE_UPSTREAM_PARSER``
    above and route through ``cadl.parser.parse_string`` instead.
    """
    if isinstance(text, str):
        _check_source_text(text)
        data = yaml.safe_load(text)
    else:
        data = text
    if not isinstance(data, dict):
        raise ValueError(
            "CADL YAML must parse to a mapping at the top level"
        )
    _check_fields(data)
    return CADLMotivationConfig.from_dict(data)


# ── Input limits ────────────────────────────────────────────────────
#
# The text comes from a public text area, so its size and every value
# that drives an allocation are bounded before a config is built.

MAX_SOURCE_CHARS = 20_000
MAX_ROBOTS = 100
MAX_NODES = 1_000
MAX_EDGES = 10_000
_PROFILES = ("uniform", "linear", "polarized", "custom")
_MODELS = ("none", "commitment_budget", "hybrid")


def _check_source_text(text: str) -> None:
    if len(text) > MAX_SOURCE_CHARS:
        raise ValueError(
            f"CADL YAML is too long ({len(text)} characters; "
            f"limit {MAX_SOURCE_CHARS})"
        )
    # Anchors and aliases let a short text expand into a huge structure.
    for token in yaml.scan(text):
        if isinstance(token, (yaml.AliasToken, yaml.AnchorToken)):
            raise ValueError("YAML anchors and aliases are not supported")


def _section(data: dict, key: str) -> dict:
    value = data.get(key)
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value


def _check_int(section: dict, path: str, key: str, low: int, high: int) -> None:
    if key not in section:
        return
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{path}.{key} must be an integer")
    if not low <= value <= high:
        raise ValueError(f"{path}.{key} must be between {low} and {high}")


def _check_number(section: dict, path: str, key: str,
                  low: float = None, high: float = None) -> None:
    if key not in section:
        return
    value = section[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{path}.{key} must be a number")
    if low is not None and not low <= value <= high:
        raise ValueError(f"{path}.{key} must be between {low} and {high}")


def _check_choice(section: dict, path: str, key: str, choices) -> None:
    if key in section and section[key] not in choices:
        raise ValueError(
            f"{path}.{key} must be one of: {', '.join(choices)}"
        )


def _check_fields(data: dict) -> None:
    """Reject values the pipeline cannot handle, with the field path."""
    for key in ("name", "description"):
        if key in data and not isinstance(data[key], str):
            raise ValueError(f"{key} must be a string")

    env = _section(data, "environment")
    _check_int(env, "environment", "num_robots", 1, MAX_ROBOTS)
    _check_int(env, "environment", "num_nodes", 1, MAX_NODES)
    _check_int(env, "environment", "num_edges", 0, MAX_EDGES)

    gov = _section(data, "governance")
    for key in ("alpha", "beta", "lambda"):
        _check_number(gov, "governance", key, 0.0, 1.0)

    mot = _section(data, "motivation")
    agent = _section(mot, "agent")
    _check_choice(agent, "motivation.agent", "profile", _PROFILES)
    values = agent.get("values")
    if values is not None:
        if not isinstance(values, list) or len(values) > MAX_ROBOTS:
            raise ValueError(
                f"motivation.agent.values must be a list of at most "
                f"{MAX_ROBOTS} numbers"
            )
        for v in values:
            if isinstance(v, bool) or not isinstance(v, (int, float)):
                raise ValueError("motivation.agent.values must contain numbers")

    gov_mot = _section(mot, "governance")
    _check_choice(gov_mot, "motivation.governance", "model", _MODELS)
    _check_number(gov_mot, "motivation.governance", "rho", 0.0, 1.0)
    for key in ("kappa", "budget_base", "wait_scale"):
        _check_number(gov_mot, "motivation.governance", key)


def parse_cadl_file(path: str) -> CADLMotivationConfig:
    """Parse a CADL YAML source file into a CADLMotivationConfig."""
    return CADLMotivationConfig.from_yaml(path)


def config_to_yaml_str(config: CADLMotivationConfig) -> str:
    return yaml.dump(config.to_dict(), default_flow_style=False, sort_keys=False)


def build_ir(config: CADLMotivationConfig) -> ThreeLayerIR:
    return build_ir_from_config(config)


def ir_to_dict(ir: ThreeLayerIR) -> dict:
    return ir.to_dict()


def ir_to_json_str(ir: ThreeLayerIR) -> str:
    return json.dumps(ir.to_dict(), indent=2)


def generate_unity_config_dict(config: CADLMotivationConfig) -> dict:
    return _gen_unity(config)


def unity_config_to_json_str(config: CADLMotivationConfig) -> str:
    return json.dumps(_gen_unity(config), indent=2)
