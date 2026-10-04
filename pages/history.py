"""History page: per-day inspection/editing, weekly and monthly charts."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.express as px
import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from auth.authentication import require_user
from database import repository as repo
from database.db import session_scope
from database.models import UserInfo
from utils.helpers import MEAL_TYPES, fmt_int, progress_status, user_today
from utils.ui import flash, safe_md, show_flash

_DB_ERROR = "A database error occurred. Nothing was changed - please try again."


# ------------------------------------------------------------ manual edit


def _add_form(user: UserInfo, selected: date, today: date) -> None:
    with st.expander("➕ Add food manually"):
        with st.form("add_food_form", clear_on_submit=True):
            name = st.text_input("Food name", max_chars=120)
            c1, c2, c3 = st.columns(3)
            meal = c1.selectbox("Meal", MEAL_TYPES)
            qty = c2.number_input("Quantity", min_value=0.0, value=1.0, step=0.5)
            unit = c3.text_input("Unit", value="serving", max_chars=30)
            c4, c5, c6, c7 = st.columns(4)
            kcal = c4.number_input("Calories", min_value=0.0, max_value=10000.0, value=0.0, step=10.0)
            prot = c5.number_input("Protein (g)", min_value=0.0, max_value=1000.0, value=0.0, step=1.0)
            carbs = c6.number_input("Carbs (g)", min_value=0.0, max_value=2000.0, value=0.0, step=1.0)
            fat = c7.number_input("Fat (g)", min_value=0.0, max_value=1000.0, value=0.0, step=1.0)
            when = st.date_input("Date", value=selected, max_value=today)
            submitted = st.form_submit_button("Add", type="primary")
        if submitted:
            if not name.strip():
                st.error("Please enter a food name.")
                return
            try:
                with session_scope() as s:
                    repo.add_food(
                        s, user.id, date=when, meal_type=meal, food_name=name.strip(),
                        quantity=qty or 1.0, unit=unit.strip() or "serving", calories=kcal,
                        protein_g=prot, carbs_g=carbs, fat_g=fat, calorie_source="user",
                    )
            except SQLAlchemyError:
                st.error(_DB_ERROR)
                return
            flash(f"Added {name.strip()} to {when.isoformat()}.")
            st.rerun()


def _entry_editor(user: UserInfo, e: dict, today: date) -> None:
    label = f"{e['meal_type']} · {e['food_name']} — {fmt_int(e['calories'])} kcal"
    with st.expander(safe_md(label)):
        with st.form(f"edit_{e['id']}"):
            name = st.text_input("Food name", value=e["food_name"], max_chars=120)
            c1, c2, c3 = st.columns(3)
            meal = c1.selectbox(
                "Meal", MEAL_TYPES,
                index=MEAL_TYPES.index(e["meal_type"]) if e["meal_type"] in MEAL_TYPES else 4,
            )
            qty = c2.number_input("Quantity", min_value=0.0, value=float(e["quantity"]), step=0.5)
            unit = c3.text_input("Unit", value=e["unit"], max_chars=30)
            c4, c5, c6, c7 = st.columns(4)
            kcal = c4.number_input("Calories", min_value=0.0, max_value=10000.0, value=float(e["calories"]), step=10.0)
            prot = c5.number_input("Protein (g)", min_value=0.0, max_value=1000.0, value=float(e["protein_g"]), step=1.0)
            carbs = c6.number_input("Carbs (g)", min_value=0.0, max_value=2000.0, value=float(e["carbs_g"]), step=1.0)
            fat = c7.number_input("Fat (g)", min_value=0.0, max_value=1000.0, value=float(e["fat_g"]), step=1.0)
            when = st.date_input("Date", value=e["date"], max_value=max(today, e["date"]))
            save = st.form_submit_button("💾 Save changes", type="primary")
            delete = st.form_submit_button("🗑️ Delete")

        if save:
            if not name.strip():
                st.error("Food name can't be empty.")
                return
            changes = dict(
                food_name=name.strip(), meal_type=meal, quantity=qty or 1.0,
                unit=unit.strip() or "serving", calories=kcal, protein_g=prot,
                carbs_g=carbs, fat_g=fat, date=when,
            )
            if abs(kcal - e["calories"]) > 1e-9:
                changes["calorie_source"] = "user"  # manually set number
            try:
                with session_scope() as s:
                    repo.update_food(s, user.id, e["id"], **changes)
            except (SQLAlchemyError, LookupError):
                st.error(_DB_ERROR)
                return
            flash("Entry updated.")
            st.rerun()
        if delete:
            try:
                with session_scope() as s:
                    repo.delete_food(s, user.id, e["id"])
            except SQLAlchemyError:
                st.error(_DB_ERROR)
                return
            flash("Entry deleted.")
            st.rerun()


def _daily_tab(user: UserInfo, today: date) -> None:
    selected = st.date_input("Select a date", value=today, max_value=today, key="hist_date")
    try:
        with session_scope() as s:
            foods = repo.list_foods_for_date(s, user.id, selected)
            totals = repo.day_totals(s, user.id, selected)
    except SQLAlchemyError:
        st.error("We couldn't load that day. Please try again.")
        return

    ratio = totals["calories"] / max(user.calorie_target, 1)
    label, color = progress_status(ratio)
    cols = st.columns(4)
    cols[0].metric("Calories", f"{fmt_int(totals['calories'])} kcal")
    cols[1].metric("Protein", f"{fmt_int(totals['protein_g'])} g")
    cols[2].metric("Carbs", f"{fmt_int(totals['carbs_g'])} g")
    cols[3].metric("Fat", f"{fmt_int(totals['fat_g'])} g")
    if foods:
        st.markdown(
            f"<span style='color:{color};font-weight:600'>● {label}</span> · {ratio * 100:.0f}% of "
            f"{fmt_int(user.calorie_target)} kcal target",
            unsafe_allow_html=True,
        )

    _add_form(user, selected, today)

    if not foods:
        st.info("No food logged for this day.")
        return
    df = pd.DataFrame(foods)[
        ["meal_type", "food_name", "quantity", "unit", "calories", "protein_g", "carbs_g", "fat_g", "calorie_source"]
    ]
    df["calorie_source"] = df["calorie_source"].map({"user": "user-provided", "estimated": "estimated"})
    df.columns = ["Meal", "Food", "Qty", "Unit", "kcal", "Protein g", "Carbs g", "Fat g", "Calories are"]
    st.dataframe(df, hide_index=True)

    st.markdown("**Edit or delete entries**")
    for e in foods:
        _entry_editor(user, e, today)


# ------------------------------------------------------------- analytics


def _frame(user_id: int, start: date, end: date) -> pd.DataFrame:
    with session_scope() as s:
        rows = repo.daily_totals_range(s, user_id, start, end)
    idx = pd.date_range(start, end, name="date")
    cols = ["calories", "protein_g", "carbs_g", "fat_g", "entries"]
    if rows:
        df = pd.DataFrame(rows)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").reindex(idx)
    else:
        df = pd.DataFrame(index=idx, columns=cols, dtype=float)
    df["logged"] = df["entries"].fillna(0) > 0
    return df


def _status_column(df: pd.DataFrame, target: int) -> pd.Series:
    def one(row) -> str:
        if not row["logged"]:
            return "Not logged"
        return progress_status(row["calories"] / max(target, 1))[0]

    return df.apply(one, axis=1)


_STATUS_COLORS = {
    "On track": "#22c55e",
    "Near target": "#eab308",
    "Over target": "#ef4444",
    "Not logged": "#9ca3af",
}


def _averages(df: pd.DataFrame) -> None:
    logged = df[df["logged"]]
    n = len(logged)
    c = st.columns(5)
    c[0].metric("Days logged", f"{n} / {len(df)}")
    if n:
        c[1].metric("Avg calories", f"{fmt_int(logged['calories'].mean())} kcal")
        c[2].metric("Avg protein", f"{fmt_int(logged['protein_g'].mean())} g")
        c[3].metric("Avg carbs", f"{fmt_int(logged['carbs_g'].mean())} g")
        c[4].metric("Avg fat", f"{fmt_int(logged['fat_g'].mean())} g")
    st.caption("Averages are per logged day (days with no entries are excluded).")


def _weekly_tab(user: UserInfo, today: date) -> None:
    try:
        df = _frame(user.id, today - timedelta(days=6), today)
    except SQLAlchemyError:
        st.error("We couldn't load your history. Please try again.")
        return
    st.subheader("Calories - last 7 days")
    plot = df.reset_index()
    plot["Day"] = plot["date"].dt.strftime("%a %d")
    plot["Calories"] = plot["calories"].fillna(0)
    plot["Status"] = _status_column(df, user.calorie_target).values
    fig = px.bar(
        plot, x="Day", y="Calories", color="Status", color_discrete_map=_STATUS_COLORS,
        text="Calories", category_orders={"Day": plot["Day"].tolist()},
    )
    fig.update_traces(texttemplate="%{text:.0f}")
    fig.add_hline(y=user.calorie_target, line_dash="dash", annotation_text="Target")
    fig.update_layout(xaxis_title=None, yaxis_title="kcal", legend_title=None)
    st.plotly_chart(fig)
    _averages(df)

    macros = plot.melt(id_vars="Day", value_vars=["protein_g", "carbs_g", "fat_g"],
                       var_name="Macro", value_name="Grams")
    macros["Macro"] = macros["Macro"].map({"protein_g": "Protein", "carbs_g": "Carbs", "fat_g": "Fat"})
    macros["Grams"] = macros["Grams"].fillna(0)
    fig2 = px.bar(macros, x="Day", y="Grams", color="Macro", barmode="group",
                  category_orders={"Day": plot["Day"].tolist()})
    fig2.update_layout(xaxis_title=None, legend_title=None)
    st.subheader("Macronutrients")
    st.plotly_chart(fig2)


def _monthly_tab(user: UserInfo, today: date) -> None:
    span = st.radio("Range", [30, 60, 90], format_func=lambda d: f"Last {d} days", horizontal=True)
    try:
        df = _frame(user.id, today - timedelta(days=span - 1), today)
    except SQLAlchemyError:
        st.error("We couldn't load your history. Please try again.")
        return

    st.subheader("Calorie trend")
    plot = df.reset_index()
    plot["7-day average"] = plot["calories"].where(df["logged"].values).rolling(7, min_periods=1).mean()
    plot["Calories"] = plot["calories"].where(df["logged"].values)
    fig = px.line(plot, x="date", y=["Calories", "7-day average"], markers=True)
    fig.add_hline(y=user.calorie_target, line_dash="dash", annotation_text="Target")
    fig.update_layout(xaxis_title=None, yaxis_title="kcal", legend_title=None)
    st.plotly_chart(fig)
    _averages(df)

    logged = df[df["logged"]]
    if logged.empty:
        st.info("Nothing logged in this period yet.")
        return

    st.subheader("Weekly averages")
    weekly = logged[["calories", "protein_g", "carbs_g", "fat_g"]].resample("W").mean().dropna(how="all")
    weekly_plot = weekly.reset_index()
    weekly_plot["Week of"] = weekly_plot["date"].dt.strftime("%b %d")
    fig2 = px.bar(weekly_plot, x="Week of", y="calories", text="calories")
    fig2.update_traces(texttemplate="%{text:.0f}")
    fig2.update_layout(xaxis_title=None, yaxis_title="avg kcal / logged day")
    st.plotly_chart(fig2)

    st.subheader("Where your calories come from (average)")
    p, c, f = (logged["protein_g"].mean() * 4, logged["carbs_g"].mean() * 4, logged["fat_g"].mean() * 9)
    if p + c + f > 0:
        pie = px.pie(names=["Protein", "Carbs", "Fat"], values=[p, c, f], hole=0.45)
        st.plotly_chart(pie)
        st.caption("Estimated using 4 kcal/g for protein and carbs and 9 kcal/g for fat.")


def render() -> None:
    user = require_user()
    today = user_today(user.timezone)
    st.title("📅 History")
    show_flash()
    daily, weekly, monthly = st.tabs(["Daily log", "Weekly", "Monthly trends"])
    with daily:
        _daily_tab(user, today)
    with weekly:
        _weekly_tab(user, today)
    with monthly:
        _monthly_tab(user, today)
