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
    # cadl-robot-town v0.6.0 puts every open question to a decision when it falls due
    assert status == {"fulfilled"}
    remeasured = [r for r in rows if r["asks"].startswith("measurement")]
    assert remeasured and all(r["status"] == "fulfilled" and r["fulfilled by"][0].startswith("F")
                              for r in remeasured)
    by_llm = [r for r in E.evidence_by_party(rec, result) if r["party"] == "llm-decider"][0]
    assert by_llm["layers"] == "rule, contract" and by_llm["fulfilled"] >= 1


def test_regulation_decisions_are_on_the_timeline():
    rec = _load("robot-town-steward-regulation-ja.decisions.jsonl")
    rows = {r["id"]: r for r in E.timeline(rec)}
    assert rows["R2"]["layers"] == ["regulation"] and not rows["R2"]["adopted"]
    assert rows["R2"]["by"] == "llm-regulator"
    assert rec.parties["regulator"]["layers"] == ["regulation"]
    # the regulation proposal is measured before the decision (cadl-robot-town v0.5.0), over
    # every period and for each company (v0.6.0)
    assert "P2.R1.m" in rec.by_id["R2"]["basis"]
    m = rec.by_id["P2.R1.m"]
    assert "proposals_passing_vote_all" in m["values"]
    assert "passing_against" in m["per_party"]["A"]
    # the answer to a question that fell due is not an amendment decision
    assert "QR4" in rec.by_id and "QR4" not in rows


# --- delegations (decisions 0.3, v1.8.0) ------------------------------------------

def test_decisions_within_a_delegation():
    pytest.importorskip("cadl.decisions")
    rec = _load("robot-town-steward-regulation-en.decisions.jsonl")
    result = E.check(rec)
    assert result["valid"] and not [f for f in result["findings"] if f["finding"] == "delegated"]
    rows = {g["id"]: g for g in E.delegations(rec, result)}
    assert set(rows) == {"G.llm-decider", "G.llm-regulator", "G.steward"}
    assert rows["G.steward"]["procedures"] == "automatic" and rows["G.steward"]["layers"] == "rule"
    assert "R2" in rows["G.llm-regulator"]["decisions"]
    line = {r["id"]: r for r in E.timeline(rec, result)}
    assert line["D1"]["delegation"] == "G.llm-decider"
    nodes = E.gsn(rec, result=result)
    assert any(n["type"] == "context" and n["ref"] == "G.llm-decider" for n in nodes)


def test_without_a_verification_no_decision_is_listed_under_a_delegation():
    rec = _load("robot-town-steward-d-en.decisions.jsonl")
    assert rec.header["decisions_format"] == "cadl-decisions/3"
    assert E.delegations(rec) and all(not g["decisions"] for g in E.delegations(rec))


# --- the CADL files of the versions (v1.7.0) --------------------------------------

def _versions(name):
    folder = EXDIR / name.replace(".decisions.jsonl", ".versions")
    return {f.name: f.read_bytes() for f in folder.glob("*.cadl")}


@pytest.mark.parametrize("path", EXAMPLES, ids=lambda p: p.name)
def test_bundled_records_are_checked_against_their_versions(path):
    pytest.importorskip("cadl.decisions")
    rec = E.load(path.read_bytes())
    files = _versions(path.name)
    assert files and set(E.attached(rec)) == set(files)
    bare = E.check(rec)
    assert bare["valid"] and not bare["versions_checked"]
    assert any("no CADL file" in n for n in bare["not_checked"])
    full = E.check(rec, files=files)
    assert full["valid"], full["errors"]
    assert sorted(Path(v).name for v in full["versions_checked"]) == sorted(files)
    assert not any("L-9" in n or "L-10" in n or "L-11" in n for n in full["not_checked"])


def test_a_changed_version_file_is_an_error():
    pytest.importorskip("cadl.decisions")
    name = "robot-town-steward-d-en.decisions.jsonl"
    files = _versions(name)
    files["v2.cadl"] = files["v2.cadl"].replace(b"block_k", b"block_k ", 1)
    result = E.check(_load(name), files=files)
    assert not result["valid"]
    assert any(e["rule"] == "L-9" for e in result["errors"])


def test_a_file_is_put_next_to_the_record_only():
    pytest.importorskip("cadl.decisions")
    name = "robot-town-council.decisions.jsonl"
    files = {"../../" + k: v for k, v in _versions(name).items()}
    result = E.check(_load(name), files=files)
    assert result["valid"] and result["versions_checked"]


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
    assert goal["undeveloped"]
    assert goal["id"] + "_u" in E.gsn_dot(gsn)


def test_a_fulfilled_follow_up_has_its_solution_in_the_gsn():
    pytest.importorskip("cadl.decisions")
    rec = _load("robot-town-steward-regulation-en.decisions.jsonl")
    result = E.check(rec)
    nodes = E.gsn(rec, result=result)
    by_id = {n["id"]: n for n in nodes}
    done = [r["id"] for r in E.follow_ups(rec, result) if r["status"] == "fulfilled"]
    goals = [n for n in nodes if n["ref"] in done]
    assert goals and all(any(by_id[c]["type"] == "solution" for c in g["children"]) for g in goals)


def _rechain(rec, ents):
    from cadl.decisions import DecisionChain
    h = rec.header
    ch = DecisionChain(run_id=h["run_id"])
    lines = [ch.header(h["model"], h["parties"], h["started"], h["time_base"], h["previous"])]
    lines += [ch.entry(e) for e in ents] + [ch.end()]
    return E.load(("\n".join(lines) + "\n").encode())


def test_the_gsn_maps_every_field_of_l8():
    """The measure in force, the point, the unknowns and the predictions are context of their
    goal or strategy; a prediction that a later measurement contradicts is a counter-evidence."""
    pytest.importorskip("cadl.decisions")
    rec = _load("robot-town-steward-d-en.decisions.jsonl")
    ents = [dict(e) for e in rec.entries]
    d1 = next(e for e in ents if e["id"] == "D1")
    d1["unknowns"] = ["How C's robots respond"]
    d1["predictions"] = [{"option": "P1.2", "subject": "metric:blocked_s", "expected": "increase",
                          "note": None}]
    rec2 = _rechain(rec, ents)
    result = E.check(rec2)
    assert result["valid"], result["errors"]
    nodes = E.gsn(rec2, result=result)
    by_id = {n["id"]: n for n in nodes}
    s = next(n for n in nodes if n["type"] == "strategy" and n["ref"] == "D1")
    texts = [by_id[c]["text"] for c in s["context"]]
    assert any(x.startswith("point: period 1") for x in texts)
    assert any(x.startswith("unknowns: How C") for x in texts)
    assert any(x.startswith("predictions: ") and "blocked_s increase" in x for x in texts)
    counter = [by_id[c] for c in s["context"] if by_id[c]["type"] == "counter"]
    # D1.n: the steward measured the adopted P1.2 under the conditions of period 2
    assert len(counter) == 1 and counter[0]["ref"] == "D1.n"
    k1 = next(n for n in nodes if n["type"] == "goal" and n["ref"] == "K1")
    assert any(by_id[c]["text"].startswith("measure: ") for c in k1["context"])
    dot = E.gsn_dot(nodes, focus="D1")
    assert counter[0]["id"] in dot and "dashed" in dot


def test_a_bundled_record_has_counter_evidence():
    """cadl-robot-town v0.7.0 measures an adopted amendment under the next period's conditions:
    D3 of the Japanese contract record adopted P3.2, which did not hold up in period 4."""
    pytest.importorskip("cadl.decisions")
    rec = _load("robot-town-steward-contract-ja.decisions.jsonl")
    result = E.check(rec)
    assert result["valid"], result["errors"]
    counter = [n for n in E.gsn(rec, result=result) if n["type"] == "counter"]
    assert counter and {n["ref"] for n in counter} == {"D3.n", "D4.n"}
