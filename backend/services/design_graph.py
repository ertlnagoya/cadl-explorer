"""Structure of a design as graphs and tables: who talks to whom, who decides, who matters.

Everything here is derived from the simulator IR (and, for duties, the
source) of a design; nothing is added that the CADL does not say.

- ``message_edges``: every message one actor sends another, from protocol
  steps, lifecycle events (``on: "A -> B : msg"``) and information
  sharing (``sharing: "A -> B : item"``), each marked whether both
  interfaces declare it.
- ``authority_edges``: decision holder → each other party of a contract.
- ``centrality``: per actor, how many contracts it is in and decides, how
  many messages it sends and receives, how many partners it has, and its
  betweenness in the message graph (how often it lies on the shortest
  path between two others — a relay whose loss cuts the others apart).
- ``n2_matrix``: the N² chart, sender × receiver with the messages.
- ``responsibility_table``: actor × contract with the actor's roles.

Actors defined as a group (``ROBOT[1..N]``) are one node, ``ROBOT``.
"""

from collections import deque
from dataclasses import dataclass
import re
from typing import Dict, List, Optional, Tuple

_ARROW = re.compile(r"^\s*([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*->\s*([A-Za-z_]\w*)(?:\[[^\]]*\])?\s*:\s*(.+?)\s*$")


def base(ref) -> str:
    """``ROBOT[i]`` / ``ROBOT[1..N]`` / ``ROBOT[*]`` → ``ROBOT``."""
    return str(ref or "").split("[", 1)[0].strip()


def _message_name(content) -> str:
    match = re.match(r"[A-Za-z_]\w*", str(content or "").strip())
    return match.group(0) if match else str(content or "").strip()


@dataclass(frozen=True)
class MessageEdge:
    sender: str
    receiver: str
    message: str
    origin: str          # e.g. "protocol TASK_DISPATCH", "DELIVERY_SLA lifecycle assign"
    declared: bool       # False when a declared interface (outputs / inputs) leaves it out


def _actors(ir: dict) -> Dict[str, dict]:
    return {base(a.get("id")): a for a in (ir.get("institution") or {}).get("actors") or []
            if isinstance(a, dict) and a.get("id")}


def _contracts(ir: dict) -> List[dict]:
    return [c for c in (ir.get("institution") or {}).get("contracts") or [] if isinstance(c, dict)]


def message_edges(ir: dict) -> List[MessageEdge]:
    actors = _actors(ir)

    def declared(sender: str, receiver: str, message: str) -> bool:
        # As in the Interfaces check: a side that lists no messages is not
        # checked; a side that lists some must include this one.
        out = actors.get(sender, {}).get("outputs") or []
        inp = actors.get(receiver, {}).get("inputs") or []
        return (not out or message in out) and (not inp or message in inp)

    edges: List[MessageEdge] = []

    def add(sender, receiver, content, origin):
        s, r, m = base(sender), base(receiver), _message_name(content)
        if s and r and m and s in actors and r in actors:
            edges.append(MessageEdge(s, r, m, origin, declared(s, r, m)))

    for p in (ir.get("protocol") or {}).get("protocols") or []:
        for step in p.get("steps") or []:
            if step.get("type") == "message":
                add(step.get("sender"), step.get("receiver"), step.get("content"),
                    f"protocol {p.get('id')}")
    for c in _contracts(ir):
        cid = c.get("id")
        for t in (c.get("lifecycle") or {}).get("transitions") or []:
            match = _ARROW.match(str(t.get("on") or ""))
            if match:
                add(*match.groups(), f"{cid} lifecycle {t.get('id')}")
        sharing = (c.get("governance") or {}).get("sharing_mode")
        for part in str(sharing or "").split(";"):
            match = _ARROW.match(part)
            if match:
                add(*match.groups(), f"{cid} information sharing")
    return edges


def authority_edges(ir: dict) -> List[Tuple[str, str, str]]:
    """(decision holder, party, contract id) for each other party of a contract."""
    out = []
    for c in _contracts(ir):
        holder = base((c.get("governance") or {}).get("decision_holder"))
        if not holder:
            continue
        for party in dict.fromkeys(base(p) for p in c.get("parties") or []):
            if party and party != holder:
                out.append((holder, party, str(c.get("id"))))
    return out


def _betweenness(nodes: List[str], succ: Dict[str, set]) -> Dict[str, float]:
    """Brandes' betweenness on an unweighted directed graph, normalised to 0..1."""
    score = {v: 0.0 for v in nodes}
    for s in nodes:
        stack, pred = [], {v: [] for v in nodes}
        sigma, dist = dict.fromkeys(nodes, 0), dict.fromkeys(nodes, -1)
        sigma[s], dist[s] = 1, 0
        queue = deque([s])
        while queue:
            v = queue.popleft()
            stack.append(v)
            for w in succ.get(v, ()):
                if dist[w] < 0:
                    dist[w] = dist[v] + 1
                    queue.append(w)
                if dist[w] == dist[v] + 1:
                    sigma[w] += sigma[v]
                    pred[w].append(v)
        delta = dict.fromkeys(nodes, 0.0)
        while stack:
            w = stack.pop()
            for v in pred[w]:
                delta[v] += sigma[v] / sigma[w] * (1 + delta[w])
            if w != s:
                score[w] += delta[w]
    n = len(nodes)
    scale = 1.0 / ((n - 1) * (n - 2)) if n > 2 else 0.0
    return {v: score[v] * scale for v in nodes}


def centrality(ir: dict) -> List[Dict[str, object]]:
    """One row per actor, most central first (betweenness, then partners)."""
    actors = list(_actors(ir))
    edges = message_edges(ir)
    succ: Dict[str, set] = {a: set() for a in actors}
    sends = dict.fromkeys(actors, 0)
    receives = dict.fromkeys(actors, 0)
    partners: Dict[str, set] = {a: set() for a in actors}
    for e in {(e.sender, e.receiver, e.message) for e in edges}:
        s, r, _ = e
        if s != r:
            succ[s].add(r)
            partners[s].add(r)
            partners[r].add(s)
        sends[s] += 1
        receives[r] += 1
    between = _betweenness(actors, succ)
    in_contracts = dict.fromkeys(actors, 0)
    decides = dict.fromkeys(actors, 0)
    for c in _contracts(ir):
        for party in dict.fromkeys(base(p) for p in c.get("parties") or []):
            if party in in_contracts:
                in_contracts[party] += 1
        holder = base((c.get("governance") or {}).get("decision_holder"))
        if holder in decides:
            decides[holder] += 1
    rows = [{
        "actor": a, "contracts": in_contracts[a], "decides": decides[a],
        "sends": sends[a], "receives": receives[a], "partners": len(partners[a]),
        "betweenness": round(between[a], 3),
    } for a in actors]
    rows.sort(key=lambda r: (-r["betweenness"], -r["partners"], -r["decides"], r["actor"]))
    return rows


def n2_matrix(ir: dict) -> Dict[str, Dict[str, List[MessageEdge]]]:
    """{sender: {receiver: [edges]}} for every pair of actors (empty lists included)."""
    actors = list(_actors(ir))
    matrix = {s: {r: [] for r in actors} for s in actors}
    seen = set()
    for e in message_edges(ir):
        key = (e.sender, e.receiver, e.message)
        if key not in seen:
            seen.add(key)
            matrix[e.sender][e.receiver].append(e)
    return matrix


def _observed(contract: dict) -> set:
    names = set()
    for m in contract.get("monitors") or []:
        for item in m.get("observe") or []:
            match = re.match(r"\s*([A-Za-z_]\w*)", str(item))
            if match and "." in str(item):
                names.add(match.group(1))
    return names


def responsibility_table(ir: dict, duties: Optional[Dict[str, Dict[str, List[str]]]] = None
                         ) -> Dict[str, Dict[str, List[str]]]:
    """{actor: {contract: [roles]}}. Roles: party, decides, duties (n), observed."""
    duties = duties or {}
    actors = list(_actors(ir))
    table = {a: {} for a in actors}
    for c in _contracts(ir):
        cid = str(c.get("id"))
        parties = {base(p) for p in c.get("parties") or []}
        holder = base((c.get("governance") or {}).get("decision_holder"))
        observed = _observed(c)
        own = {base(k): v for k, v in (duties.get(cid) or {}).items()}
        for a in actors:
            roles = []
            if a == holder:
                roles.append("decides")
            if a in parties:
                roles.append("party")
            if own.get(a):
                roles.append(f"duties ({len(own[a])})")
            if a in observed:
                roles.append("observed")
            table[a][cid] = roles
    return table


def structure_gaps(ir: dict, duties: Optional[Dict[str, Dict[str, List[str]]]] = None) -> List[str]:
    """Plain sentences for what the tables reveal: nobody decides, nobody is responsible, …"""
    notes = []
    for c in _contracts(ir):
        if not base((c.get("governance") or {}).get("decision_holder")):
            notes.append(f"Nobody decides under {c.get('id')}.")
    table = responsibility_table(ir, duties)
    for actor, row in table.items():
        if not any(row.values()):
            notes.append(f"{actor} takes part in no contract.")
    edges = message_edges(ir)
    talking = {e.sender for e in edges} | {e.receiver for e in edges}
    for actor in table:
        if edges and actor not in talking:
            notes.append(f"{actor} sends and receives no message.")
    undeclared = sorted({(e.sender, e.receiver, e.message) for e in edges if not e.declared})
    for s, r, m in undeclared:
        notes.append(f"{s} → {r} : {m} is not declared in both interfaces.")
    return notes


def message_flow_dot(ir: dict, dark: bool = False, show_authority: bool = False) -> str:
    """Actors as nodes, shaded by betweenness; one edge per sender→receiver with its messages.

    Messages missing from an interface are drawn dashed in the warning colour.
    ``show_authority`` adds the decides edges of the contracts.
    """
    def esc(text) -> str:
        return str(text).replace("\\", "\\\\").replace('"', r'\"')

    ink = "#d0d0d0" if dark else "#555555"
    text = "#f0f0f0" if dark else "#222222"
    warn = "#f2cc60" if dark else "#bf8700"
    holder = "#ffb347" if dark else "#c96a00"
    shades = ["#243b55", "#2f4f6f", "#1f6f8b", "#2a8fb0"] if dark else \
             ["#eef5fb", "#cfe3f3", "#9cc8e8", "#5aa5d6"]
    rows = {r["actor"]: r for r in centrality(ir)}
    top = max([r["betweenness"] for r in rows.values()] + [0])

    # A message graph has no natural direction of flow, so a circular layout
    # (small designs) or a spring layout (larger ones) reads better than ranks.
    layout = "circo" if len(rows) <= 8 else "neato"
    lines = [
        f'digraph "{esc(ir.get("name", "sos"))}" {{', f"  layout={layout};",
        '  overlap=false; splines=curved; sep="+12"; mindist=1.6;', '  bgcolor="transparent";',
        f'  node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=11 color="{ink}" fontcolor="{text}"];',
        f'  edge [fontname="Helvetica" fontsize=9 color="{ink}" fontcolor="{text}"];',
    ]
    for actor, r in rows.items():
        level = 0 if top == 0 else min(3, int(round(3 * r["betweenness"] / top)))
        label = f"{esc(actor)}\\nbetweenness {r['betweenness']:g}"
        lines.append(f'  "{esc(actor)}" [label="{label}" fillcolor="{shades[level]}"];')
    grouped: Dict[Tuple[str, str], List[MessageEdge]] = {}
    for e in message_edges(ir):
        grouped.setdefault((e.sender, e.receiver), [])
        if e.message not in [x.message for x in grouped[(e.sender, e.receiver)]]:
            grouped[(e.sender, e.receiver)].append(e)
    decides: Dict[Tuple[str, str], List[str]] = {}
    if show_authority:
        for h, p, cid in authority_edges(ir):
            decides.setdefault((h, p), []).append(cid)
    # One edge per ordered pair: its messages, and (if shown) the contracts the
    # sender decides for the receiver. Curved splines keep A→B and B→A apart.
    for pair in list(grouped) + [k for k in decides if k not in grouped]:
        s, r = pair
        items = grouped.get(pair, [])
        parts = [esc(e.message) for e in items]
        parts += [f"decides {esc(cid)}" for cid in decides.get(pair, [])]
        label = "\\n".join(parts)        # (no backslash inside an f-string: Python 3.9)
        attrs = [f'label="{label}"']
        if any(not e.declared for e in items):
            attrs.append(f'style=dashed color="{warn}" fontcolor="{warn}"')
        elif pair in decides:
            attrs.append(f'color="{holder}" penwidth=2.2' + ("" if items else f' fontcolor="{holder}"'))
        lines.append(f'  "{esc(s)}" -> "{esc(r)}" [{" ".join(attrs)}];')
    lines.append("}")
    return "\n".join(lines)


__all__ = ["MessageEdge", "base", "message_edges", "authority_edges", "centrality",
           "n2_matrix", "responsibility_table", "structure_gaps", "message_flow_dot"]
