"""Read a CADL design back in plain language.

Checks say whether a design is consistent; they cannot say whether it
is what the designer meant. These functions restate the design so a
person can confirm it: what each contract says, what each actor is
bound to, and what can happen to a contract instance step by step.

Everything here is derived from the simulator IR by fixed rules — no
language model is involved — so the same design always reads the same.
"""

from __future__ import annotations

from typing import Dict, List, Optional

from backend.services import design_service as ds

MAX_STORIES = 12
MAX_STEPS = 12


def _join(items: list, last: str = "and") -> str:
    items = [str(i) for i in items if str(i)]
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + f" {last} " + items[-1]


def _level(value, low: str, mid: str, high: str) -> Optional[str]:
    if not isinstance(value, (int, float)):
        return None
    return low if value < 0.34 else mid if value < 0.67 else high


def _contracts(analysis: ds.DesignAnalysis) -> list:
    return ((analysis.ir or {}).get("institution") or {}).get("contracts") or []


def _find_contract(analysis: ds.DesignAnalysis, contract_id: str) -> dict:
    for c in _contracts(analysis):
        if str(c.get("id")) == str(contract_id):
            return c
    known = ", ".join(str(c.get("id")) for c in _contracts(analysis)) or "none"
    raise ValueError(f"No contract `{contract_id}`. Contracts: {known}.")


# ── Contracts in words ──────────────────────────────────────────────


def describe_contract(contract: dict) -> List[str]:
    """One contract of the IR as a few plain sentences."""
    cid = contract.get("id", "?")
    parties = [str(p) for p in contract.get("parties") or []]
    gov = contract.get("governance") or {}
    lines = [f"{cid} is an agreement between {_join(parties) or 'no named parties'}."]

    assume = [f"`{x}`" for x in contract.get("assume") or []]
    guarantee = [f"`{x}`" for x in contract.get("guarantee") or []]
    if guarantee:
        lines.append(
            (f"As long as {_join(assume)} holds, " if assume else "Unconditionally, ")
            + f"the parties must ensure {_join(guarantee)}.")
    else:
        lines.append("It does not yet say what the parties must ensure.")

    holder = gov.get("decision_holder")
    centralised = _level(gov.get("beta"), "decisions are mostly left to the parties",
                         "decisions are shared", "decisions are strongly centralised")
    if holder:
        lines.append(f"{holder} decides" + (f"; {centralised} (β = {gov['beta']:g})." if centralised else "."))
    elif centralised:
        lines.append(f"No decision holder is named; {centralised} (β = {gov['beta']:g}).")
    else:
        lines.append("Nobody is named as deciding under this contract.")

    sharing = _level(gov.get("alpha"), "little information is shared",
                     "some information is shared", "most information is shared")
    if sharing:
        lines.append(f"Between the parties, {sharing} (α = {gov['alpha']:g}).")
    incentive = _level(gov.get("lambda"), "weak", "moderate", "strong")
    if incentive:
        kind = f" of type {gov['incentive_type']}" if gov.get("incentive_type") else ""
        lines.append(f"Compliance is encouraged by {incentive} incentives{kind} (λ = {gov['lambda']:g}).")

    if contract.get("violation_detect"):
        lines.append(
            f"A violation is detected when `{contract['violation_detect']}`"
            + (f"; the response is `{contract['violation_action']}`."
               if contract.get("violation_action") else "; no response is defined."))
    else:
        lines.append("It does not say how a violation would be detected.")

    lifecycle = contract.get("lifecycle") or {}
    if lifecycle.get("states"):
        lines.append(
            f"Each instance starts in {lifecycle.get('initial')} and ends in "
            f"{_join(lifecycle.get('terminal') or [], 'or') or 'no defined final state'}.")
    for m in contract.get("monitors") or []:
        outcome = (f"the instance moves to {m['on_match_transition']}" if m.get("on_match_transition")
                   else f"a {m.get('on_match_violation')} violation is raised"
                   if m.get("on_match_violation") else "nothing is defined to happen")
        lines.append(f"Monitor {m.get('id')} watches `{m.get('rule')}`; when it holds, {outcome}.")
    return lines


def _responsibilities(source: str) -> Dict[str, Dict[str, List[str]]]:
    """{contract id: {actor: [duties]}} — these are not carried into the IR."""
    try:
        contracts = ds.load_doc(source)["sos"].get("contracts") or []
    except ValueError:
        return {}
    out: Dict[str, Dict[str, List[str]]] = {}
    for c in contracts:
        duties = c.get("responsibilities") if isinstance(c, dict) else None
        if isinstance(duties, dict):
            out[str(c.get("id"))] = {
                str(actor): [str(i) for i in (items if isinstance(items, list) else [items])]
                for actor, items in duties.items()
            }
    return out


def describe_design(source: str) -> Dict[str, List[str]]:
    """Every contract of a design in plain sentences, keyed by contract id."""
    analysis = ds.analyze(source)
    if not analysis.parsed:
        raise ValueError(analysis.findings[0].message)
    duties = _responsibilities(source)
    described = {}
    for c in _contracts(analysis):
        cid = str(c.get("id"))
        lines = describe_contract(c)
        for actor, items in duties.get(cid, {}).items():
            lines.insert(2, f"{actor} is specifically responsible for: {_join(items)}.")
        described[cid] = lines
    return described


# ── One actor's point of view ───────────────────────────────────────


def actor_view(source: str, actor: str) -> Dict[str, object]:
    """What the design means for one actor: duties, authority, messages, exposure.

    Returns {"actor", "summary", "contracts", "protocols", "notes"} where
    the list values hold plain sentences.
    """
    analysis = ds.analyze(source)
    if not analysis.parsed:
        raise ValueError(analysis.findings[0].message)
    ir = analysis.ir
    name = ds.actor_base(actor)
    actors = {str(a.get("id")): a for a in (ir.get("institution") or {}).get("actors") or []}
    if name not in actors:
        raise ValueError(f"No actor `{actor}`. Actors: {', '.join(actors) or 'none'}.")
    me = actors[name]

    summary = f"{name} is a {me.get('role') or 'participant'} with {me.get('autonomy') or 'medium'} autonomy."
    if me.get("capabilities"):
        summary += f" It can {_join(me['capabilities'])}."

    duties = _responsibilities(source)
    contracts: List[str] = []
    for c in _contracts(analysis):
        if name not in {ds.actor_base(p) for p in c.get("parties") or []}:
            continue
        cid = c.get("id")
        gov = c.get("governance") or {}
        others = [p for p in c.get("parties") or [] if ds.actor_base(p) != name]
        holder = ds.actor_base(gov.get("decision_holder") or "")
        role = ("decides under it" if holder == name
                else f"must follow {holder}'s decisions" if holder
                else "is bound by it, with no decision holder named")
        line = f"{cid}, with {_join(others) or 'no other party'}: {name} {role}."
        guarantee = [f"`{g}`" for g in c.get("guarantee") or []]
        if guarantee:
            line += f" Together the parties must ensure {_join(guarantee)}."
        mine = [f"`{a}`" for a in c.get("assume") or [] if name in str(a)]
        if mine:
            line += f" It is expected to keep {_join(mine)} true."
        own = [item for actor, items in duties.get(str(cid), {}).items()
               if ds.actor_base(actor) == name for item in items]
        if own:
            line += f" Its own responsibility: {_join(own)}."
        if c.get("violation_detect"):
            line += (f" A breach (`{c['violation_detect']}`) leads to "
                     f"`{c.get('violation_action') or 'no defined response'}`.")
        contracts.append(line)

    protocols: List[str] = []
    for p in (ir.get("protocol") or {}).get("protocols") or []:
        sends, receives, computes = [], [], []
        for step in p.get("steps") or []:
            sender, receiver = ds.actor_base(step.get("sender") or ""), ds.actor_base(step.get("receiver") or "")
            content = str(step.get("content") or "")
            if step.get("type") == "message":
                if sender == name:
                    sends.append(f"{content} to {receiver}")
                if receiver == name:
                    receives.append(f"{content} from {sender}")
            elif step.get("type") == "compute" and sender == name:
                computes.append(content)
        if sends or receives or computes:
            parts = []
            if receives:
                parts.append(f"receives {_join(receives)}")
            if computes:
                parts.append(f"performs {_join(computes)}")
            if sends:
                parts.append(f"sends {_join(sends)}")
            line = f"In {p.get('id')} (started by `{p.get('trigger') or '?'}`), {name} {_join(parts)}."
            if (p.get("timing") or {}).get("max_total"):
                line += f" The whole exchange must finish within {p['timing']['max_total']}."
            protocols.append(line)

    notes = [
        f"{f.title}" + (f" — {f.message}" if f.message else "")
        for f in analysis.findings
        if f.level in ("error", "warning") and (f.item == name or f"'{name}'" in f.title
                                                or f" {name} " in f" {f.title} ")
    ]
    if not contracts:
        notes.append(f"{name} is not bound by any contract.")
    return {"actor": name, "summary": summary, "contracts": contracts,
            "protocols": protocols, "notes": notes}


# ── Lifecycle stories ───────────────────────────────────────────────


def _step_sentence(move: ds.Move, state: str) -> str:
    if move.kind == "transition":
        cause = f" when `{move.detail}`" if move.detail else ""
        return f"From {state}, `{move.label}` takes it to {move.target}{cause}."
    if move.kind == "deadline":
        return f"From {state}, {move.label} ({move.detail}), so it moves to {move.target}."
    return f"In {state}, {move.label} (`{move.detail}`), so it moves to {move.target}."


def narrate_path(source: str, contract_id: str, path: List[str],
                 kinds: Optional[List[str]] = None) -> Dict[str, object]:
    """Tell one path through a contract lifecycle as sentences.

    ``path`` is a list of states starting at the initial state. The
    result also lists what can happen next from the last state, so a
    caller can extend the path one step at a time. ``kinds`` optionally
    says how each step is taken ("transition", "deadline" or "monitor")
    when several events lead to the same state.
    """
    analysis = ds.analyze(source)
    if not analysis.parsed:
        raise ValueError(analysis.findings[0].message)
    contract = _find_contract(analysis, contract_id)
    lifecycle = contract.get("lifecycle") or {}
    if not lifecycle.get("states"):
        raise ValueError(f"Contract `{contract_id}` has no lifecycle.")
    initial = lifecycle.get("initial")
    path = [str(s) for s in path] or [initial]
    if path[0] != initial:
        raise ValueError(f"A path must start at the initial state `{initial}`.")

    sentences = [f"A new instance of {contract_id} starts in {initial}."]
    for index, (current, target) in enumerate(zip(path, path[1:])):
        moves = [m for m in ds.lifecycle_moves(contract, current) if m.target == target]
        wanted = kinds[index] if kinds and index < len(kinds) else None
        moves = [m for m in moves if m.kind == wanted] or moves
        if not moves:
            options = sorted({m.target for m in ds.lifecycle_moves(contract, current)})
            raise ValueError(
                f"Nothing takes an instance from `{current}` to `{target}`. "
                f"From `{current}` it can go to: {', '.join(options) or 'nowhere'}.")
        sentences.append(_step_sentence(moves[0], current))

    last = path[-1]
    terminal = last in (lifecycle.get("terminal") or [])
    next_moves = ds.lifecycle_moves(contract, last)
    if terminal:
        sentences.append(f"{last} is final: this instance has ended.")
        # The lifecycle stops here; what the contract says should follow a
        # breach is written elsewhere, so say it where the story ends.
        if "violat" in last.lower() and contract.get("violation_action"):
            sentences.append(
                f"For a violation the contract prescribes `{contract['violation_action']}`; "
                "the lifecycle itself does not continue into that.")
    elif not next_moves:
        sentences.append(f"Nothing can happen in {last}: the instance is stuck.")
    return {
        "contract": str(contract_id), "path": path, "story": " ".join(sentences),
        "ended": terminal, "stuck": not terminal and not next_moves,
        "next": [{"kind": m.kind, "event": m.label, "to": m.target, "detail": m.detail}
                 for m in next_moves],
    }


def lifecycle_stories(source: str, contract_id: str) -> List[Dict[str, object]]:
    """Distinct ways one contract instance can run from start to end.

    Each story visits a state at most once, so loops are followed once.
    Stories that end well come first, then those that end in a state
    reached through a missed deadline or a monitor, then stuck ones.
    """
    analysis = ds.analyze(source)
    if not analysis.parsed:
        raise ValueError(analysis.findings[0].message)
    contract = _find_contract(analysis, contract_id)
    lifecycle = contract.get("lifecycle") or {}
    if not lifecycle.get("states"):
        raise ValueError(f"Contract `{contract_id}` has no lifecycle.")
    initial, terminal = lifecycle.get("initial"), set(lifecycle.get("terminal") or [])
    known = set(lifecycle.get("states") or [])

    found: List[tuple] = []   # (path, kinds of the steps taken)

    def walk(path: List[str], kinds: List[str]):
        if len(found) >= MAX_STORIES * 4:
            return
        current = path[-1]
        moves = [m for m in ds.lifecycle_moves(contract, current)
                 if m.target in known and m.target not in path]
        if current in terminal or not moves or len(path) >= MAX_STEPS:
            found.append((list(path), list(kinds)))
            return
        seen_targets = set()
        for move in moves:
            # One story per (target, kind): several monitors to the same state read alike.
            if (move.target, move.kind) in seen_targets:
                continue
            seen_targets.add((move.target, move.kind))
            walk(path + [move.target], kinds + [move.kind])

    if initial not in known:
        raise ValueError(f"The initial state `{initial}` is not a declared state.")
    walk([initial], [])

    def rank(entry: tuple) -> tuple:
        path, kinds = entry
        ended = path[-1] in terminal
        abnormal = any(k != "transition" for k in kinds)
        return (0 if ended and not abnormal else 1 if ended else 2, len(path))

    stories = []
    for path, kinds in sorted(found, key=rank)[:MAX_STORIES]:
        told = narrate_path(source, contract_id, path, kinds)
        causes = _join(
            [label for kind, label in (("deadline", "a missed deadline"), ("monitor", "a monitor firing"))
             if kind in kinds], "and")
        told["outcome"] = (
            f"stuck in {path[-1]}" if told["stuck"] else
            f"ends in {path[-1]} after {causes}" if causes else f"ends in {path[-1]}")
        stories.append(told)
    return stories


# ── Verification results in words ───────────────────────────────────


def explain_verification(source: str) -> List[Dict[str, str]]:
    """What the verifier concluded about each contract, in plain words."""
    analysis = ds.analyze(source)
    out = []
    for f in analysis.findings:
        if f.stage != "Verification":
            continue
        title, message = f.title, f.message
        if "consistency" in title:
            meaning = ("The assumptions and guarantees can all be true together."
                       if f.level == "pass" else
                       "The assumptions and guarantees can never all be true together, so the "
                       "contract cannot be honoured as written.")
        elif "assumptions" in title:
            meaning = ("There are situations in which the assumptions hold, so the contract applies."
                       if f.level == "pass" else
                       "The assumptions can never hold, so the contract would never apply.")
        elif "entailment" in title:
            meaning = ("The guarantees are real obligations: assuming only what the contract "
                       "assumes, they could still fail, so some party has to make them true. "
                       "This is expected for most contracts.")
            if "Counterexample:" in message:
                example = message.split("Counterexample:", 1)[1].strip()
                meaning += f" For instance, with {example} the assumptions hold but a guarantee does not."
        else:
            meaning = message
        out.append({"contract": f.item, "check": title, "result": f.level, "meaning": meaning})
    return out
