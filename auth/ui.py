"""Login / sign-up screen."""
from __future__ import annotations

import streamlit as st

from auth.authentication import AuthError, authenticate, login_user, register_user
from utils.config import get_default_timezone
from utils.helpers import DISCLAIMER, timezone_options


def render_auth_page() -> None:
    st.markdown("<h1 style='text-align:center'>🍎 Welcome to AI Calorie Tracker</h1>", unsafe_allow_html=True)
    st.markdown(
        "<p style='text-align:center;opacity:.7'>Log meals in plain English and keep a private, day-by-day calorie history.</p>",
        unsafe_allow_html=True,
    )
    _, center, _ = st.columns([1, 2, 1])
    with center:
        login_tab, signup_tab = st.tabs(["Login", "Create Account"])

        with login_tab:
            with st.form("login_form"):
                login = st.text_input("Username or email", max_chars=254)
                password = st.text_input("Password", type="password", max_chars=128)
                submitted = st.form_submit_button("Login", type="primary")
            if submitted:
                try:
                    user = authenticate(login, password)
                except AuthError as exc:
                    st.error(str(exc))
                else:
                    login_user(user)
                    st.rerun()

        with signup_tab:
            zones = timezone_options()
            default_tz = get_default_timezone()
            with st.form("signup_form"):
                username = st.text_input("Username", max_chars=32)
                email = st.text_input("Email", max_chars=254)
                tz = st.selectbox(
                    "Timezone",
                    zones,
                    index=zones.index(default_tz) if default_tz in zones else 0,
                    help="Used to decide what 'today' and 'yesterday' mean for you.",
                )
                password = st.text_input("Password", type="password", max_chars=128,
                                         help="At least 8 characters, mixing letters and numbers/symbols.")
                confirm = st.text_input("Confirm password", type="password", max_chars=128)
                created = st.form_submit_button("Create Account", type="primary")
            if created:
                try:
                    user = register_user(username, email, password, confirm, tz)
                except AuthError as exc:
                    st.error(str(exc))
                else:
                    login_user(user)
                    st.rerun()

        st.caption(DISCLAIMER)
