"""Decision Evidence — Streamlit page.

Shows the evidence of an amendment loop as a record of consensus and
accountability: under which KPI the rules were judged, what was measured, how
the companies voted, what the LLM recommended, and who decided, with the
reason. Input is an evidence bundle (``evidence.json``) written by
``python -m town.steward`` in cadl-robot-town.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cadl_sim import evidence as E  # noqa: E402
from views._i18n import t  # noqa: E402

EXAMPLES_DIR = _ROOT / "cadl_sim" / "evidence" / "examples"
BUNDLED = sorted(EXAMPLES_DIR.glob("*.evidence.json")) if EXAMPLES_DIR.exists() else []


def _dark() -> bool:
    theme = getattr(st.context, "theme", None)
    return getattr(theme, "type", None) == "dark"


st.title(t("Decision Evidence"))
st.markdown(t(
    "An amendment loop changes the rules period by period. This page shows the record of each "
    "change as evidence: under which KPI it was judged, what was measured, how the companies "
    "voted, what the LLM recommended, and who decided, with the reason."))
st.caption(t(
    "Input is `evidence.json`, written by `python -m town.steward` in "
    "[cadl-robot-town](https://github.com/ertlnagoya/cadl-robot-town) next to `decisions.md` and "
    "`gsn.yaml`. Check the files of a run with `python -m town.evidence verify runs/<name>`."))

with st.sidebar:
    st.header(t("Evidence source"))
    upload = st.file_uploader(t("Upload an evidence.json"), type=["json"], key="evidence_upload")
    pick = None
    if BUNDLED:
        pick = st.selectbox(t("Bundled example"), [p.name for p in BUNDLED], key="evidence_example",
                            disabled=upload is not None, help=t("Used when no file is uploaded."))

try:
    if upload is not None:
        doc = E.load(upload.getvalue())
        source = upload.name
    elif pick:
        doc = E.load((EXAMPLES_DIR / pick).read_text(encoding="utf-8"))
        source = pick
    else:
        st.info(t("Upload an evidence.json in the sidebar."))
        st.stop()
except ValueError as e:
    st.error(t("This file cannot be read as evidence: {e}").format(e=e))
    st.stop()

run = doc["run"]
dark = _dark()
chain_ok = E.chain_ok(doc)
rows = E.timeline(doc)
found = E.flags(doc, t)

# --- summary -------------------------------------------------------------------

st.markdown(t("**Run:** `{name}` ({tool}, {mode}, {lang}) · LLM {llm} `{model}` · cadl {cadl} · {created}").format(
    name=run.get("name"), tool=run.get("tool"), mode=run.get("mode"), lang=run.get("lang"),
    llm=run.get("llm"), model=run.get("model"), cadl=run.get("cadl"), created=run.get("created")))
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(t("Periods"), len(rows))
c2.metric(t("Decided by the human"), sum(r["by"] == "human" for r in rows),
          help=t("The rest were decided by the tool, by the gates of the constitution."))
c3.metric(t("Changes adopted"), sum(r["adopted"] for r in rows))
c4.metric(t("Against the vote"), sum(r["vote_overridden"] for r in rows),
          help=t("Adopted although the companies' vote did not reach the quorum."))
c5.metric(t("Hash chain"), t("intact") if chain_ok else t("broken"),
          help=t("Each record's hash covers the previous hash, so a change to any record breaks the chain "
                 "from there on."))
if not chain_ok:
    st.error(t("The hash chain is broken: a record was changed after it was written. See Integrity."))

tab_time, tab_trail, tab_gsn, tab_check, tab_int = st.tabs(
    [t("Timeline"), t("Decision trail"), t("Argument (GSN)"), t("Accountability check"), t("Integrity")])

# --- timeline ------------------------------------------------------------------

with tab_time:
    st.markdown(t(
        "One column per period. Each row is one party: the human who writes the KPI, the companies "
        "who vote, the LLM who recommends, and whoever decided. A red ring marks a change adopted "
        "against the vote; an orange ring a decision that differs from the LLM's recommendation."))
    lanes = [t("KPI (human)"), t("Votes (companies)"), t("LLM recommends"), t("Decision"), t("Rules in force")]
    fig = go.Figure()
    x = [r["period"] for r in rows]

    def lane(y, text, colours, symbols=None, lines=None, hover=None):
        fig.add_trace(go.Scatter(
            x=x, y=[y] * len(x), mode="markers+text", text=text, textposition="top center",
            marker=dict(size=18, color=colours, symbol=symbols or "circle",
                        line=dict(width=[3 if c else 0 for c in (lines or [None] * len(x))],
                                  color=lines and [c or "rgba(0,0,0,0)" for c in lines])),
            hovertext=hover or text, hoverinfo="text", showlegend=False, cliponaxis=False))

    lane(lanes[0],
         [r["kpi"] or "" for r in rows],
         [E.colour("human", dark) if r["kpi"] and r["kpi_by"] == "human" else
          E.colour("tool", dark) if r["kpi"] else "rgba(0,0,0,0)" for r in rows],
         hover=[f"{r['kpi_in_force']}" for r in rows])
    lane(lanes[1], [r["votes"] if r["focus"] else "" for r in rows],
         [E.colour("companies", dark) if r["vote_passes"] else E.colour("alert", dark) if r["focus"]
          else "rgba(0,0,0,0)" for r in rows],
         hover=[f"{r['focus']}: {r['votes']}" if r["focus"] else t("no proposal") for r in rows])
    lane(lanes[2], [r["llm"] or "" for r in rows],
         [E.colour("llm", dark) if r["asked"] else "rgba(0,0,0,0)" for r in rows])
    lane(lanes[3], [r["choice"] for r in rows],
         [E.colour("human", dark) if r["by"] == "human" else E.colour("tool", dark) for r in rows],
         symbols=["star" if r["adopted"] else "circle" for r in rows],
         lines=[E.colour("alert", dark) if r["vote_overridden"] else
                E.colour("caution", dark) if r["followed_llm"] is False else None for r in rows],
         hover=[f"{r['choice']} ({r['by']})<br>{(r['reason'] or '')[:160]}" for r in rows])
    lane(lanes[4], [f"v{r['version']}" for r in rows], [E.colour("tool", dark)] * len(rows),
         symbols="square")
    fig.update_yaxes(categoryorder="array", categoryarray=list(reversed(lanes)), showgrid=False)
    fig.update_xaxes(tickmode="array", tickvals=x,
                     ticktext=[f"{t('Period')} {r['period']}<br>{r['env']}" for r in rows], showgrid=False)
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, width="stretch", key="evidence_timeline")

    st.subheader(t("What was measured under the rules in force"))
    s = pd.DataFrame(E.series(doc))
    if not s.empty:
        metrics = sorted(s["metric"].unique())
        default = [m for m in ("late_deliveries", "blocked_s", "utility C") if m in metrics]
        chosen = st.multiselect(t("Metrics"), metrics, default=default, key="evidence_metrics")
        mfig = make_subplots(rows=max(len(chosen), 1), cols=1, shared_xaxes=True, vertical_spacing=0.06,
                             subplot_titles=chosen)
        for i, m in enumerate(chosen, 1):
            part = s[s["metric"] == m]
            mfig.add_trace(go.Scatter(x=part["period"], y=part["value"], mode="lines+markers", name=m,
                                      showlegend=False), row=i, col=1)
        for r in rows:
            if r["kpi"] and r["period"] > 1:
                mfig.add_vline(x=r["period"] - 0.5, line_dash="dot", line_color=E.colour("human", dark))
            if r["adopted"]:
                mfig.add_vline(x=r["period"] + 0.5, line_dash="dash", line_color=E.colour("tool", dark))
        mfig.update_layout(height=140 + 150 * len(chosen), margin=dict(l=10, r=10, t=30, b=10))
        mfig.update_xaxes(tickmode="array", tickvals=x, ticktext=[f"{t('Period')} {p}" for p in x])
        st.plotly_chart(mfig, width="stretch", key="evidence_series")
        st.caption(t("A grey dashed line marks where an adopted version takes effect (the next period); a blue "
                     "dotted line where a new KPI starts."))

# --- decision trail --------------------------------------------------------------

with tab_trail:
    ids = [r["id"] for r in doc["records"]]
    labels = {r["id"]: (f"{r['id']} · {t('Period')} {r['period']} · " +
                        (t("KPI") if r["type"] == "kpi" else E.choice_label(r, r["decision"]["choice"])))
              for r in doc["records"]}
    rid = st.selectbox(t("Record"), ids, format_func=labels.get, key="evidence_record",
                       index=ids.index("D1") if "D1" in ids else 0)
    rec = E.record(doc, rid)
    legend = " · ".join(f'<span style="color:{E.colour(a, dark)}">■</span> {t(n)}' for a, n in
                        [("human", "human"), ("llm", "LLM"), ("tool", "tool (measured)"),
                         ("companies", "companies' vote passes"), ("alert", "against the vote"),
                         ("caution", "asked the human")])
    st.markdown(legend, unsafe_allow_html=True)
    dot = E.decision_dot(doc, rid, dark, t)
    st.graphviz_chart(dot, width="stretch")
    d = rec["decision"]
    left, right = st.columns([3, 2])
    with left:
        if rec["type"] == "decision":
            st.markdown(t("**Outcome** ({by}): {o}").format(by=t(d["by"]), o=d["outcome"]))
            if d.get("reason"):
                st.markdown(t("**Reason**: {r}").format(r=d["reason"]))
            for a in d.get("assumptions") or []:
                st.markdown(t("**Assumption**: {a}").format(a=a))
            table = []
            for c in rec.get("candidates", []):
                m = c.get("metrics") or {}
                table.append({
                    "id": c["id"], t("proposal"): c["label"], t("layer"): c.get("layer"),
                    t("gain"): c.get("gain"),
                    t("seeds improved"): f"{c['seeds_better']}/{c['seeds']}" if c.get("seeds") else None,
                    t("late"): m.get("late_deliveries"), t("blocked s"): m.get("blocked_s"),
                    t("road violations"): m.get("road_violations"),
                    t("utility"): " / ".join(f"{k} {v:.1f}" for k, v in (c.get("utility") or {}).items()),
                    t("votes"): E.votes_text(c.get("votes")),
                    t("gates failed"): ", ".join(g for g in E.GATES if c["gates"].get(g) is False),
                    t("why asked"): ", ".join(t(E.ESCALATION.get(x, x)) for x in c.get("escalation", [])),
                })
            if table:
                st.dataframe(pd.DataFrame(table), width="stretch", hide_index=True)
            if rec.get("current"):
                st.caption(t("Under the rules in force (v{v}): ").format(v=rec["version_in_force"]) + ", ".join(
                    f"{k} {v:g}" for k, v in (rec["current"]["metrics"] or {}).items()))
        else:
            st.markdown(t("**KPI** (from period {s}): {text}").format(s=rec["since"], text=rec["text"]))
            st.markdown(t("**Measure in force**: {m}").format(m=rec["measure_text"]))
            if d.get("reason"):
                st.markdown(t("**Reason**: {r}").format(r=d["reason"]))
            for a in d.get("assumptions") or []:
                st.markdown(t("**Assumption**: {a}").format(a=a))
    with right:
        req = rec.get("request")
        if rec["type"] == "kpi" and rec.get("how") == "llm":
            st.markdown(t("**LLM reading**: {r}").format(r=rec.get("interpretation", "")))
            for c in rec.get("concerns", []):
                st.markdown(f"- {c}")
        if req:
            st.markdown(t("**Question to the human**: {q}").format(q=req["question"]))
            st.markdown(t("**LLM recommends** {id}: {r}").format(
                id=E.choice_label(rec, req.get("recommendation")), r=req.get("recommendation_reason") or ""))
            if req.get("unknowns"):
                st.markdown(t("**The LLM did not know**"))
                for u in req["unknowns"]:
                    st.markdown(f"- {u}")
        elif rec["type"] == "decision" and rec.get("assessment"):
            st.markdown(t("**LLM assessment**: {a}").format(a=rec["assessment"]))
        files = [rec.get(k) for k in ("evidence_file", "llm_record")] + [
            (req or {}).get("file"), d.get("version_file"), rec.get("request_file")]
        st.caption(t("Files of this record: ") + ", ".join(f"`{f}`" for f in files if f))
    with st.expander(t("Show DOT source")):
        st.code(dot, language="dot")

# --- GSN -------------------------------------------------------------------------

with tab_gsn:
    st.markdown(t(
        "The same run as a GSN argument (`gsn.yaml`): the top goal is supported per KPI version and per "
        "period; contexts, assumptions (A) and justifications (J) hang from them with dashed edges. "
        "Nodes written by the human are blue. Pick a record to highlight its part of the argument."))
    g1, g2 = st.columns([3, 1])
    hl = g1.selectbox(t("Highlight"), [r["id"] for r in doc["records"]], key="evidence_gsn_hl",
                      format_func=lambda x: labels.get(x, x))
    whole = g2.toggle(t("Whole argument"), key="evidence_gsn_whole",
                      help=t("Draw every element. Otherwise only the highlighted part and its path to the top goal."))
    if doc.get("gsn"):
        gdot = E.gsn_dot(doc, dark, hl, focus=not whole)
        st.graphviz_chart(gdot, width="stretch")
        with st.expander(t("Show DOT source")):
            st.code(gdot, language="dot")
    else:
        st.info(t("This bundle has no GSN."))

# --- accountability check ---------------------------------------------------------

with tab_check:
    st.markdown(t(
        "What a reviewer should look at. A change adopted against the vote, a decision that differs from "
        "the LLM, and a decision without a reason are not errors in themselves; the record should show "
        "who took them and why."))
    icon = {"ok": "✅", "info": "ℹ️", "warn": "⚠️", "error": "⛔"}
    level = st.segmented_control(t("Show"), ["error", "warn", "info", "ok"], default=["error", "warn", "info"],
                                 selection_mode="multi", key="evidence_levels", format_func=lambda x: icon[x] + " " + t(x))
    shown = [f for f in found if f["level"] in (level or [])]
    if shown:
        st.dataframe(pd.DataFrame([{"": icon[f["level"]], t("record"): f["record"], t("kind"): f["kind"],
                                    t("finding"): f["text"]} for f in shown]),
                     width="stretch", hide_index=True)
    else:
        st.success(t("Nothing to show at these levels."))
    st.subheader(t("Reasons on record"))
    st.dataframe(pd.DataFrame([
        {t("record"): r["id"], t("by"): t(r["decision"]["by"]),
         t("decision"): r["decision"].get("outcome") or f"{t('reading')} {r['decision'].get('choice')}",
         t("reason"): r["decision"].get("reason") or "—",
         t("assumptions"): "; ".join(r["decision"].get("assumptions") or [])}
        for r in doc["records"]]), width="stretch", hide_index=True)

# --- integrity ----------------------------------------------------------------------

with tab_int:
    st.markdown(t(
        "Each record's hash is SHA-256 over the previous hash and the record, so changing a record "
        "breaks the chain from that record on. The files of the run (rules, measurements, LLM outputs) "
        "are listed with their hashes; `python -m town.evidence verify runs/<name>` compares them with "
        "the files on disk."))
    st.dataframe(pd.DataFrame([{t("record"): r["id"], t("stored hash"): r["stored"][:16],
                                t("recomputed"): r["recomputed"][:16], "": "✅" if r["ok"] else "⛔"}
                               for r in E.verify_chain(doc)]), width="stretch", hide_index=True)
    st.subheader(t("Inputs"))
    st.dataframe(pd.DataFrame([{t("input"): k, t("file"): v["path"], "sha256": v["sha256"][:16]}
                               for k, v in (doc.get("inputs") or {}).items()]), width="stretch", hide_index=True)
    st.subheader(t("Files of the run"))
    st.dataframe(pd.DataFrame([{t("file"): a["path"], t("bytes"): a["bytes"], "sha256": a["sha256"][:16]}
                               for a in doc.get("artifacts", [])]), width="stretch", hide_index=True)
    st.download_button(t("Download this evidence.json"), data=json.dumps(doc, ensure_ascii=False, indent=1),
                       file_name=source, mime="application/json")
