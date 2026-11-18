"""Checks of the design and the commands of cadl that check its extensions."""

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
from backend.services import prism_setup
from backend.services import design_workspace as workspace
from backend.services.cadl_service import MAX_SOURCE_CHARS
from cadl_sim.sos_dsl import build_lifecycle_view, lifecycle_to_dot, monitors_summary
from views._design.common import *  # noqa: F401,F403
from views._design.common import _plain, _problem_buttons
from views._design.cache import *  # noqa: F401,F403
from backend.services import design_jobs as jobs
from views._i18n import t

MAX_RUNS = 50


def _extension_checks(source: str, extensions: frozenset):
    """The design-time checks that cadl offers for the extensions in effect:
    `cadl norms-indices` (grounding 0.1), `cadl game-check` (games 0.1) and
    `cadl norms-check` (norms 0.1)."""
    st.markdown(t("**Checks of the extensions**"))
    st.caption(t("Commands of cadl that check the norms and the strategic model of the design "
               "before it runs. They are separate from the checks above."))
    if "grounding" in extensions:
        with st.expander(t("Derived indices (grounding 0.1): `cadl norms-indices`")):
            st.caption(t("α̂, β̂ and χ of each contract, derived from its norms and compared with the "
                       "declared α and β (Appendix J, J.4); for two or more contracts also the "
                       "indices of the SoS. The comparisons are the warnings J-6 to J-8 under "
                       "Type check."))
            try:
                indices = cached_norms_indices(source)
            except Exception as e:
                st.error(t("Not computed: {e}").format(e=e))
            else:
                _record_once("norms-indices", source, "computed",
                             f"{len((indices.get('report') or {}).get('contracts') or [])} contract(s)")
                st.code(indices["text"], language="text")
    if "games" in extensions:
        with st.expander(t("Equilibria (games 0.1): `cadl game-check`"), expanded=True):
            st.caption(t("Whether each named profile is an equilibrium at the fixed values of the "
                         "parameters, with exact arithmetic (Appendix I, I.8), and, if you ask, the "
                         "equilibrium maps (entries with `over:`) as certified cells (I.9; "
                         "`--certify`). The run is stopped after {s} s.").format(
                             s=f"{ds.GAME_CHECK_TIMEOUT:g}"))
            g1, g2 = st.columns([3, 2], vertical_alignment="center")
            certify = g1.checkbox(t("Also compute the equilibrium maps (--certify)"),
                                  key="design_game_certify")
            options = (source, certify)
            if g2.button(t("Run game-check"), key="design_game_run"):
                start_job("game-check", options)
            show_job("game-check", options, _game_report)
    if "norms" in extensions:
        _norms_check_section(source)


def _norms_check_section(source: str):
    contracts = ds.norms_contracts(source)
    with st.expander(t("Norms over all behaviours (norms 0.1): `cadl norms-check`")):
        st.caption(t(
            "The norms of a contract are translated into a model for PRISM, which "
            "answers over every behaviour up to the horizon whether each norm can be activated, "
            "violated and fulfilled, whether every activation can be kept, which obligations and "
            "prohibitions are in conflict, and whether powers and reparations are used. Every "
            "answer \"possible\" comes with a witness trace that the norms engine confirms "
            "(Appendix F, F.12). Linear arithmetic, `sum` and `avg` are checked exactly; "
            "for arithmetic that is not linear and has no domain, cadl assumes one from the "
            "literals, and the answers that hold only under it are marked with `*`. Up to "
            "four instances of "
            "the contract are checked together, also with amendments to later versions. The "
            "timeout bounds building the model and the runs of PRISM; the command is stopped "
            "{s} s after it.").format(s=f"{ds.BUILD_BUDGET:g}"))
        if not contracts:
            st.caption(t("No contract has norms."))
            return
        if not ds.prism_available():
            if prism_setup.status()["status"] == "installing":
                st.info(t("PRISM is being installed where this app runs; this takes a minute or "
                        "two. Until then `cadl norms-check` checks nothing here."))
            else:
                st.info(t("PRISM is not installed where this app runs, so `cadl norms-check` "
                        "checks nothing here; it still writes the model, which you can "
                        "download and check with PRISM on your machine."))
                if prism_setup.status()["error"]:
                    st.caption(t("PRISM could not be installed here: {e}").format(
                        e=prism_setup.status()["error"]))
        c1, c2, c3 = st.columns(3)
        contract = c1.selectbox(t("Contract"), contracts, key="design_nc_contract")
        bindings = c2.text_input(t("Bindings (i=n, ...)"), key="design_nc_bindings",
                                 help=t("Index values of the instance (--bindings); by default the "
                                      "lower bound of each set."))
        members = c3.text_input(t("Members (SET=n, ...)"), key="design_nc_members",
                                help=t("Number of members of a set in the instance (--members)."))
        c4, c5, c6 = st.columns(3)
        step = c4.text_input(t("Step"), key="design_nc_step", placeholder=t("from the durations"),
                             help=t("Time between two observations, such as 1s (--step)."))
        horizon = c5.number_input(t("Horizon (0: default)"), min_value=0, max_value=1000, step=1,
                                  key="design_nc_horizon",
                                  help=t("Last observation of an instance (--horizon)."))
        timeout = c6.number_input(t("Timeout (s)"), min_value=1,
                                  max_value=int(ds.MAX_NORMS_CHECK_TIMEOUT),
                                  value=int(ds.NORMS_CHECK_TIMEOUT), step=10,
                                  key="design_nc_timeout",
                                  help=t("Time for all runs of PRISM together (--timeout)."))
        c7, c8, c9 = st.columns(3)
        domains = c7.text_input(t("Domains (Q=lo..hi, ...)"), key="design_nc_domains",
                                placeholder=t("ROBOT[*].load=0..3"),
                                help=t("Integer domain of a quantity used in arithmetic, sum or avg "
                                     "(--domain); the answers then hold for traces whose numbers "
                                     "for it are such integers. Not needed for linear arithmetic; "
                                     "for nonlinear arithmetic it replaces the domain that cadl "
                                     "assumes from the literals."))
        instances = c8.text_input(t("Instances (N, or i=1; i=2)"), key="design_nc_instances",
                                  help=t("Several instances checked together, at most 4: a number "
                                       "(--instances N) or the bindings of each instance "
                                       "separated by `;` (--instance)."))
        starts = c9.selectbox(t("Starts of the instances"), ["any", "together"],
                              key="design_nc_starts",
                              help=t("With several instances (--starts): each starts at an "
                                   "observation the environment chooses (any), or all start "
                                   "at the first observation (together, fewer commands)."))
        max_states = 0
        if ds.supports_max_states():
            c10, _, _ = st.columns(3)
            max_states = c10.number_input(
                t("Largest number of states (0: default)"), min_value=0,
                max_value=ds.MAX_STATES_LIMIT, step=10_000, key="design_nc_max_states",
                help=t("The limit on the states kept at one step when cadl eliminates compared "
                       "quantities, and on the points of the faces of their linear arrangement "
                       "(--max-states; default {n}). Raise it when the report says the limit was "
                       "reached.").format(n=f"{ds.MAX_STATES_DEFAULT:,}"))
        from views._design.trace import _version_candidates, version_label
        candidates = _version_candidates(source)
        if ss.get("design_nc_versions") and any(k not in candidates
                                                for k in ss.design_nc_versions):
            ss.design_nc_versions = [k for k in ss.design_nc_versions if k in candidates]
        if candidates:
            ids = st.multiselect(
                t("Amend to these versions, in this order"), list(candidates),
                key="design_nc_versions", format_func=lambda k: version_label(k, candidates),
                help=t("Later versions of the design (--version): the next one may take effect "
                       "at any observation. With amendment 0.1 each instance then follows the "
                       "transitional rule of its contract, also `keep` and `switch` with "
                       "`in_states:` (K.11, cadl 0.3.36); without it the contract is amended "
                       "(F.6). Give the versions in the order of `previous:`."))
            versions = tuple(candidates[k] for k in ids)
        else:
            versions = ()
            st.caption(t("Amendments (`--version`) are checked against the versions saved under "
                       "Versions, or the other `_vN` file of an example; there is none here other "
                       "than the current design, so none is checked."))
        count = instances.strip()
        instance_option = (int(count) if count.isdigit() else
                           tuple(x.strip() for x in count.split(";") if x.strip()) or None)
        options = (source, contract, tuple(ds.split_list(bindings)), tuple(ds.split_list(members)),
                   step.strip(), int(horizon), float(timeout), tuple(ds.split_list(domains)),
                   instance_option, versions,
                   starts if ds.several_instances(instance_option) else "any", int(max_states))
        if st.button(t("Run norms-check"), key="design_nc_run"):
            start_job("norms-check", options)
        show_job("norms-check", options, _norms_check_shown)


def _norms_check_shown(result: dict):
    if result.get("failure"):
        st.error(t("Not checked: {e}").format(e=result["failure"]))
    else:
        _norms_check_report(result)


def _game_report(game: dict):
    if game.get("failure"):
        st.error(game["failure"])
        return
    if game["status"] == 2:
        st.error(t("Nothing can be claimed:") + " " + (game["errors"] or t("see the report.")))
    elif game["status"] == 1:
        st.warning(t("A map did not reach its resolution, or a query was not evaluated."))
    if game["text"]:
        st.code(game["text"], language="text")


# ── Jobs in the background and the record of the runs ──────────────

def _norms_check_call(source, contract, bindings, members, step, horizon, timeout, domains=(),
                      instances=None, versions=(), starts="any", max_states=0, cancel=None):
    return ds.norms_check(source, contract or None, list(bindings), list(members), step or None,
                          horizon or None, timeout, domains=list(domains), instances=instances,
                          versions=list(versions), starts=starts, cancel=cancel,
                          max_states=max_states or None)


def _game_check_call(source, certify, cancel=None):
    return ds.game_check(source, certify, cancel=cancel)


JOBS = {"norms-check": _norms_check_call, "game-check": _game_check_call}


def start_job(kind: str, options: tuple):
    """Start `cadl <kind>` with these options in the background (or take the run that
    already has them) and remember it as the one this session shows."""
    # keyed by this session too, so that Stop in one browser does not stop another's run
    job = jobs.start(kind, (_session(), options), JOBS[kind], *options)
    ss[f"design_job_{kind}"] = job.id


def _session() -> str:
    if "design_session_id" not in ss:
        import uuid
        ss.design_session_id = uuid.uuid4().hex
    return ss.design_session_id


def _outcome(kind: str, result: dict) -> tuple:
    """(result in a word, summary in a sentence) of one run, for the record."""
    if result.get("failure"):
        return ("stopped" if "was stopped before it finished" in result["failure"] else "failed",
                result["failure"])
    if kind == "game-check":
        return ({0: "passed", 1: "incomplete", 2: "failed"}.get(result.get("status"), "failed"),
                (result.get("text") or "").splitlines()[-1:][0] if result.get("text") else "")
    report = result.get("report") or {}
    if not report.get("checked"):
        return "not checked", str(report.get("skipped") or "")
    if report.get("unconfirmed"):
        return "failed", "a witness is not confirmed"
    found = len(report.get("findings") or [])
    conflicts = len(report.get("conflicts") or [])
    if found or conflicts:
        return "findings", f"{found} finding(s), {conflicts} conflict(s)"
    return "passed", f"{len(report.get('norms') or [])} norm(s), no finding"


def show_job(kind: str, options: tuple, render):
    """The state of this session's run of `cadl <kind>`, when it ran with `options`:
    a line that counts the seconds, with a button that stops it, while it runs; its
    result when it has finished. A failure is shown once."""
    job = jobs.get(ss.get(f"design_job_{kind}"))
    if job is None or job.key != (_session(), options):
        return
    if job.running:
        @st.fragment(run_every=1.0)
        def _waiting():
            current = jobs.get(job.id)
            if current is None or not current.running:
                st.rerun()
            with st.container(horizontal=True, vertical_alignment="center"):
                st.info(t("Running `cadl {kind}` … {s} s. You can use the rest of the page "
                          "meanwhile.").format(kind=kind, s=int(current.seconds)),
                        icon=":material/hourglass_top:")
                st.button(t("Stop"), key=f"design_job_stop_{kind}", on_click=jobs.cancel,
                          args=(current.id,), icon=":material/stop_circle:")
        _waiting()
        return
    result = job.result if job.result is not None else {"failure": f"Not run: {job.error}"}
    recorded = ss.setdefault("design_recorded_jobs", set())
    if job.id not in recorded:
        recorded.add(job.id)
        word, summary = _outcome(kind, result)
        record_run(kind, options[0], word, summary, job.seconds, rerun=(kind, options))
    if result.get("failure"):
        ss[f"design_job_{kind}"] = None     # shown once; the next run needs the button
    render(result)


def record_run(check: str, source: str, result: str, summary: str, seconds: float = 0.0,
               rerun=None, **extra):
    """Keep one run of a check of cadl with the source it ran on (by its hash) and the
    version of cadl, for the summary, the project file and the report."""
    from backend.services.design_runtime import toolchain
    import uuid
    runs = ss.setdefault("design_runs", [])
    run_id = uuid.uuid4().hex[:12]
    runs.append({"id": run_id,
        "check": check, "result": result, "summary": summary,
        "source_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest(),
        "cadl": toolchain()["cadl"], "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "seconds": round(seconds, 1), **extra,
    })
    del runs[:-MAX_RUNS]
    reruns = ss.setdefault("design_rerun", {})
    if rerun is not None:
        reruns[run_id] = rerun
    kept = {r.get("id") for r in runs}
    for old in [k for k in reruns if k not in kept]:    # their runs left the record
        del reruns[old]


def _record_once(check: str, source: str, result: str, summary: str):
    """Record a quick check once for each source it ran on."""
    sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    latest = _latest_runs().get(check)
    if latest is None or latest[1]["source_sha256"] != sha:
        record_run(check, source, result, summary)


def _latest_runs() -> dict:
    latest = {}
    for i, run in enumerate(ss.get("design_runs") or []):
        latest[run["check"]] = (i, run)
    return latest


def stale_runs(source: str) -> int:
    """How many checks have their latest result from another source than this one."""
    sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    return sum(run["source_sha256"] != sha for _, run in _latest_runs().values())


def ran_on(run: dict, source: str) -> str:
    """Which design a run checked: the current one, a saved version, or another."""
    if run["source_sha256"] == hashlib.sha256(source.encode("utf-8")).hexdigest():
        return t("the current design")
    for v in ss.get("design_versions") or []:
        if hashlib.sha256(v["source"].encode("utf-8")).hexdigest() == run["source_sha256"]:
            return t("version") + f" “{v['name']}”"
    return t("an earlier design")


def _rerun(run_id: str, source: str):
    kind, options = ss.design_rerun[run_id]
    options = (source,) + tuple(options[1:])
    if kind == "norms-check":
        # versions equal to the current design are left out, as the form does
        options = options[:9] + (tuple(v for v in options[9] if v[1] != source),) + options[10:]
    start_job(kind, options)
    ss.design_view = "Checks"
    ss.design_rerun_note = kind


def summary(c):
    """Every check in one table: what it found, on which design, when and how long."""
    source, analysis = c.source, c.analysis
    st.caption(t(
        "The latest result of each check, with the design it ran on. A result from an "
        "earlier design is marked; run the check again to bring it up to date. The record "
        "is kept with the project file (sidebar)."))
    errors, warnings = analysis.count("error"), analysis.count("warning")
    rows = [{
        "check": "cadl check", "result": "failed" if errors else "findings" if warnings else "passed",
        "summary": f"{errors} error(s), {warnings} warning(s)",
        "design": t("the current design"), "time": t("on every change"), "seconds": None,
        "cadl": None, "up to date": True,
    }]
    latest = _latest_runs()
    sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
    for check, (i, run) in latest.items():
        rows.append({"check": f"cadl {check}", "result": run["result"], "summary": run["summary"],
                     "design": ran_on(run, source), "time": run["time"], "seconds": run["seconds"],
                     "cadl": run["cadl"], "up to date": run["source_sha256"] == sha})
    for row in rows:
        row["result"] = t(row["result"])
    st.dataframe(rows, hide_index=True, width="stretch", column_config={
        "check": st.column_config.TextColumn(t("check")),
        "result": st.column_config.TextColumn(t("result")),
        "summary": st.column_config.TextColumn(t("summary")),
        "design": st.column_config.TextColumn(t("design")),
        "time": st.column_config.TextColumn(t("time")),
        "cadl": st.column_config.TextColumn("cadl"),
        "up to date": st.column_config.CheckboxColumn(
            t("up to date"), help=t("The check ran on the design that is in the editor now.")),
        "seconds": st.column_config.NumberColumn(t("seconds"), format="%.1f"),
    })
    extensions = ds.extensions_in_effect(source)
    offered = [k for k, ext in (("norms-check", "norms"), ("game-check", "games")) if ext in extensions]
    missing = [k for k in offered if k not in latest]
    if missing:
        st.caption(t("Not run yet on this design:") + " " + ", ".join(f"`cadl {k}`" for k in missing)
                   + t(" (under **Checks**)."))
    stale = [(check, run.get("id")) for check, (i, run) in latest.items()
             if run["source_sha256"] != sha and run.get("id") in (ss.get("design_rerun") or {})]
    if stale:
        with st.container(horizontal=True):
            for check, run_id in stale:
                st.button(t("Run {check} again on the current design").format(check=f"`cadl {check}`"),
                          key=f"design_rerun_{check}", on_click=_rerun, args=(run_id, source),
                          icon=":material/replay:")
    runs = ss.get("design_runs") or []
    if len(runs) > len(latest):
        with st.expander(t("Every run in this session") + f" ({len(runs)})"):
            st.dataframe([{k: r[k] for k in ("time", "check", "result", "summary", "cadl", "seconds")}
                          | {"result": t(r["result"]), "design": ran_on(r, source)}
                          for r in reversed(runs)],
                         hide_index=True, width="stretch", column_config={
                             k: st.column_config.Column(t(k))
                             for k in ("time", "check", "result", "summary", "design", "seconds")})


def _yes(entry) -> str:
    if not isinstance(entry, dict) or "answer" not in entry:
        return "-"
    return t("yes") if entry["answer"] else t("no")


def _norms_check_report(result: dict):
    report = result["report"]
    if not report.get("checked"):
        st.warning(t("Nothing was checked: {reason}").format(reason=report.get("skipped")))
    elif report.get("unconfirmed"):
        st.error(t("A witness is not confirmed by the norms engine (an error of norms-check):") + " "
                 + _plain("; ".join(map(str, report["unconfirmed"]))))
    if report.get("checked") and report.get("findings"):
        st.warning(t("Findings:") + "\n" + "\n".join(f"- {_plain(f)}" for f in report["findings"]))
    elif report.get("checked") and report.get("runs") and not report.get("unconfirmed"):
        st.success(t("No finding."))
    elif report.get("checked") and not report.get("runs"):
        st.info(t("Nothing to ask the model checker: no norm is translated."))
    for note in report.get("notes") or []:
        st.caption(_plain(t("Note: {note}.").format(note=note)))
    if not report.get("excuse_known", True):
        st.caption(t("An assumption is not translated, so a violation may be excused in a run."))
    if report.get("checked") and report.get("runs"):
        rows = [{"norm": n["id"], "kind": n["kind"], "activated": _yes(n.get("activatable")),
                 "violable": _yes(n.get("violable")), "fulfillable": _yes(n.get("fulfillable")),
                 "kept": _yes(n.get("keepable")), "unavoidable": _yes(n.get("unavoidable")),
                 "exercised": _yes(n.get("exercisable")), "reparation": _yes(n.get("reparation"))}
                for n in report["norms"] if n["status"] == "model"]
        for row, n in zip(rows, [n for n in report["norms"] if n["status"] == "model"]):
            if n.get("assumed_domains"):
                row["norm"] += "*"
        st.dataframe(rows, hide_index=True, width="stretch", column_config={
            k: st.column_config.Column(t(k)) for k in (
                "norm", "kind", "activated", "violable", "fulfillable", "kept", "unavoidable",
                "exercised", "reparation")})
        st.caption(t("*kept*: every activation can be kept; *unavoidable*: violated in every "
                   "instance that runs to the horizon; -: the question does not apply."))
        assumed = (report.get("instance") or {}).get("assumed_domains") or {}
        if assumed:
            ranges = [t("`{k}` are integers in {lo}..{hi}").format(k=k, lo=v[0], hi=v[1])
                      for k, v in assumed.items()]
            joined = (ranges[0] if len(ranges) == 1 else
                      t(", ").join(ranges[:-1]) + t(" and ") + ranges[-1])
            st.caption(t("\\* The answers hold only for traces whose numbers for {ranges}: cadl "
                         "assumed these domains from the literals of arithmetic that is not "
                         "linear. Give a domain to choose another one.").format(ranges=joined))
        if report["conflicts"]:
            st.markdown(t("*Conflicts*"))
        for c in report["conflicts"]:
            marked = "\\*" if c.get("assumed_domains") else ""
            st.markdown("- " + t("{a} and {b}{marked}: after {n} observation(s), keeping one "
                                 "violates the other.").format(
                a=c["norms"][0], b=c["norms"][1], marked=marked, n=c.get("prefix", "?")))
            for w in c.get("witnesses", []):
                # cadl 0.3.28: an instance that starts after an amendment is an
                # instance of that version, whose file the download holds
                versions = "".join(" " + t("Instance {k} is an instance of `{v}`.").format(k=k, v=v)
                                   for k, v in sorted((w.get("files") or {}).items()))
                st.markdown(_plain("    - " + t("keep {keeps}: {claim} ({state} by norms-eval):").format(
                                       keeps=w["keeps"], claim=w["claim"],
                                       state=t("confirmed") if w["confirmed"] else t("not confirmed"))
                                   + " " + "; ".join(
                                       f"#{k} `{x}`" for k, x in enumerate(w["choices"])))
                            + versions)
    untranslated = [n for n in report["norms"] if n["status"] != "model"]
    if untranslated:
        st.markdown(t("*Not translated*"))
        for n in untranslated:
            st.markdown(_plain(f"- {n['id']}: {n['reason']}"))
    _witnesses(report)
    with st.expander(t("The report of norms-check, with every witness")):
        st.code(result["text"], language="text")
    if result.get("notice"):
        st.caption(result["notice"])
    if result.get("zip"):
        st.download_button(t("Model, properties and witnesses ({n} files, .zip)").format(
                               n=len(result["files"])),
                           result["zip"], file_name=f"{report['contract']}.norms-check.zip",
                           mime="application/zip", key="design_nc_download")



def _all_witnesses(report: dict) -> dict:
    """{label: witness} of every witness in a report of norms-check."""
    out = {}
    for n in report.get("norms") or []:
        for question, entry in n.items():
            if isinstance(entry, dict) and isinstance(entry.get("witness"), dict):
                out[f"{n['id']}: {question}"] = entry["witness"]
    for c in report.get("conflicts") or []:
        for w in c.get("witnesses") or []:
            out[f"{c['norms'][0]} and {c['norms'][1]}: keep {w.get('keeps')}"] = w
    return out


def _to_traces(trace: str, contract: str, versions=()):
    ss.design_trace = trace
    ss.design_trace_ver = ss.get("design_trace_ver", 0) + 1
    ss.design_trace_contract = contract
    # the witness names the version in force at each observation (`version`), so
    # Traces gets the versions norms-check had, or none, and no time base
    from views._design.trace import set_run_versions
    set_run_versions("design_trace", ss.design_src, versions)
    ss.design_view = "Traces"


def _witnesses(report: dict):
    """One witness at a time as a sequence of messages, which the Traces view can evaluate."""
    found = _all_witnesses(report)
    if not found:
        return
    from backend.plotting.sequence import sequence_svg, trace_protocol
    from views._design.common import _is_dark
    st.markdown("*" + t("Witnesses") + "*")
    label = st.selectbox(t("Witness"), list(found), key="design_nc_witness",
                         help=t("A run of the contract instance that shows the answer: the "
                                "messages the environment chose, observation by observation."))
    w = found[label]
    records = []
    for line in w.get("trace") or []:
        try:
            records.append(json.loads(line) if isinstance(line, str) else line)
        except json.JSONDecodeError:
            pass
    st.caption(_plain(f"{w.get('claim', '')} — {w.get('explanation', '')}"))
    if records:
        st.markdown(sequence_svg(trace_protocol(records), dark=_is_dark()), unsafe_allow_html=True)
        if not (w.get("files") or {}):
            st.button(t("Evaluate this witness under Traces (norms-eval)"), key="design_nc_to_trace",
                      on_click=_to_traces, icon=":material/timeline:",
                      args=("\n".join(json.dumps(r) for r in records), report.get("contract"),
                            tuple(ss.get("design_nc_versions") or ())
                            if any("version" in r for r in records) else ()))


def checks(c):
    source, analysis = c.source, c.analysis
    note = ss.pop("design_rerun_note", None)
    if note:
        st.info(t("`cadl {kind}` runs again on the current design; its result is below.").format(kind=note))
    st.caption(
        t("Each check, what it covers and what it found. A green result means "
        "only what the description says.")
    )
    jump_index = 0
    for stage in ds.STAGES:
        stage_findings = [f for f in analysis.findings if f.stage == stage]
        errors = sum(f.level == "error" for f in stage_findings)
        warnings = sum(f.level == "warning" for f in stage_findings)
        state = ((t("{n} error") if errors == 1 else t("{n} errors")).format(n=errors) if errors else
                 (t("{n} warning") if warnings == 1 else t("{n} warnings")).format(n=warnings)
                 if warnings else t("passed") if stage_findings else t("not run"))
        with st.expander(f"{t(stage)} — {state}", expanded=bool(errors or warnings)):
            st.caption(t(ds.STAGE_SCOPE[stage]))
            if not stage_findings:
                st.caption(
                    t("Not run: nothing in the design for it to look at, or an "
                    "earlier check failed.")
                )
            for f in stage_findings:
                st.markdown(
                    f"{LEVEL_ICON[f.level]} {f.title}" + (f" — {f.message}" if f.message else ""))
                if f.level in ("error", "warning"):
                    jump_index += 1
                    _problem_buttons(f, f"check_jump_{jump_index}")
    with st.expander(t("Not checked")):
        for item in ds.NOT_CHECKED:
            st.markdown(f"- {t(item)}")
    extensions = ds.extensions_in_effect(source)
    if extensions & {"norms", "games", "grounding"}:
        _extension_checks(source, extensions)
