"""In-app guide text shared by the Explorer and About pages.

The English text is the key: the pages show each block through `t()`, and the
Japanese of every block is in views/_i18n_ja.py.
"""

WHAT_YOU_CAN_DO = """\
- **Compare two governance designs** for a 5-robot fleet: a baseline **A** and a design under study **B**.
- **See the outcome** of the change in throughput, autonomy and fairness.
- **Trace why it happens**, stage by stage: CADL → IR → Config → Result.
- **Explore** how the result depends on ρ, robot by robot, and on the scenario.
- **Reproduce and share**: download the CADL / IR / config files, or copy the page URL.
"""

OTHER_PAGES = """\
- **Design your own SoS and contract architecture** in CADL on the *Designer* page: open an existing `.cadl` file or start from an example or a template, edit actors, contracts, protocols, algorithms, regimes and metrics, step through a contract's lifecycle, keep versions, and save the result or a design report. Every edit is checked.
- **Inspect contract lifecycles** from a compiled simulator IR on the *Contract Lifecycle* page.
- **Design in conversation with an AI assistant**: the same tools are available to assistants through an MCP server (`mcp_server.py` in the repository). The assistant asks what is missing, proposes changes that are checked before they are applied, and reads the design back; the Designer shows what it changed for you to confirm.
"""

HOW_TO_DESIGN = """\
1. **Open the Designer.** In the sidebar, open a `.cadl` file or a saved project, load an example, or start a **New design**. The page leads through five stages: **Write**, **Read**, **Verify**, **Change** and **Deliver**.
2. **Write** the design as *Source* (an editor with line numbers; press **Apply** or Ctrl/⌘ + Enter) or with *Forms*, one section at a time. The editor completes the names of the design as you type, and **Insert…** puts a snippet (a contract, a norm, a lifecycle, a monitor, a protocol, a stochastic model) at the cursor. Every change is checked: each problem in the list has a **Line n** button that shows its line in the editor and an **Open form** button for the form that fixes it. **Undo** takes back any change.
3. **Read** the design back: *Architecture*, *Structure*, *Control structure*, *Lifecycle*, *Protocols*, *Regimes*, *Algorithms & metrics* and *Read-back* (the design restated in sentences, to confirm it says what you meant). Click a box in a diagram to see where the source writes it.
4. **Verify** it. *Summary* shows the latest result of every check and the design it ran on. *Checks* runs `cadl norms-check` and `cadl game-check` in the background, with a **Stop** button. *Traces* evaluates a recorded trace with `cadl norms-eval`. *Agents* runs `cadl agents-run` and `cadl audit-verify`.
5. **Change** it under *Versions*: save versions, compare two designs (the change is drawn on the architecture), and amend the values marked `# @amend:` within the constitution.
6. **Deliver** it under *Export*: the design report, simulator configs checked by `cadl sim-validate`, runtime code, and the design opened in the Explorer.

**Layout.** The editor and the stages stand side by side, or the page has one column. Below 1100 px (a tablet held upright, a narrow window) the page starts in one column; the switch next to the stages changes it. **Language.** The switch at the top of the sidebar chooses English or 日本語.

**Keeping your work.** The design lives in the browser session. Save the `.cadl` file, or the whole project as a `.zip` (versions, decisions, constitution, STPA entries and the record of the checks), from the sidebar, and open it there again later. With `CADL_WORKSPACE_GIT=1`, the shared workspace commits each change to git.
"""

HOW_TO_USE = """\
1. **Choose design B** in the sidebar: a governance template, a motivation profile and ρ. Or press one of the examples.
2. **Optionally change the baseline A** under *A — baseline* in the sidebar. It starts as plain D-SoS.
3. **Read section 1, Outcome**: the three metric cards show B and its difference from A.
4. **Read section 2, Why**: each stage lists what changed there. Open *Raw diff* for the exact lines.
5. **Use section 3, Explore** for the ρ sweep, the per-robot view and the scenario diagram.
6. **Keep the result** from section 4: download the files, save the comparison, or share the URL.

The page updates as soon as you change a setting; there is no run button.
"""

# Example name -> (what it compares, what to look at). Names match EXAMPLES in explorer.py.
DEMOS = {
    "Add motivation sensitivity": (
        "Plain D-SoS against D-SoS whose authority adjusts budgets to agent motivation (ρ = 0.5).",
        "Throughput drops a little while autonomy more than doubles. In section 2, a few "
        "CADL parameters fan out into changes in all three IR layers: Institution, Protocol and Algorithm.",
    ),
    "Directed vs collaborative": (
        "D-SoS (central authority) against C-SoS (collaborative).",
        "The largest trade-off of the three: much higher autonomy, much lower throughput. "
        "In section 2, the IR shows the decision holder and the routing protocol changing.",
    ),
    "Weak vs strong sensitivity": (
        "The same motivation-sensitive design at ρ = 0.25 and ρ = 1.0, with a polarized fleet.",
        "Only ρ changes in the IR, yet throughput falls and autonomy rises. "
        "Open *Effect of ρ* in section 3 to see the whole curve between the two.",
    ),
}
