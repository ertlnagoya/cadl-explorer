"""Violation Timeline — violations of contract instances against the faults injected.

The research plan's A1–A3 on one page:

* Timeline (A2): each contract instance's states as bands — the runtime's
  report, and under it continued evaluation — with the reported violations,
  the violations the report left out, and the injected faults as marks.
* Measurements (A1): over many trials, how often the record holds the fault,
  how often the report and the candidate cause name it, and how much is masked.
* Improvements (A3): changes of deadlines and monitor periods proposed from
  the candidate causes, run again, and compared with the ground truth.
* Run: the SoS-DSL reference runtime, unchanged, with injected faults and
  continued evaluation (when the runtime is on this machine).

Input is the NDJSON log of the runtime (`multi_robot_demo --log`) and the
simulator IR (`cadl sim-ir --format json`); see
cadl_sim/sos_dsl/violation_trace.py for the optional records.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from cadl_sim.sos_dsl import build_lifecycle_view, lifecycle_to_dot  # noqa: E402
from cadl_sim.sos_dsl import contract_tuning as tuning  # noqa: E402
from cadl_sim.sos_dsl import runtime_runner as runner  # noqa: E402
from cadl_sim.sos_dsl import violation_trace as vt  # noqa: E402
from views._i18n import t  # noqa: E402
from views import _violations as draw  # noqa: E402

EXAMPLES = _ROOT / "cadl_sim" / "sos_dsl" / "examples"
LOG_EXAMPLES = sorted((EXAMPLES / "violations").glob("*.ndjson"))
IR_EXAMPLES = sorted(EXAMPLES.glob("*.ir.json"))
DEFAULT_IR = "sos_dsl_robot_delivery.ir.json"
GUIDE_URL = ("https://github.com/ertlnagoya/cadl-explorer/blob/main/"
             "docs/violation-timeline-guide.ja.md")

_theme = getattr(st.context, "theme", None)
DARK = getattr(_theme, "type", None) == "dark"

st.session_state.setdefault("vt_library", {})    # name -> list of records
st.session_state.setdefault("vt_after", {})      # name of a log -> records run again
st.session_state.setdefault("vt_runtime_path", os.environ.get(runner.ENV_VAR, ""))

st.title(t("Violation Timeline"))
st.markdown(t(
    "The runtime reports one violation per contract instance: the first that ends it. "
    "This page sets that report beside **continued evaluation**, which goes on evaluating "
    "every deadline and monitor as if the violation had not happened, and beside the "
    "**faults that were injected**. Where the report leaves out a violation, it is "
    "*masked*; where the first violation is not the injected fault, the report names the "
    "wrong cause."))
st.caption(t(
    "Input: the NDJSON log of the SoS-DSL runtime (`multi_robot_demo --log`) and the "
    "simulator IR (`cadl sim-ir --format json`) that the **Contract Lifecycle** page reads. "
    "A log with the runtime's report only is shown as it is; continued evaluation and "
    "injected faults are optional records."))
st.caption(t("How to carry out A1–A3 of the research plan with this page: [the guide]({url}) "
             "(in Japanese).").format(url=GUIDE_URL))


# ---------------------------------------------------------------------------
# Inputs: the IR and the log
# ---------------------------------------------------------------------------

@st.cache_data(max_entries=32)
def _read_records(text: str):
    return vt.parse_ndjson(text)


def _designer_ir():
    src = st.session_state.get("design_src")
    if not src:
        return None
    try:
        from views._design.cache import cached_analyze
        return cached_analyze(src).ir
    except Exception:
        return None


with st.sidebar:
    st.header(t("Simulator IR"))
    ir_labels = {"bundled": t("Bundled example"), "upload": t("Upload"),
                 "designer": t("The Designer's design")}
    designer_ir = _designer_ir()
    ir_sources = ["bundled", "upload"] + (["designer"] if designer_ir is not None else [])
    if st.session_state.get("vt_ir_source") not in ir_sources:
        st.session_state.vt_ir_source = "bundled"
    ir_source = st.segmented_control(t("IR from"), ir_sources, key="vt_ir_source",
                                     format_func=ir_labels.get) or "bundled"
    ir_doc = None
    ir_name = ""
    if ir_source == "upload":
        up = st.file_uploader(t("Sim-IR JSON"), type=["json"], key="vt_ir_upload")
        if up is not None:
            try:
                ir_doc, ir_name = json.loads(up.getvalue().decode("utf-8")), up.name
            except json.JSONDecodeError as e:
                st.error(t("Failed to parse JSON: {e}").format(e=e))
    elif ir_source == "designer":
        ir_doc, ir_name = designer_ir, t("the Designer's design")
    if ir_doc is None:
        names = [p.name for p in IR_EXAMPLES]
        pick = st.selectbox(t("Bundled IR"), names, key="vt_ir_pick",
                            index=names.index(DEFAULT_IR) if DEFAULT_IR in names else 0)
        ir_doc, ir_name = json.loads((EXAMPLES / pick).read_text(encoding="utf-8")), pick

    st.header(t("Log"))
    uploads = st.file_uploader(
        t("NDJSON logs"), type=["ndjson", "jsonl", "json", "log", "txt"],
        accept_multiple_files=True, key="vt_log_upload",
        help=t("Each file is one trial, unless its records carry a `trial` field."))
    truth_up = st.file_uploader(
        t("Ground truth (optional)"), type=["ndjson", "jsonl", "json"], key="vt_truth_upload",
        help=t("Injected faults as NDJSON: `instance_id`, `t_ms`, `fault`, and optionally "
               "`expected` (the `detected_by` that names the fault) and `trial`."))
    for f in uploads or []:
        st.session_state.vt_library[t("upload: {name}").format(name=f.name)] = \
            _read_records(f.getvalue().decode("utf-8"))[0]
    options = [p.name for p in LOG_EXAMPLES] + list(st.session_state.vt_library)
    if "vt_log_next" in st.session_state:      # a run of the Run tab shows its own log
        st.session_state.vt_log = st.session_state.pop("vt_log_next")
    if st.session_state.get("vt_log") not in options:
        st.session_state.vt_log = ("demo_accept_300ms.ndjson"
                                   if "demo_accept_300ms.ndjson" in options else options[0])
    log_name = st.selectbox(t("Log shown"), options, key="vt_log")
    rule = st.segmented_control(
        t("Candidate cause"), vt.CANDIDATE_RULES, default=vt.CANDIDATE_RULES[0],
        key="vt_rule",
        format_func=lambda r: t("earliest onset") if r == "earliest_onset" else t("earliest record"),
        help=t("Earliest onset: the violation whose condition may have begun first; a periodic "
               "monitor's condition may have begun up to one period before it was recorded. "
               "Earliest record: the first violation in the log. Neither uses the ground "
               "truth.")) or vt.CANDIDATE_RULES[0]

if log_name in st.session_state.vt_library:
    records = st.session_state.vt_library[log_name]
    problems = []
else:
    records, problems = _read_records((EXAMPLES / "violations" / log_name).read_text(encoding="utf-8"))
if problems:
    st.warning(t("Lines that were not read: {p}").format(p="; ".join(problems[:5])))

truth = []
if truth_up is not None:
    truth, _ = vt.parse_ndjson(truth_up.getvalue().decode("utf-8"))
trials = vt.split_trials(records, Path(log_name).stem)
if truth:
    trials = [vt.merge_truth(tr, truth) for tr in trials]


@st.cache_data(max_entries=16)
def _analyze(_trials, key: str, ir_json: str, rule: str):
    ir = json.loads(ir_json)
    return [vt.analyze_trial(tr, ir, rule) for tr in _trials]


IR_JSON = json.dumps(ir_doc, sort_keys=True)
views_ = _analyze(trials, f"{log_name}|{len(records)}|{len(truth)}", IR_JSON, rule)
if not views_:
    st.info(t("The log has no records."))
    st.stop()

n_masked = sum(1 for v in views_ for s in v.instances if s.masked)
n_wrong = sum(1 for v in views_ for s in v.instances if s.reported_matches is False)
has_cont = any(v.trial.has_continued for v in views_)
has_truth = any(s.expected for v in views_ for s in v.instances)

with st.container(horizontal=True):
    st.metric(t("Trials"), len(views_), border=True)
    st.metric(t("Instances"), sum(len(v.instances) for v in views_), border=True)
    st.metric(t("Masked instances"), n_masked if has_cont else "—", border=True,
              help=t("Instances with a violation that continued evaluation recorded and "
                     "the report left out."))
    st.metric(t("Report names a wrong cause"), n_wrong if has_truth else "—", border=True,
              help=t("Instances with an injected fault whose reported violation does not "
                     "name it."))

if not has_cont:
    st.info(t("This log holds the runtime's report only. Without continued evaluation "
              "(records with `\"track\": \"continued\"`) the page cannot show what the "
              "report left out. Run the runtime here (**Run**), or add continued evaluation "
              "to the runtime's log."))

tab_tl, tab_a1, tab_a3, tab_run = st.tabs([
    t("Timeline (A2)"), t("Measurements (A1)"), t("Improvements (A3)"), t("Run")])


def _trial_label(v: vt.TrialView) -> str:
    flags = []
    if any(s.masked for s in v.instances):
        flags.append(t("masking"))
    if any(s.reported_matches is False for s in v.instances):
        flags.append(t("wrong cause"))
    cond = vt.condition_label(v.trial.condition) if v.trial.condition else ""
    return v.trial.trial_id + (f" — {cond}" if cond else "") + (f"  [{', '.join(flags)}]" if flags else "")


def show_timeline(v: vt.TrialView, key: str, instances=None, t_range=None) -> None:
    ids = instances or [s.instance_id for s in v.instances]
    chart = draw.timeline_chart(v, ids, t_range, DARK)
    st.altair_chart(chart, width="stretch")


# ---------------------------------------------------------------------------
# Timeline (A2)
# ---------------------------------------------------------------------------

with tab_tl:
    by_id = {v.trial.trial_id: v for v in views_}
    tids = list(by_id)
    if len(tids) > 1:
        tid = st.selectbox(t("Trial"), tids, key="vt_trial",
                           format_func=lambda x: _trial_label(by_id[x]))
    else:
        tid = tids[0]
    view = by_id[tid]
    run = view.trial.run or {}
    if run:
        bits = [f"{t('scenario')}: `{run.get('scenario')}`"]
        if run.get("ir_overrides"):
            bits.append(t("IR changed") + ": " + "; ".join(
                f"{o['id']}.{o['field']} = {o['value']}" for o in run["ir_overrides"]))
        st.caption(" · ".join(bits))
    all_ids = [s.instance_id for s in view.instances]
    c1, c2 = st.columns([3, 2])
    with c1:
        picked = st.multiselect(t("Instances"), all_ids, default=all_ids, key=f"vt_inst_{tid}")
    with c2:
        end = max(view.trial.end_ms, 1)
        t_range = st.slider(t("Time (ms)"), 0, end, (0, end), step=50, key=f"vt_range_{tid}")
    if picked:
        show_timeline(view, "tl", picked, t_range)
    st.caption(t(
        "Upper band of an instance: the states the runtime reported. Lower band: continued "
        "evaluation, in which the instance did not enter its violation state. A dashed line "
        "before a mark is the time in which a periodic monitor's condition may have begun: "
        "the monitor sees it only when it is evaluated, up to one period later."))

    st.subheader(t("Instances"))
    st.dataframe(pd.DataFrame(draw.instance_rows(view)), hide_index=True, width="stretch")
    st.caption(t("The candidate cause is chosen from the log alone ({rule}); the injected "
                 "faults are used only to judge it.").format(
        rule=t("earliest onset") if rule == "earliest_onset" else t("earliest record")))
    for s in view.instances:
        for r in s.reasons:
            st.markdown(f"- `{s.instance_id}` — {t(vt.REASONS.get(r, r))}")

    st.subheader(t("In the lifecycle"))
    inst = st.selectbox(t("Instance"), all_ids, key=f"vt_life_{tid}",
                        index=next((i for i, s in enumerate(view.instances) if s.masked), 0))
    s = next(x for x in view.instances if x.instance_id == inst)
    contract = vt.find_contract(view.ir, s.contract_id)
    lv = build_lifecycle_view(contract) if contract else None
    if lv is None:
        st.caption(t("The IR has no lifecycle for `{c}`.").format(c=s.contract_id))
    else:
        for track in ["reported", vt.CONTINUED][:2 if s.has_continued else 1]:
            ivs = [iv for iv in view.intervals.get(inst, []) if iv.track == track]
            states = [iv.state for iv in ivs]
            with st.container():
                st.markdown("**" + (t("Report") if track == "reported"
                                    else t("Continued evaluation")) + "**")
                st.graphviz_chart(lifecycle_to_dot(
                    lv, dark=DARK, current=states[-1] if states else None, visited=states,
                    flagged=[x for x in states if x == "Violated"]), width="stretch")
        st.caption(t("The same diagram as on the Contract Lifecycle page; the states the "
                     "instance went through are outlined, its last state in orange."))


# ---------------------------------------------------------------------------
# Measurements (A1)
# ---------------------------------------------------------------------------

with tab_a1:
    if not has_truth:
        st.info(t("These measures need injected faults (ground truth) in the log or as a "
                  "separate file. Try the bundled `batch_example.ndjson`, or a batch under **Run**."))
    else:
        rows = vt.measure(views_)
        st.markdown(t(
            "- **recall**: the record (continued evaluation) holds a violation that names "
            "the injected fault\n"
            "- **reported match**: the runtime's report names it\n"
            "- **candidate match**: the candidate cause names it\n"
            "- **masking**: of all violations of continued evaluation, the share the report "
            "left out\n\n"
            "The unit of the first three is an instance with an injected fault; the interval "
            "is the 95% Wilson score interval."))
        table = pd.DataFrame(rows)

        def _cell(r, m):
            if r[m] is None:
                return f"— ({r[f'{m} k/n']})"
            return f"{r[m]:.0%} {r[f'{m} 95% CI']} ({r[f'{m} k/n']})"

        st.dataframe(pd.DataFrame([
            {t("condition"): r["condition"], t("trials"): r["trials"],
             **{t(m): _cell(r, m) for m in draw.MEASURES}} for r in rows]),
            hide_index=True, width="stretch",
            column_config={t("condition"): st.column_config.TextColumn(width="large")})
        try:
            st.altair_chart(draw.measures_chart(rows, DARK), width="stretch")
        except Exception as e:        # a chart is a view of the table above
            st.caption(t("The chart could not be drawn: {e}").format(e=e))
        st.subheader(t("Why the report missed the fault"))
        rc = vt.reason_counts(views_)
        if rc:
            st.dataframe(pd.DataFrame([dict(r, explanation=t(r["explanation"])) for r in rc]),
                         hide_index=True, width="stretch")
        else:
            st.caption(t("In every trial the report named the injected fault."))
        units = pd.DataFrame(vt.unit_rows(views_))
        with st.expander(t("Every injected instance ({n})").format(n=len(units))):
            st.dataframe(units, hide_index=True, width="stretch")
        st.download_button(t("Download the table (CSV)"), table.to_csv(index=False),
                           file_name="a1_measures.csv", mime="text/csv")


# ---------------------------------------------------------------------------
# The runtime
# ---------------------------------------------------------------------------

def _runtime():
    path = st.session_state.get("vt_runtime_path") or ""
    if not path:
        return None, ""
    try:
        return runner.load_runtime(path), ""
    except Exception as e:
        return None, str(e)


handle, runtime_error = _runtime()


# ---------------------------------------------------------------------------
# Improvements (A3)
# ---------------------------------------------------------------------------

with tab_a3:
    proposals = tuning.propose(views_, ir_doc)
    changes = [p for p in proposals if p.override]
    assumptions = [p for p in proposals if not p.override]
    with st.expander(t("Rules")):
        for k, text in tuning.RULES.items():
            st.markdown(f"- **{k}** — {t(text)}")
    if not proposals:
        st.info(t("No rule applies to this log."))
    if changes:
        st.subheader(t("Changes of the contract"))
        df = pd.DataFrame([{"apply": True,
                            "rule": p.rule, "change": p.change,
                            "instances": len(p.instances), "why (first case)": p.reason}
                           for p in changes])
        edited = st.data_editor(
            df, hide_index=True, width="stretch", key=f"vt_props_{log_name}",
            disabled=["rule", "change", "instances", "why (first case)"],
            column_config={"apply": st.column_config.CheckboxColumn(t("apply")),
                           "rule": t("rule"), "change": t("change"),
                           "instances": t("instances"), "why (first case)": t("why (first case)")})
        chosen = [p for p, on in zip(changes, edited["apply"]) if on]
        patched = vt.apply_overrides(ir_doc, [p.override for p in chosen])
        st.download_button(t("Download the IR with the chosen changes"),
                           json.dumps(patched, indent=2, ensure_ascii=False),
                           file_name=Path(ir_name or "design.ir.json").name.replace(
                               ".ir.json", "") + ".proposed.ir.json",
                           mime="application/json", disabled=not chosen)
        st.caption(t("The changes are written as they would read in the CADL source; the "
                     "downloaded IR is the base IR with them. When a run is repeated, its own "
                     "conditions (`run.ir_overrides`) stay and the chosen changes are added."))
    if assumptions:
        st.subheader(t("Assumptions the contract could ask of the robots"))
        st.caption(t("The designer of the SoS cannot change a robot. These are not applied; "
                     "they state, as an `assume` of the contract, what the robot is asked to keep."))
        st.dataframe(pd.DataFrame([{t("assume"): p.assumption, t("instances"): len(p.instances),
                                    t("why (first case)"): p.reason} for p in assumptions]),
                     hide_index=True, width="stretch")

    if changes:
        st.subheader(t("Run again and compare"))
        runs = [v.trial.run for v in views_ if v.trial.run]
        after_key = f"{log_name}|{rule}|" + json.dumps([p.override for p in chosen], sort_keys=True)
        can_rerun = handle is not None and len(runs) == len(views_)
        if can_rerun:
            if st.button(t("Run the same trials with the chosen changes"), type="primary",
                         disabled=not chosen, key="vt_rerun"):
                bar = st.progress(0.0)
                after_recs = []
                for i, r in enumerate(runs):
                    after_recs += runner.rerun(handle, ir_doc, r, [p.override for p in chosen])
                    bar.progress((i + 1) / len(runs))
                bar.empty()
                st.session_state.vt_after[after_key] = after_recs
        else:
            st.caption(t("To run the trials again here, the runtime must be found (**Run**) "
                         "and the log must hold its `run` records. Otherwise run them yourself "
                         "with the downloaded IR and upload the log below."))
            after_up = st.file_uploader(t("Log after the changes"),
                                        type=["ndjson", "jsonl", "json"], key="vt_after_upload")
            if after_up is not None:
                st.session_state.vt_after[after_key] = \
                    _read_records(after_up.getvalue().decode("utf-8"))[0]
        after_recs = st.session_state.vt_after.get(after_key)
        if after_recs:
            a_trials = vt.split_trials(after_recs, Path(log_name).stem)
            if truth:
                a_trials = [vt.merge_truth(tr, truth) for tr in a_trials]
            after_views = [vt.analyze_trial(tr, ir_doc, rule) for tr in a_trials]
            rows_cmp, summary = tuning.compare(views_, after_views, chosen)
            with st.container(horizontal=True):
                for name, k in (("resolution", "resolution k/n"), ("side effect", "side effect k/n"),
                                ("coverage", "coverage k/n")):
                    val = summary[name]
                    st.metric(t(name), "—" if val is None else f"{val:.0%}", summary[k],
                              delta_color="off", border=True)
            st.caption(t("Resolution: of the targeted instances whose report named a wrong "
                         "cause, the share whose report names the injected fault after the "
                         "change. Side effect: of the instances without a fault, the share whose "
                         "final state changed. Coverage: of the trials with masking or a wrong "
                         "cause, the share that got a proposal. Fewer violations alone is not "
                         "counted as success."))
            if summary["without a proposal"]:
                st.caption(t("Trials without a proposal: {ids}").format(
                    ids=", ".join(summary["without a proposal"][:20])))
            st.dataframe(pd.DataFrame(rows_cmp), hide_index=True, width="stretch")
            a_by = {v.trial.trial_id: v for v in after_views}
            cmp_ids = [v.trial.trial_id for v in views_ if v.trial.trial_id in a_by]
            if cmp_ids:
                cid = st.selectbox(t("Trial to compare"), cmp_ids, key="vt_cmp_trial",
                                   format_func=lambda x: _trial_label(by_id[x]))
                st.markdown("**" + t("Before") + "**")
                show_timeline(by_id[cid], "before")
                st.markdown("**" + t("After") + "**")
                show_timeline(a_by[cid], "after")


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

with tab_run:
    st.markdown(t(
        "Runs the SoS-DSL reference runtime (`cadl/runtime/engine.py` of "
        "raspimouse-swarm-simulator) **as it is**: the page neither changes nor copies it. "
        "Beside it, a subclass that does not take violation transitions records continued "
        "evaluation, and faults are injected between the scenario and the two runtimes."))
    st.text_input(t("Path of the runtime"), key="vt_runtime_path",
                  placeholder="~/program/raspimouse-swarm-simulator",
                  help=t("The repository, or its `cadl/runtime` folder. The environment "
                         "variable {v} sets the default.").format(v=runner.ENV_VAR))
    if handle is None:
        if runtime_error:
            st.warning(runtime_error)
        st.info(t("No runtime here. The other tabs read logs; the bundled ones were made "
                  "with this tab."))
    else:
        st.success(t("Runtime: `{p}`").format(p=handle.label))
        one, many = st.tabs([t("One run"), t("Batch (A1)")])
        tr_ids = [tr.get("id") for tr in (((vt.find_contract(ir_doc, "DELIVERY_SLA") or {})
                                           .get("lifecycle") or {}).get("transitions") or [])]
        mon_ids = [m.get("id") for m in (vt.find_contract(ir_doc, "DELIVERY_SLA") or {})
                   .get("monitors") or [] if m.get("sampling_kind") == "periodic"]
        with one:
            with st.form("vt_one"):
                scenario = st.segmented_control(
                    t("Scenario"), ["demo", "baseline"], default="demo", key="vt_scenario",
                    format_func=lambda s: t("multi_robot_demo (its own faults)") if s == "demo"
                    else t("baseline (no faults; inject below)"))
                c1, c2, c3 = st.columns(3)
                acc = c1.number_input(t("accept deadline (ms)"), 50, 60000, 5000, 50, key="vt_acc")
                per = c2.number_input(t("battery_guard period (ms)"), 50, 10000, 500, 50,
                                      key="vt_per")
                dwell = c3.number_input(t("dwell in Assigned (ms, baseline)"), 0, 10000, 700, 50,
                                        key="vt_dwell")
                inj_df = st.data_editor(
                    pd.DataFrame({"instance_id": pd.Series([], dtype="str"),
                                  "t_ms": pd.Series([], dtype="int"),
                                  "fault": pd.Series([], dtype="str"),
                                  "value": pd.Series([], dtype="float")}),
                    num_rows="dynamic", key="vt_inj", width="stretch",
                    column_config={"fault": st.column_config.SelectboxColumn(
                        options=list(runner.FAULTS)),
                        "value": st.column_config.NumberColumn(
                            help=t("battery level, speed, or delay (ms)"))})
                go = st.form_submit_button(t("Run"), type="primary")
            if go:
                ov = []
                if "accept" in tr_ids:
                    ov.append({"contract": "DELIVERY_SLA", "kind": "transition", "id": "accept",
                               "field": "deadline_ms", "value": int(acc)})
                if "battery_guard" in mon_ids:
                    ov.append({"contract": "DELIVERY_SLA", "kind": "monitor",
                               "id": "battery_guard", "field": "sampling_period_ms",
                               "value": int(per)})
                injs = []
                for row in inj_df.dropna(subset=["instance_id", "fault"]).to_dict("records"):
                    key = {"battery_drop": "level", "over_speed": "speed",
                           "ack_delay": "delay_ms"}.get(row["fault"])
                    params = {"transition": "accept"} if row["fault"].startswith("ack") else {}
                    if key and pd.notna(row.get("value")):
                        params[key] = int(row["value"]) if key == "delay_ms" else float(row["value"])
                    injs.append(runner.Injection(str(row["instance_id"]), int(row["t_ms"] or 0),
                                                 row["fault"], params))
                name = f"run {len(st.session_state.vt_library) + 1}: {scenario}, accept {acc}ms"
                try:
                    recs = runner.run_trial(
                        handle, ir_doc, scenario=scenario or "demo",
                        params=runner.BaselineParams(dwell_ms=int(dwell)), injections=injs,
                        ir_overrides=ov, trial=name.split(":")[0].replace(" ", "-"),
                        ir_name=ir_name)
                except Exception as e:
                    st.error(t("The run failed: {e}").format(e=e))
                else:
                    st.session_state.vt_library[name] = recs
                    st.session_state.vt_log_next = name
                    st.rerun()
        with many:
            with st.form("vt_batch"):
                st.caption(t("Every combination of the values below, every fault, n trials "
                             "each, on the baseline scenario. The faulty robot and the time "
                             "are drawn at random from the seed."))
                c1, c2, c3 = st.columns(3)
                accs = c1.text_input(t("accept deadlines (ms)"), "500, 5000", key="vt_b_acc")
                pers = c2.text_input(t("battery_guard periods (ms)"), "500", key="vt_b_per")
                dwells = c3.text_input(t("dwells in Assigned (ms)"), "100, 700", key="vt_b_dwell")
                faults = st.multiselect(t("Faults"), list(runner.FAULTS),
                                        default=["battery_drop", "ack_missing"], key="vt_b_faults")
                c4, c5 = st.columns(2)
                n = c4.number_input(t("Trials per combination and fault"), 1, 200, 10,
                                    key="vt_b_n")
                seed = c5.number_input(t("Seed"), 0, 10 ** 6, 1, key="vt_b_seed")
                go_b = st.form_submit_button(t("Run the batch"), type="primary")
            if go_b:
                def _ints(text):
                    return [int(x) for x in text.replace(";", ",").split(",") if x.strip()]
                try:
                    sweep = {}
                    if _ints(accs):
                        sweep["deadline:accept"] = _ints(accs)
                    if _ints(pers):
                        sweep["period:battery_guard"] = _ints(pers)
                    if _ints(dwells):
                        sweep["dwell_ms"] = _ints(dwells)
                except ValueError:
                    st.error(t("Write the values as numbers separated by commas."))
                else:
                    bar = st.progress(0.0)
                    recs = runner.batch(handle, ir_doc, sweep=sweep, faults=faults, n=int(n),
                                        seed=int(seed), ir_name=ir_name,
                                        progress=lambda f: bar.progress(min(1.0, f)))
                    name = f"batch {len(st.session_state.vt_library) + 1}: n={n}, seed={seed}"
                    st.session_state.vt_library[name] = recs
                    st.session_state.vt_log_next = name
                    st.rerun()
        if log_name in st.session_state.vt_library:
            st.download_button(t("Download the log shown (NDJSON)"),
                               vt.to_ndjson(st.session_state.vt_library[log_name]),
                               file_name=log_name.split(":")[0].replace(" ", "_") + ".ndjson",
                               mime="application/x-ndjson")
