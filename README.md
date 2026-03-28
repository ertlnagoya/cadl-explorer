# CADL Explorer

Research tool for exploring and evaluating governance configurations
in Collective Autonomous Systems using CADL (Collective Autonomous Description Language).

## Purpose

CADL Explorer visualizes the full causal chain of governance design:

```
CADL diff  →  IR diff  →  Config diff  →  Experiment result diff  →  Governance evaluation
```

This enables researchers to:
- Compare governance policies (A-SoS vs C-SoS vs motivation-sensitive)
- Trace how institutional changes propagate through the 3-layer IR to simulator configs
- Run parameter sweeps and evaluate throughput / autonomy / fairness tradeoffs
- Generate publication-quality figures

## Pipeline Overview

```
1. CADL Config    — governance template + motivation profile + rho
2. 3-Layer IR     — Institution / Protocol / Algorithm layers
3. Unity Config   — cadl_config.json for the simulator
4. Experiment     — synthetic analytical model or Unity batch execution
5. Evaluation     — throughput, autonomy, fairness metrics + comparison
```

## Directory Structure

```
cadl-explorer/
├── app.py                          # Streamlit web UI
├── requirements.txt                # Production dependencies
├── requirements-dev.txt            # Dev/test dependencies
│
├── backend/
│   ├── services/                   # Service facades (main entry points)
│   │   ├── cadl_service.py         # Config creation, IR, Unity config
│   │   ├── diff_service.py         # 4-type diff API (cadl/ir/config/result)
│   │   ├── experiment_service.py   # Dispatches to synthetic or Unity runner
│   │   ├── evaluation_service.py   # Aggregates metric evaluators
│   │   └── run_manager.py          # Experiment output management
│   │
│   ├── evaluation/                 # Metric modules
│   │   ├── system_metrics.py       # Throughput
│   │   ├── autonomy_metrics.py     # System autonomy, per-robot freedom
│   │   ├── fairness_metrics.py     # Fairness (CV), Gini coefficient
│   │   └── structural_metrics.py   # IR-level structural comparison
│   │
│   ├── runners/                    # Experiment execution
│   │   ├── synthetic_runner.py     # Analytical model (no Unity required)
│   │   └── unity_runner.py         # Unity headless batch execution
│   │
│   ├── adapters/                   # External system adapters
│   │   ├── unity_config_adapter.py # Generate cadl_config.json
│   │   ├── unity_runner_adapter.py # Execute Unity CLI
│   │   └── unity_result_adapter.py # Parse metrics_report.txt
│   │
│   ├── plotting/                   # Visualization
│   │   ├── interactive.py          # Plotly charts (Streamlit)
│   │   └── publication.py          # Matplotlib figures (PNG/PDF)
│   │
│   ├── models/                     # Data models
│   │   ├── experiment_result.py    # SingleResult dataclass
│   │   └── evaluation_result.py    # EvaluationResult, MetricValue
│   │
│   └── *.py                        # Backward-compat shims (cadl_bridge, etc.)
│
├── cadl/                           # CADL core (vendored)
│   ├── schema/                     # Config schema + motivation extensions
│   ├── ir/                         # 3-layer intermediate representation
│   ├── generators/unity/           # Unity config generator
│   └── examples/                   # CADL language examples
│
├── experiments/                    # Research experiment definitions
│   ├── a_sos_baseline.yaml
│   ├── a_sos_rho_sweep.yaml
│   ├── c_sos_reference.yaml
│   └── profiles/                   # Motivation profile definitions
│
├── runs/                           # Experiment outputs (gitignored)
│
└── tests/                          # Test suite
```

## Quick Start

```bash
git clone https://github.com/ertlnagoya/cadl-explorer.git
cd cadl-explorer
pip install -r requirements.txt
streamlit run app.py
```

Opens at http://localhost:8501. Add `?auto=1` to auto-run the full pipeline.

## Running Experiments

### Interactive (Web UI)

1. Select governance template, motivation profile, and rho
2. Click **Run Governance Pipeline Demo**
3. Browse tabs: Service View, CADL/IR Diff, Config Diff, Results

### Programmatic

```python
from backend.services.cadl_service import make_config, build_ir
from backend.services.experiment_service import run_comparison_sweep
from backend.services.evaluation_service import evaluate_full

config = make_config("A-SoS + motivation-sensitive", "linear", rho=0.5)
ir = build_ir(config)

results = run_comparison_sweep("linear", rho_values=[0.0, 0.25, 0.5, 0.75, 1.0])
evaluation = evaluate_full(results["a_sos"])
print(f"Throughput: {evaluation.throughput.mean:.1f} +/- {evaluation.throughput.std:.1f}")
```

### Publication Figures

```python
from backend.plotting.publication import pub_rho_effects, pub_performance_autonomy
from backend.runners.synthetic_runner import run_sweep

results = run_sweep("directed", "linear", [0.0, 0.25, 0.5, 0.75, 1.0], num_seeds=20)
pub_rho_effects(results, "figures/rho_effects.pdf")
```

## Output Directory

Experiment outputs are saved under `runs/` using `run_manager`:

```python
from backend.services.run_manager import create_run

run = create_run("a_sos_rho_sweep")
run.save_cadl(config)
run.save_ir(ir)
run.save_config(unity_config_dict)
run.save_metrics(evaluation_dict)
run.save_report(summary_text)
# Output: runs/2026-03-28_143000_a_sos_rho_sweep/{cadl,ir,configs,metrics,plots,report}/
```

## Running Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

## Current Constraints

- **Synthetic experiments only**: Unity batch execution requires Unity installation
  and `UNITY_PATH` environment variable. The synthetic runner provides calibrated
  analytical results without Unity.
- **Fixed graph topology**: 11-node, 17-edge graph matching the Unity scene.
- **5 robots**: Hardcoded to match the simulator environment.

## CADL Core — Vendored Copy

The `cadl/` directory contains a vendored copy of the CADL core modules
(schema, IR, generators). This is currently self-contained within this repository.

**Future migration path**: The CADL core is designed to be extracted into an
independent pip-installable package (`pip install cadl`). When that happens:
1. Remove the `cadl/` directory
2. Add `cadl` to `requirements.txt`
3. No other code changes needed — all imports go through `backend/services/`

The service facade pattern (`backend/services/cadl_service.py`) ensures that
UI and business logic never import `cadl.*` directly, making this migration
a one-line change.
