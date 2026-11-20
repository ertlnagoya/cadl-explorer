"""Decision evidence: read an evidence bundle and turn it into views.

An evidence bundle (``evidence.json``, format ``robot-town-evidence/1``) is
written by ``python -m town.steward`` in cadl-robot-town. It records, for each
period of an amendment loop, under which KPI the rules were judged (``K``
records) and how each period was decided (``D`` records): what was measured,
how each company voted, what the LLM recommended, and who decided, with the
reason. The records are linked by a SHA-256 chain, and the files of the run are
listed with their hashes.

This module has no Streamlit dependency, so the views can be tested alone.
Text that a page shows is passed through ``t`` (the page's translation).
"""

from __future__ import annotations

import hashlib
import json
import textwrap
from typing import Callable

FORMAT = "robot-town-evidence/1"

# who said or did what: the colours of the actors in every view
ACTORS = {
    "human": ("#1f6feb", "#58a6ff"),       # (light theme, dark theme)
    "llm": ("#8250df", "#bc8cff"),
    "tool": ("#57606a", "#8b949e"),
    "companies": ("#1a7f37", "#3fb950"),
    "alert": ("#cf222e", "#ff7b72"),
    "caution": ("#9a6700", "#d29922"),
}
GATES = ["check", "const", "inv", "kpi_guard", "gain", "seeds", "vote"]
ESCALATION = {
    "agreement": "the companies' vote is against a gain",
    "contract": "changes the promise to customers",
    "regulation": "changes the constitution",
    "safety": "touches a safety parameter",
    "kpi_conflict": "conflicts with the KPI's conditions",
    "uncertain": "gain not seen on most seeds",
    "llm": "the LLM asked for a human",
}
KEEP = "keep"


def _id(text: str) -> str:
    return text


def colour(actor: str, dark: bool = False) -> str:
    return ACTORS[actor][1 if dark else 0]


def load(raw: str | bytes | dict) -> dict:
    """Parse and check the shape of a bundle. Raises ValueError with the reason."""
    doc = raw if isinstance(raw, dict) else json.loads(raw)
    if not isinstance(doc, dict) or doc.get("format") != FORMAT:
        raise ValueError(f"not an evidence bundle (format {doc.get('format') if isinstance(doc, dict) else '?'!r}, "
                         f"expected {FORMAT!r})")
    for key in ("run", "records", "chain"):
        if key not in doc:
            raise ValueError(f"the bundle has no {key!r}")
    return doc


def _canonical(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def verify_chain(doc: dict) -> list[dict]:
    """Recompute the chain. One row per record: id, the stored and recomputed hash, ok."""
    rows, prev = [], "0" * 64
    stored = {c["id"]: c for c in doc.get("chain", [])}
    for r in doc["records"]:
        h = hashlib.sha256(prev.encode() + _canonical(r)).hexdigest()
        got = stored.get(r["id"], {})
        rows.append({"id": r["id"], "stored": got.get("hash", ""), "recomputed": h,
                     "ok": got.get("hash") == h and got.get("prev") == prev})
        prev = h
    return rows


def chain_ok(doc: dict) -> bool:
    rows = verify_chain(doc)
    return all(r["ok"] for r in rows) and len(rows) == len(doc.get("chain", []))


def records(doc: dict, kind: str | None = None) -> list[dict]:
    return [r for r in doc["records"] if kind is None or r["type"] == kind]


def record(doc: dict, rid: str) -> dict:
    return next(r for r in doc["records"] if r["id"] == rid)


def candidate(rec: dict, cid: str | None) -> dict | None:
    return next((c for c in rec.get("candidates", []) if c["id"] == cid), None)


def focus(rec: dict) -> dict | None:
    """The proposal a decision turned on: the adopted one, else the first escalated, else the best."""
    for cid in [rec["decision"].get("adopted"), *rec.get("escalated", []), rec.get("auto_best")]:
        c = candidate(rec, cid)
        if c:
            return c
    return None


def votes_text(votes: dict | None) -> str:
    if not votes:
        return "—"
    return " ".join(f"{k}{'○' if v else '×'}" for k, v in sorted(votes.items()))


def choice_label(rec: dict, choice: str | None) -> str:
    if choice in (None, KEEP):
        return KEEP
    c = candidate(rec, choice)
    return c["label"] if c else str(choice)


# --- accountability check ----------------------------------------------------

def flags(doc: dict, t: Callable[[str], str] = _id) -> list[dict]:
    """What a reviewer should look at, one row per finding.

    level: ``ok`` (recorded as expected), ``info``, ``warn`` (a gap in the
    record), ``error`` (the record cannot be trusted as it stands).
    """
    out = []

    def add(rid, level, kind, text):
        out.append({"record": rid, "level": level, "kind": kind, "text": text})

    if not chain_ok(doc):
        bad = next((r["id"] for r in verify_chain(doc) if not r["ok"]), "?")
        add(bad, "error", t("chain"), t("The hash chain breaks at this record: it was changed after it was written."))
    for r in doc["records"]:
        d = r["decision"]
        if r["type"] == "kpi":
            if d["by"] == "tool" and r.get("how") == "llm":
                add(r["id"], "warn", t("KPI reading"),
                    t("The LLM's reading of the KPI was used without asking the human."))
            elif d["by"] == "human" and r.get("how") == "llm" and not d.get("reason"):
                add(r["id"], "warn", t("missing reason"), t("The human chose a reading of the KPI without a reason."))
            elif d["by"] == "human" and r.get("how") == "llm":
                add(r["id"], "ok", t("KPI reading"),
                    t("The human chose reading {c} with a reason.").format(c=d["choice"]))
            if r.get("how") == "llm" and d.get("followed_llm") is False:
                add(r["id"], "info", t("differs from LLM"),
                    t("The human chose {c}; the LLM recommended {r}.").format(c=d["choice"], r=r.get("recommended")))
            continue
        req = r.get("request")
        if d["by"] == "human" and not d.get("reason"):
            add(r["id"], "warn", t("missing reason"), t("The human decided without a reason."))
        if d.get("vote_overridden"):
            c = candidate(r, d.get("adopted"))
            add(r["id"], "warn" if not d.get("reason") else "info", t("vote overridden"),
                t("{label} was adopted against the vote ({votes}).").format(
                    label=c["label"] if c else d.get("adopted"), votes=votes_text(c and c.get("votes")))
                + ("" if d.get("reason") else " " + t("No reason is recorded.")))
        if d.get("followed_llm") is False and req:
            add(r["id"], "info", t("differs from LLM"),
                t("The human chose {c}; the LLM recommended {r}.").format(
                    c=choice_label(r, d["choice"]), r=choice_label(r, req.get("recommendation"))))
        if req and req.get("unknowns"):
            add(r["id"], "info", t("open questions"),
                t("The decision request lists {n} things the LLM did not know.").format(n=len(req["unknowns"])))
        if d["by"] == "tool" and d.get("adopted"):
            c = candidate(r, d["adopted"])
            passed = [g for g in GATES if c and c["gates"].get(g)]
            add(r["id"], "ok", t("automatic"),
                t("{label} was adopted by the tool: it passed {gates}.").format(
                    label=c["label"] if c else d["adopted"], gates=", ".join(passed)))
        if d["by"] == "tool" and not d.get("adopted"):
            add(r["id"], "ok", t("automatic"), t("No proposal passed every gate; the rules were kept."))
    return out


# --- timeline ------------------------------------------------------------------

def timeline(doc: dict) -> list[dict]:
    """One row per period with what each actor did (for the overview figure)."""
    rows = []
    kpi_at = {r["period"]: r for r in records(doc, "kpi")}
    for r in records(doc, "decision"):
        d, req, f = r["decision"], r.get("request") or {}, focus(r)
        k = kpi_at.get(r["period"])
        rows.append({
            "period": r["period"],
            "env": r["env"]["label"],
            "kpi": k["id"] if k else None,
            "kpi_by": k["decision"]["by"] if k else None,
            "kpi_in_force": r["kpi"],
            "proposals": len(r.get("candidates", [])),
            "focus": f["label"] if f else None,
            "votes": votes_text(f and f.get("votes")),
            "vote_passes": bool(f and f["gates"].get("vote")),
            "llm": choice_label(r, req.get("recommendation")) if req else None,
            "asked": bool(req),
            "by": d["by"],
            "choice": choice_label(r, d["choice"]),
            "adopted": d.get("adopted") is not None,
            "vote_overridden": bool(d.get("vote_overridden")),
            "followed_llm": d.get("followed_llm"),
            "version": d["version_after"],
            "reason": d.get("reason"),
        })
    return rows


def series(doc: dict) -> list[dict]:
    """The measured value of each metric under the rules in force, per period."""
    out = []
    for r in records(doc, "decision"):
        for m, v in (r["current"]["metrics"] or {}).items():
            out.append({"period": r["period"], "metric": m, "value": v, "version": r["version_in_force"]})
        for c, u in (r["current"].get("utility") or {}).items():
            out.append({"period": r["period"], "metric": f"utility {c}", "value": u,
                        "version": r["version_in_force"]})
    return out


# --- DOT -----------------------------------------------------------------------

def _q(text: str, width: int = 38, lines: int = 6) -> str:
    """A DOT label: wrapped, shortened, quotes escaped."""
    text = " ".join(str(text or "").split())
    parts = textwrap.wrap(text, width) or [""]
    if len(parts) > lines:
        parts = parts[:lines]
        parts[-1] = parts[-1][: width - 1] + "…"
    return "\\n".join(p.replace("\\", "\\\\").replace('"', '\\"') for p in parts)


def _head(dark: bool, rankdir: str = "TB") -> list[str]:
    ink = "#e6edf3" if dark else "#1f2328"
    return [
        "digraph G {",
        f'  rankdir={rankdir}; bgcolor="transparent"; nodesep=0.3; ranksep=0.5; fontname="Helvetica";',
        f'  node [fontname="Helvetica" fontsize=10 color="{ink}" fontcolor="{ink}" shape=box style=rounded];',
        f'  edge [fontname="Helvetica" fontsize=9 color="{ink}" fontcolor="{ink}"];',
    ]


def _node(nid: str, label: str, actor: str, dark: bool, **attrs) -> str:
    c = colour(actor, dark)
    extra = "".join(f' {k}="{v}"' for k, v in attrs.items())
    return f'  {nid} [label="{label}" color="{c}" penwidth=1.6{extra}];'


def decision_dot(doc: dict, rid: str, dark: bool = False, t: Callable[[str], str] = _id) -> str:
    """The trail of one record: from the KPI and the measurements to the decision."""
    r = record(doc, rid)
    return (_kpi_dot if r["type"] == "kpi" else _decision_dot)(doc, r, dark, t)


def _kpi_dot(doc: dict, r: dict, dark: bool, t) -> str:
    d = r["decision"]
    lines = _head(dark)
    lines.append(_node("kpi", _q(t("KPI written by the human") + f" ({r['id']}): " + r["text"], 44, 7),
                       "human", dark))
    if r.get("how") == "written":
        lines.append(_node("measure", _q(t("Evaluation written in the KPI") + ": " + r["measure_text"], 44, 6),
                           "human", dark))
        lines.append("  kpi -> measure;")
        lines.append("}")
        return "\n".join(lines)
    lines.append(_node("reading", _q(t("LLM reading") + ": " + r.get("interpretation", ""), 44, 7), "llm", dark))
    lines.append("  kpi -> reading;")
    for o in r.get("options", []):
        chosen = o["id"] == d["choice"]
        lines.append(_node(f"o{o['id']}", _q(f"{t('Option')} {o['id']}: {o['label']}", 34, 4),
                           "llm", dark, **({"penwidth": "3"} if chosen else {"style": "rounded,dashed"})))
        lines.append(f"  reading -> o{o['id']};")
    if r.get("concerns"):
        lines.append(_node("concerns", _q(t("LLM concerns") + f": {len(r['concerns'])}", 30, 2), "llm", dark,
                           shape="note", style="solid"))
        lines.append("  reading -> concerns [style=dashed arrowhead=none];")
    who = "human" if d["by"] == "human" else "tool"
    dec = t("Human chose") if who == "human" else t("Used without asking")
    lines.append(_node("decision", _q(f"{dec} {d['choice']}"
                                      + (f" — {d['reason']}" if d.get("reason") else ""), 44, 8), who, dark,
                       penwidth="2.4"))
    lines.append(f"  o{d['choice']} -> decision [penwidth=2.4];")
    if r.get("recommended") not in (None, "—"):
        follow = d.get("followed_llm")
        lines.append(f'  o{r["recommended"]} -> decision [style=dashed color="{colour("llm", dark)}" '
                     f'label="{t("LLM recommends")}{"" if follow is not False else " ≠"}"];')
    for i, a in enumerate(d.get("assumptions") or []):
        lines.append(_node(f"a{i}", _q(t("Assumption") + ": " + a, 34, 3), who, dark, shape="note", style="solid"))
        lines.append(f"  decision -> a{i} [style=dashed arrowhead=none];")
    lines.append(_node("measure", _q(t("Measure in force") + ": " + r["measure_text"], 44, 6), who, dark))
    lines.append("  decision -> measure;")
    lines.append("}")
    return "\n".join(lines)


def _cand_label(c: dict, t) -> str:
    head = f"{c['id']} {c['label']}"
    if c.get("metrics") is None:
        return _q(head, 30, 2) + "\\n" + _q(t("rejected") + ": " + (c.get("rejected_because") or c.get("effect")
                                                                   or "—"), 30, 3)
    gates = " ".join(f"{g}{'○' if c['gates'].get(g) else '×'}" for g in GATES if g in c["gates"])
    gain = c.get("gain")
    lines = [_q(head, 30, 2),
             f"{t('gain')} {gain:+.2f} · {t('seeds')} {c.get('seeds_better')}/{c.get('seeds')}",
             _q(gates, 30, 2)]
    return "\\n".join(lines)


def _decision_dot(doc: dict, r: dict, dark: bool, t) -> str:
    d, req = r["decision"], r.get("request")
    k = record(doc, r["kpi"])
    lines = _head(dark)
    lines.append(_node("kpi", _q(f"{r['kpi']}: " + k["measure_text"], 34, 6), "human", dark))
    cur = r["current"]["metrics"] or {}
    util = r["current"].get("utility") or {}
    measured = (f"{t('Measured')} v{r['version_in_force']} · {r['env']['label']}\\n"
                f"{t('late')} {cur.get('late_deliveries', 0):.1f} · {t('blocked')} {cur.get('blocked_s', 0):.0f} s\\n"
                f"{t('utility')} " + " / ".join(f"{c} {u:.1f}" for c, u in util.items()))
    lines.append(_node("cur", measured, "tool", dark))
    lines.append("  kpi -> cur;")
    if r.get("assessment"):
        lines.append(_node("assess", _q(t("LLM assessment") + ": " + r["assessment"], 40, 5), "llm", dark,
                           shape="note", style="solid"))
        lines.append("  cur -> assess [style=dashed arrowhead=none];")
    lines.append("  subgraph cluster_c { style=dashed; color=\"%s\"; label=\"%s\"; fontcolor=\"%s\";" % (
        colour("llm", dark), t("Proposals by the LLM, measured by the tool"), colour("llm", dark)))
    for c in r.get("candidates", []):
        esc = c["id"] in r.get("escalated", [])
        actor = "caution" if esc else ("companies" if c["id"] == r.get("auto_best") else "tool")
        style = "rounded" if c.get("metrics") is not None else "rounded,dashed"
        lines.append(_node(f"c{c['id']}", _cand_label(c, t), actor, dark, style=style))
    lines.append("  }")
    for c in r.get("candidates", []):
        lines.append(f"  cur -> c{c['id']};")
        if c.get("votes"):
            ok = c["gates"].get("vote")
            lines.append(_node(f"v{c['id']}", f"{t('votes')} {votes_text(c['votes'])}", "companies"
                               if ok else "alert", dark, shape="box", style="solid", fontsize="9"))
            lines.append(f"  c{c['id']} -> v{c['id']} [arrowhead=none];")
    who = d["by"]
    if req:
        reco = choice_label(r, req.get("recommendation"))
        lines.append(_node("llm", _q(f"{t('LLM recommends')} {reco}: " + (req.get("recommendation_reason") or ""),
                                     40, 7), "llm", dark))
        for cid in r.get("escalated", []):
            lines.append(f'  v{cid} -> llm [label="{t("asked")}"];' if candidate(r, cid).get("votes")
                         else f"  c{cid} -> llm;")
        if req.get("unknowns"):
            lines.append(_node("unknowns", _q(t("Not known") + f": {len(req['unknowns'])} "
                                              + t("items"), 24, 2), "llm", dark, shape="note", style="solid"))
            lines.append("  llm -> unknowns [style=dashed arrowhead=none];")
    title = t("Human decided") if who == "human" else t("Tool decided")
    outcome = d["outcome"]
    flags_ = []
    if d.get("vote_overridden"):
        flags_.append(t("against the vote"))
    if d.get("followed_llm") is False:
        flags_.append(t("not the LLM's recommendation"))
    label = _q(f"{title}: {outcome}", 44, 3)
    if flags_:
        label += "\\n" + _q("⚠ " + ", ".join(flags_), 44, 2)
    if d.get("reason"):
        label += "\\n" + _q(t("Reason") + ": " + d["reason"], 44, 12)
    lines.append(_node("decision", label, "alert" if d.get("vote_overridden") else who, dark, penwidth="2.4"))
    if req:
        lines.append(f'  llm -> decision [style=dashed color="{colour("llm", dark)}"];')
    adopted = d.get("adopted")
    if adopted:
        src = f"v{adopted}" if (candidate(r, adopted) or {}).get("votes") else f"c{adopted}"
        lines.append(f"  {src} -> decision [penwidth=2.4];")
    elif not req:
        lines.append("  cur -> decision [style=dotted];")
    for i, a in enumerate(d.get("assumptions") or []):
        lines.append(_node(f"a{i}", _q(t("Assumption") + ": " + a, 34, 3), who, dark, shape="note", style="solid"))
        lines.append(f"  decision -> a{i} [style=dashed arrowhead=none];")
    ver = f"v{d['version_after']}" + (f" ({d['version_file']})" if d.get("version_file") else
                                      " " + t("(unchanged)"))
    lines.append(_node("version", ver, "tool", dark, shape="folder", style="solid"))
    lines.append("  decision -> version;")
    lines.append("}")
    return "\n".join(lines)


GSN_SHAPE = {
    "goal": 'shape=box style="solid"',
    "strategy": 'shape=parallelogram style="solid"',
    "solution": 'shape=circle style="solid" fixedsize=false',
    "context": 'shape=box style="rounded"',
    "assumption": 'shape=ellipse style="solid"',
    "justification": 'shape=ellipse style="solid"',
}


def gsn_dot(doc: dict, dark: bool = False, highlight: str | None = None, focus: bool = False,
            width: int = 30) -> str:
    """The GSN argument of the run, coloured by who wrote each element.

    With ``highlight`` (a record id) the part of the argument made for that record stands out: for a
    period, its strategy and everything under it; for a KPI, its goal, its contexts and the strategies of
    its periods. With ``focus`` only that part and the path up to the top goal are drawn."""
    nodes = doc.get("gsn") or []
    by_id = {n["id"]: n for n in nodes}
    parent = {c: n["id"] for n in nodes for c in n.get("supported_by", []) + n.get("in_context_of", [])}
    marked: set[str] = set()
    for root in (n for n in nodes if highlight and n.get("ref") == highlight):
        marked.add(root["id"])
        marked.update(root.get("in_context_of", []))
        stack = list(root.get("supported_by", []))
        while stack:
            nid = stack.pop()
            if nid in marked:
                continue
            marked.add(nid)
            if root["type"] != "goal":         # a period: everything under its strategy
                stack += by_id[nid].get("supported_by", []) + by_id[nid].get("in_context_of", [])
    keep = set(by_id)
    if focus and marked:
        keep = set(marked)
        for nid in list(marked):
            while nid in parent:
                nid = parent[nid]
                keep.add(nid)
    lines = _head(dark, "TB")
    ink = "#e6edf3" if dark else "#1f2328"
    for n in nodes:
        if n["id"] not in keep:
            continue
        actor = n.get("by") or ("human" if n["type"] == "goal" and n.get("ref", "").startswith("K") else "tool")
        actor = actor if actor in ACTORS else "tool"
        col = colour(actor, dark) if actor != "tool" else ink
        if n["type"] == "solution":
            label = f'{n["id"]}\\n{_q(n.get("file") or n["text"], 16, 2)}'
        else:
            letter = {"assumption": "\\nA", "justification": "\\nJ"}.get(n["type"], "")
            label = f'{n["id"]}: {_q(n["text"], width, 5)}{letter}'
        attrs = f'{GSN_SHAPE[n["type"]]} color="{col}" penwidth={2.6 if n["id"] in marked else 1.2}'
        if marked and n["id"] not in marked:
            attrs += ' fontcolor="#8b949e" color="#8b949e"'
        if n.get("ref"):
            attrs += f' tooltip="{n["ref"]}"'
        lines.append(f'  {n["id"]} [label="{label}" {attrs}];')
    for n in nodes:
        for c in n.get("supported_by", []):
            if n["id"] in keep and c in keep:
                lines.append(f"  {n['id']} -> {c};")
        for c in n.get("in_context_of", []):
            if n["id"] in keep and c in keep:
                lines.append(f"  {n['id']} -> {c} [style=dashed arrowhead=empty];")
    lines.append("}")
    return "\n".join(lines)
