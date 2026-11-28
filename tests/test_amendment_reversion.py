"""A reversion: one new version that restores the values of an earlier version
(`amendment.reverts`, cadl 0.3.44), declared in place, gated by the constitution with
the earlier version as the base, checked with `amend-diff --base`, and proposed from
the workspace's git history."""
import shutil

import pytest

pytest.importorskip("cadl")

from backend.services import design_amend as amend
from backend.services import design_service as ds
from backend.services import design_workspace as workspace

# Robot Town as version "2", with hold_limit and block_k marked for the council.
V2 = ((ds.DESIGNS_DIR / "amendment_robot_town_v1.cadl").read_text(encoding="utf-8")
      .replace('  version: "1"\n', '  version: "2"\n', 1)
      .replace("            deadline: 60s\n", "            deadline: 60s   # @amend:hold_limit\n", 1)
      .replace('rule: "ROBOT.blocking >= 3 AND state IN [Holding, Overstay]"\n',
               'rule: "ROBOT.blocking >= 3 AND state IN [Holding, Overstay]"   # @amend:block_k\n', 1))
CONSTITUTION = """\
trial_periods: 3
amendable:
  hold_limit:
    layer: rule
    min: 10
    max: 120
    step: 10
  block_k:
    layer: rule
    min: 1
    max: 4
    step: 1
"""

needs_base = pytest.mark.skipif(
    not ds.amend_diff_takes_base(),
    reason="the installed cadl cannot check a reversion (amend-diff --base, cadl-lang 0.3.44)")


def _amended(source, new, version, previous, period):
    return amend.declare(amend.set_values(source, new), new, version, previous, period)


# v2 → v2.1 → v2.2 → v2.3: hold_limit 60 → 70 → 80, then block_k 3 → 4
V2_1 = _amended(V2, {"hold_limit": 70}, "2.1", "2", 4)
V2_2 = _amended(V2_1, {"hold_limit": 80}, "2.2", "2.1", 7)
V2_3 = _amended(V2_2, {"block_k": 4}, "2.3", "2.2", 10)
V2_4 = amend.declare(amend.set_values(V2_3, {"hold_limit": 60, "block_k": 3}),
                     {"hold_limit": 60, "block_k": 3}, "2.4", "2.3", 13, reverts="2")


@pytest.fixture
def ws(tmp_path, monkeypatch):
    monkeypatch.setenv("CADL_WORKSPACE", str(tmp_path / "ws"))
    monkeypatch.delenv("CADL_WORKSPACE_GIT", raising=False)
    monkeypatch.delenv("CADL_TRIAL_COMMAND", raising=False)
    return tmp_path / "ws"


def _git_or_skip():
    if shutil.which("git") is None:
        pytest.skip("git is not installed")


def _chain(ws, monkeypatch):
    """The workspace design adopted as v2, v2.1, v2.2 and v2.3, committed to git."""
    _git_or_skip()
    monkeypatch.setenv("CADL_WORKSPACE_GIT", "1")
    workspace.root()
    (ws / "town.constitution.yaml").write_text(CONSTITUTION, encoding="utf-8")
    workspace.write("town", V2, note="version 2")
    for values in ({"hold_limit": 70}, {"hold_limit": 80}, {"block_k": 4}):
        p = workspace.add_amendment("town", values, "try it")
        assert p["constitution_allows"], p["constitution_problems"]
        workspace.apply_proposal("town", p["proposal_id"], reason="the council adopted it")
    assert ds.yaml.safe_load(workspace.read("town"))["sos"]["version"] == "2.3"


# ── The declaration ─────────────────────────────────────────────────


def test_declare_writes_reverts_after_previous():
    sos = ds.yaml.safe_load(V2_4)["sos"]
    assert sos["version"] == "2.4"
    assert sos["amendment"]["previous"] == "2.3" and sos["amendment"]["reverts"] == "2"
    assert sos["amendment"]["effective"] == {"period": 13}
    lines = V2_4.splitlines()
    at = next(i for i, l in enumerate(lines) if l.strip().startswith("previous:"))
    assert lines[at + 1] == '    reverts: "2"'           # right after previous:, in its style
    assert "# seconds a robot may stop on the road" in V2_4      # comments stay
    assert amend.declared_version(V2_4)["reverts"] == "2"
    assert amend.declared_version(V2_3)["reverts"] is None
    # an existing `reverts:` is replaced, not repeated
    again = amend.declare(V2_4, {}, "2.5", "2.4", reverts="2.1")
    assert again.count("reverts:") == 1 and amend.declared_version(again)["reverts"] == "2.1"
    # without `previous:` it goes right under `amendment:`, before effective
    first = amend.declare(V2, {}, "3", reverts="1", effective_period=2)
    block = first.splitlines()
    at = next(i for i, l in enumerate(block) if l.strip() == "amendment:")
    assert block[at + 1] == '    reverts: "1"'
    assert ds.yaml.safe_load(first)["sos"]["amendment"]["effective"] == {"period": 2}


def test_the_reverts_line_is_a_declaration_line():
    assert "reverts" in amend._declaration_lines(V2_4)
    # only the declaration and the tagged numbers changed, so the text check passes
    assert amend._masked(V2_3) == amend._masked(V2_4)
    gate = amend.check_amendment(V2_3, V2_4)
    assert gate.ok, gate.reasons


# ── The constitution's gate ─────────────────────────────────────────


def test_check_amendment_exempts_the_restored_values_with_a_base():
    const = amend.load_constitution(CONSTITUTION)
    without = amend.check_amendment(V2_3, V2_4, const)
    assert not without.ok
    assert any("2 values change at once" in r for r in without.reasons)
    assert any("`hold_limit` moves by 20" in r for r in without.reasons)
    assert without.reverted == []
    with_base = amend.check_amendment(V2_3, V2_4, const, base=V2)
    assert with_base.ok, with_base.reasons
    assert sorted(with_base.reverted) == ["block_k", "hold_limit"]


def test_a_value_that_is_not_the_base_value_is_checked_as_usual():
    const = amend.load_constitution(CONSTITUTION)
    # hold_limit to 50, which is not version 2's 60: two steps and a second name
    other = amend.set_values(V2_3, {"hold_limit": 50, "block_k": 3})
    gate = amend.check_amendment(V2_3, other, const, base=V2)
    assert not gate.ok and gate.reverted == ["block_k"]
    assert any("`hold_limit` moves by 30" in r for r in gate.reasons)
    assert not any("values change at once" in r for r in gate.reasons)   # block_k is exempt
    # two names that are not restored still break the one-name rule
    both = amend.set_values(V2_3, {"hold_limit": 70, "block_k": 3})
    gate = amend.check_amendment(V2_3, both, const, base=V2_3)
    assert any("values change at once" in r for r in gate.reasons)


def test_the_range_still_applies_to_a_restored_value():
    const = amend.load_constitution(CONSTITUTION.replace("    min: 10\n", "    min: 65\n", 1))
    gate = amend.check_amendment(V2_3, V2_4, const, base=V2)
    assert not gate.ok
    assert any("`hold_limit` = 60 is outside 65–120" in r for r in gate.reasons)


# ── amend-diff --base ───────────────────────────────────────────────


@needs_base
def test_amend_diff_with_the_base_passes_and_without_it_reports_the_step():
    for old, new in ((V2, V2_1), (V2_1, V2_2), (V2_2, V2_3)):
        assert ds.amend_diff(old, new)["ok"]
    without = ds.amend_diff(V2_3, V2_4)
    assert not without["ok"]
    assert any("more than 1 step" in e and "hold_limit" in e for e in without["errors"])
    assert any(f["kind"] == "reverts_unchecked" for f in without["findings"])
    report = ds.amend_diff(V2_3, V2_4, V2)
    assert report["ok"], report["errors"]
    assert report["reverts"] == "2" and report["base_version"] == "2"
    assert {f.get("parameter") for f in report["findings"] if f["kind"] == "parameter_reverted"} \
        == {"hold_limit", "block_k"}
    # a base that is not the version reverted to
    wrong = ds.amend_diff(V2_3, V2_4, V2_1)
    assert not wrong["ok"] and any(f["kind"] == "reverts_mismatch" for f in wrong["findings"])
    # a base given to a version that reverts nothing
    unused = ds.amend_diff(V2, V2_1, V2)
    assert unused["ok"] and any(f["kind"] == "base_unused" for f in unused["findings"])


@needs_base
def test_amend_diff_reports_a_base_that_cannot_be_read():
    report = ds.amend_diff(V2_3, V2_4, "sos: [not a design")
    assert not report["ok"] and report["errors"][0].startswith("The base version cannot be read")


def test_amend_diff_says_when_the_installed_cadl_cannot_use_the_base(monkeypatch):
    monkeypatch.setattr(ds, "amend_diff_takes_base", lambda: False)
    report = ds.amend_diff(V2, V2_1, V2)
    if report is None:
        pytest.skip("this cadl has no amend-diff")
    texts = [f["text"] for f in report["findings"] if f["kind"] == "base_unsupported"]
    assert texts and "0.3.44" in texts[0]


# ── The workspace ───────────────────────────────────────────────────


def test_a_reversion_needs_the_version_it_reverts_to(ws):
    """Without git the versions are the snapshots; version 2 was never kept."""
    workspace.root()
    workspace.write("town", V2_3, note="version 2.3")
    with pytest.raises(workspace.WorkspaceError, match="No version `2`.*town.versions.json.*Versions: 2.3"):
        workspace.add_reversion("town", "2")
    with pytest.raises(workspace.WorkspaceError, match="already version 2.3"):
        workspace.add_reversion("town", "2.3")


def test_a_reversion_names_a_version_in_git(ws, monkeypatch):
    _chain(ws, monkeypatch)
    with pytest.raises(workspace.WorkspaceError, match="No version `1.7`.*Versions: 2, 2.1, 2.2, 2.3"):
        workspace.add_reversion("town", "1.7")
    with pytest.raises(workspace.WorkspaceError, match="a new version"):
        workspace.add_reversion("town", "2", version="")
    assert workspace.source_at_version("town", "2") == V2


def test_add_reversion_makes_one_version_that_the_gate_allows(ws, monkeypatch):
    """Without cadl's check: the proposal, its version record and the gate."""
    _chain(ws, monkeypatch)
    p = workspace.add_reversion("town", "2", "the amendments did not help")
    assert p["version"] == {"from": "2.3", "to": "2.4", "effective_period": 13, "reverts": "2"}
    assert p["restored"] == {"hold_limit": 60.0, "block_k": 3.0}
    assert p["constitution_allows"], p["constitution_problems"]
    after = workspace.source_of("town", p["proposal_id"])
    assert amend.values(after) == amend.values(V2)
    assert amend.declared_version(after)["reverts"] == "2"
    assert ds.yaml.safe_load(after)["sos"]["amendment"]["previous"] == "2.3"
    assert "deadline: 60s   # @amend:hold_limit" in after
    assert any(c.startswith("Reverted hold_limit: 80 → 60") for c in p["changes"])
    if not ds.amend_diff_takes_base():
        assert "0.3.44" in p["note"]


@needs_base
def test_add_reversion_end_to_end_in_git(ws, monkeypatch):
    _chain(ws, monkeypatch)
    seen = []

    def simulator(before, after):
        seen.append((amend.values(before)["hold_limit"], amend.values(after)["hold_limit"]))
        return {"results": {"violations": 1}, "passed": True}

    p = workspace.add_reversion("town", "2", "back to version 2", evaluator=simulator)
    assert seen == [(80.0, 60.0)] and p["trial"]["passed"]
    assert p["acceptable"], (p["constitution_problems"], p["problems_introduced"])
    assert p["amend_diff"]["ok"], p["amend_diff"]
    assert "note" not in p
    after = workspace.source_of("town", p["proposal_id"])
    assert ds.amend_diff(V2_3, after, V2)["ok"]
    applied = workspace.apply_proposal("town", p["proposal_id"], reason="the council agreed")
    assert applied["version"]["reverts"] == "2"
    assert workspace.decisions("town")[-1]["version"]["reverts"] == "2"
    md = (ws / "town.decisions.md").read_text(encoding="utf-8")
    assert "Version 2.4 (previous 2.3, reverts 2, effective from period 13)." in md
    hist = workspace.version_history("town")
    assert [v["version"] for v in hist["versions"]] == ["2", "2.1", "2.2", "2.3", "2.4"]
    assert "reverts 2" in hist["commits"][0]["message"]
    assert amend.values(workspace.read("town")) == amend.values(V2)
    # each adopted version passes amend-diff against the one before
    texts = [workspace.version_source("town", v["commit"]) for v in hist["versions"]]
    for old, new in zip(texts, texts[1:]):
        base = workspace.source_at_version("town", amend.declared_version(new)["reverts"]) \
            if amend.declared_version(new)["reverts"] else None
        assert ds.amend_diff(old, new, base)["ok"]
    # a reversion that is turned down stays in the record
    q = workspace.add_reversion("town", "2.2")
    assert q["version"]["to"] == "2.5" and q["restored"] == {"hold_limit": 80.0}
    workspace.discard_proposal("town", q["proposal_id"], reason="not now")
    assert "Version 2.5 (previous 2.4, reverts 2.2" in (ws / "town.decisions.md").read_text(encoding="utf-8")
    with pytest.raises(workspace.WorkspaceError, match="nothing to revert"):
        workspace.add_reversion("town", "2")


# ── The MCP tool ────────────────────────────────────────────────────


def test_the_mcp_tool_needs_the_version_it_reverts_to(ws):
    pytest.importorskip("mcp")
    import mcp_server

    workspace.root()
    workspace.write("town", V2_3)
    assert "No version `2`" in mcp_server.propose_reversion("town", "2")["error"]


@needs_base
def test_the_mcp_tool_proposes_a_reversion(ws, monkeypatch):
    pytest.importorskip("mcp")
    import mcp_server

    _chain(ws, monkeypatch)
    p = mcp_server.propose_reversion("town", "2", "back to version 2")
    assert p["version"]["to"] == "2.4" and p["reverts"] == "2" and p["acceptable"]
    assert p["amend_diff"]["ok"]
    assert mcp_server.record_trial("town", p["proposal_id"], {"violations": 1.0}, True)["trial"]["passed"]
    applied = mcp_server.apply_proposal("town", p["proposal_id"], reason="the council agreed")
    assert applied["applied"] and applied["version"]["reverts"] == "2"
    assert [v["version"] for v in mcp_server.get_history("town")["versions"]][-1] == "2.4"


# ── The Designer's comparison ───────────────────────────────────────


def test_the_designer_finds_the_version_a_comparison_reverts_to():
    pytest.importorskip("streamlit")
    from views._design import change

    assert change._reverted_base(V2_4, [V2_1, V2, V2_3]) == V2
    assert change._reverted_base(V2_4, [V2_1]) is None        # not at hand
    assert change._reverted_base(V2_3, [V2]) is None          # reverts nothing
