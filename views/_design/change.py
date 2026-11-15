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
from views._i18n import japanese, t

# The nouns diff_designs writes after "added" / "removed".
_DIFF_NOUNS = ("actor", "contract", "transition", "monitor", "protocol", "algorithm", "metric")
_DIFF_ITEM = re.compile(r"^(?P<pre>.+? — )?(?P<verb>added|removed) (?P<noun>%s) (?P<key>.+)$"
                        % "|".join(_DIFF_NOUNS), re.S)


def _diff_item(item: str) -> str:
    """One line of the comparison in the reader's language: "added contract X" and
    "removed contract X" are translated; field changes stay as they are."""
    if not japanese():
        return item
    m = _DIFF_ITEM.match(item)
    if not m:
        return item
    text = t("added {noun} {name}" if m["verb"] == "added" else "removed {noun} {name}")
    return (m["pre"] or "") + text.format(noun=t(m["noun"]), name=m["key"])


def _labels(*keys: str) -> dict:
    """Column labels in the reader's language; the column keys stay English."""
    return {k: st.column_config.Column(t(k)) for k in keys}


def _source_label(name: str) -> str:
    """A choice of the comparison ("Current design", "Version: v1", "Example: x") for display."""
    if name == "Current design":
        return t("Current design")
    kind, sep, rest = name.partition(": ")
    if sep and kind == "Version":
        return t("Version: {name}").format(name=rest)
    if sep and kind == "Example":
        return t("Example: {name}").format(name=rest)
    return name


def _quantity(value, unit) -> str:
    """A value of the amendment IR (in its base unit) as one writes it."""
    if value is None:
        return "—"
    if unit == "ms" and isinstance(value, (int, float)):
        for size, suffix in ((3_600_000, "h"), (60_000, "min"), (1000, "s")):
            if value and value % size == 0:
                return f"{value / size:g}{suffix}"
        return f"{value:g}ms"
    text = f"{value:g}" if isinstance(value, float) else f"{value}"
    return text + (f" {unit}" if unit else "")


def _authority(p: dict) -> str:
    who = p.get("decided_by") or "—"
    if p.get("procedure") == "vote":
        who += " " + (t("by vote, quorum {n}").format(n=p["quorum"]) if p.get("quorum")
                      else t("by vote"))
    return who


def _authority_text(text) -> str:
    """amend-diff's "COUNCIL by vote, quorum 2" in the reader's language."""
    import re as _re
    match = _re.fullmatch(r"(.+?) by vote(?:, quorum (\d+))?", text or "")
    if not match:
        return text or "—"
    return _authority({"decided_by": match.group(1), "procedure": "vote",
                       "quorum": int(match.group(2)) if match.group(2) else None})


def _declared_section(ir) -> bool:
    """The amendable parameters that the design declares (`amendment: 0.1`, Appendix K
    of the specification, cadl 0.3.32). Returns whether there was anything to show."""
    declared = (ir or {}).get("amendment")
    if not isinstance(declared, dict):
        return False
    st.markdown(t("**Declared amendable parameters (amendment 0.1)**"))
    head = [t("version {v}").format(v=declared["version"]) if declared.get("version") else None,
            t("replaces {v}").format(v=declared["previous"]) if declared.get("previous") else None]
    effective = declared.get("effective") or {}
    if "period" in effective:
        head.append(t("effective from period {p}").format(p=effective["period"]))
    elif effective.get("at"):
        head.append(t("effective at {at}").format(at=effective["at"]))
    transitional = declared.get("transitional") or {}
    if transitional.get("default"):
        head.append(t("running instances: {rule}").format(rule=transitional["default"]))
    if any(head):
        st.markdown(" · ".join(h for h in head if h))
    st.caption(t("The design names which values may be amended, by whom and in what steps. "
                 "Comparing two versions below checks an amendment against this declaration "
                 "(`cadl amend-diff`)."))
    rows = [{"parameter": p["id"], "value": _quantity(p.get("value"), p.get("unit")),
             "layer": p.get("layer") or "—", "decided by": _authority(p),
             "range": f"{_quantity(p.get('min'), p.get('unit'))}–{_quantity(p.get('max'), p.get('unit'))}",
             "step": _quantity(p.get("step"), p.get("unit")),
             "at most": "—" if p.get("max_steps") is None else
             t("{n} step" if p["max_steps"] == 1 else "{n} steps").format(n=p["max_steps"]),
             "safety": t("yes") if p.get("safety") else "",
             "where": ", ".join(p.get("at") or [])}
            for p in declared.get("parameters") or []]
    if rows:
        st.dataframe(rows, width="stretch", hide_index=True,
                     column_config=_labels("parameter", "value", "layer", "decided by", "range",
                                           "step", "at most", "safety", "where"))
    else:
        st.markdown(t("The design declares the extension but no parameter."))
    switching = [f"{c['contract']} ({c['rule']})" for c in transitional.get("contracts") or []]
    if switching:
        st.markdown(t("Transitional rules per contract: {list}").format(list=", ".join(switching)))
    return True


# Lines of amend-diff shown before the rest is folded: two unrelated designs give
# one error per difference.
_AMEND_LINES = 12


def _amend_diff_section(source_a: str, source_b: str):
    """`cadl amend-diff A B`: is B an amendment of A that its declaration allows?"""
    try:
        report = cached_amend_diff(source_a, source_b)
    except Exception as e:
        report = {"parameters": [], "contracts": [], "findings": [], "errors": [str(e)], "ok": False}
    if report is None:
        return
    unreadable = bool(report.get("invalid_old"))
    declared = "amendment" in ds.extensions_in_effect(source_a)
    with st.expander(t("Amendment: A to B under the declaration of A (amendment 0.1)"),
                     expanded=declared or unreadable):
        if not (declared or unreadable):
            st.info(t("A does not declare amendment 0.1, so it allows no change: every "
                      "difference from A is reported. Choose the earlier version of B as A."))
        st.caption(t("Each changed parameter is checked against the range, the steps and the "
                     "largest change that A declares; a change of the rules that no parameter "
                     "explains is an error (`cadl amend-diff`)."))
        if report.get("invalid_old"):
            for e in report["errors"]:
                st.markdown(f"- {LEVEL_ICON['error']} {e}")
            st.error(t("A cannot be read, so there is nothing to amend."))
            return
        changed = [p for p in report.get("parameters") or [] if p.get("changed")]
        if changed:
            st.dataframe([{"parameter": p["id"], "A": p.get("old"), "B": p.get("new"),
                           "layer": p.get("layer"), "decided by": _authority_text(p.get("decided_by")),
                           "safety": t("yes") if p.get("safety") else ""} for p in changed],
                         width="stretch", hide_index=True,
                         column_config=_labels("parameter", "A", "B", "layer", "decided by", "safety"))
        elif report.get("parameters"):
            st.markdown(t("No declared parameter changes."))
        for c in report.get("contracts") or []:
            rule = (c.get("transitional") or {}).get("rule")
            status = t(c.get("status") or "")
            st.markdown(f"**{c['id']}** — "
                        + (t("{status} by {names}").format(status=status, names=", ".join(c["by"]))
                           if c.get("by") else status)
                        + (" · " + t("running instances: {rule}").format(rule=rule) if rule else ""))
            for f in c.get("findings") or []:
                st.markdown(f"- {LEVEL_ICON.get(f['level'], '')} {f['text']}")
        # the errors are findings of level "error" as well; each line once, errors first
        lines = [(f["level"], f["text"]) for f in report.get("findings") or []
                 if f.get("kind") != "parameter_changed"]
        texts = {text for _, text in lines}
        lines += [("error", e) for e in report.get("errors") or [] if e not in texts]
        lines.sort(key=lambda line: {"error": 0, "warning": 1}.get(line[0], 2))
        for level, text in lines[:_AMEND_LINES]:
            st.markdown(f"- {LEVEL_ICON.get(level, '')} {text}")
        if len(lines) > _AMEND_LINES:
            st.caption(t("… and {n} more.").format(n=len(lines) - _AMEND_LINES))
        (st.success if report["ok"] else st.error)(
            t("The changes respect the declared amendability.") if report["ok"] else
            t("{n} error(s): the changes do not respect the declared amendability.")
            .format(n=len(report.get("errors") or [])))


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
        use.button(t("Stop using {name}").format(name=ss["design_constitution_name"])
                   if ss.get("design_constitution_name") else t("Stop using the constitution"),
                   on_click=_drop_constitution, width="stretch")
    elif BUNDLED_CONSTITUTION.exists():
        use.button(t("Use the Robot Town constitution"), on_click=_use_bundled_constitution,
                   width="stretch", help=t("The constitution.yaml bundled with the robot town example."))
    const = None
    if ss.get("design_constitution"):
        try:
            const = amend.load_constitution(ss.design_constitution)
        except ValueError as e:
            st.warning(f"{ss.get('design_constitution_name') or t('The constitution')}: {e}")
    rows = []
    for a in params:
        spec = (const or {}).get(a.name)
        rows.append({
            "parameter": a.name, "value": amend.fmt(a.value),
            "lines": ", ".join(str(o.line) for o in a.occurrences),
            **({"layer": amend.layer_of(spec),
                "range": (f"{amend.fmt(spec.get('min'))}–{amend.fmt(spec.get('max'))}" if spec else "—"),
                "step": amend.fmt(spec.get("step")) if spec else "—",
                "decided by": t("a person" if amend.needs_human(spec) else "the checks") if spec
                              else t("not amendable")} if const is not None else {}),
        })
    st.dataframe(rows, hide_index=True, width="stretch",
                 column_config=_labels("parameter", "value", "lines", "layer", "range", "step",
                                       "decided by"))
    for note in amend.findings(source, const):
        st.markdown(f"- {note}")
    if ss.get("design_amend_done"):
        st.success(t("Amended {what}. The design before it is saved as a version; Undo also "
                     "takes it back.").format(what=ss.pop("design_amend_done")))

    names = [a.name for a in params if a.value is not None]
    if names:
        c_name, c_value = st.columns(2)
        if ss.get("design_amend_name") not in names:   # kept from an earlier design
            ss.pop("design_amend_name", None)
        name = c_name.selectbox(t("Amend"), names, key="design_amend_name")
        current = next(a for a in params if a.name == name).value
        spec = (const or {}).get(name) or {}
        step = float(spec.get("step") or (1.0 if current == int(current) else 0.1))
        whole = step == int(step) and current == int(current)   # integers stay integers on screen
        value = c_value.number_input(t("New value (now {value})").format(value=amend.fmt(current)),
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
                st.markdown(t("- The constitution leaves `{name}` ({layer}) to a person. Write "
                              "the reason for your decision; it is kept in the decision record "
                              "below.").format(name=name, layer=amend.layer_of(spec)))
                reason = st.text_input(t("Reason for the decision"), key=f"design_amend_reason_{name}",
                                       placeholder=t("why this change, and what you take for granted"))
            blocked = const is not None and not gate.ok
            st.button(t("Amend {name} to {value}").format(name=name, value=amend.fmt(value)),
                      on_click=_amend_apply,
                      args=(name, current, value, amend.layer_of(spec) if spec else ""), type="primary",
                      disabled=blocked or (bool(gate.for_a_person) and not reason.strip()),
                      help=t("The constitution does not allow this amendment." if blocked else
                             "Saves the current design as a version first, then writes the new value "
                             "into every line marked with this name."))

    record = ss.get("design_decisions") or []
    if record:
        with st.expander(t("Decision record ({n})").format(n=len(record))):
            st.dataframe(record, hide_index=True, width="stretch",
                         column_config=_labels("time", "change", "layer", "reason"))
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
                st.dataframe(table, hide_index=True, width="stretch",
                             column_config=_labels("parameter", "current"))
                st.caption(t("One column per saved version, then the current design; ← marks a "
                           "value that differs from the column before."))
                moved = amend.movement(sequence[:-1], const)    # the saved versions, not the editor
                if moved:
                    st.markdown(t("**How far and how often the values moved**"))
                    st.dataframe(moved, hide_index=True, width="stretch",
                                 column_config=_labels(*(moved[0] if moved else ())))
                    st.caption(
                        t("Across the saved versions, in the order above (the current design is "
                          "left out, as it may be another design). *Steps* count the moves in units "
                          "of the constitution's `step`{hint}. Exercise 12-1 of Course D divides "
                          "by the original `step`: choose the constitution before its step was changed "
                          "(the `constitution.yaml` restored with `git checkout`, or the Robot Town "
                          "constitution); one with the changed step counts in that step. "
                          "A *reversal* is a move opposite to the one before it — the oscillation "
                          "the exercise counts. How soon the KPI is reached, and the loss on the "
                          "way, come from the simulation (`town.compare`); the rules do not give them.")
                        .format(hint="" if const is not None else
                                t(" (choose a constitution to see them)")))



def versions(c):
    source, analysis = c.source, c.analysis
    st.caption(
        t("Keep named versions of the design while you work, compare any two, "
        "and go back to one. Versions last for this browser session.")
    )
    v_name, v_save = st.columns([3, 2], vertical_alignment="bottom")
    v_name.text_input(t("Name for the current design"), key="design_version_name",
                      placeholder=t("e.g. before adding safety contract"))
    v_save.button(t("Save as a version"), on_click=_save_version, width="stretch",
                  disabled=not analysis.parsed, type="primary")

    with st.expander(t("Add CADL files as versions")):
        st.caption(
            t("Choose several files at once — for example `runs/<name>/v1.cadl`, `v2.cadl`, ... "
              "of a Course D run. They are added in the order of their names (v2 before v10), "
              "named after the files; at most {n} versions are kept.").format(n=MAX_VERSIONS))
        st.text_input(t("Prefix for their names (optional)"), key="design_version_prefix",
                      placeholder=t("e.g. step30, to tell the runs of different conditions apart"))
        picked = st.file_uploader(
            t("CADL files"), type=["cadl", "yaml", "yml"], accept_multiple_files=True,
            key=f"design_version_files_{ss.get('design_version_files_key', 0)}")
        count = len(picked or [])
        st.button(t("Add {n} file as a version" if count == 1 else "Add {n} files as versions")
                  .format(n=count),
                  on_click=_add_files_as_versions, disabled=not picked)
    report = ss.pop("design_files_added", None)
    if report:
        if report["added"]:
            st.success(t("Added {names}.").format(
                names=", ".join(f"**{n}**" for n in report["added"])))
        if report["dropped"]:
            st.warning(t("Only the last {n} versions are kept; {names} no longer fit and were "
                         "removed.").format(n=MAX_VERSIONS,
                                            names=", ".join(f"**{n}**" for n in report["dropped"])))
        for item in report["skipped"]:
            st.error(t("Not added: {item}.").format(item=item))

    versions = ss.get("design_versions", [])
    for i, version in enumerate(versions):
        same = version["source"] == source
        r_name, r_restore, r_delete = st.columns([4, 1, 1], vertical_alignment="center")
        r_name.markdown(
            f"**{version['name']}** · " + t("saved {time}").format(time=version["time"])
            + (t(" · same as the current design") if same else ""))
        r_restore.button(t("Restore"), key=f"version_restore_{i}", width="stretch",
                         on_click=_restore_version, args=(i,), disabled=same,
                         help=t("Make this version the current design. Undo takes it back."))
        r_delete.button(t("Delete"), key=f"version_delete_{i}", width="stretch",
                        on_click=_delete_version, args=(i,))

    st.divider()
    if _declared_section(c.ir if analysis.parsed else None):
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
    name_a = c_a.selectbox(t("A — baseline"), names, key="design_cmp_a", format_func=_source_label)
    name_b = c_b.selectbox(t("B — compared"), names, key="design_cmp_b", format_func=_source_label)

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
            return " " + t("({n} step)" if n == 1 else "({n} steps)").format(n=amend.fmt(n))

        moved = [f"`{k}` {amend.fmt(old_values.get(k))} → {amend.fmt(new_values.get(k))}{_in_steps(k)}"
                 for k in dict.fromkeys(list(old_values) + list(new_values))
                 if old_values.get(k) != new_values.get(k)]
        if moved:
            st.markdown(t("Amended parameters (`# @amend:`): {list}").format(list=", ".join(moved)))
        st.markdown(
            f"**A** `{base.name}` → **B** `{other.name}` — "
            + (t("{n} structural change" if total == 1 else "{n} structural changes").format(n=total)
               if total else t("no structural change"))
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
                with st.expander(f"{t(topic)} ({len(items)})", expanded=len(items) <= 8):
                    for item in items:
                        st.markdown(f"- {_diff_item(item)}")
        _amend_diff_section(source_a, source_b)
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
                                                    t(" — no difference in `norms:` or `counts_as:`")))
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
                        t("Nothing prevents the amendment.") if amendment["ok"] else
                        t("{n} error(s) prevent the amendment.").format(n=len(amendment["errors"])))
        raw = ds.source_diff(source_a, source_b, name_a, name_b)
        if raw:
            with st.expander(t("Source diff ({n} lines)").format(n=len(raw.splitlines()))):
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
                st.error(t("Could not convert the designs: {error}").format(error=e))
            else:
                st.switch_page("views/explorer.py")
