"""
Schema creation / migration helpers.

In real AutoSys, the DBA runs a Broadcom-supplied script that creates all
tables in Oracle or SQL Server.  This module does the same for our SQLite
clone.

Two entry points
----------------
create_all_sync()   — synchronous, used by CLI ``autosys init`` and tests
create_all_async()  — async, used by the Scheduler ACE on startup

After creation, seed_defaults() inserts the baseline rows every fresh
installation needs: the localhost System Agent and the built-in calendars.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime
from pathlib import Path

from sqlalchemy import inspect, text

from autosys.db.connection import get_async_engine, get_sync_engine
from autosys.db.schema import Base, CalendarRow, MachineRow

logger = logging.getLogger(__name__)

# Path to bundled calendar files shipped with the package
_CALENDARS_DIR = Path(__file__).parents[2] / "config" / "calendars"


# ---------------------------------------------------------------------------
# Synchronous path  (CLI / tests)
# ---------------------------------------------------------------------------

def create_all_sync(drop_first: bool = False) -> None:
    """
    Create all tables in the configured database (sync).

    Parameters
    ----------
    drop_first:
        If True, DROP all tables before re-creating them.
        **Destructive** — only use in tests or fresh setups.
    """
    engine = get_sync_engine()
    if drop_first:
        logger.warning("Dropping all AutoSys tables — all data will be lost!")
        Base.metadata.drop_all(bind=engine)

    Base.metadata.create_all(bind=engine)
    logger.info("AutoSys schema created (sync) on %s", engine.url)
    _seed_defaults_sync()


def _seed_defaults_sync() -> None:
    """Insert default rows that a fresh installation requires."""
    from autosys.db.connection import sync_session

    with sync_session() as session:
        _ensure_localhost_agent(session)
        _ensure_calendars(session)
        session.flush()


def _ensure_localhost_agent(session) -> None:
    """Register localhost as a System Agent if not already present."""
    existing = session.get(MachineRow, "localhost")
    if existing is None:
        session.add(MachineRow(
            machine_name   = "localhost",
            host           = "127.0.0.1",
            port           = 7520,
            status         = "UP",
            last_heartbeat = datetime.utcnow(),
            description    = "Default local System Agent",
        ))
        logger.info("Seeded default machine: localhost")


def _ensure_calendars(session) -> None:
    """Load calendar .cal files from config/calendars/ into the DB."""
    if not _CALENDARS_DIR.exists():
        logger.debug("No calendars directory found at %s — skipping", _CALENDARS_DIR)
        return

    for cal_file in sorted(_CALENDARS_DIR.glob("*.cal")):
        cal_name = cal_file.stem
        existing = session.get(CalendarRow, cal_name)
        if existing is not None:
            continue   # already seeded

        dates: list[str] = []
        for raw_line in cal_file.read_text().splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            # Date is the first token; ignore trailing comment
            token = line.split()[0]
            try:
                date.fromisoformat(token)   # validate
                dates.append(token)
            except ValueError:
                logger.warning("Skipping invalid date %r in %s", token, cal_file)

        session.add(CalendarRow(
            calendar_name = cal_name,
            dates_json    = json.dumps(dates),
            description   = f"Loaded from {cal_file.name}",
        ))
        logger.info("Seeded calendar: %s (%d dates)", cal_name, len(dates))


# ---------------------------------------------------------------------------
# Asynchronous path  (Scheduler ACE startup)
# ---------------------------------------------------------------------------

async def create_all_async(drop_first: bool = False) -> None:
    """
    Create all tables in the configured database (async).

    Called by the Scheduler ACE on startup to ensure the schema exists
    before starting the event loop.
    """
    engine = get_async_engine()

    async with engine.begin() as conn:
        if drop_first:
            logger.warning("Dropping all AutoSys tables — all data will be lost!")
            await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)

    logger.info("AutoSys schema created (async) on %s", engine.url)
    await _seed_defaults_async()


async def _seed_defaults_async() -> None:
    """Async version of the seed step."""
    from autosys.db.connection import async_session

    async with async_session() as session:
        # Check + insert localhost machine
        existing = await session.get(MachineRow, "localhost")
        if existing is None:
            session.add(MachineRow(
                machine_name   = "localhost",
                host           = "127.0.0.1",
                port           = 7520,
                status         = "UP",
                last_heartbeat = datetime.utcnow(),
                description    = "Default local System Agent",
            ))
            logger.info("Seeded default machine: localhost (async)")

        # Calendars — load synchronously inside run_sync is simpler here
        if _CALENDARS_DIR.exists():
            for cal_file in sorted(_CALENDARS_DIR.glob("*.cal")):
                cal_name = cal_file.stem
                existing_cal = await session.get(CalendarRow, cal_name)
                if existing_cal is not None:
                    continue

                dates: list[str] = []
                for raw_line in cal_file.read_text().splitlines():
                    line = raw_line.strip()
                    if not line or line.startswith("#"):
                        continue
                    token = line.split()[0]
                    try:
                        date.fromisoformat(token)
                        dates.append(token)
                    except ValueError:
                        pass

                session.add(CalendarRow(
                    calendar_name = cal_name,
                    dates_json    = json.dumps(dates),
                    description   = f"Loaded from {cal_file.name}",
                ))
                logger.info("Seeded calendar: %s (%d dates) (async)", cal_name, len(dates))


# ---------------------------------------------------------------------------
# Introspection helper  (used by tests)
# ---------------------------------------------------------------------------

def list_tables_sync() -> list[str]:
    """Return the names of all tables currently in the database."""
    engine = get_sync_engine()
    inspector = inspect(engine)
    return inspector.get_table_names()
