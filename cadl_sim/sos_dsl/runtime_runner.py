"""Run the SoS-DSL reference runtime with injected faults and continued evaluation.

The runtime itself (``cadl/runtime/engine.py`` of raspimouse-swarm-simulator)
is not changed and not copied: it is loaded from where it is
(``CADL_RUNTIME_PATH``, or a path given on the page). Two of its runtimes are
driven by the same scenario in lockstep:

* the runtime as it is, whose records are the *report*;
* a subclass that records each violation and then does not take the violation
  transition, so the instance goes on in the *assumed state* and every
  deadline and monitor keeps being evaluated (*continued evaluation*). Its
  records carry ``"track": "continued"``; each ``detected_by`` is written the
  first time it matches for an instance.

This is the "separate evaluator run alongside" of the research plan's table of
risks; when the runtime records continued evaluation itself, its log is read
by the same page without this module.

Faults are injected between the scenario and the two runtimes:

* ``battery_drop`` — from ``t_ms`` on, ``ROBOT.battery`` is ``level``;
* ``over_speed``   — from ``t_ms`` on, ``ROBOT.speed`` is ``speed``;
* ``ack_missing``  — from ``t_ms`` on, the event of transition ``transition``
  (default ``accept``) for the instance is dropped;
* ``ack_delay``    — the same event is delivered ``delay_ms`` later.

Command line (writes the bundled example logs)::

    python -m cadl_sim.sos_dsl.runtime_runner --runtime ~/program/raspimouse-swarm-simulator \\
        --out cadl_sim/sos_dsl/examples/violations
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import itertools
import json
import os
import random
import sys
import types
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Sequence

from .contract_tuning import apply_overrides
from .violation_trace import CONTINUED, expected_detections, find_transition, to_ndjson

ENV_VAR = "CADL_RUNTIME_PATH"
FAULTS = ("battery_drop", "over_speed", "ack_missing", "ack_delay")


# ---------------------------------------------------------------------------
# Finding the runtime
# ---------------------------------------------------------------------------

@dataclass
class RuntimeHandle:
    location: Path
    engine: types.ModuleType
    demo: Optional[types.ModuleType]

    @property
    def label(self) -> str:
        return str(self.location)


def _runtime_dir(path: Path) -> Optional[Path]:
    for cand in (path, path / "runtime", path / "cadl" / "runtime"):
        if (cand / "engine.py").is_file():
            return cand
    return None


_LOADED: Dict[str, RuntimeHandle] = {}


def load_runtime(path: Optional[str] = None) -> RuntimeHandle:
    """Load ``engine.py`` (and ``multi_robot_demo.py``) from ``path``.

    The modules are loaded under a private package name, so they do not clash
    with the ``cadl`` package of the compiler that the Explorer also uses.
    Raises FileNotFoundError when no runtime is there.
    """
    raw = path or os.environ.get(ENV_VAR) or ""
    if not raw:
        raise FileNotFoundError(f"no runtime path (set {ENV_VAR})")
    rdir = _runtime_dir(Path(raw).expanduser().resolve())
    if rdir is None:
        raise FileNotFoundError(f"no cadl/runtime/engine.py under {raw}")
    key = str(rdir)
    if key in _LOADED:
        return _LOADED[key]
    pkg = "_cadl_ext_runtime_" + hashlib.sha1(key.encode()).hexdigest()[:8]
    spec = importlib.util.spec_from_loader(pkg, loader=None, is_package=True)
    module = importlib.util.module_from_spec(spec)
    module.__path__ = [key]
    sys.modules[pkg] = module

    def sub(name: str) -> Optional[types.ModuleType]:
        file = rdir / f"{name}.py"
        if not file.is_file():
            return None
        s = importlib.util.spec_from_file_location(f"{pkg}.{name}", file)
        m = importlib.util.module_from_spec(s)
        sys.modules[f"{pkg}.{name}"] = m
        s.loader.exec_module(m)
        return m

    engine = sub("engine")
    try:
        demo = sub("multi_robot_demo")
    except Exception:           # a demo that does not load still leaves the engine
        demo = None
    handle = RuntimeHandle(rdir, engine, demo)
    _LOADED[key] = handle
    return handle


def try_load_runtime(path: Optional[str] = None) -> Optional[RuntimeHandle]:
    try:
        return load_runtime(path)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Continued evaluation: a subclass of the runtime
# ---------------------------------------------------------------------------

_CLASSES: Dict[int, type] = {}


def _continued_class(engine: types.ModuleType) -> type:
    if id(engine) in _CLASSES:
        return _CLASSES[id(engine)]

    class ContinuedStateMachine(engine.StateMachineEngine):
        def fire_transition(self, inst, transition, cause, t_ms):
            if cause in ("monitor", "deadline"):
                return False            # the violation transition is not taken
            return super().fire_transition(inst, transition, cause, t_ms)

    class ContinuedRuntime(engine.ContractRuntime):
        def __init__(self, ir):
            super().__init__(ir)
            self.sm = ContinuedStateMachine(self)
            self._seen = set()

        def _fire_state_jump(self, inst, target_state, cause, t_ms):
            if cause in ("monitor", "deadline"):
                return
            super()._fire_state_jump(inst, target_state, cause, t_ms)

        def _record_violation(self, ev):
            key = (ev.instance_id, ev.detected_by)
            if key in self._seen:
                return
            self._seen.add(key)
            super()._record_violation(ev)

    _CLASSES[id(engine)] = ContinuedRuntime
    return ContinuedRuntime


# ---------------------------------------------------------------------------
# Injection between the scenario and the runtimes
# ---------------------------------------------------------------------------

@dataclass
class Injection:
    instance_id: str
    t_ms: int
    fault: str
    params: Dict[str, Any] = field(default_factory=dict)
    expected: Optional[List[str]] = None

    def record(self) -> dict:
        rec = {"kind": "injection", "instance_id": self.instance_id, "t_ms": self.t_ms,
               "fault": self.fault, "params": dict(self.params)}
        if self.expected is not None:
            rec["expected"] = list(self.expected)
        rec["expected"] = expected_detections(rec)
        return rec


class Harness:
    """What the scenario drives: both runtimes, with the faults in between."""

    def __init__(self, handle: RuntimeHandle, ir: dict, injections: Sequence[Injection],
                 contract_id: str = "DELIVERY_SLA"):
        self.actual = handle.engine.ContractRuntime(ir)
        self.continued = _continued_class(handle.engine)(ir)
        self.ir = ir
        self.contract_id = contract_id
        self.injections = sorted(injections, key=lambda i: i.t_ms)
        self.active: List[Injection] = []
        self.pending: List[Injection] = list(self.injections)
        self.held: List[tuple] = []          # (due, name, payload, instance_id)
        self.injected: List[dict] = []
        self.now = 0

    # what the scenario calls --------------------------------------------
    def open_instance(self, contract_id, instance_id, t_ms=0):
        self.contract_id = contract_id
        self.continued.open_instance(contract_id, instance_id, t_ms=t_ms)
        return self.actual.open_instance(contract_id, instance_id, t_ms=t_ms)

    def update_world(self, snapshot):
        self.actual.update_world(snapshot)
        self.continued.update_world(snapshot)

    def update_instance_world(self, instance_id, snapshot):
        for rt in (self.actual, self.continued):
            rt.update_instance_world(instance_id, snapshot)
        self._pin(instance_id)

    def post_event(self, name, payload=None, t_ms=0, instance_id=None):
        for inj in self.active:
            if inj.instance_id != instance_id or inj.fault not in ("ack_missing", "ack_delay"):
                continue
            if name != self._event_of(inj):
                continue
            if inj.fault == "ack_missing":
                return
            self.held.append((t_ms + int(inj.params.get("delay_ms", 2000)), name, payload,
                              instance_id))
            return
        self._post(name, payload, t_ms, instance_id)

    def tick(self, now_ms):
        self.now = now_ms
        while self.pending and self.pending[0].t_ms <= now_ms:
            inj = self.pending.pop(0)
            self.active.append(inj)
            self.injected.append(dict(inj.record(), t_ms=inj.t_ms))
            self._pin(inj.instance_id)
        due = [h for h in self.held if h[0] <= now_ms]
        self.held = [h for h in self.held if h[0] > now_ms]
        for _, name, payload, iid in due:
            self._post(name, payload, now_ms, iid)
        self.actual.tick(now_ms)
        self.continued.tick(now_ms)

    def __getattr__(self, name):        # e.g. ``rt.log`` in a scenario
        return getattr(self.actual, name)

    # ------------------------------------------------------------------
    def _post(self, name, payload, t_ms, instance_id):
        self.actual.post_event(name, payload, t_ms=t_ms, instance_id=instance_id)
        self.continued.post_event(name, payload, t_ms=t_ms, instance_id=instance_id)

    def _event_of(self, inj: Injection) -> str:
        tr = find_transition(self.ir, self.contract_id, inj.params.get("transition", "accept"))
        return (tr or {}).get("on") or ""

    def _pin(self, instance_id):
        """Faults on the world stay, whatever the scenario writes afterwards."""
        for inj in self.active:
            if inj.instance_id != instance_id:
                continue
            if inj.fault == "battery_drop":
                snap = {"ROBOT": {"battery": float(inj.params.get("level", 10.0))}}
            elif inj.fault == "over_speed":
                snap = {"ROBOT": {"speed": float(inj.params.get("speed", 3.0))}}
            else:
                continue
            self.actual.update_instance_world(instance_id, snap)
            self.continued.update_instance_world(instance_id, snap)

    def records(self) -> List[dict]:
        rep = [dict(r) for r in self.actual.log]
        con = [dict(r, track=CONTINUED) for r in self.continued.log]
        out = rep + con + self.injected
        return sorted(out, key=lambda r: int(r.get("t_ms") or 0))


# ---------------------------------------------------------------------------
# Scenarios
# ---------------------------------------------------------------------------

@dataclass
class BaselineParams:
    """A scenario without faults: every robot is assigned, acks, delivers.

    ``dwell_ms`` is the time an instance stays Assigned before the ack, like
    ``Assigned Dwell Ms`` of PilotContractBridge in Unity.
    """
    robots: int = 5
    assign_at_ms: int = 100
    stagger_ms: int = 500
    dwell_ms: int = 700
    transit_after_ms: int = 100
    deliver_after_ms: int = 2000
    request_deadline_ms: int = 60000
    battery: float = 90.0
    speed: float = 1.0
    step_ms: int = 50

    def robot_ids(self) -> List[str]:
        return [f"robot-{i}-1" for i in range(self.robots)]

    def times(self, i: int) -> Dict[str, int]:
        a = self.assign_at_ms + i * self.stagger_ms
        k = a + self.dwell_ms
        tr = k + self.transit_after_ms
        return {"assign": a, "accept": k, "start_delivery": tr,
                "complete": tr + self.deliver_after_ms}


def _on(ir: dict, contract_id: str, transition_id: str, default: str) -> str:
    return (find_transition(ir, contract_id, transition_id) or {}).get("on") or default


def _max_deadline(ir: dict, contract_id: str) -> int:
    c = next((c for c in (ir.get("institution") or {}).get("contracts") or []
              if c.get("id") == contract_id), {})
    ds = [int(t.get("deadline_ms") or 0) for t in (c.get("lifecycle") or {}).get("transitions") or []]
    return max(ds + [0])


def run_baseline(h: Harness, ir: dict, p: BaselineParams, contract_id: str = "DELIVERY_SLA") -> int:
    """Drive the harness with the baseline scenario; returns the end time."""
    evs = {tid: _on(ir, contract_id, tid, tid)
           for tid in ("assign", "accept", "start_delivery", "complete")}
    script: Dict[int, List[Callable[[], None]]] = {}

    def at(t: int, fn: Callable[[], None]) -> None:
        t = int(round(t / p.step_ms) * p.step_ms)
        script.setdefault(t, []).append(fn)

    def post(tid: str, iid: str, t: int):
        return lambda: h.post_event(evs[tid], t_ms=t, instance_id=iid)

    ids = p.robot_ids()
    last = 0
    for i, iid in enumerate(ids):
        for tid, t in p.times(i).items():
            at(t, post(tid, iid, int(round(t / p.step_ms) * p.step_ms)))
            last = max(last, t)
    end = last + max(_max_deadline(ir, contract_id), 1000) + 1000
    for iid in ids:
        h.open_instance(contract_id, iid, t_ms=0)
        h.update_instance_world(iid, {"ROBOT": {"battery": p.battery, "speed": p.speed},
                                      "request": {"deadline": p.request_deadline_ms},
                                      "now": 0})
    for now in range(0, end + 1, p.step_ms):
        for iid in ids:
            h.update_instance_world(iid, {"now": now})
        for fn in script.get(now, []):
            fn()
        if now > 0:
            h.tick(now)
    return end


# The faults the demo's own scenario puts in, as its docstring describes them.
# robot-2-1 is never acked so that it stays Assigned for battery_guard; that is
# how the scenario holds it there, and it is not counted as a fault.
DEMO_TRUTH = [
    {"instance_id": "robot-1-1", "t_ms": 100, "fault": "ack_missing",
     "params": {"transition": "accept"}},
    {"instance_id": "robot-2-1", "t_ms": 400, "fault": "battery_drop", "params": {"level": 10.0}},
    {"instance_id": "robot-3-1", "t_ms": 400, "fault": "delivery_stall",
     "params": {"request_deadline": 4000}},
]


def run_demo(h: Harness, handle: RuntimeHandle) -> int:
    if handle.demo is None or not hasattr(handle.demo, "run_scenario"):
        raise RuntimeError("multi_robot_demo.run_scenario is not available")
    handle.demo.run_scenario(h)
    return int(getattr(handle.demo, "END_MS", h.now))


# ---------------------------------------------------------------------------
# A run, and many
# ---------------------------------------------------------------------------

def run_trial(handle: RuntimeHandle, base_ir: dict, *, scenario: str = "baseline",
              params: Optional[BaselineParams] = None,
              injections: Sequence[Injection] = (), ir_overrides: Sequence[dict] = (),
              trial: str = "run", condition: Optional[Dict[str, Any]] = None,
              ir_name: str = "") -> List[dict]:
    """The records of one run, beginning with its ``run`` record."""
    ir = apply_overrides(base_ir, ir_overrides)
    params = params or BaselineParams()
    h = Harness(handle, ir, injections)
    if scenario == "demo":
        end = run_demo(h, handle)
        truth = [dict(t, kind="injection", source="scenario") for t in DEMO_TRUTH]
        for t in truth:
            t["expected"] = expected_detections(t)
    else:
        end = run_baseline(h, ir, params)
        truth = []
    run = {
        "kind": "run", "trial": trial, "scenario": scenario, "ir": ir_name,
        "ir_overrides": [dict(o) for o in ir_overrides],
        "params": asdict(params) if scenario == "baseline" else {},
        "injections": [asdict(i) for i in injections],
        "condition": dict(condition or {}), "end_ms": end,
        "runtime": "cadl.runtime.engine (unchanged) + Explorer continued evaluation",
    }
    recs = sorted(h.records() + truth, key=lambda r: int(r.get("t_ms") or 0))
    return [run] + [dict(r, trial=trial) for r in recs]


def rerun(handle: RuntimeHandle, base_ir: dict, run: dict, extra_overrides: Sequence[dict],
          trial: Optional[str] = None) -> List[dict]:
    """The same run again (scenario, injections), with more overrides of the IR."""
    from .contract_tuning import merge_overrides
    params = BaselineParams(**run["params"]) if run.get("params") else None
    return run_trial(handle, base_ir, scenario=run.get("scenario", "baseline"), params=params,
                     injections=[Injection(**i) for i in run.get("injections") or []],
                     ir_overrides=merge_overrides(run.get("ir_overrides") or [], extra_overrides),
                     trial=trial or str(run.get("trial")), condition=run.get("condition"),
                     ir_name=run.get("ir", ""))


def random_injection(rng: random.Random, fault: str, p: BaselineParams, ir: dict,
                     contract_id: str = "DELIVERY_SLA", **fault_params) -> Injection:
    """A fault at a random robot, at a random time where it can matter.

    battery_drop: before the robot's ack (its monitor watches Assigned);
    over_speed: while it delivers; ack faults: from its assignment.
    """
    i = rng.randrange(p.robots)
    iid = p.robot_ids()[i]
    t = p.times(i)

    def pick(lo: int, hi: int) -> int:
        slots = list(range(lo, max(lo + p.step_ms, hi), p.step_ms))
        return rng.choice(slots)

    if fault == "battery_drop":
        return Injection(iid, pick(0, t["accept"]), fault,
                         {"level": fault_params.get("level", 10.0)})
    if fault == "over_speed":
        return Injection(iid, pick(t["start_delivery"], t["complete"]), fault,
                         {"speed": fault_params.get("speed", 3.0)})
    if fault == "ack_missing":
        return Injection(iid, t["assign"], fault, {"transition": "accept"})
    if fault == "ack_delay":
        delay = int(fault_params.get("delay_ms", 2000))
        tr = find_transition(ir, contract_id, "accept") or {}
        deadline = tr.get("deadline_ms")
        late = deadline is not None and p.dwell_ms + delay > int(deadline)
        # A delay the deadline tolerates is not a violation: nothing is expected.
        return Injection(iid, t["assign"], fault, {"transition": "accept", "delay_ms": delay},
                         expected=None if late else [])
    raise ValueError(f"unknown fault {fault}")


def condition_overrides(cond: Dict[str, Any], contract_id: str = "DELIVERY_SLA") -> List[dict]:
    """Overrides of the IR from a condition such as {"deadline:accept": 1000}."""
    out = []
    for k, v in cond.items():
        kind, _, name = k.partition(":")
        if kind == "deadline":
            out.append({"contract": contract_id, "kind": "transition", "id": name,
                        "field": "deadline_ms", "value": int(v)})
        elif kind == "period":
            out.append({"contract": contract_id, "kind": "monitor", "id": name,
                        "field": "sampling_period_ms", "value": int(v)})
    return out


def batch(handle: RuntimeHandle, base_ir: dict, *, sweep: Dict[str, Sequence[Any]],
          faults: Sequence[str], n: int, seed: int = 1, params: Optional[BaselineParams] = None,
          ir_name: str = "", progress: Optional[Callable[[float], None]] = None) -> List[dict]:
    """Every combination of the sweep, every fault, ``n`` trials each.

    Keys of ``sweep``: ``deadline:<transition>``, ``period:<monitor>`` (the IR)
    and ``dwell_ms`` (the scenario).
    """
    base = params or BaselineParams()
    keys = list(sweep)
    combos = list(itertools.product(*[list(sweep[k]) for k in keys])) or [()]
    total = len(combos) * len(faults) * n
    out: List[dict] = []
    done = 0
    rng = random.Random(seed)
    for ci, combo in enumerate(combos):
        cond = dict(zip(keys, combo))
        p = BaselineParams(**{**asdict(base), **({"dwell_ms": int(cond["dwell_ms"])}
                                                  if "dwell_ms" in cond else {})})
        overrides = condition_overrides(cond)
        ir = apply_overrides(base_ir, overrides)
        for fault in faults:
            for k in range(n):
                inj = random_injection(rng, fault, p, ir)
                out += run_trial(handle, base_ir, params=p, injections=[inj],
                                 ir_overrides=overrides, trial=f"c{ci}-{fault}-{k}",
                                 condition={**cond, "fault": fault}, ir_name=ir_name)
                done += 1
                if progress:
                    progress(done / total)
    return out


# ---------------------------------------------------------------------------
# Command line: the bundled examples
# ---------------------------------------------------------------------------

EXAMPLE_IR = Path(__file__).parent / "examples" / "sos_dsl_robot_delivery.ir.json"


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--runtime", default=None, help=f"path of the runtime (or ${ENV_VAR})")
    ap.add_argument("--ir", type=Path, default=EXAMPLE_IR)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--n", type=int, default=5)
    ap.add_argument("--seed", type=int, default=1)
    a = ap.parse_args(argv)
    handle = load_runtime(a.runtime)
    ir = json.loads(a.ir.read_text(encoding="utf-8"))
    a.out.mkdir(parents=True, exist_ok=True)
    acc = lambda v: [{"contract": "DELIVERY_SLA", "kind": "transition", "id": "accept",  # noqa: E731
                      "field": "deadline_ms", "value": v}]
    for name, ov in (("demo_accept_5s", []), ("demo_accept_1s", acc(1000)),
                     ("demo_accept_300ms", acc(300))):
        recs = run_trial(handle, ir, scenario="demo", ir_overrides=ov, trial=name,
                         ir_name=a.ir.name)
        (a.out / f"{name}.ndjson").write_text(to_ndjson(recs), encoding="utf-8")
    recs = batch(handle, ir, sweep={"deadline:accept": [500, 5000], "dwell_ms": [100, 700]},
                 faults=["battery_drop", "ack_missing"], n=a.n, seed=a.seed, ir_name=a.ir.name)
    (a.out / "batch_example.ndjson").write_text(to_ndjson(recs), encoding="utf-8")
    print(f"wrote {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
