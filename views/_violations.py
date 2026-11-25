"""Charts and tables of the Violation Timeline page (views/violations.py).

The data come from cadl_sim.sos_dsl.violation_trace; this module only draws.
"""

from __future__ import annotations

from typing import Dict, List, Optional, Sequence, Tuple

import altair as alt
import pandas as pd

from cadl_sim.sos_dsl.violation_trace import CONTINUED, TrialView
from views._i18n import t

# States: the categorical palette of the dataviz reference, in a fixed order,
# with the neutral grey for the initial state and red kept for Violated.
STATE_COLORS = {
    "Proposed": ("#b8b7b0", "#6b6a64"),
    "Assigned": ("#2a78d6", "#3987e5"),
    "Accepted": ("#1baf7a", "#199e70"),
    "Delivering": ("#6250d6", "#9085e9"),
    "Completed": ("#008300", "#0ca30c"),
    "Violated": ("#e34948", "#e66767"),
    "Terminated": ("#52514e", "#8a8983"),
}
_EXTRA = [("#eda100", "#c98500"), ("#e87ba4", "#d55181"), ("#eb6834", "#d95926")]

STEP = 24          # pixels per lane; yOffset of the marks is from the top of a lane
MARKS = ["reported", "unreported", "injected"]
MARK_SHAPES = {"reported": "cross", "unreported": "circle", "injected": "triangle-down"}


def mark_label(kind: str) -> str:
    return {"reported": t("reported violation"),
            "unreported": t("unreported violation (continued evaluation)"),
            "injected": t("injected fault")}[kind]


def lane_label(iid: str, track: str) -> str:
    return f"{iid} · " + (t("continued") if track == CONTINUED else t("report"))


def _state_scale(states: Sequence[str], dark: bool) -> alt.Scale:
    domain, rng = [], []
    extra = iter(_EXTRA)
    for s in states:
        if s in domain:
            continue
        pair = STATE_COLORS.get(s) or next(extra, ("#888888", "#999999"))
        domain.append(s)
        rng.append(pair[1] if dark else pair[0])
    return alt.Scale(domain=domain, range=rng)


def timeline_chart(view: TrialView, instances: Sequence[str],
                   t_range: Optional[Tuple[int, int]] = None, dark: bool = False,
                   state_order: Sequence[str] = ()) -> alt.LayerChart:
    """State bands per instance (report, and continued evaluation below it),
    with the violations and injected faults as marks."""
    lo, hi = t_range or (0, view.trial.end_ms)
    span = max(1, hi - lo)
    stub = max(1, span * 0.006)
    lanes: List[str] = []
    bars, marks, windows = [], [], []
    for s in view.instances:
        if s.instance_id not in instances:
            continue
        ivs = view.intervals.get(s.instance_id, [])
        tracks = ["reported"] + ([CONTINUED] if s.has_continued else [])
        for tr in tracks:
            lanes.append(lane_label(s.instance_id, tr))
        for iv in ivs:
            end = iv.end if iv.end > iv.start else iv.start + stub
            if end < lo or iv.start > hi:
                continue
            bars.append({"lane": lane_label(s.instance_id, iv.track), "state": iv.state,
                         "start": max(iv.start, lo), "end": min(end, hi),
                         "from (ms)": iv.start, "to (ms)": iv.end if iv.closed else None})
        rep_lane = lane_label(s.instance_id, "reported")
        cont_lane = lane_label(s.instance_id, CONTINUED if s.has_continued else "reported")
        if s.reported:
            d = s.reported
            marks.append({"lane": rep_lane, "t": d.t_ms, "mark": mark_label("reported"),
                          "what": d.detected_by, "severity": d.severity,
                          "onset from (ms)": d.onset_lo})
        for d in s.unreported:
            marks.append({"lane": cont_lane, "t": d.t_ms, "mark": mark_label("unreported"),
                          "what": d.detected_by, "severity": d.severity,
                          "onset from (ms)": d.onset_lo})
        for d in s.detections + ([s.reported] if s.reported else []):
            if not d.exact:
                lane = rep_lane if d.track == "reported" else cont_lane
                windows.append({"lane": lane, "lo": max(d.onset_lo, lo), "hi": d.t_ms,
                                "what": d.detected_by})
        for inj in s.injections:
            marks.append({"lane": rep_lane, "t": int(inj.get("t_ms") or 0),
                          "mark": mark_label("injected"), "what": str(inj.get("fault")),
                          "severity": "", "onset from (ms)": None, "off": 2})
    marks = [dict(m, off=m.get("off", STEP // 2)) for m in marks if lo <= m["t"] <= hi]
    windows = [w for w in windows if w["hi"] >= lo and w["lo"] <= hi]
    states = list(state_order) + [b["state"] for b in bars]
    y = alt.Y("lane:N", sort=lanes, title=None,
              axis=alt.Axis(labelLimit=260, labelOverlap=False, ticks=False))
    x_scale = alt.Scale(domain=[lo, hi], nice=False)

    base_bars = alt.Chart(pd.DataFrame(bars or [{"lane": "", "state": "", "start": 0,
                                                 "end": 0, "from (ms)": 0, "to (ms)": 0}]))
    band = base_bars.mark_bar(height=14, cornerRadius=3, stroke=None).encode(
        x=alt.X("start:Q", scale=x_scale, title=t("time (ms)")),
        x2="end:Q", y=y,
        color=alt.Color("state:N", scale=_state_scale(states, dark), title=t("state"),
                        legend=alt.Legend(orient="bottom")),
        tooltip=["lane", "state", "from (ms)", "to (ms)"])
    layers = [band]
    ink = "#f0efec" if dark else "#0b0b0b"
    if windows:
        layers.append(alt.Chart(pd.DataFrame(windows)).mark_rule(
            strokeWidth=2, strokeDash=[3, 2], color=ink, opacity=0.55).encode(
            x=alt.X("lo:Q", scale=x_scale), x2="hi:Q", y=y,
            tooltip=[alt.Tooltip("what", title=t("may have begun within"))]))
    if marks:
        mk = [mark_label(k) for k in MARKS]
        layers.append(alt.Chart(pd.DataFrame(marks)).mark_point(
            size=110, strokeWidth=2.2, filled=False, opacity=1).encode(
            x=alt.X("t:Q", scale=x_scale), y=y,
            yOffset=alt.YOffset("off:Q", scale=None),
            shape=alt.Shape("mark:N", scale=alt.Scale(domain=mk, range=[
                MARK_SHAPES[k] for k in MARKS]), title=t("mark"),
                legend=alt.Legend(orient="top", labelLimit=400)),
            stroke=alt.Stroke("mark:N", scale=alt.Scale(domain=mk, range=[
                ink, "#d95926" if dark else "#eb6834", "#d55181" if dark else "#c2185b"]),
                legend=None),
            tooltip=["lane", "mark", "what", alt.Tooltip("t", title="t (ms)"),
                     "severity", "onset from (ms)"]))
    return alt.layer(*layers).properties(height=alt.Step(STEP)).resolve_scale(
        color="independent", shape="independent", stroke="independent")


def instance_rows(view: TrialView) -> List[Dict]:
    rows = []
    for s in view.instances:
        cand = s.candidate.detected_by if s.candidate else ""
        if cand and s.candidate_ambiguous:
            cand += " " + t("(order unsure within a monitor period)")
        rows.append({
            t("instance"): s.instance_id,
            t("final state"): s.final_state,
            t("reported"): s.reported.detected_by + f" @ {s.reported.t_ms}" if s.reported else "",
            t("not reported"): ", ".join(f"{d.detected_by} @ {d.t_ms}" for d in s.unreported),
            t("masking"): s.masked,
            t("candidate cause"): cand,
            t("injected"): ", ".join(f"{i.get('fault')} @ {i.get('t_ms')}" for i in s.injections),
            t("report names the fault"): s.reported_matches,
            t("candidate names the fault"): s.candidate_matches,
            t("why missed"): ", ".join(s.reasons),
        })
    return rows


MEASURES = ["recall", "reported match", "candidate match", "masking"]


def measures_chart(rows: Sequence[Dict], dark: bool = False) -> alt.Chart:
    """The share and its 95% interval for each measure and condition."""
    data = []
    for r in rows:
        for m in MEASURES:
            if r.get(m) is None:
                continue
            ci = r[f"{m} 95% CI"].strip("[]").split(",")
            data.append({"condition": r["condition"], "measure": t(m), "share": r[m],
                         "lo": float(ci[0]), "hi": float(ci[1]), "k/n": r[f"{m} k/n"]})
    df = pd.DataFrame(data)
    names = [t(m) for m in MEASURES]
    colors = ["#3987e5", "#d95926", "#199e70", "#c98500"] if dark else \
             ["#2a78d6", "#eb6834", "#1baf7a", "#eda100"]
    color = alt.Color("measure:N", scale=alt.Scale(domain=names, range=colors), title=None,
                      legend=alt.Legend(orient="top"))
    y = alt.Y("condition:N", title=None, sort=list(dict.fromkeys(df["condition"])),
              axis=alt.Axis(labelLimit=360, labelOverlap=False, ticks=False))
    y_off = alt.YOffset("measure:N", sort=names, scale=alt.Scale(domain=names))
    scale = alt.Scale(domain=[0, 1])
    tip = ["condition", "measure", alt.Tooltip("share", format=".0%"), "k/n",
           alt.Tooltip("lo", format=".2f"), alt.Tooltip("hi", format=".2f")]
    base = alt.Chart(df)
    rule = base.mark_rule(strokeWidth=2).encode(
        x=alt.X("lo:Q", scale=scale, axis=alt.Axis(format="%"),
                title=t("share (95% Wilson interval)")),
        x2="hi:Q", y=y, yOffset=y_off, color=color, tooltip=tip)
    point = base.mark_point(filled=True, size=60, opacity=1).encode(
        x=alt.X("share:Q", scale=scale), y=y, yOffset=y_off, color=color, tooltip=tip)
    # With yOffset, the step is that of one measure inside a condition.
    return alt.layer(rule, point).properties(height=alt.Step(11))
