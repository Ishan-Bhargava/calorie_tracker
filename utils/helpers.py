"""Small, framework-independent helpers (dates, meals, formatting)."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone, tzinfo
from zoneinfo import ZoneInfo, available_timezones

MEAL_TYPES: list[str] = ["Breakfast", "Lunch", "Dinner", "Snack", "Other"]

DISCLAIMER = (
    "Calorie and nutrition values are estimates and are not a substitute for "
    "professional medical or dietary advice."
)

_MEAL_ALIASES = {
    "breakfast": "Breakfast",
    "brunch": "Breakfast",
    "lunch": "Lunch",
    "dinner": "Dinner",
    "supper": "Dinner",
    "snack": "Snack",
    "snacks": "Snack",
    "other": "Other",
}


def normalize_meal_type(value: object) -> str:
    if not value:
        return "Other"
    return _MEAL_ALIASES.get(str(value).strip().lower(), "Other")


def get_zone(name: str | None) -> tzinfo:
    try:
        return ZoneInfo(name or "UTC")
    except Exception:  # unknown zone or tzdata missing
        return timezone.utc


def now_in_tz(tz_name: str | None) -> datetime:
    return datetime.now(get_zone(tz_name))


def user_today(tz_name: str | None) -> date:
    return now_in_tz(tz_name).date()


def timezone_options() -> list[str]:
    try:
        zones = sorted(available_timezones())
    except Exception:
        zones = []
    return zones or ["UTC"]


def parse_iso_date(value: object) -> date | None:
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def day_label(d: date, today: date) -> str:
    if d == today:
        return "today"
    if d == today - timedelta(days=1):
        return "yesterday"
    return f"{d:%A, %b %d, %Y}"


def fmt_int(value: float | int | None) -> str:
    return f"{float(value or 0):,.0f}"


def fmt_qty(q: float | None) -> str:
    q = float(q or 0)
    return f"{q:g}"


def progress_status(ratio: float) -> tuple[str, str]:
    """Map progress toward the calorie target to (label, hex colour)."""
    if ratio < 0.90:
        return "On track", "#22c55e"
    if ratio <= 1.05:
        return "Near target", "#eab308"
    return "Over target", "#ef4444"
