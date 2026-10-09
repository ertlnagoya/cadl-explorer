"""Contract architecture diagram: actors, contracts and who holds decisions."""

from typing import Optional


def _esc(text) -> str:
    return str(text).replace("\\", "\\\\").replace('"', r'\"')


def _base(ref: str) -> str:
    return str(ref).split("[", 1)[0].strip()


def _fmt(value) -> Optional[str]:
    return f"{value:g}" if isinstance(value, (int, float)) else None


_FLAG = {
    False: {"error": "#d1242f", "warning": "#bf8700"},
    True: {"error": "#ff6b6b", "warning": "#f2cc60"},
}


def _flag_attrs(flags: dict, section: str, item: str, dark: bool) -> str:
    """Border attributes for an item that has a finding against it."""
    level = (flags or {}).get(section, {}).get(item)
    return f' color="{_FLAG[bool(dark)][level]}" penwidth=3' if level else ""


def architecture_to_dot(ir: dict, dark: bool = False, flags: Optional[dict] = None) -> str:
    """Render the institution layer of a simulator IR to Graphviz DOT.

    Actors are boxes, contracts are rounded nodes joined to their parties.
    The edge from a contract's decision holder is drawn bold. ``flags``
    ({"Actors": {id: level}, "Contracts": {id: level}}) outlines items
    that have errors or warnings.
    """
    institution = ir.get("institution") or {}
    actors = institution.get("actors") or []
    contracts = institution.get("contracts") or []

    ink = "#d0d0d0" if dark else "#555555"
    text = "#f0f0f0" if dark else "#222222"
    actor_fill = {
        "low": "#243b55" if dark else "#dbe9f6",
        "medium": "#2f4f6f" if dark else "#b9d6ee",
        "high": "#1f6f8b" if dark else "#8fc1e3",
    }
    contract_fill = "#5a4217" if dark else "#fde9c8"
    holder = "#ffb347" if dark else "#c96a00"

    lines = [
        f'digraph "{_esc(ir.get("name", "sos"))}" {{',
        "  rankdir=LR;",
        '  bgcolor="transparent";',
        f'  node [fontname="Helvetica" fontsize=11 color="{ink}" fontcolor="{text}"];',
        f'  edge [fontname="Helvetica" fontsize=9 color="{ink}" fontcolor="{text}"];',
        "",
    ]

    known = set()
    for a in actors:
        aid = str(a.get("id", ""))
        known.add(aid)
        label = [aid]
        if a.get("role"):
            label.append(str(a["role"]))
        autonomy = a.get("autonomy")
        if autonomy:
            label.append(f"autonomy: {autonomy}")
        fill = actor_fill.get(str(autonomy), actor_fill["medium"])
        lines.append(
            f'  "actor:{_esc(aid)}" [label="{(chr(92) + "n").join(_esc(x) for x in label)}" '
            f'shape=box style="filled" fillcolor="{fill}"{_flag_attrs(flags, "Actors", aid, dark)}];'
        )

    lines.append("")
    for c in contracts:
        cid = str(c.get("id", ""))
        gov = c.get("governance") or {}
        params = [
            f"{sym} {_fmt(gov.get(key))}"
            for sym, key in (("α", "alpha"), ("β", "beta"), ("λ", "lambda"))
            if _fmt(gov.get(key)) is not None
        ]
        label = [cid]
        if params:
            label.append(" · ".join(params))
        extras = []
        lifecycle = c.get("lifecycle") or {}
        if lifecycle.get("states"):
            extras.append(f'{len(lifecycle["states"])} states')
        if c.get("monitors"):
            extras.append(f'{len(c["monitors"])} monitors')
        if extras:
            label.append(", ".join(extras))
        lines.append(
            f'  "contract:{_esc(cid)}" [label="{(chr(92) + "n").join(_esc(x) for x in label)}" '
            f'shape=box style="rounded,filled" fillcolor="{contract_fill}"'
            f'{_flag_attrs(flags, "Contracts", cid, dark)}];'
        )

        decision_holder = _base(gov.get("decision_holder") or "")
        seen = set()
        for party in c.get("parties") or []:
            base = _base(party)
            if base in seen:
                continue
            seen.add(base)
            if base not in known:
                # Unknown party: draw it so the mistake is visible.
                known.add(base)
                lines.append(
                    f'  "actor:{_esc(base)}" [label="{_esc(base)}\\n(undefined)" '
                    f'shape=box style="dashed"];'
                )
            if base == decision_holder:
                lines.append(
                    f'  "actor:{_esc(base)}" -> "contract:{_esc(cid)}" '
                    f'[label="decides" penwidth=2.2 color="{holder}" fontcolor="{holder}"];'
                )
            else:
                lines.append(f'  "actor:{_esc(base)}" -> "contract:{_esc(cid)}" [dir=none];')

    lines.append("}")
    return "\n".join(lines) + "\n"


def regimes_to_dot(ir: dict, dark: bool = False, flagged=()) -> str:
    """Render the top-level regime transitions of a simulator IR to Graphviz DOT.

    Regimes are nodes; each transition is an edge labelled with its
    condition and, when given, the protocol that carries it out.
    ``flagged`` holds names with a finding against them: a regime, a
    protocol named by a transition, or a transition as "FROM → TO".
    """
    flagged = set(flagged)
    alert = "#ff6b6b" if dark else "#d1242f"
    ink = "#d0d0d0" if dark else "#555555"
    text = "#f0f0f0" if dark else "#222222"
    fill = "#1f4d2b" if dark else "#d9f0dd"
    protocol_ink = "#ffb347" if dark else "#c96a00"

    lines = [
        'digraph "regimes" {',
        # Conditions make long edge labels; top-to-bottom keeps them readable.
        "  rankdir=TB;",
        "  nodesep=0.9;",
        '  bgcolor="transparent";',
        f'  node [fontname="Helvetica" fontsize=11 shape=box style="rounded,filled" '
        f'fillcolor="{fill}" color="{ink}" fontcolor="{text}"];',
        f'  edge [fontname="Helvetica" fontsize=9 color="{ink}" fontcolor="{text}"];',
        "",
    ]
    transitions = ir.get("transitions") or []
    regimes = []
    for t in transitions:
        for name in (t.get("from_regime"), t.get("to_regime")):
            if name and name not in regimes:
                regimes.append(name)
    for name in regimes:
        mark = f' [color="{alert}" penwidth=3]' if name in flagged else ""
        lines.append(f'  "{_esc(name)}"{mark};')
    for t in transitions:
        if not t.get("from_regime") or not t.get("to_regime"):
            continue
        label = [_esc(t["condition"])] if t.get("condition") else []
        attrs = ""
        if t.get("protocol"):
            label.append(_esc(f"via {t['protocol']}"))
            attrs = f' color="{protocol_ink}" fontcolor="{protocol_ink}"'
        if (t.get("protocol") in flagged
                or f"{t['from_regime']} → {t['to_regime']}" in flagged):
            attrs = f' color="{alert}" fontcolor="{alert}" penwidth=2.5'
        lines.append(
            f'  "{_esc(t["from_regime"])}" -> "{_esc(t["to_regime"])}" '
            f'[label="{(chr(92) + "n").join(label)}"{attrs}];'
        )
    lines.append("}")
    return "\n".join(lines) + "\n"
