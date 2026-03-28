"""Diff service — structured 4-type diff API for the governance pipeline.

Provides:
  diff_cadl()   — CADL YAML diff
  diff_ir()     — per-layer IR diff
  diff_config() — Unity config diff
  diff_result() — evaluation result diff
"""

import difflib
import json
import yaml
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
