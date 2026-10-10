"""Smoke tests: every Streamlit page renders without exceptions."""
import os

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest

ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.mark.parametrize("page", ["explorer", "designer", "lifecycle", "about"])
def test_page_renders(page):
    at = AppTest.from_file(os.path.join(ROOT, "views", f"{page}.py"), default_timeout=60).run()
    assert not at.exception
    assert not at.error


def test_explorer_custom_cadl_drives_design_b():
    at = AppTest.from_file(os.path.join(ROOT, "views", "explorer.py"), default_timeout=60).run()
    before = at.metric[0].value
    at.text_area(key="custom_cadl").set_value(
        "name: mine\nsos_type: collaborative\n"
        "motivation:\n  agent:\n    profile: polarized\n"
    ).run()
    assert not at.exception
    assert at.metric[0].value != before
    assert at.radio(key="b_template").disabled


def test_explorer_rho_disabled_without_motivation_model():
    at = AppTest.from_file(os.path.join(ROOT, "views", "explorer.py"), default_timeout=60).run()
    assert not at.slider(key="b_rho").disabled
    at.radio(key="b_template").set_value("C-SoS").run()
    assert at.slider(key="b_rho").disabled


def test_explorer_demo_button_loads_example():
    at = AppTest.from_file(os.path.join(ROOT, "views", "explorer.py"), default_timeout=60).run()
    at.button(key="demo_Directed vs collaborative").click().run()
    assert not at.exception
    assert at.session_state.b_template == "C-SoS"


def test_guide_demos_match_examples():
    from views._guide import DEMOS

    src = open(os.path.join(ROOT, "views", "explorer.py"), encoding="utf-8").read()
    for name in DEMOS:
        assert f'"{name}"' in src


def _designer():
    pytest.importorskip("cadl")
    return AppTest.from_file(os.path.join(ROOT, "views", "designer.py"), default_timeout=60).run()


def _click(at, label):
    return next(b for b in at.button if b.label == label).click().run()


def _badges(at):
    return next(m.value for m in at.markdown if "-badge[" in m.value)


def _set_design(at, source):
    at.session_state.design_src = source
    at.session_state.design_ver += 1
    return at.run()


def _section(at, name):
    at.session_state.design_mode = "Forms"
    at.session_state.design_section = name
    return at.run()


def _view(at, name):
    at.session_state.design_view = name
    return at.run()


def test_designer_renders_and_checks_the_default_design():
    at = _designer()
    assert not at.exception and not at.error
    badges = _badges(at)
    assert ":green-badge[:material/check_circle: Parse]" in badges
    assert ":orange-badge[:material/warning: Lifecycle · 1]" in badges


def test_designer_reports_a_broken_design_without_crashing():
    at = _set_design(_designer(), "sos:\n  name: x\n type: Directed\n")
    assert not at.exception
    assert ":red-badge[:material/error: Parse · 1]" in _badges(at)
    assert any("Line 3, column 2" in m.value for m in at.markdown)
    at = _section(at, "System")
    assert any("Forms need a readable design" in w.value for w in at.warning)


@pytest.mark.parametrize("view", [
    "Architecture", "Lifecycle", "Protocols", "Regimes",
    "Algorithms & metrics", "Read-back", "Checks", "Versions", "Export",
])
def test_designer_views_render(view):
    from backend.services import design_service as ds

    at = _view(_set_design(_designer(), ds.load_example("a sos robot delivery")), view)
    assert not at.exception and not at.error


def test_designer_forms_add_and_delete_a_contract():
    at = _section(_click(_designer(), "New design"), "Contracts")
    assert "CONTRACT_1" not in at.session_state.design_src
    at = _click(at, "Add")
    assert not at.exception
    assert "id: CONTRACT_1" in at.session_state.design_src
    at = _click(at, "Delete")
    assert not at.exception
    assert "CONTRACT_1" not in at.session_state.design_src


def test_designer_adds_a_contract_from_a_template():
    at = _section(_click(_designer(), "New design"), "Contracts")
    at.selectbox(key="design_contract_template").set_value("Safety").run()
    at = _click(at, "Add")
    assert not at.exception
    assert "id: SAFETY_1" in at.session_state.design_src
    assert "separation_watch" in at.session_state.design_src
    assert ":red-badge" not in _badges(at)


def test_designer_forms_add_a_protocol():
    at = _section(_click(_designer(), "New design"), "Protocols")
    at = _click(at, "Add")
    assert not at.exception
    assert "id: PROTOCOL_1" in at.session_state.design_src
    assert "COORDINATOR -> WORKER[i] : request" in at.session_state.design_src


@pytest.mark.parametrize("section", [
    "System", "Actors", "Contracts", "Protocols", "Algorithms", "Regimes", "Metrics", "Verification",
])
def test_designer_form_apply_keeps_the_design(section):
    """Submitting a section unchanged must not change what the design means."""
    from backend.services import design_service as ds

    at = _section(_set_design(_designer(), ds.load_example("a sos robot delivery")), section)
    before = ds.analyze(at.session_state.design_src).ir
    submit = next(b for b in at.button if b.label.startswith("Apply"))
    at = submit.click().run()
    assert not at.exception and not at.error
    assert ds.analyze(at.session_state.design_src).ir == before


def test_designer_undo_and_redo():
    at = _click(_designer(), "New design")
    original = at.session_state.design_src
    at = _click(_section(at, "Protocols"), "Add")
    changed = at.session_state.design_src
    assert changed != original
    at = _click(at, "Undo")
    assert at.session_state.design_src == original
    at = _click(at, "Redo")
    assert at.session_state.design_src == changed


def test_designer_problem_button_opens_the_form_that_fixes_it():
    at = _designer()  # the default example has a lifecycle warning on DELIVERY_SLA
    at = _click(at, "Open form")
    assert not at.exception
    assert at.session_state.design_mode == "Forms"
    assert at.session_state.design_section == "Contracts"
    assert at.selectbox(key="design_contract").value == "DELIVERY_SLA"
    assert any("Found by the checks" in c.value for c in at.caption)


def test_designer_problem_button_shows_the_item_in_its_view():
    from backend.services import design_service as ds

    at = _set_design(_designer(), ds.load_example("a sos robot delivery"))
    at = _click(at, "Show in Protocols")  # an undeclared message in TASK_DISPATCH
    assert not at.exception
    assert at.session_state.design_view == "Protocols"
    assert at.radio(key="design_view_protocol").value == "TASK_DISPATCH"
    assert any("seq-arrow-bad" in m.value for m in at.markdown)

    at = _click(_set_design(at, ds.load_example("sos dsl robot delivery")), "Show in Lifecycle")
    assert at.session_state.design_view == "Lifecycle"


def test_designer_versions_save_restore_and_compare():
    at = _view(_click(_designer(), "New design"), "Versions")
    at.text_input(key="design_version_name").set_value("first").run()
    at = _click(at, "Save as a version")
    assert [v["name"] for v in at.session_state.design_versions] == ["first"]
    saved = at.session_state.design_src

    at = _view(_click(_section(at, "Protocols"), "Add"), "Versions")
    assert at.session_state.design_src != saved
    assert "Version: first" in at.selectbox(key="design_cmp_a").options
    assert any("added protocol PROTOCOL_1" in m.value for m in at.markdown)
    at = _click(at, "Restore")
    assert not at.exception
    assert at.session_state.design_src == saved


def test_designer_lifecycle_trace():
    at = _view(_designer(), "Lifecycle")
    at = _click(at, "Start at the initial state")
    assert at.session_state.design_trace["path"] == ["Proposed"]
    at = next(b for b in at.button if b.label == "assign → Assigned").click().run()
    assert not at.exception
    at = next(b for b in at.button if b.label.startswith("accept misses its deadline")).click().run()
    assert at.session_state.design_trace["path"] == ["Proposed", "Assigned", "Violated"]
    assert any("terminal" in s.value for s in at.success)
    at = _click(at, "Back")
    assert at.session_state.design_trace["path"] == ["Proposed", "Assigned"]


def test_explorer_accepts_custom_cadl_for_both_designs():
    at = AppTest.from_file(os.path.join(ROOT, "views", "explorer.py"), default_timeout=60).run()
    custom = "name: {}\nsos_type: {}\n"
    at.text_area(key="custom_cadl_a").set_value(custom.format("mine_a", "collaborative")).run()
    at.text_area(key="custom_cadl").set_value(custom.format("mine_b", "directed")).run()
    assert not at.exception and not at.error
    assert at.radio(key="a_template").disabled and at.radio(key="b_template").disabled
    # Loading an example drops both custom designs.
    at.button(key="demo_Directed vs collaborative").click().run()
    assert at.session_state.custom_cadl == "" and at.session_state.custom_cadl_a == ""
    assert not at.radio(key="a_template").disabled


def test_designer_read_back_view():
    at = _view(_designer(), "Read-back")
    assert not at.exception and not at.error
    assert any("DELIVERY_SLA is an agreement between" in m.value for m in at.markdown)
    # `[*]` is escaped so Markdown does not turn it into emphasis.
    assert any("ROBOT[\\*] and CUSTOMER[\\*]" in m.value for m in at.markdown)
    assert any("real obligations" in m.value for m in at.markdown)


def test_designer_shows_lifecycle_stories():
    at = _view(_designer(), "Lifecycle")
    assert any("Ends in completed" in m.value for m in at.markdown)


def test_designer_workspace_shows_and_confirms_assistant_changes(tmp_path, monkeypatch):
    monkeypatch.setenv("CADL_WORKSPACE", str(tmp_path))
    from backend.services import design_workspace as workspace

    workspace.create("depot", "template")
    proposal = workspace.add_proposal("depot", [
        {"op": "upsert", "section": "actors", "id": "AUDITOR", "fields": {"role": "auditor"}}],
        rationale="audits are required")
    at = _click(_designer(), "Open")
    assert at.session_state.design_ws_name == "depot"
    assert "AUDITOR" not in at.session_state.design_src

    # The assistant applies its proposal while the page is open.
    workspace.apply_proposal("depot", proposal["proposal_id"])
    at = at.run()
    assert any("differs from the editor" in w.value for w in at.warning)
    at = _click(at, "Reload from workspace")
    assert "AUDITOR" in at.session_state.design_src
    assert any("Added actor AUDITOR" in m.value and "audits are required" in m.value
               for m in at.markdown)
    at = _click(at, "Reviewed")
    assert not at.exception
    assert workspace.unreviewed("depot") == []


def test_designer_hides_the_workspace_unless_configured(monkeypatch):
    monkeypatch.delenv("CADL_WORKSPACE", raising=False)
    at = _designer()
    assert not any(h.value == "Workspace" for h in at.header)


def test_explorer_restores_links_shared_before_the_template_rename():
    at = AppTest.from_file(os.path.join(ROOT, "views", "explorer.py"), default_timeout=60)
    at.query_params["b"] = "am"      # the old key for "A-SoS + motivation-sensitive"
    at.query_params["a"] = "a"
    at = at.run()
    assert not at.exception
    assert at.session_state.b_template == "D-SoS + motivation-sensitive"
    assert at.session_state.a_template == "D-SoS"


def test_app_shows_its_version_on_every_page():
    from backend.version import __version__
    at = AppTest.from_file(os.path.join(ROOT, "app.py"), default_timeout=60).run()
    assert not at.exception
    assert any(f"v{__version__}" in c.value for c in at.sidebar.caption)


def test_about_page_states_the_version():
    from backend.version import __version__
    at = AppTest.from_file(os.path.join(ROOT, "views", "about.py"), default_timeout=60).run()
    assert any(f"Version **{__version__}**" in m.value for m in at.markdown)
