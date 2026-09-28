"""
Tests for the ``dbstatistics``, ``archive_events``, and ``job_depends``
CLI utilities — small AutoSys admin commands this clone had not yet
implemented.

Coverage
--------
1.  CLI: dbstatistics   — table + row count output, zero-row tables included
2.  CLI: archive_events — dry-run leaves rows untouched, real run deletes them
3.  CLI: job_depends    — depends-on / depended-on-by, no-condition and
                          unknown-job cases
"""

from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from click.testing import CliRunner

from autosys.cli.main import autosys
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.repository import jobs as job_repo
from autosys.db.schema import JobRow, EventHistoryRow
from autosys.models.enums import JobStatus
from autosys.timeutil import utcnow


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """Each test gets its own fresh SQLite DB so tests don't interfere."""
    db_path = tmp_path / "test_cli_utilities.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("AUTOSYS_DB_URL", url)
    reset_engines()
    create_all_sync(drop_first=False)
    yield url
    reset_engines()


def _run(*args):
    runner = CliRunner()
    return runner.invoke(autosys, list(args), catch_exceptions=False)


def _make_job(session, name, **kwargs):
    row = JobRow(
        job_name=name,
        job_type=kwargs.get("job_type", "CMD"),
        command=kwargs.get("command", "echo hi"),
        machine=kwargs.get("machine", "localhost"),
        status=kwargs.get("status", JobStatus.INACTIVE.value),
        owner=kwargs.get("owner", "test"),
        condition=kwargs.get("condition"),
    )
    session.add(row)
    session.flush()
    return row


def _make_event(session, created_at, event_type="STARTJOB", job_name="somejob"):
    row = EventHistoryRow(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        job_name=job_name,
        source="internal",
        created_at=created_at,
    )
    session.add(row)
    session.flush()
    return row


# ===========================================================================
# dbstatistics
# ===========================================================================

class TestCLIDbstatistics:

    def test_dbstatistics_exit_0(self):
        result = _run("dbstatistics")
        assert result.exit_code == 0

    def test_dbstatistics_lists_known_tables(self):
        result = _run("dbstatistics")
        # Table names should appear, sorted, each with a row count.
        assert "ujo_job" in result.output
        assert "ujo_job_runs" in result.output
        assert "alarms" in result.output

    def test_dbstatistics_shows_zero_for_empty_tables(self):
        result = _run("dbstatistics")
        assert "0" in result.output

    def test_dbstatistics_reflects_row_counts(self):
        import re

        with sync_session() as s:
            _make_job(s, "job1")
            _make_job(s, "job2")
        result = _run("dbstatistics")
        assert result.exit_code == 0
        # ujo_job (exactly, not ujo_job_runs/ujo_job_type/...) should show 2 rows.
        assert re.search(r"\bujo_job\s+2\b", result.output)


# ===========================================================================
# archive_events
# ===========================================================================

class TestCLIArchiveEvents:

    def test_dry_run_reports_count_without_deleting(self):
        old_time = utcnow() - timedelta(days=60)
        with sync_session() as s:
            _make_event(s, old_time)
            _make_event(s, old_time)

        result = _run("archive_events", "--older-than-days", "30", "--dry-run")
        assert result.exit_code == 0
        assert "2" in result.output

        with sync_session() as s:
            from sqlalchemy import select, func
            count = s.scalar(select(func.count()).select_from(EventHistoryRow))
        assert count == 2

    def test_real_run_deletes_old_events(self):
        old_time = utcnow() - timedelta(days=60)
        recent_time = utcnow() - timedelta(days=1)
        with sync_session() as s:
            _make_event(s, old_time)
            _make_event(s, old_time)
            _make_event(s, recent_time)

        result = _run("archive_events", "--older-than-days", "30")
        assert result.exit_code == 0
        assert "2" in result.output

        with sync_session() as s:
            from sqlalchemy import select
            remaining = list(s.scalars(select(EventHistoryRow)))
        assert len(remaining) == 1
        assert remaining[0].created_at == recent_time

    def test_no_old_events_reports_zero(self):
        recent_time = utcnow() - timedelta(days=1)
        with sync_session() as s:
            _make_event(s, recent_time)

        result = _run("archive_events", "--older-than-days", "30")
        assert result.exit_code == 0
        assert "0" in result.output

        with sync_session() as s:
            from sqlalchemy import select
            remaining = list(s.scalars(select(EventHistoryRow)))
        assert len(remaining) == 1

    def test_default_older_than_days_is_30(self):
        # No events at all — should still run cleanly with the default.
        result = _run("archive_events")
        assert result.exit_code == 0


# ===========================================================================
# job_depends
# ===========================================================================

class TestCLIJobDepends:

    def test_unknown_job_exits_0_with_warning(self):
        result = _run("job_depends", "-J", "no_such_job")
        assert result.exit_code == 0
        assert "no_such_job" in result.output

    def test_job_with_no_condition_and_no_dependents_shows_none(self):
        with sync_session() as s:
            _make_job(s, "lonely_job")

        result = _run("job_depends", "-J", "lonely_job")
        assert result.exit_code == 0
        assert "(none)" in result.output

    def test_depends_on_shows_upstream_jobs(self):
        with sync_session() as s:
            _make_job(s, "upstream_a")
            _make_job(s, "upstream_b")
            _make_job(s, "downstream", condition="success(upstream_a) & success(upstream_b)")

        result = _run("job_depends", "-J", "downstream")
        assert result.exit_code == 0
        assert "upstream_a" in result.output
        assert "upstream_b" in result.output

    def test_depended_on_by_shows_downstream_jobs(self):
        with sync_session() as s:
            _make_job(s, "root_job")
            _make_job(s, "child_a", condition="success(root_job)")
            _make_job(s, "child_b", condition="success(root_job)")
            _make_job(s, "unrelated")

        result = _run("job_depends", "-J", "root_job")
        assert result.exit_code == 0
        assert "child_a" in result.output
        assert "child_b" in result.output
        assert "unrelated" not in result.output
