"""Diagrams whose boxes can be clicked or tapped: the DOT text drawn with viz.js
(Graphviz compiled to WebAssembly) in a custom component (Streamlit custom
components v2), which reports the node that was chosen.

viz.js is loaded from jsDelivr, pinned to one version. Without it (no network,
or a Streamlit without custom components v2) the page draws the diagram with
st.graphviz_chart and offers the elements in a list instead.
"""

from __future__ import annotations

from typing import Optional

import streamlit as st

VIZ_URL = "https://cdn.jsdelivr.net/npm/@viz-js/viz@3.25.0/+esm"

_HTML = """
<div class="cadl-graph"><div class="canvas"></div><div class="note"></div></div>
"""

_CSS = """
.cadl-graph { width: 100%; }
.cadl-graph .canvas { width: 100%; overflow: auto; text-align: center; }
.cadl-graph svg { max-width: 100%; height: auto; }
.cadl-graph g.node { cursor: pointer; }
.cadl-graph g.node:hover > :is(polygon, ellipse, path) { stroke-width: 2.5px; }
.cadl-graph g.node.chosen > :is(polygon, ellipse, path) { stroke: var(--st-primary-color); stroke-width: 3px; }
.cadl-graph .note { font-size: 13px; opacity: .75; color: var(--st-text-color); }
"""

_JS = """
export default async function (component) {
  const { data, parentElement, setTriggerValue } = component
  const canvas = parentElement.querySelector(".canvas")
  const note = parentElement.querySelector(".note")
  if (!canvas) return
  if (canvas.dataset.dot === data.dot && canvas.dataset.chosen === String(data.chosen || "")) return
  try {
    window.__cadlViz = window.__cadlViz || import(data.url).then(m => m.instance())
    const viz = await window.__cadlViz
    const svg = viz.renderSVGElement(data.dot)
    const natural = svg.getAttribute("width")
    svg.removeAttribute("width")
    svg.removeAttribute("height")
    if (natural) svg.style.maxWidth = natural.endsWith("pt") ? `${parseFloat(natural) * 4 / 3}px` : natural
    canvas.replaceChildren(svg)
    canvas.dataset.dot = data.dot
    canvas.dataset.chosen = String(data.chosen || "")
    for (const node of svg.querySelectorAll("g.node")) {
      const title = node.querySelector("title")
      const id = title ? title.textContent : ""
      if (id === data.chosen) node.classList.add("chosen")
      node.setAttribute("role", "button")
      node.setAttribute("tabindex", "0")
      const choose = () => setTriggerValue("clicked", id)
      node.addEventListener("click", choose)
      node.addEventListener("keydown", e => { if (e.key === "Enter") choose() })
    }
    note.textContent = data.hint || ""
  } catch (e) {
    canvas.replaceChildren()
    note.textContent = data.failed || "The diagram could not be drawn here."
    setTriggerValue("failed", String(e))
  }
}
"""

try:
    _COMPONENT = st.components.v2.component("cadl_graph", html=_HTML, css=_CSS, js=_JS)
except AttributeError:          # Streamlit without custom components v2
    _COMPONENT = None


def available() -> bool:
    return _COMPONENT is not None and not st.session_state.get("design_graph_failed")


def _on_failed():
    st.session_state.design_graph_failed = True


def clickable_graph(dot: str, *, key: str, chosen: Optional[str], hint: str, failed: str):
    """Draw `dot`; returns the id of the node chosen in this run, or None."""
    def _on_clicked():
        clicked = getattr(st.session_state.get(key), "clicked", None)
        if clicked:     # stored before the run draws the diagram, which then shows it chosen
            st.session_state[f"{key}_chosen"] = clicked

    result = _COMPONENT(
        key=key, data={"dot": dot, "url": VIZ_URL, "chosen": st.session_state.get(f"{key}_chosen") or chosen,
                       "hint": hint, "failed": failed},
        on_clicked_change=_on_clicked, on_failed_change=_on_failed)
    return getattr(result, "clicked", None)
