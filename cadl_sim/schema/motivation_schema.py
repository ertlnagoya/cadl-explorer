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


# ── C-SoS P1–P4 delivery-target patterns (5 robots) ───────────
# Each pattern defines per-robot maximum delivery count.
# Robots stop requesting new routes after reaching their target.
CSOS_PATTERNS: Dict[str, List[int]] = {
    "P1": [1,  2,  3,  4,  5],
    "P2": [2,  4,  6,  8, 10],
    "P3": [2,  6,  9, 12, 15],
    "P4": [4,  8, 12, 16, 20],
}


@dataclass
class TaskArbitrationConfig:
    """FCFS task arbitration configuration (DELIVERY_ASSIGNMENT protocol).

    When enabled, a TASK_OWNER process manages the goal sequence and assigns
    delivery targets via FCFS claim resolution.  Maps directly to the Unity
    ``taskArbitration`` JSON block.

    claim_resolution options:
        "all-robot-wait"  — TASK_OWNER waits until every active robot has
                            submitted a claim before assigning the goal.
        "first-come"      — Goal is assigned to the first robot that claims it.
    """
    enabled: bool = False
    protocol: str = "fcfs"
    max_claim_delay_sec: float = 5.0
    delivery_interval_sec: float = 1.0
    goal_sequence: List[int] = field(default_factory=list)
    startup_delay_sec: float = 0.0
    parallel: bool = True
    deadlock_recovery_enabled: bool = False
    deadlock_detection_sec: float = 15.0
    claim_resolution: str = "all-robot-wait"


@dataclass
class AgentMotivation:
    """Per-agent motivation specification."""
    profile: str = "uniform"
    values: Optional[List[float]] = None       # Explicit motivation values if profile=custom
    max_deliveries: Optional[List[int]] = None # Per-robot delivery cap (C-SoS P1-P4)
    wandering_goal_mode: str = "random"        # "random" | "select" (deterministic list)
    wandering_goal_list: List[int] = field(default_factory=list)  # used when mode="select"

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


# CADL Appendix A §A.1 enumerates sos_type as Capitalized identifiers
# (Directed | Acknowledged | Collaborative | Virtual). Internally this
# demo stores the lowercase form for legacy compatibility; on YAML emit
# we map back to the canonical Capitalized form so generated files remain
# spec-conformant.
_SOS_TYPE_LC = {"directed", "acknowledged", "collaborative", "virtual"}
_SOS_TYPE_CANONICAL = {lc: lc.capitalize() for lc in _SOS_TYPE_LC}


def _normalize_sos_type(value: str) -> str:
    """Normalise ``value`` to the internal lowercase form.

    Accepts both CADL-canonical Capitalized identifiers and historical
    lowercase variants. Raises ``ValueError`` on unknown values.
    """
    if not isinstance(value, str):
        raise ValueError(f"sos_type must be a string, got {type(value).__name__}")
    lc = value.lower()
    if lc not in _SOS_TYPE_LC:
        raise ValueError(
            f"Unknown sos_type {value!r}; expected one of "
            f"{sorted(v.capitalize() for v in _SOS_TYPE_LC)} "
            "(CADL Appendix A §A.1)"
        )
    return lc


def _sos_type_canonical(lc: str) -> str:
    """Map internal lowercase ``sos_type`` to CADL-canonical Capitalized."""
    return _SOS_TYPE_CANONICAL.get(lc, lc.capitalize())


@dataclass
class CADLMotivationConfig:
    """Full CADL config with motivation extensions."""
    # Base simulator settings
    name: str = "A-SoS-Baseline"
    sos_type: str = "directed"  # internal lowercase; emitted Capitalized
    description: str = ""

    # Environment
    num_nodes: int = 11
    num_edges: int = 17
    num_robots: int = 5
    nats_url: str = "nats://localhost:4222"
    random_seed: int = -1              # -1 = skip InitState (time-based seed)
    start_nodes: Optional[List[int]] = None  # Per-robot initial node positions

    # Governance parameters (existing)
    alpha: float = 0.7
    beta: float = 0.3
    lambda_param: float = 0.3  # 'lambda' is reserved in Python

    # Motivation extensions
    agent_motivation: AgentMotivation = field(default_factory=AgentMotivation)
    governance_motivation: GovernanceMotivation = field(
        default_factory=GovernanceMotivation
    )

    # FCFS task arbitration (C-SoS with TASK_OWNER)
    task_arbitration: TaskArbitrationConfig = field(
        default_factory=TaskArbitrationConfig
    )

    # Experiment sweep (optional)
    experiment: Optional[ExperimentSweep] = None

    # Verification / codegen blocks (optional passthrough for v0.1 spec)
    verification: Optional[dict] = None
    codegen: Optional[dict] = None

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
            config.sos_type = _normalize_sos_type(data["sos_type"])
        if "description" in data:
            config.description = data["description"]

        env = data.get("environment", {})
        config.num_nodes = env.get("num_nodes", config.num_nodes)
        config.num_edges = env.get("num_edges", config.num_edges)
        config.num_robots = env.get("num_robots", config.num_robots)
        config.nats_url = env.get("nats_url", config.nats_url)
        config.random_seed = env.get("random_seed", config.random_seed)
        config.start_nodes = env.get("start_nodes", config.start_nodes)

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
                wandering_goal_mode=agent.get("wandering_goal_mode", "random"),
                wandering_goal_list=agent.get("wandering_goal_list", []),
            )
            gov_mot = mot.get("governance", {})
            config.governance_motivation = GovernanceMotivation(
                motivation_model=gov_mot.get("model", "none"),
                rho=gov_mot.get("rho", 0.0),
                kappa=gov_mot.get("kappa", 5.0),
                budget_base=gov_mot.get("budget_base", 3),
                wait_scale=gov_mot.get("wait_scale", 3.0),
            )

        # FCFS task arbitration
        ta = data.get("task_arbitration", {})
        if ta:
            config.task_arbitration = TaskArbitrationConfig(
                enabled=ta.get("enabled", False),
                protocol=ta.get("protocol", "fcfs"),
                max_claim_delay_sec=ta.get("max_claim_delay_sec", 5.0),
                delivery_interval_sec=ta.get("delivery_interval_sec", 1.0),
                goal_sequence=ta.get("goal_sequence", []),
                startup_delay_sec=ta.get("startup_delay_sec", 0.0),
                parallel=ta.get("parallel", True),
                deadlock_recovery_enabled=ta.get("deadlock_recovery_enabled", False),
                deadlock_detection_sec=ta.get("deadlock_detection_sec", 15.0),
                claim_resolution=ta.get("claim_resolution", "all-robot-wait"),
            )

        # Verification / codegen blocks (validated and retained as dicts)
        verif = data.get("verification")
        if verif is not None:
            cls._validate_verification_block(verif)
            config.verification = verif

        cg = data.get("codegen")
        if cg is not None:
            cls._validate_codegen_block(cg)
            config.codegen = cg

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

    # ── Validation helpers for verification / codegen blocks ───────
    _VERIFY_PROPERTY_KINDS = {"safety", "liveness", "fairness", "invariant"}
    _VERIFY_METHODS = {"smt", "model_check", "simulation", "proof"}

    @classmethod
    def _validate_verification_block(cls, block: Any) -> None:
        if not isinstance(block, dict):
            raise ValueError("`verification` block must be a mapping")
        for name, body in block.items():
            if not isinstance(body, dict):
                raise ValueError(
                    f"verification.{name} must be a mapping"
                )
            if "property" not in body:
                raise ValueError(
                    f"verification.{name} is missing required field 'property'"
                )
            prop = body["property"]
            if prop not in cls._VERIFY_PROPERTY_KINDS:
                raise ValueError(
                    f"verification.{name}.property must be one of "
                    f"{sorted(cls._VERIFY_PROPERTY_KINDS)}, got {prop!r}"
                )
            if "expr" not in body:
                raise ValueError(
                    f"verification.{name} is missing required field 'expr'"
                )
            method = body.get("method")
            if method is not None and method not in cls._VERIFY_METHODS:
                raise ValueError(
                    f"verification.{name}.method must be one of "
                    f"{sorted(cls._VERIFY_METHODS)}, got {method!r}"
                )

    @classmethod
    def _validate_codegen_block(cls, block: Any) -> None:
        if not isinstance(block, dict):
            raise ValueError("`codegen` block must be a mapping")
        for target, body in block.items():
            if not isinstance(body, dict):
                raise ValueError(
                    f"codegen.{target} must be a mapping"
                )
            for key in body:
                if key not in {"output", "template", "options"}:
                    raise ValueError(
                        f"codegen.{target} has unknown field {key!r} "
                        "(expected: output, template, options)"
                    )

    def to_dict(self) -> dict:
        """Serialize to dict for YAML output."""
        env: Dict[str, Any] = {
            "num_nodes": self.num_nodes,
            "num_edges": self.num_edges,
            "num_robots": self.num_robots,
            "nats_url": self.nats_url,
        }
        if self.random_seed != -1:
            env["random_seed"] = self.random_seed
        if self.start_nodes:
            env["start_nodes"] = self.start_nodes

        agent_mot: Dict[str, Any] = {
            "profile": self.agent_motivation.profile,
        }
        if self.agent_motivation.values:
            agent_mot["values"] = self.agent_motivation.values
        if self.agent_motivation.max_deliveries:
            agent_mot["max_deliveries"] = self.agent_motivation.max_deliveries
        if self.agent_motivation.wandering_goal_mode != "random":
            agent_mot["wandering_goal_mode"] = self.agent_motivation.wandering_goal_mode
        if self.agent_motivation.wandering_goal_list:
            agent_mot["wandering_goal_list"] = self.agent_motivation.wandering_goal_list

        d: Dict[str, Any] = {
            "name": self.name,
            "sos_type": _sos_type_canonical(self.sos_type),
            "description": self.description,
            "environment": env,
            "governance": {
                "alpha": self.alpha,
                "beta": self.beta,
                "lambda": self.lambda_param,
            },
            "motivation": {
                "agent": agent_mot,
                "governance": {
                    "model": self.governance_motivation.motivation_model,
                    "rho": self.governance_motivation.rho,
                    "kappa": self.governance_motivation.kappa,
                    "budget_base": self.governance_motivation.budget_base,
                    "wait_scale": self.governance_motivation.wait_scale,
                },
            },
        }

        # Task arbitration section (omit when disabled)
        if self.task_arbitration.enabled:
            d["task_arbitration"] = {
                "enabled": self.task_arbitration.enabled,
                "protocol": self.task_arbitration.protocol,
                "max_claim_delay_sec": self.task_arbitration.max_claim_delay_sec,
                "delivery_interval_sec": self.task_arbitration.delivery_interval_sec,
                "goal_sequence": self.task_arbitration.goal_sequence,
                "startup_delay_sec": self.task_arbitration.startup_delay_sec,
                "parallel": self.task_arbitration.parallel,
                "deadlock_recovery_enabled": self.task_arbitration.deadlock_recovery_enabled,
                "deadlock_detection_sec": self.task_arbitration.deadlock_detection_sec,
                "claim_resolution": self.task_arbitration.claim_resolution,
            }

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
        if self.verification is not None:
            d["verification"] = self.verification
        if self.codegen is not None:
            d["codegen"] = self.codegen
        return d

    def to_yaml(self, path: str):
        """Write to YAML file."""
        with open(path, "w") as f:
            yaml.dump(self.to_dict(), f, default_flow_style=False, sort_keys=False)
