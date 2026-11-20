"""Decision records (Appendix L): reading them and the views of the Decision Evidence page."""
import shutil
import subprocess
from pathlib import Path

import pytest

from cadl_sim import evidence as E

EXAMPLES = sorted((Path(__file__).resolve().parent.parent / "cadl_sim" / "evidence" / "examples")
                  .glob("*.decisions.jsonl"))


@pytest.fixture(params=EXAMPLES, ids=lambda p: p.name)
def rec(request):
    return E.load(request.param.read_bytes())


def test_examples_bundled():
    assert len(EXAMPLES) >= 3


def test_not_a_record():
    with pytest.raises(ValueError):
        E.load(b'{"format": "something else"}\n')
    with pytest.raises(ValueError):
        E.load(b"")


def test_examples_verify(rec):
    pytest.importorskip("cadl.decisions")
    result = E.check(rec)
    assert result["valid"], result["errors"]
    assert result["complete"]


def test_a_changed_line_is_an_error(rec):
    pytest.importorskip("cadl.decisions")
    lines = rec.raw.decode("utf-8").splitlines()
    lines[2] = lines[2].replace('"written": "', '"written": "1', 1)
    bad = E.check(E.load(("\n".join(lines) + "\n").encode("utf-8")))
    assert not bad["valid"]


def test_timeline_and_party_evidence(rec):
    rows = E.timeline(rec, E.check(rec))
    assert rows and all(r["kind"] in E.KINDS for r in rows)
    ev = E.evidence_by_party(rec)
    assert ev and all(r["with reason"] <= r["entries"] for r in ev)
    assert E.series(rec)


def test_steward_records_show_the_override(rec):
    pytest.importorskip("cadl.decisions")
    if "steward" not in {p for p in rec.parties}:
        pytest.skip("not a steward record")
    if "llm-decider" in rec.parties:
        pytest.skip("decided by the LLM")
    rows = E.timeline(rec, E.check(rec))
    assert "override" in rows[0]["findings"]          # D1: block_k 3 → 2 against B and C


@pytest.mark.skipif(not shutil.which("dot"), reason="Graphviz is not installed")
def test_dot_renders(rec):
    result = E.check(rec)
    nodes = E.gsn(rec)
    for did in [e["id"] for e in rec.of_type("decision")][:8]:
        for dark in (False, True):
            dot = E.trail_dot(rec, did, dark, result)
            assert subprocess.run(["dot", "-Tsvg"], input=dot.encode(), capture_output=True).returncode == 0
        g = E.gsn_dot(nodes, focus=did)
        assert subprocess.run(["dot", "-Tsvg"], input=g.encode(), capture_output=True).returncode == 0
