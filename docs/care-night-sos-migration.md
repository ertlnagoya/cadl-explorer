# Dropping the care-night-sos workarounds (cadl-explorer 1.11.0)

care-night-sos experiment 6 (`experiments/e6_explorer_lifecycle.py`,
`carenight/explorer_bridge.py`) runs committee A's amendment loop through
cadl-explorer 1.10.1 and works around six gaps. In 1.11.0 the explorer closes
them. This note says what each workaround can become. Nothing in
care-night-sos has been changed.

With the new API, replaying committee A's three adoptions (`round_interval_min`
120 → 180 → 240, `staff_per_transfer` 2 → 1) from `cadl/care_night_v2.cadl`
gives texts identical, byte for byte, to `runs/A_efficiency/care_night_v2.1.cadl`
to `v2.3.cadl`, and `amend_diff` passes for each step.

| # | Gap | 1.10.1 workaround in e6 | 1.11.0 |
|---|-----|-------------------------|--------|
| 1 | No trial hook | `council._eval` runs beside the explorer; the results survive only as text in `reason` (`_reason`) | `add_amendment(name, values, rationale, evaluator=f)` calls `f(before, after)` and keeps the numbers it returns (`{"results": {...}, "passed": bool, "note": str}`) with the proposal. Over MCP: `record_trial(name, proposal_id, results, passed, note)`, or `run_trial` with `CADL_TRIAL_COMMAND`. The trial goes into `review.json` and `<name>.decisions.md` (a table) and the commit message |
| 2 | Rejections are lost | `discard_proposal(NAME, pid)` deletes the proposal; the trial gates survive only in e6's JSON | `discard_proposal(name, pid, reason=..., assumptions=...)` records the proposal with `outcome: "rejected"`, its reason, its trial and its version, and commits it. `record=False` drops a proposal without a record |
| 3 | No version update | `rewrite_cadl(...)` plus a second `workspace.write` (a second commit), and `explorer_gate_with` to show K-9 | `add_amendment` (and `propose_amendment`) writes `sos.version` (`version="auto"`: 2 → 2.1 → 2.2), `amendment.previous`, `amendment.effective.period` (the current period plus the constitution's `trial_periods`, or `effective_period=`) and the declared `value:` in the same proposal. The proposal passes K-9, `apply_proposal` is the only commit, and `cadl amend-diff` passes |
| 4 | cadl 0.3.14 | `explorer_gate_with(project_python(), ...)` to judge with the project's cadl | `requirements.txt` asks for `cadl-lang>=0.3.43`. Run `pip install -r requirements.txt` (or `uv pip install`) in the explorer's venv. `amend_diff` then reads amendment 0.1 |
| 5 | `decided_by` ignored | none (sensor_coverage was shown as decided by the checks) | `needs_human` reads `decided_by:`: anyone but `COUNCIL` decides the change themself. `list_amendables` gives the party (`FACILITY_MANAGER`), `propose_amendment` returns `decided_by` (`{"sensor_coverage": "FACILITY_MANAGER"}`), and `apply_proposal` needs a reason |
| 6 | History hidden | `ex.workspace.history` plus `git show` to pull each version out (`from_git`, `git_versions`), then `amend.history` and `amend.movement` by hand; the constitution is not committed | `workspace.version_history(name)` (MCP `get_history`) returns `commits`, `versions` (the first commit of each `sos.version`), `values` (`amend.history`) and `movement` (`amend.movement` against the constitution). `version_source(name, commit)` gives a version's text. The constitution is committed with the design. The Designer's sidebar shows the versions table |

## What e6 could look like

```python
ex = open_explorer()                       # explorer_bridge, unchanged
shutil.copy(CONST_PATH, ex.ws / f"{NAME}.constitution.yaml")
ex.workspace.write(NAME, START.read_text(encoding="utf-8"), note="version 2")

def trial(before, after):                  # the simulator as the explorer's evaluator
    cand = council._eval(council.params(after), rnd)
    gates = council.gates(decl, value, cand, cur)
    return {"results": {k: cand[k] for k in KEYS},
            "passed": all(g.ok for g in gates.values()) and len(gates) >= 4,
            "note": "; ".join(f"{g}: {x.reason}" for g, x in gates.items())}

for rnd in range(1, ROUNDS + 1):
    ...
    prop = ex.workspace.add_amendment(NAME, {d.pid: v}, rationale=..., evaluator=trial)
    # prop["version"] == {"from": "2", "to": "2.1", "effective_period": 7}
    ...
    ex.workspace.apply_proposal(NAME, adopted_id, reason=_reason(...), assumptions=[...])
    for other in rejected_ids:
        ex.workspace.discard_proposal(NAME, other, reason="failed gate: ...")

hist = ex.workspace.version_history(NAME)  # versions, amend.history, amend.movement
```

Then these can go:

- `explorer_gate_with`, `project_python` and `_GATE` in `explorer_bridge.py`
  (the explorer's own cadl is 0.3.43);
- `rewrite_cadl` and the second `workspace.write` in e6, the `period`
  and `k` bookkeeping, and `explorer_applied` (the text after applying is the
  new version);
- `decl_vs_tag_drift` (the declared value follows the tagged lines in the same
  proposal);
- `from_git` / `git_versions` and the calls of `amend.history` and
  `amend.movement` (`version_history` returns them);
- the column of table 21 that judges the adopted proposal with cadl 0.3.43.
  Every proposal is now judged with 0.3.43, and K-9 no longer appears.

Things to keep in mind:

- Each adoption makes one commit, not two, so "2 commits per adoption" in
  table 21 and §6.4 becomes 1. Each rejection also makes a commit (of the
  decision record and `review.json`).
- The decision record has entries with `outcome: "rejected"`. Code that
  counts adoptions from `get_decisions` should filter on `outcome`.
- In the evaluator above, `rnd`, `decl`, `value` and `cur` come from the
  enclosing loop. `council.gates` also needs the candidate value. A closure
  per candidate, or `record_trial` after evaluating as e6 does today, both
  work.
- The trial results are numbers. Keep the gates' booleans as `passed`, and
  their wording as `note`.
