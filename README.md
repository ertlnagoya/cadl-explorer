# CADL Explorer

Research infrastructure for tracing the causal structure of governance design
in Collective Autonomous Systems-of-Systems (SoS).

## Research Purpose

This tool enables researchers to **trace how institutional design choices
propagate through governance layers to behavioral outcomes**:

```
CADL (institution)  →  IR (governance structure)  →  Config (execution)  →  Result (behavior)
```

Each stage is preserved, diffed, and labeled with semantic interpretation,
so that a change in governance parameters (e.g., rho, authority model)
can be followed end-to-end to its effect on throughput, autonomy, and fairness.

## Causal Pipeline

| Stage | What | Artifact |
|-------|------|----------|
| 1. CADL | Institutional design parameters | YAML config |
| 2. IR | 3-layer governance structure (Institution / Protocol / Algorithm) | JSON |
| 3. Config | Simulator execution settings | Unity cadl_config.json |
| 4. Result | Experiment outcomes (per-seed, per-robot) | SingleResult |
| 5. Evaluation | Aggregated metrics (throughput, autonomy, fairness) | EvaluationResult |

All stages are captured in `PipelineResult` with content-hash traceability IDs.

## Diff Semantics

Each stage has a **semantic diff** that interprets changes in governance terms:

| Diff type | Interprets |
|-----------|------------|
| CADL diff | Institution design changes (rho, authority model, profile) |
| IR diff | Authority structure, decision holder, information flow, planner binding |
| Config diff | Execution settings (motivationConfig, governance params) |
| Result diff | Performance/autonomy/fairness changes with direction labels |

Example output:
```
[Institution: Motivation model] authority now uses hybrid model (was none)
[Protocol: Dispatch control] motivation now affects dispatch
[Algorithm: Budget-aware planning] planner now uses commitment budget
[Performance] Throughput decreased by 8.5% (46.80 → 42.80)
[Governance] System autonomy increased by 112.4% (0.16 → 0.33)
```

## Directory Structure

```
cadl-explorer/
├── app.py                          # Streamlit web UI
├── cli.py                          # Batch CLI runner
├── backend/
│   ├── services/
│   │   ├── pipeline.py             # GovernancePipeline + ComparisonResult
│   │   ├── cadl_service.py         # CADL config / IR / Unity config
│   │   ├── diff_service.py         # 4-type syntactic + semantic diff
│   │   ├── evaluation_service.py   # Metric aggregation
│   │   ├── experiment_service.py   # Runner dispatch
│   │   └── run_manager.py          # Output management + replay
│   ├── evaluation/
│   │   ├── system_metrics.py       # Throughput
│   │   ├── autonomy_metrics.py     # System autonomy
│   │   ├── fairness_metrics.py     # Fairness, Gini
│   │   ├── structural_metrics.py   # Correlation, variance, region extent
│   │   └── region_analysis.py      # Convex hull, bounding box, region comparison
│   ├── runners/                    # Synthetic / Unity execution
│   ├── adapters/                   # Unity config / runner / result adapters
│   ├── plotting/                   # Interactive (Plotly) + Publication (Matplotlib)
│   └── models/
│       ├── pipeline_result.py      # PipelineResult + StageTrace
│       ├── experiment_result.py    # SingleResult
│       └── evaluation_result.py    # EvaluationResult + MetricValue
├── cadl/                           # Vendored CADL core
├── experiments/                    # Research experiment definitions
├── runs/                           # Experiment outputs (gitignored)
└── tests/                          # 50 tests
```

## Quick Start

```bash
git clone https://github.com/ertlnagoya/cadl-explorer.git
cd cadl-explorer
pip install -r requirements.txt
streamlit run app.py
```

## CLI Experiments

```bash
# Single pipeline
python cli.py run --template "A-SoS + motivation-sensitive" --profile linear --rho 0.5 --save

# Rho sweep
python cli.py sweep --profile linear --rho-values "0.0,0.25,0.5,0.75,1.0" --save

# Batch from experiment YAML
python cli.py batch --config experiments/a_sos_rho_sweep.yaml --save

# Compare two governance configurations
python cli.py compare --a "A-SoS" --b "A-SoS + motivation-sensitive" --rho-b 0.5

# Replay a previous run
python cli.py replay --run-dir runs/2026-03-28_143000_sweep_...

# List saved runs
python cli.py list-runs
```

## Reproducibility

Each run saves a `manifest.json` with all parameters needed for exact replay:

```
runs/2026-03-28_143000_sweep/
├── manifest.json          # {template, profile, rho, seeds, timestamp}
├── cadl/config.yaml       # CADL snapshot
├── ir/ir.json             # IR dump
├── configs/cadl_config.json # Unity config
├── metrics/
│   ├── evaluation.json    # Aggregated metrics
│   └── raw_results.json   # Per-seed results
├── plots/                 # Generated figures
└── report/summary.md      # Text summary
```

`PipelineResult` includes content-hash traceability IDs (cadl_id, ir_id, config_id)
that link each stage to its upstream dependency.

## Running Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

## Core Execution Flow

All execution—UI and CLI—flows through `GovernancePipeline` in
`backend/services/pipeline.py`:

```
┌─────────┐    ┌────────┐    ┌──────────┐    ┌────────────┐    ┌────────────┐
│  CADL   │───▶│   IR   │───▶│  Config  │───▶│ Simulation │───▶│ Evaluation │
│ (YAML)  │    │ (JSON) │    │ (Unity)  │    │ (Synthetic │    │ (Metrics)  │
│         │    │        │    │          │    │  or Unity) │    │            │
└─────────┘    └────────┘    └──────────┘    └────────────┘    └────────────┘
     │              │             │                │                  │
     └──────────────┴─────────────┴────────────────┴──────────────────┘
                         PipelineResult (with traceability IDs)
```

```python
from backend.services import GovernancePipeline, run_pipeline, compare_pipelines

# Single run
result = run_pipeline(template="A-SoS", profile="uniform", rho=0.0)

# Compare two governance designs
comparison = compare_pipelines(result_a, result_b)
# -> comparison.cadl_diff, .ir_diff, .config_diff, .result_diff
```

## Deprecated Modules

The following files in `backend/` are **backward-compatibility shims** that
forward to the new layered modules. They emit `DeprecationWarning` on import.
**New code should not use them.**

| Legacy file | Replacement |
|-------------|-------------|
| `backend/cadl_bridge.py` | `backend.services.cadl_service` |
| `backend/diff_engine.py` | `backend.services.diff_service` |
| `backend/experiment_runner.py` | `backend.services.experiment_service` |
| `backend/governance_eval.py` | `backend.services.evaluation_service` |
| `backend/plot_builder.py` | `backend.plotting.interactive` |

These shims will be removed in a future version.

## Current Constraints

- **Synthetic experiments only**: Unity requires `UNITY_PATH` env var
- **Fixed topology**: 11-node, 17-edge graph (matches Unity scene)
- **5 robots**: Hardcoded to match simulator

## CADL Core — Vendored Copy

`cadl/` is a vendored copy. Future migration: replace with `pip install cadl`.
All imports go through `backend/services/cadl_service.py` — one-line change.
