"""Amendable parameters: the values a design marks ``# @amend:<name>``.

Robot Town (Course D) marks the numbers a council or a person may change
with a comment, and a constitution (``constitution.yaml``) says, for each
name, its layer (rule, contract), whether it is safety-related, and its
range and step. The rules here are those of ``cadl-robot-town``
(``town/council.py``):

- the value of a tagged line is the first number before the comment;
- every line with the same name must hold the same value;
- an amendment changes only those numbers, keeps the text and comments,
  and moves a value within the range by whole steps.

The Explorer's form and proposal edits rewrite the YAML without comments;
``set_values`` changes the numbers in place instead, so the tags survive.
"""

from dataclasses import dataclass, field
import re
from typing import Dict, List, Optional, Tuple

import yaml

TAG = re.compile(r"#\s*@amend:(\w+)")          # as in cadl-robot-town: no space after the colon
NUM = re.compile(r"\d+(?:\.\d+)?")
LAYERS_BY_HUMAN = ("contract", "regulation")


@dataclass
class Occurrence:
    line: int                 # 1-based
    text: str                 # the line before the comment, stripped
    value: Optional[float]    # None when the line has no number before the comment


@dataclass
class Amendable:
    name: str
    occurrences: List[Occurrence] = field(default_factory=list)

    @property
    def values(self) -> List[float]:
        return [o.value for o in self.occurrences if o.value is not None]

    @property
    def value(self) -> Optional[float]:
        """The value, as cadl-robot-town reads it: that of the last line."""
        return self.values[-1] if self.values else None

    @property
    def consistent(self) -> bool:
        return len(set(self.values)) <= 1 and all(o.value is not None for o in self.occurrences)


def amendables(source: str) -> List[Amendable]:
    """Every ``# @amend:`` name in the order of first appearance, with its lines."""
    found: Dict[str, Amendable] = {}
    for i, line in enumerate(str(source or "").splitlines(), 1):
        m = TAG.search(line)
        if not m:
            continue
        body = line[:m.start()]
        n = NUM.search(body)
        found.setdefault(m.group(1), Amendable(m.group(1))).occurrences.append(
            Occurrence(i, body.strip(), float(n.group()) if n else None))
    return list(found.values())


def values(source: str) -> Dict[str, float]:
    return {a.name: a.value for a in amendables(source) if a.value is not None}


def fmt(value: Optional[float]) -> str:
    return "—" if value is None else f"{value:g}"


def load_constitution(text: str) -> dict:
    """The ``amendable`` table of a constitution; raises ValueError when it cannot be read."""
    try:
        doc = yaml.safe_load(text or "") or {}
    except yaml.YAMLError as e:
        raise ValueError(f"The constitution is not valid YAML: {str(e).splitlines()[0]}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("amendable"), dict):
        raise ValueError("The constitution has no `amendable:` table.")
    out = {}
    for name, spec in doc["amendable"].items():
        if isinstance(spec, dict):
            out[str(name)] = spec
    return out


def _num(spec: dict, key: str) -> Optional[float]:
    try:
        return float(spec[key])
    except (KeyError, TypeError, ValueError):
        return None


def on_grid(value: float, spec: dict) -> bool:
    lo, step = _num(spec, "min"), _num(spec, "step")
    if lo is None or not step:
        return True
    k = (value - lo) / step
    return abs(k - round(k)) < 1e-6


def in_range(value: float, spec: dict) -> bool:
    lo, hi = _num(spec, "min"), _num(spec, "max")
    return (lo is None or value >= lo - 1e-9) and (hi is None or value <= hi + 1e-9)


def needs_human(spec: dict) -> bool:
    """Contract and regulation layers, and safety-related rules, are decided by a person."""
    return str(spec.get("layer", "rule")) in LAYERS_BY_HUMAN or bool(spec.get("safety"))


def findings(source: str, constitution: Optional[dict] = None) -> List[str]:
    """What is wrong with the tagged values, alone and against a constitution."""
    notes = []
    params = amendables(source)
    for a in params:
        missing = [o.line for o in a.occurrences if o.value is None]
        if missing:
            notes.append(f"`{a.name}`: line {', '.join(map(str, missing))} has no number before "
                         "the `# @amend:` comment, so nothing there can be amended.")
        if len(set(a.values)) > 1:
            where = ", ".join(f"{fmt(o.value)} (line {o.line})" for o in a.occurrences
                              if o.value is not None)
            notes.append(f"`{a.name}` has different values on its lines: {where}. Every line with "
                         "the same name must hold the same value.")
    if constitution is not None:
        names = {a.name for a in params}
        for a in params:
            spec = constitution.get(a.name)
            if spec is None:
                notes.append(f"`{a.name}` is marked amendable but the constitution does not list it, "
                             "so no amendment of it can pass.")
                continue
            if a.value is not None and not in_range(a.value, spec):
                notes.append(f"`{a.name}` = {fmt(a.value)} is outside the constitution's range "
                             f"{fmt(_num(spec, 'min'))}–{fmt(_num(spec, 'max'))}.")
            elif a.value is not None and not on_grid(a.value, spec):
                notes.append(f"`{a.name}` = {fmt(a.value)} is not a whole number of steps "
                             f"({fmt(_num(spec, 'step'))}) from {fmt(_num(spec, 'min'))}.")
        for name in constitution:
            if name not in names:
                notes.append(f"The constitution lists `{name}`, which no line of the design marks "
                             "`# @amend:`.")
    return notes


def _plain(value: float, keep_point: bool = False) -> str:
    """A number as CADL writes it: no exponent (1000000, not 1e+06); ``.0`` kept if asked."""
    if value < 0:
        raise ValueError("An amendable value is written without a sign, so it cannot be negative.")
    text = f"{value:.10f}".rstrip("0").rstrip(".")
    if keep_point and "." not in text:
        text += ".0"
    return text


def set_values(source: str, new: Dict[str, float]) -> str:
    """Write ``new`` into every tagged line of each name, keeping the rest of the text.

    As in cadl-robot-town, the first number before the comment is replaced;
    a value is written without a decimal point unless the old one had one.
    """
    unknown = [n for n in new if n not in {a.name for a in amendables(source)}]
    if unknown:
        raise ValueError(f"No line is marked `# @amend:{unknown[0]}`.")
    out = []
    for line in str(source).splitlines(keepends=True):
        m = TAG.search(line)
        if m and m.group(1) in new:
            body = line[:m.start()]
            n = NUM.search(body)
            if n is None:
                raise ValueError(f"A line marked `# @amend:{m.group(1)}` has no number to change.")
            text = _plain(float(new[m.group(1)]), keep_point="." in n.group())
            line = body[:n.start()] + text + body[n.end():] + line[m.start():]
        out.append(line)
    return "".join(out)


def _masked(source: str) -> List[str]:
    """The lines with each tagged number masked, to see whether anything else changed."""
    out = []
    for line in str(source).splitlines():
        m = TAG.search(line)
        if m:
            line = NUM.sub("<v>", line[:m.start()], count=1).rstrip() + " " + line[m.start():]
        out.append(line.rstrip())
    while out and not out[-1]:
        out.pop()
    return out


@dataclass
class Gate:
    """The constitution's verdict on going from one text to another."""
    ok: bool
    reasons: List[str]
    changed: List[Tuple[str, Optional[float], Optional[float]]]
    for_a_person: List[str]   # changed names whose layer, or safety, needs a person's decision


def check_amendment(before: str, after: str, constitution: Optional[dict] = None,
                    one_step: bool = True) -> Gate:
    """Whether ``after`` amends ``before`` as the constitution allows.

    Only tagged numbers may change; each line of a name must agree; with a
    constitution, each changed value must stay in its range and (with
    ``one_step``) only one name may change, by exactly one step, as the
    Course D constitution allows.
    """
    reasons = []
    if _masked(before) != _masked(after):
        reasons.append("Something other than the numbers on `# @amend:` lines changed.")
    for a in amendables(after):
        if len(set(a.values)) > 1:
            reasons.append(f"The lines of `{a.name}` hold different values "
                           f"({', '.join(fmt(v) for v in a.values)}).")
    old, new = values(before), values(after)
    changed = [(k, old.get(k), new.get(k)) for k in sorted(set(old) | set(new))
               if old.get(k) is None or new.get(k) is None or abs(old[k] - new[k]) > 1e-9]
    if not changed:
        reasons.append("No amendable value changed.")
    elif one_step and constitution is not None and len(changed) > 1:
        reasons.append(f"{len(changed)} values change at once ({', '.join(c[0] for c in changed)}); "
                       "the constitution allows one per amendment.")
    for_a_person = []
    if constitution is not None:
        for name, a, b in changed:
            spec = constitution.get(name)
            if spec is None:
                reasons.append(f"The constitution does not let `{name}` be amended.")
                continue
            if b is not None and not in_range(b, spec):
                reasons.append(f"`{name}` = {fmt(b)} is outside {fmt(_num(spec, 'min'))}–"
                               f"{fmt(_num(spec, 'max'))}.")
            step = _num(spec, "step")
            if one_step and step and a is not None and b is not None and abs(abs(b - a) - step) > 1e-9:
                reasons.append(f"`{name}` moves by {fmt(abs(b - a))}; the constitution allows "
                               f"{fmt(step)} at a time.")
            if needs_human(spec):
                for_a_person.append(name)
    return Gate(not reasons, reasons, changed, for_a_person)


def layer_of(spec: Optional[dict]) -> str:
    if not spec:
        return "—"
    layer = str(spec.get("layer", "rule"))
    return layer + (", safety" if spec.get("safety") else "")


def history(versions: List[Tuple[str, str]]) -> List[Dict[str, str]]:
    """One row per amendable name, one column per version (label, source)."""
    names: List[str] = []
    per = []
    for label, source in versions:
        vals = values(source)
        per.append((label, vals))
        for n in vals:
            if n not in names:
                names.append(n)
    rows = []
    for n in names:
        row = {"parameter": n}
        previous = None
        for label, vals in per:
            v = vals.get(n)
            mark = " ←" if previous is not None and v is not None and abs(v - previous) > 1e-9 else ""
            row[label] = fmt(v) + mark
            previous = v if v is not None else previous
        rows.append(row)
    return rows


__all__ = ["Amendable", "Occurrence", "Gate", "amendables", "values", "load_constitution",
           "findings", "set_values", "check_amendment", "needs_human", "layer_of", "history", "fmt"]
