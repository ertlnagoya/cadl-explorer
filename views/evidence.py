"""Decision Evidence — Streamlit page.

Shows a decision record of Appendix L of the CADL specification
(``decisions.jsonl``) as evidence of consensus and accountability: under which
objective each amendment was judged, what was measured, how the parties voted,
what was recommended, and who decided, by which procedure and with which
reason. The same evidence is shown for a person, an LLM and a tool. The checks
and findings are those of ``cadl decisions-verify`` (cadl 0.3.39 or later).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cadl_sim import evidence as E  # noqa: E402
from views._i18n import t  # noqa: E402

EXAMPLES_DIR = _ROOT / "cadl_sim" / "evidence" / "examples"
BUNDLED = sorted(EXAMPLES_DIR.glob("*.decisions.jsonl")) if EXAMPLES_DIR.exists() else []


def _versions_of(record_path: Path) -> dict:
    """The CADL files of the versions of a bundled record: <name>.decisions.jsonl keeps them
    in the folder <name>.versions/ next to it."""
    folder = record_path.with_name(record_path.name.replace(".decisions.jsonl", ".versions"))
    return {f.name: f.read_bytes() for f in sorted(folder.glob("*.cadl"))} if folder.is_dir() else {}


def _dark() -> bool:
    theme = getattr(st.context, "theme", None)
    return getattr(theme, "type", None) == "dark"


st.title(t("Decision Evidence"))
st.markdown(t(
    "A decision record (Appendix L of the CADL specification) keeps, for every amendment, under "
    "which objective it was judged, what was measured, how the parties voted, what was "
    "recommended, and who decided, by which procedure and with which reason. The same evidence "
    "is asked of every party, whether a person, an LLM or a tool."))
st.caption(t(
    "Input is `decisions.jsonl`, written by `town.steward` and `town.council` in "
    "[cadl-robot-town](https://github.com/ertlnagoya/cadl-robot-town). The checks and findings "
    "are those of `cadl decisions-verify` (cadl 0.3.39 or later). "
    "[Appendix L](https://www.ertl.jp/cadl-spec/docs/spec/appendix-l-decisions)"))

with st.sidebar:
    st.header(t("Decision record"))
    upload = st.file_uploader(t("Upload a decisions.jsonl"), type=["jsonl", "json"],
                              key="evidence_upload")
    version_uploads = st.file_uploader(
        t("CADL files of the versions (optional)"), type=["cadl", "yaml", "yml"],
        accept_multiple_files=True, key="evidence_versions",
        help=t("The files that the version entries attach (v1.cadl, v2.cadl, …). With them the "
               "verifier also checks the versions, the amendable parameters and who may decide "
               "(L-9 to L-11)."))
    pick = None
    if BUNDLED:
        names = [p.name for p in BUNDLED]
        first = next((i for i, n in enumerate(names) if "steward-d-en" in n), 0)
        pick = st.selectbox(t("Bundled example"), names, index=first, key="evidence_example",
                            disabled=upload is not None, help=t("Used when no file is uploaded."))

try:
    if upload is not None:
        rec = E.load(upload.getvalue())
        source = upload.name
        version_files = {f.name: f.getvalue() for f in version_uploads or []}
    elif pick:
        rec = E.load((EXAMPLES_DIR / pick).read_bytes())
        source = pick
        version_files = _versions_of(EXAMPLES_DIR / pick)
    else:
        st.info(t("Upload a decisions.jsonl in the sidebar."))
        st.stop()
except (ValueError, UnicodeDecodeError) as e:
    st.error(t("This file cannot be read as a decision record: {e}").format(e=e))
    st.stop()

dark = _dark()
result = E.check(rec, files=version_files)
rows = E.timeline(rec, result)
found = E.findings_by_entry(result)
model = rec.header.get("model") or {}

# --- summary -------------------------------------------------------------------

st.markdown(t("**Model:** `{name}` · first version `{version}` · {n} parties · {m} entries · "
              "started {started}").format(name=model.get("name"), version=model.get("version"),
                                          n=len(rec.parties), m=len(rec.entries),
                                          started=rec.header.get("started")))
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric(t("Amendment decisions"), len(rows))
c2.metric(t("Decided by a person"), sum(r["kind"] == "human" for r in rows))
c3.metric(t("Decided by an LLM"), sum(r["kind"] == "llm" for r in rows))
c4.metric(t("Changes adopted"), sum(r["adopted"] for r in rows))
if result is None:
    c5.metric(t("Verification"), "—", help=t("Install cadl 0.3.39 or later to verify the record."))
    st.warning(t("This environment has no cadl 0.3.39 or later: the record is shown without its "
                 "checks and findings."))
else:
    c5.metric(t("Verification"), t("valid") if result["valid"] else
              t("{n} error(s)").format(n=len(result["errors"])),
              help=t("The errors L-1 to L-11 of Appendix L, L.6, as cadl decisions-verify reports "
                     "them."))
    if not result["valid"]:
        st.error(t("The record has errors and cannot be relied on as it stands. See "
                   "Accountability check."))

tabs = st.tabs([t("Timeline"), t("Decision trail"), t("Evidence by party"), t("Argument (GSN)"),
                t("Accountability check"), t("Integrity")])
tab_time, tab_trail, tab_party, tab_gsn, tab_check, tab_int = tabs

# --- timeline ------------------------------------------------------------------

with tab_time:
    st.markdown(t(
        "One column per amendment decision. Each row is one role: the objective (and who chose "
        "its reading), the votes on the proposal decided, the recommendation, the decision and "
        "the version in force after it. The colour of a decision is the kind of its party "
        "(person, LLM, tool). A red ring marks an override of the votes; an orange ring a decision "
        "against the recommendation; a square a decision made on behalf of another party without a "
        "delegation that covers it; a diamond a decision made within a delegation."))
    lanes = [t("Objective"), t("Votes"), t("Recommends"), t("Decision"), t("Version")]
    x = [f"{r['id']}" for r in rows]
    fig = go.Figure()

    def lane(y, text, colours, symbols=None, rings=None, hover=None):
        fig.add_trace(go.Scatter(
            x=x, y=[y] * len(x), mode="markers+text", text=text, textposition="top center",
            marker=dict(size=18, color=colours, symbol=symbols or "circle",
                        line=dict(width=[3 if c else 0 for c in (rings or [None] * len(x))],
                                  color=[c or "rgba(0,0,0,0)" for c in (rings or [None] * len(x))])),
            hovertext=hover or text, hoverinfo="text", showlegend=False, cliponaxis=False))

    none = "rgba(0,0,0,0)"
    lane(lanes[0], [r["objective"] if r["objective_new"] else "" for r in rows],
         [E.colour(r["reading_by"] or "human", dark) if r["objective_new"] else none for r in rows],
         hover=[f"{r['objective']}" for r in rows])
    lane(lanes[1], [r["votes"] or "" for r in rows],
         [none if r["votes"] is None else E.colour("agent" if r["vote_passes"] else "alert", dark)
          for r in rows], hover=[f"{r['on']}: {r['votes']}" for r in rows])
    lane(lanes[2], [r["recommends"] or "" for r in rows],
         [E.colour("llm", dark) if r["recommends"] else none for r in rows])
    lane(lanes[3], [r["choice"] for r in rows], [E.colour(r["kind"], dark) for r in rows],
         symbols=["square" if "delegated" in r["findings"] else "diamond" if r["delegation"]
                  else "star" if r["adopted"] else "circle"
                  for r in rows],
         rings=[E.colour("alert", dark) if "override" in r["findings"] else
                "#ffa657" if "against recommendation" in r["findings"] else None for r in rows],
         hover=[f"{r['choice']} — {r['by']} ({t(r['kind'])}, {r['procedure']})"
                + (f" · {t('layer')}: {', '.join(t(x) for x in r['layers'])}" if r['layers'] else "")
                + (f" · {t('within')} {r['delegation']}" if r["delegation"] else "")
                + f"<br>{str(r['reason'] or '')[:160]}" for r in rows])
    lane(lanes[4], [r["version"] or "" for r in rows], [E.colour("tool", dark)] * len(rows),
         symbols="square")
    fig.update_yaxes(categoryorder="array", categoryarray=list(reversed(lanes)), showgrid=False)
    fig.update_xaxes(showgrid=False)
    fig.update_layout(height=380, margin=dict(l=10, r=10, t=30, b=10))
    st.plotly_chart(fig, width="stretch", key="evidence_timeline")

    st.subheader(t("What was measured under the version in force"))
    s = pd.DataFrame(E.series(rec))
    if not s.empty:
        metrics = sorted(s["metric"].unique())
        default = [m for m in ("late_deliveries", "blocked_s", "utility @C") if m in metrics] or metrics[:2]
        chosen = st.multiselect(t("Metrics"), metrics, default=default, key="evidence_metrics")
        if chosen:
            mfig = make_subplots(rows=len(chosen), cols=1, shared_xaxes=True, vertical_spacing=0.06,
                                 subplot_titles=chosen)
            for i, m in enumerate(chosen, 1):
                part = s[s["metric"] == m]
                mfig.add_trace(go.Scatter(x=part["period"], y=part["value"], mode="lines+markers",
                                          name=m, showlegend=False), row=i, col=1)
            mfig.update_layout(height=140 + 150 * len(chosen), margin=dict(l=10, r=10, t=30, b=10))
            st.plotly_chart(mfig, width="stretch", key="evidence_series")

# --- decision trail --------------------------------------------------------------

decisions = [e for e in rec.entries if e.get("type") == "decision"]
labels = {d["id"]: f"{d['id']} · {rec.title(d.get('choice'))} · {d.get('by')} "
                   f"({t(rec.kind(d.get('by')))})" for d in decisions}

with tab_trail:
    if not decisions:
        st.info(t("The record has no decision."))
    else:
        first = next((i for i, d in enumerate(decisions) if E.is_amendment_decision(rec, d)), 0)
        did = st.selectbox(t("Decision"), [d["id"] for d in decisions], format_func=labels.get,
                           index=first, key="evidence_decision")
        d = rec.by_id[did]
        legend = " · ".join(f'<span style="color:{E.colour(k, dark)}">■</span> {t(n)}' for k, n in
                            [("human", "person"), ("llm", "LLM"), ("tool", "tool"),
                             ("agent", "agent (votes)"), ("organisation", "organisation"),
                             ("alert", "against the vote")])
        st.markdown(legend, unsafe_allow_html=True)
        dot = E.trail_dot(rec, did, dark, result, t)
        st.graphviz_chart(dot, width="stretch")
        left, right = st.columns([3, 2])
        with left:
            st.markdown(t("**Decided by** `{by}` ({kind}{obo}), procedure `{p}`").format(
                by=d.get("by"), kind=t(rec.kind(d.get("by"))), p=d.get("procedure"),
                obo=(", " + t("on behalf of") + f" `{rec.parties[d['by']]['on_behalf_of']}`")
                if (rec.parties.get(d.get("by")) or {}).get("on_behalf_of") else ""))
            g_id = ((result or {}).get("delegated_under") or {}).get(did)
            if g_id:
                g_e = rec.by_id.get(g_id) or {}
                st.markdown(t("**Within the delegation** `{g}` of `{by}`: {c}").format(
                    g=g_id, by=g_e.get("by"), c="; ".join(g_e.get("conditions") or []) or "—"))
            st.markdown(t("**Reason**: {r}").format(r=d.get("reason") or d.get("rule") or "—"))
            for f, name in (("assumptions", "Assumption"), ("unknowns", "Not known")):
                for a in d.get(f) or []:
                    st.markdown(f"- **{t(name)}**: {a}")
            for pr in d.get("predictions") or []:
                st.markdown(f"- **{t('Prediction')}**: {pr.get('subject')} → {pr.get('expected')} "
                            f"({rec.title(pr.get('option'))})")
            props = rec.basis(d, "proposal")
            if props:
                table = []
                for p in props:
                    ms = [m for m in rec.basis(d, "measurement") if m.get("of") == p["id"]]
                    vs = [v for v in rec.basis(d, "vote") if v.get("proposal") == p["id"]]
                    ch = [c for c in rec.basis(d, "check") if c.get("proposal") == p["id"]]
                    table.append({
                        "id": p["id"], t("proposal"): p.get("title"),
                        t("by"): f"{p.get('by')} ({t(rec.kind(p.get('by')))})",
                        t("layer"): ", ".join(str(c.get("layer")) for c in p.get("changes") or []),
                        t("votes"): E.votes_text(vs),
                        t("checks failed"): ", ".join(r["name"] for c in ch for r in c.get("results") or []
                                                      if r.get("passed") is False),
                        t("predictions"): len(p.get("predictions") or []),
                        t("measured"): ", ".join(f"{k} {v:g}" for k, v in list(
                            (ms[0].get("values") or {}).items())[:3]) if ms else "—",
                    })
                st.dataframe(pd.DataFrame(table), width="stretch", hide_index=True)
        with right:
            for r in rec.basis(d, "recommendation"):
                st.markdown(t("**Recommendation** by `{by}` ({kind}): {o}").format(
                    by=r.get("by"), kind=t(rec.kind(r.get("by"))), o=rec.title(r.get("recommends"))))
                st.markdown(str(r.get("reason") or ""))
                for u in r.get("unknowns") or []:
                    st.markdown(f"- **{t('Not known')}**: {u}")
            fl = found.get(did, [])
            if fl:
                st.markdown(t("**Findings**: {f}").format(f=", ".join(t(x) for x in fl)))
            fstate = {f["id"]: f for f in E.follow_ups(rec, result)}
            after = [e for e in rec.entries if e.get("decision") == did and e.get("type") == "follow_up"]
            for f in after:
                s = fstate.get(f["id"], {})
                st.markdown(t("**Follow-up** {id}: {text}").format(id=f["id"], text=f.get("text"))
                            + (f" — {t(s['status'])}" if s.get("status") else "")
                            + (" (" + ", ".join(s["fulfilled by"]) + ")" if s.get("fulfilled by") else ""))
        with st.expander(t("Show DOT source")):
            st.code(dot, language="dot")

# --- evidence by party ----------------------------------------------------------------

with tab_party:
    st.markdown(t(
        "The evidence each party gave in its proposals, recommendations and decisions, counted the "
        "same way for a person, an LLM and a tool: how many give a reason, assumptions, things not "
        "known and predictions, and how many predictions a later vote or measurement contradicted."))
    ev = pd.DataFrame(E.evidence_by_party(rec, result))
    if ev.empty:
        st.info(t("No party made a proposal, a recommendation or a decision."))
    else:
        ev["kind"] = ev["kind"].map(lambda k: t(k))
        st.dataframe(ev.rename(columns={c: t(c) for c in ev.columns}), width="stretch",
                     hide_index=True)
    parties = pd.DataFrame([{t("party"): p["id"], t("kind"): t(p.get("kind")),
                             t("roles"): ", ".join(p.get("roles") or []),
                             t("layers"): t("any") if p.get("layers") is None
                             else ", ".join(t(x) for x in p["layers"]),
                             t("on behalf of"): p.get("on_behalf_of") or "",
                             t("model"): p.get("model") or ""} for p in rec.parties.values()])
    st.subheader(t("Parties"))
    st.dataframe(parties, width="stretch", hide_index=True)
    dl = E.delegations(rec, result)
    if dl:
        st.subheader(t("Delegations"))
        st.markdown(t(
            "Who let whom decide in their place, on which layers, by which procedures and under "
            "which conditions (decisions 0.3). A decision within a delegation is not reported as "
            "delegated; one outside it is."))
        st.dataframe(pd.DataFrame([{
            "id": g["id"], t("by"): g["by"], t("to"): g["to"], t("period"): g["period"],
            t("layers"): t(g["layers"]) if g["layers"] in ("all", "none") else
            ", ".join(t(x) for x in g["layers"].split(", ")),
            t("procedures"): t(g["procedures"]), t("until"): g["until"],
            t("conditions"): g["conditions"], t("decisions within it"): ", ".join(g["decisions"]),
        } for g in dl]), width="stretch", hide_index=True)

# --- GSN -------------------------------------------------------------------------

with tab_gsn:
    st.markdown(t(
        "The record as a GSN argument, derived as Appendix L, L.8 maps it: a goal per objective, with "
        "the measure in force as its context; a strategy per decision, with its point, the "
        "recommendation, the delegation, its unknowns and its predictions as context, its "
        "measurements and checks as solutions, its reason as the justification, and its assumptions; "
        "a prediction that a later measurement contradicts as a counter-evidence (red, dashed); and a "
        "follow-up as a goal, undeveloped (a diamond) until a later entry fulfils it. The colour is "
        "the kind of the party."))
    nodes = E.gsn(rec, t, result)
    g1, g2 = st.columns([3, 1])
    gfirst = next((i for i, d in enumerate(decisions) if E.is_amendment_decision(rec, d)), 0)
    focus = g1.selectbox(t("Decision"), [d["id"] for d in decisions] or ["—"], key="evidence_gsn",
                         index=gfirst, format_func=lambda i: labels.get(i, i))
    whole = g2.toggle(t("Whole argument"), key="evidence_gsn_whole")
    gdot = E.gsn_dot(nodes, dark, None if whole or focus == "—" else focus)
    st.graphviz_chart(gdot, width="stretch")
    with st.expander(t("Show DOT source")):
        st.code(gdot, language="dot")

# --- accountability check ---------------------------------------------------------

with tab_check:
    if result is None:
        st.info(t("Install cadl 0.3.39 or later to check the record."))
    else:
        st.markdown(t(
            "Errors (L-1 to L-11) mean the record cannot be relied on as it stands. Findings are "
            "for review: an override, a decision against the recommendation, a decision made on "
            "behalf of another party or a missed prediction is not wrong in itself; the record "
            "should show who took it and why."))
        if result["errors"]:
            st.subheader(t("Errors"))
            st.dataframe(pd.DataFrame([{t("rule"): e["rule"], t("line"): e["line"],
                                        t("entry"): e["entry"], t("what"): e["what"]}
                                       for e in result["errors"]]), width="stretch", hide_index=True)
        st.subheader(t("Findings"))
        if result["findings"]:
            st.dataframe(pd.DataFrame([{t("finding"): t(f["finding"]), t("entry"): f["entry"],
                                        t("what"): f["text"]} for f in result["findings"]]),
                         width="stretch", hide_index=True)
        else:
            st.success(t("No findings."))
        wanted = E.attached(rec)
        got = [Path(v).name for v in result.get("versions_checked") or []]
        if got:
            st.caption(t("Checked against the CADL files of the versions: {files}.").format(
                files=", ".join(got)))
        elif wanted:
            st.info(t("The version entries attach {files}. Upload these files in the sidebar to "
                      "check the versions, the amendable parameters and who may decide "
                      "(L-9 to L-11).").format(files=", ".join(wanted)))
        pr = result.get("predictions") or {}       # cadl 0.3.41 or later
        if pr.get("total"):
            nc = pr["not_checked"]
            st.caption(t("Predictions: {total}; checked by a later entry: {checked} (missed: "
                         "{missed}); not checked: {rest} (about keep: {keep}, no later measurement "
                         "or vote: {none}, cannot be compared: {cannot}).").format(
                total=pr["total"], checked=pr["checked"], missed=pr["missed"],
                rest=sum(nc.values()), keep=nc.get("keep", 0), none=nc.get("no later entry", 0),
                cannot=nc.get("cannot compare", 0)))
        if result["not_checked"]:
            with st.expander(t("Not checked ({n})").format(n=len(result["not_checked"]))):
                for n in result["not_checked"]:
                    st.markdown(f"- {n}")
    st.subheader(t("Follow-ups"))
    st.markdown(t(
        "Each promise to check or do something later, what fulfils it (decisions 0.2: for a "
        "re-measurement, a measurement under the same conditions as the one it re-checks), and "
        "whether a later entry fulfilled it. A claim that the verifier does not accept leaves the "
        "promise open."))
    fu = E.follow_ups(rec, result)
    if fu:
        st.dataframe(pd.DataFrame([{
            "id": f["id"], t("by"): f"{f['by']} ({t(f['kind'])})", t("decision"): f["decision"],
            t("text"): f["text"], t("due"): f["due"] or "—", t("fulfilled by (asks)"): f["asks"] or t("any later entry"),
            t("status"): t(f["status"]), t("fulfilling entries"): ", ".join(f["fulfilled by"]),
            t("claims not shown"): ", ".join(f["not shown"])} for f in fu]),
            width="stretch", hide_index=True)
    else:
        st.info(t("The record has no follow-up."))
    st.subheader(t("Reasons on record"))
    st.dataframe(pd.DataFrame([
        {t("decision"): d["id"], t("by"): d.get("by"), t("kind"): t(rec.kind(d.get("by"))),
         t("procedure"): d.get("procedure"), t("choice"): rec.title(d.get("choice")),
         t("reason"): d.get("reason") or d.get("rule") or "—",
         t("assumptions"): "; ".join(d.get("assumptions") or []),
         t("not known"): len(d.get("unknowns") or [])} for d in decisions]),
        width="stretch", hide_index=True)

# --- integrity ----------------------------------------------------------------------

with tab_int:
    st.markdown(t(
        "Every line carries the hash chain of Appendix G, G.8: its sequence number, the digest of "
        "the line before it, and its own digest. A change of any line breaks the chain from there "
        "on. Keep the digest of the last line outside the record (an anchor): a record rewritten "
        "from a changed line on, with a new chain, is detected only against the anchor or with a "
        "key."))
    if result is not None:
        st.dataframe(pd.DataFrame([
            {t("item"): t("valid"), t("value"): str(result["valid"])},
            {t("item"): t("complete (end record)"), t("value"): str(result["complete"])},
            {t("item"): t("hash"), t("value"): str(result.get("hash"))},
            {t("item"): "run id", t("value"): str(result.get("run_id"))},
            {t("item"): t("last digest (anchor)"), t("value"): str(result.get("last_digest"))},
            {t("item"): t("lines"), t("value"): str(result.get("lines"))},
        ]), width="stretch", hide_index=True)
        anchor = st.text_input(t("Anchor kept elsewhere (optional)"), key="evidence_anchor")
        if anchor:
            if anchor.strip() == result.get("last_digest"):
                st.success(t("The record ends at the anchor."))
            else:
                st.error(t("The record does not end at the anchor: it was cut, extended or "
                           "rewritten."))
    st.download_button(t("Download this decision record"), data=rec.raw, file_name=source,
                       mime="application/jsonl")
