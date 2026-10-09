"""Structured changes to a CADL design, and proposals built from them.

An AI assistant (or any other client) edits a design by sending a list
of small change operations instead of rewriting the source. Each
proposal is applied to a copy, checked with the full toolchain, and
returned as a summary, a structural diff and the difference in check
results — so it can be reviewed before it touches the design.

Operations (each a mapping with an "op" key):

  set_system         fields
  upsert             section, id, fields        add or update an item
  remove             section, id
  rename             section, id, new_id
  set_lifecycle      contract, fields | preset  replace a contract lifecycle
  upsert_transition  contract, id, fields       one lifecycle transition
  remove_transition  contract, id
  upsert_monitor     contract, id, fields
  remove_monitor     contract, id
  add_contract_from_template  template, id?, parties?

Sections: actors, contracts, protocols, metrics, verification (lists
keyed by `id`), algorithms (mapping keyed by name) and regimes (list
keyed by "FROM->TO"). `fields` is merged into the item: nested mappings
merge key by key, and a value of null removes the key.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.services import design_service as ds

LIST_SECTIONS = {
    "actors": "actors", "contracts": "contracts", "protocols": "protocols",
    "metrics": "metrics", "verification": "verification",
}
SECTIONS = list(LIST_SECTIONS) + ["algorithms", "regimes"]
OPS = [
    "set_system", "upsert", "remove", "rename", "set_lifecycle",
    "upsert_transition", "remove_transition", "upsert_monitor", "remove_monitor",
    "add_contract_from_template",
]
SYSTEM_FIELDS = {"name", "type", "version", "description", "context", "extensions"}
MAX_OPS = 50

# The form section a change belongs to, for review marks and navigation.
_FORM_SECTION = {
    "actors": "Actors", "contracts": "Contracts", "protocols": "Protocols",
    "metrics": "Metrics", "verification": "Verification",
    "algorithms": "Algorithms", "regimes": "Regimes",
}


class OpError(ValueError):
    """A change operation that cannot be applied; the message says why."""


@dataclass
class Change:
    """What one applied operation did, in words, and where."""
    summary: str
    section: str = ""   # form section, e.g. "Contracts"
    item: str = ""


def _merge(target: dict, fields: dict) -> None:
    """Merge ``fields`` into ``target``; None removes a key, mappings merge."""
    for key, value in fields.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
            if not target[key]:
                target.pop(key)
        else:
            target[key] = copy.deepcopy(value)


def _require(op: dict, *keys: str) -> list:
    values = []
    for key in keys:
        if op.get(key) in (None, ""):
            raise OpError(f"`{op.get('op')}` needs `{key}`.")
        values.append(op[key])
    return values


def _fields(op: dict) -> dict:
    fields = op.get("fields")
    if not isinstance(fields, dict):
        raise OpError(f"`{op.get('op')}` needs `fields` as a mapping.")
    return fields


def _items(sos: dict, section: str) -> list:
    if section not in LIST_SECTIONS:
        raise OpError(f"Unknown section `{section}`. Use one of: {', '.join(SECTIONS)}.")
    items = sos.setdefault(LIST_SECTIONS[section], [])
    if not isinstance(items, list):
        raise OpError(f"`{section}` in the design is not a list.")
    return items


def _same_id(a: Any, b: Any, section: str) -> bool:
    # Actors are written `ROBOT[1..N]` but referred to as `ROBOT`.
    if section == "actors":
        return ds.actor_base(str(a)) == ds.actor_base(str(b))
    return str(a) == str(b)


def _find(items: list, item_id: Any, section: str = "") -> Optional[dict]:
    return next(
        (i for i in items if isinstance(i, dict) and _same_id(i.get("id"), item_id, section)), None)


def _contract(sos: dict, contract_id: Any) -> dict:
    contract = _find(_items(sos, "contracts"), contract_id)
    if contract is None:
        known = ", ".join(str(c.get("id")) for c in sos.get("contracts") or []) or "none"
        raise OpError(f"No contract `{contract_id}`. Contracts: {known}.")
    return contract


def _regime_key(text: Any) -> tuple:
    parts = re.split(r"\s*(?:->|→)\s*", str(text).strip())
    if len(parts) != 2 or not all(parts):
        raise OpError("A regime transition id has the form `FROM->TO`.")
    return parts[0], parts[1]


def _replace_everywhere(node: Any, pattern: "re.Pattern", new: str) -> Any:
    """Rewrite every string (and mapping key) in a document."""
    if isinstance(node, str):
        return pattern.sub(new, node)
    if isinstance(node, list):
        return [_replace_everywhere(item, pattern, new) for item in node]
    if isinstance(node, dict):
        return {(_replace_everywhere(k, pattern, new) if isinstance(k, str) else k):
                _replace_everywhere(v, pattern, new) for k, v in node.items()}
    return node


def _rename_references(sos: dict, old: str, new: str) -> None:
    """Rename an identifier wherever the design refers to it, in place."""
    pattern = re.compile(rf"(?<![\w.]){re.escape(old)}(?![\w])")
    updated = _replace_everywhere(sos, pattern, new)
    sos.clear()
    sos.update(updated)


def apply_op(sos: dict, op: dict) -> Change:
    """Apply one operation to the `sos` mapping in place."""
    if not isinstance(op, dict):
        raise OpError("Each change must be a mapping with an `op` key.")
    kind = op.get("op")
    if kind not in OPS:
        raise OpError(f"Unknown op `{kind}`. Use one of: {', '.join(OPS)}.")

    if kind == "set_system":
        fields = _fields(op)
        unknown = set(fields) - SYSTEM_FIELDS
        if unknown:
            raise OpError(
                f"`set_system` cannot set {', '.join(sorted(unknown))}; "
                f"it sets {', '.join(sorted(SYSTEM_FIELDS))}.")
        _merge(sos, fields)
        return Change(f"Set system {', '.join(fields)}", "System")

    if kind == "add_contract_from_template":
        (template,) = _require(op, "template")
        if template not in ds.CONTRACT_TEMPLATES:
            raise OpError(
                f"Unknown template `{template}`. Use one of: {', '.join(ds.CONTRACT_TEMPLATES)}.")
        contract = ds.contract_from_template(sos, template)
        if op.get("id"):
            if _find(_items(sos, "contracts"), op["id"]):
                raise OpError(f"Contract `{op['id']}` already exists.")
            contract["id"] = str(op["id"])
        if op.get("parties"):
            contract["parties"] = [str(p) for p in op["parties"]]
        _items(sos, "contracts").append(contract)
        return Change(f"Added contract {contract['id']} from the {template} template",
                      "Contracts", contract["id"])

    if kind in ("upsert", "remove", "rename"):
        section, item_id = _require(op, "section", "id")
        if section == "algorithms":
            algorithms = sos.setdefault("algorithms", {})
            if not isinstance(algorithms, dict):
                raise OpError("`algorithms` in the design is not a mapping.")
            name = str(item_id)
            if kind == "remove":
                if name not in algorithms:
                    raise OpError(f"No algorithm `{name}`.")
                del algorithms[name]
                if not algorithms:
                    sos.pop("algorithms")
                return Change(f"Removed algorithm {name}", "Algorithms", name)
            if kind == "rename":
                (new_id,) = _require(op, "new_id")
                if name not in algorithms:
                    raise OpError(f"No algorithm `{name}`.")
                sos["algorithms"] = {
                    (str(new_id) if k == name else k): v for k, v in algorithms.items()}
                return Change(f"Renamed algorithm {name} to {new_id}", "Algorithms", str(new_id))
            existed = name in algorithms
            entry = algorithms.setdefault(name, {})
            _merge(entry, _fields(op))
            return Change(f"{'Updated' if existed else 'Added'} algorithm {name}", "Algorithms", name)

        if section == "regimes":
            source, target = _regime_key(item_id)
            transitions = sos.setdefault("transitions", [])
            found = next((t for t in transitions if isinstance(t, dict)
                          and str(t.get("from")) == source and str(t.get("to")) == target), None)
            label = f"{source} → {target}"
            if kind == "rename":
                raise OpError("Regime transitions cannot be renamed; remove and add instead.")
            if kind == "remove":
                if found is None:
                    raise OpError(f"No regime transition {label}.")
                transitions.remove(found)
                if not transitions:
                    sos.pop("transitions")
                return Change(f"Removed regime transition {label}", "Regimes")
            if found is None:
                found = {"from": source, "to": target}
                transitions.append(found)
                verb = "Added"
            else:
                verb = "Updated"
            fields = {k: v for k, v in _fields(op).items() if k not in ("from", "to")}
            _merge(found, fields)
            return Change(f"{verb} regime transition {label}", "Regimes")

        items = _items(sos, section)
        found = _find(items, item_id, section)
        noun = section[:-1] if section.endswith("s") else section
        form = _FORM_SECTION[section]
        if kind == "remove":
            if found is None:
                raise OpError(f"No {noun} `{item_id}` to remove.")
            items.remove(found)
            if not items:
                sos.pop(LIST_SECTIONS[section])
            return Change(f"Removed {noun} {item_id}", form, str(item_id))
        if kind == "rename":
            (new_id,) = _require(op, "new_id")
            if found is None:
                raise OpError(f"No {noun} `{item_id}` to rename.")
            if _find(items, new_id, section):
                raise OpError(f"A {noun} `{new_id}` already exists.")
            old_id = str(found["id"])
            if section == "actors":
                # Parties, decision holders, expressions and protocol steps
                # all name the actor; rename it everywhere, keeping `[1..N]`.
                old_base, new_base = ds.actor_base(old_id), ds.actor_base(str(new_id))
                _rename_references(sos, old_base, new_base)
                return Change(
                    f"Renamed actor {old_base} to {new_base} everywhere it is referred to",
                    form, new_base)
            if section in ("contracts", "protocols"):
                found["id"] = str(new_id)
                # Regime transitions and verification directives refer to these by id.
                for t in sos.get("transitions") or []:
                    if isinstance(t, dict) and section == "protocols" and str(t.get("protocol")) == old_id:
                        t["protocol"] = str(new_id)
                for v in sos.get("verification") or []:
                    if isinstance(v, dict) and str(v.get("target")) == old_id:
                        v["target"] = str(new_id)
                return Change(f"Renamed {noun} {old_id} to {new_id}, with references to it",
                              form, str(new_id))
            found["id"] = str(new_id)
            return Change(f"Renamed {noun} {old_id} to {new_id}", form, str(new_id))
        fields = {k: v for k, v in _fields(op).items() if k != "id"}
        if found is None:
            found = {"id": str(item_id)}
            items.append(found)
            verb = "Added"
        else:
            verb = "Updated"
        _merge(found, fields)
        detail = f" ({', '.join(fields)})" if fields and verb == "Updated" else ""
        return Change(f"{verb} {noun} {found['id']}{detail}", form,
                      ds.actor_base(found["id"]) if section == "actors" else str(found["id"]))

    # Operations inside one contract.
    (contract_id,) = _require(op, "contract")
    contract = _contract(sos, contract_id)
    cid = str(contract.get("id"))

    if kind == "set_lifecycle":
        if op.get("preset"):
            if op["preset"] not in ds.LIFECYCLE_PRESETS:
                raise OpError(
                    f"Unknown lifecycle preset `{op['preset']}`. "
                    f"Use one of: {', '.join(ds.LIFECYCLE_PRESETS)}.")
            contract["lifecycle"] = copy.deepcopy(ds.LIFECYCLE_PRESETS[op["preset"]])
            return Change(f"Set the lifecycle of {cid} to the preset {op['preset']}", "Contracts", cid)
        lifecycle = contract.setdefault("lifecycle", {})
        _merge(lifecycle, _fields(op))
        if not lifecycle:
            contract.pop("lifecycle")
        return Change(f"Changed the lifecycle of {cid}", "Contracts", cid)

    (item_id,) = _require(op, "id")
    if kind in ("upsert_transition", "remove_transition"):
        container = contract.setdefault("lifecycle", {}).setdefault("transitions", [])
        noun = "transition"
    else:
        container = contract.setdefault("monitors", [])
        noun = "monitor"
    found = _find(container, item_id)
    if kind.startswith("remove"):
        if found is None:
            raise OpError(f"Contract `{cid}` has no {noun} `{item_id}`.")
        container.remove(found)
        if noun == "monitor" and not container:
            contract.pop("monitors")
        return Change(f"Removed {noun} {item_id} from {cid}", "Contracts", cid)
    fields = {k: v for k, v in _fields(op).items() if k != "id"}
    verb = "Updated" if found is not None else "Added"
    if found is None:
        found = {"id": str(item_id)}
        container.append(found)
    _merge(found, fields)
    return Change(f"{verb} {noun} {item_id} in {cid}", "Contracts", cid)


def apply_ops(source: str, ops: List[dict]) -> tuple:
    """Apply operations to a source text: (new source, changes).

    Raises OpError naming the operation that failed; nothing is applied
    partially because the work is done on a copy.
    """
    if not isinstance(ops, list) or not ops:
        raise OpError("`changes` must be a non-empty list of operations.")
    if len(ops) > MAX_OPS:
        raise OpError(f"Too many operations in one proposal ({len(ops)}; limit {MAX_OPS}).")
    doc = ds.load_doc(source)
    changes = []
    for index, op in enumerate(ops, 1):
        try:
            changes.append(apply_op(doc["sos"], op))
        except OpError as e:
            raise OpError(f"Change {index}: {e}") from e
    return ds.dump_doc(doc), changes


# ── Proposals ───────────────────────────────────────────────────────


def _problem_keys(analysis: ds.DesignAnalysis) -> Dict[tuple, ds.Finding]:
    return {
        (f.stage, f.title, f.message): f
        for f in analysis.findings if f.level in ("error", "warning")
    }


@dataclass
class Proposal:
    """A set of changes applied to a copy of the design, with its consequences."""
    source: str                              # the design after the changes
    changes: List[Change] = field(default_factory=list)
    diff: Dict[str, List[str]] = field(default_factory=dict)   # structural, by topic
    introduced: List[ds.Finding] = field(default_factory=list)  # new errors / warnings
    resolved: List[ds.Finding] = field(default_factory=list)    # problems this fixes
    errors_after: int = 0
    warnings_after: int = 0
    parses: bool = True
    # Contracts this proposal touches, restated in plain sentences as they
    # would read after it — so it can be confirmed before it is applied.
    readback: Dict[str, List[str]] = field(default_factory=dict)

    @property
    def acceptable(self) -> bool:
        """True when the result parses and adds no new error."""
        return self.parses and not any(f.level == "error" for f in self.introduced)


def propose(source: str, ops: List[dict]) -> Proposal:
    """Apply ``ops`` to a copy of ``source`` and report what would change."""
    before = ds.analyze(source)
    new_source, changes = apply_ops(source, ops)
    after = ds.analyze(new_source)
    keys_before, keys_after = _problem_keys(before), _problem_keys(after)
    from backend.services.design_readback import describe_contract  # avoids an import cycle

    touched = {c.item for c in changes if c.section == "Contracts" and c.item}
    renamed_actor = any(c.summary.startswith("Renamed actor") for c in changes)
    readback = {
        str(c.get("id")): describe_contract(c)
        for c in ((after.ir or {}).get("institution") or {}).get("contracts") or []
        if renamed_actor or str(c.get("id")) in touched
    }
    return Proposal(
        source=new_source,
        changes=changes,
        diff={topic: items for topic, items in
              (ds.diff_designs(before.ir, after.ir) if before.parsed and after.parsed else {}).items()
              if items},
        introduced=[f for key, f in keys_after.items() if key not in keys_before],
        resolved=[f for key, f in keys_before.items() if key not in keys_after],
        errors_after=after.count("error"),
        warnings_after=after.count("warning"),
        parses=after.parsed,
        readback=readback,
    )


# ── Open questions: what the design does not say yet ────────────────


@dataclass
class Question:
    """Something to ask the designer, and why."""
    question: str
    why: str
    section: str = ""
    item: str = ""
    priority: int = 2   # 1 = blocks a sound design, 2 = gap, 3 = refinement


def open_questions(source: str) -> List[Question]:
    """Questions that would close the gaps in a design, most important first.

    Derived from the checks and from what the design leaves empty, so an
    assistant can interview the designer instead of guessing.
    """
    analysis = ds.analyze(source)
    if not analysis.parsed:
        return [Question(
            "The design does not parse yet. What should it contain?",
            analysis.findings[0].message if analysis.findings else "", priority=1)]

    questions: List[Question] = []
    institution = analysis.ir.get("institution") or {}
    actors = institution.get("actors") or []
    contracts = institution.get("contracts") or []
    protocols = (analysis.ir.get("protocol") or {}).get("protocols") or []

    if not analysis.sos_type:
        questions.append(Question(
            "How is the system governed as a whole? Directed (a central authority can command "
            "the others), Acknowledged (a central authority exists but the parts stay "
            "independent), Collaborative (the parts agree among themselves) or Virtual (no "
            "central authority or agreed purpose)?",
            "The design has no SoS type.", "System", priority=1))
    if not (analysis.ir.get("description") or "").strip():
        questions.append(Question(
            "In one sentence, what is this system of systems for?",
            "The design has no description.", "System", priority=3))
    if not actors:
        questions.append(Question(
            "Which systems or organisations take part, and what does each one do?",
            "The design has no actors.", "Actors", priority=1))
    if actors and not contracts:
        questions.append(Question(
            "What do the actors promise each other? Name one agreement and who is bound by it.",
            "The design has no contracts.", "Contracts", priority=1))

    for f in analysis.findings:
        if f.level == "error":
            questions.append(Question(
                f"How should this be resolved: {f.title}?", f.message or f.title,
                f.section, f.item, 1))
            continue
        if f.level not in ("warning", "info"):
            continue
        title = f.title
        if "is not a party to any contract" in title:
            questions.append(Question(
                f"What is {f.item} obliged to do, and to whom? Which contract should bind it?",
                "It appears in no contract, so nothing constrains it.", "Actors", f.item, 2))
        elif "names no decision holder" in title:
            questions.append(Question(
                f"Under {f.item}, who has the final say when the parties disagree?",
                "The contract names no decision holder.", "Contracts", f.item, 2))
        elif "decision holder is not a party" in title:
            questions.append(Question(
                f"Should the decision holder of {f.item} also be bound by it?",
                f.message, "Contracts", f.item, 2))
        elif "guarantees quantities no metric measures" in title:
            questions.append(Question(
                f"How will you measure what {f.item} guarantees ({f.message.split(' — ')[0]})?",
                "No metric tracks these quantities, so a breach could go unnoticed.",
                "Metrics", "", 3))
        elif "has no target" in title:
            questions.append(Question(
                f"What value of the metric {f.item} is acceptable?",
                "The metric has no target.", "Metrics", f.item, 3))
        elif "cannot be reached" in title or "cannot be reached" in f.message:
            questions.append(Question(
                f"In {f.item}, what event leads to the state {f.element}? Or is the state unnecessary?",
                f.message, "Contracts", f.item, 2))
        elif "has no way out" in f.message:
            questions.append(Question(
                f"In {f.item}, what can happen once an instance is in {f.element}?",
                f.message, "Contracts", f.item, 2))
        elif "does not declare" in title:
            questions.append(Question(
                f"Should {f.element} be added to the actor's interface, or is the step wrong?",
                f.message, "Protocols", f.item, 2))
        elif "may outlast a guarantee" in title or "weaker than contract" in title \
                or "contradicts contract" in title:
            questions.append(Question(
                f"Which number is right? {f.message}", f.title, f.section, f.item, 2))
        elif "takes part in no protocol" in title:
            questions.append(Question(
                f"How does {f.item} interact with the others? Which messages does it send or receive?",
                "It takes part in no protocol.", "Protocols", "", 3))
        elif f.stage == "Expressions":
            questions.append(Question(
                f"What condition did you mean by this? {f.message.split(' — ')[0]}",
                "It cannot be read as an expression, so it is not verified.",
                f.section, f.item, 2))

    for c in contracts:
        cid = str(c.get("id"))
        if not c.get("guarantee"):
            questions.append(Question(
                f"What exactly does {cid} guarantee? Give a measurable condition.",
                "The contract has no guarantee.", "Contracts", cid, 1))
        if not c.get("assume"):
            questions.append(Question(
                f"Under what conditions does {cid} apply? What must be true for the guarantee to be owed?",
                "The contract has no assumptions, so it promises unconditionally.",
                "Contracts", cid, 3))
        if not c.get("violation_detect"):
            questions.append(Question(
                f"How would a breach of {cid} be noticed, and what happens then?",
                "The contract does not say how a violation is detected.", "Contracts", cid, 2))
        if not c.get("lifecycle"):
            questions.append(Question(
                f"What stages does one instance of {cid} go through, from start to finish?",
                "The contract has no lifecycle.", "Contracts", cid, 3))

    if contracts and not protocols:
        questions.append(Question(
            "How do the actors carry out their agreements? Describe one exchange step by step.",
            "The design has no protocols.", "Protocols", priority=3))
    if contracts and not analysis.ir.get("metrics"):
        questions.append(Question(
            "How will you tell whether the system as a whole is doing well?",
            "The design has no metrics.", "Metrics", priority=3))

    seen = set()
    unique = []
    for q in sorted(questions, key=lambda q: q.priority):
        if q.question not in seen:
            seen.add(q.question)
            unique.append(q)
    return unique
