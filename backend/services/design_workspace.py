"""A folder of CADL designs that an assistant and the Designer both work on.

Each design is `<name>.cadl`. Next to it, `<name>.review.json` keeps
what the source file cannot: pending proposals, the changes an
assistant applied that nobody has reviewed yet, a short undo stack, and
the decisions — each applied proposal with the person's reason and
assumptions — which are also written out as `<name>.decisions.md`.

A constitution for the design's `# @amend:` values is read from
`<name>.constitution.yaml`, or else `constitution.yaml`, in the folder.

The folder is `$CADL_WORKSPACE`, or `~/cadl-designs` when that is unset.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from backend.services import design_amend as amend
from backend.services import design_ops as ops
from backend.services import design_service as ds

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}")
MAX_UNDO = 10
MAX_PROPOSALS = 10


class WorkspaceError(ValueError):
    """A workspace request that cannot be carried out; the message says why."""


def root() -> Path:
    path = Path(os.environ.get("CADL_WORKSPACE") or Path.home() / "cadl-designs").expanduser()
    path.mkdir(parents=True, exist_ok=True)
    return path


def enabled() -> bool:
    """True when a workspace was configured explicitly (the Designer shows it then)."""
    return bool(os.environ.get("CADL_WORKSPACE"))


def _check_name(name: str) -> str:
    name = str(name or "").strip()
    if name.endswith(".cadl"):
        name = name[:-5]
    if not NAME.fullmatch(name):
        raise WorkspaceError(
            "A design name is 1–64 letters, digits, `_`, `-` or `.`, starting with a letter or digit.")
    return name


def path_of(name: str) -> Path:
    return root() / f"{_check_name(name)}.cadl"


def _sidecar(name: str) -> Path:
    return root() / f"{_check_name(name)}.review.json"


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def list_designs() -> List[str]:
    return sorted(p.stem for p in root().glob("*.cadl"))


def exists(name: str) -> bool:
    return path_of(name).exists()


def read(name: str) -> str:
    path = path_of(name)
    if not path.exists():
        known = ", ".join(list_designs()) or "none"
        raise WorkspaceError(f"No design `{name}` in the workspace. Designs: {known}.")
    return path.read_text(encoding="utf-8")


def source_of(name: str, proposal_id: Optional[str] = None) -> str:
    """The design's source, or what it would be after a pending proposal."""
    if not proposal_id:
        return read(name)
    entry = _load_state(name)["proposals"].get(proposal_id)
    if entry is None:
        raise WorkspaceError(f"No pending proposal `{proposal_id}` for `{name}`.")
    return entry["source"]


def _load_state(name: str) -> dict:
    path = _sidecar(name)
    state = {}
    if path.exists():
        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    state.setdefault("proposals", {})
    state.setdefault("unreviewed", [])
    state.setdefault("undo", [])
    state.setdefault("decisions", [])
    return state


def _save_state(name: str, state: dict) -> None:
    _sidecar(name).write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def write(name: str, source: str, note: str = "") -> None:
    """Replace a design's source, keeping the previous text for undo."""
    if len(source) > ds.MAX_SOURCE_CHARS:
        raise WorkspaceError(f"The design is limited to {ds.MAX_SOURCE_CHARS:,} characters.")
    path = path_of(name)
    state = _load_state(name)
    if path.exists():
        previous = path.read_text(encoding="utf-8")
        if previous != source:
            state["undo"].append({"source": previous, "time": _now(), "note": note})
            del state["undo"][:-MAX_UNDO]
            # A proposal made against the old text no longer applies cleanly.
            state["proposals"] = {}
    path.write_text(source, encoding="utf-8")
    _save_state(name, state)


def create(name: str, start: str = "empty") -> str:
    """Create a design.

    ``start`` is "empty" (just the name — nothing invented), "template"
    (a small two-actor design with placeholder terms) or the name of a
    bundled example.
    """
    if exists(name):
        raise WorkspaceError(f"A design `{name}` already exists.")
    if start == "empty":
        source = ds.dump_doc({"sos": {"name": _check_name(name)}})
    elif start in ("template", "blank"):
        source = ds.NEW_DESIGN
    elif start in ds.list_examples():
        source = ds.load_example(start)
    else:
        raise WorkspaceError(
            f"Unknown starting point `{start}`. Use `empty`, `template` or one of: "
            f"{', '.join(ds.list_examples())}.")
    write(name, source)
    return source


def undo(name: str) -> str:
    """Restore the design as it was before the last change."""
    state = _load_state(name)
    if not state["undo"]:
        raise WorkspaceError(f"Nothing to undo for `{name}`.")
    entry = state["undo"].pop()
    path_of(name).write_text(entry["source"], encoding="utf-8")
    state["proposals"] = {}
    # Marks made by the undone change no longer describe the file.
    state["unreviewed"] = [u for u in state["unreviewed"] if u.get("time", "") < entry["time"]]
    _save_state(name, state)
    return entry["source"]


# ── Proposals ───────────────────────────────────────────────────────


def _finding(f: ds.Finding) -> dict:
    return {"level": f.level, "check": f.stage, "title": f.title, "message": f.message}


def _store(name: str, source: str, proposal: ops.Proposal, rationale: str, **extra) -> str:
    state = _load_state(name)
    proposal_id = secrets.token_hex(3)
    state["proposals"][proposal_id] = {
        "base": _hash(source), "source": proposal.source, "time": _now(),
        "rationale": rationale, "changes": [asdict(c) for c in proposal.changes],
        "acceptable": proposal.acceptable,
        "errors_introduced": [f.title for f in proposal.introduced if f.level == "error"],
        **extra,
    }
    for stale in list(state["proposals"])[:-MAX_PROPOSALS]:
        del state["proposals"][stale]
    _save_state(name, state)
    return proposal_id


def add_proposal(name: str, changes: List[dict], rationale: str = "") -> dict:
    """Check a set of changes against the design and store it for review."""
    source = read(name)
    try:
        proposal = ops.propose(source, changes)
    except (ops.OpError, ValueError) as e:
        raise WorkspaceError(str(e)) from e
    proposal_id = _store(name, source, proposal, rationale)
    return {
        "proposal_id": proposal_id,
        "applied": False,
        "changes": [c.summary for c in proposal.changes],
        "structural_diff": proposal.diff,
        "problems_introduced": [_finding(f) for f in proposal.introduced],
        "problems_resolved": [_finding(f) for f in proposal.resolved],
        "contracts_would_read": proposal.readback,
        "errors_after": proposal.errors_after,
        "warnings_after": proposal.warnings_after,
        "acceptable": proposal.acceptable,
        **({"comments_note": (
            "The design marks amendable values with `# @amend:` comments. Applying "
            "this proposal rewrites the file without comments, so those marks are "
            "lost and a program that relies on them (the Course D council) can no "
            "longer amend the file. Tell the person before applying. To change only "
            "a marked value, use propose_amendment, which keeps the comments.")}
           if ds.has_amend_tags(source) else {}),
        "next": (
            "Show these changes to the designer. Apply with apply_proposal only after they agree."
            if proposal.acceptable else
            "This proposal adds an error, so apply_proposal will refuse it. Revise the changes "
            "and propose again."),
    }


def proposals(name: str) -> Dict[str, dict]:
    return _load_state(name)["proposals"]


def apply_proposal(name: str, proposal_id: str, allow_errors: bool = False,
                   reason: str = "", assumptions: Optional[List[str]] = None) -> dict:
    """Apply a stored proposal, mark what it touched as not yet reviewed, and record the decision.

    A proposal that adds an error is refused unless ``allow_errors`` is set.
    An amendment the constitution leaves to a person (a contract or
    regulation layer, or a safety-related rule) is refused without the
    person's ``reason``.
    """
    state = _load_state(name)
    entry = state["proposals"].get(proposal_id)
    if entry is None:
        raise WorkspaceError(
            f"No pending proposal `{proposal_id}` for `{name}`. It may have been replaced "
            "because the design changed; propose the changes again.")
    if entry["base"] != _hash(read(name)):
        raise WorkspaceError(
            "The design has changed since this proposal was made. Propose the changes again.")
    if entry.get("gate"):
        raise WorkspaceError(
            "The constitution does not allow this amendment (" + "; ".join(entry["gate"]) + "), so "
            "it cannot be applied. Propose an amendment the constitution allows.")
    if not entry.get("acceptable", True) and not allow_errors:
        added = "; ".join(entry.get("errors_introduced") or []) or "an error"
        raise WorkspaceError(
            f"This proposal adds an error to the design ({added}), so it was not applied. "
            "Revise the changes and propose again. Apply it as it is only if the person "
            "asked for that after seeing the error: call apply_proposal with allow_errors=true.")
    person = entry.get("for_a_person") or []
    reason = str(reason or "").strip()
    if person and not reason:
        raise WorkspaceError(
            f"The constitution leaves {', '.join(person)} to a person, so this amendment needs "
            "their decision. Ask them, then call apply_proposal again with their reason "
            "(and any assumptions they state).")
    write(name, entry["source"], note=f"proposal {proposal_id}")
    state = _load_state(name)   # write() updated the undo stack and cleared proposals
    for change in entry["changes"]:
        state["unreviewed"].append({**change, "time": _now(), "rationale": entry.get("rationale", "")})
    state["decisions"].append({
        "time": _now(), "proposal": proposal_id,
        "changes": [c["summary"] for c in entry["changes"]],
        "rationale": entry.get("rationale", ""), "reason": reason,
        "assumptions": [str(a).strip() for a in ([assumptions] if isinstance(assumptions, str)
                                                  else assumptions or []) if str(a).strip()],
        "for_a_person": person, "allowed_errors": bool(allow_errors and not entry.get("acceptable", True)),
    })
    _save_state(name, state)
    _write_decisions(name, state["decisions"])
    return {"applied": True, "changes": [c["summary"] for c in entry["changes"]],
            "unreviewed": len(state["unreviewed"]),
            "decision_record": str(_decisions_path(name)),
            "note": "These changes are listed as not yet reviewed on the Designer page, "
                    "where the person can look at each one and confirm it. The decision, "
                    "with the reason given, is kept in the decision record."}


def discard_proposal(name: str, proposal_id: str) -> None:
    state = _load_state(name)
    if state["proposals"].pop(proposal_id, None) is None:
        raise WorkspaceError(f"No pending proposal `{proposal_id}` for `{name}`.")
    _save_state(name, state)


# ── Amendments and decisions ────────────────────────────────────────


def constitution_path(name: str) -> Optional[Path]:
    for path in (root() / f"{_check_name(name)}.constitution.yaml", root() / "constitution.yaml"):
        if path.exists():
            return path
    return None


def constitution(name: str) -> Optional[dict]:
    """The constitution's `amendable` table for this design, or None when there is none."""
    path = constitution_path(name)
    if path is None:
        return None
    try:
        return amend.load_constitution(path.read_text(encoding="utf-8"))
    except ValueError as e:
        raise WorkspaceError(f"{path.name}: {e}") from e


def amendables(name: str) -> dict:
    """The design's `# @amend:` values, with what the constitution says about each."""
    source = read(name)
    const = constitution(name)
    return {
        "constitution": constitution_path(name).name if const is not None else None,
        "parameters": [{
            "name": a.name, "value": a.value, "lines": [o.line for o in a.occurrences],
            "written": [o.text for o in a.occurrences],
            **({"layer": amend.layer_of(const.get(a.name)),
                "range": [const.get(a.name, {}).get("min"), const.get(a.name, {}).get("max")],
                "step": const.get(a.name, {}).get("step"),
                "decided_by": ("a person" if const.get(a.name) and amend.needs_human(const[a.name])
                               else "the checks" if const.get(a.name) else "not amendable")}
               if const is not None else {}),
        } for a in amend.amendables(source)],
        "problems": amend.findings(source, const),
    }


def add_amendment(name: str, values: Dict[str, float], rationale: str = "") -> dict:
    """Propose new values for `# @amend:` names, written in place so the comments stay."""
    source = read(name)
    try:
        new_source = amend.set_values(source, {k: float(v) for k, v in values.items()})
    except (TypeError, ValueError) as e:
        raise WorkspaceError(str(e)) from e
    const = constitution(name)
    gate = amend.check_amendment(source, new_source, const)
    changes = [ops.Change(f"Amended {n}: {amend.fmt(a)} → {amend.fmt(b)}", "Amendments", n)
               for n, a, b in gate.changed]
    proposal = ops.compare(source, new_source, changes)
    acceptable = proposal.acceptable and gate.ok
    proposal_id = _store(name, source, proposal, rationale, for_a_person=gate.for_a_person,
                         gate=gate.reasons)
    return {
        "proposal_id": proposal_id, "applied": False,
        "changes": [c.summary for c in changes],
        "constitution": constitution_path(name).name if const is not None else None,
        "constitution_allows": gate.ok, "constitution_problems": gate.reasons,
        "decided_by_a_person": gate.for_a_person,
        "problems_introduced": [_finding(f) for f in proposal.introduced],
        "problems_resolved": [_finding(f) for f in proposal.resolved],
        "errors_after": proposal.errors_after, "acceptable": acceptable,
        "next": (
            "The constitution leaves " + ", ".join(gate.for_a_person) + " to a person. Show them "
            "the change and what it does, ask for their decision and reason, and pass the reason "
            "(and any assumptions) to apply_proposal."
            if acceptable and gate.for_a_person else
            "Show the change to the person. Apply with apply_proposal only after they agree, "
            "and pass their reason so the decision is recorded."
            if acceptable else
            "This amendment does not pass (see constitution_problems or problems_introduced), "
            "so apply_proposal will refuse it. Revise it and propose again."),
    }


def _decisions_path(name: str) -> Path:
    return root() / f"{_check_name(name)}.decisions.md"


def decisions(name: str) -> List[dict]:
    return _load_state(name)["decisions"]


def _write_decisions(name: str, entries: List[dict]) -> None:
    lines = [f"# Decisions on {_check_name(name)}", "",
             "Each applied proposal, with what the assistant proposed and why, and the person's "
             "decision and reason. Written by CADL Explorer; edit the design, not this file.", ""]
    for i, d in enumerate(entries, 1):
        lines += [f"## {i}. {d['time']} — proposal {d['proposal']}", ""]
        lines += [f"- {c}" for c in d["changes"]] + [""]
        if d.get("for_a_person"):
            lines += [f"Decided by a person under the constitution: {', '.join(d['for_a_person'])}.", ""]
        if d.get("allowed_errors"):
            lines += ["Applied although it adds an error, as the person asked.", ""]
        lines += [f"**Proposed because**: {d.get('rationale') or '—'}", "",
                  f"**Reason for the decision**: {d.get('reason') or '— (none given)'}", ""]
        if d.get("assumptions"):
            lines += ["**Assumptions**:", ""] + [f"- {a}" for a in d["assumptions"]] + [""]
    _decisions_path(name).write_text("\n".join(lines), encoding="utf-8")


# ── Review marks ────────────────────────────────────────────────────


def unreviewed(name: str) -> List[dict]:
    """Changes an assistant applied that a person has not confirmed yet."""
    return _load_state(name)["unreviewed"]


def mark_reviewed(name: str, indexes: Optional[List[int]] = None) -> int:
    """Confirm applied changes; all of them when ``indexes`` is None. Returns how many remain."""
    state = _load_state(name)
    if indexes is None:
        state["unreviewed"] = []
    else:
        drop = set(indexes)
        state["unreviewed"] = [u for i, u in enumerate(state["unreviewed"]) if i not in drop]
    _save_state(name, state)
    return len(state["unreviewed"])
