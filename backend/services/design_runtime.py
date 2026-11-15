"""The commands of cadl that look at a design from outside the editor.

- `norms_eval`: the verdicts of the norms of one contract instance on a recorded
  trace (`cadl norms-eval`, Appendix F, F.5).
- `agents_run`: a recorded sequence of messages replayed through the enforcement
  point of the agent governance extension (`cadl agents-run`, Appendix G), with
  its audit log, and `audit_verify` for such a log (`cadl audit-verify`, G.8).
- `sim_validate`: whether the design lowers to a valid simulator IR
  (`cadl sim-validate`).
- `toolchain`: the versions of cadl and PRISM that run here.

Each command runs in the bounded subprocess of design_service._run_cadl. A
trace is the JSON Lines format of cadl; it is checked to be JSON Lines and
small before it is written to a temporary file.
"""

from __future__ import annotations

import functools
import json
import subprocess
import tempfile
from pathlib import Path
from typing import List, Optional

from backend.services import design_service as ds

MAX_TRACE_BYTES = 2 << 20
MAX_TRACE_LINES = 20000
RUN_TIMEOUT = 60.0


class TraceError(ValueError):
    """A trace that cannot be used; the message says why."""


def read_trace(data: bytes | str) -> List[dict]:
    """The records of a JSON Lines trace, or TraceError."""
    if isinstance(data, bytes):
        if len(data) > MAX_TRACE_BYTES:
            raise TraceError(f"The trace is larger than {MAX_TRACE_BYTES >> 20} MB.")
        try:
            data = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            raise TraceError("The trace is not UTF-8 text.") from None
    records = []
    for number, line in enumerate(data.splitlines(), 1):
        if not line.strip():
            continue
        if len(records) >= MAX_TRACE_LINES:
            raise TraceError(f"The trace has more than {MAX_TRACE_LINES} records.")
        try:
            record = json.loads(line)
        except json.JSONDecodeError as e:
            raise TraceError(f"Line {number} is not JSON: {e.msg}.") from None
        if not isinstance(record, dict):
            raise TraceError(f"Line {number} is not a JSON object.")
        records.append(record)
    if not records:
        raise TraceError("The trace has no records.")
    return records


def _with_trace(command: str, source: str, trace: str, extra: List[str]) -> dict:
    read_trace(trace)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace.jsonl"
        path.write_text(trace, encoding="utf-8")
        audit = Path(tmp) / "audit.jsonl"
        args = [command, "--trace", str(path), "--format", "json"] + [
            str(audit) if a == "{audit}" else a for a in extra]
        run = ds._run_cadl(args, source, RUN_TIMEOUT, workdir=tmp)
        run["stdout"] = run["stdout"].replace(str(path), "the trace").replace(str(audit), "audit.jsonl")
        run["stderr"] = run["stderr"].replace(str(path), "the trace").replace(tmp, "")
        run["audit"] = audit.read_text(encoding="utf-8") if audit.exists() else ""
    return run


def _report(run: dict, command: str) -> dict:
    if run["failure"]:
        return {"failure": run["failure"]}
    try:
        report = json.loads(run["stdout"])
    except json.JSONDecodeError:
        message = ds._usage_error(run["stderr"]).replace("Error: ", "").strip()
        return {"failure": (message or f"`cadl {command}` gave no report") + "; nothing is claimed."}
    return {"report": report, "failure": None}


def norms_eval(source: str, trace: str, contract: Optional[str] = None) -> dict:
    """{"report": the JSON of `cadl norms-eval`, "failure"}: the verdict of each norm
    of the contract instance that the trace records, with the history of its states."""
    extra = ["--contract", contract] if contract else []
    out = _report(_with_trace("norms-eval", source, trace, extra), "norms-eval")
    if out.get("report"):
        out["report"]["trace"] = "the trace"
    return out


def agents_run(source: str, trace: str) -> dict:
    """{"report": the JSON of `cadl agents-run`, "audit": the audit log (JSON Lines),
    "failure"}: every message of the trace decided at the enforcement point."""
    run = _with_trace("agents-run", source, trace, ["--audit", "{audit}"])
    out = _report(run, "agents-run")
    if out.get("report"):
        out["report"]["file"], out["report"]["trace"] = "the design", "the trace"
        out["audit"] = run["audit"]
    return out


def audit_verify(source: str, audit: str) -> dict:
    """{"report": the JSON of `cadl audit-verify --source`, "valid", "failure"}: whether
    the hash chain of an audit log holds and the log was written for this design."""
    with tempfile.TemporaryDirectory() as tmp:
        log = Path(tmp) / "audit.jsonl"
        log.write_text(audit, encoding="utf-8")
        design = Path(tmp) / "design.cadl"
        design.write_text(source, encoding="utf-8")
        # audit-verify takes the log first; the design is passed with --source
        run = _run_plain(["audit-verify", str(log), "--source", str(design), "--format", "json"],
                         tmp)
    out = _report(run, "audit-verify")
    if out.get("report") is not None:
        out["valid"] = run["status"] == 0
    return out


def _run_plain(command: List[str], cwd: str) -> dict:
    """A short cadl command whose first argument is not the design."""
    import sys
    try:
        proc = subprocess.run([sys.executable, "-c", "import sys; from cadl.cli import main; "
                               "sys.exit(main())", *command], capture_output=True,
                              text=True, timeout=RUN_TIMEOUT, cwd=cwd)
    except subprocess.TimeoutExpired:
        return {"status": None, "stdout": "", "stderr": "",
                "failure": f"`cadl {command[0]}` did not finish within {RUN_TIMEOUT:g} s; "
                           "nothing is claimed.", "transient": True}
    return {"status": proc.returncode, "stdout": proc.stdout.replace(cwd, ""),
            "stderr": proc.stderr.replace(cwd, ""), "failure": None, "transient": False}


def sim_validate(source: str) -> dict:
    """{"ok", "text"}: what `cadl sim-validate` says about the design."""
    run = ds._run_cadl(["sim-validate"], source, RUN_TIMEOUT)
    if run["failure"]:
        return {"ok": False, "text": run["failure"]}
    text = (run["stdout"] + run["stderr"]).strip()
    return {"ok": run["status"] == 0, "text": text}


@functools.lru_cache(maxsize=1)
def toolchain() -> dict:
    """{"cadl": version or None, "prism": version or None} of the tools that run here."""
    try:
        from importlib.metadata import version
        cadl = version("cadl-lang")
    except Exception:
        cadl = None
    prism = None
    try:
        from cadl.norms_check import find_checker
        checker = find_checker()
    except Exception:
        checker = None
    if checker:
        try:
            out = subprocess.run([str(checker), "-version"], capture_output=True, text=True,
                                 timeout=30).stdout
            prism = next((line.split("version", 1)[1].strip() for line in out.splitlines()
                          if "version" in line.lower()), "installed")
        except (OSError, subprocess.SubprocessError):
            prism = "installed"
    return {"cadl": cadl, "prism": prism}
