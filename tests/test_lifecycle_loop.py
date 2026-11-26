"""The pages along the life of a design, and what they hand on to each other:

Designer → Violation Timeline (the design's IR), Violation Timeline → Designer's
Change stage (proposals of deadlines and monitor periods), Designer → Decision
Evidence (the decisions as a record of Appendix L); Contract Lifecycle under
"Verify by running" reading the Designer's design.
"""

from __future__ import annotations

import io
import os
import zipfile

import pytest

pytest.importorskip("cadl")

from streamlit.testing.v1 import AppTest  # noqa: E402

from backend.services import design_amend as amend  # noqa: E402
from backend.services import design_record as drec  # noqa: E402
from backend.services import design_service as ds  # noqa: E402
from backend.services import design_tuning as tuning  # noqa: E402
from cadl_sim import evidence as E  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")
DELIVERY = open(os.path.join(ROOT, "designs", "sos_dsl_robot_delivery.cadl"), encoding="utf-8").read()
ACCEPT = {"contract": "DELIVERY_SLA", "kind": "transition", "id": "accept",
          "field": "deadline_ms", "value": 1500}
GUARD = {"contract": "DELIVERY_SLA", "kind": "monitor", "id": "battery_guard",
         "field": "sampling_period_ms", "value": 250}


# ── The proposals written into the source ────────────────────────────

def test_a_proposal_rewrites_one_line_and_keeps_the_comments():
    new, kept = tuning.apply_to_source(DELIVERY, [ACCEPT, GUARD])
    assert kept
    changed = [(a, b) for a, b in zip(DELIVERY.splitlines(), new.splitlines()) if a != b]
    assert [b.strip() for _, b in changed] == ["deadline: 1500ms", "sampling: periodic(250ms)"]
    assert DELIVERY.count("#") == new.count("#")
    assert tuning.parameter(ACCEPT) == "DELIVERY_SLA.lifecycle.accept.deadline"


def test_without_the_line_the_structured_change_is_used():
    source = DELIVERY.replace("            deadline: 5s\n", "", 1)
    new, kept = tuning.apply_to_source(source, [ACCEPT])
    assert not kept
    from cadl_sim.sos_dsl.violation_trace import find_transition
    assert find_transition(ds.analyze(new).ir, "DELIVERY_SLA", "accept")["deadline_ms"] == 1500


def test_a_proposal_never_adds_a_transition():
    with pytest.raises(tuning.TuningError, match="no transition DELIVERY_SLA.nope"):
        tuning.apply_to_source(DELIVERY, [{**ACCEPT, "id": "nope"}])


def test_a_value_marked_for_amendment_is_left_to_the_constitution():
    town = ds.load_example("robot town")
    release = {"contract": "ROAD_USE", "kind": "transition", "id": "release",
               "field": "deadline_ms", "value": 61300}
    with pytest.raises(tuning.TuningError, match="marked `# @amend:hold_limit`"):
        tuning.apply_to_source(town, [release])


def test_without_the_line_a_design_with_amend_marks_is_not_rewritten():
    town = ds.load_example("robot town")
    line = next(l for l in town.splitlines(keepends=True) if "@amend:hold_limit" in l
                and "deadline:" in l)
    without = town.replace(line, "", 1)          # ROAD_USE.release has no deadline line now
    with pytest.raises(tuning.TuningError, match="drop its `# @amend:` marks"):
        tuning.apply_to_source(without, [{"contract": "ROAD_USE", "kind": "transition",
                                          "id": "release", "field": "deadline_ms",
                                          "value": 1000}])


# ── The Designer's decisions as a record of Appendix L ───────────────

def _decisions():
    town = ds.load_example("robot town")
    after = amend.set_values(town, {"hold_limit": 70})
    tuned = after.replace("hold_limit", "hold_limit", 1)     # the same text: a second version
    return town, [
        {"time": "10:00:00", "at": "2026-11-26T01:00:00Z", "change": "hold_limit: 60 → 70",
         "layer": "rule", "reason": "B and C agreed", "by": "designer",
         "changes": [{"parameter": "hold_limit", "from": "60", "to": "70", "layer": "rule"}],
         "before": town, "after": after},
        {"time": "10:05:00", "at": "2026-11-26T01:05:00Z", "change": "a deadline",
         "layer": "contract", "reason": "", "by": "violation-timeline",
         "proposal_reason": "R1: lengthen the deadline", "before": after, "after": tuned},
        {"time": "10:06:00", "change": "kept from an older session", "layer": "", "reason": ""},
    ]


def test_the_record_passes_decisions_verify():
    town, decisions = _decisions()
    text, files = drec.to_decisions(drec.system_name(town), decisions)
    result = E.check(E.load(text.encode("utf-8")), files=files)
    if result is None:
        pytest.skip("cadl decisions-verify is not here (cadl 0.3.39 or later)")
    assert result["valid"], result["errors"]
    assert sorted(files) == ["v1.cadl", "v2.cadl", "v3.cadl"]
    # an amendment keeps sos.version: the later files carry the name of their version
    assert [drec.sos_version(files[f].decode()) for f in sorted(files)] == ["1.0.0", "1.0.0+1", "1.0.0+2"]
    with zipfile.ZipFile(io.BytesIO(drec.to_zip(text, files))) as z:
        assert sorted(z.namelist()) == ["decisions.jsonl", "v1.cadl", "v2.cadl", "v3.cadl"]


def _verify(decisions):
    text, files = drec.to_decisions("design", decisions)
    result = E.check(E.load(text.encode("utf-8")), files=files)
    if result is None:
        pytest.skip("cadl decisions-verify is not here (cadl 0.3.39 or later)")
    return text, files, result


def test_an_older_entry_first_and_a_change_outside_the_record():
    town = ds.load_example("robot town")
    held = amend.set_values(town, {"hold_limit": 70})
    blocked = amend.set_values(town, {"block_k": 2})       # after an Undo of the first
    old = {"time": "09:00:00", "change": "from an older session", "layer": "", "reason": ""}
    first = {"at": "2026-01-01T00:00:00Z", "change": "hold_limit: 60 → 70", "layer": "rule",
             "reason": "r", "before": town, "after": held}
    second = {"at": "2026-01-01T00:05:00Z", "change": "block_k: 3 → 2", "layer": "rule",
              "reason": "r", "before": town, "after": blocked}
    text, files, result = _verify([old, first, second])
    assert result["valid"], result["errors"]
    # V1, V2 (hold_limit), V3 (back to V1, outside the record), V4 (block_k)
    assert sorted(files) == ["v1.cadl", "v2.cadl", "v3.cadl", "v4.cadl"]
    assert "Changes made outside the recorded decisions" in text


def test_a_malformed_entry_still_gives_a_record():
    _, files, result = _verify([{"change": "x", "changes": ["x"], "before": 5, "after": None}])
    assert result["valid"], result["errors"] and files == {}


def test_with_version_rewrites_only_a_key_of_sos():
    src = 'sos:\n  meta:\n    version: "9"\n  name: X\n  version: "1.0.0"\n'
    assert drec.with_version(src, "2") == 'sos:\n  meta:\n    version: "9"\n  name: X\n  version: "2"\n'


def test_with_version_keeps_the_rest_of_the_text():
    town = ds.load_example("robot town")
    renamed = drec.with_version(town, "2.0.0")
    assert drec.sos_version(renamed) == "2.0.0"
    assert sum(a != b for a, b in zip(town.splitlines(), renamed.splitlines())) == 1


# ── The pages ─────────────────────────────────────────────────────────

def _app(**state):
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=120)
    for key, value in state.items():
        at.session_state[key] = value
    return at


def test_the_violation_timeline_proposes_its_changes_to_the_designer():
    at = _app(design_src=DELIVERY, design_ver=0, vt_ir_source="designer")
    at.switch_page("views/violations.py").run()
    assert not at.exception
    assert [t.label for t in at.tabs][:4] == ["Run", "Timeline", "Measurements", "Improvements"]
    next(b for b in at.button if b.label == "Propose the chosen changes to the Designer").click().run()
    assert not at.exception
    pending = at.session_state.design_pending
    assert pending["overrides"] and pending["log"] == "demo_accept_300ms.ndjson"
    assert at.session_state.design_view == "Versions"
    assert any("Proposed by the Violation Timeline" in m.value for m in at.markdown)

    # AppTest reruns the page it was last pointed at, so point it at the Designer
    at.switch_page("views/designer.py").run()
    # the bundled log ran with accept at 300ms; the design has 5s
    assert any("a condition of the run" in w.value and "5s" in w.value for w in at.warning)
    apply = next(b for b in at.button if b.label == "Apply the proposal")
    assert apply.disabled                                   # the reason comes first
    at.text_input(key="design_pending_reason").set_value("the report must name the fault").run()
    apply = next(b for b in at.button if b.label == "Apply the proposal")
    assert apply.disabled                                   # and the check of the values
    at.checkbox(key="design_pending_checked").check().run()
    next(b for b in at.button if b.label == "Apply the proposal").click().run()
    assert not at.exception
    assert "design_pending" not in at.session_state
    assert DELIVERY.count("#") == at.session_state.design_src.count("#")     # comments kept
    assert at.session_state.design_versions[-1]["name"] == "before the Violation Timeline's proposal"
    decision = at.session_state.design_decisions[-1]
    assert decision["by"] == "violation-timeline" and decision["before"] == DELIVERY
    assert decision["reason"] == "the report must name the fault"
    assert "DELIVERY_SLA.lifecycle.accept.deadline: 5s → 600ms" in decision["change"]
    assert any(s.value.startswith("Changed ") for s in at.success)


def test_with_another_ir_the_violation_timeline_says_how_to_send_changes_back():
    at = _app()
    at.switch_page("views/violations.py").run()
    assert not at.exception
    assert not any(b.label == "Propose the chosen changes to the Designer" for b in at.button)
    assert any("cannot be sent back to a design" in c.value for c in at.caption)


def test_the_designer_record_opens_in_decision_evidence():
    town, decisions = _decisions()
    at = _app(design_src=town, design_ver=0, design_decisions=decisions, design_view="Versions")
    at.switch_page("views/designer.py").run()
    assert not at.exception
    next(b for b in at.button if b.label == "Open in Decision Evidence").click().run()
    assert not at.exception
    assert at.session_state.evidence_example == "__designer__"
    assert any(t.value == "Decision Evidence" for t in at.title)
    assert not at.error
    shown = next(s for s in at.selectbox if s.label == "Record shown")
    assert shown.value == "__designer__" and shown.options[0] == "The Designer's decisions (3)"


def test_contract_lifecycle_reads_the_designer_design_under_verify_by_running():
    at = _app(design_src=DELIVERY, design_ver=0)
    at.switch_page("views/lifecycle.py").run()
    assert not at.exception
    pick = next(s for s in at.selectbox if s.label == "IR shown")
    assert pick.options[0] == "The Designer's design" and pick.value == "__designer__"
    from views._i18n import t  # noqa: F401  (the label is the English key here)
    src = open(os.path.join(ROOT, "app.py"), encoding="utf-8").read()
    verify = src.index('t("Verify by running")')
    assert src.index("views/violations.py") > verify and src.index("views/lifecycle.py") > verify


def test_about_lists_every_page_by_stage():
    at = AppTest.from_file(os.path.join(ROOT, "views", "about.py"), default_timeout=60).run()
    assert not at.exception
    text = "\n".join(m.value for m in at.markdown)
    for page in ("Violation Timeline", "Contract Lifecycle", "Decision Evidence"):
        assert f"*{page}*" in text
    assert [h.value for h in at.header if "page" in h.value][:3] == [
        "Violation Timeline page", "Contract Lifecycle page", "Decision Evidence page"]
