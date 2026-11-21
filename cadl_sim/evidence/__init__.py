"""Decision records (Appendix L of the CADL specification): read and show them.

A decision record (``decisions.jsonl``, format ``cadl-decisions/2``, or
``cadl-decisions/1`` of decisions 0.1) records
how the amendments of a model were decided: the parties with their roles,
then entries (objectives, proposals, measurements, checks, votes, requests,
recommendations, decisions, versions, follow-ups), each with the earlier
entries it rests on (``basis``). Every line carries the hash chain of
Appendix G, G.8. cadl-robot-town writes one in each run of ``town.steward``
and ``town.council``. In decisions 0.2 each deciding party gives the layers
on which it decides, and each follow-up says what fulfils it.

The checks and findings come from ``cadl.decisions.verify`` (cadl 0.3.38 or
later); this module turns a record into views and has no Streamlit
dependency. Text that a page shows is passed through ``t``.
"""

from __future__ import annotations

import json
import tempfile
import textwrap
from pathlib import Path
from typing import Callable, Optional

FORMAT = "cadl-decisions/2"
FORMATS = ("cadl-decisions/1", FORMAT)
KEEP = "keep"
INTEGRITY = ("seq", "prev", "digest")

# the colours of the kinds of party, (light theme, dark theme)
KINDS = {
    "human": ("#1f6feb", "#58a6ff"),
    "llm": ("#8250df", "#bc8cff"),
    "tool": ("#57606a", "#8b949e"),
    "agent": ("#1a7f37", "#3fb950"),
    "organisation": ("#9a6700", "#d29922"),
    "alert": ("#cf222e", "#ff7b72"),
}


def _id(text: str) -> str:
    return text


def colour(kind: str, dark: bool = False) -> str:
    return KINDS.get(kind, KINDS["tool"])[1 if dark else 0]


class Record:
    """The lines of a decision record, without the integrity fields."""

    def __init__(self, header: dict, entries: list, complete: bool, raw: bytes):
        self.header, self.entries, self.complete, self.raw = header, entries, complete, raw
        self.parties = {p["id"]: p for p in header.get("parties") or []
                        if isinstance(p, dict) and isinstance(p.get("id"), str)}
        self.by_id = {e["id"]: e for e in entries if isinstance(e.get("id"), str)}

    def kind(self, pid) -> str:
        return (self.parties.get(pid) or {}).get("kind") or "tool"

    def of_type(self, *types: str) -> list:
        return [e for e in self.entries if e.get("type") in types]

    def basis(self, e: dict, type_: Optional[str] = None) -> list:
        out = [self.by_id[b] for b in e.get("basis") or [] if isinstance(b, str) and b in self.by_id]
        return [x for x in out if type_ is None or x.get("type") == type_]

    def title(self, option) -> str:
        if option is None:
            return "—"
        if option == KEEP:
            return KEEP
        p = self.by_id.get(option) or {}
        return p.get("title") or str(option)


def load(raw) -> Record:
    """Parse a record. Raises ValueError with the reason when it is not one."""
    data = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    lines = [x for x in data.decode("utf-8").splitlines() if x.strip()]
    if not lines:
        raise ValueError("the file is empty")
    objs = []
    for n, line in enumerate(lines, 1):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"line {n} is not JSON ({e})") from None
        if not isinstance(obj, dict):
            raise ValueError(f"line {n} is not a JSON object")
        objs.append({k: v for k, v in obj.items() if k not in INTEGRITY})
    head = objs[0]
    if head.get("decisions_format") not in FORMATS:
        raise ValueError(f"not a decision record (format {head.get('decisions_format')!r}, "
                         f"expected {' or '.join(map(repr, FORMATS))})")
    complete = len(objs) > 1 and "decisions_end" in objs[-1]
    entries = [o for o in (objs[1:-1] if complete else objs[1:]) if isinstance(o.get("id"), str)]
    return Record(head, entries, complete, data)


def check(rec: Record, key: Optional[bytes] = None,
          files: Optional[dict] = None) -> Optional[dict]:
    """The result of ``cadl.decisions.verify``, or None when cadl has no verifier.

    ``files`` maps file names to bytes: the CADL files of the versions, put next to the
    record so that the paths in the ``attachments`` of its ``version`` entries resolve (with
    them the verifier also checks L-9 to L-11 and the outcome of the votes)."""
    try:
        from cadl.decisions import verify
    except ImportError:
        return None
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "decisions.jsonl"
        p.write_bytes(rec.raw)
        for name, data in (files or {}).items():
            name = Path(name).name                 # no directories: a file next to the record
            if name and name != p.name:
                (Path(d) / name).write_bytes(data)
        try:
            return verify(p, key=key)
        except Exception as e:           # a keyed record without its key
            return {"valid": False, "errors": [{"rule": "key", "line": 1, "entry": None,
                                                "what": str(e)}],
                    "findings": [], "not_checked": [], "versions_checked": [],
                    "complete": rec.complete,
                    "run_id": rec.header.get("run_id"), "last_digest": None, "lines": 0,
                    "entries": len(rec.entries), "parties": len(rec.parties),
                    "hash": rec.header.get("hash"), "key_id": rec.header.get("key_id")}


def attached(rec: Record) -> list:
    """The paths of the CADL files that the ``version`` entries attach."""
    return [a["path"] for e in rec.of_type("version") for a in e.get("attachments") or []
            if isinstance(a, dict) and str(a.get("path", "")).endswith(".cadl")]


def findings_by_entry(result: Optional[dict]) -> dict:
    out: dict = {}
    for f in (result or {}).get("findings", []):
        out.setdefault(f.get("entry"), []).append(f["finding"])
    return out


def period(e: dict) -> Optional[int]:
    p = e.get("point")
    return p.get("period") if isinstance(p, dict) and isinstance(p.get("period"), int) else None


def is_amendment_decision(rec: Record, d: dict) -> bool:
    """A decision on amendments, not on a reading of an objective."""
    for o in d.get("options") or []:
        p = rec.by_id.get(o)
        if p is not None and p.get("kind") == "measure":
            return False
    return True


def layers_of(rec: Record, d: dict) -> list:
    """The layers of the changes among the options of a decision."""
    out = []
    for o in d.get("options") or []:
        for c in (rec.by_id.get(o) or {}).get("changes") or []:
            if isinstance(c, dict) and c.get("layer") and c["layer"] not in out:
                out.append(c["layer"])
    return out


def voted_on(d: dict):
    if d.get("choice") != KEEP:
        return d.get("choice")
    return next((o for o in d.get("options") or [] if o != KEEP), None)


def votes_text(votes: list) -> str:
    mark = {"yes": "○", "no": "×"}
    return " ".join(f"{v.get('by')}{mark.get(v.get('value'), '–')}"
                    for v in sorted(votes, key=lambda v: str(v.get("by")))) or "—"


# --- timeline ------------------------------------------------------------------

def timeline(rec: Record, result: Optional[dict] = None) -> list:
    """One row per amendment decision (usually one per period)."""
    found = findings_by_entry(result)
    versions = {e.get("decision"): e.get("version") for e in rec.of_type("version") if e.get("decision")}
    rows, objective = [], None
    for e in rec.entries:
        if e.get("type") == "objective":
            objective = e
            continue
        if e.get("type") != "decision" or not is_amendment_decision(rec, e):
            continue
        # an automatic decision that keeps the rules follows no vote in particular
        on = None if e.get("procedure") == "automatic" and e.get("choice") == KEEP else voted_on(e)
        votes = [v for v in rec.basis(e, "vote") if v.get("proposal") == on] if on else []
        recs = rec.basis(e, "recommendation")
        k = period(e)
        reading = next((x for x in rec.of_type("decision") if period(x) == k
                        and not is_amendment_decision(rec, x)), None)
        rows.append({
            "id": e.get("id"), "period": k if k is not None else len(rows) + 1,
            "layers": layers_of(rec, e),
            "objective": objective.get("id") if objective else None,
            "objective_new": bool(objective and period(objective) == k),
            "reading_by": rec.kind(reading.get("by")) if reading else None,
            "on": rec.title(on) if on else None,
            "votes": votes_text(votes) if votes else None,
            "vote_passes": None if not votes else
            sum(v.get("value") == "yes" for v in votes) * 2 > len(votes),
            "recommends": rec.title(recs[0].get("recommends")) if recs else None,
            "by": e.get("by"), "kind": rec.kind(e.get("by")),
            "choice": rec.title(e.get("choice")),
            "adopted": e.get("choice") not in (None, KEEP),
            "procedure": e.get("procedure"),
            "version": versions.get(e.get("id")) or e.get("result"),
            "reason": e.get("reason") or e.get("rule"),
            "findings": found.get(e.get("id"), []),
        })
    return rows


def series(rec: Record) -> list:
    """The measured values under the version in force, per period."""
    out = []
    for m in rec.of_type("measurement"):
        if m.get("of") != "in_force" or period(m) is None:
            continue
        for name, v in (m.get("values") or {}).items():
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                out.append({"period": period(m), "metric": name, "value": v})
        for pid, vals in (m.get("per_party") or {}).items():
            for name, v in (vals or {}).items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    out.append({"period": period(m), "metric": f"{name} @{pid}", "value": v})
    return out


# --- follow-ups -------------------------------------------------------------------

def _not_shown(result: Optional[dict]) -> set:
    """(entry, follow-up) pairs whose fulfilment the verifier did not accept."""
    out = set()
    for f in (result or {}).get("findings", []):
        if f["finding"] == "fulfilment not shown":
            words = str(f.get("text") or "").split()
            if len(words) > 1 and words[0] == "names":
                out.add((f.get("entry"), words[1]))
    return out


def asks(f: dict) -> str:
    """What fulfils a follow-up, in words (decisions 0.2), or '' for any later entry."""
    fb = f.get("fulfilled_by")
    if not isinstance(fb, dict):
        return ""
    if fb.get("type") != "measurement":
        return str(fb.get("type") or "")
    parts = ["measurement"]
    if fb.get("of"):
        parts.append(f"of {fb['of']}")
    if fb.get("same_method_as"):
        parts.append(f"as {fb['same_method_as']}")
    if fb.get("subjects"):
        parts.append(", ".join(s.replace("metric:", "") for s in fb["subjects"]))
    return " ".join(parts)


def follow_ups(rec: Record, result: Optional[dict] = None) -> list:
    """Each follow-up, who made it, what fulfils it, and whether it was fulfilled."""
    open_ = {f.get("entry") for f in (result or {}).get("findings", [])
             if f["finding"] == "open follow-up"}
    rejected = _not_shown(result)
    rows = []
    for f in rec.of_type("follow_up"):
        fid = f["id"]
        claims = [e for e in rec.entries if fid in (e.get("fulfils") or [])]
        good = [e["id"] for e in claims if (e["id"], fid) not in rejected]
        bad = [e["id"] for e in claims if (e["id"], fid) in rejected]
        status = ("fulfilled" if good else "open" if fid in open_ else
                  "claimed, not shown" if bad else "pending")
        due = f.get("due") or {}
        rows.append({"id": fid, "by": f.get("by"), "kind": rec.kind(f.get("by")),
                     "decision": f.get("decision"), "text": f.get("text"),
                     "due": due.get("period") if isinstance(due, dict) else None,
                     "asks": asks(f), "status": status, "fulfilled by": good,
                     "not shown": bad})
    return rows


# --- evidence by party --------------------------------------------------------------

def evidence_by_party(rec: Record, result: Optional[dict] = None) -> list:
    """The evidence each party gave, counted the same way for a person, an LLM and a tool."""
    missed: dict = {}
    for f in (result or {}).get("findings", []):
        if f["finding"] == "prediction missed":
            missed[f["entry"]] = missed.get(f["entry"], 0) + 1
    rows = []
    fus = follow_ups(rec, result)
    for pid, p in rec.parties.items():
        mine = [e for e in rec.entries if e.get("by") == pid
                and e.get("type") in ("proposal", "recommendation", "decision")]
        if not mine:
            continue
        promised = [f for f in fus if f["by"] == pid]
        layers = p.get("layers")
        rows.append({
            "party": pid, "kind": p.get("kind"), "on_behalf_of": p.get("on_behalf_of"),
            "layers": "any" if layers is None else ", ".join(layers),
            "entries": len(mine),
            "decisions": sum(e.get("type") == "decision" for e in mine),
            "with reason": sum(bool(str(e.get("reason") or "").strip()) for e in mine),
            "with assumptions": sum(bool(e.get("assumptions")) for e in mine),
            "with unknowns": sum(bool(e.get("unknowns")) for e in mine),
            "predictions": sum(len(e.get("predictions") or []) for e in mine),
            "predictions missed": sum(missed.get(e.get("id"), 0) for e in mine),
            "follow-ups": len(promised),
            "fulfilled": sum(f["status"] == "fulfilled" for f in promised),
        })
    return rows


# --- DOT -----------------------------------------------------------------------

def _q(text, width: int = 38, lines: int = 6) -> str:
    text = " ".join(str(text if text is not None else "").split())
    parts = textwrap.wrap(text, width) or [""]
    if len(parts) > lines:
        parts = parts[:lines]
        parts[-1] = parts[-1][: width - 1] + "…"
    return "\\n".join(p.replace("\\", "\\\\").replace('"', '\\"') for p in parts)


def _head(dark: bool) -> list:
    ink = "#e6edf3" if dark else "#1f2328"
    return [
        "digraph G {",
        '  rankdir=TB; bgcolor="transparent"; nodesep=0.3; ranksep=0.45; fontname="Helvetica";',
        f'  node [fontname="Helvetica" fontsize=10 color="{ink}" fontcolor="{ink}" shape=box style=rounded];',
        f'  edge [fontname="Helvetica" fontsize=9 color="{ink}" fontcolor="{ink}"];',
    ]


def _nid(eid: str) -> str:
    return "n_" + "".join(c if c.isalnum() else "_" for c in str(eid))


def _num(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) and not isinstance(v, bool) else str(v)


def label(rec: Record, e: dict, t: Callable = _id) -> str:
    typ, who = e.get("type"), e.get("by")
    head = f"{e.get('id')} · {t(typ)} · {who} ({t(rec.kind(who))})"
    if typ == "objective":
        return _q(head, 44, 2) + "\\n" + _q(e.get("text"), 44, 5)
    if typ == "proposal":
        ch = "; ".join(f"{c.get('parameter')} {c.get('from')} → {c.get('to')}"
                       for c in e.get("changes") or [] if isinstance(c, dict))
        return "\\n".join([_q(head, 40, 2), _q(e.get("title"), 40, 2)] + ([_q(ch, 40, 2)] if ch else []))
    if typ == "measurement":
        vals = list((e.get("values") or {}).items())[:4]
        return _q(head, 40, 2) + "\\n" + _q(", ".join(f"{k} {_num(v)}" for k, v in vals), 40, 3)
    if typ == "check":
        bad = [str(r.get("name")) for r in e.get("results") or [] if r.get("passed") is False]
        return _q(head, 34, 2) + "\\n" + _q((t("failed") + ": " + ", ".join(bad)) if bad
                                            else t("all passed"), 34, 2)
    if typ == "request":
        return _q(head, 40, 2) + "\\n" + _q(e.get("question"), 40, 3)
    if typ == "recommendation":
        return (_q(head, 40, 2) + "\\n" + _q(t("recommends") + " " + rec.title(e.get("recommends"))
                                            + ": " + str(e.get("reason") or ""), 40, 5))
    if typ == "decision":
        lines = [_q(head, 44, 2),
                 _q(f"{t('chose')} {rec.title(e.get('choice'))} ({e.get('procedure')})", 44, 2),
                 _q(t("Reason") + ": " + str(e.get("reason") or e.get("rule") or "—"), 44, 8)]
        extra = [f"{t(f)} {len(e[f])}" for f in ("assumptions", "unknowns", "predictions") if e.get(f)]
        if extra:
            lines.append(_q(" · ".join(extra), 44, 1))
        return "\\n".join(lines)
    if typ == "follow_up":
        return _q(head, 34, 2) + "\\n" + _q(e.get("text"), 34, 3)
    if typ == "version":
        return _q(f"{e.get('id')} · {t('version')} {e.get('version')}", 34, 2)
    return _q(head + ": " + str(e.get("text") or ""), 40, 4)


def trail_dot(rec: Record, did: str, dark: bool = False, result: Optional[dict] = None,
              t: Callable = _id) -> str:
    """One decision with the entries it rests on and what it led to."""
    d = rec.by_id[did]
    flags = findings_by_entry(result).get(did, [])
    shown = [b for b in rec.basis(d) if b.get("type") != "vote"]
    votes = rec.basis(d, "vote")
    after = [e for e in rec.entries if e.get("decision") == did
             and e.get("type") in ("follow_up", "version")]
    fus = {f["id"]: f for f in follow_ups(rec, result)}
    later = []
    for f in [e for e in after if e.get("type") == "follow_up"]:
        row = fus.get(f["id"]) or {}
        for x in row.get("fulfilled by", []) + row.get("not shown", []):
            if x in rec.by_id and rec.by_id[x] not in later:
                later.append(rec.by_id[x])
    lines = _head(dark)
    for e in shown + after + later:
        shape = {"follow_up": "note", "version": "folder"}.get(e.get("type"), "box")
        lines.append(f'  {_nid(e["id"])} [label="{label(rec, e, t)}" color="{colour(rec.kind(e.get("by")), dark)}" '
                     f'penwidth=1.6 shape={shape} style="{"rounded" if shape == "box" else "solid"}"];')
    proposals = [x for x in shown if x.get("type") == "proposal"]
    for p in proposals:
        vs = [v for v in votes if v.get("proposal") == p["id"]]
        if vs:
            vid = _nid(p["id"] + "_votes")
            passes = sum(v.get("value") == "yes" for v in vs) * 2 > len(vs)
            lines.append(f'  {vid} [label="{t("votes")} {votes_text(vs)}" shape=box style=solid '
                         f'fontsize=9 color="{colour("agent" if passes else "alert", dark)}"];')
            lines.append(f"  {_nid(p['id'])} -> {vid} [arrowhead=none];")
            lines.append(f"  {vid} -> {_nid(did)};")
        else:
            lines.append(f"  {_nid(p['id'])} -> {_nid(did)};")
    linked = set()
    for x in shown:
        if x.get("type") in ("measurement", "check"):
            for p in proposals:
                if p["id"] in (x.get("of"), x.get("proposal")):
                    lines.append(f"  {_nid(p['id'])} -> {_nid(x['id'])} [style=dashed arrowhead=none];")
                    linked.add(x["id"])
    for x in shown:
        if x.get("type") == "proposal" or x["id"] in linked:
            continue
        style = "dashed" if x.get("type") in ("recommendation", "request") else "solid"
        lines.append(f"  {_nid(x['id'])} -> {_nid(did)} [style={style}];")
    alert = "override" in flags or "against recommendation" in flags
    note = ("\\n" + _q("⚠ " + ", ".join(t(f) for f in flags), 44, 2)) if flags else ""
    lines.append(f'  {_nid(did)} [label="{label(rec, d, t)}{note}" penwidth=2.6 '
                 f'color="{colour("alert" if alert else rec.kind(d.get("by")), dark)}"];')
    for e in after:
        lines.append(f"  {_nid(did)} -> {_nid(e['id'])};")
    for fid, row in fus.items():
        if rec.by_id.get(fid, {}).get("decision") != did:
            continue
        for x in row["fulfilled by"]:
            lines.append(f'  {_nid(fid)} -> {_nid(x)} [label="{t("fulfils")}" fontsize=9 dir=back];')
        for x in row["not shown"]:
            lines.append(f'  {_nid(fid)} -> {_nid(x)} [label="{t("fulfilment not shown")}" '
                         f'fontsize=9 dir=back style=dashed color="{colour("alert", dark)}"];')
    lines.append("}")
    return "\n".join(lines)


GSN_SHAPE = {"goal": "box", "strategy": "parallelogram", "solution": "circle",
             "context": "box", "assumption": "ellipse", "justification": "ellipse"}


def gsn(rec: Record, t: Callable = _id, result: Optional[dict] = None) -> list:
    """The GSN argument derived from the record, as Appendix L, L.8 maps it."""
    nodes = [{"id": "G0", "type": "goal", "by": "tool", "ref": None, "children": [], "context": [],
              "text": t("The system keeps meeting the objectives of its owner")}]
    count = {"G": 0, "S": 0, "Sn": 0, "C": 0, "A": 0, "J": 0}
    index = {"G0": nodes[0]}

    def add(kind, prefix, text, parent, link="children", by="tool", ref=None):
        count[prefix] += 1
        nid = f"{prefix}{count[prefix]}"
        node = {"id": nid, "type": kind, "text": text, "by": by, "ref": ref,
                "children": [], "context": []}
        nodes.append(node)
        index[nid] = node
        index[parent][link].append(nid)
        return nid

    goal = None
    fulfilled = {r["id"]: r for r in follow_ups(rec, result)}
    for e in rec.entries:
        typ = e.get("type")
        if typ == "objective":
            goal = add("goal", "G", f"{e.get('id')}: {e.get('text')}", "G0",
                       by=rec.kind(e.get("by")), ref=e.get("id"))
        elif typ == "decision" and goal is not None:
            kind = rec.kind(e.get("by"))
            s = add("strategy", "S", f"{e.get('id')}: {t('chose')} {rec.title(e.get('choice'))} "
                                     f"({e.get('procedure')})", goal, by=kind, ref=e.get("id"))
            for b in rec.basis(e):
                if b.get("type") in ("measurement", "check"):
                    add("solution", "Sn", b.get("id"), s, ref=b.get("id"))
                elif b.get("type") == "recommendation":
                    add("context", "C", f"{t('recommends')} {rec.title(b.get('recommends'))}", s,
                        "context", by=rec.kind(b.get("by")), ref=b.get("id"))
            if e.get("reason") or e.get("rule"):
                add("justification", "J", e.get("reason") or e.get("rule"), s, "context", by=kind)
            for a in e.get("assumptions") or []:
                add("assumption", "A", a, s, "context", by=kind)
            for f in rec.of_type("follow_up"):
                if f.get("decision") == e.get("id"):
                    g = add("goal", "G", f"{f.get('id')}: {f.get('text')}", s,
                            by=rec.kind(f.get("by")), ref=f.get("id"))
                    row = fulfilled.get(f.get("id")) or {}
                    for x in row.get("fulfilled by", []):
                        add("solution", "Sn", x, g, by=rec.kind((rec.by_id.get(x) or {}).get("by")), ref=x)
    return nodes


def gsn_dot(nodes: list, dark: bool = False, focus: Optional[str] = None, width: int = 30) -> str:
    by_id = {n["id"]: n for n in nodes}
    parent = {c: n["id"] for n in nodes for c in n["children"] + n["context"]}
    keep = set(by_id)
    if focus:
        keep, stack = set(), [n["id"] for n in nodes if n.get("ref") == focus]
        while stack:
            nid = stack.pop()
            if nid not in keep:
                keep.add(nid)
                stack += by_id[nid]["children"] + by_id[nid]["context"]
        for nid in list(keep):
            while nid in parent:
                nid = parent[nid]
                keep.add(nid)
    ink = "#e6edf3" if dark else "#1f2328"
    lines = _head(dark)
    for n in nodes:
        if n["id"] not in keep:
            continue
        col = colour(n["by"], dark) if n["by"] != "tool" else ink
        letter = {"assumption": "\\nA", "justification": "\\nJ"}.get(n["type"], "")
        small = n["type"] == "solution"
        lines.append(f'  {n["id"]} [label="{n["id"]}: {_q(n["text"], 16 if small else width, 2 if small else 5)}'
                     f'{letter}" shape={GSN_SHAPE[n["type"]]} style="{"rounded" if n["type"] == "context" else "solid"}" '
                     f'color="{col}" penwidth=1.6];')
    for n in nodes:
        for c in n["children"]:
            if n["id"] in keep and c in keep:
                lines.append(f"  {n['id']} -> {c};")
        for c in n["context"]:
            if n["id"] in keep and c in keep:
                lines.append(f"  {n['id']} -> {c} [style=dashed arrowhead=empty];")
    lines.append("}")
    return "\n".join(lines)
