"""A STAMP control structure inferred from a design, and an STPA table to fill in.

STAMP (Leveson) models safety as control: controllers send *control actions*
down to the processes they control and receive *feedback* back up. CADL
does not say which messages are control and which are feedback, so this
module infers it from authority:

- A contract's decision holder **controls** the contract's other parties.
  Levels follow from that relation: a controller sits above what it
  controls (actors in a cycle of control share a level).
- A message from a controller to an actor it controls is a **control
  action**; a message back up, or a monitor of the contract that observes
  the controlled actor, is **feedback** (a monitor is the contract runtime
  watching on the controller's behalf).
- Every other message is **coordination** between actors that do not
  control each other.

Each controller → controlled pair is a **control loop**. A loop with
control actions but no feedback is *open*: the controller acts without
knowing the effect, the classic cause of an unsafe control action. A loop
with feedback but no control action means the controller is informed but
cannot act through the CADL.

``uca_rows`` gives the STPA step-3 table for every control action, with
the four guide words and hints from the CADL (deadlines that bound the
action, monitors that would detect its effect).
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

from backend.services import design_graph as graph


@dataclass
class Loop:
    controller: str
    controlled: str
    contracts: List[str] = field(default_factory=list)
    control: List[str] = field(default_factory=list)      # messages down
    feedback: List[str] = field(default_factory=list)     # messages up, "monitor <id>"

    @property
    def status(self) -> str:
        if self.control and self.feedback:
            return "closed"
        if self.control:
            return "open: no feedback"
        if self.feedback:
            return "no control action"
        return "authority only"


@dataclass
class ControlStructure:
    levels: Dict[str, int]                        # 0 = top
    loops: List[Loop]
    coordination: List[Tuple[str, str, str]]      # sender, receiver, message
    actors: List[str]

    def loop(self, controller: str, controlled: str) -> Optional[Loop]:
        return next((l for l in self.loops
                     if l.controller == controller and l.controlled == controlled), None)


def _contracts(ir: dict) -> List[dict]:
    return [c for c in (ir.get("institution") or {}).get("contracts") or [] if isinstance(c, dict)]


def _components(actors: List[str], controls: Dict[str, set]) -> Dict[str, int]:
    """Strongly connected components (Tarjan), as {actor: component index}."""
    index, low, on_stack, stack, comp = {}, {}, set(), [], {}
    counter = [0]

    def visit(v):
        index[v] = low[v] = counter[0]
        counter[0] += 1
        stack.append(v)
        on_stack.add(v)
        for w in sorted(controls.get(v, ())):
            if w not in index:
                visit(w)
                low[v] = min(low[v], low[w])
            elif w in on_stack:
                low[v] = min(low[v], index[w])
        if low[v] == index[v]:
            n = len(set(comp.values()))
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp[w] = n
                if w == v:
                    break

    for a in actors:
        if a not in index:
            visit(a)
    return comp


def _levels(actors: List[str], controls: Dict[str, set]) -> Dict[str, int]:
    """Level 0 at the top; a controlled actor is always below its controller.

    Actors that control each other (a cycle of control) form one component
    and share a level; components are placed by the longest chain of control
    above them, so the result does not depend on the order of the actors.
    """
    comp = _components(actors, controls)
    above: Dict[int, set] = {c: set() for c in set(comp.values())}
    for c, below in controls.items():
        for d in below:
            if comp[c] != comp[d]:
                above[comp[d]].add(comp[c])
    depth: Dict[int, int] = {}

    def level_of(k: int) -> int:
        if k not in depth:
            depth[k] = 0 if not above[k] else 1 + max(level_of(u) for u in above[k])
        return depth[k]

    return {a: level_of(comp[a]) for a in actors}


def control_structure(ir: dict) -> ControlStructure:
    actors = graph.actor_ids(ir)
    known = set(actors)
    controls: Dict[str, set] = {a: set() for a in actors}
    loops: Dict[Tuple[str, str], Loop] = {}
    for holder, party, cid in graph.authority_edges(ir):
        if holder in known and party in known:
            controls[holder].add(party)
            loop = loops.setdefault((holder, party), Loop(holder, party))
            if cid not in loop.contracts:
                loop.contracts.append(cid)

    # A message from s to r is a control action of the loop s → r if s controls r,
    # and feedback of the loop r → s if r controls s. When both control each other
    # it is both: s acts on r, and r learns about s from it.
    coordination: List[Tuple[str, str, str]] = []
    for e in graph.message_edges(ir):
        if e.sender == e.receiver:
            continue
        down, up = loops.get((e.sender, e.receiver)), loops.get((e.receiver, e.sender))
        if down is not None and e.message not in down.control:
            down.control.append(e.message)
        if up is not None and e.message not in up.feedback:
            up.feedback.append(e.message)
        if down is None and up is None and (e.sender, e.receiver, e.message) not in coordination:
            coordination.append((e.sender, e.receiver, e.message))

    # Monitors observe attributes of actors on behalf of their contract's holder.
    for c in _contracts(ir):
        holder = graph.base((c.get("governance") or {}).get("decision_holder"))
        for m in c.get("monitors") or []:
            for item in m.get("observe") or []:
                text = str(item)
                if "." not in text:
                    continue
                watched = graph.base(text.split(".", 1)[0])
                loop = loops.get((holder, watched))
                label = f"monitor {m.get('id')}"
                if loop is not None and label not in loop.feedback:
                    loop.feedback.append(label)

    return ControlStructure(_levels(actors, controls), list(loops.values()), coordination, actors)


def findings(cs: ControlStructure) -> List[str]:
    notes = []
    for l in cs.loops:
        where = ", ".join(l.contracts)
        if l.status == "open: no feedback":
            notes.append(
                f"Open loop: {l.controller} controls {l.controlled} ({where}) with "
                f"{', '.join(l.control)} but gets no feedback — no message back and no "
                f"monitor of {l.controlled}.")
        elif l.status == "no control action":
            notes.append(
                f"{l.controller} decides for {l.controlled} ({where}) and is informed "
                f"({', '.join(l.feedback)}) but sends it no control action.")
        elif l.status == "authority only":
            notes.append(
                f"{l.controller} decides for {l.controlled} ({where}) but they exchange "
                f"nothing: neither control actions nor feedback.")
    if not cs.loops:
        notes.append("No contract names a decision holder, so no control structure can be "
                     "inferred: every message is coordination between peers.")
    return notes


def control_structure_dot(cs: ControlStructure, dark: bool = False) -> str:
    def esc(text) -> str:
        return str(text).replace("\\", "\\\\").replace('"', r'\"')

    ink = "#d0d0d0" if dark else "#555555"
    text = "#f0f0f0" if dark else "#222222"
    control = "#ff7b72" if dark else "#cf222e"      # control actions: red, downward
    feedback = "#79c0ff" if dark else "#0969da"     # feedback: blue, upward
    open_fill = "#5a1e1e" if dark else "#ffebe9"
    peer = "#8b949e"
    node_fill = "#1f2a37" if dark else "#f6f8fa"
    ctrl_fill = "#243b55" if dark else "#dbe9f6"
    controllers = {l.controller for l in cs.loops}
    open_targets = {l.controlled for l in cs.loops if l.status == "open: no feedback"}

    lines = [
        'digraph control_structure {', "  rankdir=TB; newrank=true; nodesep=0.7; ranksep=1.0;",
        '  bgcolor="transparent";',
        f'  node [shape=box style="filled" fontname="Helvetica" fontsize=11 color="{ink}" fontcolor="{text}"];',
        f'  edge [fontname="Helvetica" fontsize=9];',
    ]
    for a in cs.actors:
        fill = open_fill if a in open_targets else ctrl_fill if a in controllers else node_fill
        role = "controller" if a in controllers else "controlled" if any(
            l.controlled == a for l in cs.loops) else "peer"
        lines.append(f'  "{esc(a)}" [label="{esc(a)}\\n({role})" fillcolor="{fill}"];')
    by_level: Dict[int, List[str]] = {}
    for a, lv in cs.levels.items():
        by_level.setdefault(lv, []).append(a)
    for lv, names in sorted(by_level.items()):
        lines.append("  { rank=same; " + " ".join(f'"{esc(n)}";' for n in names) + " }")
    for l in cs.loops:
        down = "\\n".join(esc(m) for m in l.control)
        up = "\\n".join(esc(m) for m in l.feedback)
        if l.control:
            lines.append(f'  "{esc(l.controller)}" -> "{esc(l.controlled)}" '
                         f'[label="{down}" color="{control}" fontcolor="{control}" penwidth=1.8];')
        elif not l.feedback:
            lines.append(f'  "{esc(l.controller)}" -> "{esc(l.controlled)}" '
                         f'[label="decides" style=dotted color="{control}" fontcolor="{control}"];')
        else:   # feedback only: keep the controller above with an invisible edge
            lines.append(f'  "{esc(l.controller)}" -> "{esc(l.controlled)}" [style=invis];')
        if l.feedback:
            lines.append(f'  "{esc(l.controlled)}" -> "{esc(l.controller)}" '
                         f'[label="{up}" color="{feedback}" fontcolor="{feedback}" '
                         f'style=dashed constraint=false];')
    for s, r, m in cs.coordination:
        lines.append(f'  "{esc(s)}" -> "{esc(r)}" [label="{esc(m)}" color="{peer}" '
                     f'fontcolor="{peer}" style=dotted constraint=false];')
    lines.append("}")
    return "\n".join(lines)


GUIDE_WORDS = ["Not provided", "Provided when unsafe",
               "Too early / too late / out of order", "Stopped too soon / applied too long"]


def _seconds(ms) -> str:
    ms = int(ms)
    return f"{ms // 1000}s" if ms % 1000 == 0 else f"{ms}ms"


def _hints(ir: dict, controller: str, controlled: str, message: str) -> Dict[str, str]:
    """What the CADL already says about the timing and detection of one control action.

    *deadlines*: a lifecycle transition that this action triggers, its own
    deadline, and the deadline of the step that must follow it (the
    controlled actor's response). *detected by*: monitors of the controller's
    contracts that observe the controlled actor.
    """
    timing, detect = [], []
    for c in _contracts(ir):
        if graph.base((c.get("governance") or {}).get("decision_holder")) != controller:
            continue
        transitions = (c.get("lifecycle") or {}).get("transitions") or []
        for t in transitions:
            match = graph._ARROW.match(str(t.get("on") or ""))
            if not match or graph._message_name(match.group(3)) != message \
                    or graph.base(match.group(1)) != controller or graph.base(match.group(2)) != controlled:
                continue
            if t.get("deadline_ms") is not None:
                timing.append(f"{c.get('id')}.{t.get('id')} within {_seconds(t['deadline_ms'])}")
            for nxt in transitions:
                if nxt is t or nxt.get("to_state") in (nxt.get("from_states") or []):
                    continue                    # the action itself, or a step that loops in place
                if t.get("to_state") in (nxt.get("from_states") or []) and nxt.get("deadline_ms") is not None:
                    then = nxt.get("on_violation_transition") or "a violation"
                    timing.append(f"then {nxt.get('id')} within {_seconds(nxt['deadline_ms'])}, else {then}")
        for m in c.get("monitors") or []:
            if any(graph.base(str(o).split(".", 1)[0]) == controlled for o in m.get("observe") or []):
                detect.append(f"monitor {m.get('id')}: {m.get('rule')}")
    return {"deadlines": "; ".join(dict.fromkeys(timing)), "detected by": "; ".join(dict.fromkeys(detect))}


def uca_key(row: Dict[str, str]) -> str:
    """The identity of a control action in the STPA table."""
    return f"{row['controller']} → {row['controlled']} : {row['control action']}"


def apply_entries(rows: List[Dict[str, str]], entries: Dict[str, Dict[str, str]]) -> List[Dict[str, str]]:
    """Fill the guide-word cells of ``rows`` from saved ``entries`` (keyed by uca_key)."""
    return [{**r, **{g: entries.get(uca_key(r), {}).get(g, "") for g in GUIDE_WORDS}} for r in rows]


def update_entries(entries: Dict[str, Dict[str, str]], keys: List[str],
                   records: List[Dict[str, str]]) -> Dict[str, Dict[str, str]]:
    """Store what was typed for each control action; entries of other actions are kept."""
    out = dict(entries)
    for key, rec in zip(keys, records):
        typed = {g: str(rec.get(g) or "") for g in GUIDE_WORDS}
        if any(typed.values()):
            out[key] = typed
        else:
            out.pop(key, None)
    return out


def uca_rows(ir: dict, cs: Optional[ControlStructure] = None) -> List[Dict[str, str]]:
    """STPA step 3: one row per control action, the guide-word cells left to fill in."""
    cs = cs or control_structure(ir)
    rows = []
    for l in cs.loops:
        for action in l.control:
            hint = _hints(ir, l.controller, l.controlled, action)
            rows.append({"controller": l.controller, "control action": action,
                         "controlled": l.controlled, "contracts": ", ".join(l.contracts),
                         **{g: "" for g in GUIDE_WORDS},
                         "deadlines in the CADL": hint["deadlines"],
                         "detected by": hint["detected by"],
                         "feedback": ", ".join(l.feedback) or "none"})
    return rows


__all__ = ["Loop", "ControlStructure", "control_structure", "findings",
           "control_structure_dot", "GUIDE_WORDS", "uca_rows", "uca_key",
           "apply_entries", "update_entries"]
