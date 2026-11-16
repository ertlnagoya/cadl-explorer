"""The views that read the design back: diagrams, tables and sentences."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
import zipfile
from datetime import datetime

import streamlit as st

from backend.plotting.architecture import architecture_to_dot, regimes_to_dot
from backend.plotting.sequence import participants, sequence_svg
from backend.services import design_graph as graph
from backend.services import design_amend as amend
from backend.services import design_contracts as dcon
from backend.services import design_stamp as stamp
from backend.services import design_readback as readback
from backend.services import design_service as ds
from backend.services import design_workspace as workspace
from backend.services.cadl_service import MAX_SOURCE_CHARS
from cadl_sim.sos_dsl import build_lifecycle_view, lifecycle_to_dot, monitors_summary
from views._design.common import *  # noqa: F401,F403
from views._design.common import _md, _is_dark, _jump, _show_line, _trace_reset, _trace_step, _trace_back
from views._design import graph_component
from views._i18n import t


def _labels(*keys: str) -> dict:
    """Column labels in the reader's language; the column keys stay English."""
    return {k: st.column_config.Column(t(k)) for k in keys}


def _points(n: int) -> str:
    return t("{n} point" if n == 1 else "{n} points").format(n=n)


def _control_structure_view(ir: dict, source: str = ""):
    """STAMP control structure inferred from authority, the STPA table to fill in, and what watches each promise."""
    if not graph.actor_ids(ir):
        st.info(t("This design has no actors yet. Add them under **Forms → Actors**."))
        return
    cs = stamp.control_structure(ir)
    notes = stamp.findings(cs)
    if notes:
        with st.expander(t("What the control structure shows — {points}").format(points=_points(len(notes))),
                         expanded=len(notes) <= 6):
            for note in notes:
                st.markdown(f"- {note}")
    t_diagram, t_loops, t_uca, t_cover = st.tabs(
        [t("Diagram"), t("Control loops"), t("Unsafe control actions (STPA)"), t("Monitoring coverage")])

    with t_diagram:
        diagram(stamp.control_structure_dot(cs, dark=_is_dark()), "design_g_control", source, ir)
        st.caption(
            t("A STAMP control structure **inferred** from the CADL, which does not say which "
            "messages are control and which are feedback. A contract's decision holder is "
            "drawn above the parties it decides for. Red arrows down are *control actions* "
            "(its messages to them); dashed blue arrows up are *feedback* (their messages "
            "back, and monitors of the contract that observe them). A dotted red arrow is "
            "authority with nothing exchanged; dotted grey arrows are coordination between "
            "actors that do not control each other. A highlighted (red-tinted) box is "
            "controlled without feedback. When two actors control each other, a message "
            "between them counts as control for the sender and as feedback for the receiver."))

    with t_loops:
        if not cs.loops:
            st.info(t("No contract names a decision holder, so there are no control loops. "
                    "Set `authority.decision_holder` under **Forms → Contracts**."))
        else:
            st.dataframe([{
                "controller": l.controller, "controlled": l.controlled,
                "contracts": ", ".join(l.contracts), "control actions": ", ".join(l.control) or "—",
                "feedback": ", ".join(l.feedback) or "—", "loop": l.status,
            } for l in cs.loops], hide_index=True, width="stretch",
                column_config=_labels("controller", "controlled", "contracts", "control actions",
                                      "feedback", "loop"))
            st.caption(
                t("*closed*: control actions and feedback. *open: no feedback*: the controller "
                "acts without learning the effect — the typical cause of an unsafe control "
                "action in STAMP. *no control action*: informed but unable to act through the "
                "CADL. *authority only*: the contract gives the controller a say but nothing "
                "is exchanged."))

    with t_uca:
        rows = stamp.uca_rows(ir, cs)
        if not rows:
            st.info(t("There are no control actions to analyse yet."))
        else:
            st.markdown(
                t("STPA step 3: for every control action, write when each guide word would make it "
                "unsafe (or *n/a*). **What the CADL already says** below helps with the timing "
                "and the detection."))
            # Entries live in the session, keyed by the control action itself, so they
            # survive switching views and never move to another action when the design
            # changes. The editor's key follows the set of actions for the same reason.
            entries = ss.setdefault("design_uca_entries", {})
            rows = stamp.apply_entries(rows, entries)
            keys = [stamp.uca_key(r) for r in rows]
            table = [{"control action": k, **{g: r[g] for g in stamp.GUIDE_WORDS}}
                     for k, r in zip(keys, rows)]
            editor_key = "design_uca_" + hashlib.sha1("\n".join(keys).encode()).hexdigest()[:12]
            edited = st.data_editor(table, hide_index=True, width="stretch", key=editor_key,
                                    disabled=["control action"],
                                    column_config=_labels("control action", *stamp.GUIDE_WORDS))
            records = edited if isinstance(edited, list) else edited.to_dict("records")
            ss.design_uca_entries = stamp.update_entries(entries, keys, records)
            st.markdown(t("**What the CADL already says**"))
            st.dataframe([{"control action": row["control action"], "contracts": r["contracts"],
                           "deadlines": r["deadlines in the CADL"] or "—",
                           "detected by": r["detected by"] or "—", "feedback": r["feedback"]}
                          for row, r in zip(table, rows)], hide_index=True, width="stretch",
                         column_config=_labels("control action", "contracts", "deadlines",
                                               "detected by", "feedback"))
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(stamp.apply_entries(rows, ss.design_uca_entries))
            st.download_button(t("Download the table (CSV)"), buffer.getvalue(),
                               file_name="unsafe_control_actions.csv", mime="text/csv")
            st.caption(
                t("The four guide words are those of STPA (Leveson & Thomas, *STPA Handbook*, 2018). "
                "Entries stay with their control action while you switch views or edit the design, "
                "for this browser session only; download them to keep them. "
                "A deadline in the CADL bounds *too late*; a monitor that observes the controlled "
                "actor is a way the effect is *detected*; it does not prevent it."))

    with t_cover:
        promises = dcon.coverage(ir, source)
        if not promises:
            st.info(t("No contract states an assumption or a guarantee yet. Add them under "
                    "**Forms → Contracts**."))
        else:
            for note in dcon.coverage_findings(ir, promises):
                st.markdown(f"- {note}")
            st.dataframe([{
                "contract": p.contract, "kind": p.kind, "promise": p.text,
                "watched by": ", ".join(p.watched_by) or "—",
                "measured by": ", ".join(p.measured_by) or "—", "status": p.status,
            } for p in promises], hide_index=True, width="stretch",
                column_config=_labels("contract", "kind", "promise", "watched by", "measured by",
                                      "status"))
            st.caption(
                t("What checks each assumption and guarantee while the contract runs: a monitor "
                "of the contract whose `observe` or `rule` names one of its quantities, the "
                "contract's `violation.detect`, or a lifecycle deadline that equals its time "
                "bound (on a transition named in the quantity, such as `pickup` for "
                "`pickup_time`, or through an `# @amend:` name), or, in a design that declares "
                "norms 0.1, the norms monitor generated with the Python runtime, which decides "
                "every assumption and guarantee. *measured by*: a metric with "
                "the same name, which evaluates the run afterwards. The CADL does not link a "
                "promise to its monitor, so the links are found by name: a monitor that checks "
                "the same thing under another name is not counted."))


def _structure_view(ir: dict, source: str):
    """Message flow, centrality, N² chart and responsibilities of the design."""
    actors = graph.actor_ids(ir)
    if not actors:
        st.info(t("This design has no actors yet. Add them under **Forms → Actors**."))
        return
    duties = readback._responsibilities(source)
    gaps = graph.structure_gaps(ir, duties)
    if gaps:
        with st.expander(t("What the structure shows — {points}").format(points=_points(len(gaps))),
                         expanded=len(gaps) <= 6):
            for note in gaps:
                st.markdown(f"- {note}")
    t_flow, t_central, t_n2, t_resp, t_deps = st.tabs(
        [t("Message flow"), t("Centrality"), t("N² chart"), t("Responsibilities"), t("Contract dependencies")])

    with t_flow:
        show = st.toggle(t("Show who decides"), key="design_structure_authority",
                         help=t("Adds an edge from each contract's decision holder to the other parties."))
        if not graph.message_edges(ir):
            st.info(
                t("No messages yet: the design has no protocol steps, no lifecycle event "
                "written as `\"A -> B : msg\"` and no information sharing. Add a protocol "
                "under **Forms → Protocols** to see who talks to whom."))
        diagram(graph.message_flow_dot(ir, dark=_is_dark(), show_authority=show), "design_g_flow",
                source, ir)
        st.caption(
            t("Each arrow is what one actor sends another, from protocol steps, lifecycle "
            "events (`on: \"A -> B : msg\"`) and information sharing. A dashed arrow "
            "carries a message that a declared interface leaves out (amber); a thick "
            "violet arrow carries *decides* (the sender is the contract's decision "
            "holder). The more strongly an actor is coloured, the higher its betweenness "
            "(see **Centrality**)."))

    with t_central:
        rows = [{"actor": r["actor"], "betweenness": r["betweenness"], "partners": r["partners"],
                 "contracts": r["contracts"], "decides": r["decides"],
                 "out / in": f"{r['sends']} / {r['receives']}"} for r in graph.centrality(ir)]
        st.dataframe(rows, hide_index=True, width="stretch", column_config={
            **_labels("actor", "contracts"),
            "out / in": st.column_config.TextColumn(
                t("out / in"), help=t("Distinct messages it sends / receives.")),
            "betweenness": st.column_config.ProgressColumn(
                t("betweenness"), min_value=0.0, max_value=1.0, format="%.2f",
                help=t("Share of shortest message paths between two other actors that pass "
                       "through this one. A high value marks a relay: if it fails, others "
                       "cannot reach each other.")),
            "partners": st.column_config.NumberColumn(
                t("partners"), help=t("Actors it exchanges messages with.")),
            "decides": st.column_config.NumberColumn(
                t("decides"), help=t("Contracts it is the decision holder of.")),
        })
        st.caption(
            t("Counts come from the CADL only: messages are those of the **Message flow**, "
            "contracts those the actor is a party of. A group such as `ROBOT[1..N]` is one "
            "row. Centrality says where the structure concentrates; whether that is a risk "
            "depends on what the actor does."))

    with t_n2:
        matrix = graph.n2_matrix(ir)
        table = []
        for sender in actors:
            row = {"from ↓ to →": sender}
            for receiver in actors:
                cell = matrix.get(sender, {}).get(receiver) or []
                bad = sum(not e.declared for e in cell)
                row[receiver] = (f"{len(cell)}" + (f" ⚠{bad}" if bad else "")) if cell else (
                    "—" if sender == receiver else "")
            table.append(row)
        st.dataframe(table, hide_index=True, width="stretch", column_config=_labels("from ↓ to →"))
        st.caption(
            t("Row: sender. Column: receiver. A cell counts the distinct messages; ⚠ counts "
            "those a declared interface leaves out. Many filled cells in one row or column "
            "show an actor many others depend on; an empty row and column, an actor nobody "
            "talks to."))
        origins = {}
        for e in graph.message_edges(ir):
            origins.setdefault((e.sender, e.receiver, e.message), []).append(e.origin)
        pairs = [(s, r, cell) for s, row in matrix.items() for r, cell in row.items() if cell]
        if pairs:
            with st.expander(t("Messages by pair ({n})").format(n=len(pairs))):
                for s, r, cell in pairs:
                    items = ", ".join(
                        f"`{e.message}`" + ("" if e.declared else " ⚠") + " ("
                        + "; ".join(dict.fromkeys(origins[(s, r, e.message)])) + ")" for e in cell)
                    st.markdown(f"- **{s} → {r}**: {items}")

    with t_resp:
        contracts = [str(c.get("id")) for c in (ir.get("institution") or {}).get("contracts") or []]
        if not contracts:
            st.info(t("This design has no contracts yet."))
        else:
            resp = graph.responsibility_table(ir, duties)
            st.dataframe(
                [{"actor ↓": a, **{c: ", ".join(resp[a].get(c) or []) or "—" for c in contracts}}
                 for a in actors],
                hide_index=True, width="stretch", column_config=_labels("actor ↓"))
            st.caption(
                t("*decides*: the contract's decision holder. *party*: listed in `parties`. "
                "*duties (n)*: entries under `responsibilities:`. *observed*: a monitor "
                "observes one of its attributes. A contract column without *decides* has "
                "nobody to settle a dispute; an actor row of dashes takes part in nothing."))

    with t_deps:
        if len(dcon._contracts(ir)) < 2:
            st.info(t("Dependencies are between contracts; this design has fewer than two."))
        else:
            deps = dcon.dependencies(ir)
            for note in dcon.dependency_findings(ir, deps):
                st.markdown(f"- {note}")
            diagram(dcon.dependency_dot(ir, dark=_is_dark()), "design_g_deps", source, ir)
            sources = dcon.assumption_sources(ir, deps)
            if sources:
                st.dataframe(sources, hide_index=True, width="stretch",
                             column_config={**_labels("contract", "assumption"),
                                            "guaranteed by": st.column_config.TextColumn(
                                 t("guaranteed by"), help=t("Contracts whose guarantee names a "
                                                            "quantity of this assumption."))})
            else:
                st.markdown(t("- No contract states an assumption (`assume:`), so none relies on "
                            "what another guarantees."))
            st.caption(
                t("Each box is a contract with its parties and decision holder. A dashed line joins "
                "two contracts that share actors: a change to one can affect how those actors "
                "meet the other. A blue arrow runs from a contract to one whose assumption it "
                "guarantees (it names the same quantity): the second relies on the first, as in "
                "assume-guarantee reasoning. The links are found by name; they are not proved."))



def _node_name(node_id: str) -> str:
    kind, _, name = node_id.partition(":")
    return name if name and kind in ("actor", "contract", "regime", "state") else node_id


def _dot_nodes(dot: str) -> list:
    return list(dict.fromkeys(m.group(1) for m in re.finditer(r'^\s*"([^"]+)"\s*\[', dot, re.M)))


def _close(key: str):
    ss.pop(f"{key}_chosen", None)
    ss.pop(f"{key}_pick", None)


def diagram(dot: str, key: str, source: str, ir: dict):
    """A diagram whose boxes can be chosen (clicked, tapped, or picked from a list where
    the diagram cannot take clicks); the chosen element is shown in the source below."""
    if graph_component.available():
        clicked = graph_component.clickable_graph(
            dot, key=key, chosen=ss.get(f"{key}_chosen"),
            hint=t("Click or tap a box to see where the design writes it."),
            failed=t("The diagram needs viz.js from the network, which did not load; reload the "
                     "page to draw it without it."))
        if clicked:
            ss[f"{key}_chosen"] = clicked
    else:
        st.graphviz_chart(dot, width="stretch")
        nodes = _dot_nodes(dot)
        if nodes:
            pick = st.selectbox(t("Show an element in the source"), [""] + nodes, key=f"{key}_pick",
                                format_func=lambda n: _node_name(n) if n else t("(choose one)"))
            ss[f"{key}_chosen"] = pick or None
    chosen = ss.get(f"{key}_chosen")
    if chosen:
        _element_panel(source, chosen, key, ir)


def _element_panel(source: str, node_id: str, key: str, ir: dict):
    """Where the design writes one element of a diagram, with a way to edit it there."""
    name = _node_name(node_id)
    base = name.split("[", 1)[0]
    institution = (ir or {}).get("institution") or {}
    actors = {str(a.get("id", "")).split("[", 1)[0] for a in institution.get("actors") or []}
    contracts = {str(x.get("id", "")) for x in institution.get("contracts") or []}
    section = "Actors" if base in actors else "Contracts" if name in contracts else None
    line = ds.find_line(source, name, base)
    with st.container(border=True):
        lines = source.splitlines()
        if line:
            first, last = max(1, line - 1), min(len(lines), line + 9)
            st.markdown(f"**{_md(name)}** — " + t("line") + f" {line}")
            st.code("\n".join(f"{n:>4}  {lines[n - 1]}" for n in range(first, last + 1)),
                    language="yaml")
        else:
            st.markdown(f"**{_md(name)}** — " + t("not written in the source by this name"))
        with st.container(horizontal=True):
            if line:
                st.button(t("Edit at line {n}").format(n=line), key=f"{key}_edit", on_click=_show_line,
                          args=(line,), icon=":material/code:")
            if section:
                st.button(t("Open form"), key=f"{key}_form", on_click=_jump,
                          args=(section, name if section == "Contracts" else ""))
            st.button(t("Close"), key=f"{key}_close", on_click=_close, args=(key,))


def architecture(c):
    source, analysis, flags, ir, ir_contracts = c.source, c.analysis, c.flags, c.ir, c.ir_contracts
    n_actors = len(ir["institution"].get("actors") or [])
    n_contracts = len(ir_contracts)
    st.markdown(
        f"**{analysis.name}** · {analysis.sos_type} · "
        + t("{n} actor" if n_actors == 1 else "{n} actors").format(n=n_actors) + t(", ")
        + t("{n} contract" if n_contracts == 1 else "{n} contracts").format(n=n_contracts)
    )
    diagram(architecture_to_dot(ir, dark=_is_dark(), flags=flags), "design_g_arch", source, ir)
    st.caption(
        t("Blue boxes are actors (darker = more autonomous); rounded boxes are "
        "contracts with their α / β / λ. A bold *decides* edge marks the "
        "contract's decision holder. A red or orange outline marks an item "
        "with an error or a warning.")
    )


def structure(c):
    _structure_view(c.ir, c.source)


def control_structure(c):
    _control_structure_view(c.ir, c.source)


def lifecycle(c):
    source, analysis, ir, ir_contracts = c.source, c.analysis, c.ir, c.ir_contracts
    if not ir_contracts:
        st.info(t("This design has no contracts yet. Add one under **Forms → Contracts**."))
    else:
        cids = [str(c.get("id")) for c in ir_contracts]
        if ss.get("design_life_contract") not in cids:
            ss.pop("design_life_contract", None)
        pick = st.radio(t("Contract"), cids, horizontal=True, key="design_life_contract")
        ir_contract = next(c for c in ir_contracts if str(c.get("id")) == pick)
        lc_view = build_lifecycle_view(ir_contract)
        if lc_view is None:
            st.info(
                t("`{contract}` has no `lifecycle:` section. Add states and transitions "
                  "under **Forms → Contracts**, or apply a lifecycle preset there.").format(contract=pick)
            )
        else:
            trace = ss.get("design_trace") or {}
            path = trace.get("path") or []
            tracing = (
                trace.get("contract") == pick and path
                and all(state in lc_view.states for state in path)
            )
            if not tracing:
                path = []
            bad_states = {
                f.element for f in analysis.findings
                if f.stage == "Lifecycle" and f.item == pick and f.element
            }
            diagram(lifecycle_to_dot(
                lc_view, dark=_is_dark(),
                current=path[-1] if path else None, visited=path, flagged=bad_states),
                f"design_g_life_{pick}", source, ir)
            st.caption(
                t("{states} states, {transitions} transitions. "
                  "Double circle = initial, dashed box = terminal, dashed red edge = "
                  "transition taken when a deadline is missed.").format(
                    states=len(lc_view.states), transitions=len(lc_view.transitions))
                + (t(" Red outline = state with a problem.") if bad_states else "")
            )

            with st.container(border=True):
                st.markdown(t("**Step through one contract instance**"))
                if not tracing:
                    st.caption(
                        t("Follow a single instance of this contract from its initial "
                        "state, choosing what happens at each step.")
                    )
                    st.button(
                        t("Start at the initial state"), on_click=_trace_reset,
                        args=(pick, lc_view.initial), disabled=not lc_view.initial,
                    )
                else:
                    current = path[-1]
                    st.markdown(t("Path: ") + " → ".join(
                        f"**{s}**" if i == len(path) - 1 else s for i, s in enumerate(path)))
                    moves = ds.lifecycle_moves(ir_contract, current)
                    if current in lc_view.terminal:
                        st.success(t("`{state}` is terminal: this instance has ended.").format(state=current))
                    elif not moves:
                        st.warning(t("Nothing can happen in `{state}`: the instance is stuck.")
                                   .format(state=current))
                    else:
                        st.caption(t("In `{state}`, choose what happens next:").format(state=current))
                        icons = {"transition": ":material/arrow_forward:",
                                 "deadline": ":material/timer_off:",
                                 "monitor": ":material/visibility:"}
                        for i, move in enumerate(moves):
                            st.button(
                                f"{move.label} → {move.target}", key=f"trace_{len(path)}_{i}",
                                icon=icons[move.kind], width="stretch",
                                on_click=_trace_step, args=(move.target,),
                                help=move.detail or None,
                            )
                    c_back, c_reset, _ = st.columns([1, 1, 3])
                    c_back.button(t("Back"), on_click=_trace_back, disabled=len(path) < 2,
                                  width="stretch", key="trace_back")
                    c_reset.button(t("Restart"), on_click=_trace_reset,
                                   args=(pick, lc_view.initial), width="stretch",
                                   key="trace_restart")

        if lc_view is not None:
            with st.expander(t("Every way an instance can run, as stories")):
                try:
                    stories = readback.lifecycle_stories(source, pick)
                except ValueError as e:
                    stories = []
                    st.caption(t("Cannot tell the stories: {error}").format(error=e))
                st.caption(
                    t("Each story follows one instance from start to end, visiting a "
                    "state at most once. Read them and ask: should this be possible?"))
                for story in stories:
                    st.markdown(_md(f"**{story['outcome'].capitalize()}** — {story['story']}"))

        monitors = monitors_summary(ir_contract)
        st.markdown(f"**{t('Monitors')}** ({len(monitors)})")
        if monitors:
            st.dataframe(monitors, width="stretch", hide_index=True,
                         column_config=_labels(*monitors[0]))
        else:
            st.caption(t("No `monitors:` declared on this contract."))


def protocols(c):
    analysis, ir = c.analysis, c.ir
    ir_protocols = (ir.get("protocol") or {}).get("protocols") or []
    if not ir_protocols:
        st.info(t("This design has no protocols yet. Add one under **Forms → Protocols**."))
    else:
        pids = [str(p.get("id")) for p in ir_protocols]
        if ss.get("design_view_protocol") not in pids:
            ss.pop("design_view_protocol", None)
        pick = st.radio(t("Protocol"), pids, horizontal=True, key="design_view_protocol")
        proto = next(p for p in ir_protocols if str(p.get("id")) == pick)
        st.markdown(f"**{t('Trigger')}** `{proto.get('trigger') or '—'}`")
        bad_messages = {
            f.element for f in analysis.findings
            if f.section == "Protocols" and f.item == pick and f.element
            and f.level in ("error", "warning")
        }
        st.markdown(sequence_svg(proto, dark=_is_dark(), flagged=bad_messages),
                    unsafe_allow_html=True)
        n_steps = len(proto.get("steps") or [])
        st.caption(
            t("{n} step between {actors} actors, top to bottom. Arrows are messages, "
              "rounded boxes are local computations, grey bands mark `if`, "
              "`parallel` and `barrier`." if n_steps == 1 else
              "{n} steps between {actors} actors, top to bottom. Arrows are messages, "
              "rounded boxes are local computations, grey bands mark `if`, "
              "`parallel` and `barrier`.").format(n=n_steps, actors=len(participants(proto)))
            + (t(" Red arrows are messages with a problem.") if bad_messages else "")
        )
        for f in analysis.findings:
            if f.section == "Protocols" and f.item == pick and f.level in ("error", "warning"):
                st.markdown(f"{LEVEL_ICON[f.level]} {f.title}" + (f" — {f.message}" if f.message else ""))
        facts = [
            {"item": label, "value": str(proto.get(label))}
            for label in ("precondition", "postcondition") if proto.get(label)
        ]
        facts += [{"item": f"timing · {k}", "value": str(v)} for k, v in (proto.get("timing") or {}).items()]
        facts += [{"item": f"fallback · {k}", "value": str(v)} for k, v in (proto.get("fallback") or {}).items()]
        if facts:
            st.dataframe(facts, width="stretch", hide_index=True,
                         column_config=_labels("item", "value"))


def regimes(c):
    analysis, ir = c.analysis, c.ir
    if not ir.get("transitions"):
        st.info(
            t("This design has no regime transitions. Add them under "
            "**Forms → Regimes** to describe how the SoS moves between "
            "operating regimes such as NORMAL and EMERGENCY.")
        )
    else:
        regime_findings = [
            f for f in analysis.findings
            if f.section == "Regimes" and f.level in ("error", "warning")
        ]
        diagram(
            regimes_to_dot(ir, dark=_is_dark(),
                           flagged={f.element for f in regime_findings if f.element}),
            "design_g_regimes", c.source, ir)
        st.caption(
            t("Operating regimes of the whole SoS. Each edge shows the condition "
              "for the transition; orange edges name the protocol that carries it out.")
            + (t(" Red marks a regime or transition with a problem.") if regime_findings else "")
        )
        for f in regime_findings:
            st.markdown(f"{LEVEL_ICON[f.level]} {f.title}" + (f" — {f.message}" if f.message else ""))
        _regime_tables(ir)


def _regime_tables(ir: dict):
    """What each regime binds (`regimes:`, cadl 0.3.32) and the safety invariants
    of the transitions."""
    bindings = ds.regime_bindings(ir)
    if bindings:
        st.markdown(t("**What each regime binds**"))
        # a list, which wraps in a narrow column; a table would hide the contracts
        lines = []
        for b in bindings:
            parts = [t("in force: {list}").format(list=", ".join(b["active_contracts"]) or "—")]
            if b["suspended_contracts"]:
                parts.append(t("suspended: {list}").format(list=", ".join(b["suspended_contracts"])))
            if b["parameters"]:
                parts.append(t("values: {list}").format(list=", ".join(
                    f"`{k}` = {_value_text(v)}" for k, v in b["parameters"].items())))
            lines.append(f"- **{b['name']}** — " + " · ".join(parts))
        st.markdown("\n".join(lines))
        st.caption(t("From the `regimes:` block. A parameter that a regime does not bind keeps "
                     "its declared value; a contract that no regime suspends is in force in "
                     "every regime. Contracts that no regime has in force together are not "
                     "required to agree: a conflict between them is a warning."))
    invariants = [{"transition": f"{tr.get('from_regime')} → {tr.get('to_regime')}",
                   "safety invariant": tr["safety_invariant"],
                   "condition": tr.get("condition") or "—"}
                  for tr in ir.get("transitions") or [] if tr.get("safety_invariant")]
    if invariants:
        st.markdown(t("**Safety invariants of the transitions**"))
        st.dataframe(invariants, width="stretch", hide_index=True,
                     column_config=_labels("transition", "safety invariant", "condition"))


def _value_text(value) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def algorithms(c):
    analysis, ir = c.analysis, c.ir
    algorithms = (ir.get("algorithm") or {}).get("algorithms") or []
    st.markdown(f"**{t('Algorithms')}** ({len(algorithms)})")
    if algorithms:
        st.dataframe(
            [{"function": a.get("name"), "central": a.get("central") or "—",
              "local": a.get("local") or "—"} for a in algorithms],
            width="stretch", hide_index=True, column_config=_labels("function"),
        )
        st.caption(t("`central` runs at the authority, `local` at each actor."))
    else:
        st.caption(t("None declared. Add them under **Forms → Algorithms**."))

    metrics = ir.get("metrics") or []
    st.markdown(f"**{t('Metrics')}** ({len(metrics)})")
    if metrics:
        metric_notes = {}
        for f in analysis.findings:
            if f.section == "Metrics" and f.item and f.level in ("error", "warning", "info"):
                metric_notes.setdefault(f.item, []).append(f.title)
        st.dataframe(
            [{"id": m.get("id"), "formula": m.get("formula") or "—",
              "target": m.get("target") or "—",
              "notes": "; ".join(metric_notes.get(str(m.get("id")), []))} for m in metrics],
            width="stretch", hide_index=True, column_config=_labels("formula", "target", "notes"),
        )
    else:
        st.caption(t("None declared. Add them under **Forms → Metrics**."))

    environment = ir.get("environment") or {}
    if environment:
        st.markdown(f"**{t('Environment')}** ({len(environment)})")
        st.dataframe(
            [{"parameter": k, "value": str(v)} for k, v in environment.items()],
            width="stretch", hide_index=True, column_config=_labels("parameter", "value"),
        )


def read_back(c):
    source, ir = c.source, c.ir
    st.caption(
        t("The design restated in plain sentences by fixed rules — it says what the "
        "design says. Use it to confirm that this is what you meant; the checks "
        "cannot tell you that.")
    )
    described = readback.describe_design(source)
    st.markdown(t("**Contracts**"))
    if not described:
        st.caption(t("No contracts yet."))
    for cid, lines in described.items():
        with st.expander(cid, expanded=len(described) <= 2):
            for line in lines:
                st.markdown(_md(f"- {line}"))
    game_lines = readback.describe_model(source)
    if game_lines:
        st.markdown(t("**Strategic model**"))
        for line in game_lines:
            st.markdown(_md(f"- {line}"))

    actor_ids = [str(a.get("id")) for a in ir["institution"].get("actors") or []]
    if actor_ids:
        st.markdown(t("**One actor's point of view**"))
        if ss.get("design_readback_actor") not in actor_ids:
            ss.pop("design_readback_actor", None)
        who = st.radio(t("Actor"), actor_ids, horizontal=True, key="design_readback_actor",
                       label_visibility="collapsed")
        seen = readback.actor_view(source, who)
        st.markdown(_md(seen["summary"]))
        for heading, key, empty in (
                ("Bound by", "contracts", "Not bound by any contract."),
                ("Takes part in", "protocols", "Takes part in no protocol."),
                ("Open issues", "notes", None)):
            if seen[key]:
                st.markdown(f"*{t(heading)}*")
                for line in seen[key]:
                    st.markdown(_md(f"- {line}"))
            elif empty:
                st.caption(t(empty))

    explained = readback.explain_verification(source)
    if explained:
        st.markdown(t("**What verification concluded**"))
        for row in explained:
            st.markdown(_md(f"{LEVEL_ICON[row['result']]} **{row['check']}** — {row['meaning']}"))
