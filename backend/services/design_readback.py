"""Read a CADL design back in plain language.

Checks say whether a design is consistent; they cannot say whether it
is what the designer meant. These functions restate the design so a
person can confirm it: what each contract says, what each actor is
bound to, and what can happen to a contract instance step by step.

Everything here is derived from the simulator IR by fixed rules — no
language model is involved — so the same design always reads the same.
"""

from __future__ import annotations

import re
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
    lines += [_norm_sentence(n) for n in contract.get("norms") or [] if isinstance(n, dict)]
    for r in contract.get("counts_as") or []:
        if isinstance(r, dict):
            condition = r.get("if", r.get("if_"))       # `if` as `cadl sim-ir` writes it
            lines.append(f"`{r.get('when')}` counts as {r.get('creates')}"
                         + (f" when `{condition}` holds." if condition else "."))
    lines += _grounding_sentences(contract.get("grounding"))
    if contract.get("norms"):
        lines += _wildcard_sentences(contract)
    return lines


def _wildcard_reading(text) -> Optional[str]:
    """How cadl reads a predicate with `A[*]` or `A[lo..hi]` outside a quantifier
    (norms 0.1, F.5, cadl 0.3.24): the quantified form, or None when nothing is
    rewritten (also for a message event or a text that is not a predicate)."""
    try:
        from cadl.norms_eval import desugar_wildcards
        from cadl.parser import parse_rule
        from cadl.unparse import expr_to_source
    except ImportError:         # a cadl before 0.3.24
        return None
    if not isinstance(text, str) or not re.search(r"\[(\*|[^\[\]]*\.\.[^\[\]]*)\]", text):
        return None
    try:
        tree = parse_rule(text)
        plain, read = expr_to_source(tree), expr_to_source(desugar_wildcards(tree))
    except Exception:
        return None
    return read if read != plain else None


def _wildcard_sentences(contract: dict) -> List[str]:
    """One sentence per predicate of a contract with norms whose wildcard or range
    references cadl reads as a quantifier or comprehension."""
    texts = [str(x) for key in ("assume", "guarantee") for x in contract.get(key) or []]
    for n in contract.get("norms") or []:
        if isinstance(n, dict):
            texts += [str(n[k]) for k in ("trigger", "until", "content") if n.get(k)]
    for r in contract.get("counts_as") or []:
        if isinstance(r, dict):
            texts += [str(r[k]) for k in ("when", "if", "if_") if r.get(k)]
    out = []
    for text in dict.fromkeys(texts):
        read = _wildcard_reading(text)
        if read:
            out.append(f"`{text}` is read as `{read}`.")
    if out:
        out.insert(0, "A reference such as `ROBOT[*]` outside a quantifier is read as a quantifier "
                      "or comprehension over the members (F.5); `*1`, `*2` name the variables:")
    return out


def _ratio(given, total) -> str:
    from fractions import Fraction
    if not isinstance(given, int) or not isinstance(total, int) or total <= 0:
        return ""
    value = Fraction(given, total)
    return str(value) if value.denominator == 1 else f"{value} = {float(value):.3g}"


def _grounding_sentences(grounding) -> List[str]:
    """The grounding of a contract (grounding 0.1, Appendix J) as the IR carries it:
    its information items and the indices derived from its norms."""
    if not isinstance(grounding, dict):
        return []
    lines = []
    items = grounding.get("items") or {}
    if items:
        lines.append("Its information items (grounding 0.1) are "
                     + _join([f"{k}, from {v}" for k, v in items.items()]) + ".")
    indices = grounding.get("indices") or {}
    alpha, beta, cov = indices.get("alpha"), indices.get("beta"), indices.get("coverage")
    if isinstance(alpha, dict) and _ratio(alpha.get("given"), alpha.get("pairs")):
        lines.append(f"Pairs of an item and a party other than its origin, given to the party by "
                     f"a sharing entry or a norm: {alpha['given']} of {alpha['pairs']} "
                     f"(α̂ = {_ratio(alpha.get('given'), alpha.get('pairs'))}).")
    if isinstance(beta, dict) and _ratio(beta.get("largest"), beta.get("powers")):
        lines.append(f"Powers held by the single actor with the most: {beta['largest']} of "
                     f"{beta['powers']} (β̂ = {_ratio(beta.get('largest'), beta.get('powers'))}).")
    if isinstance(cov, dict) and _ratio(cov.get("incentive"), cov.get("norms")):
        lines.append(f"Obligations and prohibitions that carry an incentive: {cov['incentive']} of "
                     f"{cov['norms']} (χ = {_ratio(cov.get('incentive'), cov.get('norms'))}).")
    if (items or indices) and grounding.get("tolerance") is not None:
        lines.append(f"A declared α or β may differ from its index by at most "
                     f"{grounding['tolerance']:g} (J-7); a declared 0 or 1 is tested by J-6 instead, "
                     f"which looks in the norms for a counterexample. `cadl norms-indices` "
                     f"reports both.")
    return lines


_NORM_VERB = {"obligation": "must", "permission": "may", "prohibition": "must not"}
_MESSAGE = re.compile(r"^\s*(.+?)\s*->\s*(.+?)\s*:\s*(.+?)\s*$")


def _norm_sentence(n: dict) -> str:
    """A norm of the norms extension (norms 0.1) as one or two sentences."""
    kind = str(n.get("kind") or "norm")
    bearer = str(n.get("bearer") or "someone")
    content = str(n.get("content") or "")
    message = _MESSAGE.match(content)
    verb = _NORM_VERB.get(kind, "can")
    if kind == "power":
        what = f"can bring about `{content}`"
    elif message and message.group(1) == bearer:
        what = f"{verb} send `{message.group(3)}` to {message.group(2)}"
    else:
        what = f"{verb} bring about `{content}`"
    text = f"Norm {n.get('id')} ({kind}): {bearer} {what}"
    if n.get("counterparty") and not (message and message.group(2) == n["counterparty"]):
        text += f", towards {n['counterparty']}"
    if n.get("trigger"):
        text += f", after `{n['trigger']}`"
    if n.get("deadline_ms") is not None:
        text += f", within {n['deadline_ms'] / 1000:g} s"
    text += "."
    if n.get("until"):
        text += f" It ends when `{n['until']}`."
    if n.get("shares"):
        text += f" Its message gives the information {_join([f'`{x}`' for x in n['shares']])} to the receiver."
    then = []
    magnitude = n.get("on_violation_magnitude")
    size = f" of magnitude {magnitude:g}" if isinstance(magnitude, (int, float)) else ""
    if n.get("on_violation_severity"):
        then.append(f"a {n['on_violation_severity']} violation")
    if n.get("on_violation_sanction"):
        then.append(f"the sanction “{n['on_violation_sanction']}”{size}")
    elif size:
        then.append(f"a sanction{size}")
    if n.get("on_violation_reparation"):
        then.append(f"the reparation {n['on_violation_reparation']}")
    if n.get("on_violation_transition"):
        then.append(f"a move to {n['on_violation_transition']}")
    if then:
        text += f" Violating it brings {_join(then)}."
    elif kind in ("obligation", "prohibition"):
        text += " Violating it is a Major violation (the default)."
    reward, size = n.get("on_fulfilment_reward"), n.get("on_fulfilment_magnitude")
    if reward or isinstance(size, (int, float)):
        text += (" Fulfilling it brings " + (f"the reward “{reward}”" if reward else "a reward")
                 + (f" of magnitude {size:g}" if isinstance(size, (int, float)) else "") + ".")
    return text


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
        # N-15 (cadl 0.3.24): a wildcard or range reference whose reading may surprise.
        lines += [f"Check that this is meant (N-15): {f.title[0].lower()}{f.title[1:-len(' (N-15)')]}."
                  for f in analysis.findings
                  if f.title.endswith("(N-15)") and f"of contract '{cid}'" in f.title]
        described[cid] = lines
    return described


# ── The strategic model in words ────────────────────────────────────


def _term(term: dict) -> str:
    weight = str(term.get("weight"))
    return f"{f'({weight})' if ' ' in weight else weight} × {term.get('reward')}"


def describe_model(source: str) -> List[str]:
    """The strategic model of a design (games 0.1, Appendix I) in plain sentences:
    players, who chooses which command, the profiles, the payoffs and the
    equilibrium queries. Empty for a design without one."""
    analysis = ds.analyze(source)
    model = (analysis.ir or {}).get("stochastic_model") or {}
    if model.get("kind") != "smg" or "games" not in ds.extensions_in_effect(source):
        return []
    players = model.get("players") or []
    lines = ["The `model:` block is a turn-based stochastic game (games 0.1) between "
             + _join([f"{p.get('id')} ({_join(p.get('actors') or []) or 'no actor'})"
                      for p in players]) + "."]
    owned: Dict[str, List[str]] = {}
    for c in model.get("commands") or []:
        owned.setdefault(str(c.get("owner") or ""), [])
        if c.get("action") and c["action"] not in owned[str(c.get("owner") or "")]:
            owned[str(c.get("owner") or "")].append(str(c["action"]))
    for p in players:
        actions = owned.get(str(p.get("id")), [])
        lines.append(f"{p.get('id')} chooses " + (_join([f"`{a}`" for a in actions], "or")
                                                   if actions else "no command") + ".")
    if owned.get(""):
        lines.append(f"No player owns {_join([f'`{a}`' for a in owned['']])}.")
    for prof in model.get("profiles") or []:
        rules = prof.get("rules") or []
        said = [f"`{r.get('choose')}`" if str(r.get("guard", "true")) == "true"
                else f"`{r.get('choose')}` when `{r.get('guard')}`" for r in rules]
        compliance = " (the compliance profile)" if prof.get("id") == model.get("compliance") else ""
        lines.append(f"Profile {prof.get('id')}{compliance} chooses {'; otherwise '.join(said)}.")
    for pay in model.get("payoffs") or []:
        terms = pay.get("terms") or []
        lines.append(f"The payoff of {pay.get('player')} is the sum of "
                     f"{_join([_term(t) for t in terms])}, accumulated until `{pay.get('until')}`."
                     if len(terms) > 1 else
                     f"The payoff of {pay.get('player')} is {_join([_term(t) for t in terms])}, "
                     f"accumulated until `{pay.get('until')}`.")
        for t in terms:
            for key, what, counts in (
                    ("sanction_of", "minus the magnitude of the sanction of", "violations"),
                    ("reward_of", "the magnitude of the reward of", "fulfilments")):
                if t.get(key):
                    lines.append(f"The weight of {t.get('reward')} is {what} norm {t[key]} times "
                                 f"the parameter bound to the λ of its contract (grounding 0.1, "
                                 f"J.7); every result assumes that {t.get('reward')} counts the "
                                 f"{counts} of the norm.")
    for q in model.get("equilibria") or []:
        concept = ("an equilibrium from every reachable state" if q.get("concept") == "every_reachable_state"
                   else "a Nash equilibrium")
        epsilon = f" up to ε = {q['epsilon']:g}" if q.get("epsilon") else ""
        over = q.get("over") or {}
        where = (" over " + _join([f"{k} in [{v[0]:g}, {v[1]:g}]" for k, v in over.items()])
                 if over else "")
        lines.append(f"Query {q.get('id')} asks whether profile {q.get('profile')} is {concept}"
                     f"{epsilon}{where}, in {_join(q.get('regimes') or [], 'and')}"
                     + (" (a map: `cadl game-check --certify`)." if over else "."))
    return lines


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
