"""The language of the pages: English, or Japanese where a translation exists.

`t(text)` returns the Japanese text of `text` when the reader chose Japanese
and views/_i18n_ja.py has it; otherwise the English text itself. The English
text is the key, so a string without a translation stays readable. What cadl
writes (findings, reports) is shown as cadl wrote it, except the notes and
witness explanations of norms-check whose form views/_cadl_ja.py knows.
"""

from __future__ import annotations

import streamlit as st

from views._i18n_ja import JA

LANGUAGES = ["English", "日本語"]


def default_language() -> str:
    """Japanese for a browser that asks for it first, English otherwise."""
    try:
        locale = getattr(st.context, "locale", None) or ""
        if not locale:
            locale = (st.context.headers.get("Accept-Language") or "").split(",")[0]
    except Exception:           # no request in a bare run
        locale = ""
    return LANGUAGES[1] if locale.lower().startswith("ja") else LANGUAGES[0]


def japanese() -> bool:
    return st.session_state.get("lang") == LANGUAGES[1]


def t(text: str) -> str:
    if japanese():
        return JA.get(text, text)
    return text
