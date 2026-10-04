"""All database reads/writes. Every query is scoped by ``user_id``.

Functions take an open ``Session`` (from ``session_scope()``) so callers control
the transaction boundary. They return plain dicts / detached values.
"""
from __future__ import annotations

from datetime import date
from typing import Any, Optional

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from database.models import ChatMessage, FoodLog, User

# ----------------------------------------------------------------- users

_USER_SETTINGS_FIELDS = {
    "calorie_target",
    "protein_target_g",
    "carbs_target_g",
    "fat_target_g",
    "timezone",
}


def create_user(
    s: Session, username: str, email: str, password_hash: str, timezone: str
) -> User:
    user = User(
        username=username, email=email, password_hash=password_hash, timezone=timezone
    )
    s.add(user)
    s.flush()
    return user


def get_user(s: Session, user_id: int) -> Optional[User]:
    return s.get(User, user_id)


def find_user_by_login(s: Session, login: str) -> Optional[User]:
    """Look up by username (case-insensitive) or e-mail (lower-cased)."""
    key = login.strip().lower()
    return s.scalars(
        select(User).where((func.lower(User.username) == key) | (User.email == key))
    ).first()


def username_exists(s: Session, username: str) -> bool:
    return (
        s.scalar(select(func.count()).select_from(User).where(func.lower(User.username) == username.lower()))
        or 0
    ) > 0


def email_exists(s: Session, email: str) -> bool:
    return (
        s.scalar(select(func.count()).select_from(User).where(User.email == email.lower())) or 0
    ) > 0


def update_user_settings(s: Session, user_id: int, **fields: Any) -> None:
    user = s.get(User, user_id)
    if user is None:
        raise LookupError("User not found")
    for key, value in fields.items():
        if key not in _USER_SETTINGS_FIELDS:
            raise ValueError(f"Unknown setting: {key}")
        setattr(user, key, value)


def update_password_hash(s: Session, user_id: int, password_hash: str) -> None:
    user = s.get(User, user_id)
    if user is None:
        raise LookupError("User not found")
    user.password_hash = password_hash


# ------------------------------------------------------------- food logs

_FOOD_UPDATE_FIELDS = {
    "date",
    "meal_type",
    "food_name",
    "quantity",
    "unit",
    "calories",
    "protein_g",
    "carbs_g",
    "fat_g",
    "calorie_source",
    "notes",
}


def add_food(
    s: Session,
    user_id: int,
    *,
    date: date,
    meal_type: str,
    food_name: str,
    quantity: float = 1.0,
    unit: str = "serving",
    calories: float = 0.0,
    protein_g: float = 0.0,
    carbs_g: float = 0.0,
    fat_g: float = 0.0,
    calorie_source: str = "estimated",
    notes: Optional[str] = None,
) -> dict[str, Any]:
    log = FoodLog(
        user_id=user_id,
        date=date,
        meal_type=meal_type,
        food_name=food_name,
        quantity=quantity,
        unit=unit,
        calories=calories,
        protein_g=protein_g,
        carbs_g=carbs_g,
        fat_g=fat_g,
        calorie_source=calorie_source,
        notes=notes,
    )
    s.add(log)
    s.flush()
    return log.to_dict()


def _owned_food(s: Session, user_id: int, log_id: int) -> Optional[FoodLog]:
    return s.scalars(
        select(FoodLog).where(FoodLog.id == log_id, FoodLog.user_id == user_id)
    ).first()


def get_food(s: Session, user_id: int, log_id: int) -> Optional[dict[str, Any]]:
    log = _owned_food(s, user_id, log_id)
    return log.to_dict() if log else None


def update_food(s: Session, user_id: int, log_id: int, **changes: Any) -> dict[str, Any]:
    log = _owned_food(s, user_id, log_id)
    if log is None:
        raise LookupError("Food entry not found")
    for key, value in changes.items():
        if key not in _FOOD_UPDATE_FIELDS:
            raise ValueError(f"Unknown field: {key}")
        setattr(log, key, value)
    s.flush()
    return log.to_dict()


def delete_food(s: Session, user_id: int, log_id: int) -> bool:
    result = s.execute(
        delete(FoodLog).where(FoodLog.id == log_id, FoodLog.user_id == user_id)
    )
    return (result.rowcount or 0) > 0


def list_foods(
    s: Session, user_id: int, start: date, end: date, limit: int = 500
) -> list[dict[str, Any]]:
    rows = s.scalars(
        select(FoodLog)
        .where(FoodLog.user_id == user_id, FoodLog.date >= start, FoodLog.date <= end)
        .order_by(FoodLog.date, FoodLog.created_at, FoodLog.id)
        .limit(limit)
    ).all()
    return [r.to_dict() for r in rows]


def list_foods_for_date(s: Session, user_id: int, d: date) -> list[dict[str, Any]]:
    return list_foods(s, user_id, d, d)


def find_foods_by_name(
    s: Session, user_id: int, start: date, end: date, name: str
) -> list[dict[str, Any]]:
    """Exact (case-insensitive) name matches first, otherwise substring matches."""
    needle = name.strip().lower()
    if not needle:
        return []
    rows = list_foods(s, user_id, start, end)
    exact = [r for r in rows if r["food_name"].lower() == needle]
    if exact:
        return exact
    return [
        r
        for r in rows
        if needle in r["food_name"].lower() or r["food_name"].lower() in needle
    ]


def day_totals(s: Session, user_id: int, d: date) -> dict[str, float]:
    row = s.execute(
        select(
            func.coalesce(func.sum(FoodLog.calories), 0.0),
            func.coalesce(func.sum(FoodLog.protein_g), 0.0),
            func.coalesce(func.sum(FoodLog.carbs_g), 0.0),
            func.coalesce(func.sum(FoodLog.fat_g), 0.0),
            func.count(FoodLog.id),
        ).where(FoodLog.user_id == user_id, FoodLog.date == d)
    ).one()
    return {
        "calories": float(row[0]),
        "protein_g": float(row[1]),
        "carbs_g": float(row[2]),
        "fat_g": float(row[3]),
        "entries": int(row[4]),
    }


def daily_totals_range(
    s: Session, user_id: int, start: date, end: date
) -> list[dict[str, Any]]:
    rows = s.execute(
        select(
            FoodLog.date,
            func.sum(FoodLog.calories),
            func.sum(FoodLog.protein_g),
            func.sum(FoodLog.carbs_g),
            func.sum(FoodLog.fat_g),
            func.count(FoodLog.id),
        )
        .where(FoodLog.user_id == user_id, FoodLog.date >= start, FoodLog.date <= end)
        .group_by(FoodLog.date)
        .order_by(FoodLog.date)
    ).all()
    return [
        {
            "date": r[0],
            "calories": float(r[1] or 0),
            "protein_g": float(r[2] or 0),
            "carbs_g": float(r[3] or 0),
            "fat_g": float(r[4] or 0),
            "entries": int(r[5]),
        }
        for r in rows
    ]


# ---------------------------------------------------------- chat messages


def add_chat_message(s: Session, user_id: int, role: str, message: str) -> None:
    s.add(ChatMessage(user_id=user_id, role=role, message=message))


def get_recent_chat_messages(
    s: Session, user_id: int, limit: int = 50
) -> list[dict[str, Any]]:
    rows = s.scalars(
        select(ChatMessage)
        .where(ChatMessage.user_id == user_id)
        .order_by(ChatMessage.id.desc())
        .limit(limit)
    ).all()
    return [r.to_dict() for r in reversed(rows)]


def clear_chat(s: Session, user_id: int) -> None:
    s.execute(delete(ChatMessage).where(ChatMessage.user_id == user_id))
