# Example logs of the Violation Timeline

Each file is the NDJSON log of a run of the SoS-DSL reference runtime
(`cadl/runtime/engine.py` of raspimouse-swarm-simulator, unchanged) with the
IR `../sos_dsl_robot_delivery.ir.json`, written by

```bash
python -m cadl_sim.sos_dsl.runtime_runner --runtime ~/program/raspimouse-swarm-simulator \
    --out cadl_sim/sos_dsl/examples/violations
```

| File | Scenario | What it shows |
|---|---|---|
| `demo_accept_5s.ndjson` | `multi_robot_demo`, as it is | The five outcomes of the textbook; for robot-2-1, continued evaluation also records `deadline:accept` at 5100 ms |
| `demo_accept_1s.ndjson` | the same, `accept` deadline 1 s | robot-2-1 is still reported as `monitor:battery_guard` (550 ms); `deadline:accept` (1100 ms) is masked. The exchange of reasons seen in Unity is not reproduced here |
| `demo_accept_300ms.ndjson` | the same, `accept` deadline 300 ms | robot-2-1 is reported as `deadline:accept` (400 ms) although its battery dropped at 400 ms; `battery_guard` (550 ms) is masked — the case of Figure 1 of the research plan |
| `batch_example.ndjson` | the baseline scenario, 40 trials | `accept` 500 / 5000 ms × dwell in Assigned 100 / 700 ms × `battery_drop` / `ack_missing`, 5 trials each, seed 1 |

What is in a log
- Records without `track` are what the runtime itself writes; for the demo they
  equal the output of `multi_robot_demo --log` record for record.
- Records with `"track": "continued"` come from continued evaluation: a subclass
  of the runtime that records each violation and does not take the violation
  transition. Each `detected_by` is written the first time it matches.
- `"kind": "injection"` records are the ground truth. In the demo they restate
  the faults of its own scenario (`"source": "scenario"`): robot-1-1 never acks,
  robot-2-1's battery drops at 400 ms, robot-3-1's request deadline becomes
  4000 ms. robot-2-1 is also never acked; the scenario does that to keep it in
  Assigned, and it is not counted as a fault.
- `"kind": "run"` records say how to run the trial again.
