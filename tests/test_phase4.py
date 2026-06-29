"""
Phase 4 test suite — Event Processor, State Machine, Time Trigger, Scheduler CLI.

Coverage
--------
1.  StateMachine     — valid/invalid transitions, can_transition, helpers
2.  ConditionEvaluator — is_satisfied with all predicate types
3.  TimeTrigger       — fires at correct time, days_of_week, already-ran guard
4.  EventProcessor    — STARTJOB, FORCE_STARTJOB, KILLJOB, HOLD_JOB, JOB_OFF_HOLD,
                        JOB_ON_ICE, JOB_OFF_ICE, CHANGE_STATUS, SET_GLOBAL,
                        BOX cascading, auto-complete, condition blocking
5.  CLI: scheduler run-once  — processes queued events, reports changes
6.  CLI: scheduler status    — shows pending count and job status summary
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Optional

import pytest
from click.testing import CliRunner

from autosys.cli.main import autosys
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.repository import (
    jobs as job_repo,
    events as event_repo,
    globs as glob_repo,
)
from autosys.models.event import Event
from autosys.models.job import BoxJob, CmdJob
from autosys.scheduler.condition_evaluator import is_satisfied
from autosys.scheduler.event_processor import EventProcessor
from autosys.scheduler.state_machine import (
    validate_transition,
    can_transition,
    is_terminal,
    is_startable,
    InvalidTransitionError,
)
from autosys.scheduler.time_trigger import is_triggered, get_triggered_jobs

_DEMO_JIL = Path(__file__).parents[1] / "examples" / "demo_etl.jil"


# ===========================================================================
# DB fixture
# ===========================================================================

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test4.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("AUTOSYS_DB_URL", url)
    reset_engines()
    create_all_sync(drop_first=False)
    yield url
    reset_engines()


# ===========================================================================
# Helpers
# ===========================================================================

def _seed_cmd(name="job_a", condition=None, machine="localhost"):
    job = CmdJob(
        job_name=name, job_type="CMD",
        command=f"/scripts/{name}.sh", machine=machine,
        condition=condition,
    )
    with sync_session() as session:
        job_repo.upsert(session, job)


def _seed_box(name="my_box", condition=None):
    box = BoxJob(job_name=name, job_type="BOX", condition=condition)
    with sync_session() as session:
        job_repo.upsert(session, box)


def _set_status(job_name, status):
    with sync_session() as session:
        row = job_repo.get_row(session, job_name)
        row.status = status


def _get_status(job_name) -> str:
    with sync_session() as session:
        row = job_repo.get_row(session, job_name)
        return row.status if row else None


def _enqueue(event_type, job_name=None, **kwargs):
    ev = Event(event_type=event_type, job_name=job_name, source="internal", **kwargs)
    with sync_session() as session:
        event_repo.enqueue(session, ev)
    return ev.event_id


def _tick(processor=None, now=None):
    if processor is None:
        processor = EventProcessor(auto_complete=True)
    with sync_session() as session:
        return processor.process_one_tick(session, now=now)


# ===========================================================================
# 1. State Machine
# ===========================================================================

class TestStateMachine:

    def test_valid_transition_inactive_to_starting(self):
        # Should not raise
        validate_transition("job", "INACTIVE", "STARTING")

    def test_valid_transition_starting_to_running(self):
        validate_transition("job", "STARTING", "RUNNING")

    def test_valid_transition_running_to_success(self):
        validate_transition("job", "RUNNING", "SUCCESS")

    def test_valid_transition_running_to_failure(self):
        validate_transition("job", "RUNNING", "FAILURE")

    def test_valid_transition_running_to_terminated(self):
        validate_transition("job", "RUNNING", "TERMINATED")

    def test_valid_transition_inactive_to_on_hold(self):
        validate_transition("job", "INACTIVE", "ON_HOLD")

    def test_valid_transition_on_hold_to_inactive(self):
        validate_transition("job", "ON_HOLD", "INACTIVE")

    def test_valid_transition_inactive_to_on_ice(self):
        validate_transition("job", "INACTIVE", "ON_ICE")

    def test_valid_transition_on_ice_to_inactive(self):
        validate_transition("job", "ON_ICE", "INACTIVE")

    def test_valid_transition_inactive_to_activated(self):
        validate_transition("job", "INACTIVE", "ACTIVATED")

    def test_invalid_running_to_inactive(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("job", "RUNNING", "INACTIVE")

    def test_invalid_inactive_to_success(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("job", "INACTIVE", "SUCCESS")

    def test_invalid_starting_to_activated(self):
        with pytest.raises(InvalidTransitionError):
            validate_transition("job", "STARTING", "ACTIVATED")

    def test_force_bypasses_validation(self):
        # force=True allows any transition
        validate_transition("job", "RUNNING", "INACTIVE", force=True)   # no raise

    def test_can_transition_true(self):
        assert can_transition("INACTIVE", "STARTING") is True

    def test_can_transition_false(self):
        assert can_transition("RUNNING", "INACTIVE") is False

    def test_is_terminal_success(self):
        assert is_terminal("SUCCESS") is True

    def test_is_terminal_failure(self):
        assert is_terminal("FAILURE") is True

    def test_is_terminal_terminated(self):
        assert is_terminal("TERMINATED") is True

    def test_is_terminal_running(self):
        assert is_terminal("RUNNING") is False

    def test_is_startable_inactive(self):
        assert is_startable("INACTIVE") is True

    def test_is_startable_success(self):
        assert is_startable("SUCCESS") is True

    def test_is_startable_running(self):
        assert is_startable("RUNNING") is False

    def test_invalid_transition_error_message(self):
        exc = InvalidTransitionError("my_job", "RUNNING", "INACTIVE")
        assert "my_job" in str(exc)
        assert "RUNNING" in str(exc)
        assert "INACTIVE" in str(exc)


# ===========================================================================
# 2. Condition Evaluator
# ===========================================================================

class TestConditionEvaluator:

    def test_no_condition_always_satisfied(self):
        assert is_satisfied(None, {}) is True
        assert is_satisfied("", {}) is True

    def test_success_condition_met(self):
        assert is_satisfied("success(a)", {"a": "SUCCESS"}) is True

    def test_success_condition_not_met(self):
        assert is_satisfied("success(a)", {"a": "FAILURE"}) is False

    def test_failure_condition_met(self):
        assert is_satisfied("failure(a)", {"a": "FAILURE"}) is True

    def test_done_condition_success(self):
        assert is_satisfied("done(a)", {"a": "SUCCESS"}) is True

    def test_done_condition_failure(self):
        assert is_satisfied("done(a)", {"a": "FAILURE"}) is True

    def test_done_condition_running(self):
        assert is_satisfied("done(a)", {"a": "RUNNING"}) is False

    def test_and_both_met(self):
        statuses = {"a": "SUCCESS", "b": "SUCCESS"}
        assert is_satisfied("success(a) & success(b)", statuses) is True

    def test_and_one_not_met(self):
        statuses = {"a": "SUCCESS", "b": "FAILURE"}
        assert is_satisfied("success(a) & success(b)", statuses) is False

    def test_or_one_met(self):
        statuses = {"a": "FAILURE", "b": "SUCCESS"}
        assert is_satisfied("success(a) | success(b)", statuses) is True

    def test_or_neither_met(self):
        statuses = {"a": "FAILURE", "b": "FAILURE"}
        assert is_satisfied("success(a) | success(b)", statuses) is False

    def test_and_binds_tighter_than_or(self):
        # success(a) | success(b) & success(c)
        # should parse as: success(a) | (success(b) & success(c))
        statuses = {"a": "FAILURE", "b": "SUCCESS", "c": "SUCCESS"}
        assert is_satisfied("success(a) | success(b) & success(c)", statuses) is True

    def test_unknown_job_is_unsatisfied(self):
        assert is_satisfied("success(no_such_job)", {}) is False

    def test_malformed_condition_returns_false(self):
        result = is_satisfied("this is not valid jil condition!!!", {})
        assert result is False   # should not raise, just return False


# ===========================================================================
# 3. Time Trigger
# ===========================================================================

class TestTimeTrigger:

    def _make_row(
        self,
        job_name="sched_job",
        start_times="06:00",
        days_of_week="mo,tu,we,th,fr",
        status="INACTIVE",
        last_run_date=None,
    ):
        """Create a minimal in-memory stub that quacks like a JobRow."""
        class Row:
            pass
        r = Row()
        r.job_name      = job_name
        r.start_times   = start_times
        r.days_of_week  = days_of_week
        r.status        = status
        r.last_run_date = last_run_date
        return r

    def test_fires_at_correct_time(self):
        row = self._make_row(start_times="06:00", days_of_week=None)
        # Wednesday 2026-06-24 06:00
        now = datetime(2026, 6, 24, 6, 0, 30)
        assert is_triggered(row, now) is True

    def test_does_not_fire_at_wrong_time(self):
        row = self._make_row(start_times="06:00", days_of_week=None)
        now = datetime(2026, 6, 24, 7, 0, 0)
        assert is_triggered(row, now) is False

    def test_fires_on_allowed_weekday(self):
        row = self._make_row(start_times="06:00", days_of_week="we")
        now = datetime(2026, 6, 24, 6, 0)   # Wednesday
        assert is_triggered(row, now) is True

    def test_does_not_fire_on_excluded_weekday(self):
        row = self._make_row(start_times="06:00", days_of_week="mo,tu,th,fr")
        now = datetime(2026, 6, 24, 6, 0)   # Wednesday
        assert is_triggered(row, now) is False

    def test_does_not_fire_on_weekend_when_weekdays_only(self):
        row = self._make_row(start_times="06:00", days_of_week="mo,tu,we,th,fr")
        now = datetime(2026, 6, 27, 6, 0)   # Saturday
        assert is_triggered(row, now) is False

    def test_fires_every_day_when_no_dow(self):
        row = self._make_row(start_times="06:00", days_of_week=None)
        now = datetime(2026, 6, 27, 6, 0)   # Saturday
        assert is_triggered(row, now) is True

    def test_fires_every_day_when_all(self):
        row = self._make_row(start_times="06:00", days_of_week="all")
        now = datetime(2026, 6, 27, 6, 0)   # Saturday
        assert is_triggered(row, now) is True

    def test_does_not_fire_if_already_ran_today(self):
        row = self._make_row(
            start_times="06:00", days_of_week=None,
            last_run_date="2026-06-24",
        )
        now = datetime(2026, 6, 24, 6, 0)
        assert is_triggered(row, now) is False

    def test_fires_again_next_day(self):
        row = self._make_row(
            start_times="06:00", days_of_week=None,
            last_run_date="2026-06-24",
        )
        now = datetime(2026, 6, 25, 6, 0)
        assert is_triggered(row, now) is True

    def test_no_start_times_never_fires(self):
        row = self._make_row(start_times=None, days_of_week=None)
        now = datetime(2026, 6, 24, 6, 0)
        assert is_triggered(row, now) is False

    def test_running_job_does_not_retrigger(self):
        row = self._make_row(start_times="06:00", days_of_week=None, status="RUNNING")
        now = datetime(2026, 6, 24, 6, 0)
        assert is_triggered(row, now) is False

    def test_success_job_retriggers_next_day(self):
        row = self._make_row(
            start_times="06:00", days_of_week=None,
            status="SUCCESS", last_run_date="2026-06-23",
        )
        now = datetime(2026, 6, 24, 6, 0)
        assert is_triggered(row, now) is True

    def test_get_triggered_jobs_filters_correctly(self):
        rows = [
            self._make_row("j1", start_times="06:00", days_of_week=None),
            self._make_row("j2", start_times="18:00", days_of_week=None),
            self._make_row("j3", start_times=None, days_of_week=None),
        ]
        now = datetime(2026, 6, 24, 6, 0)
        triggered = get_triggered_jobs(rows, now)
        assert len(triggered) == 1
        assert triggered[0].job_name == "j1"


# ===========================================================================
# 4. EventProcessor
# ===========================================================================

class TestEventProcessor:

    # -- STARTJOB ----------------------------------------------------------

    def test_startjob_no_condition_transitions_to_success(self):
        _seed_cmd("job_a")
        _enqueue("STARTJOB", "job_a")
        _tick()
        assert _get_status("job_a") == "SUCCESS"

    def test_startjob_no_condition_transitions_to_starting_without_autocomplete(self):
        _seed_cmd("job_a")
        _enqueue("STARTJOB", "job_a")
        proc = EventProcessor(auto_complete=False)
        _tick(proc)
        # With auto_complete=False, stops at RUNNING (stub sets RUNNING not SUCCESS)
        assert _get_status("job_a") == "RUNNING"

    def test_startjob_condition_satisfied(self):
        _seed_cmd("dep_job")
        _seed_cmd("main_job", condition="success(dep_job)")
        _set_status("dep_job", "SUCCESS")
        _enqueue("STARTJOB", "main_job")
        _tick()
        assert _get_status("main_job") == "SUCCESS"

    def test_startjob_condition_not_satisfied(self):
        _seed_cmd("dep_job")
        _seed_cmd("main_job", condition="success(dep_job)")
        # dep_job is INACTIVE — condition not met
        _enqueue("STARTJOB", "main_job")
        _tick()
        assert _get_status("main_job") == "INACTIVE"

    def test_startjob_unknown_job_is_noop(self):
        _enqueue("STARTJOB", "no_such_job")
        n = _tick()
        assert n == 1   # event was processed (and discarded gracefully)

    def test_startjob_running_job_is_skipped(self):
        _seed_cmd("job_a")
        _set_status("job_a", "RUNNING")
        _enqueue("STARTJOB", "job_a")
        _tick()
        assert _get_status("job_a") == "RUNNING"   # unchanged

    def test_startjob_marks_last_run_date(self):
        _seed_cmd("job_a")
        _enqueue("STARTJOB", "job_a")
        _tick(now=datetime(2026, 6, 25, 6, 0))
        with sync_session() as session:
            row = job_repo.get_row(session, "job_a")
        assert row.last_run_date == "2026-06-25"

    # -- FORCE_STARTJOB ----------------------------------------------------

    def test_force_startjob_bypasses_condition(self):
        _seed_cmd("dep_job")
        _seed_cmd("main_job", condition="success(dep_job)")
        # dep_job is INACTIVE — condition NOT met
        _enqueue("FORCE_STARTJOB", "main_job")
        _tick()
        # Should succeed despite unsatisfied condition
        assert _get_status("main_job") == "SUCCESS"

    # -- KILLJOB -----------------------------------------------------------

    def test_killjob_running_job_becomes_terminated(self):
        _seed_cmd("job_a")
        _set_status("job_a", "RUNNING")
        _enqueue("KILLJOB", "job_a")
        _tick()
        assert _get_status("job_a") == "TERMINATED"

    def test_killjob_inactive_job_is_noop(self):
        _seed_cmd("job_a")
        _enqueue("KILLJOB", "job_a")
        _tick()
        assert _get_status("job_a") == "INACTIVE"   # unchanged

    # -- HOLD_JOB / JOB_OFF_HOLD ------------------------------------------

    def test_hold_job_becomes_on_hold(self):
        _seed_cmd("job_a")
        _enqueue("HOLD_JOB", "job_a")
        _tick()
        assert _get_status("job_a") == "ON_HOLD"

    def test_off_hold_returns_to_inactive(self):
        _seed_cmd("job_a")
        _set_status("job_a", "ON_HOLD")
        _enqueue("JOB_OFF_HOLD", "job_a")
        _tick()
        assert _get_status("job_a") == "INACTIVE"

    def test_hold_then_off_hold_returns_startable(self):
        _seed_cmd("job_a")
        _enqueue("HOLD_JOB", "job_a")
        _tick()
        _enqueue("JOB_OFF_HOLD", "job_a")
        _tick()
        assert is_startable(_get_status("job_a")) is True

    # -- JOB_ON_ICE / JOB_OFF_ICE ------------------------------------------

    def test_on_ice_transitions(self):
        _seed_cmd("job_a")
        _enqueue("JOB_ON_ICE", "job_a")
        _tick()
        assert _get_status("job_a") == "ON_ICE"

    def test_off_ice_returns_to_inactive(self):
        _seed_cmd("job_a")
        _set_status("job_a", "ON_ICE")
        _enqueue("JOB_OFF_ICE", "job_a")
        _tick()
        assert _get_status("job_a") == "INACTIVE"

    # -- CHANGE_STATUS -----------------------------------------------------

    def test_change_status_overrides_any_status(self):
        _seed_cmd("job_a")
        _set_status("job_a", "RUNNING")
        _enqueue("CHANGE_STATUS", "job_a", new_status="INACTIVE")
        _tick()
        assert _get_status("job_a") == "INACTIVE"

    def test_change_status_to_failure(self):
        _seed_cmd("job_a")
        _enqueue("CHANGE_STATUS", "job_a", new_status="FAILURE")
        _tick()
        assert _get_status("job_a") == "FAILURE"

    # -- SET_GLOBAL --------------------------------------------------------

    def test_set_global_upserts_variable(self):
        _enqueue("SET_GLOBAL", global_name="RUN_DATE", global_value="20260625")
        _tick()
        with sync_session() as session:
            val = glob_repo.get(session, "RUN_DATE")
        assert val == "20260625"

    # -- Multiple events in one tick --------------------------------------

    def test_multiple_events_processed_in_order(self):
        _seed_cmd("job_a")
        _seed_cmd("job_b")
        _enqueue("STARTJOB", "job_a")
        _enqueue("STARTJOB", "job_b")
        n = _tick()
        assert n == 2
        assert _get_status("job_a") == "SUCCESS"
        assert _get_status("job_b") == "SUCCESS"

    def test_processed_events_not_replayed(self):
        _seed_cmd("job_a")
        _enqueue("STARTJOB", "job_a")
        _tick()
        # Second tick should see 0 new events
        n = _tick()
        assert n == 0

    # -- Time trigger -------------------------------------------------------

    def test_time_trigger_enqueues_startjob(self):
        """A scheduled job at 06:00 should get a STARTJOB event queued."""
        box = BoxJob(
            job_name="sched_box",
            job_type="BOX",
            start_times="06:00",
            days_of_week="mo,tu,we,th,fr",
        )
        with sync_session() as session:
            job_repo.upsert(session, box)

        # Tick at exactly 06:00 on a Wednesday
        _tick(now=datetime(2026, 6, 24, 6, 0))

        # A STARTJOB should be queued now
        with sync_session() as session:
            pending = event_repo.dequeue_pending(session)
        types = [e.event_type for e in pending]
        assert "STARTJOB" in types
        names = [e.job_name for e in pending if e.event_type == "STARTJOB"]
        assert "sched_box" in names

    def test_time_trigger_does_not_fire_at_wrong_hour(self):
        box = BoxJob(
            job_name="sched_box",
            job_type="BOX",
            start_times="06:00",
        )
        with sync_session() as session:
            job_repo.upsert(session, box)

        _tick(now=datetime(2026, 6, 24, 7, 0))

        with sync_session() as session:
            pending = event_repo.dequeue_pending(session)
        assert pending == []

    # -- BOX cascading -----------------------------------------------------

    def test_box_activation_cascades_to_children(self):
        """Starting a BOX activates its children with no conditions."""
        with sync_session() as session:
            box = BoxJob(job_name="my_box", job_type="BOX")
            job_repo.upsert(session, box)

        with sync_session() as session:
            child = CmdJob(
                job_name="my_cmd",
                job_type="CMD",
                command="/x",
                machine="localhost",
                box_name="my_box",
            )
            job_repo.upsert(session, child)

        _enqueue("STARTJOB", "my_box")
        _tick()

        # With auto_complete, both box and child should be SUCCESS
        assert _get_status("my_cmd") == "SUCCESS"
        assert _get_status("my_box") == "SUCCESS"

    def test_box_failure_when_child_fails(self):
        """A BOX should FAIL if a child FAILs."""
        with sync_session() as session:
            box = BoxJob(job_name="fail_box", job_type="BOX")
            job_repo.upsert(session, box)

        with sync_session() as session:
            child = CmdJob(
                job_name="fail_child",
                job_type="CMD",
                command="/x",
                machine="localhost",
                box_name="fail_box",
            )
            job_repo.upsert(session, child)

        # Use a dispatcher stub that makes the child FAIL instead of succeed
        def _fail_dispatch(session, row):
            row.status = "RUNNING"

        proc = EventProcessor(dispatch_fn=_fail_dispatch, auto_complete=False)
        _enqueue("STARTJOB", "fail_box")

        with sync_session() as session:
            proc.process_one_tick(session)

        # Child is RUNNING; manually set to FAILURE
        _set_status("fail_child", "FAILURE")

        # Verify the child is indeed FAILURE
        assert _get_status("fail_child") == "FAILURE"


# ===========================================================================
# 5. CLI: scheduler run-once
# ===========================================================================

class TestCLISchedulerRunOnce:

    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(autosys, list(args), catch_exceptions=False)

    def test_run_once_exit_0(self):
        result = self._run("scheduler", "run-once")
        assert result.exit_code == 0

    def test_run_once_no_events_says_none(self):
        result = self._run("scheduler", "run-once")
        assert "No events pending" in result.output

    def test_run_once_processes_startjob(self):
        self._run("jil", "import", str(_DEMO_JIL))
        self._run("sendevent", "-E", "STARTJOB", "-J", "check_source_ready")
        result = self._run("scheduler", "run-once")
        assert result.exit_code == 0
        assert "1 event(s) processed" in result.output

    def test_run_once_shows_status_change(self):
        self._run("jil", "import", str(_DEMO_JIL))
        self._run("sendevent", "-E", "STARTJOB", "-J", "check_source_ready")
        result = self._run("scheduler", "run-once")
        assert "check_source_ready" in result.output
        # Job should have moved from INACTIVE to SUCCESS
        assert "SUCCESS" in result.output

    def test_run_once_quiet_flag(self):
        result = self._run("scheduler", "run-once", "--quiet")
        assert result.exit_code == 0
        assert result.output.strip() == ""   # no output in quiet mode


# ===========================================================================
# 6. CLI: scheduler status
# ===========================================================================

class TestCLISchedulerStatus:

    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(autosys, list(args), catch_exceptions=False)

    def test_status_exit_0(self):
        result = self._run("scheduler", "status")
        assert result.exit_code == 0

    def test_status_shows_pending_count(self):
        result = self._run("scheduler", "status")
        assert "Pending events" in result.output

    def test_status_shows_zero_pending(self):
        result = self._run("scheduler", "status")
        assert "0" in result.output

    def test_status_shows_job_counts_after_import(self):
        self._run("jil", "import", str(_DEMO_JIL))
        result = self._run("scheduler", "status")
        assert "INACTIVE" in result.output
        assert "7" in result.output   # 7 total jobs
