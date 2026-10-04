"""Engine / session management and database initialisation.

Run ``python -m database.db`` to create the tables.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from database.models import Base
from utils.config import get_database_url

_engine: Engine | None = None
_session_factory: sessionmaker[Session] | None = None


def get_engine() -> Engine:
    global _engine
    if _engine is None:
        url = get_database_url()
        # pre_ping + recycle: serverless Postgres (e.g. Neon) closes idle connections.
        kwargs: dict = {"pool_pre_ping": True, "pool_recycle": 300}
        if url.startswith("sqlite"):
            # Streamlit runs scripts in worker threads.
            kwargs["connect_args"] = {"check_same_thread": False}
        _engine = create_engine(url, **kwargs)

        if url.startswith("sqlite"):

            @event.listens_for(_engine, "connect")
            def _enable_sqlite_fk(dbapi_conn, _record) -> None:  # pragma: no cover
                cur = dbapi_conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

    return _engine


def get_session_factory() -> sessionmaker[Session]:
    global _session_factory
    if _session_factory is None:
        _session_factory = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _session_factory


@contextmanager
def session_scope() -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any error."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db() -> None:
    """Create all tables if they do not exist yet."""
    Base.metadata.create_all(get_engine())


if __name__ == "__main__":
    init_db()
    print(f"Database initialised at {get_database_url()}")
