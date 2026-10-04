"""AI Coach chat page."""
from __future__ import annotations

import logging

import streamlit as st
from sqlalchemy.exc import SQLAlchemyError

from agent.calorie_agent import AgentError, CalorieAgent
from auth.authentication import require_user
from database import repository as repo
from database.db import session_scope
from utils.config import get_gemini_api_key
from utils.helpers import DISCLAIMER
from utils.ui import safe_md

logger = logging.getLogger(__name__)

WELCOME = (
    "Hi! I'm your AI Calorie Coach 🍎 Tell me what you ate and I'll log it - for example:\n\n"
    "- “I had two eggs and toast for breakfast”\n"
    "- “Yesterday I had pasta for dinner, around 700 calories”\n"
    "- “How many calories did I eat yesterday?”\n"
)


@st.cache_resource
def _get_agent() -> CalorieAgent:
    return CalorieAgent()


def render() -> None:
    user = require_user()
    st.title("💬 AI Coach")

    api_ready = get_gemini_api_key() is not None
    if not api_ready:
        st.error(
            "GEMINI_API_KEY is not configured. Add it to the `.env` file and restart the app "
            "to enable the AI Coach."
        )

    try:
        with session_scope() as s:
            history = repo.get_recent_chat_messages(s, user.id, limit=100)
    except SQLAlchemyError:
        st.error("We couldn't load your conversation. Please try again.")
        return

    if not history:
        with st.chat_message("assistant"):
            st.markdown(WELCOME)
    for m in history:
        with st.chat_message(m["role"]):
            st.markdown(safe_md(m["message"]))

    prompt = st.chat_input(
        "What did you eat? Or ask about your intake…", disabled=not api_ready, max_chars=2000
    )
    st.caption(DISCLAIMER)
    if not prompt:
        return

    with st.chat_message("user"):
        st.markdown(safe_md(prompt))
    with st.chat_message("assistant"):
        with st.spinner("Thinking…"):
            try:
                result = _get_agent().respond(user, prompt)
            except AgentError as exc:
                st.error(str(exc))
                return
            except Exception as exc:
                logger.exception("Unexpected error in chat")
                st.error(f"Something went wrong on our side ({type(exc).__name__}). Please try again.")
                return
        st.markdown(safe_md(result.reply))

    try:
        with session_scope() as s:
            repo.add_chat_message(s, user.id, "user", prompt)
            repo.add_chat_message(s, user.id, "assistant", result.reply)
    except SQLAlchemyError:
        logger.exception("Failed to persist chat messages")
        st.warning("Your food log was updated, but this message couldn't be saved to chat history.")
