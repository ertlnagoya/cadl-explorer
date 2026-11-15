"""Long checks of cadl in the background, so that the page stays usable.

A job runs one function (such as design_service.norms_check) in a thread of the
server and keeps its result. The page asks for the state of the job on each
run and can cancel it: the function gets a threading.Event as `cancel`, which
stops the subprocess of cadl. Results of finished jobs are kept for
MAX_KEPT jobs, keyed by the options they ran with, so that a second request
with the same options gets the same result without running again.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

MAX_KEPT = 32


@dataclass
class Job:
    id: str
    kind: str
    key: Any
    started: float = field(default_factory=time.time)
    finished: Optional[float] = None
    result: Optional[dict] = None
    error: Optional[str] = None
    cancel: threading.Event = field(default_factory=threading.Event)

    @property
    def running(self) -> bool:
        return self.finished is None

    @property
    def seconds(self) -> float:
        return (self.finished or time.time()) - self.started


_lock = threading.Lock()
_jobs: "OrderedDict[str, Job]" = OrderedDict()
_by_key: Dict[Any, str] = {}


def _forget_old():
    while len(_jobs) > MAX_KEPT:
        old_id, old = next(iter(_jobs.items()))
        if old.running:
            break
        _jobs.pop(old_id)
        if _by_key.get((old.kind, old.key)) == old_id:
            _by_key.pop((old.kind, old.key), None)


def start(kind: str, key: Any, function: Callable[..., dict], *args, **kwargs) -> Job:
    """The job of `kind` for `key`: the one that runs or ran with it, unless that one
    failed in a way that may not recur (`transient`) or was cancelled, else a new
    one that calls function(*args, cancel=..., **kwargs) in a thread."""
    with _lock:
        known = _jobs.get(_by_key.get((kind, key), ""))
        if known is not None and (known.running or not (
                known.cancel.is_set() or known.error
                or (known.result or {}).get("transient"))):
            return known
        job = Job(id=uuid.uuid4().hex, kind=kind, key=key)
        _jobs[job.id] = job
        _by_key[(kind, key)] = job.id
        _forget_old()

    def run():
        try:
            job.result = function(*args, cancel=job.cancel, **kwargs)
        except Exception as e:      # reported on the page, never raised in the thread
            job.error = str(e) or e.__class__.__name__
        finally:
            job.finished = time.time()

    if os.environ.get("CADL_JOBS_SYNC") == "1":     # the page tests: inline
        run()
    else:
        threading.Thread(target=run, name=f"cadl-{kind}", daemon=True).start()
    return job


def get(job_id: Optional[str]) -> Optional[Job]:
    with _lock:
        return _jobs.get(job_id or "")


def find(kind: str, key: Any) -> Optional[Job]:
    """The job of `kind` that ran or runs with `key`, if it is still kept."""
    with _lock:
        return _jobs.get(_by_key.get((kind, key), ""))


def cancel(job_id: Optional[str]) -> None:
    job = get(job_id)
    if job is not None and job.running:
        job.cancel.set()


def clear() -> None:
    """Forget every job that has finished (the tests)."""
    with _lock:
        for job_id in [i for i, j in _jobs.items() if not j.running]:
            job = _jobs.pop(job_id)
            if _by_key.get((job.kind, job.key)) == job_id:
                _by_key.pop((job.kind, job.key), None)
