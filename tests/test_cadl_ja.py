"""Japanese for the notes and witness explanations of `cadl norms-check`."""

from __future__ import annotations

import pytest

from backend.plotting.sequence import sequence_svg, trace_protocol
from views import _cadl_ja as ja

# texts as cadl 0.3.43 writes them (cadl/norms_check.py)
NOTES = [
    "ROBOT[1..N] has 1 member(s) in the instance because --members does not give its size; "
    "the answers hold for that size only: give --members ROBOT=n to check more",
    "'ROBOT[1].length * ROBOT[1].width > 6' is not linear (a product of two quantities): "
    "ROBOT[1].length, ROBOT[1].width take the integers -1..7, a domain assumed from the "
    "literals of the predicate (as --domain ROBOT[1].length=-1..7); the answers hold only for "
    "traces whose numbers for them are such integers. Give --domain to choose another one",
    "index i is not bound; it is bound to 1 (use --bindings i=n)",
    "--members ROBOT=2: ROBOT is declared with 4 members; the answers (\"violable: no\" among "
    "them) hold for instances with 2 of them only",
    "--bindings j: the contract does not use the name j; the binding has no effect",
    "the index of ROBOT[k] is not bound: it matches every member, as in norms-eval",
    "ROBOT[3..4] belong to the domain of a quantifier or aggregate but not to the instance "
    "(--members): in the model they have no value",
    "ROBOT[*].load takes the integers 0..3 of --domain and the values that are not numbers: "
    "the answers hold for traces in which its numbers are such integers",
    "the 2 instances start at observations that the environment chooses, in any order, and "
    "share the observations, the message events and the quantities; each ends at an "
    "observation of its own",
    "the 3 instances start together at the first observation (--starts together) and share "
    "the observations, the message events and the quantities; each ends at an observation of "
    "its own (traces whose instances start at different observations are not covered)",
    "an instance that starts at observation s has at most 8 - s + 1 observations (the horizon "
    "8 is shared): a later start leaves a shorter instance, and a larger --horizon covers "
    "longer ones",
    "'max(a, b) * (c) > 3' is not linear (BinaryOp in arithmetic): a takes the integers 0..3, "
    "a domain assumed from the literals of the predicate (as --domain a=0..3); the answers "
    "hold only for traces whose numbers for it are such integers. Give --domain to choose "
    "another one",
]
EXPLANATIONS = [
    "replayed from the strategy of PRISM",
    "every instance that runs to the horizon violates the norm; this is the one without "
    "messages and without values",
    "at observation 3 every choice of the environment that activates the norm leads to a "
    "state from which every continuation that runs to the horizon violates it; this trace "
    "takes one of them and continues without messages and values",
    "after 2 observation(s) both norms are in force; keeping keep_out violates must_enter",
    "the strategy did not reach the state",
]


@pytest.fixture
def japanese(monkeypatch):
    monkeypatch.setattr(ja, "japanese", lambda: True)


@pytest.mark.parametrize("text", NOTES)
def test_every_note_of_norms_check_has_japanese(japanese, text):
    out = ja.note(text)
    assert out != text and not any(w in out for w in ("the answers", "instance", "Give"))


@pytest.mark.parametrize("text", EXPLANATIONS)
def test_every_witness_explanation_has_japanese(japanese, text):
    assert ja.explanation(text) != text


def test_the_names_and_numbers_are_kept(japanese):
    out = ja.note(NOTES[1])
    assert "'ROBOT[1].length * ROBOT[1].width > 6'" in out and "2 つの量の積" in out
    assert "-1..7" in out and "--domain ROBOT[1].length=-1..7" in out
    assert ja.note(NOTES[0]).count("ROBOT") == 3
    assert ja.claim("over_capacity activated") == "over_capacity が発効する"
    assert "keep_out を守ると must_enter に違反します" in ja.explanation(EXPLANATIONS[3])


def test_an_unknown_form_and_english_are_left_as_they_are(japanese, monkeypatch):
    assert ja.note("a note of a later cadl") == "a note of a later cadl"
    assert ja.claim("over_capacity reviewed") == "over_capacity reviewed"
    monkeypatch.setattr(ja, "japanese", lambda: False)
    assert ja.note(NOTES[0]) == NOTES[0] and ja.claim("x activated") == "x activated"


def test_the_sequence_diagram_takes_its_words():
    records = [{"t": 0}, {"t": 1, "state": "A", "end": True}]
    words = {"no message": "メッセージなし", "state": "状態", "end": "トレースの終わり",
             "no actor": "（アクターなし）"}
    steps = trace_protocol(records, words=words)["steps"]
    assert [s["content"] for s in steps] == ["t = 0: メッセージなし", "t = 1 · 状態 A · トレースの終わり"]
    assert "（アクターなし）" in sequence_svg({"steps": steps}, words=words)
    assert "(no actor)" in sequence_svg({"steps": steps})
    assert "まだ" in sequence_svg({"steps": []}, words={"no steps": "まだステップがありません"})


def test_a_reason_of_cadl_in_arithmetic(japanese):
    assert "算術の中の BinaryOp" in ja.note(NOTES[-1])
