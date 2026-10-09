"""Smoke tests: every Streamlit page renders without exceptions."""
import os

import pytest

pytest.importorskip("streamlit.testing.v1")
from streamlit.testing.v1 import AppTest

ROOT = os.path.join(os.path.dirname(__file__), "..")


@pytest.mark.parametrize("page", ["explorer", "lifecycle", "about"])
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
