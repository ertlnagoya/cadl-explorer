"""In-app guide text shared by the Explorer and About pages."""

WHAT_YOU_CAN_DO = """\
- **Compare two governance designs** for a 5-robot fleet: a baseline **A** and a design under study **B**.
- **See the outcome** of the change in throughput, autonomy and fairness.
- **Trace why it happens**, stage by stage: CADL → IR → Config → Result.
- **Explore** how the result depends on ρ, robot by robot, and on the scenario.
- **Reproduce and share**: download the CADL / IR / config files, or copy the page URL.
- **Inspect contract lifecycles** written in the SoS-DSL extension (*Contract Lifecycle* page).
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
