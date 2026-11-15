"""The CADL editor: a plain text area with line numbers, problem marks, completion
of names and snippets, built as a custom component (Streamlit custom components
v2). It works with the on-screen keyboard of a tablet, where a code editor such
as Ace does not, and it does not correct or capitalize what one types.

Python gives it the source, the problems by line, the names of the design and
the snippets; it gives back the text when one presses Apply, Ctrl/⌘ + Enter or
leaves the editor. `available()` is False on a Streamlit without custom
components v2 (Python 3.9 has Streamlit 1.50 at most); the page then uses Ace.
"""

from __future__ import annotations

from typing import Callable, List, Optional

import streamlit as st

_HTML = """
<div class="cadl-ed">
  <div class="bar">
    <button type="button" data-act="apply" class="primary" title="Apply (Ctrl/⌘ + Enter)">Apply</button>
    <button type="button" data-act="indent" title="Indent the line or the selection (Tab)">⇥</button>
    <button type="button" data-act="outdent" title="Outdent (Shift + Tab)">⇤</button>
    <button type="button" data-act="comment" title="Comment or uncomment the lines">#</button>
    <select class="snip" title="Insert a snippet at the cursor"><option value="">Insert…</option></select>
    <span class="state"></span>
  </div>
  <div class="body">
    <div class="gutter" aria-hidden="true"></div>
    <textarea class="code" spellcheck="false" autocapitalize="off" autocomplete="off"
              autocorrect="off" wrap="off" aria-label="CADL source"></textarea>
    <ul class="pop" role="listbox" hidden></ul>
  </div>
  <div class="msg" aria-live="polite"></div>
</div>
"""

_CSS = """
.cadl-ed { font-family: var(--st-font, sans-serif); color: var(--st-text-color); }
.cadl-ed .bar { display: flex; flex-wrap: wrap; gap: 6px; align-items: center; margin-bottom: 6px; }
.cadl-ed button, .cadl-ed select {
  min-height: 36px; min-width: 40px; padding: 4px 12px; font-size: 14px;
  border: 1px solid var(--st-border-color, #ccc); border-radius: var(--st-button-radius, 6px);
  background: var(--st-secondary-background-color); color: var(--st-text-color); cursor: pointer; }
.cadl-ed button.primary { background: var(--st-primary-color); color: var(--st-background-color);
  border-color: var(--st-primary-color); }
.cadl-ed button:disabled { opacity: .5; cursor: default; }
.cadl-ed .state { font-size: 12px; opacity: .75; }
.cadl-ed .body { position: relative; display: flex; border: 1px solid var(--st-border-color, #ccc);
  border-radius: var(--st-base-radius, 6px); overflow: hidden; background: var(--st-background-color); }
.cadl-ed .gutter, .cadl-ed .code { font-family: var(--st-code-font, ui-monospace, Menlo, monospace);
  font-size: 13px; line-height: 20px; }
.cadl-ed .gutter { flex: none; width: 52px; height: 100%; box-sizing: border-box; padding: 8px 0 28px; overflow: hidden; text-align: right;
  background: var(--st-secondary-background-color); user-select: none; }
.cadl-ed .gutter div { padding-right: 8px; height: 20px; opacity: .6; cursor: pointer; }
.cadl-ed .gutter div.error { background: rgba(255, 75, 75, .35); opacity: 1; font-weight: bold; }
.cadl-ed .gutter div.warning { background: rgba(255, 165, 0, .35); opacity: 1; }
.cadl-ed .gutter div.focus { outline: 2px solid var(--st-primary-color); outline-offset: -2px; opacity: 1; }
.cadl-ed .code { flex: 1; height: 100%; box-sizing: border-box; border: 0; outline: none; resize: none; padding: 8px; white-space: pre;
  overflow: auto; tab-size: 2; background: transparent; color: var(--st-text-color); }
.cadl-ed .pop { position: absolute; z-index: 5; margin: 0; padding: 2px 0; list-style: none;
  max-height: 220px; overflow-y: auto; min-width: 200px; font-size: 13px;
  background: var(--st-background-color); border: 1px solid var(--st-border-color, #ccc);
  border-radius: 6px; box-shadow: 0 4px 14px rgba(0, 0, 0, .2); }
.cadl-ed .pop li { padding: 6px 10px; cursor: pointer; display: flex; justify-content: space-between; gap: 12px; }
.cadl-ed .pop li small { opacity: .6; }
.cadl-ed .pop li.on { background: var(--st-primary-color); color: var(--st-background-color); }
.cadl-ed .msg { min-height: 20px; font-size: 13px; margin-top: 4px; }
.cadl-ed .msg.error { color: var(--st-red-color, #d33); }
.cadl-ed .msg.warning { color: var(--st-orange-color, #c60); }
"""

_JS = """
export default function (component) {
  const { data, parentElement, setTriggerValue } = component
  const root = parentElement.querySelector(".cadl-ed")
  if (!root) return
  const ta = root.querySelector(".code")
  const gutter = root.querySelector(".gutter")
  const pop = root.querySelector(".pop")
  const msg = root.querySelector(".msg")
  const state = root.querySelector(".state")
  const snip = root.querySelector(".snip")
  const L = data.labels || {}
  const lineHeight = 20

  root.querySelector(".body").style.height = (data.height || 560) + "px"
  root.querySelector('[data-act="apply"]').textContent = L.apply || "Apply"
  snip.options[0].textContent = L.insert || "Insert…"

  // Python replaced the source (another file, undo, a form): show it. Otherwise
  // keep what is being typed, which Python has not seen yet.
  if (root.dataset.version !== String(data.version)) {
    root.dataset.version = String(data.version)
    if (ta.value !== data.value) ta.value = data.value
    root.dataset.dirty = ""
  }
  const marks = {}
  for (const a of data.annotations || []) {
    const row = a.row
    if (!marks[row] || a.type === "error") marks[row] = a
  }

  function setDirty(on) {
    root.dataset.dirty = on ? "1" : ""
    state.textContent = on ? (L.unsaved || "Not applied yet") : ""
  }
  setDirty(!!root.dataset.dirty)

  function lines() { return ta.value.split("\\n") }

  function drawGutter() {
    const n = lines().length
    let html = ""
    for (let i = 0; i < n; i++) {
      const m = marks[i]
      html += `<div data-row="${i}" class="${m ? m.type : ""}" title="${m ? m.text.replace(/"/g, "&quot;") : ""}">${i + 1}</div>`
    }
    gutter.innerHTML = html
    gutter.scrollTop = ta.scrollTop
  }

  function caretRow() { return ta.value.slice(0, ta.selectionStart).split("\\n").length - 1 }

  function showMessage() {
    const m = marks[caretRow()]
    msg.className = "msg " + (m ? m.type : "")
    msg.textContent = m ? `${L.line || "Line"} ${m.row + 1}: ${m.text}` : ""
  }

  function apply() {
    closePop()
    setDirty(false)
    setTriggerValue("applied", ta.value)
  }

  function lineStart(pos) { return ta.value.lastIndexOf("\\n", pos - 1) + 1 }

  function replace(start, end, text, caret) {
    ta.setRangeText(text, start, end, "end")
    if (caret !== undefined) ta.setSelectionRange(caret, caret)
    setDirty(true)
    drawGutter()
  }

  function shiftLines(out) {
    const s = lineStart(ta.selectionStart)
    let e = ta.value.indexOf("\\n", ta.selectionEnd)
    if (e < 0) e = ta.value.length
    const block = ta.value.slice(s, e).split("\\n").map(l =>
      out ? l.replace(/^ {1,2}/, "") : "  " + l).join("\\n")
    ta.setRangeText(block, s, e, "select")
    setDirty(true)
    drawGutter()
  }

  function toggleComment() {
    const s = lineStart(ta.selectionStart)
    let e = ta.value.indexOf("\\n", ta.selectionEnd)
    if (e < 0) e = ta.value.length
    const rows = ta.value.slice(s, e).split("\\n")
    const all = rows.every(l => !l.trim() || /^\\s*#/.test(l))
    const block = rows.map(l => all ? l.replace(/^(\\s*)# ?/, "$1") : l.replace(/^(\\s*)/, "$1# ")).join("\\n")
    ta.setRangeText(block, s, e, "select")
    setDirty(true)
    drawGutter()
  }

  // ── completion ──
  let items = [], active = 0, wordStart = 0
  function closePop() { pop.hidden = true; items = [] }
  function currentWord() {
    const pos = ta.selectionStart
    const before = ta.value.slice(lineStart(pos), pos)
    const m = before.match(/[A-Za-z_][\\w.\\[\\]*]*$/)
    return m ? { word: m[0], start: pos - m[0].length } : null
  }
  function openPop() {
    const w = currentWord()
    if (!w || w.word.length < 2) { closePop(); return }
    const low = w.word.toLowerCase()
    items = (data.names || []).filter(n => n.name.toLowerCase().startsWith(low) && n.name !== w.word).slice(0, 12)
    if (!items.length) { closePop(); return }
    wordStart = w.start
    active = 0
    pop.innerHTML = items.map((n, i) =>
      `<li data-i="${i}" class="${i === 0 ? "on" : ""}"><span>${n.name}</span><small>${n.kind}</small></li>`).join("")
    const row = caretRow()
    const col = ta.selectionStart - lineStart(ta.selectionStart)
    const charWidth = 7.8
    pop.style.top = Math.min((row + 1) * lineHeight + 8 - ta.scrollTop, ta.clientHeight - 40) + "px"
    pop.style.left = Math.max(56, Math.min(56 + 8 + col * charWidth - ta.scrollLeft, ta.clientWidth - 160)) + "px"
    pop.hidden = false
  }
  function choose(i) {
    const n = items[i]
    if (!n) return
    replace(wordStart, ta.selectionStart, n.name)
    closePop()
    ta.focus()
  }
  function highlight(i) {
    active = (i + items.length) % items.length
    pop.querySelectorAll("li").forEach((li, k) => li.classList.toggle("on", k === active))
    const li = pop.querySelectorAll("li")[active]
    if (li) li.scrollIntoView({ block: "nearest" })
  }
  pop.onmousedown = e => e.preventDefault()
  pop.onclick = e => {
    const li = e.target.closest("li")
    if (li) choose(Number(li.dataset.i))
  }

  // ── snippets ──
  if (snip.options.length === 1) {
    for (const [i, s] of (data.snippets || []).entries()) {
      const o = document.createElement("option")
      o.value = String(i)
      o.textContent = s.label
      o.title = s.where
      snip.appendChild(o)
    }
  }
  snip.onchange = () => {
    const s = (data.snippets || [])[Number(snip.value)]
    snip.value = ""
    if (!s) return
    const pos = ta.selectionStart
    const start = lineStart(pos)
    const line = ta.value.slice(start, pos)
    const indent = (line.match(/^\\s*/) || [""])[0]
    const blank = !line.trim()
    const body = s.text.split("\\n").map((l, k) => (k === 0 && !blank ? "" : indent) + l).join("\\n")
    const text = blank ? body.slice(indent.length) : "\\n" + indent + body.slice(indent.length)
    replace(pos, pos, text, pos + text.length)
    msg.className = "msg"
    msg.textContent = (L.inserted || "Inserted; it goes into") + " " + s.where + "."
    ta.focus()
  }

  // ── keys and events ──
  ta.onkeydown = e => {
    if (!pop.hidden && items.length) {
      if (e.key === "ArrowDown") { e.preventDefault(); highlight(active + 1); return }
      if (e.key === "ArrowUp") { e.preventDefault(); highlight(active - 1); return }
      if (e.key === "Enter" || e.key === "Tab") { e.preventDefault(); choose(active); return }
      if (e.key === "Escape") { e.preventDefault(); closePop(); return }
    }
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); apply(); return }
    if (e.key === "Tab") {
      e.preventDefault()
      if (ta.selectionStart !== ta.selectionEnd || e.shiftKey) shiftLines(e.shiftKey)
      else replace(ta.selectionStart, ta.selectionEnd, "  ")
      return
    }
    if (e.key === "Enter" && !e.shiftKey && !e.altKey) {
      e.preventDefault()
      const pos = ta.selectionStart
      const line = ta.value.slice(lineStart(pos), pos)
      let indent = (line.match(/^\\s*/) || [""])[0]
      if (/:\\s*$/.test(line)) indent += "  "
      else if (/^\\s*-\\s+\\w[\\w-]*:\\s*\\S/.test(line)) indent += "  "
      replace(pos, ta.selectionEnd, "\\n" + indent)
    }
  }
  ta.oninput = () => { setDirty(true); drawGutter(); openPop() }
  ta.onscroll = () => { gutter.scrollTop = ta.scrollTop; closePop() }
  ta.onkeyup = e => { if (!["ArrowDown", "ArrowUp"].includes(e.key) || pop.hidden) showMessage() }
  ta.onclick = () => { closePop(); showMessage() }
  ta.onblur = () => {
    setTimeout(() => {
      if (root.contains(document.activeElement) || (parentElement.activeElement &&
          root.contains(parentElement.activeElement))) return
      closePop()
      if (root.dataset.dirty) apply()
    }, 150)
  }
  gutter.onclick = e => {
    const div = e.target.closest("div[data-row]")
    if (!div) return
    const row = Number(div.dataset.row)
    const all = lines()
    const start = all.slice(0, row).join("\\n").length + (row ? 1 : 0)
    ta.focus()
    ta.setSelectionRange(start, start + all[row].length)
    showMessage()
  }
  root.querySelector('[data-act="apply"]').onclick = apply
  root.querySelector('[data-act="indent"]').onclick = () => { ta.focus(); shiftLines(false) }
  root.querySelector('[data-act="outdent"]').onclick = () => { ta.focus(); shiftLines(true) }
  root.querySelector('[data-act="comment"]').onclick = () => { ta.focus(); toggleComment() }

  drawGutter()

  // Python asks to show a line (a problem, an element of a diagram)
  if (data.focus && root.dataset.focus !== String(data.focus.nonce)) {
    root.dataset.focus = String(data.focus.nonce)
    const row = Math.max(0, (data.focus.line || 1) - 1)
    const all = lines()
    if (row < all.length) {
      const start = all.slice(0, row).join("\\n").length + (row ? 1 : 0)
      ta.scrollTop = Math.max(0, row * lineHeight - ta.clientHeight / 3)
      gutter.scrollTop = ta.scrollTop
      ta.setSelectionRange(start, start + all[row].length)
      const div = gutter.querySelector(`div[data-row="${row}"]`)
      if (div) div.classList.add("focus")
      showMessage()
      if (data.focus.take) ta.focus({ preventScroll: true })
    }
  }
}
"""

try:
    _COMPONENT = st.components.v2.component("cadl_editor", html=_HTML, css=_CSS, js=_JS)
except AttributeError:          # Streamlit without custom components v2
    _COMPONENT = None


def available() -> bool:
    return _COMPONENT is not None


def cadl_editor(value: str, *, version: str, annotations: List[dict], names: List[dict],
                snippets: List[dict], labels: dict, height: int = 560, focus: Optional[dict] = None,
                key: str, on_applied: Callable[[], None]):
    """Mount the editor; `on_applied` runs when the text is applied, and reads it from
    st.session_state[key]["applied"]."""
    return _COMPONENT(
        key=key,
        data={"value": value, "version": version, "annotations": annotations, "names": names,
              "snippets": snippets, "labels": labels, "height": height, "focus": focus},
        on_applied_change=on_applied,
    )
