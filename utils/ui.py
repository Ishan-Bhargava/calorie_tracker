"""Streamlit UI helpers shared by several pages."""
from __future__ import annotations

import streamlit as st

_FLASH_KEY = "_flash"


def safe_md(text: str) -> str:
    """Escape characters Streamlit's markdown would treat specially (~strike~, $math$)."""
    return str(text).replace("~", "\\~").replace("$", "\\$")


def progress_bar_html(ratio: float, color: str) -> str:
    pct = max(0.0, min(ratio, 1.0)) * 100
    return (
        '<div style="background:rgba(128,128,128,0.25);border-radius:9px;height:18px;overflow:hidden">'
        f'<div style="width:{pct:.0f}%;background:{color};height:100%;border-radius:9px"></div></div>'
    )


def flash(message: str, level: str = "success") -> None:
    st.session_state[_FLASH_KEY] = (level, message)


def show_flash() -> None:
    item = st.session_state.pop(_FLASH_KEY, None)
    if item:
        level, message = item
        getattr(st, level, st.info)(message)
