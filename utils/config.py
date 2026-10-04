"""Central configuration, read from environment variables / the .env file."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

# Trust the operating system's certificate store (needed behind corporate HTTPS-inspecting
# proxies whose root CA is installed in Windows/macOS but unknown to Python's bundled CAs).
try:
    import truststore

    truststore.inject_into_ssl()
except Exception:  # package missing or unsupported: fall back to default certificates
    pass

_PLACEHOLDER_KEYS = {"", "your_api_key_here", "changeme"}


def get_gemini_api_key() -> str | None:
    """Return the Gemini API key (server side only) or None if not configured."""
    key = os.getenv("GEMINI_API_KEY", "").strip()
    return None if key.lower() in _PLACEHOLDER_KEYS else key


def get_gemini_model() -> str:
    return os.getenv("GEMINI_MODEL", "gemini-3.8-flash").strip() or "gemini-3.8-flash"


def get_database_url() -> str:
    url = os.getenv("DATABASE_URL", "").strip()
    if url:
        # Providers such as Neon give "postgres://" or "postgresql://" URLs; SQLAlchemy needs
        # the driver name to use psycopg (v3).
        for prefix in ("postgres://", "postgresql://"):
            if url.startswith(prefix):
                return "postgresql+psycopg://" + url[len(prefix):]
        return url
    return f"sqlite:///{(BASE_DIR / 'calorie_tracker.db').as_posix()}"


def get_default_timezone() -> str:
    return os.getenv("DEFAULT_TIMEZONE", "UTC").strip() or "UTC"
