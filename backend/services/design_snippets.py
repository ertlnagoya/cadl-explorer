"""What the editor offers while one writes CADL: names to complete and snippets to insert.

`names` collects the names a design already uses (actors, messages, contracts,
states, norms, quantities) and the keys of CADL. `SNIPPETS` are the beginnings
of the parts that are easy to get wrong, in the form of the examples of cadl;
each is inserted at the cursor, indented like the line it goes into.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional

KEYS = [
    "sos", "name", "type", "version", "description", "extensions", "actors", "id", "role",
    "autonomy", "capabilities", "interface", "input", "output", "contracts", "parties",
    "assume", "guarantee", "authority", "decision_holder", "beta", "information", "alpha",
    "incentives", "lambda", "violation", "detect", "lifecycle", "states", "initial",
    "terminal", "transitions", "from", "to", "on", "deadline", "on_timeout", "monitors",
    "observe", "rule", "action", "norms", "kind", "obligation", "prohibition", "permission",
    "power", "bearer", "counterparty", "trigger", "content", "on_violation", "severity",
    "reparation", "sanction", "transition", "effects", "discharge", "on_amendment",
    "counts_as", "protocols", "steps", "precondition", "postcondition", "timing", "fallback",
    "algorithms", "metrics", "formula", "target", "environment", "model", "parameters",
    "variables", "labels", "commands", "guard", "updates", "prob", "set", "properties",
    "regimes", "rewards", "within", "throughout", "until", "Acknowledged", "Collaborative",
    "Directed", "Virtual", "Major", "Minor", "Critical",
]

# (label, text, where it goes). The text is indented relative to the cursor's line.
SNIPPETS = [
    ("Actor", '''- id: NEW_ACTOR
  role: "role of the actor"
  autonomy: medium
  capabilities: [do_something]
  interface:
    input:  [request]
    output: [reply]''', "an item of `actors:`"),
    ("Contract", '''- id: NEW_CONTRACT
  parties:
    - ACTOR_A
    - "ACTOR_B[*]"
  assume:
    - "ACTOR_A.is_operational == true"
  guarantee:
    - "response_time <= 300s"
  authority:
    decision_holder: ACTOR_A
    beta: 0.7
  information:
    alpha: 0.5
  incentives:
    type: task_completion
    lambda: 0.3''', "an item of `contracts:`"),
    ("Guarantee within a time", '''- "within(300s, ACTOR[i].status == Delivered)"''',
     "an item of `guarantee:` (bounded temporal operator, norms 0.1)"),
    ("Assumption throughout a time", '''- "throughout(300s, ACTOR.is_operational == true)"''',
     "an item of `assume:`"),
    ("Lifecycle", '''lifecycle:
  states: [Proposed, Active, Fulfilled, Violated]
  initial: Proposed
  terminal: [Fulfilled, Violated]
  transitions:
    - id: start
      from: Proposed
      to: Active
      on: "ACTOR_A -> ACTOR_B[i] : request"
    - id: finish
      from: Active
      to: Fulfilled
      on: "ACTOR_B[i] -> ACTOR_A : reply"
      deadline: 5s
      on_violation:
        transition: Violated
        severity: Major''', "a key of a contract (sos-dsl 0.1)"),
    ("Norm: obligation with a deadline", '''- id: answer
  kind: obligation
  bearer: "ACTOR_B[i]"
  counterparty: ACTOR_A
  trigger: "ACTOR_A -> ACTOR_B[i] : request"
  content: "ACTOR_B[i] -> ACTOR_A : reply"
  deadline: 5s
  on_violation:
    severity: Major''', "an item of a contract's `norms:` (norms 0.1)"),
    ("Norm: prohibition", '''- id: no_unsafe_state
  kind: prohibition
  bearer: "ACTOR_B[i]"
  content: "ACTOR_B[i].battery < 10 AND ACTOR_B[i].status == InTransit"
  on_violation:
    severity: Critical''', "an item of a contract's `norms:`"),
    ("Norm: permission", '''- id: may_ask
  kind: permission
  bearer: "ACTOR_B[i]"
  content: "ACTOR_B[i] -> ACTOR_A : help_request"''', "an item of a contract's `norms:`"),
    ("Norm: power", '''- id: reassign
  kind: power
  bearer: ACTOR_A
  content: reassignment
  effects:
    discharge: [answer]''', "an item of a contract's `norms:`"),
    ("Monitor", '''- id: battery_guard
  observe: "ACTOR_B[i].battery"
  sampling: periodic(500ms)
  rule: "ACTOR_B[i].battery < 20"
  on_match:
    transition: Violated
    severity: Major''', "an item of a contract's `monitors:` (sos-dsl 0.1)"),
    ("Protocol", '''- id: NewProtocol
  trigger: "new_request(ACTOR_A)"
  steps:
    - "ACTOR_A -> ACTOR_B[i] : request"
    - "ACTOR_B[i] -> ACTOR_A : reply"''', "an item of `protocols:`"),
    ("Stochastic model (stochastic 0.1)", '''model:
  kind: dtmc
  parameters:
    - { id: p_fail, range: [0, 0.5], value: 0.05 }
  variables:
    - { id: task, type: "{Pending, Done}", init: Pending }
  labels:
    done: "task == Done"
  commands:
    - action: attempt
      guard: "task == Pending"
      updates:
        - prob: "1 - p_fail"
          set: { task: Done }
        - prob: rest
          set: { task: Pending }
  properties:
    - { id: P_DONE, expr: "P=? [ F done ]" }''', "a key of `sos:`, with `- stochastic: 0.1` under `extensions:`"),
]


def snippets() -> List[Dict[str, str]]:
    return [{"label": label, "text": text, "where": where} for label, text, where in SNIPPETS]


def names(source: str, ir: Optional[dict]) -> List[Dict[str, str]]:
    """[{"name", "kind"}] of the names in the design and the keys of CADL, without repeats."""
    found: Dict[str, str] = {}

    def add(name, kind):
        name = str(name or "").strip()
        if name and name not in found:
            found[name] = kind

    institution = (ir or {}).get("institution") or {}
    for a in institution.get("actors") or []:
        ref = str(a.get("id") or "")
        base = ref.split("[", 1)[0]
        add(base, "actor")
        if "[" in ref or re.search(rf'["\s]{re.escape(base)}\[', source):
            add(f"{base}[i]", "actor")
            add(f"{base}[*]", "actor")
        interface = a.get("interface") or {}
        for message in (a.get("inputs") or interface.get("input") or []) + \
                (a.get("outputs") or interface.get("output") or []):
            add(message, "message")
    for c in institution.get("contracts") or []:
        add(c.get("id"), "contract")
        lifecycle = c.get("lifecycle") or {}
        for state in lifecycle.get("states") or []:
            add(state.get("name") if isinstance(state, dict) else state, "state")
    for p in ((ir or {}).get("protocol") or {}).get("protocols") or []:
        add(p.get("id"), "protocol")
    for m in (ir or {}).get("metrics") or []:
        add(m.get("id"), "metric")
    # norms and quantities, read from the text (the IR does not keep the norms)
    for match in re.finditer(r"^\s*-\s*id:\s*[\"']?([A-Za-z_]\w*)", source, re.M):
        add(match.group(1), "id")
    for match in re.finditer(r"\b([A-Z][A-Z0-9_]*(?:\[[^\]\s]*\])?\.[a-z_]\w*)", source):
        add(match.group(1), "quantity")
    for key in KEYS:
        add(key, "key")
    return [{"name": n, "kind": k} for n, k in found.items()]
