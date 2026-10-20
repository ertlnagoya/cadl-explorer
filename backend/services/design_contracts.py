"""What watches each promise, and how the contracts depend on each other.

Derived from the simulator IR (and, for ``# @amend:`` names, the source) of a
design; the CADL does not link a guarantee to the monitor that checks it, so
the links here are found by the quantities they name.

- ``coverage``: every assumption and guarantee of every contract, with what
  watches it at run time — a monitor of the contract whose ``observe`` or
  ``rule`` names one of its quantities, the contract's ``violation.detect``
  (``violation_detect`` in the IR), or a lifecycle deadline that equals its
  time bound — and the metrics that
  measure it afterwards (a metric whose id is one of its quantities).
- ``coverage_findings``: guarantees nothing watches, and monitors that watch
  nothing an assumption or guarantee names.
- ``dependencies``: contracts that share parties, and contracts whose
  assumption another contract guarantees (assume-guarantee reasoning);
  ``assumption_sources`` lists, per assumption, who guarantees it.
- ``dependency_dot``: the contracts as a graph of those dependencies.

A quantity is the last part of a name: ``ROBOT[i].battery`` names
``battery``. State names, keywords and units are not quantities.
"""

from dataclasses import dataclass, field
import re
from typing import Dict, List, Optional, Set, Tuple

from backend.services.design_graph import base

_NAME = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
_KEYWORDS = {"AND", "OR", "NOT", "IN", "and", "or", "not", "true", "false", "True", "False",
             "state", "now"}
_DURATION = re.compile(r"\s*(\d+(?:\.\d+)?)\s*(ms|sec|min|s|m|h)\s*$")
_UNIT_MS = {"ms": 1, "s": 1000, "sec": 1000, "m": 60000, "min": 60000, "h": 3600000}
_NAME_AT = r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)"
_UPPER_BOUND = re.compile(r"^\s*" + _NAME_AT + r"\s*(<=|<)\s*(.+?)\s*$")       # name <= bound
_LOWER_BOUND = re.compile(r"^\s*(.+?)\s*(>=|>)\s*" + _NAME_AT + r"\s*$")       # bound >= name
_AND = re.compile(r"\s+AND\s+")
_STRING = re.compile(r"\"[^\"]*\"|'[^']*'")
_AMEND_DEADLINE = re.compile(r"^\s*(?:-\s*)?deadline:\s*['\"]?([^'\"\s#]+)['\"]?\s*#\s*@amend:\s*([A-Za-z_]\w*)")
_ITEM = re.compile(r"^\s*-\s")
_ID = re.compile(r"^\s*(?:-\s*)?id:\s*['\"]?([A-Za-z_]\w*)")


def _contracts(ir: dict) -> List[dict]:
    return [c for c in (ir.get("institution") or {}).get("contracts") or []
            if isinstance(c, dict) and c.get("id")]


def _states(contract: dict) -> Set[str]:
    return {str(s) for s in (contract.get("lifecycle") or {}).get("states") or []}


def _actors(ir: dict) -> Set[str]:
    return {base(a.get("id")) for a in (ir.get("institution") or {}).get("actors") or []
            if isinstance(a, dict) and a.get("id")}


def _skip(ir: dict, contract: dict) -> Set[str]:
    """Names that are not quantities in this contract: its states and the actors."""
    return _states(contract) | _actors(ir)


def quantities(expr, exclude: Set[str] = frozenset()) -> Set[str]:
    """The quantities an expression names: ``ROBOT[i].battery < 20 AND state == Assigned`` → {battery}.

    Index brackets, ``IN [..]`` lists and quoted strings are dropped; so are
    keywords, the names in ``exclude`` (the contract's states and the
    actors, which are not quantities), units after a number, and
    a function name followed by arguments (``min_dist(a, b)`` names a and b;
    a predicate without arguments, ``conflict_free()``, names itself).
    """
    text = re.sub(r"\[[^\]]*\]", "", _STRING.sub(" ", str(expr or "")))
    names = set()
    for m in _NAME.finditer(text):
        if m.start() and (text[m.start() - 1].isdigit() or text[m.start() - 1] == "."):
            continue                                    # 300s, 0.5e3 ...
        token = m.group(0)
        rest = text[m.end():].lstrip()
        if rest.startswith("(") and not rest.startswith("()"):
            continue                                    # a function applied to arguments
        if token in _KEYWORDS or token in exclude:
            continue
        names.add(token.split(".")[-1])
    return names


def _duration_ms(text) -> Optional[int]:
    match = _DURATION.match(str(text or ""))
    return int(float(match.group(1)) * _UNIT_MS[match.group(2)]) if match else None


def _seconds(ms: int) -> str:
    return f"{ms / 1000:g}s"


def amend_deadlines(source: Optional[str]) -> Dict[str, Tuple[str, int]]:
    """``deadline: 60s  # @amend:hold_limit`` → {"hold_limit": (transition id, 60000)}."""
    out: Dict[str, Tuple[str, int]] = {}
    transition = ""
    for line in str(source or "").splitlines():
        if _ITEM.match(line):           # a new list item: its id may come on a later line
            transition = ""
        tid = _ID.match(line)
        if tid:
            transition = tid.group(1)
        match = _AMEND_DEADLINE.match(line)
        if match and _duration_ms(match.group(1)) is not None:
            out[match.group(2)] = (transition, _duration_ms(match.group(1)))
    return out


@dataclass
class Promise:
    contract: str
    kind: str                 # "assume" | "guarantee"
    text: str
    names: Set[str]
    watched_by: List[str] = field(default_factory=list)
    measured_by: List[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        return "watched" if self.watched_by else "measured only" if self.measured_by else "not watched"


def _time_bounds(text: str) -> List[Tuple[str, str]]:
    """``a <= 120s AND 60s > b`` → [(a, "120s"), (b, "60s")]: each upper bound in a conjunction."""
    out = []
    for part in _AND.split(re.sub(r"\[[^\]]*\]", "", text)):
        upper, lower = _UPPER_BOUND.match(part), _LOWER_BOUND.match(part)
        if upper:
            out.append((upper.group(1).split(".")[-1], upper.group(3)))
        elif lower:
            out.append((lower.group(3).split(".")[-1], lower.group(1)))
    return out


def _deadline_matches(text: str, transitions: List[dict], amended: Dict[str, Tuple[str, int]]
                      ) -> List[str]:
    """Deadlines that bound the same time as ``quantity <= bound``.

    A deadline of the same length on a transition whose id is a whole part of
    the quantity's name (``pickup_time <= 120s`` and transition ``pickup``), or
    the deadline the bound names through ``# @amend:`` (``hold_time <= hold_limit``).
    """
    out = []
    for quantity, bound in _time_bounds(text):
        if bound in amended:
            tid, ms = amended[bound]
            if any(t.get("id") == tid and t.get("deadline_ms") == ms for t in transitions):
                out.append(f"deadline of {tid} ({_seconds(ms)}, @amend:{bound})")
            continue
        ms = _duration_ms(bound)
        for t in transitions:
            tid = str(t.get("id") or "")
            if ms is not None and t.get("deadline_ms") == ms and tid and \
                    re.search(rf"(^|_){re.escape(tid)}(_|$)", quantity):
                out.append(f"deadline of {tid} ({_seconds(ms)})")
    return list(dict.fromkeys(out))


def coverage(ir: dict, source: Optional[str] = None) -> List[Promise]:
    """Every assumption and guarantee, with what watches it and what measures it."""
    metrics = [str(m.get("id")) for m in ir.get("metrics") or [] if isinstance(m, dict) and m.get("id")]
    amended = amend_deadlines(source)
    out: List[Promise] = []
    for c in _contracts(ir):
        states = _skip(ir, c)
        transitions = [t for t in (c.get("lifecycle") or {}).get("transitions") or [] if isinstance(t, dict)]
        monitors = [m for m in c.get("monitors") or [] if isinstance(m, dict)]
        detect = quantities(c.get("violation_detect"), states)
        for kind in ("assume", "guarantee"):
            for text in c.get(kind) or []:
                p = Promise(str(c["id"]), kind, str(text), quantities(text, states))
                for m in monitors:
                    observed = set()
                    for o in m.get("observe") or []:
                        observed |= quantities(o, states)
                    if p.names & (observed | quantities(m.get("rule"), states)):
                        p.watched_by.append(f"monitor {m.get('id')}")
                if p.names & detect:
                    p.watched_by.append("violation.detect")
                if kind == "guarantee":
                    p.watched_by += _deadline_matches(str(text), transitions, amended)
                p.measured_by = [f"metric {mid}" for mid in metrics if mid in p.names]
                out.append(p)
    return out


def coverage_findings(ir: dict, promises: List[Promise]) -> List[str]:
    notes = []
    for p in promises:
        if p.kind == "guarantee" and not p.watched_by:
            how = (f" It is measured afterwards ({', '.join(p.measured_by)}), not watched while "
                   "the contract runs." if p.measured_by else "")
            notes.append(f"Nothing watches the guarantee `{p.text}` of {p.contract}: no monitor, "
                         f"`violation.detect` or deadline names what it bounds.{how}")
    for c in _contracts(ir):
        named = set().union(*[p.names for p in promises if p.contract == str(c["id"])] or [set()])
        for m in c.get("monitors") or []:
            if not isinstance(m, dict):
                continue
            seen = quantities(m.get("rule"), _skip(ir, c))
            for o in m.get("observe") or []:
                seen |= quantities(o, _skip(ir, c))
            if seen and not seen & named:
                notes.append(f"Monitor {m.get('id')} of {c['id']} watches "
                             f"{', '.join(f'`{n}`' for n in sorted(seen))}, which no assumption "
                             "or guarantee of the contract names: it checks an obligation the "
                             "contract does not state.")
    return notes


@dataclass(frozen=True)
class Dependency:
    source: str
    target: str
    kind: str                 # "shared parties" | "assumes what it guarantees"
    detail: Tuple[str, ...]   # the shared actors, or the quantities


def _members(contract: dict) -> List[str]:
    holder = (contract.get("governance") or {}).get("decision_holder")
    out = [base(p) for p in contract.get("parties") or []]
    if holder:
        out.append(base(holder))
    return list(dict.fromkeys(a for a in out if a))


def dependencies(ir: dict) -> List[Dependency]:
    """Shared parties (undirected, once per pair) and assume-guarantee links (provider → user)."""
    contracts = _contracts(ir)
    out: List[Dependency] = []
    for i, a in enumerate(contracts):
        for b in contracts[i + 1:]:
            shared = [x for x in _members(a) if x in _members(b)]
            if shared:
                out.append(Dependency(str(a["id"]), str(b["id"]), "shared parties", tuple(shared)))
    for provider in contracts:
        given = set()
        for g in provider.get("guarantee") or []:
            given |= quantities(g, _skip(ir, provider))
        for user in contracts:
            if user is provider:
                continue
            needed = set()
            for s in user.get("assume") or []:
                needed |= quantities(s, _skip(ir, user))
            if given & needed:
                out.append(Dependency(str(provider["id"]), str(user["id"]),
                                      "assumes what it guarantees", tuple(sorted(given & needed))))
    return out


def assumption_sources(ir: dict, deps: List[Dependency]) -> List[Dict[str, str]]:
    """Every assumption with the contracts that guarantee one of its quantities."""
    rows = []
    for c in _contracts(ir):
        for s in c.get("assume") or []:
            names = quantities(s, _skip(ir, c))
            by = [d.source for d in deps if d.kind == "assumes what it guarantees"
                  and d.target == str(c["id"]) and names & set(d.detail)]
            rows.append({"contract": str(c["id"]), "assumption": str(s),
                         "guaranteed by": ", ".join(dict.fromkeys(by))})
    return rows


def dependency_findings(ir: dict, deps: List[Dependency]) -> List[str]:
    notes = []
    ids = [str(c["id"]) for c in _contracts(ir)]
    if len(ids) < 2:
        return notes
    linked = {d.source for d in deps} | {d.target for d in deps}
    for cid in ids:
        if cid not in linked:
            notes.append(f"{cid} shares no party with another contract and relies on none: "
                         "it can be read, and changed, on its own.")
    rows = assumption_sources(ir, deps)
    if rows:
        backed = sum(bool(r["guaranteed by"]) for r in rows)
        rest = len(rows) - backed
        notes.append(f"{backed} of {len(rows)} assumption{'s' if len(rows) != 1 else ''} "
                     f"{'is' if backed == 1 else 'are'} guaranteed by another contract"
                     + ("." if not rest else
                        f"; the other{'s rest' if rest != 1 else ' rests'} on the environment or "
                        "on an actor's own behaviour."))
    return notes


def dependency_dot(ir: dict, dark: bool = False) -> str:
    """Contracts as nodes; grey lines for shared parties, blue arrows provider → user."""
    def esc(text) -> str:
        return str(text).replace("\\", "\\\\").replace('"', r'\"')

    ink = "#d0d0d0" if dark else "#555555"
    text = "#f0f0f0" if dark else "#222222"
    fill = "#5a4a1e" if dark else "#fdf0d5"
    blue = "#79c0ff" if dark else "#0969da"
    contracts = _contracts(ir)
    deps = dependencies(ir)
    layout = "dot" if len(contracts) <= 6 else "neato"
    lines = [
        f'graph "{esc(ir.get("name", "sos"))}" {{', f"  layout={layout};",
        # top to bottom: the right-hand column is narrow, and a row of boxes shrinks the text
        "  rankdir=TB; nodesep=0.6; ranksep=0.9;" if layout == "dot" else
        '  overlap=false; splines=true; sep="+12";',
        '  bgcolor="transparent";',
        f'  node [shape=box style="rounded,filled" fontname="Helvetica" fontsize=11 color="{ink}" '
        f'fontcolor="{text}" fillcolor="{fill}"];',
        f'  edge [fontname="Helvetica" fontsize=9 color="{ink}" fontcolor="{text}"];',
    ]
    for c in contracts:
        members = ", ".join(_members(c))
        label = f"{esc(c['id'])}\\n{esc(members)}"
        lines.append(f'  "{esc(c["id"])}" [label="{label}"];')
    for d in deps:
        detail = esc(", ".join(d.detail))
        if d.kind == "shared parties":
            lines.append(f'  "{esc(d.source)}" -- "{esc(d.target)}" [label="{detail}" style=dashed];')
        else:
            lines.append(f'  "{esc(d.source)}" -- "{esc(d.target)}" [label="{detail}" dir=forward '
                         f'color="{blue}" fontcolor="{blue}" penwidth=1.8];')
    lines.append("}")
    return "\n".join(lines)


__all__ = ["Promise", "Dependency", "quantities", "amend_deadlines", "coverage", "coverage_findings",
           "dependencies", "assumption_sources", "dependency_findings", "dependency_dot"]
