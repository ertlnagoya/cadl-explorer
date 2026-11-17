"""Traces and the agent boundary: the design against recorded runs.

- Traces: `cadl norms-eval` gives the verdict of each norm on one recorded run of
  a contract instance. The trace can be a file, one of the examples, a witness
  of norms-check, or text typed on the page. With amendment 0.1 the other versions
  of the run and a time base apply the versions while the run goes on (K.11).
- Measurements: `cadl metrics-eval` judges measured values of the metrics against
  the target in force at the start of their window.
- Agents: `cadl agents-run` replays recorded messages through the enforcement
  point of the agent governance extension, and `cadl audit-verify` checks the
  hash chain of the audit log it writes.
"""

from __future__ import annotations

import hashlib
import json
import re

import pandas as pd
import streamlit as st

from backend.plotting.sequence import sequence_svg, trace_protocol
from backend.services import design_runtime as runtime
from backend.services import design_service as ds
from views._design.cache import _NotCached, _uncached
from views._design.common import _is_dark, _load_example, _plain, ss
from views._i18n import t

TRACES_DIR = ds.DESIGNS_DIR / "traces"
MEASUREMENTS_DIR = ds.DESIGNS_DIR / "measurements"
STATE_COLOR = {"inactive": "#9e9e9e", "active": "#1f77b4", "fulfilled": "#2ca02c",
               "violated": "#d62728", "excused": "#ff7f0e", "expired": "#8c564b",
               "suspended": "#bcbd22", "exercised": "#17becf", "repaired": "#9467bd"}
# marks, since a table does not draw the icons of Material Symbols
VERDICT_ICON = {"fulfilled": "✓", "violated": "✗", "excused": "–", "pending": "…",
                "exercised": "↯"}


@st.cache_data(show_spinner=False, max_entries=32)
def _norms_eval(source: str, trace: str, contract: str, versions: tuple = (),
                base: tuple = (None, 0.0, False)) -> dict:
    return _uncached(runtime.norms_eval(source, trace, contract or None, versions, *base))


@st.cache_data(show_spinner=False, max_entries=16)
def _metrics_eval(source: str, measurements: str, versions: tuple = (),
                  base: tuple = (None, 0.0, False)) -> dict:
    return _uncached(runtime.metrics_eval(source, measurements, versions, *base))


@st.cache_data(show_spinner=False, max_entries=16)
def _agents_run(source: str, trace: str) -> dict:
    return _uncached(runtime.agents_run(source, trace))


def cached_norms_eval(source: str, trace: str, contract: str, versions: tuple = (),
                      base: tuple = (None, 0.0, False)) -> dict:
    """The verdicts, kept for the same design, trace and versions; a failure that may
    not recur (the server busy, a timeout) is not kept."""
    try:
        return _norms_eval(source, trace, contract, versions, base)
    except _NotCached as e:
        return e.result


def cached_metrics_eval(source: str, measurements: str, versions: tuple = (),
                        base: tuple = (None, 0.0, False)) -> dict:
    try:
        return _metrics_eval(source, measurements, versions, base)
    except _NotCached as e:
        return e.result


def cached_agents_run(source: str, trace: str) -> dict:
    try:
        return _agents_run(source, trace)
    except _NotCached as e:
        return e.result


def _open_example(name: str):
    ss.design_example = name
    _load_example()


def _example_buttons(names: list):
    """A button for each example that has what this view needs; it replaces the design."""
    examples = ds.list_examples()
    with st.container(horizontal=True):
        for name in names:
            if name in examples:
                st.button(t("Open the example {name}").format(name=f"*{name}*"),
                          key=f"design_open_example_{name}", on_click=_open_example, args=(name,),
                          icon=":material/folder_open:",
                          help=t("Replaces the design in the editor."))


def example_stem(source: str):
    """The file stem of the bundled example that the source is, or None."""
    return next((p.stem for p in ds.list_examples().values()
                 if p.read_text(encoding="utf-8") == source), None)


def example_traces(source: str, prefix: str, directory=None) -> dict:
    """{label: text} of the bundled traces of the example that the source is, or of
    every example whose file starts with `prefix` when the source is no example."""
    out = {}
    directory = directory or TRACES_DIR
    if not directory.is_dir():
        return out
    match = example_stem(source)
    for path in sorted(directory.glob("*.jsonl")):
        if match and not path.stem.startswith(match + "_"):
            continue
        if not match and not path.stem.startswith(prefix):
            continue
        out[path.stem] = path.read_text(encoding="utf-8")
    return out


def _use_text(key: str, text: str):
    ss[key] = text
    ss[f"{key}_ver"] = ss.get(f"{key}_ver", 0) + 1


def _keep_text(key: str, widget_key: str):
    """Keep the typed text in ss[key], which outlives the text area when the view changes."""
    ss[key] = ss[widget_key]


def trace_input(key: str, examples: dict, help_text: str, measurements: bool = False) -> str:
    """A trace (or measurements) from an example, a file or typed text; the text is kept
    in ss[key]. The text area has a versioned key, so that an example or a file replaces
    its text."""
    labels = ((t("Example measurements"), t("Use these measurements"),
               t("Or open a file of measurements (JSON Lines)"),
               t("Measurements (one JSON object per line)")) if measurements else
              (t("Example trace"), t("Use this trace"), t("Or open a trace file (JSON Lines)"),
               t("Trace (one JSON object per line)")))
    c1, c2 = st.columns([3, 2], vertical_alignment="bottom")
    if examples:
        choose = t("(choose one)")
        pick = c1.selectbox(labels[0], [""] + list(examples), key=f"{key}_example",
                            format_func=lambda s: s or choose)
        c2.button(labels[1], key=f"{key}_use", disabled=not pick, width="stretch",
                  on_click=_use_text, args=(key, examples.get(pick, "")))
    upload = st.file_uploader(labels[2], type=["jsonl", "json", "txt"],
                              key=f"{key}_file_{ss.get(f'{key}_ver', 0)}", help=help_text)
    if upload is not None:
        try:
            text = upload.getvalue().decode("utf-8-sig")
            runtime.read_trace(text)
        except (UnicodeDecodeError, runtime.TraceError) as e:
            st.error(f"{upload.name}: {e}")
        else:
            _use_text(key, text)
            st.rerun()
    widget_key = f"{key}_text_{ss.get(f'{key}_ver', 0)}"
    return st.text_area(labels[3], value=ss.get(key, ""), height=180,
                        key=widget_key, on_change=_keep_text, args=(key, widget_key),
                        placeholder=t('{"t": 0, "bindings": {"i": 1}, "values": {...}}'))


def _state_chart(norms: list, end: float):
    """The state of each norm over the time of the trace, as bars."""
    rows = []
    for n in norms:
        history = n.get("history") or []
        for k, h in enumerate(history):
            stop = history[k + 1]["t"] if k + 1 < len(history) else max(end, h["t"])
            rows.append({"norm": n["id"], "state": h["state"], "from": h["t"],
                         "to": stop if stop > h["t"] else h["t"] + max(end, 1) * 0.02,
                         "why": h.get("reason") or ""})
    if not rows:
        return
    import altair as alt
    states = sorted({r["state"] for r in rows})
    chart = alt.Chart(pd.DataFrame(rows)).mark_bar(height=16).encode(
        x=alt.X("from:Q", title="t (s)", scale=alt.Scale(zero=False)), x2="to:Q",
        y=alt.Y("norm:N", title=None, sort=[n["id"] for n in norms]),
        color=alt.Color("state:N", scale=alt.Scale(
            domain=states, range=[STATE_COLOR.get(s, "#7f7f7f") for s in states])),
        tooltip=["norm", "state", "from", "to", "why"])
    st.altair_chart(chart.properties(height=40 + 28 * len(norms), width="container"))


def _norms_check_answers(contract: str) -> dict:
    """{norm: {"violable": yes/no, ...}} from this session's norms-check of the contract."""
    from backend.services import design_jobs as jobs
    job = jobs.get(ss.get("design_job_norms-check"))
    report = ((job.result or {}).get("report") or {}) if job and not job.running else {}
    if report.get("contract") != contract or not report.get("checked"):
        return {}
    answers = {}
    for n in report.get("norms") or []:
        if n.get("status") != "model":
            continue
        answers[n["id"]] = {q: ("yes" if (n.get(q) or {}).get("answer") else "no")
                            for q in ("violable", "fulfillable") if isinstance(n.get(q), dict)}
    return answers


# The length of a period for the examples whose files do not name one in a
# comment (`--period D`); the measurements of the robot town name periods.
EXAMPLE_PERIODS = {"amendment_robot_town": "3600s"}


def _version_candidates(source: str) -> dict:
    """{id: (label, source)} of the other versions a run can have: the versions
    saved under Versions ("saved:NAME"), and the bundled versions of the example
    that the source is, its stem with another `_vN` ("example:STEM"). The ids do
    not depend on the language of the page."""
    out = {f"saved:{v['name']}": (v["name"], v["source"])
           for v in ss.get("design_versions", []) if v["source"] != source}
    stem = example_stem(source)
    found = re.fullmatch(r"(.+)_v\d+", stem or "")
    if found:
        for path in sorted(ds.DESIGNS_DIR.glob(f"{found.group(1)}_v*.cadl")):
            if path.stem != stem and re.fullmatch(r".+_v\d+", path.stem):
                out[f"example:{path.stem}"] = (t("example: {name}").format(name=path.stem),
                                               path.read_text(encoding="utf-8"))
    return out


def _default_period(source: str) -> str:
    found = re.search(r"--period\s+(\S+)", source)
    if found:
        return found.group(1)
    family = re.sub(r"_v\d+$", "", example_stem(source) or "")
    return EXAMPLE_PERIODS.get(family, "")


def run_versions(source: str, key: str, title: str = ""):
    """The other versions of a run and its time base (amendment 0.1, K.11), or
    ((), (None, 0.0, False)) when the design does not declare the extension or
    cadl is older than 0.3.34. The choices start again for another design."""
    none = ((), (None, 0.0, False))
    if "amendment" not in ds.extensions_in_effect(source) or not runtime.supports_run_versions():
        return none
    candidates = _version_candidates(source)
    design = hashlib.sha256(source.encode("utf-8")).hexdigest()
    if ss.get(f"{key}_design") != design:
        ss[f"{key}_design"] = design
        ss[f"{key}_versions"] = [k for k in candidates if k.startswith("example:")]
        ss[f"{key}_period"] = _default_period(source)
        ss[f"{key}_origin"] = 0.0
        ss[f"{key}_epoch"] = False
    else:                                   # a version saved or removed meanwhile
        ss[f"{key}_versions"] = [k for k in ss.get(f"{key}_versions", []) if k in candidates]
    with st.expander(title or t("Versions of the run (amendment 0.1)"), expanded=bool(candidates)):
        st.caption(t(
            "The versions form one sequence by `previous:`. The version in force at an "
            "observation comes from the effective times on a time base, from the key `version` "
            "of the observation, or both; when a version takes effect, an instance follows the "
            "transitional rule of its contract: `keep`, or `switch` (with `in_states:` only in "
            "those states) (Appendix K, K.11)."))
        if not candidates:
            st.caption(t("Save the other versions under Versions to apply them here."))
            return none
        ids = st.multiselect(t("Other versions of the run"), list(candidates),
                             key=f"{key}_versions", format_func=lambda k: candidates[k][0],
                             help=t("Passed as --version FILE each; the order does not matter."))
        c1, c2, c3 = st.columns(3, vertical_alignment="bottom")
        period = c1.text_input(t("Length of a period"), key=f"{key}_period",
                               placeholder="3600s",
                               help=t("--period: the length of period k of `effective: { period: "
                                      "k }`, such as 3600s or 1h."))
        origin = c2.number_input(t("Start of period 1 (s)"), min_value=0.0, step=1.0,
                                 format="%.0f", key=f"{key}_origin",
                                 help=t("--origin (default 0)."))
        epoch = c3.checkbox(t("Time stamps are Unix time"), key=f"{key}_epoch",
                            help=t("--epoch: for `effective: { at: ... }`."))
    versions = tuple(candidates[k] for k in ids if k in candidates)
    return versions, (period.strip() or None, float(origin or 0.0), bool(epoch))


def _versions_section(report: dict):
    """What the versions of the run did to the instance (`versions` of norms-eval)."""
    v = report.get("versions")
    if not v:
        return
    st.markdown(t("**Versions of the run** · sequence {sequence} · in force at the start: {start} "
                  "· followed at the end: {follows}").format(
        sequence=" → ".join(f"`{x}`" for x in v.get("sequence") or []),
        start=f"`{v.get('start')}`", follows=f"`{v.get('follows')}`"))
    events = v.get("events") or []
    if events:
        st.dataframe([{"t": e.get("t"), "version": e.get("version"), "event": e.get("event"),
                       "what happened": e.get("text")} for e in events],
                     hide_index=True, width="stretch", column_config={
                         k: st.column_config.Column(t(k))
                         for k in ("version", "event", "what happened")})
    else:
        st.caption(t("No version took effect during the run."))


def traces(c):
    source = c.source
    st.caption(t(
        "`cadl norms-eval` follows one contract instance through a recorded trace and gives "
        "the verdict of each norm: fulfilled, violated, excused or still pending. Where "
        "norms-check answers over every behaviour, this answers for one run: of a "
        "simulation, of the real system, or a witness of norms-check."))
    contracts = ds.norms_contracts(source) if "norms" in ds.extensions_in_effect(source) else []
    if not contracts:
        st.info(t("This design declares no norms (norms 0.1), so there is nothing to evaluate. "
                  "The examples *norms robot delivery* and *norms no double assignment* have "
                  "norms and traces."))
        _example_buttons(["norms robot delivery", "norms no double assignment"])
        return
    contract = st.selectbox(t("Contract"), contracts, key="design_trace_contract")
    text = trace_input("design_trace", example_traces(source, "norms_"),
                       t("The trace format of cadl (Appendix F, F.5): one observation per line, "
                         "with `t`, and `bindings`, `values`, `messages` and `state` as needed; "
                         "`regime` for a design with `regimes:` (A.7.1) and `version` with "
                         "amendment 0.1 (K.11); a run that names a regime cannot also have "
                         "versions."))
    if not text.strip():
        st.caption(t("Choose an example trace, open a file, or type the trace above."))
        return
    try:
        records = runtime.read_trace(text)
    except runtime.TraceError as e:
        st.error(str(e))
        return
    versions, base = run_versions(source, "design_trace")
    result = cached_norms_eval(source, text, contract, versions, base)
    if result.get("failure"):
        st.error(t("Not evaluated: {e}").format(e=result["failure"]))
        return
    report = result["report"]
    _record("norms-eval", source, text, report, (contract, versions, base))
    for note in result.get("notes") or []:
        st.warning(_plain(note))
    inst = report.get("instance") or {}
    st.markdown(
        f"**{report.get('contract')}** · " + t("bindings") + " "
        + (", ".join(f"`{k}={v}`" for k, v in (inst.get("bindings") or {}).items()) or "—")
        + f" · t = {inst.get('start')} … {inst.get('end')} · {inst.get('observations')} "
        + t("observations") + (f" · {inst.get('end_reason')}" if inst.get("end_reason") else ""))
    answers = _norms_check_answers(report.get("contract"))
    rows = []
    for n in report.get("norms") or []:
        row = {"norm": n["id"], "kind": n.get("kind"), "bearer": n.get("bearer"),
               "verdict": f"{VERDICT_ICON.get(n.get('verdict'), '')} {n.get('verdict')}",
               "activated at": n.get("activated_at"), "decided at": n.get("time"),
               "why": n.get("reason") or ""}
        if answers:
            a = answers.get(n["id"], {})
            row["norms-check: violable"] = a.get("violable", "—")
        rows.append(row)
    st.dataframe(rows, hide_index=True, width="stretch", column_config={
        k: st.column_config.Column(t(k)) for k in (
            "norm", "kind", "bearer", "verdict", "activated at", "decided at", "why",
            "norms-check: violable")})
    if answers:
        st.caption(t("The last column is the answer of this session's norms-check over every "
                     "behaviour: a norm violated here must be violable there."))
    _versions_section(report)
    _state_chart(report.get("norms") or [], float(inst.get("end") or 0))
    with st.expander(t("The trace as a sequence of messages")):
        st.markdown(sequence_svg(trace_protocol(records), dark=_is_dark()), unsafe_allow_html=True)
    for w in report.get("warnings") or []:
        st.warning(_plain(str(w)))
    st.download_button(t("Verdicts (JSON)"), json.dumps(report, indent=2, ensure_ascii=False),
                       file_name=f"{report.get('contract')}.norms-eval.json",
                       mime="application/json", key="design_trace_download")


def _record(check: str, source: str, trace: str, report: dict, options=()):
    """Record the run in Summary once for the same design, trace and options."""
    from views._design.verify import record_run
    sha = hashlib.sha256((source + "\0" + trace + "\0" + repr(options)).encode("utf-8")).hexdigest()
    if ss.get(f"design_recorded_{check}") == sha:
        return
    ss[f"design_recorded_{check}"] = sha
    if check == "metrics-eval":
        counts = report.get("counts") or {}
        word = "findings" if counts.get("not met") else "passed"
        summary = (f"{counts.get('met', 0)} met, {counts.get('not met', 0)} not met, "
                   f"{counts.get('not judged', 0)} not judged")
    elif check == "norms-eval":
        verdicts = [n.get("verdict") for n in report.get("norms") or []]
        violated = verdicts.count("violated")
        word = "findings" if violated else "passed"
        summary = f"{violated} violated, {verdicts.count('fulfilled')} fulfilled of {len(verdicts)} norms"
    else:
        s = report.get("summary") or {}
        word = "findings" if s.get("deny") or s.get("escalate") else "passed"
        summary = (f"{s.get('messages', 0)} messages: {s.get('allow', 0)} allowed, "
                   f"{s.get('deny', 0)} denied, {s.get('escalate', 0)} escalated")
    record_run(check, source, word, summary,
               trace_sha256=hashlib.sha256(trace.encode("utf-8")).hexdigest())


def _verify_audit(source: str, audit: str, key: str):
    result = runtime.audit_verify(source, audit)
    ss[key] = result


def agents(c):
    source = c.source
    st.caption(t(
        "`cadl agents-run` replays recorded messages through the enforcement point of the "
        "agent governance extension (agents 0.1): each message is allowed, denied or "
        "escalated to a person, holds are approved, rejected or time out, and every decision "
        "goes into an audit log with a hash chain, which `cadl audit-verify` checks. The "
        "gateways that do this in front of real agents (`cadl mcp-gateway`, "
        "`cadl a2a-gateway`) run on your own machine; this page does not start them."))
    if "agents" not in ds.extensions_in_effect(source):
        st.info(t("This design does not use the agent governance extension (agents 0.1). The "
                  "example *agents tool governance* does, with traces."))
        _example_buttons(["agents tool governance"])
        return
    text = trace_input("design_agents_trace", example_traces(source, "agents_"),
                       t("Recorded messages in the trace format of norms-eval, with `epoch` on "
                         "the first record (Appendix G)."))
    if not text.strip():
        st.caption(t("Choose an example trace, open a file, or type the messages above."))
        return
    try:
        records = runtime.read_trace(text)
    except runtime.TraceError as e:
        st.error(str(e))
        return
    result = cached_agents_run(source, text)
    if result.get("failure"):
        st.error(t("Not replayed: {e}").format(e=result["failure"]))
        return
    report = result["report"]
    _record("agents-run", source, text, report)
    s = report.get("summary") or {}
    holds = s.get("holds") or {}
    with st.container(horizontal=True):
        for label, value in ((t("messages"), s.get("messages", 0)), (t("allowed"), s.get("allow", 0)),
                             (t("denied"), s.get("deny", 0)), (t("escalated"), s.get("escalate", 0)),
                             (t("holds approved"), holds.get("approved", 0)),
                             (t("holds rejected"), holds.get("rejected", 0)),
                             (t("holds expired"), holds.get("expired", 0))):
            st.metric(label, value, border=True)
    steps = report.get("steps") or []
    st.dataframe([{
        "time": step.get("time"),
        "message": f"{step['message'].get('from')} → {step['message'].get('to')} : "
                   f"{step['message'].get('name')}",
        "decision": (step.get("result") or {}).get("decision"),
        "reason": (step.get("result") or {}).get("reason"),
        "norms": ", ".join((step.get("result") or {}).get("norms") or []) or "—",
        "hold": step.get("hold_id") or "—",
        "escalated to": (step.get("result") or {}).get("escalate_to") or "—",
    } for step in steps], hide_index=True, width="stretch", column_config={
        k: st.column_config.Column(t(k)) for k in (
            "time", "message", "decision", "reason", "norms", "hold", "escalated to")})
    decisions = [(step.get("result") or {}).get("decision") or "" for step in steps]
    with st.expander(t("The messages as a sequence, with the decisions"), expanded=len(steps) <= 12):
        st.markdown(sequence_svg(trace_protocol(records, decisions), dark=_is_dark()),
                    unsafe_allow_html=True)
    if report.get("holds"):
        st.markdown("**" + t("Holds") + "**")
        st.dataframe([{
            "hold": h.get("hold_id"), "state": h.get("state"),
            "message": f"{h['message'].get('from')} → {h['message'].get('to')} : {h['message'].get('name')}",
            "approver": h.get("approver"), "deadline": h.get("deadline"), "closed": h.get("closed"),
            "released": h.get("released"),
        } for h in report["holds"]], hide_index=True, width="stretch", column_config={
            k: st.column_config.Column(t(k)) for k in (
                "hold", "state", "message", "approver", "deadline", "closed", "released")})
    for w in report.get("warnings") or []:
        st.warning(_plain(str(w)))
    audit = result.get("audit") or ""
    st.markdown("**" + t("Audit log") + "**")
    with st.container(horizontal=True, vertical_alignment="center"):
        st.download_button(t("Audit log (JSON Lines)"), audit, file_name="audit.jsonl",
                           mime="application/jsonl", key="design_agents_audit_download")
        st.button(t("Verify its hash chain"), key="design_agents_verify", on_click=_verify_audit,
                  args=(source, audit, "design_agents_verified"))
    other = st.file_uploader(t("Or verify another audit log against this design"),
                             type=["jsonl"], key="design_agents_other_audit")
    if other is not None and ss.get("design_agents_other_id") != other.file_id \
            and other.size > runtime.MAX_AUDIT_BYTES:
        ss.design_agents_other_id = other.file_id
        ss.design_agents_verified = {"failure": t("The audit log is larger than {n} MB.").format(
            n=runtime.MAX_AUDIT_BYTES >> 20)}
    elif other is not None and ss.get("design_agents_other_id") != other.file_id:
        ss.design_agents_other_id = other.file_id
        _verify_audit(source, other.getvalue().decode("utf-8", errors="replace"),
                      "design_agents_verified")
    verified = ss.get("design_agents_verified")
    if verified:
        if verified.get("failure"):
            st.error(verified["failure"])
        elif verified.get("valid"):
            r = verified["report"]
            st.success(t("The audit log verifies: {n} records, written for this design, complete.")
                       .format(n=r.get("records")) if r.get("complete") else
                       t("The audit log verifies so far ({n} records); it has no end record.")
                       .format(n=r.get("records")))
        else:
            e = (verified.get("report") or {}).get("error") or {}
            st.error(t("The audit log does not verify") + f": {t('line')} {e.get('line')}: {e.get('what')}.")


def _number(value):
    """A number of a judgement as text: whole numbers without a decimal point, and
    a dash for none (the columns mix both)."""
    if value is None:
        return "—"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def measurements(c):
    """`cadl metrics-eval`: measured values of the metrics against their targets."""
    source = c.source
    st.caption(t(
        "`cadl metrics-eval` judges measured values of the metrics (one JSON object per line: "
        "`metric`, the start of its window as `t` or `period`, the value as `value`, "
        "`samples` or `runs`, and the values of the terms of the target as `values`). Each is "
        "judged under the declaration of the metric in the version in force at the start of "
        "its window, against the target in force then: met, not met, or not judged with the "
        "reason."))
    if not runtime.supports_run_versions():
        st.info(t("This needs cadl 0.3.34 or later."))
        return
    if not (c.ir or {}).get("metrics"):
        st.info(t("This design declares no metrics. The example *amendment robot town v1* has "
                  "metrics with targets and schedules, and measurements."))
        _example_buttons(["amendment robot town v1"])
        return
    text = trace_input("design_measure", example_traces(source, "amendment_", MEASUREMENTS_DIR),
                       t("Measurements in JSON Lines, as described above."), measurements=True)
    if not text.strip():
        st.caption(t("Choose an example, open a file, or type the measurements above."))
        return
    try:
        runtime.read_trace(text)
    except runtime.TraceError as e:
        st.error(str(e))
        return
    versions, base = run_versions(source, "design_measure",
                                  t("Versions of the model (amendment 0.1)"))
    result = cached_metrics_eval(source, text, versions, base)
    if result.get("failure"):
        st.error(t("Not run: {e}").format(e=result["failure"]))
        return
    report = result["report"]
    _record("metrics-eval", source, text, report, (versions, base))
    for note in result.get("notes") or []:
        st.warning(_plain(note))
    counts = report.get("counts") or {}
    with st.container(horizontal=True):
        for label in ("met", "not met", "not judged"):
            st.metric(t(label), counts.get(label, 0))
    st.caption(t("Versions: {versions}").format(
        versions=" → ".join(f"`{v}`" for v in report.get("versions") or []) or "—"))
    rows = [{"line": str(m.get("line") or "").replace("line ", ""), "metric": m.get("metric"), "start": _number(m.get("start")),
             "window": _number(m.get("window")), "version": m.get("version"), "value": _number(m.get("value")),
             "target": m.get("target"), "bound": _number(m.get("bound")),
             "target from": m.get("target_from") + (f" ({m['decision']})" if m.get("decision")
                                                    else "") if m.get("target_from") else "",
             "verdict": t(m.get("verdict") or ""), "why": m.get("reason") or ""}
            for m in report.get("measurements") or []]
    st.dataframe(rows, hide_index=True, width="stretch", column_config={
        k: st.column_config.Column(t(k)) for k in (
            "line", "metric", "start", "window", "version", "value", "target", "bound",
            "target from", "verdict", "why")})
    st.caption(t("bound: the value of the target with the terms given, which the value is "
                 "compared with exactly; start and window in seconds."))
    st.download_button(t("Judgements (JSON)"), json.dumps(report, indent=2, ensure_ascii=False),
                       file_name="metrics-eval.json", mime="application/json",
                       key="design_measure_download")
