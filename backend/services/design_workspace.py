"""A folder of CADL designs that an assistant and the Designer both work on.

Each design is `<name>.cadl`. Next to it, `<name>.review.json` keeps
what the source file cannot: pending proposals, the changes an
assistant applied that nobody has reviewed yet, and a short undo stack.

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


def add_proposal(name: str, changes: List[dict], rationale: str = "") -> dict:
    """Check a set of changes against the design and store it for review."""
    source = read(name)
    try:
        proposal = ops.propose(source, changes)
    except (ops.OpError, ValueError) as e:
        raise WorkspaceError(str(e)) from e
    state = _load_state(name)
    proposal_id = secrets.token_hex(3)
    state["proposals"][proposal_id] = {
        "base": _hash(source), "source": proposal.source, "time": _now(),
        "rationale": rationale, "changes": [asdict(c) for c in proposal.changes],
    }
    for stale in list(state["proposals"])[:-MAX_PROPOSALS]:
        del state["proposals"][stale]
    _save_state(name, state)
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
        "next": (
            "Show these changes to the designer. Apply with apply_proposal only after they agree."
            if proposal.acceptable else
            "This proposal adds an error. Revise the changes and propose again rather than applying it."),
    }


def proposals(name: str) -> Dict[str, dict]:
    return _load_state(name)["proposals"]


def apply_proposal(name: str, proposal_id: str) -> dict:
    """Apply a stored proposal and mark what it touched as not yet reviewed."""
    state = _load_state(name)
    entry = state["proposals"].get(proposal_id)
    if entry is None:
        raise WorkspaceError(
            f"No pending proposal `{proposal_id}` for `{name}`. It may have been replaced "
            "because the design changed; propose the changes again.")
    if entry["base"] != _hash(read(name)):
        raise WorkspaceError(
            "The design has changed since this proposal was made. Propose the changes again.")
    write(name, entry["source"], note=f"proposal {proposal_id}")
    state = _load_state(name)   # write() updated the undo stack and cleared proposals
    for change in entry["changes"]:
        state["unreviewed"].append({**change, "time": _now(), "rationale": entry.get("rationale", "")})
    _save_state(name, state)
    return {"applied": True, "changes": [c["summary"] for c in entry["changes"]],
            "unreviewed": len(state["unreviewed"]),
            "note": "These changes are listed as not yet reviewed on the Designer page, "
                    "where the person can look at each one and confirm it."}


def discard_proposal(name: str, proposal_id: str) -> None:
    state = _load_state(name)
    if state["proposals"].pop(proposal_id, None) is None:
        raise WorkspaceError(f"No pending proposal `{proposal_id}` for `{name}`.")
    _save_state(name, state)


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
