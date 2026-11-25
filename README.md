# CADL Explorer

Research infrastructure for tracing the causal structure of governance design
in Systems of Systems (SoS).

- Specification and hands-on: <https://www.ertl.jp/cadl-spec/>
- Hosted app: <https://cadl-explorer.streamlit.app/>

The experiment results shown by the app come from a **synthetic model**
(`backend/runners/synthetic_runner.py`), not from measurements of a real
system. The governance template "D-SoS" (Directed SoS) is a configuration with a
strong central authority; its `sos_type` is `Directed`. Up to v0.4.1 it was
called "A-SoS", which the CADL specification uses for Acknowledged SoS; the
old names are still accepted by the CLI, experiment files and shared links.
The simulator parameters α / β / λ mean autonomy level, centralization level
and exploration probability, and differ from the per-contract α / β / λ of the
CADL language specification.

## The Designer of v1.0

The Designer leads through five stages of the work, each with its views:

| Stage | Views | What it is for |
|---|---|---|
| Write | Source, Forms | The CADL source in an editor with line numbers, problem marks, completion of the names of the design and snippets (contract, norm, lifecycle, monitor, protocol, stochastic model); forms that keep the comments and the lines they do not change |
| Read | Architecture, Structure, Control structure, Lifecycle, Protocols, Regimes, Algorithms & metrics, Read-back | Diagrams whose boxes open the place in the source that writes them; tables; the design restated in sentences |
| Verify | Summary, Checks, Traces, Measurements, Agents | The latest result of every check of cadl with the design it ran on; `cadl norms-check` and `cadl game-check` in the background with a stop button; witnesses of norms-check as sequences of messages; `cadl norms-eval` on a recorded trace, with the regimes it names and the versions of the run; `cadl metrics-eval` on measured values of the metrics; `cadl agents-run` and `cadl audit-verify` for the agent boundary |
| Change | Versions | Saved versions, amendments within the constitution or the declaration of amendment 0.1, the comparison of two designs with its changes drawn on the architecture |
| Deliver | Export | The report (with the record of the checks), simulator configs checked by `cadl sim-validate`, runtime code with a look at each generated file, the design sent to the Explorer page |

Side by side, the editor stays on the left. In one column (a tablet held
upright, a window narrower than 1100 px) each stage takes the whole width; the
page starts in the layout that the width of the window suits, and the switch
next to the stages changes it. The editor is a plain text area, so it works
with the on-screen keyboard of a tablet and does not correct or capitalize
what one types.

The pages are in English or Japanese (the switch at the top of the sidebar; a
browser that asks for Japanese gets Japanese). What cadl writes, such as the
findings and the reports, stays in English.

The sidebar saves the whole project as one zip (the design, the saved
versions, the decision record, the constitution, the entries of the STPA table
and the record of the checks) and opens it again. With Streamlit 1.51 or later
the editor and the clickable diagrams are custom components; the diagrams load
viz.js from jsDelivr. On Python 3.9 (Streamlit 1.50) the page uses the Ace
editor and diagrams with a list of their elements instead.

## The Violation Timeline

The SoS-DSL runtime reports one violation per contract instance: the first,
which ends the instance in `Violated`. The **Violation Timeline** page sets
that report beside *continued evaluation* (every deadline and monitor goes on
being evaluated as if the violation had not happened) and beside the faults
that were injected, so that a violation the report leaves out (*masking*) and
a report that names the wrong cause can be seen and measured.

| Tab | What it is for |
|---|---|
| Timeline (A2) | Per contract instance, the states reported and the states of continued evaluation as bands; reported violations, unreported ones and injected faults as marks; the window in which a periodic monitor's condition may have begun; the candidate cause; the path on the lifecycle diagram of the Contract Lifecycle page |
| Measurements (A1) | Over many trials and per condition: recall of the record, how often the report and the candidate cause name the injected fault, the masking rate, with 95% Wilson intervals; why the report missed the fault |
| Improvements (A3) | Changes of deadlines and monitor periods proposed by rules from the candidate causes (never from the ground truth), written as in the CADL source; assumptions on the robots; the trials run again with the changes, and resolution, side effect and coverage |
| Run | The reference runtime run here with injected faults (`battery_drop`, `over_speed`, `ack_missing`, `ack_delay`) and continued evaluation: one run, or a batch over deadlines, periods and dwell times |

Input is the NDJSON log of the runtime (`multi_robot_demo --log`) and the
simulator IR. A plain log of the runtime is shown as it is; the records for
continued evaluation, injections and runs are optional additions described in
`cadl_sim/sos_dsl/violation_trace.py`. The bundled logs in
`cadl_sim/sos_dsl/examples/violations/` were made with the runtime.

The runtime (`cadl/runtime/engine.py` of raspimouse-swarm-simulator) is not
changed or copied: the **Run** tab loads it from the path given there or in
`CADL_RUNTIME_PATH`, and records continued evaluation with a subclass that
does not take violation transitions. Without the runtime, the other tabs work
on logs.

How to carry out A1–A3 of the research plan with this page, step by step:
[docs/violation-timeline-guide.ja.md](docs/violation-timeline-guide.ja.md) (in Japanese).

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

Example output (D-SoS, uniform, ρ=0 against D-SoS + motivation-sensitive, linear, ρ=0.5):
```
[Institution: Motivation model] authority now uses hybrid model (was none)
[Protocol: Dispatch control] motivation now affects dispatch
[Algorithm: Budget-aware planning] planner now uses commitment budget
[Performance] Throughput (total deliveries) decreased by 6.6% (47.20 -> 44.10)
[Governance] System autonomy increased by 136.1% (0.14 -> 0.33)
```

## Directory Structure

```
cadl-explorer/
├── app.py                          # Streamlit entry point (page router)
├── views/
│   ├── designer.py                 # Author, check and export a full CADL design (the page and its stages)
│   ├── _design/                    # The parts of the Designer
│   │   ├── common.py               # State, jumps, versions and helpers shared by the parts
│   │   ├── cache.py                # Cached calls of cadl
│   │   ├── layout.py               # Stages, views, side by side or one column, the guide
│   │   ├── edit.py                 # Source editor and forms
│   │   ├── editor_component.py     # The CADL editor (custom component)
│   │   ├── graph_component.py      # Diagrams whose boxes can be chosen (custom component, viz.js)
│   │   ├── read.py                 # Diagrams, tables and sentences
│   │   ├── verify.py               # Checks, norms-check and game-check in the background, the summary
│   │   ├── trace.py                # norms-eval on a trace; agents-run and audit-verify
│   │   ├── change.py               # Versions, amendments, comparison
│   │   ├── deliver.py              # Export
│   │   └── sidebar.py              # Workspace, files, projects, starting points
│   ├── _designer_forms.py          # Form-based editing for the Designer
│   ├── _i18n.py, _i18n_ja.py       # English and Japanese texts of the pages
│   ├── _guide.py                   # "Getting started" guide shown on the pages
│   ├── explorer.py                 # Compare two designs: outcome → causal chain → explore → reproduce
│   ├── lifecycle.py                # Contract Lifecycle page (state machine from IR JSON)
│   ├── violations.py               # Violation Timeline page: report, continued evaluation, injected faults
│   ├── _violations.py              # Its charts and tables
│   ├── evidence.py                 # Decision Evidence page (decision records of Appendix L)
│   └── about.py                    # Glossary, scenario and model notes
├── cli.py                          # Batch CLI runner
├── mcp_server.py                   # Design tools for AI assistants (Model Context Protocol)
├── backend/
│   ├── version.py                  # The version of CADL Explorer: the one place to change at a release
│   ├── services/
│   │   ├── pipeline.py             # GovernancePipeline + ComparisonResult
│   │   ├── cadl_service.py         # CADL config / IR / Unity config
│   │   ├── design_service.py       # Full CADL designs via cadl-lang: check, edit, diff, export
│   │   ├── design_ops.py           # Structured change operations, proposals, open questions
│   │   ├── design_readback.py      # A design restated in plain language; lifecycle stories
│   │   ├── design_graph.py         # Message flow, authority, centrality, N² chart, responsibilities
│   │   ├── design_stamp.py         # STAMP control structure (inferred) and the STPA table
│   │   ├── design_contracts.py     # What watches each promise; dependencies between contracts
│   │   ├── design_amend.py         # # @amend: values, the constitution's gate, in-place amendments
│   │   ├── design_workspace.py     # Folder of designs shared with an AI assistant (optionally in git)
│   │   ├── design_jobs.py          # Long checks of cadl in a background thread, with cancel
│   │   ├── design_runtime.py       # norms-eval, agents-run, audit-verify, sim-validate; versions of cadl and PRISM
│   │   ├── design_project.py       # A project as one zip
│   │   ├── design_snippets.py      # Names and snippets offered by the editor
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
├── docs/
│   └── violation-timeline-guide.ja.md  # How to carry out A1–A3 with the Violation Timeline
├── designs/                        # Example CADL designs for the Designer (traces/: recorded runs of three of them)
├── cadl_sim/                       # Vendored CADL subset for simulation
│   └── sos_dsl/                    # Lifecycle view; violations, proposals and the runner of the Violation Timeline
├── experiments/                    # Research experiment definitions
├── runs/                           # Experiment outputs (gitignored)
└── tests/                          # test suite
```

## Quick Start

```bash
git clone https://github.com/ertlnagoya/cadl-explorer.git
cd cadl-explorer
pip install -r requirements.txt
streamlit run app.py
```

Python 3.9 or later is required.

The app has four pages:

- **Explorer** (`views/explorer.py`) compares two governance designs, a
  baseline A and a design under study B. It shows the outcome first
  (throughput, autonomy, fairness), then the causal chain stage by stage, a ρ
  sweep, and the IDs and files needed to reproduce the comparison. The
  selection is kept in the URL, so a comparison can be shared as a link.
- **Designer** (`views/designer.py`, parts in `views/_design/`) is an editor
  for full CADL designs: actors, contracts with their lifecycles and monitors,
  protocols, algorithms, regime transitions, metrics and verification
  directives. Its five stages are described in
  [The Designer of v1.0](#the-designer-of-v10). Each change is parsed,
  type-checked and verified with the upstream `cadl-lang` toolchain. What the
  stages rest on:
  - *Write.* Forms (`views/_designer_forms.py`) edit the source and keep its
    comments. On top of the upstream checks the page reports malformed
    expressions, undeclared messages, lifecycle structure problems and
    conflicts between sections, says what each check covers, and links every
    problem to its line and to the form that fixes it. Undo / redo, contract
    templates and, with the `anthropic` package and `ANTHROPIC_API_KEY`, a
    draft from a plain-language description are offered.
  - *Read.* The *Structure* view (`backend/services/design_graph.py`) shows who
    sends what to whom, the betweenness and degree of each actor, an N² chart,
    a table of who decides, takes part in or answers for each contract, and the
    dependencies between contracts (`backend/services/design_contracts.py`).
    The *Lifecycle* view steps through a contract lifecycle event by event.
    The *Control structure* view (`backend/services/design_stamp.py`) infers a
    STAMP control structure from authority, reports open loops, and gives the
    STPA table of unsafe control actions and a coverage table of what watches
    each assumption and guarantee at run time. The read-back restates the
    extensions of cadl as well: norms (norms 0.1), strategic models (games
    0.1), information items and derived indices (grounding 0.1), and how cadl
    reads a wildcard reference such as `ROBOT[*].battery > 10`. The *Regimes*
    view lists what each regime binds (`regimes:`: parameter values and the
    contracts in force or suspended) and the safety invariants of the
    transitions.
  - *Verify.* **Checks** shows `cadl norms-indices` and runs `cadl game-check`
    (also `--certify`) and `cadl norms-check` in the background. norms-check
    needs PRISM on PATH; without it, it says that nothing was checked. On
    Streamlit Community Cloud, `packages.txt` installs Java and the first run
    downloads PRISM 4.11 in the background, checks its SHA-256 and puts it on
    PATH (`backend/services/prism_setup.py`; `CADL_EXPLORER_INSTALL_PRISM=1`
    does the same elsewhere on Linux x86-64, `=0` turns it off). The form of norms-check takes domains of quantities, several
    instances (`--starts any` or `together`), the versions to amend to (saved
    versions, or the other `_vN` file of an example; with amendment 0.1 also
    `keep` and `switch` with `in_states:`, cadl 0.3.36) and the limit on the
    states (`--max-states`); a witness that names versions opens under
    **Traces** with them. Nonlinear arithmetic is marked where an answer holds only under the
    domains that cadl assumes. **Traces** passes the other versions of a run
    (saved versions, or the other `_vN` file of an example) and its time base
    (`--version`, `--period`, `--origin`, `--epoch`) to `cadl norms-eval` and
    shows what each version did to the instance; a trace may name the regime
    of an observation (`regime`). **Measurements** runs `cadl metrics-eval`:
    each measurement met, not met or not judged, with the target in force.
    **Agents** passes the versions of a run and the time base to
    `cadl agents-run` (cadl 0.3.35), which switches the enforcement point at
    the effective times; a line may name the regime (cadl 0.3.36).
  - *Change.* Named versions (several CADL files, such as the `v1.cadl`,
    `v2.cadl`, ... of a Course D run, can be added at once) and amendable parameters (values marked `# @amend:`,
    checked against a constitution and amended in place, with how far and how
    often each moved across the versions; `backend/services/design_amend.py`).
    An amendment between two versions with norms is checked with
    `cadl norms-diff`. A design that declares amendment 0.1 (Appendix K) lists
    its amendable parameters, and the comparison of two versions runs
    `cadl amend-diff`: each change against the declared range, steps and
    largest change, and the changes that no parameter explains.
  - *Deliver.* The design is exported as CADL, simulator IR, simulator config
    or runtime code, agent policies (Rego, Cedar) for agents 0.1 and PRISM
    files for stochastic 0.1. **Export** lists the norms monitors of the OPA
    and Solidity code with their warnings; for the OPA, Solidity and Unity C#
    targets it takes the other versions of a run (`cadl codegen --version`,
    cadl 0.3.36), so that the code follows the version in force. It names `cadl mcp-gateway`,
    `cadl a2a-gateway` and `cadl audit-verify`, which the Explorer does not
    run as gateways.

  Example designs live in `designs/`.

  **Limits of the checks.** game-check and norms-check run in a subprocess
  that is stopped after a time limit (120 s for game-check; for norms-check
  its timeout, at most 120 s, and 30 s more) or by the Stop button. On POSIX
  the cadl process also has limits of CPU time, file size and address space
  (1 GiB, where the system enforces it; macOS does not), PRISM is bounded by
  its Java heap (1 GB, or `CADL_PRISM_JAVAMAXMEM`), and a watchdog stops a run
  whose processes, PRISM included, take more than 1.5 GiB of memory together.
  The watchdog looks every 0.5 s and reads the resident memory (on Linux from
  `/proc`; on macOS the larger of the resident size and the physical
  footprint, through `proc_pid_rusage`). A process that cadl starts is
  recorded just after it is created, so for a moment it is neither watched nor
  killed with its run. At most two such runs take place at once on one server;
  a further one is refused with a request to try again. A crash of cadl is
  reported as such, and nothing is claimed then. The model and witnesses of
  norms-check are offered for download up to 10 MB.
- **Contract Lifecycle** (`views/lifecycle.py`) draws the contract lifecycle of
  the SoS-DSL extension. It reads a simulator IR produced by the `cadl`
  compiler (`cadl sim-ir <file>.cadl --format json`), or a bundled sample.
- **Decision Evidence** (`views/evidence.py`) shows a decision record of
  Appendix L of the specification (`decisions.jsonl`, written by
  cadl-robot-town's `town.steward` and `town.council`): a timeline of who
  decided each amendment and how the parties voted, the trail of each decision
  with what it rests on, the evidence of each party counted the same way for a
  person, an LLM and a tool, a GSN derived from the record, the errors and
  findings of `cadl decisions-verify`, and the hash chain with an anchor check.
  From v1.6.0 it reads decisions 0.2: the layers on which each party decides
  (the operator on the rules and the contract, the regulator on the
  regulation), and each follow-up with what fulfils it and whether it was
  fulfilled. From v1.7.0 it also takes the CADL files of the versions that
  the record attaches (`v1.cadl`, `v2.cadl`, …; the bundled records come with
  theirs), so that the verifier checks the versions, the amendable parameters
  and who may decide (L-9 to L-11). From v1.8.0 it reads decisions 0.3: the
  delegations (who let whom decide in their place, on which layers, by which
  procedures and under which conditions) are listed with the decisions made
  within each, shown as a diamond on the timeline and as a context in the GSN;
  a decision made in another's place outside every delegation stays a square
  and the finding delegated. From v1.9.0 the GSN maps every field of L.8:
  the measure in force as the context of an objective, the point, unknowns
  and predictions of a decision as context of its strategy, a prediction that
  a later measurement contradicts as a counter-evidence, and a follow-up not
  yet fulfilled as an undeveloped goal; with cadl 0.3.41 or later the check
  also says how many predictions a later entry checked. From v1.9.1 the
  bundled records of the steward are those of cadl-robot-town v0.7.0, which
  measures an adopted amendment under the next period's conditions, so their
  GSN shows counter-evidence; from v1.9.2 they are those of v0.7.1, whose
  next-period measurements name the scenario period they use. It needs cadl 0.3.39 or later for the checks;
  records of the robot town are bundled.
- **About & Glossary** (`views/about.py`) explains the templates, parameters,
  metrics and the fixed scenario.

## Designing with an AI assistant (MCP)

`mcp_server.py` exposes the Designer's tools to an AI assistant over the
[Model Context Protocol](https://modelcontextprotocol.io), so a design can be
built in conversation. The assistant asks what is missing, proposes small
structured changes, has them checked by the CADL toolchain, and reads the
result back in plain language. It never edits the source directly, and nothing
changes until a proposal is applied.

```bash
pip install -r requirements-mcp.txt
```

The MCP library needs Python 3.10 or later (the web app alone runs on 3.9).
The assistant starts the server as a separate process, so give it the
interpreter of the environment where you ran the command above, as an
absolute path.

Register the server with an MCP client. For Claude Desktop, add to
`claude_desktop_config.json` (use absolute paths):

```json
{
  "mcpServers": {
    "cadl-design": {
      "command": "/path/to/python",
      "args": ["/path/to/cadl-explorer/mcp_server.py"],
      "env": {"CADL_WORKSPACE": "/path/to/my-designs"}
    }
  }
}
```

For Claude Code:

```bash
claude mcp add cadl-design -e CADL_WORKSPACE=/path/to/my-designs -- /path/to/python /path/to/cadl-explorer/mcp_server.py
```

Then start with the `design_interview` prompt, or `draft_from_document` to
draft a design from a requirements document or SLA.

| Tool | What it does |
|------|--------------|
| `list_designs`, `create_design`, `get_design` | Find, start and outline designs in the workspace |
| `open_questions` | What the design leaves open, as questions to ask the designer |
| `propose_changes` | Check a set of structured changes **without applying them**; reports what would change and which problems appear or disappear |
| `apply_proposal`, `discard_proposal`, `undo_last_change` | Act on a proposal once the designer agrees. `apply_proposal` refuses a proposal that adds an error unless called with `allow_errors=true`, and records the decision — the designer's `reason` and `assumptions` — in `<name>.decisions.md` |
| `list_amendables`, `propose_amendment` | The values marked `# @amend:<name>` (Robot Town, Course D) and what the constitution (`<name>.constitution.yaml` or `constitution.yaml` in the workspace) says about each; a proposal that changes them in place, keeping the comments, with the constitution's verdict. A change the constitution leaves to a person (contract or regulation layer, safety-related rule) cannot be applied without their reason |
| `get_decisions` | The decision record: each applied proposal, why it was proposed, and the person's reason and assumptions |
| `check_design` | Every check, what each covers, and what none of them check |
| `describe_contracts`, `actor_view`, `explain_verification` | The design read back in plain sentences |
| `lifecycle_stories`, `step_lifecycle` | The ways one contract instance can run, as stories or step by step. A monitor whose rule contains `state == X` (in a plain conjunction) is taken to fire in X only |
| `get_diagram`, `export_design` | Diagrams as DOT / SVG, a design report, the simulator IR |

Designs live as `.cadl` files in `CADL_WORKSPACE` (default `~/cadl-designs`).
To see and confirm what the assistant did, run the app with the same folder:

```bash
CADL_WORKSPACE=/path/to/my-designs streamlit run app.py
```

With `CADL_WORKSPACE_GIT=1` (for the app and for the MCP server), the folder
is a git repository and every change is committed: an edit saved from the
Designer, an applied proposal with the person's reason, and an undo. The
sidebar lists those commits.

The Designer then shows the workspace in its sidebar, warns when the file
changed underneath the editor, and lists every change the assistant applied
as *not yet reviewed* until someone confirms it. The read-back and the
lifecycle stories are also available in the Designer (views *Read-back* and
*Lifecycle*); they are produced by fixed rules, not by a language model.

The checks establish that a design is consistent, not that it is what was
intended. That is what the read-back and the review marks are for.

## CLI Experiments

```bash
# Single pipeline
python cli.py run --template "D-SoS + motivation-sensitive" --profile linear --rho 0.5 --save

# Rho sweep
python cli.py sweep --profile linear --rho-values "0.0,0.25,0.5,0.75,1.0" --save

# Batch from experiment YAML
python cli.py batch --config experiments/d_sos_rho_sweep.yaml --save

# Compare two governance configurations
python cli.py compare --a "D-SoS" --b "D-SoS + motivation-sensitive" --rho-b 0.5

# Replay a previous run
python cli.py replay --run-dir runs/2026-03-28_143000_sweep_...

# List saved runs
python cli.py list-runs
```

## Reproducibility

Each run saves a `manifest.json` with all parameters needed for exact replay:

```
runs/2026-03-28_143000_sweep/
├── manifest.json          # {template, profile, rho, seeds, created_at, pipeline_version}
├── cadl/config.yaml       # CADL snapshot
├── ir/ir.json             # IR dump
├── configs/cadl_config.json # Unity config
├── metrics/
│   ├── evaluation.json    # Aggregated metrics
│   └── raw_results.json   # Per-seed results
├── logs/                  # Reserved (empty)
├── plots/                 # Reserved (empty)
└── report/                # Reserved (empty)
```

`PipelineResult` includes content-hash traceability IDs (cadl_id, ir_id, config_id)
that link each stage to its upstream dependency.

The version of CADL Explorer is defined in `backend/version.py`. It is shown in
the sidebar and on the About page, and `PipelineResult.to_dict()` carries it as
`pipeline_version`. A saved run records it in `manifest.json` under the same
key.

## Running Tests

```bash
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

The tests run on Python 3.9 (with Streamlit 1.50, the last release for it)
and later. `tests/conftest.py` works around a bug of that release's
`streamlit.testing` (a segmented control's value), and the two tests that
upload files skip where `streamlit.testing` has no `file_uploader`. The tests
of `cadl norms-check` that need PRISM skip without `prism` on PATH.
`tests/conftest.py` also sets `CADL_JOBS_SYNC=1`, which runs the background
checks of the Designer in the same thread, so that a test sees their result.

`.github/workflows/tests.yml` runs the tests on Python 3.9, 3.12 and 3.14
(on 3.12 with `requirements-mcp.txt`, so that the tests of the MCP server run),
and once more on Python 3.12 with PRISM 4.11 installed and `REQUIRE_PRISM=1`,
which makes a test that needs PRISM fail instead of skip.

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
result = run_pipeline(template="D-SoS", profile="uniform", rho=0.0)

# Compare two governance designs
comparison = compare_pipelines(result_a, result_b)
# -> comparison.cadl, .ir, .config, .result (the diff at each stage)
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

`cadl_sim/` is a vendored subset of the CADL schema, IR, and Unity generator,
scoped to this explorer's needs. It was renamed from `cadl/` to avoid a
name collision with the upstream `cadl` Python package
([github.com/ertlnagoya/cadl](https://github.com/ertlnagoya/cadl), published
on PyPI as `cadl-lang`), which provides the full language parser (Lark
grammar, verification, codegen).

The Designer page already depends on `cadl-lang` directly, through
`backend/services/design_service.py`. The Explorer's pipeline still runs on
`cadl_sim/`. Future migration path: gradually replace `cadl_sim/` modules
with the upstream package. Most imports funnel through
`backend/services/cadl_service.py`, which is the single point where the
swap would happen.

## License

Apache License 2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).
