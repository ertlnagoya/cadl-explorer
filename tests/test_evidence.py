"""The evidence bundle of cadl-robot-town: chain, flags and views."""
import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from cadl_sim import evidence as E

EXAMPLES = sorted((Path(__file__).resolve().parent.parent / "cadl_sim" / "evidence" / "examples")
                  .glob("*.evidence.json"))


@pytest.fixture(params=EXAMPLES, ids=lambda p: p.name)
def doc(request):
    return E.load(request.param.read_text(encoding="utf-8"))


def test_examples_bundled():
    assert len(EXAMPLES) >= 2


def test_chain_intact(doc):
    assert E.chain_ok(doc)


def test_changing_a_record_breaks_the_chain(doc):
    bad = copy.deepcopy(doc)
    d1 = E.record(bad, "D1")
    d1["decision"]["reason"] = "changed afterwards"
    assert not E.chain_ok(bad)
    rows = E.verify_chain(bad)
    assert [r["id"] for r in rows if not r["ok"]][0] == "D1"
    assert any(f["level"] == "error" for f in E.flags(bad))


def test_not_a_bundle():
    with pytest.raises(ValueError):
        E.load(json.dumps({"format": "something else"}))


def test_vote_override_is_flagged(doc):
    d1 = E.record(doc, "D1")
    assert d1["decision"]["vote_overridden"]           # both records adopt block_k 3 → 2 against B and C
    kinds = {(f["record"], f["kind"]) for f in E.flags(doc)}
    assert ("D1", "vote overridden") in kinds


def test_missing_reason_is_flagged(doc):
    bad = copy.deepcopy(doc)
    E.record(bad, "D2")["decision"]["reason"] = None
    assert ("D2", "missing reason") in {(f["record"], f["kind"]) for f in E.flags(bad)}


def test_timeline_one_row_per_period(doc):
    rows = E.timeline(doc)
    assert [r["period"] for r in rows] == list(range(1, len(rows) + 1))
    assert rows[0]["kpi"] == "K1"
    assert rows[-1]["version"] >= 2


@pytest.mark.skipif(not shutil.which("dot"), reason="Graphviz is not installed")
def test_dot_renders(doc):
    for r in doc["records"]:
        for dark in (False, True):
            dot = E.decision_dot(doc, r["id"], dark)
            assert subprocess.run(["dot", "-Tsvg"], input=dot.encode(), capture_output=True).returncode == 0
    gsn = E.gsn_dot(doc, highlight="D1")
    assert subprocess.run(["dot", "-Tsvg"], input=gsn.encode(), capture_output=True).returncode == 0
