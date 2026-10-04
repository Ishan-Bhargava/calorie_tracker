"""AI Calorie Tracker - Streamlit entry point.  Run: streamlit run app.py"""
from __future__ import annotations

import streamlit as st

st.set_page_config(page_title="AI Calorie Tracker", page_icon="🍎", layout="wide")

from auth.authentication import get_current_user, logout  # noqa: E402
from auth.ui import render_auth_page  # noqa: E402
from database.db import init_db  # noqa: E402
from pages import chat, dashboard, history, profile  # noqa: E402
from utils.helpers import DISCLAIMER  # noqa: E402


@st.cache_resource
def _bootstrap() -> bool:
    init_db()
    return True


def main() -> None:
    try:
        _bootstrap()
    except Exception:
        st.error("The database could not be initialised. Check DATABASE_URL and file permissions.")
        st.stop()

    user = get_current_user()
    if user is None:
        # Registering a single hidden page stops Streamlit auto-listing the pages/ folder.
        st.navigation(
            [st.Page(render_auth_page, title="Login", url_path="login", default=True)],
            position="hidden",
        ).run()
        return

    navigation = st.navigation(
        [
            st.Page(dashboard.render, title="Dashboard", icon="🏠", url_path="dashboard", default=True),
            st.Page(chat.render, title="AI Coach", icon="💬", url_path="coach"),
            st.Page(history.render, title="History", icon="📅", url_path="history"),
            st.Page(profile.render, title="Profile", icon="⚙️", url_path="profile"),
        ]
    )
    with st.sidebar:
        st.divider()
        st.markdown("### 🍎 AI Calorie Tracker")
        st.markdown(f"👤 **{user.username}**")
        if st.button("🚪 Logout"):
            logout()
            st.rerun()
        st.caption(DISCLAIMER)
    navigation.run()


main()
