"""
Real job definitions must load fully on PostgreSQL (audit ING-01, ING-02).

SQLite ignores VARCHAR lengths, so every other test passes no matter how long
a value is. PostgreSQL -- what the client bundle runs -- enforces them, and an
over-long value used to drop the job (ARCHIVE_ONLY / apply_failed) or, in the
archive row, roll back the whole file. A single `autorep -J ALL -q` export is
one file, so that was the whole estate.

These tests need a real PostgreSQL. They run when AUTOSYS_TEST_PG_URL is set
(the CI `postgres` job sets it) and are skipped otherwise. The database is
dropped and recreated, so never point this at anything but a scratch DB.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest
from sqlalchemy import inspect, select, text

PG_URL = os.environ.get("AUTOSYS_TEST_PG_URL")
pytestmark = pytest.mark.skipif(not PG_URL, reason="AUTOSYS_TEST_PG_URL not set")

FIXTURES = Path(__file__).parent / "fixtures" / "jil"


@pytest.fixture()
def pg(monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", PG_URL)
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    from autosys.db.schema import Base
    connection.reset_engines()
    Base.metadata.drop_all(connection.get_sync_engine())
    create_all_sync()
    yield connection
    connection.reset_engines()


def _ingest(path: Path):
    from autosys.db.connection import sync_session
    from autosys.parser.jil_ingest import ingest_paths
    return ingest_paths(sync_session, [path])


def _jobs():
    from autosys.db.connection import sync_session
    from autosys.db.schema import JobRow
    with sync_session() as s:
        return {r.job_name: r for r in s.scalars(select(JobRow))}


def _dispositions():
    from autosys.db.connection import sync_session
    from autosys.db.schema import JilStanzaRow
    with sync_session() as s:
        return [(r.object_name, r.disposition, r.issues_json)
                for r in s.scalars(select(JilStanzaRow).order_by(JilStanzaRow.seq))]


def test_maximum_length_values_load_in_full(pg):
    _ingest(FIXTURES / "max_lengths.jil")
    failed = [d for d in _dispositions() if d[1] not in ("LOADED", "LOADED_WITH_WARNINGS")]
    assert failed == [], f"stanzas not stored: {failed}"

    jobs = _jobs()
    assert len(jobs) == 13
    box = jobs["LONG_BOX"]
    assert len(box.box_success) > 255 and box.box_success.count("s(") == 10
    assert len(box.box_failure) > 255
    assert box.start_times.count(":") == 48
    assert len(box.owner) > 128 and len(box.group) == 130
    assert len(box.application) == 260 and len(box.run_calendar) == 130
    assert all(jobs[f"LONG_BOX_CHILD_STEP_{i:02d}"].box_name == "LONG_BOX" for i in range(10))
    assert len(jobs["LONG_BOX_CHILD_STEP_00"].machine) > 255
    assert jobs["BIG_FILE_WATCH"].watch_file_min_size == 5_000_000_000


def test_overlong_unknown_name_does_not_lose_the_file(pg):
    _ingest(FIXTURES / "long_unknown_name.jil")
    jobs = _jobs()
    assert {"BEFORE_LONG", "AFTER_LONG"} <= set(jobs), "neighbouring jobs were lost"
    disp = _dispositions()
    assert [d[1] for d in disp].count("QUARANTINED") == 1
    # The full name is never lost: it stays in the stanza's raw text.
    from autosys.db.connection import sync_session
    from autosys.db.schema import JilStanzaRow
    with sync_session() as s:
        raw = s.scalars(select(JilStanzaRow.raw_text)
                        .where(JilStanzaRow.disposition == "QUARANTINED")).one()
    assert "W" * 300 in raw


def test_existing_narrow_columns_are_widened_on_start(pg):
    # A database created by an older release still has the narrow types;
    # create_all_sync must widen them in place, keeping the data.
    from autosys.db.migrations import create_all_sync
    eng = pg.get_sync_engine()
    with eng.begin() as c:
        c.execute(text('ALTER TABLE ujo_job ALTER COLUMN start_times TYPE VARCHAR(256)'))
        c.execute(text('ALTER TABLE ujo_job ALTER COLUMN watch_file_min_size TYPE INTEGER'))
    from autosys.db.connection import sync_session
    from autosys.db.schema import JobRow
    with sync_session() as s:
        s.add(JobRow(job_name="OLD", job_type="CMD", start_times="06:00", watch_file_min_size=5))
        s.commit()
    create_all_sync()
    cols = {c["name"]: c["type"] for c in inspect(eng).get_columns("ujo_job")}
    assert str(cols["start_times"]).upper() == "TEXT"
    assert str(cols["watch_file_min_size"]).upper() == "BIGINT"
    assert _jobs()["OLD"].start_times == "06:00"
