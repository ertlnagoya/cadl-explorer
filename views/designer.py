"""Designer page — author a full CADL design and check it as you go.

The work goes through five stages: write the design (the source and the
forms), read it back (diagrams, tables and sentences), verify it (the checks
of cadl, recorded traces and the agent boundary), change it (versions and
amendments) and deliver it (export). Side by side, the editor stays on the
left and the other stages on the right; in one column (a tablet held upright,
a narrow window) each stage takes the whole width.

The source text in st.session_state.design_src is the single source of
truth; forms read it and write it back. The parts of the page live in
views/_design/.
"""

from __future__ import annotations

import hashlib
from types import SimpleNamespace

import streamlit as st

from backend.services import design_service as ds
from backend.services import design_workspace as workspace
from views._design import change, deliver, edit, read, sidebar, trace, verify
from views._design import layout
from views._design.cache import cached_analyze
from views._design.common import (DEFAULT_EXAMPLE, LEVEL_ICON, SPEC_URL, VIEWS, _jump, _md,
                                  _mark_reviewed, _problem_buttons, ss)
from views._designer_forms import SECTIONS
from views._i18n import t

RENDER = {
    "Architecture": read.architecture, "Structure": read.structure,
    "Control structure": read.control_structure, "Lifecycle": read.lifecycle,
    "Protocols": read.protocols, "Regimes": read.regimes,
    "Algorithms & metrics": read.algorithms, "Read-back": read.read_back,
    "Summary": verify.summary, "Checks": verify.checks, "Traces": trace.traces,
    "Agents": trace.agents, "Versions": change.versions, "Export": deliver.export,
}
# Views that do not need the design to parse.
WITHOUT_IR = {"Checks", "Versions", "Summary", "Traces", "Agents"}

if "design_src" not in ss:
    ss.design_src = (
        ds.load_example(DEFAULT_EXAMPLE) if DEFAULT_EXAMPLE in ds.list_examples() else ds.NEW_DESIGN)
    ss.design_ver = 0
for key, default in (("design_mode", "Source"), ("design_view", VIEWS[0])):
    if not ss.get(key):
        ss[key] = default
layout.normalize()
layout.viewport()

source = ss.design_src
analysis = cached_analyze(source)
ir = analysis.ir
c = SimpleNamespace(
    source=source, analysis=analysis, flags=ds.flagged(analysis), ir=ir,
    ir_contracts=(ir or {}).get("institution", {}).get("contracts") or [],
    # Form widgets are keyed by the source so they re-seed whenever it changes.
    src_key=hashlib.sha256(source.encode("utf-8")).hexdigest()[:10],
    file_stem=(analysis.name or "design").replace(" ", "_"),
)

sidebar.render(c)

# ── Header ──────────────────────────────────────────────────────────

st.title(t("Designer"))
st.markdown(t(
    "Design a System of Systems and its contract architecture in **CADL**: "
    "actors, contracts with their lifecycles and monitors, protocols, "
    "algorithms, regimes and metrics. Every change is checked and drawn."))
st.caption(t(
    "**Write** the design as source or with forms · **read** it back as diagrams and "
    "sentences · **verify** it with the checks of cadl · **change** it in versions and "
    "amendments · **deliver** it as files and code.") + f" [{t('CADL specification and hands-on')}]({SPEC_URL})")
layout.guide()


def _status():
    """Badges of the checks, changes of the assistant to review, and the problems."""
    badges = []
    for stage in ds.STAGES:
        errors, warnings = analysis.count("error", stage), analysis.count("warning", stage)
        ran = any(f.stage == stage for f in analysis.findings)
        if errors:
            badges.append(f":red-badge[{LEVEL_ICON['error']} {t(stage)} · {errors}]")
        elif warnings:
            badges.append(f":orange-badge[{LEVEL_ICON['warning']} {t(stage)} · {warnings}]")
        elif ran:
            badges.append(f":green-badge[{LEVEL_ICON['pass']} {t(stage)}]")
        else:
            badges.append(f":gray-badge[:material/remove: {t(stage)}]")
    stale = verify.stale_runs(source)
    if stale:
        badges.append(f":violet-badge[:material/history: {t('older results')} · {stale}]")
    st.markdown(" ".join(badges))

    ws_name = ss.get("design_ws_name") if workspace.enabled() else None
    pending_review = workspace.unreviewed(ws_name) if ws_name and workspace.exists(ws_name) else []
    if pending_review:
        with st.expander(
                f"{t('Changed by the assistant, not yet reviewed')} — {len(pending_review)}",
                expanded=True):
            st.caption(t(
                "The checks say these changes are consistent, not that they are what you "
                "meant. Look at each one, then confirm it."))
            for i, change_ in enumerate(pending_review):
                st.markdown(_md(
                    f":material/smart_toy: **{change_['summary']}**"
                    + (f"  \n*{t('Why')}:* {change_['rationale']}" if change_.get("rationale") else "")))
                with st.container(horizontal=True):
                    st.button(t("Reviewed"), key=f"review_{i}", on_click=_mark_reviewed, args=(i,))
                    if change_.get("section") in SECTIONS:
                        st.button(t("Open form"), key=f"review_form_{i}", on_click=_jump,
                                  args=(change_["section"], change_.get("item", "")))
            st.button(t("Mark all reviewed"), on_click=_mark_reviewed, key="review_all")

    problems = [f for f in analysis.findings if f.level in ("error", "warning")]
    n_errors = sum(f.level == "error" for f in problems)
    if not problems:
        st.caption(t("No problems found. **Checks** lists what was checked — and what is not."))
        return
    n_warnings = len(problems) - n_errors
    label = ", ".join(
        f"{n} {t(word + ('s' if n != 1 else ''))}"
        for n, word in ((n_errors, "error"), (n_warnings, "warning")) if n)
    # In one column the Write stage puts the editor below this list; warnings stay
    # closed there, since the editor marks their lines.
    writing = not layout.side_by_side() and ss.get("design_stage") == "Write"
    with st.expander(f"{t('Problems')} — {label}",
                     expanded=bool(n_errors) or (len(problems) <= 3 and not writing)):
        for i, f in enumerate(problems[:8]):
            where = f" ({t('line')} {f.line})" if getattr(f, "line", None) else ""
            st.markdown(
                f"{LEVEL_ICON[f.level]} **{f.title}**{where}" + (f"  \n{f.message}" if f.message else ""))
            _problem_buttons(f, f"jump_{i}")
        if len(problems) > 8:
            st.caption(t("{n} more under **Checks**.").format(n=len(problems) - 8))


def _views():
    view = layout.view_picker()
    if view not in WITHOUT_IR and ir is None:
        st.info(t("This view appears once the design parses. Fix the problem listed above."))
    else:
        RENDER[view](c)


if layout.side_by_side():
    col_edit, col_view = st.columns(2, gap="large")
    with col_edit:
        edit.render(c)
    with col_view:
        layout.stage_picker()
        _status()
        _views()
else:
    stage = layout.stage_picker()
    if stage == "Write":
        _status()
        edit.render(c)
    else:
        _status()
        _views()
