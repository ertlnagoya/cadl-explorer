"""
Structured diff engine for CADL, IR, and Unity configs.

Returns diffs as lists of (tag, line) tuples where tag is:
  "same", "add", "remove", "change", "header"
"""

import difflib
import json
import yaml


def _unified_diff_lines(text_a: str, text_b: str, label_a: str, label_b: str):
    """Compute unified diff and return tagged lines."""
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


def compute_cadl_diff(config_a, config_b):
    """CADL YAML diff between two configs."""
    yaml_a = yaml.dump(config_a.to_dict(), default_flow_style=False, sort_keys=False)
    yaml_b = yaml.dump(config_b.to_dict(), default_flow_style=False, sort_keys=False)
    return _unified_diff_lines(yaml_a, yaml_b, config_a.name, config_b.name)


def compute_ir_diff(ir_a, ir_b):
    """Per-layer IR diff. Returns dict of layer_name -> tagged lines."""
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
        result[layer_label] = _unified_diff_lines(ja, jb, ir_a.name, ir_b.name)
    return result


def compute_config_diff(unity_a: dict, unity_b: dict, label_a: str, label_b: str):
    """Unity config JSON diff."""
    ja = json.dumps(unity_a, indent=2, sort_keys=True)
    jb = json.dumps(unity_b, indent=2, sort_keys=True)
    return _unified_diff_lines(ja, jb, label_a, label_b)


def tagged_lines_to_html(tagged_lines):
    """Convert tagged diff lines to HTML with color coding."""
    if not tagged_lines:
        return '<p style="color: gray;">No differences</p>'

    colors = {
        "add": "#22863a",
        "remove": "#cb2431",
        "change": "#b08800",
        "header": "#6f42c1",
        "same": "#586069",
    }
    bg_colors = {
        "add": "#f0fff4",
        "remove": "#ffeef0",
        "change": "#fffbdd",
        "header": "#f5f0ff",
        "same": "transparent",
    }

    html_parts = ['<div style="font-family: monospace; font-size: 13px; line-height: 1.5;">']
    for tag, line in tagged_lines:
        escaped = (
            line.replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
        )
        color = colors.get(tag, "#333")
        bg = bg_colors.get(tag, "transparent")
        html_parts.append(
            f'<div style="color: {color}; background: {bg}; padding: 1px 8px;">'
            f'{escaped}</div>'
        )
    html_parts.append("</div>")
    return "\n".join(html_parts)
