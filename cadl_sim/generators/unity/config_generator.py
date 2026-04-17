"""
Unity Config Generator — converts CADL motivation configs to cadl_config.json.

Generates the JSON format expected by SimulatorConfigurator.cs, extended with
motivation_config section for the A-SoS motivation-sensitive arbitrator.
"""

import json
import copy
import os
from typing import Optional

from cadl_sim.schema.motivation_schema import CADLMotivationConfig


# ── Default graph (matches Unity scene and Go arbitrator hardcoded graph) ──

DEFAULT_GRAPH = {
    "nodes": list(range(11)),
    "edges": [
        {"src": 0, "dst": 1, "length": 1.0},
        {"src": 1, "dst": 2, "length": 1.0},
        {"src": 2, "dst": 3, "length": 2.0},
        {"src": 3, "dst": 4, "length": 2.1},
        {"src": 4, "dst": 5, "length": 1.3},
        {"src": 5, "dst": 6, "length": 2.0},
        {"src": 6, "dst": 7, "length": 1.2},
        {"src": 7, "dst": 0, "length": 1.0},
        {"src": 7, "dst": 8, "length": 2.5},
        {"src": 0, "dst": 8, "length": 1.0},
        {"src": 1, "dst": 8, "length": 1.0},
        {"src": 8, "dst": 9, "length": 3.0},
        {"src": 6, "dst": 9, "length": 1.0},
        {"src": 5, "dst": 9, "length": 0.8},
        {"src": 4, "dst": 10, "length": 1.4},
        {"src": 3, "dst": 10, "length": 0.8},
        {"src": 2, "dst": 10, "length": 1.7},
    ],
}

# ── SoS type mapping ──
SOS_TYPE_MAP = {
    "directed": "directed",        # A-SoS
    "a_sos": "directed",
    "collaborative": "collaborative",  # C-SoS
    "c_sos": "collaborative",
    "acknowledged": "acknowledged",    # MCP-SoS
}


def generate_unity_config(
    config: CADLMotivationConfig,
    output_path: Optional[str] = None,
    seed: Optional[int] = None,
    rho_override: Optional[float] = None,
    motivation_profile_override: Optional[str] = None,
) -> dict:
    """
    Generate a Unity cadl_config.json from a CADLMotivationConfig.

    Parameters
    ----------
    config : CADLMotivationConfig
        The CADL config to convert.
    output_path : str, optional
        If provided, write JSON to this path.
    seed : int, optional
        Random seed override for this run.
    rho_override : float, optional
        Override the ρ value in governance motivation.
    motivation_profile_override : str, optional
        Override the motivation profile name.

    Returns
    -------
    dict : The generated Unity config dictionary.
    """
    # Apply overrides
    cfg = copy.deepcopy(config)
    if rho_override is not None:
        cfg.governance_motivation.rho = rho_override
    if motivation_profile_override is not None:
        cfg.agent_motivation.profile = motivation_profile_override
        cfg.agent_motivation.values = None  # Reset custom values

    sos_type = SOS_TYPE_MAP.get(cfg.sos_type, cfg.sos_type)

    # Resolve motivation values
    motivation_values = cfg.agent_motivation.resolve(cfg.num_robots)

    # Build the base config (compatible with existing SimulatorConfigurator.cs)
    unity_config = {
        "simulatorConfig": {
            "name": cfg.name,
            "sosType": sos_type,
            "description": cfg.description or f"{cfg.name} configuration",
            "environment": {
                "num_nodes": cfg.num_nodes,
                "num_edges": cfg.num_edges,
                "num_robots": cfg.num_robots,
                "nats_url": cfg.nats_url,
                "edge_length_min": 0.8,
                "edge_length_max": 3.0,
                "same_direction_penalty": 1.5,
                "opposite_direction_penalty": 7.0,
                "intersection_threshold": 0.375,
                "discrete_time_sync": True,
            },
            "graph": copy.deepcopy(DEFAULT_GRAPH),
        },
        "agentTemplates": _build_agent_templates(cfg, sos_type),
        "communicationSetup": _build_communication_setup(cfg, sos_type),
        "protocols": _build_protocols(cfg, sos_type),
        "regimeTransitions": [
            {
                "fromRegime": "NORMAL",
                "toRegime": "CONGESTED",
                "condition": "occupied_edges > total_edges * 0.6",
                "triggerProtocol": "RESOURCE_QUERY",
            },
            {
                "fromRegime": "CONGESTED",
                "toRegime": "NORMAL",
                "condition": "occupied_edges <= total_edges * 0.4",
            },
        ],
        "metrics": [
            {"id": "goal_sum", "formula": "sum(ROBOT[i].goal_count for i in 1..N)", "target": ">= 0"},
            {"id": "goal_min", "formula": "min(ROBOT[i].goal_count for i in 1..N)", "target": ">= 0"},
            {"id": "collision_rate", "formula": "collisions_per_minute / num_robots", "target": "<= 0.5"},
            {"id": "retry_rate", "formula": "total_retries / total_requests", "target": "<= 0.3"},
        ],
        # ── Motivation extension (read by motivation-aware arbitrator) ──
        "motivationConfig": {
            "enabled": cfg.governance_motivation.motivation_model != "none",
            "model": cfg.governance_motivation.motivation_model,
            "rho": cfg.governance_motivation.rho,
            "kappa": cfg.governance_motivation.kappa,
            "budgetBase": cfg.governance_motivation.budget_base,
            "waitScale": cfg.governance_motivation.wait_scale,
            "agentMotivation": motivation_values,
            "profile": cfg.agent_motivation.profile,
            # C-SoS P1-P4: per-robot delivery cap (null = no limit)
            "maxDeliveries": cfg.agent_motivation.max_deliveries,
            # Wandering goal mode: "random" (default) | "select" (deterministic list)
            "wanderingGoalMode": cfg.agent_motivation.wandering_goal_mode,
            "wanderingGoalList": cfg.agent_motivation.wandering_goal_list,
        },
    }

    # random_seed: explicit seed overrides config value; -1 = skip InitState
    if seed is not None:
        unity_config["simulatorConfig"]["environment"]["random_seed"] = seed
    elif cfg.random_seed != -1:
        unity_config["simulatorConfig"]["environment"]["random_seed"] = cfg.random_seed

    # start_nodes (per-robot initial node positions)
    if cfg.start_nodes:
        unity_config["simulatorConfig"]["environment"]["start_nodes"] = cfg.start_nodes

    # FCFS task arbitration
    if cfg.task_arbitration.enabled:
        ta = cfg.task_arbitration
        unity_config["taskArbitration"] = {
            "enabled": ta.enabled,
            "protocol": ta.protocol,
            "maxClaimDelaySec": ta.max_claim_delay_sec,
            "deliveryIntervalSec": ta.delivery_interval_sec,
            "goalSequence": ta.goal_sequence,
            "startupDelaySec": ta.startup_delay_sec,
            "parallel": ta.parallel,
            "deadlockRecoveryEnabled": ta.deadlock_recovery_enabled,
            "deadlockDetectionSec": ta.deadlock_detection_sec,
            "claimResolution": ta.claim_resolution,
        }

    if output_path:
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with open(output_path, "w") as f:
            json.dump(unity_config, f, indent=2)

    return unity_config


def _build_agent_templates(cfg, sos_type):
    """Build agent templates based on SoS type."""
    if sos_type == "directed":
        return [
            {
                "templateId": "ARBITRATOR",
                "prefab": "Agent_LowAutonomy",
                "role": "traffic_controller",
                "autonomy": "full_authority",
                "capabilities": [
                    "manage_edge_flags",
                    "manage_cross_flags",
                    "compute_direction_dijkstra",
                    "track_agent_state",
                    "assign_next_node",
                ],
                "planner": {"type": "central", "algorithm": "DirectionDijkstra"},
                "inputs": ["init_request", "next_request", "ret_request"],
                "outputs": ["next_node_assignment", "rejection"],
            },
            {
                "templateId": "ROBOT",
                "prefab": "Agent_LowAutonomy",
                "role": "compliant_navigator",
                "autonomy": "low",
                "count": cfg.num_robots,
                "capabilities": [
                    "line_trace_follow",
                    "intersection_detection",
                    "collision_back",
                    "nats_request_response",
                ],
                "planner": {"type": "none", "algorithm": "N/A"},
                "inputs": ["next_node_assignment", "rejection"],
                "outputs": ["next_request", "ret_request"],
            },
        ]
    else:
        # C-SoS (collaborative)
        return [
            {
                "templateId": "ARBITRATOR",
                "prefab": "Agent_LowAutonomy",
                "role": "traffic_verifier",
                "autonomy": "low",
                "capabilities": [
                    "manage_edge_flags",
                    "manage_cross_flags",
                    "compute_direction_dijkstra",
                    "serve_resource_query",
                    "track_agent_state",
                    "verify_proposed_next_node",
                ],
                "planner": {"type": "central", "algorithm": "DirectionDijkstra"},
                "inputs": ["init_request", "next_request_csos", "ret_request", "resource_query"],
                "outputs": ["permit_state", "resource_response", "rejection"],
            },
            {
                "templateId": "ROBOT",
                "prefab": "Agent_HighAutonomy",
                "role": "autonomous_navigator",
                "autonomy": "high",
                "count": cfg.num_robots,
                "capabilities": [
                    "line_trace_follow",
                    "intersection_detection",
                    "collision_back",
                    "local_direction_dijkstra",
                    "query_global_resource",
                    "nats_request_response",
                ],
                "planner": {"type": "local", "algorithm": "DirectionDijkstra"},
                "inputs": ["permit_state", "resource_response"],
                "outputs": ["next_request_csos", "resource_query", "ret_request"],
            },
        ]


def _build_communication_setup(cfg, sos_type):
    """Build communication setup."""
    governance = {
        "alpha": cfg.alpha,
        "beta": cfg.beta,
        "lambda": cfg.lambda_param,
    }

    if sos_type == "directed":
        governance["decisionHolder"] = "ARBITRATOR"
    else:
        governance["decisionHolder"] = "ROBOT[*]"

    return {
        "channels": [
            {"channelName": "next_request", "fromTemplate": "ROBOT[*]", "toTemplate": "ARBITRATOR"},
            {"channelName": "assignment", "fromTemplate": "ARBITRATOR", "toTemplate": "ROBOT[*]"},
        ],
        "nats_subjects": {
            "init": "init",
            "next": "next",
            "ret": "ret",
            "fin": "fin",
            "disp": "disp",
            "resource": "resource",
            "goalcount": "goalcount",
            "stop": "stop",
        },
        "governance": governance,
    }


def _build_protocols(cfg, sos_type):
    """Build protocol definitions."""
    protocols = []

    if sos_type == "directed":
        protocols.append({
            "protocolId": "DIRECTED_ROUTING",
            "trigger": "ROBOT[i] enters intersection node",
            "steps": [
                {"stepType": "message", "sender": "ROBOT[i]", "receiver": "ARBITRATOR",
                 "content": "next_request(id, src, dst, goal)"},
                {"stepType": "compute", "sender": "ARBITRATOR",
                 "content": "assign_next_node(id, src, dst, goal)"},
                {"stepType": "message", "sender": "ARBITRATOR", "receiver": "ROBOT[i]",
                 "content": "next_node(next) or rejection(retry_count)"},
            ],
            "timing": {"max_response": "10s", "retry_delay_base": "1s"},
            "precondition": "ROBOT[i].at_intersection == true",
            "postcondition": "ROBOT[i].moving_or_retrying",
        })
    else:
        protocols.append({
            "protocolId": "COLLABORATIVE_ROUTING",
            "trigger": "ROBOT[i] enters intersection node",
            "steps": [
                {"stepType": "compute", "sender": "ROBOT[i]",
                 "content": "compute_local_dijkstra(src, goal)"},
                {"stepType": "message", "sender": "ROBOT[i]", "receiver": "ARBITRATOR",
                 "content": "next_request_csos(id, src, dst, next, goal)"},
                {"stepType": "compute", "sender": "ARBITRATOR",
                 "content": "verify_proposed_next_node(id, src, next)"},
                {"stepType": "message", "sender": "ARBITRATOR", "receiver": "ROBOT[i]",
                 "content": "permit_state(1=approved, -1=retry, -2=reverse)"},
            ],
            "timing": {"max_response": "10s", "retry_delay_base": "1s"},
            "precondition": "ROBOT[i].at_intersection == true",
            "postcondition": "ROBOT[i].moving_or_retrying",
        })
        protocols.append({
            "protocolId": "RESOURCE_QUERY",
            "trigger": "ROBOT[i] needs global occupancy state",
            "steps": [
                {"stepType": "message", "sender": "ROBOT[i]", "receiver": "ARBITRATOR",
                 "content": "resource_query(id)"},
                {"stepType": "message", "sender": "ARBITRATOR", "receiver": "ROBOT[i]",
                 "content": "resource_response(edge_flags, cross_flags)"},
                {"stepType": "compute", "sender": "ROBOT[i]",
                 "content": "update_local_flags(edge_flags, cross_flags)"},
            ],
            "timing": {"max_response": "5s"},
            "postcondition": "ROBOT[i].local_flags_updated",
        })

    # Collision recovery (common)
    protocols.append({
        "protocolId": "COLLISION_RECOVERY",
        "trigger": "ROBOT[i].distance_sensor < 0.1m for > maxT",
        "steps": [
            {"stepType": "compute", "sender": "ROBOT[i]", "content": "execute_back_handler()"},
            {"stepType": "message", "sender": "ROBOT[i]", "receiver": "ARBITRATOR",
             "content": "next_request(id, src, dst, next, goal, re=true)"},
            {"stepType": "message", "sender": "ARBITRATOR", "receiver": "ROBOT[i]",
             "content": "permit_state_or_next"},
        ],
        "timing": {"back_duration": "2s", "max_retries": 3},
        "postcondition": "ROBOT[i].collision_resolved",
    })

    return protocols


def generate_unity_config_from_yaml(
    yaml_path: str,
    output_path: str,
    seed: Optional[int] = None,
    rho_override: Optional[float] = None,
    motivation_profile_override: Optional[str] = None,
) -> dict:
    """Convenience: load YAML, generate Unity config."""
    cfg = CADLMotivationConfig.from_yaml(yaml_path)
    return generate_unity_config(
        cfg, output_path, seed, rho_override, motivation_profile_override
    )


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Generate Unity config from CADL YAML")
    parser.add_argument("input", help="Input CADL YAML file")
    parser.add_argument("-o", "--output", default="cadl_config.json", help="Output JSON path")
    parser.add_argument("--seed", type=int, help="Random seed")
    parser.add_argument("--rho", type=float, help="Override rho value")
    parser.add_argument("--profile", help="Override motivation profile")
    args = parser.parse_args()

    result = generate_unity_config_from_yaml(
        args.input, args.output, args.seed, args.rho, args.profile
    )
    print(f"Generated {args.output} ({len(json.dumps(result))} bytes)")
