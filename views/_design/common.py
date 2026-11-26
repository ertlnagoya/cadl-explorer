"""State, callbacks and small helpers shared by the parts of the Designer."""

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
from views._designer_forms import SECTIONS
from views._i18n import t

SPEC_URL = "https://www.ertl.jp/cadl-spec/"
DEFAULT_EXAMPLE = "sos dsl robot delivery"
LEVEL_ICON = {
    "error": ":material/error:", "warning": ":material/warning:",
    "info": ":material/info:", "pass": ":material/check_circle:",
}
VIEWS = [
    "Architecture", "Structure", "Control structure", "Lifecycle", "Protocols", "Regimes",
    "Algorithms & metrics", "Read-back", "Summary", "Checks", "Traces", "Measurements", "Agents",
    "Versions", "Export",
]
# (stage, view to open, what to do there): the guide for a first visit.
GUIDE_STEPS = [
    ("Write", "", "Open a `.cadl` file or an example from the sidebar, and edit the source "
                  "or use the forms (Chapter 6)."),
    ("Read", "Architecture", "See the actors and contracts as diagrams, and read the design "
                             "back in sentences (Chapter 7)."),
    ("Verify", "Checks", "Read what the checks found, and run the checks of the extensions "
                         "(Chapters 8 and 10)."),
    ("Change", "Versions", "Save versions, amend the marked values within the constitution, "
                           "and compare two designs (Chapter 12, section 12.6)."),
    ("Deliver", "Export", "Download the report, the simulator configs and the runtime code "
                          "(Chapter 12, section 12.2)."),
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
    ss.design_stage = "Write"       # one column: the forms are in the Write stage
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


def _show_line(line: int):
    """Open the source at a line (one column: the Write stage)."""
    ss.design_mode = "Source"
    ss.design_stage = "Write"
    ss.design_focus = {"line": int(line), "nonce": datetime.now().timestamp(), "take": True}


def _problem_buttons(finding, key: str):
    """'Show in source', 'Open form' and 'Show in view' for one finding, side by side."""
    target = ds.view_of(finding)
    line = getattr(finding, "line", 0)
    if not (finding.section or target or line):
        return
    with st.container(horizontal=True):
        if line:
            st.button(
                t("Line {n}").format(n=line), key=f"{key}_line", on_click=_show_line, args=(line,),
                icon=":material/code:", help=t("Show this line in the source editor."))
        if finding.section:
            st.button(
                t("Open form"), key=f"{key}_form", on_click=_jump,
                args=(finding.section, finding.item),
                help=(t("Open Forms → {section} → {item}, where this can be fixed.") if finding.item
                      else t("Open Forms → {section}, where this can be fixed.")).format(
                    section=t(finding.section), item=finding.item),
            )
        if target:
            st.button(
                t("Show in {view}").format(view=t(target[0])), key=f"{key}_view", on_click=_show,
                args=(target[0], target[1], finding.item),
                help=t("Open the view where this item is outlined in red or orange."),
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
            skipped.append(t("{name} (too large; the limit is {n:,} characters)").format(
                name=f.name, n=MAX_SOURCE_CHARS))
            continue
        try:
            text = f.getvalue().decode("utf-8-sig")
        except UnicodeDecodeError:
            skipped.append(t("{name} (not UTF-8 text)").format(name=f.name))
            continue
        if len(text) > MAX_SOURCE_CHARS:
            skipped.append(t("{name} ({n:,} characters; the limit is {m:,})").format(
                name=f.name, n=len(text), m=MAX_SOURCE_CHARS))
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


def _plain(text: str) -> str:
    """Text of cadl (notes, findings, reasons) with every asterisk literal, such as
    `a * b > c * d` in a note of norms-check; code spans are left as they are."""
    parts = str(text).split("`")
    return "`".join(p if k % 2 else p.replace("*", "\\*") for k, p in enumerate(parts))


def _seq_words() -> dict:
    """The words of the sequence diagrams in the reader's language."""
    return {"no actor": t("(no actor)"), "no message": t("no message"), "state": t("state"),
            "end": t("end of the trace"), "more steps": t("more steps"),
            "no steps": t("This protocol has no steps yet."), "wait until": t("wait until"),
            "else": t("else"), "in parallel": t("in parallel"), "end parallel": t("end parallel")}


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
    before = ss.design_src
    _set_source(amend.set_values(ss.design_src, {name: new}))
    change = f"{name}: {amend.fmt(old)} → {amend.fmt(new)}"
    _record_decision(change=change, layer=layer, reason=reason, by="designer",
                     changes=[{"parameter": name, "from": amend.fmt(old), "to": amend.fmt(new),
                               "layer": layer or None}],
                     before=before, after=ss.design_src)
    ss.design_amend_done = change


def _record_decision(**entry):
    """Keep a decision of the Change stage in the session's decision record.

    Besides what the page shows (time, change, layer, reason), an entry keeps
    who proposed it, the changed values and the design before and after it,
    which the record of Appendix L needs (backend/services/design_record.py)."""
    from backend.services.design_record import now_iso
    ss.setdefault("design_decisions", []).append(
        {"time": datetime.now().strftime("%H:%M:%S"), "at": now_iso(), **entry})


def _apply_pending():
    """Apply the changes the Violation Timeline proposed (ss.design_pending)."""
    from backend.services import design_tuning as tuning
    pending = ss.get("design_pending") or {}
    overrides = pending.get("overrides") or []
    try:
        new, _ = tuning.apply_to_source(ss.design_src, overrides)
    except tuning.TuningError as e:
        ss.design_pending_error = str(e)
        return
    reason = (ss.get("design_pending_reason") or "").strip()
    # The values the design had, which may differ from those of the run the
    # proposal was made from (a condition of the run).
    ir_before = ds.analyze(ss.design_src).ir
    changes = []
    for o in overrides:
        now = tuning.current(ir_before, o)
        changes.append({"parameter": tuning.parameter(o),
                        "from": None if now is None else tuning.cadl_value({**o, "value": now}),
                        "to": tuning.cadl_value(o), "layer": "contract"})
    change = "; ".join(f"{c['parameter']}: {c['from'] or '—'} → {c['to']}" for c in changes)
    ss.design_version_name = "before the Violation Timeline's proposal"
    _save_version()
    before = ss.design_src
    _set_source(new)
    _record_decision(change=change, layer="contract", reason=reason, by="violation-timeline",
                     title=pending.get("title") or change,
                     proposal_reason=pending.get("reason") or "",
                     changes=changes, before=before, after=new)
    ss.pop("design_pending", None)
    ss.pop("design_pending_reason", None)
    ss.pop("design_pending_checked", None)
    ss.design_amend_done = change


def _discard_pending():
    ss.pop("design_pending", None)
    ss.pop("design_pending_reason", None)
    ss.pop("design_pending_checked", None)


def _use_bundled_constitution():
    ss.design_constitution = BUNDLED_CONSTITUTION.read_text(encoding="utf-8")
    ss.design_constitution_name = BUNDLED_CONSTITUTION.name


def _drop_constitution():
    ss.pop("design_constitution", None)
    ss.pop("design_constitution_name", None)
    ss.design_constitution_key = ss.get("design_constitution_key", 0) + 1   # empties the uploader



def _constitution() -> dict | None:
    """The constitution in use, or None when there is none or it cannot be read."""
    try:
        return amend.load_constitution(ss.design_constitution) if ss.get("design_constitution") else None
    except ValueError:
        return None

