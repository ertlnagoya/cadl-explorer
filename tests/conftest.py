"""Test-only adjustments for older Streamlit releases (Python 3.9 has 1.50 at most),
and the switch that makes the tests that need PRISM fail instead of skip."""
import os

import pytest

try:
    from streamlit.testing.v1 import AppTest
    from streamlit.testing.v1 import element_tree as _et
except ImportError:             # Streamlit without AppTest: the page tests skip
    AppTest = _et = None

# The AppTest of Streamlit 1.50 knows button groups only as st.feedback, whose value
# is a list; a single-select st.segmented_control holds a string, which it iterates
# character by character ("ValueError: content: "S" is not in list"). Later releases
# have ElementTree.segmented_control and read it correctly; only the harness is wrong.
if _et is not None and not hasattr(_et.ElementTree, "segmented_control"):
    _value = _et.ButtonGroup.value.fget

    def _button_group_value(self):
        value = _value(self)
        return [value] if isinstance(value, str) else ([] if value is None else value)

    _et.ButtonGroup.value = property(_button_group_value)

# Later releases of AppTest have `file_uploader`; the tests that upload files
# need it.
needs_file_uploader = pytest.mark.skipif(
    AppTest is None or not hasattr(AppTest, "file_uploader"),
    reason="this Streamlit's AppTest has no file_uploader")


# REQUIRE_PRISM=1 (the CI job with PRISM) turns a missing PRISM into a failure, so
# that the tests of norms-check that need it cannot be skipped there unnoticed.
REQUIRE_PRISM = os.environ.get("REQUIRE_PRISM") == "1"


def prism_or_skip() -> None:
    """Skip the calling test without PRISM on PATH; fail instead with REQUIRE_PRISM=1."""
    from backend.services.design_service import prism_available
    if prism_available():
        return
    if REQUIRE_PRISM:
        pytest.fail("REQUIRE_PRISM=1, but PRISM (prism) is not on PATH")
    pytest.skip("PRISM (prism) is not on PATH")


@pytest.fixture
def prism():
    prism_or_skip()
