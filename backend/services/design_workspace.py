"""A folder of CADL designs that an assistant and the Designer both work on.

Each design is `<name>.cadl`. Next to it, `<name>.review.json` keeps
what the source file cannot: pending proposals, the changes an
assistant applied that nobody has reviewed yet, a short undo stack, and
the decisions — each applied or rejected proposal with the person's
reason and assumptions, and the results of any trial of it — which are
also written out as `<name>.decisions.md`.

A trial is an evaluation outside the toolchain, such as a simulation:
`record_trial` attaches its numbers to a pending proposal (an evaluator
passed to `add_amendment` or `add_proposal` does so on the spot, and
`run_trial` runs the command in `$CADL_TRIAL_COMMAND`). Applying or
discarding the proposal carries them into the decision record.

In a design that declares CADL's amendment extension (`amendment:`),
`add_amendment` also writes the next version: `sos.version`,
`amendment.previous`, `amendment.effective.period` and the declared
`value:` of the parameter, so that `cadl amend-diff` reads the change.
`add_reversion` writes one next version that restores the values of an
earlier version (read from git) and declares `amendment.reverts`.

A constitution for the design's `# @amend:` values is read from
`<name>.constitution.yaml`, or else `constitution.yaml`, in the folder.

The folder is `$CADL_WORKSPACE`, or `~/cadl-designs` when that is unset.

With `CADL_WORKSPACE_GIT=1`, the folder is a git repository (made with
`git init` when it is not one) and every change to a design is committed: an
edit saved from the Designer, an applied proposal with the person's reason,
a rejected one, and an undo; the constitution is committed with them.
`history(name)` lists those commits and `version_history(name)` the versions
they hold.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import tempfile
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Union

import yaml

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


def git_enabled() -> bool:
    return os.environ.get("CADL_WORKSPACE_GIT", "").lower() in ("1", "true", "yes")


def _git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(root()), *args], capture_output=True, text=True,
                          timeout=30)


def _commit(name: str, message: str) -> None:
    """Commit the files of a design when CADL_WORKSPACE_GIT is set; a failure of git
    does not stop the change, which is already written."""
    if not git_enabled():
        return
    try:
        # a folder inside a repository commits there; any other folder becomes one
        if _git("rev-parse", "--is-inside-work-tree").stdout.strip() != "true":
            _git("init", "-q")
        const = constitution_path(name)
        files = [p.name for p in (path_of(name), _sidecar(name), _decisions_path(name), const)
                 if p is not None and p.exists()]
        _git("add", "--", *files)
        who = [] if _git("config", "user.email").stdout.strip() else [
            "-c", "user.name=CADL Explorer", "-c", "user.email=cadl-explorer@localhost"]
        subprocess.run(["git", "-C", str(root()), *who, "commit", "-q", "-m", message, "--", *files],
                       capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.SubprocessError):
        pass


def history(name: str, limit: int = 20) -> List[dict]:
    """The commits of a design and its decision record, newest first:
    [{"commit", "time", "message"}] (the first line of each message)."""
    if not git_enabled():
        return []
    try:
        out = _git("log", f"-{int(limit)}", "--format=%h%x09%ad%x09%s", "--date=format:%Y-%m-%d %H:%M",
                   "--", path_of(name).name, _decisions_path(name).name).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    return [dict(zip(("commit", "time", "message"), line.split("\t", 2)))
            for line in out.splitlines() if line.count("\t") >= 2]


def version_history(name: str, limit: int = 100) -> dict:
    """The versions of a design in git, oldest first, and how its `# @amend:` values moved.

    A version is the first commit that holds a new ``sos.version`` (or, for a
    design without one, a new set of amendable values). ``values`` is
    ``design_amend.history`` of those versions and ``movement`` is
    ``design_amend.movement`` against the constitution. Without
    ``CADL_WORKSPACE_GIT`` there is no history and the lists are empty.
    """
    commits = history(name, limit)
    versions = _versions(name, commits)
    pairs = [(v["version"], v["source"]) for v in versions]
    try:
        const = constitution(name)
    except WorkspaceError:
        const = None
    return {"commits": commits,
            "versions": [{k: v[k] for k in ("version", "commit", "time", "message")} for v in versions],
            "values": amend.history(pairs), "movement": amend.movement(pairs, const),
            **({} if git_enabled() else {"note": "Start with CADL_WORKSPACE_GIT=1 to keep the history."})}


def _versions(name: str, commits: List[dict]) -> List[dict]:
    """The versions held by ``commits`` (newest first), oldest first, each with its source."""
    versions = []
    for c in reversed(commits):
        try:
            text = _git("show", f"{c['commit']}:{path_of(name).name}").stdout
        except (OSError, subprocess.SubprocessError):
            continue
        if not text:
            continue
        declared = amend.declared_version(text) or {}
        label = declared.get("version")
        try:
            data = yaml.safe_load(text) or {}
            label = label or (str(data["sos"]["version"]) if data.get("sos", {}).get("version") is not None
                              else None)
        except Exception:
            pass
        key = label if label is not None else repr(sorted(amend.values(text).items()))
        if versions and versions[-1]["key"] == key:
            continue
        versions.append({"key": key, "version": label or c["commit"], "commit": c["commit"],
                         "time": c["time"], "message": c["message"], "source": text})
    return versions


def source_at_version(name: str, version: str) -> str:
    """The design's source when it first declared ``sos.version`` ``version``, from git.

    Needs ``CADL_WORKSPACE_GIT``, since only the history in git keeps the
    earlier versions."""
    if not git_enabled():
        raise WorkspaceError("The sources of earlier versions are kept only in git: start with "
                             "CADL_WORKSPACE_GIT=1 so that each version is committed.")
    read(name)
    wanted = str(version or "").strip()
    versions = _versions(name, history(name, 1000))
    for v in versions:
        if v["version"] == wanted:
            return v["source"]
    known = ", ".join(v["version"] for v in versions) or "none"
    raise WorkspaceError(f"No version `{wanted}` of `{name}` in git. Versions: {known}.")


def version_source(name: str, commit: str) -> str:
    """The design's source as it was at ``commit`` of its history."""
    if not re.fullmatch(r"[0-9a-fA-F]{4,40}", str(commit or "")):
        raise WorkspaceError("A commit is given by its hexadecimal id.")
    out = _git("show", f"{commit}:{path_of(name).name}")
    if out.returncode != 0:
        raise WorkspaceError(f"No version of `{name}` at commit `{commit}`.")
    return out.stdout


def write(name: str, source: str, note: str = "", commit: bool = True) -> None:
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
    if commit:
        _commit(name, f"{_check_name(name)}: {note or 'edited'}")


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
    _commit(name, f"{_check_name(name)}: undo the last change")
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


def add_proposal(name: str, changes: List[dict], rationale: str = "",
                 evaluator: Optional[Evaluator] = None) -> dict:
    """Check a set of changes against the design and store it for review.

    ``evaluator(before, after)``, when given, is run on the proposal and its
    results recorded as its trial (see ``record_trial``).
    """
    source = read(name)
    try:
        proposal = ops.propose(source, changes)
    except (ops.OpError, ValueError) as e:
        raise WorkspaceError(str(e)) from e
    proposal_id = _store(name, source, proposal, rationale)
    trial = _evaluate(name, proposal_id, source, proposal.source, evaluator) if evaluator else None
    return {
        "proposal_id": proposal_id,
        **({"trial": trial} if trial else {}),
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
    write(name, entry["source"], note=f"proposal {proposal_id}", commit=False)
    state = _load_state(name)   # write() updated the undo stack and cleared proposals
    for change in entry["changes"]:
        state["unreviewed"].append({**change, "time": _now(), "rationale": entry.get("rationale", "")})
    state["decisions"].append(_decision(entry, proposal_id, "applied", reason, assumptions,
                                        allowed_errors=bool(allow_errors and not entry.get("acceptable", True))))
    _save_state(name, state)
    _write_decisions(name, state["decisions"])
    _commit(name, _message(name, entry, "", reason))
    return {"applied": True, "changes": [c["summary"] for c in entry["changes"]],
            **({"version": entry["version"]} if entry.get("version") else {}),
            **({"trial": entry["trial"]} if entry.get("trial") else {}),
            "unreviewed": len(state["unreviewed"]),
            "decision_record": str(_decisions_path(name)),
            "note": "These changes are listed as not yet reviewed on the Designer page, "
                    "where the person can look at each one and confirm it. The decision, "
                    "with the reason given, is kept in the decision record."}


def _assumptions(assumptions: Union[None, str, List[str]]) -> List[str]:
    return [str(a).strip() for a in ([assumptions] if isinstance(assumptions, str) else assumptions or [])
            if str(a).strip()]


def _decision(entry: dict, proposal_id: str, outcome: str, reason: str,
              assumptions: Union[None, str, List[str]], allowed_errors: bool = False) -> dict:
    return {
        "time": _now(), "proposal": proposal_id, "outcome": outcome,
        "changes": [c["summary"] for c in entry["changes"]],
        "rationale": entry.get("rationale", ""), "reason": str(reason or "").strip(),
        "assumptions": _assumptions(assumptions),
        "for_a_person": entry.get("for_a_person") or [],
        **({"decided_by": entry["decided_by"]} if entry.get("decided_by") else {}),
        "allowed_errors": allowed_errors,
        **({"version": entry["version"]} if entry.get("version") else {}),
        **({"trial": entry["trial"]} if entry.get("trial") else {}),
    }


def _message(name: str, entry: dict, prefix: str, reason: str) -> str:
    version = entry.get("version") or {}
    trial = entry.get("trial") or {}
    return (f"{_check_name(name)}: {prefix}" + "; ".join(c["summary"] for c in entry["changes"])
            + (f" (version {version['to']}, previous {version['from']}"
               + (f", reverts {version['reverts']}" if version.get("reverts") else "")
               + (f", effective period {version['effective_period']}" if version.get("effective_period")
                  is not None else "") + ")" if version.get("to") else "")
            + (f"\n\nReason: {reason}" if reason else "")
            + (f"\n\nProposed because: {entry['rationale']}" if entry.get("rationale") else "")
            + (f"\n\nTrial: " + ", ".join(f"{k} = {v:g}" for k, v in trial["results"].items())
               + ("" if trial.get("passed") is None else f" ({'passed' if trial['passed'] else 'failed'})")
               if trial.get("results") else ""))


def discard_proposal(name: str, proposal_id: str, reason: str = "",
                     assumptions: Union[None, str, List[str]] = None, record: bool = True) -> dict:
    """Drop a pending proposal and, with ``record``, keep it in the decision record as
    rejected, with the reason, the assumptions and any trial results."""
    state = _load_state(name)
    entry = state["proposals"].pop(proposal_id, None)
    if entry is None:
        raise WorkspaceError(f"No pending proposal `{proposal_id}` for `{name}`.")
    if record:
        state["decisions"].append(_decision(entry, proposal_id, "rejected", reason, assumptions))
    _save_state(name, state)
    if record:
        _write_decisions(name, state["decisions"])
        _commit(name, _message(name, entry, "rejected: ", str(reason or "").strip()))
    return {"discarded": proposal_id, "recorded": bool(record),
            **({"trial": entry["trial"]} if entry.get("trial") else {})}


# ── Trials ──────────────────────────────────────────────────────────

Evaluator = Callable[[str, str], Any]
TRIAL_TIMEOUT = 600


def _trial(raw: Any, by: str = "") -> dict:
    """A trial result in one form: ``{"results": {name: number}, "passed", "note", "by"}``.

    ``raw`` is that mapping, or a mapping of names to numbers alone.
    """
    if not isinstance(raw, dict):
        raise WorkspaceError("A trial gives a mapping of names to numbers, or "
                             "{\"results\": {...}, \"passed\": true|false, \"note\": \"...\"}.")
    results = raw.get("results") if isinstance(raw.get("results"), dict) else \
        {k: v for k, v in raw.items() if k not in ("passed", "note", "by")}
    clean = {}
    for k, v in results.items():
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise WorkspaceError(f"The trial result `{k}` is {v!r}; a trial result is a number.")
        clean[str(k)] = float(v)
    passed = raw.get("passed")
    if passed is not None and not isinstance(passed, bool):
        raise WorkspaceError("`passed` of a trial is true, false or left out.")
    return {"results": clean, "passed": passed, "note": str(raw.get("note") or "").strip(),
            "by": str(raw.get("by") or by or "").strip(), "time": _now()}


def record_trial(name: str, proposal_id: str, results: Any, passed: Optional[bool] = None,
                 note: str = "", by: str = "") -> dict:
    """Attach the results of a trial (a simulation, a pilot) to a pending proposal.

    ``results`` maps names to numbers. The trial replaces an earlier one of the
    same proposal and goes into the decision record when the proposal is applied
    or discarded.
    """
    state = _load_state(name)
    entry = state["proposals"].get(proposal_id)
    if entry is None:
        raise WorkspaceError(f"No pending proposal `{proposal_id}` for `{name}`.")
    raw = dict(results) if isinstance(results, dict) else results
    if isinstance(raw, dict) and "results" not in raw:
        raw = {"results": raw}
    if isinstance(raw, dict):
        raw = {**raw, **({"passed": passed} if passed is not None else {}),
               **({"note": note} if note else {})}
    entry["trial"] = _trial(raw, by)
    _save_state(name, state)
    return entry["trial"]


def _evaluate(name: str, proposal_id: str, before: str, after: str, evaluator: Evaluator) -> dict:
    try:
        raw = evaluator(before, after)
    except WorkspaceError:
        raise
    except Exception as e:  # the evaluator is the caller's code
        raise WorkspaceError(f"The trial failed: {type(e).__name__}: {e}") from e
    trial = _trial(raw, getattr(evaluator, "__name__", ""))
    state = _load_state(name)
    state["proposals"][proposal_id]["trial"] = trial
    _save_state(name, state)
    return trial


def trial_command() -> Optional[List[str]]:
    """The command in `$CADL_TRIAL_COMMAND`, split as a shell would, or None."""
    text = os.environ.get("CADL_TRIAL_COMMAND", "").strip()
    return shlex.split(text) if text else None


def run_trial(name: str, proposal_id: str, command: Optional[List[str]] = None) -> dict:
    """Run the trial command on a pending proposal and record what it prints.

    The command (``$CADL_TRIAL_COMMAND`` unless given) is run with two more
    arguments, the files of the design before and after the proposal, and
    ``CADL_DESIGN`` and ``CADL_PROPOSAL_ID`` in its environment. It prints one
    JSON object, the trial as ``record_trial`` takes it. It runs for at most
    ``$CADL_TRIAL_TIMEOUT`` seconds (600).
    """
    command = command or trial_command()
    if not command:
        raise WorkspaceError("No trial command: set CADL_TRIAL_COMMAND to a program that takes the "
                             "design before and after a proposal and prints its results as JSON.")
    after = source_of(name, proposal_id)
    before = read(name)
    timeout = float(os.environ.get("CADL_TRIAL_TIMEOUT") or TRIAL_TIMEOUT)
    with tempfile.TemporaryDirectory(prefix="cadl_trial_") as tmp:
        files = [Path(tmp) / f"{_check_name(name)}.before.cadl", Path(tmp) / f"{_check_name(name)}.after.cadl"]
        files[0].write_text(before, encoding="utf-8")
        files[1].write_text(after, encoding="utf-8")
        env = {**os.environ, "CADL_DESIGN": _check_name(name), "CADL_PROPOSAL_ID": proposal_id}
        try:
            done = subprocess.run([*command, *map(str, files)], capture_output=True, text=True,
                                  timeout=timeout, env=env, cwd=str(root()))
        except subprocess.TimeoutExpired as e:
            raise WorkspaceError(f"The trial command did not finish in {timeout:g} s.") from e
        except OSError as e:
            raise WorkspaceError(f"The trial command could not be run: {e}") from e
    if done.returncode != 0:
        raise WorkspaceError(f"The trial command exited with status {done.returncode}: "
                             + (done.stderr or done.stdout).strip()[-500:])
    lines = [l for l in done.stdout.strip().splitlines() if l.strip()]
    try:
        raw = json.loads(lines[-1]) if lines else None
    except json.JSONDecodeError:
        try:
            raw = json.loads(done.stdout)
        except json.JSONDecodeError as e:
            raise WorkspaceError("The trial command did not print a JSON object: "
                                 + done.stdout.strip()[-300:]) from e
    return record_trial(name, proposal_id, raw, by=Path(command[0]).name)


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
                "decided_by": ((amend.decider(const[a.name]) or "a person")
                               if const.get(a.name) and amend.needs_human(const[a.name])
                               else amend.decider(const.get(a.name)) or "the checks"
                               if const.get(a.name) else "not amendable")}
               if const is not None else {}),
        } for a in amend.amendables(source)],
        "problems": amend.findings(source, const),
    }


def constitution_settings(name: str) -> dict:
    """The constitution's other keys (``trial_periods`` and the like), or {}."""
    path = constitution_path(name)
    if path is None:
        return {}
    try:
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except Exception:
        return {}
    return {k: v for k, v in doc.items() if k != "amendable"} if isinstance(doc, dict) else {}


def _next_declaration(name: str, source: str, version: Union[None, bool, str],
                      effective_period: Optional[int]) -> Optional[dict]:
    """What the next version declares: {"from", "to", "effective_period"}, or None.

    ``version`` is "auto" or True (the next number, ``design_amend.next_version``),
    a version, or None or False (keep the version). Without ``effective_period``
    the period moves on by the constitution's ``trial_periods`` when both are given.
    """
    declared = amend.declared_version(source)
    if declared is None or version in (None, False, ""):
        return None
    old = declared["version"]
    new = amend.next_version(old) if version in (True, "auto") else str(version)
    if new == old:
        raise WorkspaceError(f"The next version must differ from the current one ({old}).")
    period = effective_period
    eff = declared.get("effective") or {}
    if period is None and isinstance(eff.get("period"), int):
        trial = constitution_settings(name).get("trial_periods")
        period = eff["period"] + int(trial) if isinstance(trial, int) and trial > 0 else None
    if period is not None and (isinstance(period, bool) or int(period) < 1):
        raise WorkspaceError("The effective period is a positive whole number.")
    return {"from": old, "to": new, "effective_period": None if period is None else int(period)}


def add_amendment(name: str, values: Dict[str, float], rationale: str = "",
                  version: Union[None, bool, str] = "auto", effective_period: Optional[int] = None,
                  evaluator: Optional[Evaluator] = None) -> dict:
    """Propose new values for `# @amend:` names, written in place so the comments stay.

    In a design that declares the amendment extension, the proposal is also the
    next version: the declared ``value:`` of each parameter follows the tagged
    lines, and, unless ``version`` is None or False, ``sos.version`` becomes
    ``version`` ("auto": the next number), ``amendment.previous`` the current
    version and ``amendment.effective.period`` ``effective_period`` (by default
    the current one plus the constitution's ``trial_periods``).
    ``evaluator(before, after)``, when given, is run on the proposal and its
    results recorded as its trial.
    """
    source = read(name)
    try:
        floats = {k: float(v) for k, v in values.items()}
        tagged = amend.set_values(source, floats)
        bump = _next_declaration(name, source, version, effective_period)
        new_source = amend.declare(tagged, floats, *((bump["to"], bump["from"], bump["effective_period"])
                                                     if bump else ()))
    except (TypeError, ValueError) as e:
        raise WorkspaceError(str(e)) from e
    const = constitution(name)
    gate = amend.check_amendment(source, new_source, const)
    changes = [ops.Change(f"Amended {n}: {amend.fmt(a)} → {amend.fmt(b)}", "Amendments", n)
               for n, a, b in gate.changed]
    proposal = ops.compare(source, new_source, changes)
    acceptable = proposal.acceptable and gate.ok
    proposal_id = _store(name, source, proposal, rationale, for_a_person=gate.for_a_person,
                         decided_by=gate.decided_by, gate=gate.reasons,
                         **({"version": bump} if bump else {}))
    trial = _evaluate(name, proposal_id, source, new_source, evaluator) if evaluator else None
    return {
        "proposal_id": proposal_id, "applied": False,
        "changes": [c.summary for c in changes],
        **({"version": bump} if bump else {}),
        **({"trial": trial} if trial else {}),
        "constitution": constitution_path(name).name if const is not None else None,
        "constitution_allows": gate.ok, "constitution_problems": gate.reasons,
        "decided_by_a_person": gate.for_a_person,
        **({"decided_by": gate.decided_by} if gate.decided_by else {}),
        "problems_introduced": [_finding(f) for f in proposal.introduced],
        "problems_resolved": [_finding(f) for f in proposal.resolved],
        "errors_after": proposal.errors_after, "acceptable": acceptable,
        "next": (
            "The constitution leaves " + ", ".join(
                f"{n} (decided by {gate.decided_by[n]})" if n in gate.decided_by else n
                for n in gate.for_a_person) + " to a person. Show them "
            "the change and what it does, ask for their decision and reason, and pass the reason "
            "(and any assumptions) to apply_proposal."
            if acceptable and gate.for_a_person else
            "Show the change to the person. Apply with apply_proposal only after they agree, "
            "and pass their reason so the decision is recorded."
            if acceptable else
            "This amendment does not pass (see constitution_problems or problems_introduced), "
            "so apply_proposal will refuse it. Revise it and propose again."),
    }


def add_reversion(name: str, to_version: str, rationale: str = "",
                  version: Union[None, bool, str] = "auto", effective_period: Optional[int] = None,
                  evaluator: Optional[Evaluator] = None) -> dict:
    """Propose one new version that restores the `# @amend:` values of an earlier version.

    The source of ``to_version`` is read from git (``CADL_WORKSPACE_GIT``).
    Every tagged value goes back to its value there, written in place, and
    the next version (``version`` "auto": the next number, or a version) is
    declared with ``amendment.previous`` the current version and
    ``amendment.reverts`` ``to_version``. The constitution's gate is applied
    with that version as the base, so the restored values are exempt from
    the one-name and one-step rules (their range is still checked), and
    ``cadl amend-diff current new --base that`` is run when the installed
    cadl can (0.3.44). The proposal is stored like that of ``add_amendment``,
    so ``apply_proposal``, ``record_trial``, ``run_trial`` and
    ``discard_proposal`` take it.
    """
    source = read(name)
    declared = amend.declared_version(source)
    if declared is None:
        raise WorkspaceError(f"`{name}` does not declare the amendment extension (an `amendment:` "
                             "block), so a reversion cannot be declared.")
    target = str(to_version or "").strip()
    if not target:
        raise WorkspaceError("Name the earlier version to revert to.")
    if target == declared["version"]:
        raise WorkspaceError(f"`{name}` is already version {target}; name an earlier version.")
    if version in (None, False, ""):
        raise WorkspaceError("A reversion is a new version: give `version` (\"auto\" numbers it).")
    base = source_at_version(name, target)
    restored, current = amend.values(base), amend.values(source)
    moves = {n: v for n, v in restored.items() if n in current and abs(current[n] - v) > 1e-9}
    if not moves:
        raise WorkspaceError(f"`{name}` already holds the `# @amend:` values of version {target}; "
                             "there is nothing to revert.")
    try:
        tagged = amend.set_values(source, moves)
        bump = _next_declaration(name, source, version, effective_period)
        bump = {**bump, "reverts": target}
        new_source = amend.declare(tagged, moves, bump["to"], bump["from"], bump["effective_period"],
                                   reverts=target)
    except (TypeError, ValueError) as e:
        raise WorkspaceError(str(e)) from e
    const = constitution(name)
    gate = amend.check_amendment(source, new_source, const, base=base)
    changes = [ops.Change(f"Reverted {n}: {amend.fmt(a)} → {amend.fmt(b)} (the value of version {target})",
                          "Amendments", n) for n, a, b in gate.changed]
    proposal = ops.compare(source, new_source, changes)
    acceptable = proposal.acceptable and gate.ok
    proposal_id = _store(name, source, proposal, rationale, for_a_person=gate.for_a_person,
                         decided_by=gate.decided_by, gate=gate.reasons, version=bump)
    trial = _evaluate(name, proposal_id, source, new_source, evaluator) if evaluator else None
    try:
        report = ds.amend_diff(source, new_source, base)
    except Exception as e:  # the check is reported, it does not stop the proposal
        report = {"ok": False, "errors": [f"amend-diff failed: {type(e).__name__}: {e}"]}
    checked = ds.amend_diff_takes_base()
    untouched = sorted(set(current) - set(restored))
    return {
        "proposal_id": proposal_id, "applied": False,
        "reverts": target, "restored": moves,
        "changes": [c.summary for c in changes],
        "version": bump,
        **({"trial": trial} if trial else {}),
        "constitution": constitution_path(name).name if const is not None else None,
        "constitution_allows": gate.ok, "constitution_problems": gate.reasons,
        "decided_by_a_person": gate.for_a_person,
        **({"decided_by": gate.decided_by} if gate.decided_by else {}),
        **({"amend_diff": {"ok": report.get("ok"), "errors": report.get("errors") or [],
                           "warnings": [f["text"] for f in report.get("findings") or []
                                        if f.get("level") == "warning"]}}
           if report is not None else {}),
        **({} if checked else {"note": "The installed cadl cannot check a reversion (amend-diff "
                                       "--base and amendment.reverts need cadl-lang 0.3.44)."}),
        **({"not_in_that_version": untouched} if untouched else {}),
        "problems_introduced": [_finding(f) for f in proposal.introduced],
        "problems_resolved": [_finding(f) for f in proposal.resolved],
        "errors_after": proposal.errors_after, "acceptable": acceptable,
        "next": (
            "The constitution leaves " + ", ".join(
                f"{n} (decided by {gate.decided_by[n]})" if n in gate.decided_by else n
                for n in gate.for_a_person) + " to a person. Show them "
            "the reversion and what it does, ask for their decision and reason, and pass the reason "
            "(and any assumptions) to apply_proposal."
            if acceptable and gate.for_a_person else
            "Show the reversion to the person. Apply with apply_proposal only after they agree, "
            "and pass their reason so the decision is recorded."
            if acceptable else
            "This reversion does not pass (see constitution_problems or problems_introduced), "
            "so apply_proposal will refuse it."),
    }


def _decisions_path(name: str) -> Path:
    return root() / f"{_check_name(name)}.decisions.md"


def decisions(name: str) -> List[dict]:
    return _load_state(name)["decisions"]


def _write_decisions(name: str, entries: List[dict]) -> None:
    lines = [f"# Decisions on {_check_name(name)}", "",
             "Each applied or rejected proposal, with what the assistant proposed and why, the "
             "results of its trial, and the person's decision and reason. Written by CADL "
             "Explorer; edit the design, not this file.", ""]
    for i, d in enumerate(entries, 1):
        outcome = d.get("outcome", "applied")
        lines += [f"## {i}. {d['time']} — proposal {d['proposal']}"
                  + (" (rejected)" if outcome == "rejected" else ""), ""]
        lines += [f"- {c}" for c in d["changes"]] + [""]
        version = d.get("version") or {}
        if version.get("to"):
            lines += [f"Version {version['to']} (previous {version['from']}"
                      + (f", reverts {version['reverts']}" if version.get("reverts") else "")
                      + (f", effective from period {version['effective_period']}"
                         if version.get("effective_period") is not None else "")
                      + (")." if outcome == "applied" else ") — not adopted."), ""]
        if d.get("for_a_person"):
            who = d.get("decided_by") or {}
            lines += ["Decided by a person under the constitution: "
                      + ", ".join(f"{n} ({who[n]})" if n in who else n for n in d["for_a_person"]) + ".", ""]
        trial = d.get("trial") or {}
        if trial.get("results") or trial.get("note"):
            verdict = "" if trial.get("passed") is None else (" — passed" if trial["passed"] else " — failed")
            lines += [f"**Trial**{' by ' + trial['by'] if trial.get('by') else ''}{verdict}:", ""]
            if trial.get("results"):
                lines += ["| result | value |", "|---|---|"] + [
                    f"| {k} | {v:g} |" for k, v in trial["results"].items()] + [""]
            if trial.get("note"):
                lines += [trial["note"], ""]
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
