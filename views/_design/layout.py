"""The stages of the Designer, the view picker and the layout (side by side or one column).

`design_view` names the view; the stage is the one that view belongs to, so a
jump to a view (from a problem, a diagram or a test) also selects its stage.
"Write" is a stage of its own only in one column; side by side, the editor is
always on the left.
"""

from __future__ import annotations

import streamlit as st

from views._design.common import VIEWS, ss
from views._i18n import t

STAGES = {
    "Write": [],
    "Read": ["Architecture", "Structure", "Control structure", "Lifecycle", "Protocols",
             "Regimes", "Algorithms & metrics", "Read-back"],
    "Verify": ["Summary", "Checks", "Traces", "Agents"],
    "Change": ["Versions"],
    "Deliver": ["Export"],
}
STAGE_ICON = {
    "Write": ":material/edit_note:", "Read": ":material/visibility:",
    "Verify": ":material/fact_check:", "Change": ":material/history:",
    "Deliver": ":material/output:",
}
LAYOUTS = ["Side by side", "One column"]
# Tablets and phones that say so in their user agent start in one column. An
# iPad with a desktop browser does not say so; its user switches the layout.
_NARROW_AGENTS = ("ipad", "android", "mobile", "tablet", "silk", "kindle")


def stage_of(view: str) -> str:
    return next((s for s, views in STAGES.items() if view in views), "Read")


def _default_layout() -> str:
    try:
        agent = (st.context.headers.get("User-Agent") or "").lower()
    except Exception:           # no request in a bare run
        agent = ""
    return LAYOUTS[1] if any(word in agent for word in _NARROW_AGENTS) else LAYOUTS[0]


def side_by_side() -> bool:
    return ss.get("design_layout") != LAYOUTS[1]


# Below this width (CSS pixels) the page starts in one column: a tablet held upright,
# or a narrow window. The width comes from the browser (see _viewport).
ONE_COLUMN_BELOW = 1100

_VIEWPORT_JS = """
export default function (component) {
  const { setStateValue, data } = component
  const report = () => {
    if (Math.abs((data.width || 0) - window.innerWidth) > 40) setStateValue("width", window.innerWidth)
  }
  report()
  if (!window.__cadlResize) {
    window.__cadlResize = true
    let timer = null
    window.addEventListener("resize", () => { clearTimeout(timer); timer = setTimeout(report, 400) })
  }
}
"""

try:
    _VIEWPORT = st.components.v2.component("cadl_viewport", html="<span></span>", js=_VIEWPORT_JS)
except AttributeError:          # Streamlit without custom components v2
    _VIEWPORT = None


def viewport():
    """Ask the browser for the width of the window; it comes back in the next run."""
    if _VIEWPORT is not None:
        state = ss.get("design_viewport") or {}
        width = getattr(state, "width", None) if not isinstance(state, dict) else state.get("width")
        _VIEWPORT(key="design_viewport", data={"width": width or 0},
                  on_width_change=lambda: None)


def _width():
    state = ss.get("design_viewport") or {}
    width = state.get("width") if isinstance(state, dict) else getattr(state, "width", None)
    return width if isinstance(width, (int, float)) and width > 0 else None


def normalize():
    """Make the stage, the view and the layout agree before any widget is drawn."""
    width = _width()
    if not ss.get("design_layout_chosen") and width:
        # until the reader chooses a layout, it follows the width of the window
        ss.design_layout = LAYOUTS[1] if width < ONE_COLUMN_BELOW else LAYOUTS[0]
    if ss.get("design_layout") not in LAYOUTS:
        ss.design_layout = _default_layout()
    if ss.get("design_view") not in VIEWS:
        ss.design_view = ss.get("design_view_prev") if ss.get("design_view_prev") in VIEWS else VIEWS[0]
    ss.design_view_prev = ss.design_view
    last = ss.setdefault("design_last_view", {})
    last[stage_of(ss.design_view)] = ss.design_view
    stage = ss.get("design_stage")
    if stage != "Write" or side_by_side():
        ss.design_stage = stage_of(ss.design_view)


def _on_stage():
    stage = ss.get("design_stage")
    if stage is None:               # a click on the selected stage deselects it
        ss.design_stage = stage_of(ss.design_view)
    elif stage != "Write":
        ss.design_view = ss.get("design_last_view", {}).get(stage) or STAGES[stage][0]


def _on_layout():
    if ss.get("design_layout") is None:
        ss.design_layout = LAYOUTS[0]
    ss.design_layout_chosen = True


def stage_picker() -> str:
    """The stages, and the layout switch next to them; returns the stage."""
    stages = [s for s in STAGES if s != "Write" or not side_by_side()]
    with st.container(horizontal=True, vertical_alignment="center"):
        st.pills(
            t("Stage"), stages, key="design_stage", on_change=_on_stage,
            format_func=lambda s: f"{STAGE_ICON[s]} {t(s)}", label_visibility="collapsed")
        st.segmented_control(
            t("Layout"), LAYOUTS, key="design_layout", on_change=_on_layout,
            format_func=lambda s: ":material/view_column_2:" if s == LAYOUTS[0] else ":material/view_agenda:",
            label_visibility="collapsed",
            help=t("Side by side: the editor on the left, the other stages on the right. "
                   "One column: each stage takes the whole width, as on a tablet held upright."))
    return ss.design_stage


def _on_view():
    if ss.get("design_view_pick"):
        ss.design_view = ss.design_view_pick


def view_picker() -> str:
    """The views of the current stage; returns the view to draw. The picker has a key
    of its own, so that a jump that sets `design_view` is not undone by its value."""
    views = STAGES[stage_of(ss.design_view)]
    if len(views) > 1:
        ss.design_view_pick = ss.design_view
        st.pills(
            t("View"), views, key="design_view_pick", format_func=t, on_change=_on_view,
            label_visibility="collapsed")
    return ss.design_view


def guide():
    """A path through the stages for a first visit, with the chapters of the textbook."""
    from views._design.common import GUIDE_STEPS, TEXTBOOK_URL
    with st.expander(t("First time here? A path through the Designer"), icon=":material/school:"):
        for i, (stage, view, text) in enumerate(GUIDE_STEPS, 1):
            cols = st.columns([5, 2], vertical_alignment="center")
            cols[0].markdown(f"**{i}. {t(stage)}** — {t(text)}")
            if view:
                cols[1].button(t("Go to {view}").format(view=t(view)), key=f"guide_{i}",
                               on_click=_go, args=(stage, view), width="stretch")
            else:
                cols[1].button(t("Go to {view}").format(view=t("Write")), key=f"guide_{i}",
                               on_click=_go, args=(stage, view), width="stretch")
        st.caption(t("The textbook works through the same steps:") + f" [{t('CADL textbook')}]({TEXTBOOK_URL})")


def _go(stage: str, view: str):
    if view:
        ss.design_view = view
        ss.design_stage = stage
    else:
        ss.design_stage = "Write"
        ss.design_mode = "Source"
