"""
Three-Layer Intermediate Representation for SoS Governance.

Layer 1 (Institution): WHO decides, WHAT governance rules apply
Layer 2 (Protocol):    HOW coordination messages flow
Layer 3 (Algorithm):   WHAT computation executes the decisions

Extended with motivation-related fields for A-SoS experiments.
"""

from dataclasses import dataclass, field
from typing import List, Dict, Optional, Any
import json
import textwrap


# ── Layer 1: Institution ──────────────────────────────────────────────

@dataclass
class MotivationInterpretation:
    """How the institution interprets agent motivation."""
    model: str = "none"           # none | commitment_budget | hybrid
    description: str = ""
    rho: float = 0.0              # Sensitivity parameter
    kappa: float = 0.0            # Budget extension factor
    is_latent: bool = True        # Motivation is latent (not directly observed)

    def summary(self) -> str:
        if self.model == "none":
            return "Motivation ignored (homogeneous treatment)"
        return (
            f"Model={self.model}, ρ={self.rho}, κ={self.kappa} — "
            f"{self.description}"
        )


@dataclass
class CommitmentPolicy:
    """Institutional commitment policy for agents."""
    budget_base: int = 0          # Base budget per agent
    budget_formula: str = ""      # e.g., "B_base + kappa * m_i"
    enforcement: str = "none"     # none | soft_throttle | hard_block
    overshoot_action: str = ""    # What happens when budget exceeded

    def summary(self) -> str:
        if self.enforcement == "none":
            return "No commitment budget enforced"
        return (
            f"Budget={self.budget_formula}, "
            f"enforcement={self.enforcement}, "
            f"overshoot→{self.overshoot_action}"
        )


@dataclass
class InstitutionLayer:
    """Layer 1: Institutional governance structure."""
    sos_type: str = "directed"
    decision_authority: str = "central"  # central | local | hybrid
    agent_autonomy: str = "low"          # low | medium | high
    governance_params: Dict[str, float] = field(default_factory=dict)

    # Motivation extensions
    motivation_interpretation: MotivationInterpretation = field(
        default_factory=MotivationInterpretation
    )
    commitment_policy: CommitmentPolicy = field(
        default_factory=CommitmentPolicy
    )
    agent_motivation_values: List[float] = field(default_factory=list)


# ── Layer 2: Protocol ─────────────────────────────────────────────────

@dataclass
class ProtocolStep:
    """A single step in a coordination protocol."""
    step_type: str = "compute"     # compute | message
    sender: str = ""
    receiver: str = ""
    content: str = ""
    condition: str = ""


@dataclass
class MotivationProtocolEffect:
    """How motivation affects the protocol layer."""
    affects_dispatch: bool = False    # Budget check before dispatching
    affects_acceptance: bool = False  # Budget check before accepting task
    affects_waiting: bool = False     # Over-budget robots wait longer
    fallback_action: str = "wait"    # wait | skip | reassign


@dataclass
class ProtocolLayer:
    """Layer 2: Coordination protocols."""
    routing_protocol: str = ""
    collision_protocol: str = ""
    resource_query_protocol: str = ""
    steps: List[ProtocolStep] = field(default_factory=list)

    # Motivation extensions
    motivation_effect: MotivationProtocolEffect = field(
        default_factory=MotivationProtocolEffect
    )


# ── Layer 3: Algorithm Binding ────────────────────────────────────────

@dataclass
class PlannerModification:
    """How the central planner objective is modified by motivation."""
    base_algorithm: str = "DirectionDijkstra"
    objective_modified: bool = False
    modification_type: str = "none"   # none | wait_injection | cost_scaling
    uses_preference: bool = False
    uses_budget: bool = False
    description: str = ""


@dataclass
class AlgorithmBindingLayer:
    """Layer 3: Algorithm-level bindings."""
    routing_algorithm: str = "DirectionDijkstra"
    occupancy_model: str = "edge_flags"
    retry_strategy: str = "exponential_backoff"

    # Motivation extensions
    planner_modification: PlannerModification = field(
        default_factory=PlannerModification
    )


# ── Task Arbitration ─────────────────────────────────────────────────

@dataclass
class TaskArbitrationLayer:
    """FCFS task arbitration state in the IR.

    Represents the TASK_OWNER actor and DELIVERY_ASSIGNMENT protocol.
    When enabled, a separate process manages the delivery goal sequence
    and assigns tasks via FCFS with configurable claim resolution.
    """
    enabled: bool = False
    protocol: str = "fcfs"
    claim_resolution: str = "all-robot-wait"
    max_claim_delay_sec: float = 5.0
    delivery_interval_sec: float = 1.0
    deadlock_recovery_enabled: bool = False
    goal_sequence: List[int] = field(default_factory=list)

    def summary(self) -> str:
        if not self.enabled:
            return "Task arbitration disabled (wandering/random goal selection)"
        return (
            f"FCFS, claim_resolution={self.claim_resolution}, "
            f"maxDelay={self.max_claim_delay_sec}s, "
            f"goals={self.goal_sequence[:5]}{'...' if len(self.goal_sequence) > 5 else ''}"
        )


# ── Full IR ───────────────────────────────────────────────────────────

@dataclass
class ThreeLayerIR:
    """Complete 3-layer IR for a governance configuration."""
    name: str = ""
    institution: InstitutionLayer = field(default_factory=InstitutionLayer)
    protocol: ProtocolLayer = field(default_factory=ProtocolLayer)
    algorithm: AlgorithmBindingLayer = field(default_factory=AlgorithmBindingLayer)
    task_arbitration: TaskArbitrationLayer = field(default_factory=TaskArbitrationLayer)

    def to_dict(self) -> dict:
        """Serialize to nested dict."""
        return {
            "name": self.name,
            "layer1_institution": {
                "sos_type": self.institution.sos_type,
                "decision_authority": self.institution.decision_authority,
                "agent_autonomy": self.institution.agent_autonomy,
                "governance_params": self.institution.governance_params,
                "motivation_interpretation": {
                    "model": self.institution.motivation_interpretation.model,
                    "rho": self.institution.motivation_interpretation.rho,
                    "kappa": self.institution.motivation_interpretation.kappa,
                    "description": self.institution.motivation_interpretation.description,
                },
                "commitment_policy": {
                    "budget_base": self.institution.commitment_policy.budget_base,
                    "budget_formula": self.institution.commitment_policy.budget_formula,
                    "enforcement": self.institution.commitment_policy.enforcement,
                    "overshoot_action": self.institution.commitment_policy.overshoot_action,
                },
                "agent_motivation_values": self.institution.agent_motivation_values,
            },
            "layer2_protocol": {
                "routing_protocol": self.protocol.routing_protocol,
                "collision_protocol": self.protocol.collision_protocol,
                "resource_query_protocol": self.protocol.resource_query_protocol,
                "motivation_effect": {
                    "affects_dispatch": self.protocol.motivation_effect.affects_dispatch,
                    "affects_waiting": self.protocol.motivation_effect.affects_waiting,
                    "fallback_action": self.protocol.motivation_effect.fallback_action,
                },
            },
            "layer3_algorithm": {
                "routing_algorithm": self.algorithm.routing_algorithm,
                "occupancy_model": self.algorithm.occupancy_model,
                "retry_strategy": self.algorithm.retry_strategy,
                "planner_modification": {
                    "base_algorithm": self.algorithm.planner_modification.base_algorithm,
                    "objective_modified": self.algorithm.planner_modification.objective_modified,
                    "modification_type": self.algorithm.planner_modification.modification_type,
                    "uses_budget": self.algorithm.planner_modification.uses_budget,
                    "description": self.algorithm.planner_modification.description,
                },
            },
            "task_arbitration": {
                "enabled": self.task_arbitration.enabled,
                "protocol": self.task_arbitration.protocol,
                "claim_resolution": self.task_arbitration.claim_resolution,
                "max_claim_delay_sec": self.task_arbitration.max_claim_delay_sec,
                "delivery_interval_sec": self.task_arbitration.delivery_interval_sec,
                "deadlock_recovery_enabled": self.task_arbitration.deadlock_recovery_enabled,
                "goal_sequence": self.task_arbitration.goal_sequence,
            },
        }

    def to_json(self, path: str):
        with open(path, "w") as f:
            json.dump(self.to_dict(), f, indent=2)


# ── Builder ───────────────────────────────────────────────────────────

def build_ir_from_config(config) -> ThreeLayerIR:
    """Build a ThreeLayerIR from a CADLMotivationConfig."""
    ir = ThreeLayerIR(name=config.name)

    # Layer 1: Institution
    ir.institution.sos_type = config.sos_type
    if config.sos_type == "directed":
        ir.institution.decision_authority = "central"
        ir.institution.agent_autonomy = "low"
    elif config.sos_type == "collaborative":
        ir.institution.decision_authority = "hybrid"
        ir.institution.agent_autonomy = "high"
    else:
        ir.institution.decision_authority = "local"
        ir.institution.agent_autonomy = "high"

    ir.institution.governance_params = {
        "alpha": config.alpha,
        "beta": config.beta,
        "lambda": config.lambda_param,
    }

    gm = config.governance_motivation
    ir.institution.motivation_interpretation = MotivationInterpretation(
        model=gm.motivation_model,
        rho=gm.rho,
        kappa=gm.kappa,
        description=_motivation_description(gm.motivation_model, gm.rho),
    )

    if gm.motivation_model != "none":
        ir.institution.commitment_policy = CommitmentPolicy(
            budget_base=gm.budget_base,
            budget_formula=f"B_base({gm.budget_base}) + kappa({gm.kappa}) * m_i",
            enforcement="soft_throttle",
            overshoot_action=f"extra_wait = floor(rho({gm.rho}) * overshoot * wait_scale({gm.wait_scale}))",
        )

    mv = config.agent_motivation.resolve(config.num_robots)
    ir.institution.agent_motivation_values = mv

    # Layer 2: Protocol
    if config.sos_type == "directed":
        ir.protocol.routing_protocol = "DIRECTED_ROUTING"
        ir.protocol.collision_protocol = "COLLISION_RECOVERY"
        ir.protocol.resource_query_protocol = "N/A"
    else:
        ir.protocol.routing_protocol = "COLLABORATIVE_ROUTING"
        ir.protocol.collision_protocol = "COLLISION_RECOVERY"
        ir.protocol.resource_query_protocol = "RESOURCE_QUERY"

    if gm.motivation_model != "none":
        ir.protocol.motivation_effect = MotivationProtocolEffect(
            affects_dispatch=True,
            affects_waiting=True,
            fallback_action="wait_with_extra_retry",
        )

    # Layer 3: Algorithm
    ir.algorithm.routing_algorithm = "DirectionDijkstra"
    ir.algorithm.occupancy_model = "edge_flags"
    ir.algorithm.retry_strategy = "linear_backoff"

    if gm.motivation_model != "none":
        ir.algorithm.planner_modification = PlannerModification(
            base_algorithm="DirectionDijkstra",
            objective_modified=True,
            modification_type="wait_injection",
            uses_budget=True,
            description=(
                f"Arbitrator injects extra retry ticks when robot's cumulative "
                f"goals exceed B_i. Route computation unchanged."
            ),
        )

    # Task arbitration layer
    ta = config.task_arbitration
    if ta.enabled:
        ir.task_arbitration = TaskArbitrationLayer(
            enabled=True,
            protocol=ta.protocol,
            claim_resolution=ta.claim_resolution,
            max_claim_delay_sec=ta.max_claim_delay_sec,
            delivery_interval_sec=ta.delivery_interval_sec,
            deadlock_recovery_enabled=ta.deadlock_recovery_enabled,
            goal_sequence=list(ta.goal_sequence),
        )
        # FCFS task arbitration affects Layer 2: claim-request/delivery-assignment protocol
        ir.protocol.motivation_effect = MotivationProtocolEffect(
            affects_dispatch=True,
            affects_waiting=True,
            fallback_action="claim_with_motivation_delay",
        )

    return ir


def _motivation_description(model: str, rho: float) -> str:
    if model == "none":
        return "Central authority ignores individual motivation (homogeneous treatment)"
    elif model == "commitment_budget":
        return (
            f"Central authority grants commitment budget proportional to motivation. "
            f"Sensitivity ρ={rho} controls throttling of over-budget robots."
        )
    elif model == "hybrid":
        return (
            f"Budget constraint with preference-sensitive routing priority. "
            f"ρ={rho} governs both throttling strength and priority weighting."
        )
    return f"Unknown model: {model}"


# ── Comparison ────────────────────────────────────────────────────────

def compare_irs(irs: List[ThreeLayerIR]) -> str:
    """Generate a human-readable comparison of multiple IRs."""
    if not irs:
        return "No IRs to compare."

    lines = []
    names = [ir.name for ir in irs]
    lines.append(f"{'Field':<45} | " + " | ".join(f"{n:<25}" for n in names))
    lines.append("-" * (47 + 28 * len(names)))

    # Layer 1
    lines.append("── Layer 1: Institution ──")
    _cmp(lines, "  sos_type", [ir.institution.sos_type for ir in irs])
    _cmp(lines, "  decision_authority", [ir.institution.decision_authority for ir in irs])
    _cmp(lines, "  agent_autonomy", [ir.institution.agent_autonomy for ir in irs])
    _cmp(lines, "  alpha", [ir.institution.governance_params.get("alpha", "?") for ir in irs])
    _cmp(lines, "  beta", [ir.institution.governance_params.get("beta", "?") for ir in irs])
    _cmp(lines, "  lambda", [ir.institution.governance_params.get("lambda", "?") for ir in irs])
    _cmp(lines, "  motivation.model", [ir.institution.motivation_interpretation.model for ir in irs])
    _cmp(lines, "  motivation.rho", [ir.institution.motivation_interpretation.rho for ir in irs])
    _cmp(lines, "  motivation.kappa", [ir.institution.motivation_interpretation.kappa for ir in irs])
    _cmp(lines, "  commitment.enforcement", [ir.institution.commitment_policy.enforcement for ir in irs])
    _cmp(lines, "  commitment.budget_base", [ir.institution.commitment_policy.budget_base for ir in irs])
    _cmp(lines, "  agent_motivation", [
        _fmt_list(ir.institution.agent_motivation_values) for ir in irs
    ])

    # Layer 2
    lines.append("── Layer 2: Protocol ──")
    _cmp(lines, "  routing_protocol", [ir.protocol.routing_protocol for ir in irs])
    _cmp(lines, "  motivation→dispatch", [ir.protocol.motivation_effect.affects_dispatch for ir in irs])
    _cmp(lines, "  motivation→waiting", [ir.protocol.motivation_effect.affects_waiting for ir in irs])
    _cmp(lines, "  fallback_action", [ir.protocol.motivation_effect.fallback_action for ir in irs])

    # Layer 3
    lines.append("── Layer 3: Algorithm ──")
    _cmp(lines, "  routing_algorithm", [ir.algorithm.routing_algorithm for ir in irs])
    _cmp(lines, "  planner.modified", [ir.algorithm.planner_modification.objective_modified for ir in irs])
    _cmp(lines, "  planner.mod_type", [ir.algorithm.planner_modification.modification_type for ir in irs])
    _cmp(lines, "  planner.uses_budget", [ir.algorithm.planner_modification.uses_budget for ir in irs])

    # Task arbitration
    lines.append("── Task Arbitration ──")
    _cmp(lines, "  enabled", [ir.task_arbitration.enabled for ir in irs])
    _cmp(lines, "  protocol", [ir.task_arbitration.protocol for ir in irs])
    _cmp(lines, "  claim_resolution", [ir.task_arbitration.claim_resolution for ir in irs])
    _cmp(lines, "  max_claim_delay_sec", [ir.task_arbitration.max_claim_delay_sec for ir in irs])
    _cmp(lines, "  deadlock_recovery", [ir.task_arbitration.deadlock_recovery_enabled for ir in irs])

    return "\n".join(lines)


def _cmp(lines, label, values):
    vals_str = [str(v) for v in values]
    # Mark differences
    all_same = len(set(vals_str)) <= 1
    marker = "  " if all_same else "* "
    lines.append(
        f"{marker}{label:<43} | " + " | ".join(f"{v:<25}" for v in vals_str)
    )


def _fmt_list(lst):
    if not lst:
        return "[]"
    return "[" + ",".join(f"{v:.1f}" for v in lst) + "]"


def ir_to_text(ir: ThreeLayerIR) -> str:
    """Pretty-print a single IR."""
    d = ir.to_dict()
    return json.dumps(d, indent=2)
