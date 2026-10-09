"""In-app guide text shared by the Explorer and About pages."""

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
1. Open **Designer**. **Open an existing CADL file** from the sidebar, or start from an example or **New design**.
2. **Edit the design** as *Source* (an editor with line numbers; press **Apply** or Ctrl/⌘ + Enter), or switch to *Forms* and edit one section at a time: System, Actors, Contracts, Protocols, Algorithms, Regimes, Metrics and Verification. New contracts can start from a template (service-level agreement, safety, data sharing) and lifecycles from a preset. Both ways change the same design, and **Undo** takes back any change.
3. **Read the checks** on the right: one badge per check, then the problems. **Open …** next to a problem takes you to the form that fixes it. *Checks* explains what each check covers and what is not checked.
4. **Look at the views**: *Architecture* (actors, contracts, who decides; items with problems are outlined), *Lifecycle* (each contract's state machine, which you can step through event by event or read as stories), *Protocols* (a sequence diagram per protocol), *Regimes*, *Algorithms & metrics*, and *Read-back* (each contract and each actor's obligations restated in plain sentences, to confirm the design says what you meant).
5. **Keep versions** under *Versions*: save named versions, compare any two designs, restore one, or send two to the Explorer.
6. **Save** the design with **Save as .cadl** in the sidebar. *Export* also offers a design report (HTML, printable to PDF), the simulator IR, simulator configs and generated runtime code. The design is kept for this browser session only.
"""

HOW_TO_USE = """\
1. **Choose design B** in the sidebar: a governance template, a motivation profile and ρ. Or press one of the examples.
2. **Optionally change the baseline A** under *A — baseline* in the sidebar. It starts as plain A-SoS.
3. **Read section 1, Outcome**: the three metric cards show B and its difference from A.
4. **Read section 2, Why**: each stage lists what changed there. Open *Raw diff* for the exact lines.
5. **Use section 3, Explore** for the ρ sweep, the per-robot view and the scenario diagram.
6. **Keep the result** from section 4: download the files, save the comparison, or share the URL.

The page updates as soon as you change a setting; there is no run button.
"""

# Example name -> (what it compares, what to look at). Names match EXAMPLES in explorer.py.
DEMOS = {
    "Add motivation sensitivity": (
        "Plain A-SoS against A-SoS whose authority adjusts budgets to agent motivation (ρ = 0.5).",
        "Throughput drops a little while autonomy more than doubles. In section 2, a few "
        "CADL parameters fan out into changes in all three IR layers: Institution, Protocol and Algorithm.",
    ),
    "Directed vs collaborative": (
        "A-SoS (central authority) against C-SoS (collaborative).",
        "The largest trade-off of the three: much higher autonomy, much lower throughput. "
        "In section 2, the IR shows the decision holder and the routing protocol changing.",
    ),
    "Weak vs strong sensitivity": (
        "The same motivation-sensitive design at ρ = 0.25 and ρ = 1.0, with a polarized fleet.",
        "Only ρ changes in the IR, yet throughput falls and autonomy rises. "
        "Open *Effect of ρ* in section 3 to see the whole curve between the two.",
    ),
}
