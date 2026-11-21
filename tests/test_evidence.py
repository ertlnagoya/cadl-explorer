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


# --- decisions 0.2 ---------------------------------------------------------------

EXDIR = Path(__file__).resolve().parent.parent / "cadl_sim" / "evidence" / "examples"


def _load(name):
    return E.load((EXDIR / name).read_bytes())


def test_a_record_of_decisions_0_1_is_read():
    rec = E.load((Path(__file__).parent / "data" / "decisions-0.1-steward-d-en.jsonl").read_bytes())
    assert rec.header["decisions_format"] == "cadl-decisions/1"
    assert E.timeline(rec) and E.follow_ups(rec) == []
    pytest.importorskip("cadl.decisions")
    assert E.check(rec)["valid"]


def test_follow_ups_and_their_status():
    pytest.importorskip("cadl.decisions")
    rec = _load("robot-town-steward-regulation-en.decisions.jsonl")
    result = E.check(rec)
    rows = E.follow_ups(rec, result)
    status = {r["status"] for r in rows}
    assert "fulfilled" in status and "open" in status
    remeasured = [r for r in rows if r["asks"].startswith("measurement")]
    assert remeasured and all(r["status"] == "fulfilled" and r["fulfilled by"][0].startswith("F")
                              for r in remeasured)
    by_llm = [r for r in E.evidence_by_party(rec, result) if r["party"] == "llm-decider"][0]
    assert by_llm["layers"] == "rule, contract" and by_llm["fulfilled"] >= 1


def test_regulation_decisions_are_on_the_timeline():
    rec = _load("robot-town-steward-regulation-ja.decisions.jsonl")
    rows = {r["id"]: r for r in E.timeline(rec)}
    assert rows["R5"]["layers"] == ["regulation"] and rows["R5"]["adopted"]
    assert rows["R5"]["by"] == "llm-regulator"
    assert rec.parties["regulator"]["layers"] == ["regulation"]


def test_a_claim_not_shown_is_not_a_fulfilment(tmp_path):
    pytest.importorskip("cadl.decisions")
    from cadl.decisions import DecisionChain
    rec = _load("robot-town-steward-d-en.decisions.jsonl")
    # D1 promises to re-measure under the conditions of P1.m0; D2 claims it
    ents = [dict(e) for e in rec.entries]
    i = next(n for n, e in enumerate(ents) if e["id"] == "D1") + 1
    ents.insert(i, {"id": "D1.f1", "type": "follow_up", "point": {"period": 1}, "written": "2026-11-21T00:00:00Z",
                    "by": "operator", "basis": [], "fulfils": [], "attachments": [], "generated": None,
                    "text": "re-measure", "due": {"period": 2}, "decision": "D1",
                    "fulfilled_by": {"type": "measurement", "of": "in_force", "same_method_as": "P1.m0",
                                     "subjects": []}})
    next(e for e in ents if e["id"] == "D2")["fulfils"] = ["D1.f1"]
    h = rec.header
    ch = DecisionChain(run_id=h["run_id"])
    lines = [ch.header(h["model"], h["parties"], h["started"], h["time_base"], h["previous"])]
    lines += [ch.entry(e) for e in ents] + [ch.end()]
    rec2 = E.load(("\n".join(lines) + "\n").encode())
    result = E.check(rec2)
    row = [r for r in E.follow_ups(rec2, result) if r["id"] == "D1.f1"][0]
    assert row["status"] == "open" and row["not shown"] == ["D2"] and row["fulfilled by"] == []
    dot = E.trail_dot(rec2, "D1", False, result)
    assert "D2" in dot
    gsn = E.gsn(rec2, result=result)
    goal = [n for n in gsn if n["ref"] == "D1.f1"][0]
    assert goal["children"] == []          # no solution: the promise is not fulfilled


def test_a_fulfilled_follow_up_has_its_solution_in_the_gsn():
    pytest.importorskip("cadl.decisions")
    rec = _load("robot-town-steward-regulation-en.decisions.jsonl")
    result = E.check(rec)
    nodes = E.gsn(rec, result=result)
    by_id = {n["id"]: n for n in nodes}
    done = [r["id"] for r in E.follow_ups(rec, result) if r["status"] == "fulfilled"]
    goals = [n for n in nodes if n["ref"] in done]
    assert goals and all(any(by_id[c]["type"] == "solution" for c in g["children"]) for g in goals)
