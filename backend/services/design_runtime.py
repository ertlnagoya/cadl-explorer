"""The commands of cadl that look at a design from outside the editor.

- `norms_eval`: the verdicts of the norms of one contract instance on a recorded
  trace (`cadl norms-eval`, Appendix F, F.5), also with the versions of the run
  (amendment 0.1, Appendix K, K.11; cadl 0.3.34).
- `metrics_eval`: measured values of metrics judged against the target in force
  (`cadl metrics-eval`, cadl 0.3.34).
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
import os
import re
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


def _with_trace(command: str, source: str, trace: str, extra: List[str],
                versions=(), flag: str = "--trace") -> dict:
    """Run `cadl COMMAND` on the design with the trace (or measurements, `flag`) in a
    temporary file; `versions` are (name, source) of the other versions of the run,
    passed as `--version FILE` each."""
    read_trace(trace)
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "trace.jsonl"
        path.write_text(trace, encoding="utf-8")
        audit = Path(tmp) / "audit.jsonl"
        args = [command, flag, str(path), "--format", "json"] + [
            str(audit) if a == "{audit}" else a for a in extra]
        names = {}
        for k, (name, text) in enumerate(versions or (), 1):
            try:
                ds._parse(text)         # the input limits of the page
            except Exception as e:      # noqa: BLE001 - said on the page
                return {"failure": f"{name}: {str(e).splitlines()[0] if str(e) else type(e).__name__}"
                                   "; nothing is claimed.", "transient": False,
                        "stdout": "", "stderr": "", "audit": ""}
            version = Path(tmp) / f"v{k}.cadl"
            version.write_text(text, encoding="utf-8")
            names[str(version)] = str(name)
            args += ["--version", str(version)]
        run = ds._run_cadl(args, source, RUN_TIMEOUT, workdir=tmp)
        label = "the measurements" if flag == "--measurements" else "the trace"
        real = os.path.realpath(tmp)
        for key in ("stdout", "stderr"):
            for version, name in names.items():
                run[key] = run[key].replace(os.path.realpath(version), name).replace(version, name)
            for p in (os.path.realpath(path), str(path)):
                run[key] = run[key].replace(p, label)
            run[key] = run[key].replace(os.path.realpath(audit), "audit.jsonl").replace(
                str(audit), "audit.jsonl")
        # _run_cadl has taken the directory out of standard error
        err = run["stderr"].replace(real, "").replace(tmp, "")
        err = re.sub(r"(?:/private)?/trace\.jsonl\b", label, err)
        for version, name in names.items():
            err = re.sub(r"(?:/private)?/" + re.escape(Path(version).name) + r"\b", name, err)
        run["stderr"] = err
        run["audit"] = audit.read_text(encoding="utf-8") if audit.exists() else ""
    return run


def time_base(period: Optional[str] = None, origin: Optional[float] = None,
              epoch: bool = False) -> List[str]:
    """The options of the time base of the versions (K.11): --period, --origin, --epoch."""
    args = []
    if period and str(period).strip():
        args += ["--period", str(period).strip()]
    if origin:
        value = float(origin)
        args += ["--origin", str(int(value)) if value.is_integer() else repr(value)]
    if epoch:
        args.append("--epoch")
    return args


def supports_run_versions() -> bool:
    """Whether cadl applies the versions of a run (norms-eval --version, metrics-eval;
    cadl 0.3.34)."""
    import importlib.util
    return importlib.util.find_spec("cadl.metrics_eval") is not None


def _report(run: dict, command: str) -> dict:
    if run["failure"]:
        return {"failure": run["failure"], "transient": run.get("transient", False)}
    try:
        report = json.loads(run["stdout"])
    except json.JSONDecodeError:
        message = ds._usage_error(run["stderr"]).replace("Error: ", "").strip()
        return {"failure": (message or f"`cadl {command}` gave no report") + "; nothing is claimed."}
    # the warnings that cadl writes to standard error only, such as a version
    # applied without a time base or an amendment applied as a switch
    notes = [line[len("Warning: "):] if line.startswith("Warning: ") else line
             for line in (run.get("stderr") or "").splitlines()
             if line.lower().startswith(("warning", "note"))]
    return {"report": report, "failure": None, "notes": notes}


def norms_eval(source: str, trace: str, contract: Optional[str] = None, versions=(),
               period: Optional[str] = None, origin: Optional[float] = None,
               epoch: bool = False) -> dict:
    """{"report": the JSON of `cadl norms-eval`, "failure"}: the verdict of each norm
    of the contract instance that the trace records, with the history of its states.
    `versions` (name, source) and the time base apply the versions of the run (K.11);
    the report then has the key `versions`."""
    extra = ["--contract", contract] if contract else []
    if versions or period or origin or epoch:
        extra += time_base(period, origin, epoch)
    out = _report(_with_trace("norms-eval", source, trace, extra, versions), "norms-eval")
    if out.get("report"):
        out["report"]["trace"] = "the trace"
    return out


def metrics_eval(source: str, measurements: str, versions=(), period: Optional[str] = None,
                 origin: Optional[float] = None, epoch: bool = False) -> dict:
    """{"report": the JSON of `cadl metrics-eval`, "failure"}: each measurement judged
    under the declaration of its metric in the version in force at the start of its
    window: met, not met, or not judged with the reason."""
    extra = time_base(period, origin, epoch)
    out = _report(_with_trace("metrics-eval", source, measurements, extra, versions,
                              flag="--measurements"), "metrics-eval")
    if out.get("report"):
        out["report"]["file"], out["report"]["measurements_file"] = "the design", "the measurements"
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


MAX_AUDIT_BYTES = 8 << 20


def audit_verify(source: str, audit: str) -> dict:
    """{"report": the JSON of `cadl audit-verify --source`, "valid", "failure"}: whether
    the hash chain of an audit log holds and the log was written for this design. It
    takes one of the slots of design_service._run_cadl while it runs."""
    if len(audit.encode("utf-8")) > MAX_AUDIT_BYTES:
        return {"failure": f"The audit log is larger than {MAX_AUDIT_BYTES >> 20} MB; nothing is claimed."}
    if not ds._RUN_SLOTS.acquire(timeout=1):
        return {"failure": ds.BUSY, "transient": True}
    try:
        return _audit_verify(source, audit)
    finally:
        ds._RUN_SLOTS.release()


def _audit_verify(source: str, audit: str) -> dict:
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
        return {"ok": False, "text": run["failure"], "transient": run.get("transient", False)}
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
            found = re.search(r"version\s*:?\s*(\S+)", out, re.I)
            prism = found.group(1) if found else "installed"
        except Exception:
            prism = "installed"
    return {"cadl": cadl, "prism": prism}
