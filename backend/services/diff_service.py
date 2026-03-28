"""Diff service — structured 4-type diff API for the governance pipeline.

Provides:
  Syntactic diffs:
    diff_cadl()   — CADL YAML diff
    diff_ir()     — per-layer IR diff
    diff_config() — Unity config diff
    diff_result() — evaluation result diff

  Semantic diffs (with human-readable interpretation):
    semantic_diff_cadl()   — institutional design diff
    semantic_diff_ir()     — governance structure diff
    semantic_diff_config() — execution settings diff
    semantic_diff_result() — behavioral/performance diff with meaning labels
"""

import difflib
import json
import yaml
import numpy as np
from dataclasses import dataclass, field
from typing import List, Tuple, Dict, Optional


@dataclass
class DiffResult:
    """Structured diff result with tagged lines and rendering."""
    category: str  # "cadl", "ir", "config", "result"
    label_a: str = ""
    label_b: str = ""
    tagged_lines: List[Tuple[str, str]] = field(default_factory=list)

    @property
    def has_changes(self) -> bool:
        return any(tag in ("add", "remove") for tag, _ in self.tagged_lines)

    def to_html(self) -> str:
        """Render as color-coded HTML."""
        if not self.tagged_lines:
            return '<p style="color: gray;">No differences</p>'

        colors = {
            "add": "#22863a", "remove": "#cb2431", "change": "#b08800",
            "header": "#6f42c1", "same": "#586069",
        }
        bg_colors = {
            "add": "#f0fff4", "remove": "#ffeef0", "change": "#fffbdd",
            "header": "#f5f0ff", "same": "transparent",
        }

        parts = ['<div style="font-family: monospace; font-size: 13px; line-height: 1.5;">']
        for tag, line in self.tagged_lines:
            escaped = line.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            color = colors.get(tag, "#333")
            bg = bg_colors.get(tag, "transparent")
            parts.append(f'<div style="color: {color}; background: {bg}; padding: 1px 8px;">{escaped}</div>')
        parts.append("</div>")
        return "\n".join(parts)


def _unified_diff(text_a: str, text_b: str, label_a: str, label_b: str) -> List[Tuple[str, str]]:
    """Compute unified diff and return tagged (tag, line) tuples."""
    lines_a = text_a.splitlines(keepends=True)
    lines_b = text_b.splitlines(keepends=True)
    diff = difflib.unified_diff(lines_a, lines_b, fromfile=label_a, tofile=label_b)

    tagged = []
    for line in diff:
        line = line.rstrip("\n")
        if line.startswith("+++") or line.startswith("---"):
            tagged.append(("header", line))
        elif line.startswith("@@"):
            tagged.append(("header", line))
        elif line.startswith("+"):
            tagged.append(("add", line))
        elif line.startswith("-"):
            tagged.append(("remove", line))
        else:
            tagged.append(("same", line))
    return tagged


def diff_cadl(config_a, config_b) -> DiffResult:
    """CADL YAML diff between two CADLMotivationConfig objects."""
    yaml_a = yaml.dump(config_a.to_dict(), default_flow_style=False, sort_keys=False)
    yaml_b = yaml.dump(config_b.to_dict(), default_flow_style=False, sort_keys=False)
    tagged = _unified_diff(yaml_a, yaml_b, config_a.name, config_b.name)
    return DiffResult(category="cadl", label_a=config_a.name, label_b=config_b.name, tagged_lines=tagged)


def diff_ir(ir_a, ir_b) -> Dict[str, DiffResult]:
    """Per-layer IR diff. Returns dict of layer_name -> DiffResult."""
    dict_a = ir_a.to_dict()
    dict_b = ir_b.to_dict()

    result = {}
    for layer_key, layer_label in [
        ("layer1_institution", "Layer 1: Institution"),
        ("layer2_protocol", "Layer 2: Protocol"),
        ("layer3_algorithm", "Layer 3: Algorithm"),
    ]:
        ja = json.dumps(dict_a.get(layer_key, {}), indent=2, sort_keys=True)
        jb = json.dumps(dict_b.get(layer_key, {}), indent=2, sort_keys=True)
        tagged = _unified_diff(ja, jb, ir_a.name, ir_b.name)
        result[layer_label] = DiffResult(
            category="ir", label_a=ir_a.name, label_b=ir_b.name, tagged_lines=tagged,
        )
    return result


def diff_config(unity_a: dict, unity_b: dict, label_a: str, label_b: str) -> DiffResult:
    """Unity config JSON diff."""
    ja = json.dumps(unity_a, indent=2, sort_keys=True)
    jb = json.dumps(unity_b, indent=2, sort_keys=True)
    tagged = _unified_diff(ja, jb, label_a, label_b)
    return DiffResult(category="config", label_a=label_a, label_b=label_b, tagged_lines=tagged)


def diff_result(eval_a: dict, eval_b: dict, label_a: str = "A", label_b: str = "B") -> DiffResult:
    """Evaluation result diff — compares two evaluation dicts."""
    ja = json.dumps(eval_a, indent=2, sort_keys=True)
    jb = json.dumps(eval_b, indent=2, sort_keys=True)
    tagged = _unified_diff(ja, jb, label_a, label_b)
    return DiffResult(category="result", label_a=label_a, label_b=label_b, tagged_lines=tagged)


# ── Semantic diff: meaning-labeled diff results ─────────────────────


@dataclass
class SemanticLabel:
    """A human-readable interpretation of a diff."""
    category: str       # "institution", "protocol", "algorithm", "performance", etc.
    field: str          # "rho", "throughput", etc.
    direction: str      # "increased", "decreased", "changed", "added", "removed"
    magnitude: str      # "significant", "moderate", "minimal"
    summary: str        # e.g. "Throughput decreased by 12.2%"


@dataclass
class SemanticDiffResult:
    """Diff with semantic interpretation."""
    category: str
    label_a: str = ""
    label_b: str = ""
    syntactic: DiffResult = field(default_factory=lambda: DiffResult(category=""))
    labels: List[SemanticLabel] = field(default_factory=list)
    summary: str = ""

    def to_html(self) -> str:
        """Render semantic diff as HTML with labels."""
        parts = []
        if self.labels:
            parts.append('<div style="margin-bottom: 12px;">')
            for lbl in self.labels:
                icon = {"increased": "arrow_upward", "decreased": "arrow_downward",
                        "changed": "swap_horiz", "added": "add", "removed": "remove"}.get(lbl.direction, "")
                color = {"increased": "#22863a", "decreased": "#cb2431",
                         "changed": "#b08800"}.get(lbl.direction, "#586069")
                parts.append(
                    f'<div style="padding: 4px 12px; margin: 2px 0; border-left: 3px solid {color}; '
                    f'background: #f8f9fa; font-size: 13px;">'
                    f'<strong>{lbl.category}</strong>: {lbl.summary}</div>'
                )
            parts.append('</div>')
        if self.summary:
            parts.append(f'<div style="padding: 8px 12px; background: #e8f4fd; border-radius: 4px; '
                         f'margin-bottom: 8px; font-size: 13px;">{self.summary}</div>')
        parts.append(self.syntactic.to_html())
        return "\n".join(parts)


def _classify_magnitude(ratio: float) -> str:
    if abs(ratio) > 0.15:
        return "significant"
    elif abs(ratio) > 0.05:
        return "moderate"
    return "minimal"


def _field_diff(d_a: dict, d_b: dict, path: str = "") -> List[Tuple[str, object, object]]:
    """Recursively find differing fields. Returns [(path, val_a, val_b), ...]."""
    diffs = []
    all_keys = sorted(set(list(d_a.keys()) + list(d_b.keys())))
    for key in all_keys:
        full_path = f"{path}.{key}" if path else key
        va = d_a.get(key)
        vb = d_b.get(key)
        if isinstance(va, dict) and isinstance(vb, dict):
            diffs.extend(_field_diff(va, vb, full_path))
        elif va != vb:
            diffs.append((full_path, va, vb))
    return diffs


def semantic_diff_cadl(cadl_a: dict, cadl_b: dict, label_a: str = "A", label_b: str = "B") -> SemanticDiffResult:
    """Semantic diff of CADL configs — institutional design changes."""
    ya = yaml.dump(cadl_a or {}, default_flow_style=False, sort_keys=False)
    yb = yaml.dump(cadl_b or {}, default_flow_style=False, sort_keys=False)
    syntactic = DiffResult(
        category="cadl", label_a=label_a, label_b=label_b,
        tagged_lines=_unified_diff(ya, yb, label_a, label_b),
    )

    labels = []
    field_diffs = _field_diff(cadl_a or {}, cadl_b or {})
    CADL_FIELD_LABELS = {
        "sos_type": "SoS governance type",
        "governance.alpha": "autonomy level (alpha)",
        "governance.beta": "centralization level (beta)",
        "governance.lambda": "exploration probability (lambda)",
        "motivation.governance.model": "motivation model",
        "motivation.governance.rho": "motivation sensitivity (rho)",
        "motivation.agent.profile": "agent motivation profile",
    }
    for path, va, vb in field_diffs:
        desc = CADL_FIELD_LABELS.get(path, path)
        labels.append(SemanticLabel(
            category="Institution Design", field=path, direction="changed",
            magnitude="significant" if path in CADL_FIELD_LABELS else "minimal",
            summary=f"{desc}: {va} -> {vb}",
        ))

    summary = f"{len(field_diffs)} institutional parameter(s) changed" if field_diffs else "No institutional changes"
    return SemanticDiffResult(
        category="cadl", label_a=label_a, label_b=label_b,
        syntactic=syntactic, labels=labels, summary=summary,
    )


def semantic_diff_ir(ir_a: dict, ir_b: dict, label_a: str = "A", label_b: str = "B") -> SemanticDiffResult:
    """Semantic diff of IR — governance structure changes per layer."""
    ja = json.dumps(ir_a or {}, indent=2, sort_keys=True)
    jb = json.dumps(ir_b or {}, indent=2, sort_keys=True)
    syntactic = DiffResult(
        category="ir", label_a=label_a, label_b=label_b,
        tagged_lines=_unified_diff(ja, jb, label_a, label_b),
    )

    labels = []
    LAYER_NAMES = {
        "layer1_institution": "Institution (WHO decides)",
        "layer2_protocol": "Protocol (HOW coordination flows)",
        "layer3_algorithm": "Algorithm (WHAT computation runs)",
    }
    for layer_key, layer_desc in LAYER_NAMES.items():
        la = (ir_a or {}).get(layer_key, {})
        lb = (ir_b or {}).get(layer_key, {})
        diffs = _field_diff(la, lb)
        if diffs:
            changed_fields = [d[0] for d in diffs]
            labels.append(SemanticLabel(
                category=layer_desc, field=layer_key, direction="changed",
                magnitude="significant" if len(diffs) > 2 else "moderate",
                summary=f"{len(diffs)} field(s) changed: {', '.join(changed_fields[:3])}",
            ))

    summary = f"Governance structure differs in {len(labels)} layer(s)" if labels else "Identical governance structure"
    return SemanticDiffResult(
        category="ir", label_a=label_a, label_b=label_b,
        syntactic=syntactic, labels=labels, summary=summary,
    )


def semantic_diff_config(config_a: dict, config_b: dict, label_a: str = "A", label_b: str = "B") -> SemanticDiffResult:
    """Semantic diff of Unity configs — execution settings changes."""
    ja = json.dumps(config_a or {}, indent=2, sort_keys=True)
    jb = json.dumps(config_b or {}, indent=2, sort_keys=True)
    syntactic = DiffResult(
        category="config", label_a=label_a, label_b=label_b,
        tagged_lines=_unified_diff(ja, jb, label_a, label_b),
    )

    labels = []
    KEY_SECTIONS = ["simulatorConfig", "motivationConfig", "communicationSetup"]
    for section in KEY_SECTIONS:
        sa = (config_a or {}).get(section, {})
        sb = (config_b or {}).get(section, {})
        if isinstance(sa, dict) and isinstance(sb, dict):
            diffs = _field_diff(sa, sb)
            if diffs:
                labels.append(SemanticLabel(
                    category=f"Config: {section}", field=section, direction="changed",
                    magnitude="significant" if len(diffs) > 3 else "moderate",
                    summary=f"{len(diffs)} setting(s) changed in {section}",
                ))

    summary = f"{len(labels)} config section(s) differ" if labels else "Identical configs"
    return SemanticDiffResult(
        category="config", label_a=label_a, label_b=label_b,
        syntactic=syntactic, labels=labels, summary=summary,
    )


def semantic_diff_result(eval_a: dict, eval_b: dict, label_a: str = "A", label_b: str = "B") -> SemanticDiffResult:
    """Semantic diff of results — behavioral/performance changes with meaning labels."""
    ja = json.dumps(eval_a or {}, indent=2, sort_keys=True)
    jb = json.dumps(eval_b or {}, indent=2, sort_keys=True)
    syntactic = DiffResult(
        category="result", label_a=label_a, label_b=label_b,
        tagged_lines=_unified_diff(ja, jb, label_a, label_b),
    )

    labels = []
    METRIC_LABELS = {
        "throughput": ("Performance", "Throughput (total deliveries)"),
        "autonomy": ("Governance", "System autonomy"),
        "fairness": ("Equity", "Delivery fairness"),
    }

    for key, (cat, desc) in METRIC_LABELS.items():
        va = (eval_a or {}).get(key, 0)
        vb = (eval_b or {}).get(key, 0)
        if va == 0 and vb == 0:
            continue
        delta = vb - va
        ratio = delta / max(abs(va), 1e-6)
        direction = "increased" if delta > 0.01 else "decreased" if delta < -0.01 else "unchanged"
        if direction == "unchanged":
            continue
        magnitude = _classify_magnitude(ratio)
        pct = abs(ratio * 100)
        labels.append(SemanticLabel(
            category=cat, field=key, direction=direction, magnitude=magnitude,
            summary=f"{desc} {direction} by {pct:.1f}% ({va:.2f} -> {vb:.2f})",
        ))

    # Build natural language summary
    summaries = [lbl.summary for lbl in labels]
    if summaries:
        summary = "Behavioral changes: " + "; ".join(summaries)
    else:
        summary = "No significant behavioral differences"

    return SemanticDiffResult(
        category="result", label_a=label_a, label_b=label_b,
        syntactic=syntactic, labels=labels, summary=summary,
    )


# ── Backward-compat aliases ─────────────────────────────────────────

def compute_cadl_diff(config_a, config_b):
    """Backward-compat: returns tagged_lines list."""
    return diff_cadl(config_a, config_b).tagged_lines


def compute_ir_diff(ir_a, ir_b):
    """Backward-compat: returns dict of layer_name -> tagged_lines list."""
    return {k: v.tagged_lines for k, v in diff_ir(ir_a, ir_b).items()}


def compute_config_diff(unity_a, unity_b, label_a, label_b):
    """Backward-compat: returns tagged_lines list."""
    return diff_config(unity_a, unity_b, label_a, label_b).tagged_lines


def tagged_lines_to_html(tagged_lines):
    """Backward-compat: render tagged lines list to HTML."""
    dr = DiffResult(category="compat", tagged_lines=tagged_lines)
    return dr.to_html()
