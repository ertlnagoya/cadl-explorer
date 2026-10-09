"""Designer page — author a full CADL design and check it as you go.

Left: the CADL source and forms that edit the same document.
Right: checks and one view at a time (diagrams, tables, versions, export).
The source text in st.session_state.design_src is the single source of
truth; forms read it and write it back.
"""

import hashlib
import json
from datetime import datetime

import streamlit as st

from backend.plotting.architecture import architecture_to_dot, regimes_to_dot
from backend.plotting.sequence import participants, sequence_svg
from backend.services import design_service as ds
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
    "Architecture", "Lifecycle", "Protocols", "Regimes",
    "Algorithms & metrics", "Checks", "Versions", "Export",
]
MAX_HISTORY = 50
MAX_VERSIONS = 20

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


def _save_version():
    versions = ss.setdefault("design_versions", [])
    name = (ss.get("design_version_name") or "").strip() or f"Version {len(versions) + 1}"
    versions.append({
        "name": name, "time": datetime.now().strftime("%H:%M:%S"), "source": ss.design_src,
    })
    del versions[:-MAX_VERSIONS]
    ss.design_version_name = ""


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


def _is_dark() -> bool:
    theme = getattr(st.context, "theme", None)
    return getattr(theme, "type", None) == "dark"


@st.cache_data(show_spinner=False, max_entries=64)
def cached_analyze(source: str, _version: int = 3) -> ds.DesignAnalysis:
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
            if ds.has_comments(source):
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
