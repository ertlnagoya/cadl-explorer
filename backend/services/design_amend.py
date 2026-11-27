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
# A `decided_by:` naming one of these leaves the decision to the council's vote
# (cadl-robot-town, the `rule` layer of CADL's amendment extension); any other
# name is a person or a role, who decides the change themself.
COUNCILS = ("COUNCIL",)


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


def decider(spec: Optional[dict]) -> Optional[str]:
    """The party the constitution names in ``decided_by:``, or None when it names none."""
    who = (spec or {}).get("decided_by")
    return str(who).strip() or None if who is not None else None


def needs_human(spec: dict) -> bool:
    """Contract and regulation layers, safety-related rules, and values whose
    ``decided_by:`` names someone other than the council, are decided by a person."""
    who = decider(spec)
    return (str(spec.get("layer", "rule")) in LAYERS_BY_HUMAN or bool(spec.get("safety"))
            or (who is not None and who.upper() not in COUNCILS))


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
    """The lines with each tagged number masked, to see whether anything else changed.

    The lines that declare the version (``sos.version``, ``amendment.previous``,
    ``amendment.effective`` and the ``value:`` of each declared parameter) are
    left out: :func:`declare` changes them with the values.
    """
    out = []
    skip = set(_declaration_lines(source).values())
    for i, line in enumerate(str(source).splitlines()):
        if i in skip:
            continue
        m = TAG.search(line)
        if m:
            line = NUM.sub("<v>", line[:m.start()], count=1).rstrip() + " " + line[m.start():]
        out.append(line.rstrip())
    while out and not out[-1]:
        out.pop()
    return out


# ── The declaration of the version (CADL's amendment extension, Appendix K) ──

_KEY = re.compile(r"^(\s*)(-\s+)?([A-Za-z_]\w*)\s*:(.*)$")


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _content(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _declaration_lines(source: str) -> Dict[str, int]:
    """Where the version is declared: 0-based line numbers, keyed by
    ``version``, ``amendment``, ``previous``, ``effective``, ``effective.period``
    (a block-form ``effective:``) and ``value:<id>`` for each declared parameter.
    Only the lines directly under ``sos:`` and its ``amendment:`` count."""
    lines = str(source).splitlines()
    found: Dict[str, int] = {}
    sos_at = next((i for i, l in enumerate(lines) if re.match(r"^sos\s*:\s*(#.*)?$", l)), None)
    if sos_at is None:
        return found
    child = next((_indent(l) for l in lines[sos_at + 1:] if _content(l)), None)
    if not child:
        return found
    amend_at, sub, section, param, param_indent = None, None, None, None, None
    for i in range(sos_at + 1, len(lines)):
        line = lines[i]
        if not _content(line):
            continue
        ind = _indent(line)
        if ind < child:
            break
        m = _KEY.match(line)
        if ind == child:
            amend_at = None
            if m and not m.group(2) and m.group(3) == "version":
                found["version"] = i
            elif m and not m.group(2) and m.group(3) == "amendment":
                amend_at, sub, section, param = i, None, None, None
                found["amendment"] = i
            continue
        if amend_at is None or not m:
            continue
        if sub is None:
            sub = ind
        if ind == sub and not m.group(2):
            section, param = m.group(3), None
            if section in ("previous", "effective"):
                found[section] = i
            continue
        if section == "effective" and ind > sub and m.group(3) == "period":
            found["effective.period"] = i
        elif section == "parameters" and m.group(2) and m.group(3) == "id":
            param, param_indent = m.group(4).split("#")[0].strip().strip("\"'"), ind + len(m.group(2))
        elif section == "parameters" and param and not m.group(2) and m.group(3) == "value" \
                and ind == param_indent:
            found[f"value:{param}"] = i
    return found


def declared_version(source: str) -> Optional[dict]:
    """``{"version", "previous", "effective"}`` of a design that declares the amendment
    extension (an ``amendment:`` block), or None."""
    try:
        doc = yaml.safe_load(source or "") or {}
    except yaml.YAMLError:
        return None
    sos = doc.get("sos") if isinstance(doc, dict) else None
    if not isinstance(sos, dict) or not isinstance(sos.get("amendment"), dict):
        return None
    block = sos["amendment"]
    eff = block.get("effective")
    return {"version": None if sos.get("version") is None else str(sos["version"]),
            "previous": None if block.get("previous") is None else str(block["previous"]),
            "effective": eff if isinstance(eff, dict) else None,
            "parameters": [str(p.get("id")) for p in block.get("parameters") or []
                           if isinstance(p, dict) and p.get("id") is not None]}


def next_version(version: Optional[str]) -> str:
    """The version after ``version``: the last number goes up by one (``2.1`` → ``2.2``,
    ``v3`` → ``v4``), and a version of one number gets ``.1`` (``2`` → ``2.1``), as the
    councils of care-night-sos and cadl-robot-town number their amendments."""
    text = str(version or "").strip()
    if not text:
        return "1"
    if re.fullmatch(r"\d+", text):
        return text + ".1"
    m = re.search(r"(\d+)(\D*)$", text)
    if m is None:
        return text + ".1"
    return text[:m.start(1)] + str(int(m.group(1)) + 1) + m.group(2)


def _scalar(old_rest: str, text: str) -> str:
    """``text`` in place of the scalar in ``key:<old_rest>``, keeping the spacing, the
    comment and the old quotes. An unquoted old scalar stays unquoted only when ``text``
    is a whole number; otherwise it is quoted, since ``2.10`` unquoted would read as 2.1."""
    body, sep, comment = old_rest.partition("#")
    old = body.strip()
    lead = body[:len(body) - len(body.lstrip())] or " "
    gap = body[len(body.rstrip()):] if sep else ""
    q = old[0] if old[:1] in ("'", '"') else ("" if re.fullmatch(r"\d+", text) else '"')
    return f"{lead}{q}{text}{q}{gap}{sep}{comment}"


def declare(source: str, new: Dict[str, float], version: Optional[str] = None,
            previous: Optional[str] = None, effective_period: Optional[int] = None) -> str:
    """Update the declaration of a version to go with ``set_values(source, new)``.

    In a design with an ``amendment:`` block (CADL's amendment extension), the
    ``value:`` of each declared parameter in ``new`` becomes its new value (in
    the old one's unit and form), and, when ``version`` is given, ``sos.version``
    becomes ``version``, ``amendment.previous`` becomes ``previous`` (inserted
    when missing) and ``amendment.effective.period`` becomes ``effective_period``
    when one is given. The rest of the text, comments included, stays as it is,
    so ``cadl amend-diff old new`` reads it as the next version. A design without
    the block is returned unchanged.
    """
    if declared_version(source) is None:
        return source
    lines = str(source).splitlines(keepends=True)
    where = _declaration_lines(source)

    def put(i: int, text: str) -> None:
        line = lines[i]
        end = line[len(line.rstrip("\r\n")):]
        m = _KEY.match(line.rstrip("\r\n"))
        lines[i] = line[:len(line.rstrip("\r\n")) - len(m.group(4))] + text + end

    for name, value in new.items():
        i = where.get(f"value:{name}")
        if i is None:
            continue
        rest = _KEY.match(lines[i].rstrip("\r\n")).group(4)
        n = NUM.search(rest.split("#")[0])
        if n is None:
            continue
        put(i, rest[:n.start()] + _plain(float(value), keep_point="." in n.group()) + rest[n.end():])
    if version is not None:
        if "version" in where:
            put(where["version"], _scalar(_KEY.match(lines[where["version"]].rstrip("\r\n")).group(4),
                                          str(version)))
        if previous is not None:
            if "previous" in where:
                put(where["previous"], _scalar(_KEY.match(lines[where["previous"]].rstrip("\r\n")).group(4),
                                               str(previous)))
            else:
                at = where["amendment"]
                below = next((l for l in lines[at + 1:] if _content(l)), "")
                ind = " " * (_indent(below) if _indent(below) > _indent(lines[at]) else _indent(lines[at]) + 2)
                lines.insert(at + 1, f'{ind}previous: "{previous}"\n')
                where = _declaration_lines("".join(lines))
        if effective_period is not None:
            i = where.get("effective.period")
            if i is None and "effective" in where:
                i = where["effective"]
                rest = _KEY.match(lines[i].rstrip("\r\n")).group(4)
                new_rest, n = re.subn(r"(period\s*:\s*)\d+", lambda m: m.group(1) + str(int(effective_period)),
                                      rest, count=1)
                if n:
                    put(i, new_rest)
                else:
                    raise ValueError("amendment.effective does not give a period, so its period "
                                     "cannot be changed.")
            elif i is not None:
                rest = _KEY.match(lines[i].rstrip("\r\n")).group(4)
                put(i, re.sub(r"\d+", str(int(effective_period)), rest, count=1))
            else:
                at = where.get("previous", where["amendment"])
                ind = " " * _indent(lines[at]) if at != where["amendment"] else \
                    " " * (_indent(lines[at]) + 2)
                lines.insert(at + 1, f"{ind}effective: {{ period: {int(effective_period)} }}\n")
    return "".join(lines)


@dataclass
class Gate:
    """The constitution's verdict on going from one text to another."""
    ok: bool
    reasons: List[str]
    changed: List[Tuple[str, Optional[float], Optional[float]]]
    for_a_person: List[str]   # changed names whose layer, safety or decided_by needs a person
    decided_by: Dict[str, str] = field(default_factory=dict)   # changed name → the party named


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
    deciders = {n: decider(constitution.get(n)) for n, _, _ in changed
                if constitution is not None and decider(constitution.get(n))}
    return Gate(not reasons, reasons, changed, for_a_person, deciders)


def layer_of(spec: Optional[dict]) -> str:
    if not spec:
        return "—"
    layer = str(spec.get("layer", "rule"))
    return (layer + (", safety" if spec.get("safety") else "")
            + (f", decided by {decider(spec)}" if decider(spec) else ""))


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


def steps(name: str, moved: float, constitution: Optional[dict]) -> Optional[float]:
    """``moved`` in steps of the constitution's ``step`` for ``name``; None without one."""
    step = _num((constitution or {}).get(name) or {}, "step")
    return abs(moved) / abs(step) if step else None


def movement(versions: List[Tuple[str, str]], constitution: Optional[dict] = None) -> List[Dict[str, str]]:
    """How each value moved across the versions, as Course D's Exercise 12-1 tabulates it.

    One row per name that changed: the number of amendments, the cumulative
    amount moved, that amount and the largest single move in steps of the
    constitution (the original ``step`` when it is the constitution before the
    step was changed), and the reversals — moves opposite to the one before.
    """
    series: Dict[str, List[float]] = {}
    for _, source in versions:
        for n, v in values(source).items():
            series.setdefault(n, []).append(v)
    rows = []
    for n, vals in series.items():
        moves = [b - a for a, b in zip(vals, vals[1:]) if abs(b - a) > 1e-9]
        if not moves:
            continue
        total = sum(abs(m) for m in moves)
        cumulative, largest = steps(n, total, constitution), steps(n, max(map(abs, moves)), constitution)
        rows.append({
            "parameter": n, "from → to": f"{fmt(vals[0])} → {fmt(vals[-1])}",
            "amendments": str(len(moves)), "moved in all": fmt(total),
            "steps in all": "—" if cumulative is None else fmt(round(cumulative, 3)),
            "largest move (steps)": "—" if largest is None else fmt(round(largest, 3)),
            "reversals": str(sum(1 for a, b in zip(moves, moves[1:]) if (a > 0) != (b > 0))),
        })
    return rows


__all__ = ["Amendable", "Occurrence", "Gate", "amendables", "values", "load_constitution",
           "findings", "set_values", "check_amendment", "needs_human", "decider", "declare",
           "declared_version", "next_version", "layer_of", "history",
           "movement", "steps", "fmt"]
