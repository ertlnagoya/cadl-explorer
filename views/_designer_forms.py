"""Forms of the Designer page: one section per part of a CADL design.

Every form edits the parsed YAML document and hands it back through
``apply``, which rewrites the source text and reruns the page.
"""

import copy

import streamlit as st

from backend.services import design_service as ds

SECTIONS = [
    "System", "Actors", "Contracts", "Protocols",
    "Algorithms", "Regimes", "Metrics", "Verification",
]

ss = st.session_state


def _blank(columns):
    """A single empty row, so an empty table still shows its columns."""
    return [dict.fromkeys(columns, "")]


def _lines(value) -> str:
    return "\n".join(str(x) for x in value or [])


def _from_lines(text: str) -> list:
    return [line.strip() for line in text.splitlines() if line.strip()]


_MARK = {"error": " — has errors", "warning": " — has warnings"}


def _pick(label: str, ids: list, state_key: str, marks: dict = None):
    """Selectbox over ids that survives adds, renames and deletes.

    ``marks`` ({id: "error" | "warning"}) labels items that have findings.
    """
    if f"{state_key}_next" in ss:
        ss[state_key] = ss.pop(f"{state_key}_next")
    if ss.get(state_key) not in ids:
        ss.pop(state_key, None)
    if not ids:
        return None
    marks = marks or {}
    return st.selectbox(
        label, ids, key=state_key, format_func=lambda i: f"{i}{_MARK.get(marks.get(i), '')}")


def _party_options(sos: dict) -> list:
    options = []
    for actor in sos.get("actors") or []:
        aid = str(actor.get("id", ""))
        base = ds.actor_base(aid)
        options += [f"{base}[*]", f"{base}[i]"] if "[" in aid else [base]
    return options


# ── Sections ────────────────────────────────────────────────────────

def _system(sos, key, apply, flags):
    with st.form(f"sos_{key}", border=False):
        c1, c2, c3 = st.columns([3, 2, 1])
        name = c1.text_input("Name", sos.get("name", ""))
        current = str(sos.get("type", ds.SOS_TYPES[1]))
        sos_type = c2.selectbox(
            "Type", ds.SOS_TYPES,
            index=ds.SOS_TYPES.index(current) if current in ds.SOS_TYPES else 1,
            help="Maier's SoS categories, from most to least centrally managed.",
        )
        version = c3.text_input("Version", str(sos.get("version", "")))
        description = st.text_input("Description", sos.get("description", ""))

        context = sos.get("context") if isinstance(sos.get("context"), dict) else {}
        st.markdown("**Context**")
        st.caption("Environment parameters the design refers to, and assumptions about the setting.")
        env_rows = st.data_editor(
            ds.mapping_to_rows(context.get("environment")) or _blank(["name", "value"]),
            num_rows="dynamic", width="stretch", key=f"env_{key}",
        )
        assumptions = st.text_area(
            "Assumptions (one per line)", _lines(context.get("assumptions")), height=80)

        if st.form_submit_button("Apply", type="primary"):
            sos["name"], sos["type"] = name.strip(), sos_type
            ds.put(sos, "version", version.strip())
            ds.put(sos, "description", description.strip())
            context = dict(context)
            ds.put(context, "environment", ds.rows_to_mapping(env_rows, typed=True))
            ds.put(context, "assumptions", _from_lines(assumptions))
            ds.put(sos, "context", context)
            apply("System updated")


def _actors(sos, key, apply, flags):
    columns = ["id", "role", "autonomy", "capabilities", "input", "output"]
    with st.form(f"actors_{key}", border=False):
        st.caption(
            "One row per actor. Use `NAME[1..N]` for a group. Lists are "
            "comma-separated. Add a row at the bottom; select rows to delete."
        )
        rows = st.data_editor(
            ds.actors_to_rows(sos) or _blank(columns),
            num_rows="dynamic", width="stretch", key=f"actors_table_{key}",
            column_config={
                "autonomy": st.column_config.SelectboxColumn(
                    options=ds.AUTONOMY_LEVELS, required=True, default="medium"),
                "input": st.column_config.TextColumn("input messages"),
                "output": st.column_config.TextColumn("output messages"),
            },
        )
        if st.form_submit_button("Apply", type="primary"):
            ds.rows_to_actors(sos, rows)
            apply("Actors updated")


def _contracts(sos, key, apply, flags):
    contracts = sos.get("contracts") or []
    ids = [str(c.get("id", f"contract_{i}")) for i, c in enumerate(contracts)]
    col_pick, col_del = st.columns([5, 1], vertical_alignment="bottom")
    with col_pick:
        picked = _pick("Contract", ids, "design_contract", flags.get("Contracts"))
    if col_del.button("Delete", width="stretch", disabled=picked is None,
                      help="Delete the selected contract", key="contract_del"):
        sos["contracts"] = [c for c in contracts if str(c.get("id")) != picked]
        apply(f"Deleted {picked}")

    col_template, col_add = st.columns([5, 1], vertical_alignment="bottom")
    template = col_template.selectbox(
        "New contract from", list(ds.CONTRACT_TEMPLATES), key="design_contract_template",
        format_func=lambda t: f"{t} — {ds.CONTRACT_TEMPLATES[t][0]}",
    )
    if col_add.button("Add", width="stretch", help="Add a contract from this template",
                      key="contract_add"):
        added = ds.contract_from_template(sos, template)
        sos.setdefault("contracts", []).append(added)
        ss.design_contract_next = added["id"]
        apply(f"Added {added['id']}")

    contract = next((c for c in contracts if str(c.get("id")) == picked), None)
    if contract is None:
        st.caption("No contracts yet. Choose a template and press **Add**.")
        return

    col_preset, col_use = st.columns([5, 1], vertical_alignment="bottom")
    preset = col_preset.selectbox(
        f"Lifecycle preset for {picked}", list(ds.LIFECYCLE_PRESETS), key="design_lifecycle_preset",
        help="Replaces this contract's lifecycle with a ready-made state machine.",
    )
    if col_use.button("Use", width="stretch", key="lifecycle_use",
                      help="Replace the lifecycle of the selected contract with this preset"):
        contract["lifecycle"] = copy.deepcopy(ds.LIFECYCLE_PRESETS[preset])
        apply(f"Lifecycle of {picked} replaced")

    actor_ids = [str(a.get("id", "")) for a in sos.get("actors") or []]
    parties_now = [str(p) for p in contract.get("parties") or []]
    authority = contract.get("authority") or {}
    information = contract.get("information") or {}
    incentives = contract.get("incentives") or {}
    violation = contract.get("violation") or {}
    lifecycle = contract.get("lifecycle") or {}
    holders = ["(none)"] + sorted({ds.actor_base(a) for a in actor_ids})
    holder_now = ds.actor_base(authority.get("decision_holder") or "") or "(none)"
    if holder_now not in holders:
        holders.append(holder_now)

    with st.form(f"contract_{picked}_{key}", border=False):
        cid = st.text_input("Contract id", picked)
        parties = st.multiselect(
            "Parties", sorted(set(_party_options(sos) + parties_now)), parties_now,
            accept_new_options=True,
            help="Actors bound by this contract. `NAME[*]` means every member of a group.",
        )
        c1, c2 = st.columns(2)
        assume = c1.text_area("Assume (one expression per line)", _lines(contract.get("assume")), height=100)
        guarantee = c2.text_area("Guarantee (one expression per line)", _lines(contract.get("guarantee")), height=100)

        st.markdown("**Authority** — who decides")
        a1, a2 = st.columns([2, 1])
        holder = a1.selectbox("Decision holder", holders, index=holders.index(holder_now))
        beta = a2.number_input(
            "β", 0.0, 1.0, authority.get("beta"), 0.05,
            help="How centralised decisions are, 0–1. Empty = not set.")
        a3, a4 = st.columns(2)
        scope = a3.text_input("Decision scope", authority.get("decision_scope", "") or "")
        mode = a4.text_input("Mode", authority.get("mode", "") or "",
                             help="For example `centralized` or `distributed`.")

        st.markdown("**Information** — who sees what")
        alpha = st.number_input(
            "α", 0.0, 1.0, information.get("alpha"), 0.05,
            help="How much information is shared, 0–1. Empty = not set.")
        view_rows = st.data_editor(
            ds.mapping_to_rows(information.get("views"), "actor", "view") or _blank(["actor", "view"]),
            num_rows="dynamic", width="stretch", key=f"views_{picked}_{key}",
        )
        sharing = st.text_area(
            "Sharing (one `A -> B : data` per line)", _lines(information.get("sharing")), height=80)

        st.markdown("**Incentives**")
        i1, i2 = st.columns([2, 1])
        incentive_type = i1.text_input("Type", incentives.get("type", "") or "")
        lam = i2.number_input(
            "λ", 0.0, 1.0, incentives.get("lambda"), 0.05,
            help="How strong the incentives are, 0–1. Empty = not set.")
        rules = st.text_area("Rules (one per line)", _lines(incentives.get("rules")), height=80)

        st.markdown("**Violation handling**")
        detect = st.text_input("Detect", violation.get("detect", "") or "")
        v1, v2 = st.columns(2)
        action = v1.text_input("Action", violation.get("action", "") or "")
        escalation = v2.text_input("Escalation", violation.get("escalation", "") or "")

        st.markdown("**Lifecycle** — states of one contract instance")
        l1, l2, l3 = st.columns([3, 1, 2])
        states = l1.text_input("States (comma-separated)", ds.join_list(lifecycle.get("states")))
        initial = l2.text_input("Initial", lifecycle.get("initial", "") or "")
        terminal = l3.text_input("Terminal (comma-separated)", ds.join_list(lifecycle.get("terminal")))
        st.caption(
            "Transitions: `from` may list several states. `deadline` takes a "
            "duration such as `5s`; give the state to move to when it is missed."
        )
        transition_cols = ["id", "from", "to", "on", "when", "deadline",
                           "on violation → state", "severity"]
        transition_rows = st.data_editor(
            ds.transitions_to_rows(lifecycle) or _blank(transition_cols),
            num_rows="dynamic", width="stretch", key=f"transitions_{picked}_{key}",
            column_config={"severity": st.column_config.SelectboxColumn(options=[""] + ds.SEVERITIES)},
        )

        st.markdown("**Monitors** — rules watched while the contract runs")
        monitor_cols = ["id", "observe", "sampling", "rule", "on match → state",
                        "on match → violation", "severity"]
        monitor_rows = st.data_editor(
            ds.monitors_to_rows(contract) or _blank(monitor_cols),
            num_rows="dynamic", width="stretch", key=f"monitors_{picked}_{key}",
            column_config={"severity": st.column_config.SelectboxColumn(options=[""] + ds.SEVERITIES)},
        )

        if st.form_submit_button("Apply contract", type="primary"):
            contract["id"] = cid.strip() or picked
            ds.put(contract, "parties", list(parties))
            ds.put(contract, "assume", _from_lines(assume))
            ds.put(contract, "guarantee", _from_lines(guarantee))

            authority = dict(authority)
            ds.put(authority, "decision_scope", scope.strip())
            ds.put(authority, "decision_holder", "" if holder == "(none)" else holder)
            ds.put(authority, "beta", beta)
            ds.put(authority, "mode", mode.strip())
            ds.put(contract, "authority", authority)

            information = dict(information)
            ds.put(information, "alpha", alpha)
            ds.put(information, "views", ds.rows_to_mapping(view_rows, "actor", "view"))
            ds.put(information, "sharing", _from_lines(sharing))
            ds.put(contract, "information", information)

            incentives = dict(incentives)
            ds.put(incentives, "type", incentive_type.strip())
            ds.put(incentives, "lambda", lam)
            ds.put(incentives, "rules", _from_lines(rules))
            ds.put(contract, "incentives", incentives)

            violation = dict(violation)
            ds.put(violation, "detect", detect.strip())
            ds.put(violation, "action", action.strip())
            ds.put(violation, "escalation", escalation.strip())
            ds.put(contract, "violation", violation)

            lifecycle = dict(lifecycle)
            ds.put(lifecycle, "states", ds.split_list(states))
            ds.put(lifecycle, "initial", initial.strip())
            ds.put(lifecycle, "terminal", ds.split_list(terminal))
            ds.rows_to_transitions(lifecycle, transition_rows)
            ds.put(contract, "lifecycle", lifecycle)
            ds.rows_to_monitors(contract, monitor_rows)

            ss.design_contract_next = contract["id"]
            apply(f"Contract {contract['id']} updated")


def _protocols(sos, key, apply, flags):
    protocols = sos.get("protocols") or []
    ids = [str(p.get("id", f"protocol_{i}")) for i, p in enumerate(protocols)]
    col_pick, col_add, col_del = st.columns([4, 1, 1], vertical_alignment="bottom")
    with col_pick:
        picked = _pick("Protocol", ids, "design_protocol", flags.get("Protocols"))
    if col_add.button("Add", width="stretch", help="Add a new protocol", key="protocol_add"):
        added = ds.new_protocol(sos)
        sos.setdefault("protocols", []).append(added)
        ss.design_protocol_next = added["id"]
        apply(f"Added {added['id']}")
    if col_del.button("Delete", width="stretch", disabled=picked is None,
                      help="Delete the selected protocol", key="protocol_del"):
        ds.put(sos, "protocols", [p for p in protocols if str(p.get("id")) != picked])
        apply(f"Deleted {picked}")

    protocol = next((p for p in protocols if str(p.get("id")) == picked), None)
    if protocol is None:
        st.caption("No protocols yet. Press **Add** to create one.")
        return

    steps_text, structured = ds.steps_to_text(protocol.get("steps"))
    rollback = protocol.get("rollback") if isinstance(protocol.get("rollback"), dict) else {}

    with st.form(f"protocol_{picked}_{key}", border=False):
        p1, p2 = st.columns(2)
        pid = p1.text_input("Protocol id", picked)
        trigger = p2.text_input("Trigger", protocol.get("trigger", "") or "",
                                help="The event that starts the protocol.")
        c1, c2 = st.columns(2)
        precondition = c1.text_input("Precondition", protocol.get("precondition", "") or "")
        postcondition = c2.text_input("Postcondition", protocol.get("postcondition", "") or "")
        invariant = st.text_input("Safety invariant", protocol.get("safety_invariant", "") or "")

        if structured:
            st.caption(
                "This protocol has nested steps (`if`, `parallel` or `barrier`), "
                "so its steps are edited as YAML."
            )
            steps = st.text_area("Steps (YAML list)", steps_text, height=220)
        else:
            steps = st.text_area(
                "Steps (one per line, in order)", steps_text, height=160,
                help="`A -> B : message` sends a message; `A : computation` is a local step.",
            )

        t1, t2 = st.columns(2)
        with t1:
            st.markdown("**Timing**")
            timing_rows = st.data_editor(
                ds.mapping_to_rows(protocol.get("timing"), "bound", "duration")
                or _blank(["bound", "duration"]),
                num_rows="dynamic", width="stretch", key=f"timing_{picked}_{key}",
            )
        with t2:
            st.markdown("**Fallback**")
            fallback_rows = st.data_editor(
                ds.mapping_to_rows(protocol.get("fallback"), "event", "action")
                or _blank(["event", "action"]),
                num_rows="dynamic", width="stretch", key=f"fallback_{picked}_{key}",
            )
        st.markdown("**Rollback**")
        r1, r2 = st.columns(2)
        rb_condition = r1.text_input("Condition", rollback.get("condition", "") or "")
        rb_action = r2.text_input("Action", rollback.get("action", "") or "")

        if st.form_submit_button("Apply protocol", type="primary"):
            try:
                new_steps = ds.text_to_steps(steps, structured)
            except ValueError as e:
                st.error(str(e))
                return
            protocol["id"] = pid.strip() or picked
            ds.put(protocol, "trigger", trigger.strip())
            ds.put(protocol, "precondition", precondition.strip())
            ds.put(protocol, "steps", new_steps)
            ds.put(protocol, "timing", ds.rows_to_mapping(timing_rows, "bound", "duration"))
            ds.put(protocol, "fallback", ds.rows_to_mapping(fallback_rows, "event", "action"))
            rollback = dict(rollback)
            ds.put(rollback, "condition", rb_condition.strip())
            ds.put(rollback, "action", rb_action.strip())
            ds.put(protocol, "rollback", rollback)
            ds.put(protocol, "postcondition", postcondition.strip())
            ds.put(protocol, "safety_invariant", invariant.strip())
            ss.design_protocol_next = protocol["id"]
            apply(f"Protocol {protocol['id']} updated")


def _algorithms(sos, key, apply, flags):
    columns = ["function", "central", "local"]
    with st.form(f"algorithms_{key}", border=False):
        st.caption(
            "For each function of the SoS (for example `pathfinding`), the "
            "algorithm run by the central authority and the one run locally by each actor."
        )
        rows = st.data_editor(
            ds.algorithms_to_rows(sos) or _blank(columns),
            num_rows="dynamic", width="stretch", key=f"algorithms_table_{key}",
        )
        if st.form_submit_button("Apply", type="primary"):
            ds.rows_to_algorithms(sos, rows)
            apply("Algorithms updated")


def _regimes(sos, key, apply, flags):
    columns = ["from", "to", "condition", "protocol", "safety_invariant"]
    protocol_ids = [""] + [str(p.get("id")) for p in sos.get("protocols") or []]
    with st.form(f"regimes_{key}", border=False):
        st.caption(
            "Operating regimes of the whole SoS (for example NORMAL, CONGESTED) "
            "and the transitions between them. `protocol` names the protocol "
            "that carries out the transition."
        )
        rows = st.data_editor(
            ds.records_to_rows(sos.get("transitions"), columns) or _blank(columns),
            num_rows="dynamic", width="stretch", key=f"regimes_table_{key}",
            column_config={
                "protocol": st.column_config.SelectboxColumn(options=protocol_ids),
                "safety_invariant": st.column_config.TextColumn("safety invariant"),
            },
        )
        if st.form_submit_button("Apply", type="primary"):
            ds.put(sos, "transitions", ds.rows_to_records(
                sos.get("transitions"), rows, columns, ["from", "to"], ["from", "to"]))
            apply("Regime transitions updated")


def _metrics(sos, key, apply, flags):
    columns = ["id", "formula", "target"]
    with st.form(f"metrics_{key}", border=False):
        st.caption("What the SoS is measured by: a formula and the target it should meet.")
        rows = st.data_editor(
            ds.records_to_rows(sos.get("metrics"), columns) or _blank(columns),
            num_rows="dynamic", width="stretch", key=f"metrics_table_{key}",
        )
        if st.form_submit_button("Apply", type="primary"):
            ds.put(sos, "metrics", ds.rows_to_records(sos.get("metrics"), rows, columns, ["id"]))
            apply("Metrics updated")


def _verification(sos, key, apply, flags):
    v_columns = ["id", "type", "target", "property", "method", "expr", "bound"]
    c_columns = ["target", "output"]
    with st.form(f"verification_{key}", border=False):
        st.markdown("**Verification directives**")
        st.caption(
            "Properties the verifier should check, on top of the built-in "
            "contract checks. `target` is a contract or protocol id."
        )
        v_rows = st.data_editor(
            ds.records_to_rows(sos.get("verification"), v_columns) or _blank(v_columns),
            num_rows="dynamic", width="stretch", key=f"verification_table_{key}",
            column_config={
                "type": st.column_config.SelectboxColumn(options=[""] + ds.VERIFICATION_TYPES),
                "method": st.column_config.SelectboxColumn(options=[""] + ds.VERIFICATION_METHODS),
            },
        )
        st.markdown("**Code generation**")
        st.caption("Targets to generate runtime code for, and where to write it.")
        c_rows = st.data_editor(
            ds.records_to_rows(sos.get("codegen"), c_columns) or _blank(c_columns),
            num_rows="dynamic", width="stretch", key=f"codegen_table_{key}",
            column_config={"target": st.column_config.SelectboxColumn(
                options=list(ds.CODEGEN_TARGETS))},
        )
        if st.form_submit_button("Apply", type="primary"):
            records = ds.rows_to_records(sos.get("verification"), v_rows, v_columns, ["id"])
            for record in records:
                bound = str(record.get("bound", ""))
                if bound.isdigit():
                    record["bound"] = int(bound)
            ds.put(sos, "verification", records)
            ds.put(sos, "codegen", ds.rows_to_records(sos.get("codegen"), c_rows, c_columns, ["target"]))
            apply("Verification and code generation updated")


_RENDER = {
    "System": _system, "Actors": _actors, "Contracts": _contracts,
    "Protocols": _protocols, "Algorithms": _algorithms, "Regimes": _regimes,
    "Metrics": _metrics, "Verification": _verification,
}


def section_counts(sos: dict) -> dict:
    """How many items each section holds, for the section labels."""
    algorithms = sos.get("algorithms")
    return {
        "Actors": len(sos.get("actors") or []),
        "Contracts": len(sos.get("contracts") or []),
        "Protocols": len(sos.get("protocols") or []),
        "Algorithms": len(algorithms) if isinstance(algorithms, dict) else 0,
        "Regimes": len(sos.get("transitions") or []),
        "Metrics": len(sos.get("metrics") or []),
        "Verification": len(sos.get("verification") or []),
    }


_ICON = {"error": ":material/error:", "warning": ":material/warning:", "info": ":material/info:"}


def _section_problems(findings, section: str, item) -> None:
    """Findings about this section (and the selected item) above its form."""
    relevant = [
        f for f in findings or []
        if f.section == section and f.level in ("error", "warning", "info")
        and (not f.item or item is None or f.item == item or section in ("Actors", "Metrics"))
    ]
    if not relevant:
        return
    with st.container(border=True):
        st.caption("Found by the checks in this section:")
        for f in relevant:
            st.markdown(f"{_ICON[f.level]} **{f.title}**" + (f" — {f.message}" if f.message else ""))


def render_forms(doc: dict, key: str, apply, flags: dict = None, findings=None) -> None:
    """Section picker plus the form of the chosen section."""
    sos = doc["sos"]
    counts = section_counts(sos)
    if ss.get("design_section") not in SECTIONS:
        ss.design_section = SECTIONS[0]
    section = st.radio(
        "Section", SECTIONS, key="design_section", horizontal=True, label_visibility="collapsed",
        format_func=lambda s: (f"{s} ({counts[s]})" if s in counts else s)
        + (" ⚠" if (flags or {}).get(s) else ""),
    )
    selected = {"Contracts": ss.get("design_contract_next") or ss.get("design_contract"),
                "Protocols": ss.get("design_protocol_next") or ss.get("design_protocol")}
    _section_problems(findings, section, selected.get(section))
    _RENDER[section](sos, key, apply, flags or {})
