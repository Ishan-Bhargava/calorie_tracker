"""Gemini-powered calorie agent.

Flow per user message:
 1. Build a small context (date, targets, recent totals, recent chat) from the DB.
 2. Ask Gemini for a structured JSON "plan" (intent + foods + dates).
 3. Validate the plan with pydantic and sanity-check dates / numbers.
 4. Python performs the DB operation (Gemini never touches the database).
 5. For queries, fetch the data in Python and let Gemini phrase the answer.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal, Optional

import httpx
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator
from sqlalchemy.exc import SQLAlchemyError

from agent.prompts import COMPOSE_SYSTEM_PROMPT, SYSTEM_PROMPT, build_context
from database import repository as repo
from database.db import session_scope
from database.models import UserInfo
from utils.config import get_gemini_api_key, get_gemini_model
from utils.helpers import (
    MEAL_TYPES,
    day_label,
    fmt_int,
    fmt_qty,
    normalize_meal_type,
    now_in_tz,
    parse_iso_date,
)

logger = logging.getLogger(__name__)

Intent = Literal[
    "add_food",
    "update_food",
    "delete_food",
    "query_day",
    "query_history",
    "nutrition_question",
    "general_chat",
]

MAX_FOODS_PER_MESSAGE = 30
MAX_HISTORY_DAYS = 366
MAX_PAST_DAYS = 3650  # refuse to log food more than ~10 years ago


# --------------------------------------------------------------- exceptions


class AgentError(Exception):
    """AI service problem. The message is safe to show to the user."""


class ActionRejected(Exception):
    """The AI's plan was understood but can't be applied. Message is shown as the reply."""


# ------------------------------------------------------- structured output


class FoodItem(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str = ""
    quantity: Optional[float] = Field(default=None, ge=0, le=100000)
    unit: Optional[str] = None
    meal_type: Optional[str] = None
    calories: Optional[float] = Field(default=None, ge=0, le=10000)
    protein_g: Optional[float] = Field(default=None, ge=0, le=1000)
    carbs_g: Optional[float] = Field(default=None, ge=0, le=2000)
    fat_g: Optional[float] = Field(default=None, ge=0, le=1000)
    calories_source: Literal["user", "estimated"] = "estimated"
    log_id: Optional[int] = None
    new_name: Optional[str] = None
    notes: Optional[str] = None

    @field_validator("name", mode="before")
    @classmethod
    def _clean_name(cls, v: Any) -> str:
        return str(v or "").strip()

    @field_validator("calories_source", mode="before")
    @classmethod
    def _clean_source(cls, v: Any) -> str:
        v = str(v or "estimated").strip().lower()
        return v if v in {"user", "estimated"} else "estimated"


class AgentResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    intent: Intent
    date: Optional[str] = None
    start_date: Optional[str] = None
    end_date: Optional[str] = None
    new_date: Optional[str] = None
    meal_type: Optional[str] = None
    needs_clarification: bool = False
    foods: list[FoodItem] = Field(default_factory=list)
    reply: str = ""

    @field_validator("foods", mode="before")
    @classmethod
    def _none_to_list(cls, v: Any) -> Any:
        return [] if v is None else v

    @field_validator("reply", mode="before")
    @classmethod
    def _clean_reply(cls, v: Any) -> str:
        return "" if v is None else str(v)


@dataclass
class AgentResult:
    reply: str
    intent: str
    changed: bool = False
    affected_dates: list[date] = field(default_factory=list)


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    if not text.startswith("{"):
        start, end = text.find("{"), text.rfind("}")
        if start == -1 or end <= start:
            raise ValueError("No JSON object found in model output")
        text = text[start : end + 1]
    data = json.loads(text)
    if not isinstance(data, dict):
        raise ValueError("Model output is not a JSON object")
    return data


# ----------------------------------------------------------- formatting


def _fmt_entry_for_prompt(e: dict[str, Any]) -> str:
    src = "user-provided" if e["calorie_source"] == "user" else "estimated"
    return (
        f"[id {e['id']}] {e['date']} {e['meal_type']}: {e['food_name']} "
        f"({fmt_qty(e['quantity'])} {e['unit']}) {e['calories']:.0f} kcal "
        f"P{e['protein_g']:.0f} C{e['carbs_g']:.0f} F{e['fat_g']:.0f} ({src})"
    )


def _fmt_entry_line(e: dict[str, Any]) -> str:
    tilde = "" if e["calorie_source"] == "user" else "~"
    return (
        f"- {e['meal_type']}: {e['food_name']} ({fmt_qty(e['quantity'])} {e['unit']}) "
        f"- {tilde}{e['calories']:.0f} kcal, P {e['protein_g']:.0f} g, "
        f"C {e['carbs_g']:.0f} g, F {e['fat_g']:.0f} g"
    )


def _fmt_totals(t: dict[str, float]) -> str:
    return (
        f"{fmt_int(t['calories'])} kcal · P {fmt_int(t['protein_g'])} g · "
        f"C {fmt_int(t['carbs_g'])} g · F {fmt_int(t['fat_g'])} g"
    )


def _resolve_date(raw: Optional[str], today: date, default: Optional[date] = None) -> date:
    if raw is None or str(raw).strip() == "":
        return default or today
    d = parse_iso_date(raw)
    if d is None:
        raise ActionRejected(
            "I couldn't work out which date you meant. Could you give me the date (e.g. 2026-10-04)?"
        )
    if d > today:
        raise ActionRejected("I can't log or change food for a future date.")
    if (today - d).days > MAX_PAST_DAYS:
        raise ActionRejected("That date is too far in the past for me to record.")
    return d


# ------------------------------------------------------------------ agent


class CalorieAgent:
    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None) -> None:
        self._api_key = api_key or get_gemini_api_key()
        self._model = model or get_gemini_model()
        self._client: Optional[genai.Client] = None

    # ----- Gemini plumbing

    def _get_client(self) -> genai.Client:
        if not self._api_key:
            raise AgentError(
                "The AI assistant isn't configured yet: GEMINI_API_KEY is missing. "
                "Add it to your .env file and restart the app."
            )
        if self._client is None:
            self._client = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(timeout=60_000),
            )
        return self._client

    def _generate(self, system: str, prompt: str, json_mode: bool) -> str:
        client = self._get_client()
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=0.2,
            response_mime_type="application/json" if json_mode else None,
        )
        try:
            response = client.models.generate_content(
                model=self._model, contents=prompt, config=config
            )
        except genai_errors.APIError as exc:
            raise self._translate_api_error(exc) from exc
        except (httpx.HTTPError, ConnectionError, TimeoutError, OSError) as exc:
            logger.warning("Gemini network failure: %s", exc)
            if "CERTIFICATE_VERIFY_FAILED" in str(exc) or "ssl" in str(exc).lower():
                raise AgentError(
                    "A secure connection to the AI service couldn't be verified (SSL certificate error). "
                    "If you're behind a corporate proxy, set SSL_CERT_FILE to your company's CA bundle (.pem)."
                ) from exc
            raise AgentError(
                "I couldn't reach the AI service. Please check your internet connection and try again."
            ) from exc

        try:
            text = response.text
        except Exception:  # blocked / no candidates
            text = None
        if not text or not text.strip():
            raise AgentError(
                "The AI returned an empty response (it may have been filtered). Please rephrase and try again."
            )
        return text

    @staticmethod
    def _translate_api_error(exc: genai_errors.APIError) -> AgentError:
        code = getattr(exc, "code", None)
        text = str(exc).lower()
        logger.warning("Gemini API error %s: %s", code, exc)
        if code in (401, 403) or "api key" in text or "api_key" in text:
            return AgentError(
                "The Gemini API key is invalid or doesn't have access. Check GEMINI_API_KEY in your .env file."
            )
        if code == 404:
            return AgentError(
                "The configured Gemini model wasn't found. Check GEMINI_MODEL in your .env file."
            )
        if code == 429:
            return AgentError(
                "The AI service is rate-limited or out of quota right now. Please wait a moment and try again."
            )
        if code is not None and code >= 500:
            return AgentError("The AI service is temporarily unavailable. Please try again shortly.")
        return AgentError("The AI service returned an error. Please try again.")

    def _plan(self, context: str, message: str) -> AgentResponse:
        prompt = f"{context}\nUser's new message:\n\"\"\"\n{message}\n\"\"\"\n"
        last_error: Exception | None = None
        for attempt in range(2):
            text = self._generate(SYSTEM_PROMPT, prompt, json_mode=True)
            try:
                return AgentResponse.model_validate(_extract_json(text))
            except (ValueError, TypeError, ValidationError) as exc:
                last_error = exc
                logger.warning("Invalid Gemini JSON (attempt %d): %s", attempt + 1, exc)
                prompt += (
                    "\nIMPORTANT: your previous output was not valid for the schema. "
                    "Return ONLY one valid JSON object that matches the schema."
                )
        logger.error("Gemini returned unusable output: %s", last_error)
        raise AgentError(
            "I got a confusing answer from the AI and didn't change anything. Please try rephrasing."
        )

    # ----- context

    def _build_context(self, user: UserInfo, now: datetime) -> str:
        today = now.date()
        with session_scope() as s:
            totals = repo.day_totals(s, user.id, today)
            recent = {
                r["date"]: r["calories"]
                for r in repo.daily_totals_range(s, user.id, today - timedelta(days=6), today)
            }
            entries = repo.list_foods(s, user.id, today - timedelta(days=1), today, limit=60)
            history = repo.get_recent_chat_messages(s, user.id, limit=8)

        recent_text = "\n".join(
            f"{today - timedelta(days=i):%Y-%m-%d (%a)}: "
            f"{recent.get(today - timedelta(days=i), 0):.0f} kcal"
            for i in range(6, -1, -1)
        )
        entries_text = "\n".join(_fmt_entry_for_prompt(e) for e in entries) or "(none)"
        history_text = (
            "\n".join(f"{m['role'].capitalize()}: {m['message'][:400]}" for m in history) or "(none)"
        )
        return build_context(
            now_text=now.strftime("%Y-%m-%d (%A) %H:%M"),
            timezone=user.timezone,
            calorie_target=user.calorie_target,
            protein_target=user.protein_target_g,
            carbs_target=user.carbs_target_g,
            fat_target=user.fat_target_g,
            today_total_kcal=totals["calories"],
            recent_days_text=recent_text,
            entries_text=entries_text,
            history_text=history_text,
        )

    # ----- public API

    def respond(self, user: UserInfo, message: str) -> AgentResult:
        message = (message or "").strip()
        if not message:
            return AgentResult(
                reply="Tell me what you ate (e.g. “two eggs and toast for breakfast”) or ask me about your intake.",
                intent="general_chat",
            )
        message = message[:2000]

        now = now_in_tz(user.timezone)
        try:
            context = self._build_context(user, now)
        except SQLAlchemyError:
            logger.exception("DB error while building context")
            raise AgentError("I couldn't read your data because of a database problem.") from None

        plan = self._plan(context, message)

        try:
            return self._execute(user, plan, now.date(), message, context)
        except ActionRejected as exc:
            return AgentResult(reply=str(exc), intent=plan.intent)
        except SQLAlchemyError:
            logger.exception("DB error while applying plan")
            return AgentResult(
                reply="Sorry, I couldn't save that because of a database problem. Nothing was changed - please try again.",
                intent=plan.intent,
            )

    # ----- execution

    def _execute(
        self, user: UserInfo, plan: AgentResponse, today: date, message: str, context: str
    ) -> AgentResult:
        intent = plan.intent
        if plan.needs_clarification and intent in {"add_food", "update_food", "delete_food"}:
            return AgentResult(
                reply=plan.reply or "Could you give me a bit more detail?", intent=intent
            )
        if intent == "add_food":
            return self._add(user, plan, today)
        if intent == "update_food":
            return self._update(user, plan, today)
        if intent == "delete_food":
            return self._delete(user, plan, today)
        if intent in {"query_day", "query_history"}:
            return self._query(user, plan, today, message, context)
        return AgentResult(
            reply=plan.reply or "I'm here to help you track your meals and calories!",
            intent=intent,
        )

    def _add(self, user: UserInfo, plan: AgentResponse, today: date) -> AgentResult:
        foods = plan.foods[:MAX_FOODS_PER_MESSAGE]
        if not foods:
            return AgentResult(
                reply=plan.reply
                or "I didn't catch any food in that. What did you eat, and roughly how much?",
                intent=plan.intent,
            )
        d = _resolve_date(plan.date, today)

        problems = [f.name or "(unnamed item)" for f in foods if not f.name]
        missing_cal = [f.name for f in foods if f.name and f.calories is None]
        if problems:
            raise ActionRejected("I couldn't tell what one of the foods was. Could you list them again?")
        if missing_cal:
            raise ActionRejected(
                "I couldn't estimate calories for: "
                + ", ".join(missing_cal)
                + ". Could you describe the portion size, or tell me the calories?"
            )

        saved: list[dict[str, Any]] = []
        with session_scope() as s:
            for f in foods:
                saved.append(
                    repo.add_food(
                        s,
                        user.id,
                        date=d,
                        meal_type=normalize_meal_type(f.meal_type or plan.meal_type),
                        food_name=f.name[:120],
                        quantity=f.quantity if f.quantity else 1.0,
                        unit=(f.unit or "serving").strip()[:30] or "serving",
                        calories=f.calories or 0.0,
                        protein_g=f.protein_g or 0.0,
                        carbs_g=f.carbs_g or 0.0,
                        fat_g=f.fat_g or 0.0,
                        calorie_source=f.calories_source,
                        notes=(f.notes or None) and f.notes[:300],
                    )
                )
            totals = repo.day_totals(s, user.id, d)

        lines = "\n".join(_fmt_entry_line(e) for e in saved)
        added_kcal = sum(e["calories"] for e in saved)
        summary = (
            f"**✅ Saved to {day_label(d, today)} ({d.isoformat()}):**\n{lines}\n\n"
            f"Added: {fmt_int(added_kcal)} kcal · Day total now: {_fmt_totals(totals)}"
        )
        if any(e["calorie_source"] == "estimated" for e in saved):
            summary += "\n\n_“~” marks estimated values._"
        return AgentResult(
            reply=f"{plan.reply}\n\n{summary}".strip(),
            intent=plan.intent,
            changed=True,
            affected_dates=[d],
        )

    def _find_target(
        self, s, user: UserInfo, f: FoodItem, plan: AgentResponse, today: date
    ) -> dict[str, Any]:
        """Locate exactly one of the user's entries described by the AI."""
        if f.log_id is not None:
            found = repo.get_food(s, user.id, f.log_id)
            if found:
                return found
        if not f.name:
            raise ActionRejected("Which food entry did you mean? Please tell me its name.")
        if plan.date:
            d = _resolve_date(plan.date, today)
            start, end = d, d
        else:
            start, end = today - timedelta(days=7), today
        matches = repo.find_foods_by_name(s, user.id, start, end, f.name)
        if not matches:
            raise ActionRejected(
                f"I couldn't find “{f.name}” in your log"
                + (f" for {start.isoformat()}" if start == end else " in the last week")
                + ". Which day was it?"
            )
        if len(matches) > 1:
            options = "; ".join(
                f"{m['date']} {m['meal_type']}: {m['food_name']} ({m['calories']:.0f} kcal)"
                for m in matches[:6]
            )
            raise ActionRejected(
                f"I found several entries matching “{f.name}”: {options}. Which one did you mean?"
            )
        return matches[0]

    def _update(self, user: UserInfo, plan: AgentResponse, today: date) -> AgentResult:
        if not plan.foods:
            return AgentResult(
                reply=plan.reply or "Which entry would you like to change, and to what?",
                intent=plan.intent,
            )
        new_date = _resolve_date(plan.new_date, today) if plan.new_date else None
        lines: list[str] = []
        affected: set[date] = set()
        with session_scope() as s:
            for f in plan.foods[:MAX_FOODS_PER_MESSAGE]:
                target = self._find_target(s, user, f, plan, today)
                changes: dict[str, Any] = {}
                if f.new_name:
                    changes["food_name"] = f.new_name[:120]
                if f.quantity:
                    changes["quantity"] = f.quantity
                if f.unit:
                    changes["unit"] = f.unit.strip()[:30]
                if f.meal_type:
                    changes["meal_type"] = normalize_meal_type(f.meal_type)
                for key in ("calories", "protein_g", "carbs_g", "fat_g"):
                    value = getattr(f, key)
                    if value is not None:
                        changes[key] = value
                if f.calories is not None:
                    changes["calorie_source"] = f.calories_source
                if new_date:
                    changes["date"] = new_date
                if not changes:
                    raise ActionRejected(
                        f"I'm not sure what to change about “{target['food_name']}”. What should it be?"
                    )
                updated = repo.update_food(s, user.id, target["id"], **changes)
                affected.update({target["date"], updated["date"]})
                lines.append(_fmt_entry_line(updated))
            totals = {d: repo.day_totals(s, user.id, d) for d in sorted(affected)}

        total_lines = "\n".join(
            f"- {day_label(d, today)} ({d.isoformat()}): {_fmt_totals(t)}" for d, t in totals.items()
        )
        summary = "**✏️ Updated:**\n" + "\n".join(lines) + f"\n\nDay totals now:\n{total_lines}"
        return AgentResult(
            reply=f"{plan.reply}\n\n{summary}".strip(),
            intent=plan.intent,
            changed=True,
            affected_dates=sorted(affected),
        )

    def _delete(self, user: UserInfo, plan: AgentResponse, today: date) -> AgentResult:
        if not plan.foods:
            return AgentResult(
                reply=plan.reply or "Which entry would you like me to delete?", intent=plan.intent
            )
        removed: list[dict[str, Any]] = []
        with session_scope() as s:
            seen: set[int] = set()
            for f in plan.foods[:MAX_FOODS_PER_MESSAGE]:
                target = self._find_target(s, user, f, plan, today)
                if target["id"] in seen:
                    continue
                seen.add(target["id"])
                if repo.delete_food(s, user.id, target["id"]):
                    removed.append(target)
            affected = sorted({r["date"] for r in removed})
            totals = {d: repo.day_totals(s, user.id, d) for d in affected}

        if not removed:
            raise ActionRejected("I couldn't find anything to delete.")
        lines = "\n".join(
            f"- {r['date']} {r['meal_type']}: {r['food_name']} ({r['calories']:.0f} kcal)"
            for r in removed
        )
        total_lines = "\n".join(
            f"- {day_label(d, today)} ({d.isoformat()}): {_fmt_totals(t)}" for d, t in totals.items()
        )
        summary = f"**🗑️ Removed:**\n{lines}\n\nDay totals now:\n{total_lines}"
        return AgentResult(
            reply=f"{plan.reply}\n\n{summary}".strip(),
            intent=plan.intent,
            changed=True,
            affected_dates=affected,
        )

    # ----- queries

    def _query(
        self, user: UserInfo, plan: AgentResponse, today: date, message: str, context: str
    ) -> AgentResult:
        if plan.intent == "query_day":
            d = _resolve_date(plan.date, today)
            start = end = d
        else:
            end = _resolve_date(plan.end_date, today)
            start = _resolve_date(plan.start_date, today, default=end - timedelta(days=6))
            if start > end:
                start, end = end, start
            if (end - start).days + 1 > MAX_HISTORY_DAYS:
                start = end - timedelta(days=MAX_HISTORY_DAYS - 1)

        with session_scope() as s:
            totals_by_day = {
                r["date"]: r for r in repo.daily_totals_range(s, user.id, start, end)
            }
            entries = (
                repo.list_foods(s, user.id, start, end, limit=120)
                if (end - start).days <= 13
                else []
            )

        data_text = self._format_query_data(totals_by_day, entries, start, end, today, user)
        prompt = (
            f"Current local date: {today.isoformat()} ({today:%A})\n"
            f"User's daily calorie target: {user.calorie_target} kcal\n"
            f"User question: {message}\n\n"
            f"Database results (authoritative):\n{data_text}\n\n"
            "Write the answer for the user."
        )
        try:
            reply = self._generate(COMPOSE_SYSTEM_PROMPT, prompt, json_mode=False).strip()
        except AgentError:
            logger.warning("Falling back to plain data summary for query reply")
            reply = f"Here's what I found:\n\n{data_text}"
        return AgentResult(reply=reply, intent=plan.intent)

    @staticmethod
    def _format_query_data(
        totals_by_day: dict[date, dict[str, Any]],
        entries: list[dict[str, Any]],
        start: date,
        end: date,
        today: date,
        user: UserInfo,
    ) -> str:
        if not totals_by_day:
            return f"No food was logged between {start.isoformat()} and {end.isoformat()}."

        by_day: dict[date, list[dict[str, Any]]] = {}
        for e in entries:
            by_day.setdefault(e["date"], []).append(e)

        blocks: list[str] = []
        grand = {"calories": 0.0, "protein_g": 0.0, "carbs_g": 0.0, "fat_g": 0.0}
        for d in sorted(totals_by_day):
            t = totals_by_day[d]
            for k in grand:
                grand[k] += t[k]
            head = f"**{d:%A, %b %d, %Y} ({day_label(d, today)})** - {_fmt_totals(t)}"
            lines = [_fmt_entry_line(e) for e in sorted(
                by_day.get(d, []), key=lambda x: MEAL_TYPES.index(x["meal_type"])
                if x["meal_type"] in MEAL_TYPES else len(MEAL_TYPES)
            )]
            blocks.append("\n".join([head, *lines]))

        days_logged = len(totals_by_day)
        span = (end - start).days + 1
        footer = ""
        if span > 1:
            footer = (
                f"\n\nPeriod {start.isoformat()} to {end.isoformat()}: {days_logged} of {span} days logged; "
                f"total {fmt_int(grand['calories'])} kcal; average per logged day "
                f"{fmt_int(grand['calories'] / days_logged)} kcal "
                f"(target {user.calorie_target} kcal/day)."
            )
        return "\n\n".join(blocks) + footer
