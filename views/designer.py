"""Designer page — author a full CADL design and check it as you go.

Left: the CADL source and forms that edit the same document.
Right: checks and one view at a time (diagrams, tables, versions, export).
The source text in st.session_state.design_src is the single source of
truth; forms read it and write it back.
"""

import csv
import hashlib
import io
import json
import re
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
from views._designer_forms import SECTIONS, render_forms

try:
    from streamlit_ace import st_ace
except ImportError:  # the plain text area is the fallback editor
    st_ace = None

SPEC_URL = "https://www.ertl.jp/cadl-spec/"
DEFAULT_EXAMPLE = "sos dsl robot delivery"
LEVEL_ICON = {
    "error": ":material/error:", "warning": ":material/warning:",
    "info": ":material/info:", "pass": ":material/check_circle:",
}
VIEWS = [
    "Architecture", "Structure", "Control structure", "Lifecycle", "Protocols", "Regimes",
    "Algorithms & metrics", "Read-back", "Checks", "Versions", "Export",
]
MAX_HISTORY = 50
MAX_VERSIONS = 20
BUNDLED_CONSTITUTION = ds.DESIGNS_DIR / "robot_town.constitution.yaml"

ss = st.session_state


# ── State ───────────────────────────────────────────────────────────

def _record():
    """Remember the current source so the next change can be undone."""
    history = ss.setdefault("design_history", [])
    if "design_src" in ss and (not history or history[-1] != ss.design_src):
        history.append(ss.design_src)
        del history[:-MAX_HISTORY]
    ss.design_redo = []


def _set_source(text: str, record: bool = True):
    """Replace the design; the editor is rebuilt with the new text."""
    if record:
        _record()
    ss.design_src = text
    ss.design_ver = ss.get("design_ver", 0) + 1


def _on_edit():
    text = ss[f"design_editor_{ss.design_ver}"]
    if text != ss.design_src:
        _record()
        ss.design_src = text


def _undo():
    if ss.get("design_history"):
        ss.setdefault("design_redo", []).append(ss.design_src)
        _set_source(ss.design_history.pop(), record=False)


def _redo():
    if ss.get("design_redo"):
        ss.setdefault("design_history", []).append(ss.design_src)
        _set_source(ss.design_redo.pop(), record=False)


def _load_example():
    _set_source(ds.load_example(ss.design_example))
    ss.design_file_name = ""


def _new_design():
    _set_source(ds.NEW_DESIGN)
    ss.design_file_name = ""


def _jump(section: str, item: str = ""):
    """Open the form that fixes a finding."""
    ss.design_mode = "Forms"
    ss.design_section = section if section in SECTIONS else SECTIONS[0]
    if item and section == "Contracts":
        ss.design_contract_next = item
    if item and section == "Protocols":
        ss.design_protocol_next = item


def _show(view: str, key: str = None, item: str = ""):
    """Open the view that shows a finding, selecting its contract or protocol."""
    ss.design_view = view
    if key and item:
        ss[key] = item


def _problem_buttons(finding, key: str):
    """'Open form' and 'Show in view' for one finding, side by side."""
    target = ds.view_of(finding)
    if not (finding.section or target):
        return
    with st.container(horizontal=True):
        if finding.section:
            st.button(
                "Open form", key=f"{key}_form", on_click=_jump,
                args=(finding.section, finding.item),
                help=f"Open Forms → {finding.section}"
                     + (f" → {finding.item}" if finding.item else "") + ", where this can be fixed.",
            )
        if target:
            st.button(
                f"Show in {target[0]}", key=f"{key}_view", on_click=_show,
                args=(target[0], target[1], finding.item),
                help="Open the view where this item is outlined in red or orange.",
            )


def _open_workspace():
    name = ss.design_ws_pick
    _set_source(workspace.read(name))
    ss.design_ws_name = name
    ss.design_file_name = name


def _save_workspace():
    workspace.write(ss.design_ws_name, ss.design_src, note="saved from the Designer")


def _mark_reviewed(index=None):
    workspace.mark_reviewed(ss.design_ws_name, None if index is None else [index])


def _add_version(base: str, source: str) -> str:
    versions = ss.setdefault("design_versions", [])
    base = base.strip() or f"Version {len(versions) + 1}"
    taken = {v["name"] for v in versions}
    name, n = base, 2
    while name in taken:            # the comparison lists versions by name
        name, n = f"{base} ({n})", n + 1
    versions.append({
        "name": name, "time": datetime.now().strftime("%H:%M:%S"), "source": source,
    })
    del versions[:-MAX_VERSIONS]
    return name


def _save_version():
    name = _add_version(ss.get("design_version_name") or "", ss.design_src)
    ss.design_version_name = ""
    # Compare the version just saved with whatever is opened next.
    ss.design_cmp_a, ss.design_cmp_b = f"Version: {name}", "Current design"


def _natural(name: str):
    """v2 before v10."""
    return [int(t) if i % 2 else t.lower() for i, t in enumerate(re.split(r"(\d+)", name))]


def _add_files_as_versions():
    """Course D writes runs/<name>/v1.cadl, v2.cadl, ...: add them all, in order, as versions."""
    key = f"design_version_files_{ss.get('design_version_files_key', 0)}"
    files = sorted(ss.get(key) or [], key=lambda f: _natural(f.name))
    prefix = (ss.get("design_version_prefix") or "").strip()
    before = [v["name"] for v in ss.get("design_versions", [])]
    added, skipped = [], []
    for f in files:
        if f.size > MAX_SOURCE_CHARS * 4:
            skipped.append(f"{f.name} (too large; the limit is {MAX_SOURCE_CHARS:,} characters)")
            continue
        try:
            text = f.getvalue().decode("utf-8-sig")
        except UnicodeDecodeError:
            skipped.append(f"{f.name} (not UTF-8 text)")
            continue
        if len(text) > MAX_SOURCE_CHARS:
            skipped.append(f"{f.name} ({len(text):,} characters; the limit is {MAX_SOURCE_CHARS:,})")
            continue
        stem = f.name.rsplit(".", 1)[0]
        added.append(_add_version(f"{prefix} {stem}" if prefix else stem, text))
    kept = {v["name"] for v in ss.get("design_versions", [])}
    ss.design_files_added = {"added": [n for n in added if n in kept], "skipped": skipped,
                             "dropped": [n for n in dict.fromkeys(before + added) if n not in kept]}
    if len(added) > 1 and added[0] in kept:
        # Compare the first and the last of the run.
        ss.design_cmp_a, ss.design_cmp_b = f"Version: {added[0]}", f"Version: {added[-1]}"
    ss.design_version_files_key = ss.get("design_version_files_key", 0) + 1   # empties the uploader


def _restore_version(index: int):
    _set_source(ss.design_versions[index]["source"])


def _delete_version(index: int):
    ss.design_versions.pop(index)


def _trace_reset(contract_id: str, initial: str):
    ss.design_trace = {"contract": contract_id, "path": [initial]}


def _trace_step(target: str):
    ss.design_trace["path"].append(target)


def _trace_back():
    if len(ss.design_trace["path"]) > 1:
        ss.design_trace["path"].pop()


def _md(text: str) -> str:
    """Keep `ROBOT[*]` literal: Markdown would read the asterisks as emphasis."""
    return str(text).replace("[*]", "[\\*]")


def _is_dark() -> bool:
    theme = getattr(st.context, "theme", None)
    return getattr(theme, "type", None) == "dark"


def _amend_apply(name: str, old: float, new: float, layer: str = ""):
    """Write the new value into every line marked with ``name``, keeping the comments.

    The decision — with the reason, when the constitution leaves it to a
    person — is kept in the session's decision record.
    """
    reason = (ss.get(f"design_amend_reason_{name}") or "").strip()
    ss.design_version_name = f"before {name} {amend.fmt(old)} → {amend.fmt(new)}"
    _save_version()
    _set_source(amend.set_values(ss.design_src, {name: new}))
    ss.setdefault("design_decisions", []).append({
        "time": datetime.now().strftime("%H:%M:%S"), "change": f"{name}: {amend.fmt(old)} → {amend.fmt(new)}",
        "layer": layer, "reason": reason})
    ss.design_amend_done = f"{name}: {amend.fmt(old)} → {amend.fmt(new)}"


def _use_bundled_constitution():
    ss.design_constitution = BUNDLED_CONSTITUTION.read_text(encoding="utf-8")
    ss.design_constitution_name = BUNDLED_CONSTITUTION.name


def _drop_constitution():
    ss.pop("design_constitution", None)
    ss.pop("design_constitution_name", None)
    ss.design_constitution_key = ss.get("design_constitution_key", 0) + 1   # empties the uploader


def _amendments_section(source: str, versions: list):
    """The `# @amend:` values, checked against a constitution, amended in place, and their history."""
    params = amend.amendables(source)
    if not params:
        return
    st.markdown("**Amendable parameters**")
    st.caption(
        "Values marked `# @amend:<name>` (Robot Town, Course D). A constitution "
        "(`constitution.yaml`) gives each name its layer, range and step. Amending here "
        "changes the number on every marked line and keeps the comments; the forms and the "
        "source editor's other edits would drop them.")
    up, use = st.columns([3, 2], vertical_alignment="bottom")
    uploaded = up.file_uploader("Constitution", type=["yaml", "yml"],
                                key=f"design_constitution_file_{ss.get('design_constitution_key', 0)}",
                                help="The constitution.yaml of the Course D repository, or your own.")
    if uploaded is not None and ss.get("design_constitution_upload") != uploaded.file_id:
        ss.design_constitution_upload = uploaded.file_id     # read each upload once
        ss.design_constitution = uploaded.getvalue().decode("utf-8", errors="replace")
        ss.design_constitution_name = uploaded.name
    if ss.get("design_constitution"):
        use.button(f"Stop using {ss.get('design_constitution_name', 'the constitution')}",
                   on_click=_drop_constitution, width="stretch")
    elif BUNDLED_CONSTITUTION.exists():
        use.button("Use the Robot Town constitution", on_click=_use_bundled_constitution,
                   width="stretch", help="The constitution.yaml bundled with the robot town example.")
    const = None
    if ss.get("design_constitution"):
        try:
            const = amend.load_constitution(ss.design_constitution)
        except ValueError as e:
            st.warning(f"{ss.get('design_constitution_name', 'The constitution')}: {e}")
    rows = []
    for a in params:
        spec = (const or {}).get(a.name)
        rows.append({
            "parameter": a.name, "value": amend.fmt(a.value),
            "lines": ", ".join(str(o.line) for o in a.occurrences),
            **({"layer": amend.layer_of(spec),
                "range": (f"{amend.fmt(spec.get('min'))}–{amend.fmt(spec.get('max'))}" if spec else "—"),
                "step": amend.fmt(spec.get("step")) if spec else "—",
                "decided by": ("a person" if amend.needs_human(spec) else "the checks") if spec
                              else "not amendable"} if const is not None else {}),
        })
    st.dataframe(rows, hide_index=True, width="stretch")
    for note in amend.findings(source, const):
        st.markdown(f"- {note}")
    if ss.get("design_amend_done"):
        st.success(f"Amended {ss.pop('design_amend_done')}. The design before it is saved as a "
                   "version; Undo also takes it back.")

    names = [a.name for a in params if a.value is not None]
    if names:
        c_name, c_value = st.columns(2)
        name = c_name.selectbox("Amend", names, key="design_amend_name")
        current = next(a for a in params if a.name == name).value
        spec = (const or {}).get(name) or {}
        step = float(spec.get("step") or (1.0 if current == int(current) else 0.1))
        whole = step == int(step) and current == int(current)   # integers stay integers on screen
        value = c_value.number_input(f"New value (now {amend.fmt(current)})",
                                     value=int(current) if whole else float(current),
                                     step=int(step) if whole else step,
                                     min_value=0 if whole else 0.0,   # written without a sign
                                     key=f"design_amend_value_{name}_{amend.fmt(current)}")
        value = float(value)
        if value != current:
            gate = amend.check_amendment(source, amend.set_values(source, {name: value}), const)
            if const is None:
                st.caption("No constitution: only the consistency of the marked lines is checked.")
            for reason in gate.reasons:
                st.markdown(f"- ⚠ {reason}")
            if const is not None and gate.ok:
                st.markdown("- The constitution allows this amendment.")
            reason = ""
            if gate.for_a_person:
                st.markdown(f"- The constitution leaves `{name}` ({amend.layer_of(spec)}) to a "
                            "person. Write the reason for your decision; it is kept in the "
                            "decision record below.")
                reason = st.text_input("Reason for the decision", key=f"design_amend_reason_{name}",
                                       placeholder="why this change, and what you take for granted")
            blocked = const is not None and not gate.ok
            st.button(f"Amend {name} to {amend.fmt(value)}", on_click=_amend_apply,
                      args=(name, current, value, amend.layer_of(spec) if spec else ""), type="primary",
                      disabled=blocked or (bool(gate.for_a_person) and not reason.strip()),
                      help=("The constitution does not allow this amendment." if blocked else
                            "Saves the current design as a version first, then writes the new value "
                            "into every line marked with this name."))

    record = ss.get("design_decisions") or []
    if record:
        with st.expander(f"Decision record ({len(record)})"):
            st.dataframe(record, hide_index=True, width="stretch")
            text = "\n".join(f"- {d['time']} {d['change']}" + (f" ({d['layer']})" if d["layer"] else "")
                             + (f" — {d['reason']}" if d["reason"] else "") for d in record)
            st.download_button("Download the record (Markdown)", "# Decisions\n\n" + text + "\n",
                               file_name="decisions.md", mime="text/markdown")
            st.caption("The amendments made here in this browser session, with the reasons given. "
                       "Download the record to keep it.")

    _history_section(source, versions, const)


def _constitution() -> dict | None:
    """The constitution in use, or None when there is none or it cannot be read."""
    try:
        return amend.load_constitution(ss.design_constitution) if ss.get("design_constitution") else None
    except ValueError:
        return None


def _history_section(source: str, versions: list, const: dict | None):
    """How the `# @amend:` values changed across the saved versions (Course D, Exercise 12-1)."""
    if versions:
        sequence = [(v["name"], v["source"]) for v in versions] + [("current", source)]
        table = amend.history(sequence)
        if table:
            with st.expander("History of the amendable values across the versions"):
                st.dataframe(table, hide_index=True, width="stretch")
                st.caption("One column per saved version, then the current design; ← marks a "
                           "value that differs from the column before.")
                moved = amend.movement(sequence[:-1], const)    # the saved versions, not the editor
                if moved:
                    st.markdown("**How far and how often the values moved**")
                    st.dataframe(moved, hide_index=True, width="stretch")
                    st.caption(
                        "Across the saved versions, in the order above (the current design is "
                        "left out, as it may be another design). *Steps* count the moves in units of the "
                        "constitution's `step`" + ("" if const is not None else
                        " (choose a constitution to see them)") + "; use the constitution the run "
                        "started from, as Exercise 12-1 of Course D divides by the original `step`. "
                        "A *reversal* is a move opposite to the one before it — the oscillation "
                        "the exercise counts. How soon the KPI is reached, and the loss on the "
                        "way, come from the simulation (`town.compare`), not from the rules.")


def _control_structure_view(ir: dict, source: str = ""):
    """STAMP control structure inferred from authority, the STPA table to fill in, and what watches each promise."""
    if not graph.actor_ids(ir):
        st.info("This design has no actors yet. Add them under **Forms → Actors**.")
        return
    cs = stamp.control_structure(ir)
    notes = stamp.findings(cs)
    if notes:
        with st.expander(f"What the control structure shows — {len(notes)} point"
                         f"{'s' if len(notes) != 1 else ''}", expanded=len(notes) <= 6):
            for note in notes:
                st.markdown(f"- {note}")
    t_diagram, t_loops, t_uca, t_cover = st.tabs(
        ["Diagram", "Control loops", "Unsafe control actions (STPA)", "Monitoring coverage"])

    with t_diagram:
        st.graphviz_chart(stamp.control_structure_dot(cs, dark=_is_dark()), width="stretch")
        st.caption(
            "A STAMP control structure **inferred** from the CADL, which does not say which "
            "messages are control and which are feedback. A contract's decision holder is "
            "drawn above the parties it decides for. Red arrows down are *control actions* "
            "(its messages to them); dashed blue arrows up are *feedback* (their messages "
            "back, and monitors of the contract that observe them). A dotted red arrow is "
            "authority with nothing exchanged; dotted grey arrows are coordination between "
            "actors that do not control each other. A highlighted (red-tinted) box is "
            "controlled without feedback. When two actors control each other, a message "
            "between them counts as control for the sender and as feedback for the receiver.")

    with t_loops:
        if not cs.loops:
            st.info("No contract names a decision holder, so there are no control loops. "
                    "Set `authority.decision_holder` under **Forms → Contracts**.")
        else:
            st.dataframe([{
                "controller": l.controller, "controlled": l.controlled,
                "contracts": ", ".join(l.contracts), "control actions": ", ".join(l.control) or "—",
                "feedback": ", ".join(l.feedback) or "—", "loop": l.status,
            } for l in cs.loops], hide_index=True, width="stretch")
            st.caption(
                "*closed*: control actions and feedback. *open: no feedback*: the controller "
                "acts without learning the effect — the typical cause of an unsafe control "
                "action in STAMP. *no control action*: informed but unable to act through the "
                "CADL. *authority only*: the contract gives the controller a say but nothing "
                "is exchanged.")

    with t_uca:
        rows = stamp.uca_rows(ir, cs)
        if not rows:
            st.info("There are no control actions to analyse yet.")
        else:
            st.markdown(
                "STPA step 3: for every control action, write when each guide word would make it "
                "unsafe (or *n/a*). **What the CADL already says** below helps with the timing "
                "and the detection.")
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
                                    disabled=["control action"])
            records = edited if isinstance(edited, list) else edited.to_dict("records")
            ss.design_uca_entries = stamp.update_entries(entries, keys, records)
            st.markdown("**What the CADL already says**")
            st.dataframe([{"control action": t["control action"], "contracts": r["contracts"],
                           "deadlines": r["deadlines in the CADL"] or "—",
                           "detected by": r["detected by"] or "—", "feedback": r["feedback"]}
                          for t, r in zip(table, rows)], hide_index=True, width="stretch")
            buffer = io.StringIO()
            writer = csv.DictWriter(buffer, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(stamp.apply_entries(rows, ss.design_uca_entries))
            st.download_button("Download the table (CSV)", buffer.getvalue(),
                               file_name="unsafe_control_actions.csv", mime="text/csv")
            st.caption(
                "The four guide words are those of STPA (Leveson & Thomas, *STPA Handbook*, 2018). "
                "Entries stay with their control action while you switch views or edit the design, "
                "for this browser session only; download them to keep them. "
                "A deadline in the CADL bounds *too late*; a monitor that observes the controlled "
                "actor is a way the effect is *detected*, not a way it is prevented.")

    with t_cover:
        promises = dcon.coverage(ir, source)
        if not promises:
            st.info("No contract states an assumption or a guarantee yet. Add them under "
                    "**Forms → Contracts**.")
        else:
            for note in dcon.coverage_findings(ir, promises):
                st.markdown(f"- {note}")
            st.dataframe([{
                "contract": p.contract, "kind": p.kind, "promise": p.text,
                "watched by": ", ".join(p.watched_by) or "—",
                "measured by": ", ".join(p.measured_by) or "—", "status": p.status,
            } for p in promises], hide_index=True, width="stretch")
            st.caption(
                "What checks each assumption and guarantee while the contract runs: a monitor "
                "of the contract whose `observe` or `rule` names one of its quantities, the "
                "contract's `violation.detect`, or a lifecycle deadline that equals its time "
                "bound (on a transition named in the quantity, such as `pickup` for "
                "`pickup_time`, or through an `# @amend:` name). *measured by*: a metric with "
                "the same name, which evaluates the run afterwards. The CADL does not link a "
                "promise to its monitor, so the links are found by name: a monitor that checks "
                "the same thing under another name is not counted.")


def _structure_view(ir: dict, source: str):
    """Message flow, centrality, N² chart and responsibilities of the design."""
    actors = graph.actor_ids(ir)
    if not actors:
        st.info("This design has no actors yet. Add them under **Forms → Actors**.")
        return
    duties = readback._responsibilities(source)
    gaps = graph.structure_gaps(ir, duties)
    if gaps:
        with st.expander(f"What the structure shows — {len(gaps)} point{'s' if len(gaps) != 1 else ''}",
                         expanded=len(gaps) <= 6):
            for note in gaps:
                st.markdown(f"- {note}")
    t_flow, t_central, t_n2, t_resp, t_deps = st.tabs(
        ["Message flow", "Centrality", "N² chart", "Responsibilities", "Contract dependencies"])

    with t_flow:
        show = st.toggle("Show who decides", key="design_structure_authority",
                         help="Adds an edge from each contract's decision holder to the other parties.")
        if not graph.message_edges(ir):
            st.info(
                "No messages yet: the design has no protocol steps, no lifecycle event "
                "written as `\"A -> B : msg\"` and no information sharing. Add a protocol "
                "under **Forms → Protocols** to see who talks to whom.")
        st.graphviz_chart(graph.message_flow_dot(ir, dark=_is_dark(), show_authority=show),
                          width="stretch")
        st.caption(
            "Each arrow is what one actor sends another, from protocol steps, lifecycle "
            "events (`on: \"A -> B : msg\"`) and information sharing. A dashed arrow "
            "carries a message that a declared interface leaves out (amber); a thick "
            "violet arrow carries *decides* (the sender is the contract's decision "
            "holder). The more strongly an actor is coloured, the higher its betweenness "
            "(see **Centrality**).")

    with t_central:
        rows = [{"actor": r["actor"], "betweenness": r["betweenness"], "partners": r["partners"],
                 "contracts": r["contracts"], "decides": r["decides"],
                 "out / in": f"{r['sends']} / {r['receives']}"} for r in graph.centrality(ir)]
        st.dataframe(rows, hide_index=True, width="stretch", column_config={
            "out / in": st.column_config.TextColumn(
                "out / in", help="Distinct messages it sends / receives."),
            "betweenness": st.column_config.ProgressColumn(
                "betweenness", min_value=0.0, max_value=1.0, format="%.2f",
                help="Share of shortest message paths between two other actors that pass "
                     "through this one. A high value marks a relay: if it fails, others "
                     "cannot reach each other."),
            "partners": st.column_config.NumberColumn(
                "partners", help="Actors it exchanges messages with."),
            "decides": st.column_config.NumberColumn(
                "decides", help="Contracts it is the decision holder of."),
        })
        st.caption(
            "Counts come from the CADL only: messages are those of the **Message flow**, "
            "contracts those the actor is a party of. A group such as `ROBOT[1..N]` is one "
            "row. Centrality says where the structure concentrates; whether that is a risk "
            "depends on what the actor does.")

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
        st.dataframe(table, hide_index=True, width="stretch")
        st.caption(
            "Row: sender. Column: receiver. A cell counts the distinct messages; ⚠ counts "
            "those a declared interface leaves out. Many filled cells in one row or column "
            "show an actor many others depend on; an empty row and column, an actor nobody "
            "talks to.")
        origins = {}
        for e in graph.message_edges(ir):
            origins.setdefault((e.sender, e.receiver, e.message), []).append(e.origin)
        pairs = [(s, r, cell) for s, row in matrix.items() for r, cell in row.items() if cell]
        if pairs:
            with st.expander(f"Messages by pair ({len(pairs)})"):
                for s, r, cell in pairs:
                    items = ", ".join(
                        f"`{e.message}`" + ("" if e.declared else " ⚠") + " ("
                        + "; ".join(dict.fromkeys(origins[(s, r, e.message)])) + ")" for e in cell)
                    st.markdown(f"- **{s} → {r}**: {items}")

    with t_resp:
        contracts = [str(c.get("id")) for c in (ir.get("institution") or {}).get("contracts") or []]
        if not contracts:
            st.info("This design has no contracts yet.")
        else:
            resp = graph.responsibility_table(ir, duties)
            st.dataframe(
                [{"actor ↓": a, **{c: ", ".join(resp[a].get(c) or []) or "—" for c in contracts}}
                 for a in actors],
                hide_index=True, width="stretch")
            st.caption(
                "*decides*: the contract's decision holder. *party*: listed in `parties`. "
                "*duties (n)*: entries under `responsibilities:`. *observed*: a monitor "
                "observes one of its attributes. A contract column without *decides* has "
                "nobody to settle a dispute; an actor row of dashes takes part in nothing.")

    with t_deps:
        if len(dcon._contracts(ir)) < 2:
            st.info("Dependencies are between contracts; this design has fewer than two.")
        else:
            deps = dcon.dependencies(ir)
            for note in dcon.dependency_findings(ir, deps):
                st.markdown(f"- {note}")
            st.graphviz_chart(dcon.dependency_dot(ir, dark=_is_dark()), width="stretch")
            sources = dcon.assumption_sources(ir, deps)
            if sources:
                st.dataframe(sources, hide_index=True, width="stretch",
                             column_config={"guaranteed by": st.column_config.TextColumn(
                                 "guaranteed by", help="Contracts whose guarantee names a quantity "
                                                       "of this assumption.")})
            else:
                st.markdown("- No contract states an assumption (`assume:`), so none relies on "
                            "what another guarantees.")
            st.caption(
                "Each box is a contract with its parties and decision holder. A dashed line joins "
                "two contracts that share actors: a change to one can affect how those actors "
                "meet the other. A blue arrow runs from a contract to one whose assumption it "
                "guarantees (it names the same quantity): the second relies on the first, as in "
                "assume-guarantee reasoning. The links are found by name, not proved.")


@st.cache_data(show_spinner=False, max_entries=64)
def cached_analyze(source: str, _version: int = 4) -> ds.DesignAnalysis:
    return ds.analyze(source)


@st.cache_data(show_spinner=False, max_entries=32)
def cached_codegen(source: str, target: str) -> bytes:
    return ds.export_codegen_zip(source, target)


if "design_src" not in ss:
    ss.design_src = (
        ds.load_example(DEFAULT_EXAMPLE) if DEFAULT_EXAMPLE in ds.list_examples() else ds.NEW_DESIGN)
    ss.design_ver = 0
for key, default in (("design_mode", "Source"), ("design_view", VIEWS[0])):
    if not ss.get(key):
        ss[key] = default

source = ss.design_src
analysis = cached_analyze(source)
flags = ds.flagged(analysis)
# Form widgets are keyed by the source so they re-seed whenever it changes.
src_key = hashlib.sha256(source.encode("utf-8")).hexdigest()[:10]
file_stem = (analysis.name or "design").replace(" ", "_")

# ── Sidebar: files and starting points ──────────────────────────────

with st.sidebar:
    # Shown when the app is started with CADL_WORKSPACE: the folder an AI
    # assistant edits through the MCP server (mcp_server.py).
    if workspace.enabled():
        st.header("Workspace")
        ws_designs = workspace.list_designs()
        if not ws_designs:
            st.caption(f"No designs yet in `{workspace.root()}`.")
        else:
            if ss.get("design_ws_pick") not in ws_designs:
                ss.design_ws_pick = ss.get("design_ws_name") if ss.get("design_ws_name") in ws_designs \
                    else ws_designs[0]
            st.selectbox("Design shared with the assistant", ws_designs, key="design_ws_pick")
            st.button("Open", on_click=_open_workspace, width="stretch",
                      help="Load this design from the workspace into the editor.")
        ws_name = ss.get("design_ws_name")
        if ws_name and workspace.exists(ws_name):
            on_disk = workspace.read(ws_name)
            if on_disk != source:
                st.warning(
                    f"`{ws_name}` in the workspace differs from the editor. The assistant "
                    "may have changed it, or you have unsaved edits.")
                st.button("Reload from workspace", on_click=_open_workspace, width="stretch",
                          help="Replace the editor content with the workspace file. Undo takes it back.")
                st.button("Save to workspace", on_click=_save_workspace, width="stretch",
                          type="primary", help="Write the editor content to the workspace file.")
            else:
                st.caption(f"Editing `{ws_name}`; in sync with the workspace.")

    st.header("File")
    upload = st.file_uploader(
        "Open an existing CADL file", type=["cadl", "yaml", "yml"],
        help="Replaces the design in the editor with the file's content.",
    )
    if upload is not None and ss.get("design_upload_id") != upload.file_id:
        ss.design_upload_id = upload.file_id
        if upload.size > MAX_SOURCE_CHARS * 4:
            st.error(f"The file is too large (limit {MAX_SOURCE_CHARS:,} characters).")
        else:
            try:
                text = upload.read().decode("utf-8-sig")
            except UnicodeDecodeError:
                st.error("The file is not UTF-8 text.")
            else:
                if len(text) > MAX_SOURCE_CHARS:
                    st.error(
                        f"The file has {len(text):,} characters; the limit is "
                        f"{MAX_SOURCE_CHARS:,}."
                    )
                else:
                    _set_source(text)
                    ss.design_file_name = upload.name.rsplit(".", 1)[0]
                    st.rerun()

    if not ss.get("design_file_name"):
        ss.design_file_name = file_stem
    save_name = st.text_input("File name", key="design_file_name").strip() or file_stem
    st.download_button(
        "Save as .cadl", source, file_name=f"{save_name}.cadl",
        mime="text/yaml", width="stretch", type="primary",
        help="Downloads the design exactly as it is in the editor.",
    )
    st.caption("The design lives in this browser session only. Save it to keep it.")

    st.header("Start from")
    examples = ds.list_examples()
    if examples:
        st.selectbox("Example design", list(examples), key="design_example")
        st.button("Load example", on_click=_load_example, width="stretch",
                  help="Replaces the design in the editor.")
    st.button("New design", on_click=_new_design, width="stretch",
              help="Replaces the design in the editor with a small starting template.")

    with st.expander("Draft from a description"):
        unavailable = ds.ai_available()
        if unavailable:
            st.caption(
                "Writes a first CADL draft from a plain-language description, using "
                f"the upstream `cadl ai` generator. Not enabled here: {unavailable}. "
                "To use it, run the app locally with `pip install anthropic` and "
                "`ANTHROPIC_API_KEY` set."
            )
        else:
            st.caption(
                "Describe the system in plain language (English or Japanese). The "
                "description is sent to the Anthropic API; the draft replaces the "
                "design in the editor and is checked like any other."
            )
            description = st.text_area(
                "Description", key="design_ai_text", height=140,
                max_chars=ds.MAX_DESCRIPTION_CHARS, label_visibility="collapsed",
                placeholder="A warehouse where a dispatcher assigns delivery tasks to robots…",
            )
            if st.button("Write a draft", width="stretch", disabled=not description.strip()):
                try:
                    with st.spinner("Writing a draft…"):
                        draft, draft_errors = ds.draft_from_description(description)
                except Exception as e:
                    st.error(f"Could not write a draft: {e}")
                else:
                    _set_source(draft)
                    ss.design_file_name = ""
                    if draft_errors:
                        st.toast("The draft has problems; see the checks.")
                    st.rerun()

# ── Header ──────────────────────────────────────────────────────────

st.title("Designer")
st.markdown(
    "Design a System of Systems and its contract architecture in **CADL**: "
    "actors, contracts with their lifecycles and monitors, protocols, "
    "algorithms, regimes and metrics. Every change is checked and drawn."
)
st.caption(
    "**1** Open a `.cadl` file or start from an example (sidebar) · "
    "**2** edit the *Source* or use the *Forms* — both change the same design · "
    "**3** read the checks and diagrams on the right · "
    "**4** save with **Save as .cadl**. "
    f"[CADL specification and hands-on]({SPEC_URL})"
)

col_edit, col_view = st.columns(2, gap="large")

# ── Left: source and forms ──────────────────────────────────────────

with col_edit:
    # A horizontal container keeps the buttons at their natural width and
    # wraps them on a narrow screen instead of squeezing their labels.
    with st.container(horizontal=True, vertical_alignment="center"):
        mode = st.segmented_control(
            "Edit as", ["Source", "Forms"], key="design_mode", label_visibility="collapsed",
        ) or "Source"
        st.button(
            "Undo", on_click=_undo, disabled=not ss.get("design_history"),
            help="Take back the last change to the design.",
        )
        st.button("Redo", on_click=_redo, disabled=not ss.get("design_redo"))

    if mode == "Source":
        if st_ace is not None:
            edited = st_ace(
                value=source, language="yaml", key=f"design_ace_{ss.design_ver}",
                theme="tomorrow_night" if _is_dark() else "chrome",
                height=640, font_size=13, tab_size=2, wrap=False, show_gutter=True,
                auto_update=False, annotations=analysis.annotations or None,
            )
            if edited is not None and edited != source:
                if len(edited) > MAX_SOURCE_CHARS:
                    st.error(f"The design is limited to {MAX_SOURCE_CHARS:,} characters.")
                else:
                    _record()
                    ss.design_src = edited
                    st.rerun()
            st.caption("Press **Apply** or Ctrl/⌘ + Enter to check your changes.")
        else:
            st.markdown(
                "<style>textarea{font-family:ui-monospace,SFMono-Regular,Menlo,monospace !important;"
                "font-size:13px !important;}</style>",
                unsafe_allow_html=True,
            )
            st.text_area(
                "CADL source", value=source, height=640, max_chars=MAX_SOURCE_CHARS,
                key=f"design_editor_{ss.design_ver}", on_change=_on_edit,
                label_visibility="collapsed",
            )
            st.caption("Changes are checked when you click outside the editor or press Ctrl/⌘ + Enter.")
    else:
        try:
            doc = ds.load_doc(source)
        except ValueError as e:
            doc = None
            st.warning(f"Forms need a readable design. Fix the source first: {e}")

        if doc is not None:
            if ds.has_amend_tags(source):
                st.warning(
                    "This design marks amendable values with `# @amend:` comments "
                    "(Course D). Applying a form rewrites the source without comments, "
                    "so the council could no longer find them. To change a marked value, "
                    "use **Amendable parameters** in the **Versions** view, which keeps the "
                    "comments; for other edits, edit the **Source** or use **Undo** after applying."
                )
            elif ds.has_comments(source):
                st.caption(
                    ":material/warning: Applying a form rewrites the source and "
                    "drops its `#` comments. **Undo** brings them back."
                )

            def _apply(message: str):
                _set_source(ds.dump_doc(doc))
                st.toast(message)
                st.rerun()

            render_forms(doc, src_key, _apply, flags, analysis.findings)

# ── Right: checks and views ─────────────────────────────────────────

with col_view:
    # One line of badges: a stage is red / orange / green / grey.
    badges = []
    for stage in ds.STAGES:
        errors, warnings = analysis.count("error", stage), analysis.count("warning", stage)
        ran = any(f.stage == stage for f in analysis.findings)
        if errors:
            badges.append(f":red-badge[{LEVEL_ICON['error']} {stage} · {errors}]")
        elif warnings:
            badges.append(f":orange-badge[{LEVEL_ICON['warning']} {stage} · {warnings}]")
        elif ran:
            badges.append(f":green-badge[{LEVEL_ICON['pass']} {stage}]")
        else:
            badges.append(f":gray-badge[:material/remove: {stage}]")
    st.markdown(" ".join(badges))

    ws_name = ss.get("design_ws_name") if workspace.enabled() else None
    pending_review = workspace.unreviewed(ws_name) if ws_name and workspace.exists(ws_name) else []
    if pending_review:
        with st.expander(
                f"Changed by the assistant, not yet reviewed — {len(pending_review)}", expanded=True):
            st.caption(
                "The checks say these changes are consistent, not that they are what you "
                "meant. Look at each one, then confirm it.")
            for i, change in enumerate(pending_review):
                st.markdown(_md(
                    f":material/smart_toy: **{change['summary']}**"
                    + (f"  \n*Why:* {change['rationale']}" if change.get("rationale") else "")))
                with st.container(horizontal=True):
                    st.button("Reviewed", key=f"review_{i}", on_click=_mark_reviewed, args=(i,))
                    if change.get("section") in SECTIONS:
                        st.button("Open form", key=f"review_form_{i}", on_click=_jump,
                                  args=(change["section"], change.get("item", "")))
            st.button("Mark all reviewed", on_click=_mark_reviewed, key="review_all")

    problems = [f for f in analysis.findings if f.level in ("error", "warning")]
    n_errors = sum(f.level == "error" for f in problems)
    if not problems:
        st.caption(
            "No problems found. **Checks** lists what was checked — and what is not."
        )
    else:
        n_warnings = len(problems) - n_errors
        label = ", ".join(
            f"{n} {word}{'s' if n != 1 else ''}"
            for n, word in ((n_errors, "error"), (n_warnings, "warning")) if n)
        with st.expander(f"Problems — {label}", expanded=bool(n_errors) or len(problems) <= 3):
            for i, f in enumerate(problems[:8]):
                st.markdown(
                    f"{LEVEL_ICON[f.level]} **{f.title}**" + (f"  \n{f.message}" if f.message else ""))
                _problem_buttons(f, f"jump_{i}")
            if len(problems) > 8:
                st.caption(f"{len(problems) - 8} more under **Checks**.")

    # A horizontal radio wraps onto a second line when the column is narrow.
    view = st.radio(
        "View", VIEWS, key="design_view", horizontal=True, label_visibility="collapsed")

    ir = analysis.ir
    ir_contracts = (ir or {}).get("institution", {}).get("contracts") or []
    needs_ir = view not in ("Checks", "Versions")
    if needs_ir and ir is None:
        st.info("This view appears once the design parses. Fix the problem listed above.")

    elif view == "Architecture":
        n_actors = len(ir["institution"].get("actors") or [])
        st.markdown(
            f"**{analysis.name}** · {analysis.sos_type} · "
            f"{n_actors} actor{'s' if n_actors != 1 else ''}, "
            f"{len(ir_contracts)} contract{'s' if len(ir_contracts) != 1 else ''}"
        )
        st.graphviz_chart(architecture_to_dot(ir, dark=_is_dark(), flags=flags), width="stretch")
        st.caption(
            "Blue boxes are actors (darker = more autonomous); rounded boxes are "
            "contracts with their α / β / λ. A bold *decides* edge marks the "
            "contract's decision holder. A red or orange outline marks an item "
            "with an error or a warning."
        )

    elif view == "Structure":
        _structure_view(ir, source)

    elif view == "Control structure":
        _control_structure_view(ir, source)

    elif view == "Lifecycle":
        if not ir_contracts:
            st.info("This design has no contracts yet. Add one under **Forms → Contracts**.")
        else:
            cids = [str(c.get("id")) for c in ir_contracts]
            if ss.get("design_life_contract") not in cids:
                ss.pop("design_life_contract", None)
            pick = st.radio("Contract", cids, horizontal=True, key="design_life_contract")
            ir_contract = next(c for c in ir_contracts if str(c.get("id")) == pick)
            lc_view = build_lifecycle_view(ir_contract)
            if lc_view is None:
                st.info(
                    f"`{pick}` has no `lifecycle:` section. Add states and transitions "
                    "under **Forms → Contracts**, or apply a lifecycle preset there."
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
                st.graphviz_chart(
                    lifecycle_to_dot(
                        lc_view, dark=_is_dark(),
                        current=path[-1] if path else None, visited=path, flagged=bad_states),
                    width="stretch",
                )
                st.caption(
                    f"{len(lc_view.states)} states, {len(lc_view.transitions)} transitions. "
                    "Double circle = initial, dashed box = terminal, dashed red edge = "
                    "transition taken when a deadline is missed."
                    + (" Red outline = state with a problem." if bad_states else "")
                )

                with st.container(border=True):
                    st.markdown("**Step through one contract instance**")
                    if not tracing:
                        st.caption(
                            "Follow a single instance of this contract from its initial "
                            "state, choosing what happens at each step."
                        )
                        st.button(
                            "Start at the initial state", on_click=_trace_reset,
                            args=(pick, lc_view.initial), disabled=not lc_view.initial,
                        )
                    else:
                        current = path[-1]
                        st.markdown("Path: " + " → ".join(
                            f"**{s}**" if i == len(path) - 1 else s for i, s in enumerate(path)))
                        moves = ds.lifecycle_moves(ir_contract, current)
                        if current in lc_view.terminal:
                            st.success(f"`{current}` is terminal: this instance has ended.")
                        elif not moves:
                            st.warning(f"Nothing can happen in `{current}`: the instance is stuck.")
                        else:
                            st.caption(f"In `{current}`, choose what happens next:")
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
                        c_back.button("Back", on_click=_trace_back, disabled=len(path) < 2,
                                      width="stretch", key="trace_back")
                        c_reset.button("Restart", on_click=_trace_reset,
                                       args=(pick, lc_view.initial), width="stretch",
                                       key="trace_restart")

            if lc_view is not None:
                with st.expander("Every way an instance can run, as stories"):
                    try:
                        stories = readback.lifecycle_stories(source, pick)
                    except ValueError as e:
                        stories = []
                        st.caption(f"Cannot tell the stories: {e}")
                    st.caption(
                        "Each story follows one instance from start to end, visiting a "
                        "state at most once. Read them and ask: should this be possible?")
                    for story in stories:
                        st.markdown(_md(f"**{story['outcome'].capitalize()}** — {story['story']}"))

            monitors = monitors_summary(ir_contract)
            st.markdown(f"**Monitors** ({len(monitors)})")
            if monitors:
                st.dataframe(monitors, width="stretch", hide_index=True)
            else:
                st.caption("No `monitors:` declared on this contract.")

    elif view == "Protocols":
        ir_protocols = (ir.get("protocol") or {}).get("protocols") or []
        if not ir_protocols:
            st.info("This design has no protocols yet. Add one under **Forms → Protocols**.")
        else:
            pids = [str(p.get("id")) for p in ir_protocols]
            if ss.get("design_view_protocol") not in pids:
                ss.pop("design_view_protocol", None)
            pick = st.radio("Protocol", pids, horizontal=True, key="design_view_protocol")
            proto = next(p for p in ir_protocols if str(p.get("id")) == pick)
            st.markdown(f"**Trigger** `{proto.get('trigger') or '—'}`")
            bad_messages = {
                f.element for f in analysis.findings
                if f.section == "Protocols" and f.item == pick and f.element
                and f.level in ("error", "warning")
            }
            st.markdown(sequence_svg(proto, dark=_is_dark(), flagged=bad_messages),
                        unsafe_allow_html=True)
            n_steps = len(proto.get("steps") or [])
            st.caption(
                f"{n_steps} step{'s' if n_steps != 1 else ''} between "
                f"{len(participants(proto))} actors, top to bottom. Arrows are messages, "
                "rounded boxes are local computations, grey bands mark `if`, "
                "`parallel` and `barrier`."
                + (" Red arrows are messages with a problem." if bad_messages else "")
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
                st.dataframe(facts, width="stretch", hide_index=True)

    elif view == "Regimes":
        if not ir.get("transitions"):
            st.info(
                "This design has no regime transitions. Add them under "
                "**Forms → Regimes** to describe how the SoS moves between "
                "operating regimes such as NORMAL and EMERGENCY."
            )
        else:
            regime_findings = [
                f for f in analysis.findings
                if f.section == "Regimes" and f.level in ("error", "warning")
            ]
            st.graphviz_chart(
                regimes_to_dot(ir, dark=_is_dark(),
                               flagged={f.element for f in regime_findings if f.element}),
                width="stretch",
            )
            st.caption(
                "Operating regimes of the whole SoS. Each edge shows the condition "
                "for the transition; orange edges name the protocol that carries it out."
                + (" Red marks a regime or transition with a problem." if regime_findings else "")
            )
            for f in regime_findings:
                st.markdown(f"{LEVEL_ICON[f.level]} {f.title}" + (f" — {f.message}" if f.message else ""))

    elif view == "Algorithms & metrics":
        algorithms = (ir.get("algorithm") or {}).get("algorithms") or []
        st.markdown(f"**Algorithms** ({len(algorithms)})")
        if algorithms:
            st.dataframe(
                [{"function": a.get("name"), "central": a.get("central") or "—",
                  "local": a.get("local") or "—"} for a in algorithms],
                width="stretch", hide_index=True,
            )
            st.caption("`central` runs at the authority, `local` at each actor.")
        else:
            st.caption("None declared. Add them under **Forms → Algorithms**.")

        metrics = ir.get("metrics") or []
        st.markdown(f"**Metrics** ({len(metrics)})")
        if metrics:
            metric_notes = {}
            for f in analysis.findings:
                if f.section == "Metrics" and f.item and f.level in ("error", "warning", "info"):
                    metric_notes.setdefault(f.item, []).append(f.title)
            st.dataframe(
                [{"id": m.get("id"), "formula": m.get("formula") or "—",
                  "target": m.get("target") or "—",
                  "notes": "; ".join(metric_notes.get(str(m.get("id")), []))} for m in metrics],
                width="stretch", hide_index=True,
            )
        else:
            st.caption("None declared. Add them under **Forms → Metrics**.")

        environment = ir.get("environment") or {}
        if environment:
            st.markdown(f"**Environment** ({len(environment)})")
            st.dataframe(
                [{"parameter": k, "value": str(v)} for k, v in environment.items()],
                width="stretch", hide_index=True,
            )

    elif view == "Read-back":
        st.caption(
            "The design restated in plain sentences by fixed rules — it says what the "
            "design says. Use it to confirm that this is what you meant; the checks "
            "cannot tell you that."
        )
        described = readback.describe_design(source)
        st.markdown("**Contracts**")
        if not described:
            st.caption("No contracts yet.")
        for cid, lines in described.items():
            with st.expander(cid, expanded=len(described) <= 2):
                for line in lines:
                    st.markdown(_md(f"- {line}"))

        actor_ids = [str(a.get("id")) for a in ir["institution"].get("actors") or []]
        if actor_ids:
            st.markdown("**One actor's point of view**")
            if ss.get("design_readback_actor") not in actor_ids:
                ss.pop("design_readback_actor", None)
            who = st.radio("Actor", actor_ids, horizontal=True, key="design_readback_actor",
                           label_visibility="collapsed")
            seen = readback.actor_view(source, who)
            st.markdown(_md(seen["summary"]))
            for heading, key, empty in (
                    ("Bound by", "contracts", "Not bound by any contract."),
                    ("Takes part in", "protocols", "Takes part in no protocol."),
                    ("Open issues", "notes", None)):
                if seen[key]:
                    st.markdown(f"*{heading}*")
                    for line in seen[key]:
                        st.markdown(_md(f"- {line}"))
                elif empty:
                    st.caption(empty)

        explained = readback.explain_verification(source)
        if explained:
            st.markdown("**What verification concluded**")
            for row in explained:
                st.markdown(_md(f"{LEVEL_ICON[row['result']]} **{row['check']}** — {row['meaning']}"))

    elif view == "Checks":
        st.caption(
            "Each check, what it covers and what it found. A green result means "
            "only what the description says."
        )
        jump_index = 0
        for stage in ds.STAGES:
            stage_findings = [f for f in analysis.findings if f.stage == stage]
            errors = sum(f.level == "error" for f in stage_findings)
            warnings = sum(f.level == "warning" for f in stage_findings)
            state = (f"{errors} error{'s' if errors != 1 else ''}" if errors else
                     f"{warnings} warning{'s' if warnings != 1 else ''}" if warnings else
                     "passed" if stage_findings else "not run")
            with st.expander(f"{stage} — {state}", expanded=bool(errors or warnings)):
                st.caption(ds.STAGE_SCOPE[stage])
                if not stage_findings:
                    st.caption(
                        "Not run: nothing in the design for it to look at, or an "
                        "earlier check failed."
                    )
                for f in stage_findings:
                    st.markdown(
                        f"{LEVEL_ICON[f.level]} {f.title}" + (f" — {f.message}" if f.message else ""))
                    if f.level in ("error", "warning"):
                        jump_index += 1
                        _problem_buttons(f, f"check_jump_{jump_index}")
        with st.expander("Not checked"):
            for item in ds.NOT_CHECKED:
                st.markdown(f"- {item}")

    elif view == "Versions":
        st.caption(
            "Keep named versions of the design while you work, compare any two, "
            "and go back to one. Versions last for this browser session."
        )
        v_name, v_save = st.columns([3, 2], vertical_alignment="bottom")
        v_name.text_input("Name for the current design", key="design_version_name",
                          placeholder="e.g. before adding safety contract")
        v_save.button("Save as a version", on_click=_save_version, width="stretch",
                      disabled=not analysis.parsed, type="primary")

        with st.expander("Add CADL files as versions"):
            st.caption(
                "Choose several files at once — for example `runs/<name>/v1.cadl`, `v2.cadl`, ... "
                "of a Course D run. They are added in the order of their names (v2 before v10), "
                f"named after the files; at most {MAX_VERSIONS} versions are kept.")
            st.text_input("Prefix for their names (optional)", key="design_version_prefix",
                          placeholder="e.g. step30, to tell the runs of different conditions apart")
            picked = st.file_uploader(
                "CADL files", type=["cadl", "yaml", "yml"], accept_multiple_files=True,
                key=f"design_version_files_{ss.get('design_version_files_key', 0)}")
            count = len(picked or [])
            st.button(f"Add {count} file as a version" if count == 1 else f"Add {count} files as versions",
                      on_click=_add_files_as_versions, disabled=not picked)
        report = ss.pop("design_files_added", None)
        if report:
            if report["added"]:
                st.success("Added " + ", ".join(f"**{n}**" for n in report["added"]) + ".")
            if report["dropped"]:
                st.warning(f"Only the last {MAX_VERSIONS} versions are kept; "
                           + ", ".join(f"**{n}**" for n in report["dropped"]) + " no longer fit and "
                           "were removed.")
            for item in report["skipped"]:
                st.error(f"Not added: {item}.")

        versions = ss.get("design_versions", [])
        for i, version in enumerate(versions):
            same = version["source"] == source
            r_name, r_restore, r_delete = st.columns([4, 1, 1], vertical_alignment="center")
            r_name.markdown(
                f"**{version['name']}** · saved {version['time']}"
                + (" · same as the current design" if same else ""))
            r_restore.button("Restore", key=f"version_restore_{i}", width="stretch",
                             on_click=_restore_version, args=(i,), disabled=same,
                             help="Make this version the current design. Undo takes it back.")
            r_delete.button("Delete", key=f"version_delete_{i}", width="stretch",
                            on_click=_delete_version, args=(i,))

        st.divider()
        if amend.amendables(source):
            _amendments_section(source, versions)
            st.divider()
        elif any(amend.amendables(v["source"]) for v in versions):
            # a run added as versions while another design is open
            _history_section(source, versions, _constitution())
            st.divider()
        st.markdown("**Compare two designs**")
        sources = {"Current design": source}
        sources.update({f"Version: {v['name']}": v["source"] for v in versions})
        sources.update({f"Example: {name}": None for name in ds.list_examples()})
        names = list(sources)
        for key, default in (("design_cmp_a", names[1] if len(names) > 1 else names[0]),
                             ("design_cmp_b", names[0])):
            if ss.get(key) not in names:
                ss[key] = default
        c_a, c_b = st.columns(2)
        name_a = c_a.selectbox("A — baseline", names, key="design_cmp_a")
        name_b = c_b.selectbox("B — compared", names, key="design_cmp_b")

        def _source_of(name: str) -> str:
            return sources[name] if sources[name] is not None else ds.load_example(
                name.split(": ", 1)[1])

        source_a, source_b = _source_of(name_a), _source_of(name_b)
        base, other = cached_analyze(source_a), cached_analyze(source_b)
        if name_a == name_b:
            st.info("Choose two different designs to compare.")
        elif not (base.parsed and other.parsed):
            st.info("Both designs must parse to be compared.")
        else:
            changes = ds.diff_designs(base.ir, other.ir)
            total = sum(len(v) for v in changes.values())
            old_values, new_values = amend.values(source_a), amend.values(source_b)
            const = _constitution()

            def _in_steps(k: str) -> str:
                a, b = old_values.get(k), new_values.get(k)
                n = None if a is None or b is None else amend.steps(k, b - a, const)
                if n is None:
                    return ""
                n = round(n, 3)
                return f" ({amend.fmt(n)} step{'' if n == 1 else 's'})"

            moved = [f"`{k}` {amend.fmt(old_values.get(k))} → {amend.fmt(new_values.get(k))}{_in_steps(k)}"
                     for k in dict.fromkeys(list(old_values) + list(new_values))
                     if old_values.get(k) != new_values.get(k)]
            if moved:
                st.markdown("Amended parameters (`# @amend:`): " + ", ".join(moved))
            st.markdown(
                f"**A** `{base.name}` → **B** `{other.name}` — "
                + (f"{total} structural change{'s' if total != 1 else ''}" if total
                   else "no structural change")
            )
            for topic, items in changes.items():
                if items:
                    with st.expander(f"{topic} ({len(items)})", expanded=len(items) <= 8):
                        for item in items:
                            st.markdown(f"- {item}")
            raw = ds.source_diff(source_a, source_b, name_a, name_b)
            if raw:
                with st.expander(f"Source diff ({len(raw.splitlines())} lines)"):
                    st.code(raw, language="diff")

            st.markdown("**Run both in the Explorer**")
            st.caption(
                "The Explorer's synthetic model is much smaller than CADL: it reads "
                "only the SoS type (Directed vs. the others), the motivation profile "
                "and ρ. Structural changes do not change its metrics."
            )
            if st.button("Open A and B in the Explorer"):
                try:
                    ss.custom_cadl_a = ds.to_explorer_yaml(source_a)
                    ss.custom_cadl = ds.to_explorer_yaml(source_b)
                except Exception as e:
                    st.error(f"Could not convert the designs: {e}")
                else:
                    st.switch_page("views/explorer.py")

    elif view == "Export":
        if not analysis.ok:
            st.warning("The design has errors; generated files may be incomplete.")
        st.markdown("**Design**")
        e1, e2, e3 = st.columns(3)
        e1.download_button(
            "CADL source (.cadl)", source, file_name=f"{file_stem}.cadl",
            mime="text/yaml", width="stretch", key="export_cadl")
        e2.download_button(
            "Simulator IR (JSON)", ds.export_ir_json(analysis),
            file_name=f"{file_stem}.ir.json", mime="application/json", width="stretch",
            help="The same file `cadl sim-ir --format json` produces; the Contract Lifecycle page reads it.")
        e3.download_button(
            "Simulator IR (YAML)", ds.export_ir_yaml(analysis),
            file_name=f"{file_stem}.ir.yaml", mime="text/yaml", width="stretch")

        st.markdown("**Design report**")
        r1, r2 = st.columns([2, 3], vertical_alignment="center")
        r1.download_button(
            "Design report (HTML)",
            ds.build_report(source, analysis, datetime.now().strftime("%Y-%m-%d %H:%M")),
            file_name=f"{file_stem}.report.html", mime="text/html", width="stretch",
            type="primary")
        r2.caption(
            "One page with the checks, diagrams, contracts, protocols and source. "
            "Open it in a browser and print to get a PDF. The graph diagrams are "
            "drawn when the page opens and need a network connection."
        )

        st.markdown("**Simulator config**")
        s1, s2 = st.columns([2, 3], vertical_alignment="bottom")
        sim_target = s1.selectbox("Target", list(ds.SIM_TARGETS), format_func=ds.SIM_TARGETS.get,
                                  key="design_sim_target")
        try:
            sim_config = ds.export_sim_config(source, sim_target)
        except Exception as e:
            s2.error(f"Cannot generate: {e}")
        else:
            s2.download_button(
                f"Download {ds.SIM_TARGETS[sim_target]} simulator config", sim_config,
                file_name=f"{file_stem}.sim.{sim_target}.txt", width="stretch")

        st.markdown("**Runtime code**")
        c1, c2 = st.columns([2, 3], vertical_alignment="bottom")
        code_target = c1.selectbox("Target", list(ds.CODEGEN_TARGETS),
                                   format_func=ds.CODEGEN_TARGETS.get, key="design_code_target")
        try:
            code_zip = cached_codegen(source, code_target)
        except Exception as e:
            c2.error(f"Cannot generate: {e}")
        else:
            c2.download_button(
                f"Download {ds.CODEGEN_TARGETS[code_target]} (.zip)", code_zip,
                file_name=f"{file_stem}.{code_target}.zip", mime="application/zip",
                width="stretch")

        st.markdown("**Analyses**")
        with st.expander("Regime map"):
            try:
                regimes = ds.regime_map(source)
            except Exception as e:
                regimes = None
                st.error(f"Cannot build the regime map: {e}")
            if regimes is None:
                st.caption("This design has no top-level `transitions:` between governance regimes.")
            else:
                st.code(regimes["text"], language="text")
                st.download_button(
                    "Regime map (DOT)", regimes["dot"], file_name=f"{file_stem}.regimes.dot")
        with st.expander("IEC 62853 dependability summary"):
            try:
                report = ds.iec62853_report(source)
            except Exception as e:
                st.error(f"Cannot build the summary: {e}")
            else:
                if report.get("disclaimer"):
                    st.caption(report["disclaimer"])
                st.json(report, expanded=2)
                st.download_button(
                    "Summary (JSON)", json.dumps(report, indent=2, ensure_ascii=False),
                    file_name=f"{file_stem}.iec62853.json", mime="application/json")
