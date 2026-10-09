"""Scenario diagram: the fixed 11-node road graph with 5 robots and the arbitrator."""

from typing import List, Optional

_POSITIONS = {
    0: (100, 200), 1: (200, 100), 2: (350, 80),
    3: (500, 100), 4: (600, 200), 5: (550, 320),
    6: (400, 380), 7: (200, 350), 8: (250, 230),
    9: (450, 300), 10: (500, 140),
}
_EDGES = [
    (0, 1), (1, 2), (2, 3), (3, 4), (4, 5), (5, 6), (6, 7), (7, 0),
    (7, 8), (0, 8), (1, 8), (8, 9), (6, 9), (5, 9), (4, 10), (3, 10), (2, 10),
]
_ROBOT_NODES = [0, 2, 4, 6, 8]

_PALETTE = {
    False: dict(bg="#fafafa", border="#e0e0e0", edge="#bbb", node="#ffffff",
                node_stroke="#8a8a8a", text="#333", link="#999"),
    True: dict(bg="#1a1c24", border="#3a3d4a", edge="#555a6b", node="#262730",
               node_stroke="#9aa0b4", text="#e6e6e6", link="#7a8094"),
}
# Low and high ends of the motivation scale (sequential, single hue).
_MOTIVATION_LOW = (198, 219, 239)
_MOTIVATION_HIGH = (8, 81, 156)

# decision_authority (IR layer 1) -> (label, fill, draws command links to robots)
_ARBITRATOR = {
    "central": ("Arbitrator — central authority", "#c0392b", True),
    "hybrid": ("Arbitrator — verifier only", "#7f8c8d", False),
}


def motivation_color(m: float) -> str:
    """Colour for a motivation value in [0, 1]."""
    m = max(0.0, min(1.0, float(m)))
    r, g, b = (
        round(lo + (hi - lo) * m)
        for lo, hi in zip(_MOTIVATION_LOW, _MOTIVATION_HIGH)
    )
    return f"rgb({r},{g},{b})"


def scenario_svg(
    motivations: Optional[List[float]] = None,
    decision_authority: Optional[str] = None,
    dark: bool = False,
) -> str:
    """Render the scenario as an SVG string.

    With ``motivations`` the robots are coloured and labelled by their
    motivation value. ``decision_authority`` (from IR layer 1) selects how
    the arbitrator is drawn.
    """
    c = _PALETTE[bool(dark)]
    svg = [
        f'<svg viewBox="0 0 700 470" style="max-width:700px;width:100%;'
        f'background:{c["bg"]};border:1px solid {c["border"]};border-radius:8px;">',
    ]

    label, fill, commands = _ARBITRATOR.get(
        decision_authority, ("Arbitrator", "#7f8c8d", False),
    )
    arb_x, arb_y = 350, 440

    if commands:
        for nid in _ROBOT_NODES:
            x, y = _POSITIONS[nid]
            svg.append(
                f'<line x1="{arb_x}" y1="{arb_y - 15}" x2="{x + 25}" y2="{y - 20}" '
                f'stroke="{fill}" stroke-width="1" stroke-dasharray="4 4" opacity="0.6"/>'
            )

    for s, d in _EDGES:
        x1, y1 = _POSITIONS[s]
        x2, y2 = _POSITIONS[d]
        svg.append(
            f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" '
            f'stroke="{c["edge"]}" stroke-width="2"/>'
        )

    for nid, (x, y) in _POSITIONS.items():
        svg.append(
            f'<circle cx="{x}" cy="{y}" r="14" fill="{c["node"]}" '
            f'stroke="{c["node_stroke"]}" stroke-width="1.5"/>'
            f'<text x="{x}" y="{y + 4}" text-anchor="middle" font-size="11" '
            f'fill="{c["text"]}">{nid}</text>'
        )

    for i, nid in enumerate(_ROBOT_NODES):
        x, y = _POSITIONS[nid]
        rx, ry = x + 25, y - 20
        m = motivations[i] if motivations and i < len(motivations) else None
        robot_fill = motivation_color(m) if m is not None else "#4682b4"
        robot_text = "#ffffff" if m is None or m >= 0.5 else "#1a1a1a"
        svg.append(
            f'<rect x="{rx - 12}" y="{ry - 11}" width="24" height="22" rx="4" '
            f'fill="{robot_fill}" stroke="{c["node_stroke"]}" stroke-width="1"/>'
            f'<text x="{rx}" y="{ry + 4}" text-anchor="middle" font-size="10" '
            f'fill="{robot_text}" font-weight="bold">R{i}</text>'
        )
        if m is not None:
            svg.append(
                f'<text x="{rx + 16}" y="{ry + 4}" font-size="10" '
                f'fill="{c["text"]}">m={m:.1f}</text>'
            )

    svg.append(
        f'<rect x="{arb_x - 110}" y="{arb_y - 15}" width="220" height="28" rx="6" fill="{fill}"/>'
        f'<text x="{arb_x}" y="{arb_y + 4}" text-anchor="middle" font-size="12" '
        f'fill="white">{label}</text>'
    )
    svg.append("</svg>")
    return "\n".join(svg)
