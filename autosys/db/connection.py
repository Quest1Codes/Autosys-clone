"""
Database connection and session management.

Provides two surfaces:
  - Async (aiosqlite) — used by the Scheduler ACE daemon and FastAPI endpoints
  - Sync  (regular SQLite) — used by CLI commands and tests

Both share the same SQLAlchemy metadata / schema so migrations only need
to run once.

Usage (async)
-------------
    from autosys.db.connection import async_session

    async with async_session() as session:
        result = await session.execute(select(JobRow))
        jobs = result.scalars().all()

Usage (sync / CLI)
------------------
    from autosys.db.connection import sync_session

    with sync_session() as session:
        job = session.get(JobRow, "my_job")

Engine caching
--------------
Engines are cached *by URL* (not as module-level singletons) so that:
  1. Different tests can use different SQLite files without interference.
  2. The AUTOSYS_DB_URL environment variable is read at *session open time*,
     not at module import time — allowing monkeypatch.setenv() to work.

Call reset_engines() in test teardown to discard all cached engines and
close their connection pools.
"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import AsyncGenerator, Generator

from sqlalchemy import create_engine, event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import Session, sessionmaker

from autosys.db.schema import Base


# ---------------------------------------------------------------------------
# Resolve DB URL
# ---------------------------------------------------------------------------

_DEFAULT_DB_PATH = Path(__file__).parents[2] / "data" / "autosys.db"


def _get_db_url(async_: bool = True) -> str:
    """
    Return the SQLAlchemy database URL, reading the env var at call time.

    Priority order:
    1. AUTOSYS_DB_URL environment variable (allows tests to override)
    2. Default path: <project_root>/data/autosys.db
    """
    raw = os.environ.get("AUTOSYS_DB_URL")
    if raw:
        if async_:
            if raw.startswith("sqlite:///") and "aiosqlite" not in raw:
                return raw.replace("sqlite:///", "sqlite+aiosqlite:///", 1)
            elif raw.startswith("postgresql://") and "asyncpg" not in raw:
                return raw.replace("postgresql://", "postgresql+asyncpg://", 1)
        return raw

    _DEFAULT_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    path_str = str(_DEFAULT_DB_PATH)
    if async_:
        return f"sqlite+aiosqlite:///{path_str}"
    return f"sqlite:///{path_str}"


# ---------------------------------------------------------------------------
# Engine cache — keyed by URL string so each test DB gets its own engine
# ---------------------------------------------------------------------------

_sync_engines:  dict[str, object] = {}   # url → Engine
_async_engines: dict[str, object] = {}   # url → AsyncEngine
_sync_session_factories:  dict[str, object] = {}
_async_session_factories: dict[str, object] = {}


def reset_engines() -> None:
    """
    Dispose all cached engines and clear the cache.

    Call this in test teardown (or the test fixture's autouse cleanup) so
    that each test starts with a fresh connection pool pointing at the
    correct database file.
    """
    for eng in list(_sync_engines.values()):
        try:
            eng.dispose()
        except Exception:
            pass
    _sync_engines.clear()
    _sync_session_factories.clear()

    # Async engines are disposed later (they need an event loop).
    # For tests that only use sync sessions this is sufficient.
    _async_engines.clear()
    _async_session_factories.clear()


# ---------------------------------------------------------------------------
# Sync engine + session factory  (CLI and tests)
# ---------------------------------------------------------------------------

def get_sync_engine():
    """
    Return a sync Engine for the current AUTOSYS_DB_URL, creating it once
    per URL and caching it for reuse within the same process lifetime.
    """
    url = _get_db_url(async_=False)
    if url not in _sync_engines:
        engine = create_engine(
            url,
            echo=os.environ.get("AUTOSYS_SQL_ECHO", "false").lower() == "true",
            connect_args={"check_same_thread": False} if "sqlite" in url else {},
        )
        if "sqlite" in url:
            @event.listens_for(engine, "connect")
            def _set_sqlite_pragmas(dbapi_conn, _record):
                dbapi_conn.execute("PRAGMA journal_mode=WAL;")
                dbapi_conn.execute("PRAGMA foreign_keys=ON;")

        _sync_engines[url] = engine
        _sync_session_factories[url] = sessionmaker(
            bind=engine,
            autoflush=False,
            autocommit=False,
            expire_on_commit=False,
        )
    return _sync_engines[url]


@contextmanager
def sync_session() -> Generator[Session, None, None]:
    """
    Sync context manager that yields a SQLAlchemy Session.

    The engine is resolved from AUTOSYS_DB_URL at the moment this context
    manager is entered (not at module import time), so tests can safely
    change the env var between invocations.
    """
    url = _get_db_url(async_=False)
    get_sync_engine()   # ensure engine + factory are cached for this URL
    factory = _sync_session_factories[url]
    session: Session = factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Async engine + session factory
# ---------------------------------------------------------------------------

def get_async_engine():
    """
    Return an AsyncEngine for the current AUTOSYS_DB_URL, creating once
    per URL and caching it for the process lifetime.
    """
    url = _get_db_url(async_=True)
    if url not in _async_engines:
        engine = create_async_engine(
            url,
            echo=os.environ.get("AUTOSYS_SQL_ECHO", "false").lower() == "true",
            connect_args={"check_same_thread": False} if "sqlite" in url else {},
        )
        _async_engines[url] = engine
        _async_session_factories[url] = async_sessionmaker(
            bind=engine,
            class_=AsyncSession,
            expire_on_commit=False,
            autoflush=False,
            autocommit=False,
        )
    return _async_engines[url]


@asynccontextmanager
async def async_session() -> AsyncGenerator[AsyncSession, None]:
    """Async context manager that yields a SQLAlchemy AsyncSession."""
    url = _get_db_url(async_=True)
    get_async_engine()   # ensure cached
    factory = _async_session_factories[url]
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
