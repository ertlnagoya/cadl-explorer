"""Versions, amendments and comparison of designs."""

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
from views._design.common import (_add_files_as_versions, _amend_apply, _constitution, _delete_version,
    _drop_constitution, _restore_version, _save_version, _use_bundled_constitution)
from views._design.cache import *  # noqa: F401,F403
from backend.plotting.architecture import diff_ir
from views._design.common import _is_dark
from views._i18n import t


def _amendments_section(source: str, versions: list):
    """The `# @amend:` values, checked against a constitution, amended in place, and their history."""
    params = amend.amendables(source)
    if not params:
        return
    st.markdown(t("**Amendable parameters**"))
    st.caption(
        t("Values marked `# @amend:<name>` (Robot Town, Course D). A constitution "
        "(`constitution.yaml`) gives each name its layer, range and step. Amending here "
        "changes the number on every marked line and keeps the comments; the forms and the "
        "source editor's other edits would drop them."))
    up, use = st.columns([3, 2], vertical_alignment="bottom")
    uploaded = up.file_uploader(t("Constitution"), type=["yaml", "yml"],
                                key=f"design_constitution_file_{ss.get('design_constitution_key', 0)}",
                                help=t("The constitution.yaml of the Course D repository, or your own."))
    if uploaded is not None and ss.get("design_constitution_upload") != uploaded.file_id:
        ss.design_constitution_upload = uploaded.file_id     # read each upload once
        ss.design_constitution = uploaded.getvalue().decode("utf-8", errors="replace")
        ss.design_constitution_name = uploaded.name
    if ss.get("design_constitution"):
        use.button(f"Stop using {ss.get('design_constitution_name', 'the constitution')}",
                   on_click=_drop_constitution, width="stretch")
    elif BUNDLED_CONSTITUTION.exists():
        use.button(t("Use the Robot Town constitution"), on_click=_use_bundled_constitution,
                   width="stretch", help=t("The constitution.yaml bundled with the robot town example."))
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
        name = c_name.selectbox(t("Amend"), names, key="design_amend_name")
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
                st.caption(t("No constitution: only the consistency of the marked lines is checked."))
            for reason in gate.reasons:
                st.markdown(f"- ⚠ {reason}")
            if const is not None and gate.ok:
                st.markdown(t("- The constitution allows this amendment."))
            reason = ""
            if gate.for_a_person:
                st.markdown(f"- The constitution leaves `{name}` ({amend.layer_of(spec)}) to a "
                            "person. Write the reason for your decision; it is kept in the "
                            "decision record below.")
                reason = st.text_input("Reason for the decision", key=f"design_amend_reason_{name}",
                                       placeholder=t("why this change, and what you take for granted"))
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
            st.download_button(t("Download the record (Markdown)"), "# Decisions\n\n" + text + "\n",
                               file_name="decisions.md", mime="text/markdown")
            st.caption(t("The amendments made here in this browser session, with the reasons given. "
                       "Download the record to keep it."))

    _history_section(source, versions, const)


def _history_section(source: str, versions: list, const: dict | None):
    """How the `# @amend:` values changed across the saved versions (Course D, Exercise 12-1)."""
    if versions:
        sequence = [(v["name"], v["source"]) for v in versions] + [("current", source)]
        table = amend.history(sequence)
        if table:
            with st.expander(t("History of the amendable values across the versions")):
                st.dataframe(table, hide_index=True, width="stretch")
                st.caption(t("One column per saved version, then the current design; ← marks a "
                           "value that differs from the column before."))
                moved = amend.movement(sequence[:-1], const)    # the saved versions, not the editor
                if moved:
                    st.markdown(t("**How far and how often the values moved**"))
                    st.dataframe(moved, hide_index=True, width="stretch")
                    st.caption(
                        "Across the saved versions, in the order above (the current design is "
                        "left out, as it may be another design). *Steps* count the moves in units of the "
                        "constitution's `step`" + ("" if const is not None else
                        " (choose a constitution to see them)") + ". Exercise 12-1 of Course D divides "
                        "by the original `step`: choose the constitution before its step was changed "
                        "(the `constitution.yaml` restored with `git checkout`, or the Robot Town "
                        "constitution); one with the changed step counts in that step. "
                        "A *reversal* is a move opposite to the one before it — the oscillation "
                        "the exercise counts. How soon the KPI is reached, and the loss on the "
                        "way, come from the simulation (`town.compare`), not from the rules.")



def versions(c):
    source, analysis = c.source, c.analysis
    st.caption(
        t("Keep named versions of the design while you work, compare any two, "
        "and go back to one. Versions last for this browser session.")
    )
    v_name, v_save = st.columns([3, 2], vertical_alignment="bottom")
    v_name.text_input("Name for the current design", key="design_version_name",
                      placeholder=t("e.g. before adding safety contract"))
    v_save.button(t("Save as a version"), on_click=_save_version, width="stretch",
                  disabled=not analysis.parsed, type="primary")

    with st.expander(t("Add CADL files as versions")):
        st.caption(
            "Choose several files at once — for example `runs/<name>/v1.cadl`, `v2.cadl`, ... "
            "of a Course D run. They are added in the order of their names (v2 before v10), "
            f"named after the files; at most {MAX_VERSIONS} versions are kept.")
        st.text_input("Prefix for their names (optional)", key="design_version_prefix",
                      placeholder=t("e.g. step30, to tell the runs of different conditions apart"))
        picked = st.file_uploader(
            t("CADL files"), type=["cadl", "yaml", "yml"], accept_multiple_files=True,
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
        r_restore.button(t("Restore"), key=f"version_restore_{i}", width="stretch",
                         on_click=_restore_version, args=(i,), disabled=same,
                         help=t("Make this version the current design. Undo takes it back."))
        r_delete.button(t("Delete"), key=f"version_delete_{i}", width="stretch",
                        on_click=_delete_version, args=(i,))

    st.divider()
    if amend.amendables(source):
        _amendments_section(source, versions)
        st.divider()
    elif any(amend.amendables(v["source"]) for v in versions):
        # a run added as versions while another design is open
        _history_section(source, versions, _constitution())
        st.divider()
    st.markdown(t("**Compare two designs**"))
    sources = {"Current design": source}
    sources.update({f"Version: {v['name']}": v["source"] for v in versions})
    sources.update({f"Example: {name}": None for name in ds.list_examples()})
    names = list(sources)
    for key, default in (("design_cmp_a", names[1] if len(names) > 1 else names[0]),
                         ("design_cmp_b", names[0])):
        if ss.get(key) not in names:
            ss[key] = default
    c_a, c_b = st.columns(2)
    name_a = c_a.selectbox(t("A — baseline"), names, key="design_cmp_a")
    name_b = c_b.selectbox(t("B — compared"), names, key="design_cmp_b")

    def _source_of(name: str) -> str:
        return sources[name] if sources[name] is not None else ds.load_example(
            name.split(": ", 1)[1])

    source_a, source_b = _source_of(name_a), _source_of(name_b)
    base, other = cached_analyze(source_a), cached_analyze(source_b)
    if name_a == name_b:
        st.info(t("Choose two different designs to compare."))
    elif not (base.parsed and other.parsed):
        st.info(t("Both designs must parse to be compared."))
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
        union, marks = diff_ir(base.ir, other.ir)
        if marks["Diff"]:
            with st.expander(t("The changes on the architecture diagram"), expanded=True):
                st.graphviz_chart(architecture_to_dot(union, dark=_is_dark(), flags=marks),
                                  width="stretch")
                st.caption(t("B's actors and contracts, with those of A that B lacks. Green: added "
                             "in B; amber: changed; grey and dashed: removed."))
        for topic, items in changes.items():
            if items:
                with st.expander(f"{topic} ({len(items)})", expanded=len(items) <= 8):
                    for item in items:
                        st.markdown(f"- {item}")
        try:
            amendment = cached_norms_diff(source_a, source_b)
        except Exception as e:
            amendment = {"contracts": [], "errors": [str(e)], "ok": False}
        if amendment is not None:
            with st.expander(t("Norms: amending A to B (norms 0.1)"), expanded=not amendment["ok"]):
                st.caption(t("What the amendment does to the norms of contract instances that "
                           "are running under A: norms removed, added or of another kind, "
                           "norms carried over with a changed content or deadline, and "
                           "`transform:` targets that B lacks (`cadl norms-diff`)."))
                for c in amendment["contracts"]:
                    st.markdown(f"**{c['id']}**" + ("" if c["findings"] else
                                                    " — no difference in `norms:` or `counts_as:`"))
                    for f in c["findings"]:
                        st.markdown(f"- {LEVEL_ICON.get(f['level'], '')} {f['text']}")
                if not amendment["contracts"] and amendment["ok"]:
                    st.markdown(t("No contract of A has norms, so no running instance is amended."))
                for e in amendment["errors"] if not amendment["contracts"] else []:
                    st.markdown(f"- {LEVEL_ICON['error']} {e}")
                if amendment.get("invalid_old"):
                    st.error(t("A cannot be read as a version with norms, so there is nothing "
                             "to amend."))
                else:
                    (st.success if amendment["ok"] else st.error)(
                        "Nothing prevents the amendment." if amendment["ok"] else
                        f"{len(amendment['errors'])} error(s) prevent the amendment.")
        raw = ds.source_diff(source_a, source_b, name_a, name_b)
        if raw:
            with st.expander(f"Source diff ({len(raw.splitlines())} lines)"):
                st.code(raw, language="diff")

        st.markdown(t("**Run both in the Explorer**"))
        st.caption(
            t("The Explorer's synthetic model is much smaller than CADL: it reads "
            "only the SoS type (Directed vs. the others), the motivation profile "
            "and ρ. Structural changes do not change its metrics.")
        )
        if st.button(t("Open A and B in the Explorer")):
            try:
                ss.custom_cadl_a = ds.to_explorer_yaml(source_a)
                ss.custom_cadl = ds.to_explorer_yaml(source_b)
                ss.explorer_from_designer = True
            except Exception as e:
                st.error(f"Could not convert the designs: {e}")
            else:
                st.switch_page("views/explorer.py")
