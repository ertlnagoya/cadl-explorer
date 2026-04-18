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
# published (see cadl_repo / CHANGELOG).  UI code must route through
# these two functions instead of calling CADLMotivationConfig directly,
# so that the migration becomes a one-file change.

_USE_UPSTREAM_PARSER = False  # flip to True after `pip install cadl`


def parse_cadl_yaml(text: str) -> CADLMotivationConfig:
    """Parse a CADL YAML source string into a CADLMotivationConfig.

    Currently delegates to the bundled ``cadl_sim`` schema. When the
    upstream ``cadl`` package is available, flip ``_USE_UPSTREAM_PARSER``
    above and route through ``cadl.parser.parse_string`` instead.
    """
    data = yaml.safe_load(text) if isinstance(text, str) else text
    if not isinstance(data, dict):
        raise ValueError(
            "CADL YAML must parse to a mapping at the top level"
        )
    return CADLMotivationConfig.from_dict(data)


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
