"""Dashboard: today's calories, macros and meals."""
from __future__ import annotations

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from auth.authentication import require_user
from database import repository as repo
from database.db import session_scope
from utils.helpers import DISCLAIMER, MEAL_TYPES, fmt_int, fmt_qty, progress_status, user_today
from utils.ui import progress_bar_html, safe_md, show_flash


def _macro_metric(col, label: str, value: float, target: int | None) -> None:
    with col.container(border=True):
        suffix = f" / {target} g" if target else " g"
        st.metric(label, f"{fmt_int(value)}{suffix}")
        if target:
            st.progress(min(value / target, 1.0))


def render() -> None:
    user = require_user()
    today = user_today(user.timezone)

    try:
        with session_scope() as s:
            totals = repo.day_totals(s, user.id, today)
            foods = repo.list_foods_for_date(s, user.id, today)
    except SQLAlchemyError:
        st.error("We couldn't load your data right now. Please try again.")
        return

    st.title(f"👋 Welcome back, {user.username}!")
    st.caption(f"{today:%A, %B %d, %Y}")
    show_flash()

    target = max(user.calorie_target, 1)
    kcal = totals["calories"]
    ratio = kcal / target
    status, color = progress_status(ratio)
    remaining = target - kcal

    c1, c2 = st.columns(2)
    with c1.container(border=True):
        st.metric("Today's Calories", f"{fmt_int(kcal)} / {fmt_int(target)} kcal")
        st.markdown(progress_bar_html(ratio, color), unsafe_allow_html=True)
        st.markdown(
            f"<span style='color:{color};font-weight:600'>● {status}</span> · {ratio * 100:.0f}% of target",
            unsafe_allow_html=True,
        )
    with c2.container(border=True):
        if remaining >= 0:
            st.metric("Remaining", f"{fmt_int(remaining)} kcal")
        else:
            st.metric("Over target by", f"{fmt_int(-remaining)} kcal")

    m1, m2, m3 = st.columns(3)
    _macro_metric(m1, "Protein", totals["protein_g"], user.protein_target_g)
    _macro_metric(m2, "Carbs", totals["carbs_g"], user.carbs_target_g)
    _macro_metric(m3, "Fat", totals["fat_g"], user.fat_target_g)

    st.subheader("Today's Meals")
    by_meal: dict[str, list[dict]] = {m: [] for m in MEAL_TYPES}
    for f in foods:
        by_meal.setdefault(f["meal_type"], []).append(f)

    for meal in MEAL_TYPES:
        items = by_meal.get(meal, [])
        if meal == "Other" and not items:
            continue
        with st.container(border=True):
            subtotal = sum(i["calories"] for i in items)
            st.markdown(f"**{meal}**" + (f" · {fmt_int(subtotal)} kcal" if items else ""))
            if not items:
                st.caption("Not logged")
            for i in items:
                tilde = "" if i["calorie_source"] == "user" else "~"
                st.markdown(
                    safe_md(
                        f"• {i['food_name']} ({fmt_qty(i['quantity'])} {i['unit']}) — "
                        f"{tilde}{fmt_int(i['calories'])} kcal"
                    )
                )

    st.info("💬 Log meals by chatting with the **AI Coach**, or edit entries manually in **History**.")
    st.caption(DISCLAIMER)
