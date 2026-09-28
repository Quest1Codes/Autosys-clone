"""
V4 — targeted-query scheduler tick optimizations.

See dev/task3-simulator-jil-and-dryrun-visibility/architect-output/03-recommendation-and-plan.md
(V4) in the shinro repo for the full rationale and measured numbers
(8.30s -> ~0.50s per tick at 85,000 jobs on this machine).

Covers:
  TestListStatusOnly     — JobRepository.list_status_only + build_status_snapshot
  TestListStuckStarting  — JobRepository.list_stuck_starting
  TestListSchedulable    — JobRepository.list_schedulable
  TestRunFinishReturnsBool — RunRepository.finish's found/not-found signal
  TestFinishRunWithRetry — AgentDispatch._finish_run_with_retry's retry behavior
"""
from __future__ import annotations

from typing import Generator

import pytest
from sqlalchemy.orm import Session


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_v4_scheduler_perf.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    from autosys.db import connection as _conn
    _conn.reset_engines()
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn.reset_engines()


@pytest.fixture()
def session(isolated_db) -> Generator[Session, None, None]:
    from autosys.db.connection import sync_session
    with sync_session() as s:
        yield s


def _seed_job(session: Session, name: str, job_type="CMD", **extra):
    from autosys.db.schema import JobRow
    kwargs = {"status": 8, **extra}  # INACTIVE unless overridden
    session.add(JobRow(job_name=name, job_type=job_type, command="echo hi",
                        machine="localhost", **kwargs))
    session.commit()


class TestListStatusOnly:

    def test_returns_name_status_pairs(self, session):
        from autosys.db.repository import jobs as job_repo
        from autosys.models.enums import JobStatus
        _seed_job(session, "a", status=JobStatus.RUNNING.value)
        _seed_job(session, "b", status=JobStatus.SUCCESS.value)
        pairs = dict(job_repo.list_status_only(session))
        assert pairs == {"a": JobStatus.RUNNING.value, "b": JobStatus.SUCCESS.value}

    def test_matches_list_all_status_for_same_data(self, session):
        """The lean query must agree with the full-row query it replaced."""
        from autosys.db.repository import jobs as job_repo
        for i in range(20):
            _seed_job(session, f"job_{i}", status=(i % 5) + 1)
        full = {r.job_name: r.status for r in job_repo.list_all(session)}
        lean = dict(job_repo.list_status_only(session))
        assert full == lean

    def test_build_status_snapshot_uses_it_correctly(self, session):
        """Integration check: build_status_snapshot's normalized names are
        unchanged by switching its underlying query."""
        from autosys.db.repository import jobs as job_repo
        from autosys.models.enums import JobStatus
        from autosys.scheduler.condition_evaluator import build_status_snapshot
        _seed_job(session, "running_job", status=JobStatus.RUNNING.value)
        _seed_job(session, "inactive_job", status=JobStatus.INACTIVE.value)
        snap = build_status_snapshot(session)
        assert snap["running_job"] == "RUNNING"
        assert snap["inactive_job"] == "INACTIVE"

    def test_empty_table(self, session):
        from autosys.db.repository import jobs as job_repo
        assert job_repo.list_status_only(session) == []


class TestListStuckStarting:

    def test_returns_only_non_box_starting_jobs(self, session):
        from autosys.db.repository import jobs as job_repo
        from autosys.models.enums import JobStatus
        _seed_job(session, "stuck_cmd", status=JobStatus.STARTING.value)
        _seed_job(session, "running_cmd", status=JobStatus.RUNNING.value)
        _seed_job(session, "inactive_cmd", status=JobStatus.INACTIVE.value)
        _seed_job(session, "stuck_box", job_type="BOX", status=JobStatus.STARTING.value)
        names = {r.job_name for r in job_repo.list_stuck_starting(session)}
        assert names == {"stuck_cmd"}

    def test_empty_when_nothing_stuck(self, session):
        from autosys.db.repository import jobs as job_repo
        from autosys.models.enums import JobStatus
        _seed_job(session, "a", status=JobStatus.SUCCESS.value)
        assert job_repo.list_stuck_starting(session) == []


class TestListSchedulable:

    def test_returns_only_jobs_with_start_times(self, session):
        from autosys.db.repository import jobs as job_repo
        _seed_job(session, "scheduled", start_times="06:00")
        _seed_job(session, "unscheduled")
        names = {r.job_name for r in job_repo.list_schedulable(session)}
        assert names == {"scheduled"}

    def test_empty_when_none_scheduled(self, session):
        from autosys.db.repository import jobs as job_repo
        _seed_job(session, "a")
        _seed_job(session, "b")
        assert job_repo.list_schedulable(session) == []

    def test_matches_list_all_filtered_in_python(self, session):
        """The SQL filter must agree with the old Python-side filter it replaced."""
        from autosys.db.repository import jobs as job_repo
        for i in range(15):
            _seed_job(session, f"job_{i}", start_times="06:00" if i % 3 == 0 else None)
        expected = {r.job_name for r in job_repo.list_all(session) if r.start_times}
        actual = {r.job_name for r in job_repo.list_schedulable(session)}
        assert actual == expected
        assert len(expected) == 5  # sanity: i=0,3,6,9,12


class TestRunFinishReturnsBool:
    """
    RunRepository.finish()'s return value is what
    AgentDispatch._finish_run_with_retry relies on to know whether to retry
    -- see dispatch.py's module docstring for the race this guards against
    (a run row created in the dispatching tick's still-open transaction,
    not yet visible to the background thread's own session).
    """

    def test_returns_true_and_updates_when_row_exists(self, session):
        from autosys.db.repository import runs as run_repo
        from autosys.db.schema import JobRunRow
        _seed_job(session, "j1")
        run_repo.start(session, run_id="r1", job_name="j1", command="echo hi",
                        machine="localhost", run_date="2026-01-01")
        session.commit()
        ok = run_repo.finish(session, run_id="r1", status="SUCCESS", exit_code=0, pid=123)
        assert ok is True
        row = session.get(JobRunRow, "r1")
        assert row.status == 4  # SUCCESS
        assert row.exit_code == 0

    def test_returns_false_when_row_not_yet_visible(self, session):
        from autosys.db.repository import runs as run_repo
        ok = run_repo.finish(session, run_id="does-not-exist-yet", status="SUCCESS", exit_code=0)
        assert ok is False


class TestFinishRunWithRetry:
    """
    Exercises the retry loop directly (mocking run_repo.finish rather than
    spinning up a real race) since the real race is a rare, timing-sensitive
    event -- see dispatch.py's module docstring for where this was found
    live (test_phase5.py's real-dispatch tests hitting it often enough,
    before this fix, to be a genuine flake, not a theoretical one).

    Patches dispatch.run_repo (the name as imported into dispatch.py's own
    namespace), not the shared run_repo singleton, so other modules using
    the real one are unaffected.
    """

    def test_succeeds_on_first_attempt_without_sleeping(self, isolated_db, monkeypatch):
        from autosys.agent import dispatch as dispatch_mod

        calls = []

        class _FakeRunRepo:
            def finish(self, session, run_id, status, exit_code, pid=None):
                calls.append((run_id, status, exit_code, pid))
                return True

        sleeps = []
        monkeypatch.setattr(dispatch_mod, "run_repo", _FakeRunRepo())
        monkeypatch.setattr(dispatch_mod.time, "sleep", lambda s: sleeps.append(s))

        dispatch_mod._finish_run_with_retry("r1", "SUCCESS", 0, 123)

        assert calls == [("r1", "SUCCESS", 0, 123)]
        assert sleeps == []  # first attempt succeeded, no need to have slept

    def test_retries_then_succeeds(self, isolated_db, monkeypatch):
        from autosys.agent import dispatch as dispatch_mod

        attempts = {"n": 0}

        class _FakeRunRepo:
            def finish(self, session, run_id, status, exit_code, pid=None):
                attempts["n"] += 1
                return attempts["n"] >= 3  # visible on the 3rd attempt

        sleeps = []
        monkeypatch.setattr(dispatch_mod, "run_repo", _FakeRunRepo())
        monkeypatch.setattr(dispatch_mod.time, "sleep", lambda s: sleeps.append(s))

        dispatch_mod._finish_run_with_retry("r2", "FAILURE", 1, None)

        assert attempts["n"] == 3
        assert len(sleeps) == 2  # slept between attempts 1->2 and 2->3, not after success

    def test_gives_up_after_max_attempts_without_raising(self, isolated_db, monkeypatch):
        from autosys.agent import dispatch as dispatch_mod

        attempts = {"n": 0}

        class _FakeRunRepo:
            def finish(self, session, run_id, status, exit_code, pid=None):
                attempts["n"] += 1
                return False  # never becomes visible

        monkeypatch.setattr(dispatch_mod, "run_repo", _FakeRunRepo())
        monkeypatch.setattr(dispatch_mod.time, "sleep", lambda s: None)

        dispatch_mod._finish_run_with_retry("r3", "SUCCESS", 0, None)  # must not raise

        assert attempts["n"] == dispatch_mod._FINISH_RETRY_ATTEMPTS
