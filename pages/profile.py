"""Profile / settings page."""
from __future__ import annotations

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from auth.authentication import AuthError, change_password, require_user
from database import repository as repo
from database.db import session_scope
from utils.helpers import timezone_options
from utils.ui import flash, show_flash


def render() -> None:
    user = require_user()
    st.title("⚙️ Profile")
    show_flash()
    st.caption(f"Signed in as **{user.username}** · {user.email}")

    st.subheader("Daily targets")
    zones = timezone_options()
    with st.form("settings_form"):
        calories = st.number_input(
            "Daily calorie target (kcal)", min_value=800, max_value=10000,
            value=int(user.calorie_target), step=50,
        )
        c1, c2, c3 = st.columns(3)
        protein = c1.number_input("Protein target (g, 0 = none)", min_value=0, max_value=1000,
                                  value=int(user.protein_target_g or 0), step=5)
        carbs = c2.number_input("Carbs target (g, 0 = none)", min_value=0, max_value=2000,
                                value=int(user.carbs_target_g or 0), step=5)
        fat = c3.number_input("Fat target (g, 0 = none)", min_value=0, max_value=1000,
                              value=int(user.fat_target_g or 0), step=5)
        tz = st.selectbox(
            "Timezone", zones,
            index=zones.index(user.timezone) if user.timezone in zones else 0,
            help="Decides what 'today' and 'yesterday' mean.",
        )
        saved = st.form_submit_button("Save settings", type="primary")
    if saved:
        try:
            with session_scope() as s:
                repo.update_user_settings(
                    s, user.id, calorie_target=int(calories),
                    protein_target_g=int(protein) or None,
                    carbs_target_g=int(carbs) or None,
                    fat_target_g=int(fat) or None,
                    timezone=tz,
                )
        except SQLAlchemyError:
            st.error("Couldn't save your settings. Please try again.")
        else:
            flash("Settings saved.")
            st.rerun()

    st.subheader("Change password")
    with st.form("password_form", clear_on_submit=True):
        current = st.text_input("Current password", type="password", max_chars=128)
        new = st.text_input("New password", type="password", max_chars=128)
        confirm = st.text_input("Confirm new password", type="password", max_chars=128)
        changed = st.form_submit_button("Update password")
    if changed:
        try:
            change_password(user.id, current, new, confirm)
        except AuthError as exc:
            st.error(str(exc))
        else:
            flash("Password updated.")
            st.rerun()

    st.subheader("Chat history")
    confirm_clear = st.checkbox("I want to permanently delete my AI Coach conversation history")
    if st.button("Clear chat history", disabled=not confirm_clear):
        try:
            with session_scope() as s:
                repo.clear_chat(s, user.id)
        except SQLAlchemyError:
            st.error("Couldn't clear your chat history. Please try again.")
        else:
            flash("Chat history cleared. Your food log is unchanged.")
            st.rerun()
