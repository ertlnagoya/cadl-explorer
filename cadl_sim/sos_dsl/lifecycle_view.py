"""Build a Lifecycle View from a CADL Sim-IR JSON document.

This module is intentionally dependency-free (stdlib only) so that it
can be unit-tested without Streamlit. The Streamlit page in
``pages/`` imports these functions and feeds the DOT output to
``st.graphviz_chart``.

Input shape (relevant subset of cadl Sim-IR, see Appendix E.7):

    {
      "institution": {
        "contracts": [
          {
            "id": "DELIVERY_SLA",
            "lifecycle": {
              "states": [...],
              "initial": "Proposed",
              "terminal": [...],
              "transitions": [
                {"id": "...", "from_states": [...], "to_state": "...",
                 "on": "...", "deadline_ms": 5000,
                 "on_violation_transition": "Violated",
                 "on_violation_severity": "Major"}
              ]
            },
            "monitors": [
              {"id": "battery_guard", "rule": "...",
               "sampling_kind": "periodic", "sampling_period_ms": 500,
               "on_match_severity": "Major"}
            ]
          }
        ]
      }
    }
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


# ---------------------------------------------------------------------------
# View model
# ---------------------------------------------------------------------------

@dataclass
class LifecycleView:
    """Concrete view of a single contract's lifecycle, ready to render."""
    contract_id: str
    states: list[str] = field(default_factory=list)
    initial: str | None = None
    terminal: list[str] = field(default_factory=list)
    transitions: list[dict[str, Any]] = field(default_factory=list)


def build_lifecycle_view(contract: dict) -> LifecycleView | None:
    """Extract a LifecycleView from one contract IR dict.

    Returns None if the contract has no lifecycle: section.
    """
    lc = contract.get("lifecycle")
    if not lc or not isinstance(lc, dict):
        return None
    return LifecycleView(
        contract_id=str(contract.get("id", "")),
        states=list(lc.get("states", []) or []),
        initial=lc.get("initial"),
        terminal=list(lc.get("terminal", []) or []),
        transitions=list(lc.get("transitions", []) or []),
    )


def iter_contract_views(ir_doc: dict) -> Iterable[LifecycleView]:
    """Yield LifecycleViews for every contract in an IR document."""
    contracts = (
        ir_doc.get("institution", {}).get("contracts", [])
        if isinstance(ir_doc, dict)
        else []
    )
    for c in contracts:
        v = build_lifecycle_view(c)
        if v is not None:
            yield v


# ---------------------------------------------------------------------------
# DOT rendering — consumed by Streamlit's st.graphviz_chart
# ---------------------------------------------------------------------------

# Visual conventions:
#   - initial state: bold double-border
#   - terminal states: dashed border; "Violated" gets red, "Completed" green
#   - deadline transition: edge label includes Δ"5s"
#   - on_violation lifts: secondary dashed red edge to the violation state
_TERMINAL_FILL = {
    "Completed": "#d4f5d4",  # pale green
    "Violated": "#fde0e0",   # pale red
    "Terminated": "#e8e8e8", # pale grey
}


# Dark-background counterparts of the fills above.
_TERMINAL_FILL_DARK = {
    "Completed": "#1f4d2b",
    "Violated": "#5c2323",
    "Terminated": "#3a3a3a",
}


def _state_attrs(state: str, view: LifecycleView, dark: bool = False) -> str:
    parts: list[str] = [f'label="{state}"']
    if state == view.initial:
        parts.extend(['shape=doublecircle', 'style="bold,filled"',
                      f'fillcolor="{"#1f3a5f" if dark else "#e0ecff"}"'])
    elif state in view.terminal:
        fills = _TERMINAL_FILL_DARK if dark else _TERMINAL_FILL
        fill = fills.get(state, "#3a3a3a" if dark else "#e8e8e8")
        parts.extend(['shape=box', 'style="rounded,dashed,filled"',
                      f'fillcolor="{fill}"'])
    else:
        parts.extend(['shape=box', 'style="rounded,filled"',
                      f'fillcolor="{"#262730" if dark else "#ffffff"}"'])
    return ", ".join(parts)


def _format_ms(ms: Any) -> str | None:
    if ms is None:
        return None
    try:
        ms_i = int(ms)
    except (TypeError, ValueError):
        return None
    if ms_i % 1000 == 0:
        return f"{ms_i // 1000}s"
    return f"{ms_i}ms"


def _edge_label(tr: dict) -> str:
    parts: list[str] = []
    on = tr.get("on") or ""
    if on:
        # Trim long labels for readability.
        parts.append(on if len(on) <= 40 else on[:37] + "…")
    deadline = _format_ms(tr.get("deadline_ms"))
    if deadline is not None:
        parts.append(f"Δ {deadline}")
    when = tr.get("when")
    if when:
        parts.append(f"[{when}]")
    return r"\n".join(parts) if parts else tr.get("id", "")


def _escape_label(s: str) -> str:
    return s.replace('"', r'\"')


def lifecycle_to_dot(view: LifecycleView, dark: bool = False) -> str:
    """Render a LifecycleView to Graphviz DOT.

    The output is suitable for Streamlit's ``st.graphviz_chart``.
    ``dark`` switches to a palette that reads on a dark page background.
    """
    lines: list[str] = []
    lines.append(f'digraph "{_escape_label(view.contract_id)}_lifecycle" {{')
    lines.append('  rankdir=LR;')
    ink = ' color="#c8c8c8" fontcolor="#f0f0f0"' if dark else ''
    violation = '"#ff6b6b"' if dark else "red"
    lines.append('  bgcolor="transparent";')
    lines.append(f'  node [fontname="Helvetica" fontsize=11{ink}];')
    lines.append(f'  edge [fontname="Helvetica" fontsize=10{ink}];')
    lines.append('')

    # Nodes
    for st in view.states:
        attrs = _state_attrs(st, view, dark)
        lines.append(f'  "{_escape_label(st)}" [{attrs}];')

    # Transitions
    lines.append('')
    for tr in view.transitions:
        from_states = tr.get("from_states") or []
        to_state = tr.get("to_state")
        if not to_state:
            continue
        label = _escape_label(_edge_label(tr))
        for fr in from_states:
            lines.append(
                f'  "{_escape_label(fr)}" -> "{_escape_label(to_state)}" '
                f'[label="{label}"];'
            )

        # on_violation lifts as a secondary dashed red edge from each
        # source state to the violation target.
        on_viol = tr.get("on_violation_transition")
        if on_viol:
            sev = tr.get("on_violation_severity") or "Major"
            for fr in from_states:
                lines.append(
                    f'  "{_escape_label(fr)}" -> "{_escape_label(on_viol)}" '
                    f'[style=dashed color={violation} fontcolor={violation} '
                    f'label="violation\\n{sev}"];'
                )

    lines.append('}')
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# Monitor summary — used as a sidecar panel on the Streamlit page
# ---------------------------------------------------------------------------

def monitors_summary(contract: dict) -> list[dict]:
    """Project each monitor to a small dict suitable for st.dataframe."""
    out: list[dict] = []
    for m in contract.get("monitors", []) or []:
        sampling = m.get("sampling_kind") or "event"
        period_ms = m.get("sampling_period_ms")
        if sampling == "periodic" and period_ms is not None:
            sampling_label = f"periodic({_format_ms(period_ms)})"
        else:
            sampling_label = sampling
        out.append({
            "id": m.get("id", ""),
            "sampling": sampling_label,
            "rule": m.get("rule", ""),
            "on_match": (
                m.get("on_match_transition")
                or m.get("on_match_violation")
                or ""
            ),
            "severity": m.get("on_match_severity") or "",
        })
    return out
