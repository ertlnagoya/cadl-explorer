"""Export: the design, its report, simulator configs, runtime code and analyses."""

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
from backend.services import design_workspace as workspace
from backend.services.cadl_service import MAX_SOURCE_CHARS
from cadl_sim.sos_dsl import build_lifecycle_view, lifecycle_to_dot, monitors_summary
from views._design.common import *  # noqa: F401,F403
from views._design.common import _md
from views._design.cache import *  # noqa: F401,F403
from views._design.cache import _NotCached, _uncached
from views._i18n import t


@st.cache_data(show_spinner=False, max_entries=16)
def _sim_validate_kept(source: str) -> dict:
    from backend.services.design_runtime import sim_validate
    return _uncached(sim_validate(source))


def _sim_validate(source: str) -> dict:
    try:
        return _sim_validate_kept(source)
    except _NotCached as e:
        return e.result


def _to_explorer(source: str):
    try:
        ss.custom_cadl = ds.to_explorer_yaml(source)
    except Exception as e:
        ss.design_to_explorer_error = f"Could not convert the design: {e}"
        return
    ss.custom_cadl_a = ""
    ss.explorer_from_designer = True
    ss.design_go_explorer = True


LANGUAGE = {".py": "python", ".sol": "solidity", ".rego": "rego", ".cs": "csharp", ".json": "json",
            ".yaml": "yaml", ".yml": "yaml", ".md": "markdown", ".cedar": "text", ".txt": "text",
            ".toml": "toml", ".sh": "bash"}


def _preview(archive: bytes, target: str):
    """The files of the generated archive, one at a time."""
    with zipfile.ZipFile(io.BytesIO(archive)) as zf:
        names = [i.filename for i in zf.infolist() if not i.is_dir()]
        if not names:
            return
        with st.expander(t("Look at the generated files") + f" ({len(names)})"):
            key = f"design_code_file_{target}"
            if ss.get(key) not in names:
                ss[key] = names[0]
            name = st.selectbox(t("File"), names, key=key)
            info = zf.getinfo(name)
            text = zf.read(info)[:200_000].decode("utf-8", errors="replace")
            suffix = "." + name.rsplit(".", 1)[-1] if "." in name else ""
            st.code(text, language=LANGUAGE.get(suffix, "text"), line_numbers=True)
            if info.file_size > 200_000:
                st.caption(t("Only the beginning of the file is shown."))


def export(c):
    source, analysis, file_stem = c.source, c.analysis, c.file_stem
    if ss.pop("design_go_explorer", False):
        st.switch_page("views/explorer.py")
    if not analysis.ok:
        st.warning(t("The design has errors; generated files may be incomplete."))
    st.markdown(t("**Design**"))
    e1, e2, e3 = st.columns(3)
    e1.download_button(
        t("CADL source (.cadl)"), source, file_name=f"{file_stem}.cadl",
        mime="text/yaml", width="stretch", key="export_cadl")
    e2.download_button(
        t("Simulator IR (JSON)"), ds.export_ir_json(analysis),
        file_name=f"{file_stem}.ir.json", mime="application/json", width="stretch",
        help=t("The same file `cadl sim-ir --format json` produces; the Contract Lifecycle page reads it."))
    e3.download_button(
        t("Simulator IR (YAML)"), ds.export_ir_yaml(analysis),
        file_name=f"{file_stem}.ir.yaml", mime="text/yaml", width="stretch")

    st.markdown(t("**Design report**"))
    r1, r2 = st.columns([2, 3], vertical_alignment="center")
    r1.download_button(
        t("Design report (HTML)"),
        ds.build_report(source, analysis, datetime.now().strftime("%Y-%m-%d %H:%M"),
                        runs=ss.get("design_runs") or []),
        file_name=f"{file_stem}.report.html", mime="text/html", width="stretch",
        type="primary")
    r2.caption(
        t("One page with the checks, diagrams, contracts, protocols and source. "
        "Open it in a browser and print to get a PDF. The graph diagrams are "
        "drawn when the page opens and need a network connection.")
    )

    st.markdown(t("**Simulator config**"))
    s1, s2 = st.columns([2, 3], vertical_alignment="bottom")
    sim_target = s1.selectbox(t("Target"), list(ds.SIM_TARGETS), format_func=ds.SIM_TARGETS.get,
                              key="design_sim_target")
    try:
        sim_config = ds.export_sim_config(source, sim_target)
    except Exception as e:
        s2.error(f"Cannot generate: {e}")
    else:
        s2.download_button(
            f"Download {ds.SIM_TARGETS[sim_target]} simulator config", sim_config,
            file_name=f"{file_stem}.sim.{sim_target}.txt", width="stretch")
    validated = _sim_validate(source)
    (st.caption if validated["ok"] else st.warning)(
        (":material/check_circle: " if validated["ok"] else ":material/warning: ")
        + t("`cadl sim-validate`:") + " " + _md(validated["text"].splitlines()[0] if validated["text"] else ""),
        help=validated["text"] or None)

    st.markdown("**" + t("Run in the Explorer") + "**")
    x1, x2 = st.columns([2, 3], vertical_alignment="center")
    x1.button(t("Open this design in the Explorer"), on_click=_to_explorer, args=(source,),
              width="stretch", icon=":material/compare_arrows:")
    x2.caption(t(
        "The Explorer runs a synthetic model of the robot delivery town on this design (as "
        "design B) against a template (design A). It reads only the SoS type, the motivation "
        "profile and ρ; to compare two of your designs, use **Compare two designs** under "
        "**Versions**."))
    if ss.get("design_to_explorer_error"):
        st.error(ss.pop("design_to_explorer_error"))

    st.markdown(t("**Runtime code**"))
    extensions = ds.extensions_in_effect(source)
    targets = dict(ds.CODEGEN_TARGETS)
    if "agents" in extensions:
        targets.update(ds.AGENT_CODEGEN_TARGETS)
    c1, c2 = st.columns([2, 3], vertical_alignment="bottom")
    if ss.get("design_code_target") not in targets:
        ss.design_code_target = next(iter(targets))
    code_target = c1.selectbox(t("Target"), list(targets),
                               format_func=targets.get, key="design_code_target")
    try:
        code_zip = cached_codegen(source, code_target)
    except Exception as e:
        c2.error(f"Cannot generate: {e}")
    else:
        c2.download_button(
            f"Download {targets[code_target]} (.zip)", code_zip,
            file_name=f"{file_stem}.{code_target}.zip", mime="application/zip",
            width="stretch")
        _preview(code_zip, code_target)
    notes = []
    if "norms" in extensions:
        notes.append("With norms 0.1, the Python runtime includes `norms_monitor.py`, an online "
                     "monitor of the norms and of every `assume` / `guarantee` item. The OPA "
                     "and Solidity targets include a norms monitor per contract "
                     "(`norms_monitor_<contract>.rego`, `<Contract>NormsMonitor.sol`) that "
                     "follows the norms observation by observation (Appendix D, D.5); a "
                     "contract with a quantifier, an aggregate, a wildcard or range reference "
                     "in a norm, a counts-as rule or an `assume:` item gets none. The "
                     "Solidity monitor also refuses `*`, `/`, literals with more than six "
                     "decimals, a content with six or more temporal operators, `until` inside "
                     "or around another temporal operator, an operand that joins a nested "
                     "operator with other parts, and a monitor whose code exceeds the size "
                     "limits of the EVM (cadl 0.3.31; `within` and `throughout` may be nested "
                     "at any depth); the Rego "
                     "monitor refuses `/`. The warnings below say which. Unity C# leaves out "
                     "the items with `within`, `throughout` or `until`.")
    if "agents" in extensions:
        notes.append("The agent policies (agents 0.1) decide the messages at the enforcement "
                     "point; the Cedar policies cover the statically decidable norms only. "
                     "cadl also runs the contract in front of agents: `cadl mcp-gateway` "
                     "decides every tool call of an MCP agent and `cadl a2a-gateway` every "
                     "message of an A2A agent, holding the calls that need approval, and "
                     "`cadl audit-verify` checks their tamper-evident audit log "
                     f"([Appendix G]({SPEC_URL}docs/spec/appendix-g-agents), G.8, G.11, "
                     "G.13). The Explorer does not run them.")
    for note in notes:
        st.caption(note)
    monitors = cached_monitor_notes(source, code_target) if "norms" in extensions else None
    if monitors and (monitors["files"] or monitors["warnings"]):
        if monitors["files"]:
            st.caption("Norms monitors in this archive: "
                       + ", ".join(f"`{f}`" for f in monitors["files"]) + ".")
        for warning in monitors["warnings"]:
            st.warning(_md(warning), icon=":material/warning:")

    if "stochastic" in extensions:
        st.markdown(t("**Stochastic model (PRISM)**"))
        m1, m2 = st.columns([2, 3], vertical_alignment="bottom")
        m1.caption(t("The `model:` block (stochastic 0.1) as PRISM model and properties files, "
                   "one pair per design, for PRISM or Storm (`cadl model-export`)."))
        try:
            prism = cached_prism(source)
        except Exception as e:
            m2.error(f"Cannot export: {e}")
        else:
            buffer = io.BytesIO()
            with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
                for name, text in prism.items():
                    zf.writestr(name, text)
            m2.download_button(f"Download {len(prism)} PRISM files (.zip)", buffer.getvalue(),
                               file_name=f"{file_stem}.prism.zip", mime="application/zip",
                               width="stretch")

    st.markdown(t("**Analyses**"))
    with st.expander(t("Regime map")):
        try:
            regimes = ds.regime_map(source)
        except Exception as e:
            regimes = None
            st.error(f"Cannot build the regime map: {e}")
        if regimes is None:
            st.caption(t("This design has no top-level `transitions:` between governance regimes."))
        else:
            st.code(regimes["text"], language="text")
            st.download_button(
                t("Regime map (DOT)"), regimes["dot"], file_name=f"{file_stem}.regimes.dot")
    with st.expander(t("IEC 62853 dependability summary")):
        try:
            report = ds.iec62853_report(source)
        except Exception as e:
            st.error(f"Cannot build the summary: {e}")
        else:
            if report.get("disclaimer"):
                st.caption(report["disclaimer"])
            st.json(report, expanded=2)
            st.download_button(
                t("Summary (JSON)"), json.dumps(report, indent=2, ensure_ascii=False),
                file_name=f"{file_stem}.iec62853.json", mime="application/json")
