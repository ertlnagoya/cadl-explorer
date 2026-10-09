"""Sequence diagram for one protocol of a simulator IR, as an SVG string."""

import re
from html import escape
from typing import Iterable, List

_PALETTE = {
    False: dict(bg="#fafafa", border="#e0e0e0", line="#9a9a9a", text="#222222",
                head="#dbe9f6", head_stroke="#4682b4", arrow="#333333",
                compute="#fde9c8", compute_stroke="#c96a00", band="#ececec", band_text="#444444"),
    True: dict(bg="#1a1c24", border="#3a3d4a", line="#6a7084", text="#e6e6e6",
               head="#243b55", head_stroke="#8fc1e3", arrow="#e6e6e6",
               compute="#5a4217", compute_stroke="#ffb347", band="#2a2d39", band_text="#c8c8c8"),
}

_LANE = 190     # distance between lifelines
_ROW = 46       # height of one step
_TOP = 64       # y of the first step
_MARGIN = 100   # x of the first lifeline


def _base(ref) -> str:
    return str(ref or "").split("[", 1)[0].strip()


def _clip(text: str, limit: int) -> str:
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _band_label(step: dict) -> str:
    content = step.get("content") or ""
    if step.get("type") == "barrier":
        return f"wait until {content}"
    return {
        "if": f"if {step.get('condition') or ''}",
        "else": "else",
        "parallel_begin": "in parallel",
        "parallel_end": "end parallel",
    }.get(content, content)


def participants(protocol: dict) -> List[str]:
    """Actors of a protocol in order of first appearance."""
    seen: List[str] = []
    for step in protocol.get("steps") or []:
        for ref in (step.get("sender"), step.get("receiver")):
            base = _base(ref)
            if base and base not in seen:
                seen.append(base)
    return seen


def sequence_svg(protocol: dict, dark: bool = False, flagged: Iterable[str] = ()) -> str:
    """Render the steps of one IR protocol as a sequence diagram.

    Messages are arrows between lifelines, computations are boxes on the
    actor's lifeline, and if / parallel / barrier markers are full-width bands.
    Messages whose name is in ``flagged`` are drawn in red.
    """
    c = _PALETTE[bool(dark)]
    flagged = set(flagged)
    alert = "#ff6b6b" if dark else "#d1242f"
    steps = protocol.get("steps") or []
    actors = participants(protocol) or ["(no actor)"]
    x_of = {a: _MARGIN + i * _LANE for i, a in enumerate(actors)}
    width = _MARGIN * 2 + (len(actors) - 1) * _LANE
    height = _TOP + max(len(steps), 1) * _ROW + 20

    svg = [
        f'<svg viewBox="0 0 {width} {height}" style="max-width:{width}px;width:100%;'
        f'background:{c["bg"]};border:1px solid {c["border"]};border-radius:8px;" '
        f'font-family="Helvetica, Arial, sans-serif">',
        '<defs><marker id="seq-arrow" markerWidth="9" markerHeight="8" refX="8" refY="4" '
        f'orient="auto"><path d="M0,0 L9,4 L0,8 z" fill="{c["arrow"]}"/></marker>'
        '<marker id="seq-arrow-bad" markerWidth="9" markerHeight="8" refX="8" refY="4" '
        f'orient="auto"><path d="M0,0 L9,4 L0,8 z" fill="{alert}"/></marker></defs>',
    ]

    for actor, x in x_of.items():
        svg.append(
            f'<line x1="{x}" y1="40" x2="{x}" y2="{height - 10}" stroke="{c["line"]}" '
            f'stroke-width="1" stroke-dasharray="4 4"/>'
            f'<rect x="{x - 70}" y="10" width="140" height="28" rx="5" fill="{c["head"]}" '
            f'stroke="{c["head_stroke"]}"/>'
            f'<text x="{x}" y="29" text-anchor="middle" font-size="12" font-weight="bold" '
            f'fill="{c["text"]}">{escape(_clip(actor, 18))}</text>'
        )

    for i, step in enumerate(steps):
        y = _TOP + i * _ROW + _ROW / 2
        kind = step.get("type")
        sender, receiver = _base(step.get("sender")), _base(step.get("receiver"))
        content = step.get("content") or ""
        number = f"{i + 1}. "

        if kind == "message" and sender in x_of and receiver in x_of:
            x1, x2 = x_of[sender], x_of[receiver]
            # Show the receiver's index when it adds information ("ROBOT[*]").
            target = str(step.get("receiver") or "")
            suffix = f"  → {target}" if "[" in target else ""
            name = re.match(r"[A-Za-z_]\w*", content)
            bad = bool(name) and name.group(0) in flagged
            stroke = alert if bad else c["arrow"]
            ink = alert if bad else c["text"]
            marker = "seq-arrow-bad" if bad else "seq-arrow"
            thickness = 2.4 if bad else 1.4
            if x1 == x2:
                svg.append(
                    f'<path d="M{x1},{y - 8} h36 v16 h-30" fill="none" stroke="{stroke}" '
                    f'stroke-width="{thickness}" marker-end="url(#{marker})"/>'
                    f'<text x="{x1 + 44}" y="{y + 4}" font-size="11" fill="{ink}">'
                    f'{escape(_clip(number + content + suffix, 40))}</text>'
                )
            else:
                svg.append(
                    f'<line x1="{x1}" y1="{y}" x2="{x2}" y2="{y}" stroke="{stroke}" '
                    f'stroke-width="{thickness}" marker-end="url(#{marker})"/>'
                    f'<text x="{(x1 + x2) / 2}" y="{y - 6}" text-anchor="middle" font-size="11" '
                    f'fill="{ink}">{escape(_clip(number + content + suffix, 16 + 26 * abs(x2 - x1) // _LANE))}</text>'
                )
        elif kind == "compute":
            x = x_of.get(sender, x_of[actors[0]])
            svg.append(
                f'<rect x="{x - 84}" y="{y - 13}" width="168" height="26" rx="12" '
                f'fill="{c["compute"]}" stroke="{c["compute_stroke"]}"/>'
                f'<text x="{x}" y="{y + 4}" text-anchor="middle" font-size="11" '
                f'fill="{c["text"]}">{escape(_clip(number + content, 26))}</text>'
            )
        else:
            svg.append(
                f'<rect x="12" y="{y - 12}" width="{width - 24}" height="24" rx="4" '
                f'fill="{c["band"]}"/>'
                f'<text x="24" y="{y + 4}" font-size="11" font-style="italic" '
                f'fill="{c["band_text"]}">{escape(_clip(_band_label(step), width // 7))}</text>'
            )

    if not steps:
        svg.append(
            f'<text x="{width / 2}" y="{_TOP + 20}" text-anchor="middle" font-size="12" '
            f'fill="{c["band_text"]}">This protocol has no steps yet.</text>'
        )
    svg.append("</svg>")
    return "\n".join(svg)
