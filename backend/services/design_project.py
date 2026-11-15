"""A project of the Designer as one zip file: the design and what was done with it.

A browser session keeps the saved versions, the decision record, the
constitution, the entries of the STPA table and the record of the checks only
while it lasts. `build` puts them with the design into one zip, which `read`
takes back:

    manifest.json        format, versions of the Explorer and of cadl, time
    design.cadl          the design in the editor
    versions.json        the saved versions, in order: name, time, file
    versions/NN_<name>.cadl
    decisions.json       the decision record (and decisions.md to read)
    constitution.yaml    the constitution in use, if any
    uca.json             the entries of the table of unsafe control actions
    runs.json            the record of the checks, with the hash of each source

`read` refuses a zip that is not such a project, that is larger than the
limits, or whose design or versions are larger than the editor takes.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime
from typing import Optional

from backend.services.cadl_service import MAX_SOURCE_CHARS
from backend.version import __version__

FORMAT = "cadl-explorer-project"
FORMAT_VERSION = 1
MAX_ZIP_BYTES = 8 << 20
MAX_MEMBER_BYTES = 4 << 20
MAX_MEMBERS = 200


class ProjectError(ValueError):
    """A file that cannot be opened as a project; the message says why."""


def _stem(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(name)).strip("._")[:60] or "version"


def build(name: str, source: str, versions: list, decisions: list, constitution: Optional[str],
          constitution_name: Optional[str], uca: dict, runs: list, cadl: Optional[str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("manifest.json", json.dumps({
            "format": FORMAT, "format_version": FORMAT_VERSION, "name": name,
            "explorer": __version__, "cadl": cadl,
            "saved": datetime.now().isoformat(timespec="seconds"),
        }, indent=2, ensure_ascii=False))
        zf.writestr("design.cadl", source)
        listed = []
        for k, v in enumerate(versions, 1):
            path = f"versions/{k:02d}_{_stem(v['name'])}.cadl"
            zf.writestr(path, v["source"])
            listed.append({"name": v["name"], "time": v.get("time", ""), "file": path})
        zf.writestr("versions.json", json.dumps(listed, indent=2, ensure_ascii=False))
        zf.writestr("decisions.json", json.dumps(decisions, indent=2, ensure_ascii=False))
        zf.writestr("decisions.md", "# Decisions\n\n" + "".join(
            f"- {d.get('time', '')} {d.get('change', '')}"
            + (f" ({d['layer']})" if d.get("layer") else "")
            + (f" — {d['reason']}" if d.get("reason") else "") + "\n" for d in decisions))
        if constitution:
            zf.writestr("constitution.yaml", constitution)
        zf.writestr("uca.json", json.dumps({"entries": uca, "constitution_name": constitution_name},
                                           indent=2, ensure_ascii=False))
        zf.writestr("runs.json", json.dumps(runs, indent=2, ensure_ascii=False))
    return buffer.getvalue()


def is_project(data: bytes) -> bool:
    return data[:4] == b"PK\x03\x04"


def _text(zf: zipfile.ZipFile, name: str, limit: int = MAX_MEMBER_BYTES) -> Optional[str]:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > limit:
        raise ProjectError(f"`{name}` is larger than this app takes.")
    try:
        return zf.read(info).decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ProjectError(f"`{name}` is not UTF-8 text.") from None


def _json(zf: zipfile.ZipFile, name: str, default):
    text = _text(zf, name)
    if text is None:
        return default
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        raise ProjectError(f"`{name}` is not JSON.") from None


def _design(text: Optional[str], name: str) -> str:
    if text is None:
        raise ProjectError(f"`{name}` is missing.")
    if len(text) > MAX_SOURCE_CHARS:
        raise ProjectError(f"`{name}` has {len(text):,} characters; the limit is {MAX_SOURCE_CHARS:,}.")
    return text


def read(data: bytes) -> dict:
    """{"name", "source", "versions", "decisions", "constitution", "constitution_name",
    "uca", "runs", "manifest"} of a project zip, or ProjectError."""
    if len(data) > MAX_ZIP_BYTES:
        raise ProjectError(f"The file is larger than {MAX_ZIP_BYTES >> 20} MB.")
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ProjectError("The file is not a zip archive.") from None
    with zf:
        if len(zf.infolist()) > MAX_MEMBERS:
            raise ProjectError(f"The archive has more than {MAX_MEMBERS} files.")
        manifest = _json(zf, "manifest.json", None)
        if not isinstance(manifest, dict) or manifest.get("format") != FORMAT:
            raise ProjectError("This zip is not a project of the CADL Explorer (no manifest.json).")
        if int(manifest.get("format_version") or 0) > FORMAT_VERSION:
            raise ProjectError("The project was saved by a later version of the Explorer.")
        source = _design(_text(zf, "design.cadl"), "design.cadl")
        versions = []
        for v in _json(zf, "versions.json", []):
            if not isinstance(v, dict) or not isinstance(v.get("file"), str):
                raise ProjectError("`versions.json` does not list the versions.")
            versions.append({"name": str(v.get("name") or v["file"]), "time": str(v.get("time") or ""),
                             "source": _design(_text(zf, v["file"]), v["file"])})
        decisions = [d for d in _json(zf, "decisions.json", []) if isinstance(d, dict)]
        for d in decisions:
            for key in ("time", "change", "layer", "reason"):
                d[key] = str(d.get(key) or "")
        uca = _json(zf, "uca.json", {})
        runs = [r for r in _json(zf, "runs.json", []) if isinstance(r, dict) and "check" in r
                and "source_sha256" in r]
        return {
            "name": str(manifest.get("name") or ""), "manifest": manifest, "source": source,
            "versions": versions, "decisions": decisions,
            "constitution": _text(zf, "constitution.yaml"),
            "constitution_name": (uca.get("constitution_name") if isinstance(uca, dict) else None),
            "uca": (uca.get("entries") if isinstance(uca, dict) and isinstance(uca.get("entries"), dict)
                    else {}),
            "runs": runs,
        }
