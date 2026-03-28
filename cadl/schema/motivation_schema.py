"""
CADL Schema Extension for A-SoS Motivation-Sensitive Governance.

Defines the YAML schema for describing motivation profiles, governance
interpretation, and experiment sweep parameters.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
from enum import Enum
import yaml
import json


class MotivationModel(Enum):
    """How the governance layer interprets motivation."""
    NONE = "none"                           # Baseline: ignore motivation
    COMMITMENT_BUDGET = "commitment_budget"  # Budget-constrained dispatch
    HYBRID = "hybrid"                        # Budget + preference sensitivity


class MotivationProfile(Enum):
    """Pre-defined motivation distributions across robots."""
    UNIFORM = "uniform"        # All robots have m=0.5
    LINEAR = "linear"          # Linearly spaced [0.2, 0.4, 0.6, 0.8, 1.0]
    POLARIZED = "polarized"    # Half low (0.2), half high (0.9)
    CUSTOM = "custom"          # Explicit per-robot values


# ── C-SoS P1–P4 delivery-target patterns (paper Table V, 5 robots) ───────────
# Each pattern defines per-robot maximum delivery count.
# Robots stop requesting new routes after reaching their target.
CSOS_PATTERNS: Dict[str, List[int]] = {
    "P1": [1,  2,  3,  4,  5],
    "P2": [2,  4,  6,  8, 10],
    "P3": [2,  6,  9, 12, 15],
    "P4": [4,  8, 12, 16, 20],
}


@dataclass
class AgentMotivation:
    """Per-agent motivation specification."""
    profile: str = "uniform"
    values: Optional[List[float]] = None       # Explicit motivation values if profile=custom
    max_deliveries: Optional[List[int]] = None # Per-robot delivery cap (C-SoS P1-P4)

    def resolve(self, num_robots: int) -> List[float]:
        """Resolve profile to concrete per-robot motivation values."""
        if self.profile == "custom" and self.values:
            if len(self.values) != num_robots:
                raise ValueError(
                    f"Custom motivation values length ({len(self.values)}) "
                    f"!= num_robots ({num_robots})"
                )
            return self.values
        elif self.profile == "uniform":
            return [0.5] * num_robots
        elif self.profile == "linear":
            if num_robots == 1:
                return [0.5]
            return [
                0.2 + 0.8 * i / (num_robots - 1)
                for i in range(num_robots)
            ]
        elif self.profile == "polarized":
            half = num_robots // 2
            return [0.2] * half + [0.9] * (num_robots - half)
        else:
            raise ValueError(f"Unknown motivation profile: {self.profile}")


@dataclass
class GovernanceMotivation:
    """Governance-level motivation interpretation parameters."""
    motivation_model: str = "none"
    rho: float = 0.0         # Sensitivity to budget overshoot [0, 1]
    kappa: float = 5.0       # How much motivation extends budget
    budget_base: int = 3     # Base commitment budget per robot
    wait_scale: float = 3.0  # Overshoot-to-retry conversion factor


@dataclass
class ExperimentSweep:
    """Sweep parameters for batch experiments."""
    rho: List[float] = field(default_factory=lambda: [0.0])
    motivation_profiles: List[str] = field(default_factory=lambda: ["uniform"])
    seeds: List[int] = field(default_factory=lambda: list(range(20)))
    kappa: List[float] = field(default_factory=lambda: [5.0])
    budget_base: List[int] = field(default_factory=lambda: [3])
    duration_seconds: float = 120.0

    def num_conditions(self) -> int:
        return (
            len(self.rho)
            * len(self.motivation_profiles)
            * len(self.seeds)
            * len(self.kappa)
            * len(self.budget_base)
        )


@dataclass
class CADLMotivationConfig:
    """Full CADL config with motivation extensions."""
    # Base simulator settings
    name: str = "A-SoS-Baseline"
    sos_type: str = "directed"  # "directed" = A-SoS, "collaborative" = C-SoS
    description: str = ""

    # Environment
    num_nodes: int = 11
    num_edges: int = 17
    num_robots: int = 5
    nats_url: str = "nats://localhost:4222"

    # Governance parameters (existing)
    alpha: float = 0.7
    beta: float = 0.3
    lambda_param: float = 0.3  # 'lambda' is reserved in Python

    # Motivation extensions
    agent_motivation: AgentMotivation = field(default_factory=AgentMotivation)
    governance_motivation: GovernanceMotivation = field(
        default_factory=GovernanceMotivation
    )

    # Experiment sweep (optional)
    experiment: Optional[ExperimentSweep] = None

    @classmethod
    def from_yaml(cls, path: str) -> "CADLMotivationConfig":
        """Load from YAML file."""
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        return cls._from_dict(data)

    @classmethod
    def from_dict(cls, data: dict) -> "CADLMotivationConfig":
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, data: dict) -> "CADLMotivationConfig":
        config = cls()
        if "name" in data:
            config.name = data["name"]
        if "sos_type" in data:
            config.sos_type = data["sos_type"]
        if "description" in data:
            config.description = data["description"]

        env = data.get("environment", {})
        config.num_nodes = env.get("num_nodes", config.num_nodes)
        config.num_edges = env.get("num_edges", config.num_edges)
        config.num_robots = env.get("num_robots", config.num_robots)
        config.nats_url = env.get("nats_url", config.nats_url)

        gov = data.get("governance", {})
        config.alpha = gov.get("alpha", config.alpha)
        config.beta = gov.get("beta", config.beta)
        config.lambda_param = gov.get("lambda", config.lambda_param)

        # Motivation extensions
        mot = data.get("motivation", {})
        if mot:
            agent = mot.get("agent", {})
            config.agent_motivation = AgentMotivation(
                profile=agent.get("profile", "uniform"),
                values=agent.get("values"),
                max_deliveries=agent.get("max_deliveries"),
            )
            gov_mot = mot.get("governance", {})
            config.governance_motivation = GovernanceMotivation(
                motivation_model=gov_mot.get("model", "none"),
                rho=gov_mot.get("rho", 0.0),
                kappa=gov_mot.get("kappa", 5.0),
                budget_base=gov_mot.get("budget_base", 3),
                wait_scale=gov_mot.get("wait_scale", 3.0),
            )

        # Experiment sweep
        exp = data.get("experiment", {})
        if exp and "sweep" in exp:
            sw = exp["sweep"]
            config.experiment = ExperimentSweep(
                rho=sw.get("rho", [0.0]),
                motivation_profiles=sw.get("motivation_profiles", ["uniform"]),
                seeds=sw.get("seeds", list(range(20))),
                kappa=sw.get("kappa", [5.0]),
                budget_base=sw.get("budget_base", [3]),
                duration_seconds=exp.get("duration_seconds", 120.0),
            )

        return config

    def to_dict(self) -> dict:
        """Serialize to dict for YAML output."""
        d = {
            "name": self.name,
            "sos_type": self.sos_type,
            "description": self.description,
            "environment": {
                "num_nodes": self.num_nodes,
                "num_edges": self.num_edges,
                "num_robots": self.num_robots,
                "nats_url": self.nats_url,
            },
            "governance": {
                "alpha": self.alpha,
                "beta": self.beta,
                "lambda": self.lambda_param,
            },
            "motivation": {
                "agent": {
                    "profile": self.agent_motivation.profile,
                },
                "governance": {
                    "model": self.governance_motivation.motivation_model,
                    "rho": self.governance_motivation.rho,
                    "kappa": self.governance_motivation.kappa,
                    "budget_base": self.governance_motivation.budget_base,
                    "wait_scale": self.governance_motivation.wait_scale,
                },
            },
        }
        if self.agent_motivation.values:
            d["motivation"]["agent"]["values"] = self.agent_motivation.values
        if self.experiment:
            d["experiment"] = {
                "sweep": {
                    "rho": self.experiment.rho,
                    "motivation_profiles": self.experiment.motivation_profiles,
                    "seeds": self.experiment.seeds,
                    "kappa": self.experiment.kappa,
                    "budget_base": self.experiment.budget_base,
                },
                "duration_seconds": self.experiment.duration_seconds,
            }
        return d

    def to_yaml(self, path: str):
        """Write to YAML file."""
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)
