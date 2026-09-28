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
from autosys.timeutil import utcnow
from pathlib import Path

from sqlalchemy import inspect, text
from sqlalchemy.exc import OperationalError

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

    try:
        Base.metadata.create_all(bind=engine)
    except OperationalError as exc:
        if "already exists" in str(exc):
            logger.debug("Schema already up-to-date (index already exists — skipping)")
        else:
            raise
    add_missing_columns(engine)
    logger.info("AutoSys schema ensured (sync) on %s", engine.url)
    _seed_defaults_sync()


def _add_missing_columns_conn(conn) -> list[str]:
    """ALTER TABLE ... ADD COLUMN for every model column absent from the DB.

    ``create_all`` never alters existing tables, so a DB created by an older
    version of the schema (e.g. a stale Docker volume) would otherwise break
    on every new column.  Only nullable / defaulted columns can be added this
    way; NOT NULL columns without a default are skipped with a warning.
    Works on SQLite and PostgreSQL.
    """
    insp = inspect(conn)
    existing_tables = set(insp.get_table_names())
    added: list[str] = []
    for table in Base.metadata.sorted_tables:
        if table.name not in existing_tables:
            continue
        have = {c["name"] for c in insp.get_columns(table.name)}
        for col in table.columns:
            if col.name in have:
                continue
            if not col.nullable and col.default is None and col.server_default is None:
                logger.warning(
                    "Cannot auto-add NOT NULL column %s.%s without a default",
                    table.name, col.name,
                )
                continue
            coltype = col.type.compile(dialect=conn.dialect)
            ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {coltype}'
            if col.default is not None and getattr(col.default, "is_scalar", False):
                val = col.default.arg
                if isinstance(val, bool):
                    val = int(val)
                if isinstance(val, (int, float)):
                    ddl += f" DEFAULT {val}"
                elif isinstance(val, str):
                    ddl += " DEFAULT '" + val.replace("'", "''") + "'"
            conn.execute(text(ddl))
            if not col.nullable and not (
                col.default is not None and getattr(col.default, "is_scalar", False)
            ):
                # Python-callable default (e.g. utcnow): SQLite forbids a
                # non-constant DEFAULT in ADD COLUMN, so backfill instead.
                conn.execute(text(
                    f'UPDATE "{table.name}" SET "{col.name}" = CURRENT_TIMESTAMP'
                ))
            logger.info("Migrated schema: added column %s.%s (%s)", table.name, col.name, coltype)
            added.append(f"{table.name}.{col.name}")
    return added


def add_missing_columns(engine=None) -> list[str]:
    """Add columns present in the models but missing from existing tables."""
    engine = engine or get_sync_engine()
    with engine.begin() as conn:
        return _add_missing_columns_conn(conn)


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
            last_heartbeat = utcnow(),
            description    = "Default local System Agent",
        ))
        logger.info("Seeded default machine: localhost")


def _ensure_calendars(session) -> None:
    """Load calendar .cal files from config/calendars/ into the DB."""
    if not _CALENDARS_DIR.exists():
        logger.debug(f"No calendars directory found at {_CALENDARS_DIR} — skipping")
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
                logger.warning(f"Skipping invalid date {token!r} in {cal_file}")

        session.add(CalendarRow(
            calendar_name = cal_name,
            dates_json    = json.dumps(dates),
            description   = f"Loaded from {cal_file.name}",
        ))
        logger.info(f"Seeded calendar: {cal_name} ({len(dates)} dates)")


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
        try:
            await conn.run_sync(Base.metadata.create_all)
        except OperationalError as exc:
            if "already exists" in str(exc):
                logger.debug("Schema already up-to-date (index already exists — skipping)")
            else:
                raise
        await conn.run_sync(_add_missing_columns_conn)

    logger.info("AutoSys schema ensured (async) on %s", engine.url)
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
                last_heartbeat = utcnow(),
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
                logger.info(f"Seeded calendar: {cal_name} ({len(dates)} dates) (async)")


# ---------------------------------------------------------------------------
# Introspection helper  (used by tests)
# ---------------------------------------------------------------------------

def list_tables_sync() -> list[str]:
    """Return the names of all tables currently in the database."""
    engine = get_sync_engine()
    inspector = inspect(engine)
    return inspector.get_table_names()
