"""Registration, login, logout and session handling."""
from __future__ import annotations

import base64
import hashlib
import re
import time
from typing import Optional

import bcrypt
import streamlit as st
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from database import repository as repo
from database.db import session_scope
from database.models import UserInfo
from utils.config import get_default_timezone

USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,32}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8
MAX_PASSWORD_LEN = 128

_SESSION_KEY = "user_id"
_FAIL_KEY = "_login_failures"
_MAX_FAILURES = 5
_LOCKOUT_SECONDS = 60


class AuthError(Exception):
    """Raised with a message that is safe to show to the user."""


# ---------------------------------------------------------------- hashing


def _prehash(password: str) -> bytes:
    # SHA-256 + base64 keeps the input under bcrypt's 72-byte limit for any password length.
    return base64.b64encode(hashlib.sha256(password.encode("utf-8")).digest())


def hash_password(password: str) -> str:
    return bcrypt.hashpw(_prehash(password), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(_prehash(password), password_hash.encode("utf-8"))
    except ValueError:
        return False


_dummy_hash: str | None = None


def _burn_hash_time(password: str) -> None:
    """Spend comparable time when the user doesn't exist (limits user enumeration by timing)."""
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = hash_password("not-a-real-password")
    verify_password(password, _dummy_hash)


# ------------------------------------------------------------- validation


def validate_password(password: str) -> None:
    if len(password) < MIN_PASSWORD_LEN:
        raise AuthError(f"Password must be at least {MIN_PASSWORD_LEN} characters long.")
    if len(password) > MAX_PASSWORD_LEN:
        raise AuthError(f"Password must be at most {MAX_PASSWORD_LEN} characters long.")
    if password.isdigit() or password.isalpha():
        raise AuthError("Password must contain a mix of letters and numbers/symbols.")


# ------------------------------------------------------------ operations


def register_user(
    username: str, email: str, password: str, confirm: str, timezone: str | None = None
) -> UserInfo:
    username = (username or "").strip()
    email = (email or "").strip().lower()

    if not USERNAME_RE.match(username):
        raise AuthError(
            "Username must be 3-32 characters: letters, numbers, '.', '_' or '-'."
        )
    if len(email) > 254 or not EMAIL_RE.match(email):
        raise AuthError("Please enter a valid email address.")
    validate_password(password or "")
    if password != confirm:
        raise AuthError("Passwords do not match.")

    try:
        with session_scope() as s:
            if repo.username_exists(s, username):
                raise AuthError("That username is already taken.")
            if repo.email_exists(s, email):
                raise AuthError("An account with that email already exists.")
            user = repo.create_user(
                s, username, email, hash_password(password), timezone or get_default_timezone()
            )
            return user.to_info()
    except IntegrityError:
        # Lost a race against another signup; the unique constraints caught it.
        raise AuthError("That username or email is already registered.") from None
    except SQLAlchemyError:
        raise AuthError("We couldn't create your account due to a database problem.") from None


def _check_lockout() -> None:
    now = time.time()
    failures = [t for t in st.session_state.get(_FAIL_KEY, []) if now - t < _LOCKOUT_SECONDS]
    st.session_state[_FAIL_KEY] = failures
    if len(failures) >= _MAX_FAILURES:
        wait = int(_LOCKOUT_SECONDS - (now - failures[0])) + 1
        raise AuthError(f"Too many failed attempts. Please try again in {wait} seconds.")


def authenticate(login: str, password: str) -> UserInfo:
    login = (login or "").strip()
    if not login or not password:
        raise AuthError("Please enter your username/email and password.")
    _check_lockout()
    try:
        with session_scope() as s:
            user = repo.find_user_by_login(s, login)
            if user is None:
                _burn_hash_time(password)
                ok = False
            else:
                ok = verify_password(password, user.password_hash)
            if not ok:
                st.session_state.setdefault(_FAIL_KEY, []).append(time.time())
                raise AuthError("Invalid username/email or password.")
            return user.to_info()
    except SQLAlchemyError:
        raise AuthError("We couldn't sign you in due to a database problem.") from None


def change_password(user_id: int, current: str, new: str, confirm: str) -> None:
    validate_password(new)
    if new != confirm:
        raise AuthError("New passwords do not match.")
    try:
        with session_scope() as s:
            user = repo.get_user(s, user_id)
            if user is None or not verify_password(current, user.password_hash):
                raise AuthError("Your current password is incorrect.")
            repo.update_password_hash(s, user_id, hash_password(new))
    except SQLAlchemyError:
        raise AuthError("We couldn't update your password due to a database problem.") from None


# ---------------------------------------------------------------- session


def login_user(user: UserInfo) -> None:
    st.session_state.clear()
    st.session_state[_SESSION_KEY] = user.id


def logout() -> None:
    st.session_state.clear()


def get_current_user() -> Optional[UserInfo]:
    """Return the logged-in user (fresh from the DB) or None."""
    user_id = st.session_state.get(_SESSION_KEY)
    if user_id is None:
        return None
    try:
        with session_scope() as s:
            user = repo.get_user(s, user_id)
            info = user.to_info() if user else None
    except SQLAlchemyError:
        st.error("Database error. Please try again.")
        st.stop()
    if info is None:
        st.session_state.clear()
    return info


def require_user() -> UserInfo:
    """For pages: return the current user or stop rendering."""
    user = get_current_user()
    if user is None:
        st.warning("Please log in to continue.")
        st.stop()
    return user
