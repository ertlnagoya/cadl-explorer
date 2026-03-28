# Governance Pipeline Demo

Web UI that visualizes the causal chain:

```
CADL diff → IR diff → Config diff → Experiment result diff → Governance evaluation
```

## Quick Start

```bash
cd demo_ui
pip install -r requirements.txt
streamlit run app.py
```

Opens at http://localhost:8501

## Usage

### Sidebar Controls

| Control | Options |
|---------|---------|
| Service | Robotaxi / Delivery Robot (label only) |
| Governance Template | A-SoS / C-SoS / A-SoS + motivation-sensitive |
| Motivation Profile | uniform / linear / polarized |
| rho slider | 0.0 - 1.0 (motivation sensitivity) |

### Buttons

- **Run Governance Pipeline Demo** — runs all 5 stages automatically
- Individual step buttons available in the expander

### Tabs

1. **Service View** — network graph, agent positions, baseline vs selected CADL config
2. **CADL / IR Diff** — sub-tabs for CADL YAML diff, Layer 1/2/3 IR diffs (color-coded)
3. **Simulator Config Diff** — Unity cadl_config.json diff with key fields summary
4. **Results & Evaluation** — scatter plots, rho effect curves, per-robot analysis, governance summary

## Architecture

```
demo_ui/
├── app.py                  # Streamlit entry point
├── requirements.txt
├── backend/
│   ├── cadl_bridge.py      # Wraps cadl/ parser, IR, config generator
│   ├── diff_engine.py      # Structured diffs with color tagging
│   ├── experiment_runner.py # Synthetic experiment model
│   ├── governance_eval.py  # Performance/autonomy/fairness evaluation
│   └── plot_builder.py     # Plotly interactive charts
└── README.md
```

All heavy logic delegates to the existing `cadl/` modules.

## Demo Script

1. Select **A-SoS + motivation-sensitive**, profile **linear**, rho **0.5**
2. Click **Run Governance Pipeline Demo**
3. Walk through each tab:
   - Service View: see network and config side-by-side
   - CADL/IR Diff: green = added, red = removed (motivation model, commitment policy)
   - Config Diff: see Unity config changes (motivationConfig section)
   - Results: scatter shows A-SoS baseline vs selected, rho curves show tradeoffs
4. Adjust rho slider and re-run to see how governance sensitivity affects outcomes
5. Switch to C-SoS template to compare collaborative vs directed governance
