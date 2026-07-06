"""
Phase 10 — Alarms, Notifications & NSM  (~37 tests).

TestAlarmManagerFailure      — ALARM_IF_FAIL logic
TestAlarmManagerTerminated   — ALARM_IF_TERMINATED logic
TestAlarmManagerMaxRun       — MAX_RUN_ALARM logic
TestAlarmManagerMinRun       — MIN_RUN_ALARM logic
TestAlarmManagerMachineDown  — HEARTBEAT_FAIL for jobs on downed machines
TestAlarmManagerDedup        — identical alarm not raised twice
TestAlarmManagerAutoResolve  — auto-clear when job recovers to SUCCESS
TestDispatcher               — pluggable channel delivery
TestEPSIntegration           — alarms raised through the full EPS tick
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Generator
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy.orm import Session

from autosys.db.connection import sync_session
from autosys.db.schema import AlarmRow, JobRow, JobRunRow, MachineRow
from autosys.notifications.alarm_manager import AlarmManager
from autosys.notifications.config import NotificationConfig
from autosys.notifications.dispatcher import Dispatcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_p10.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection as _conn
    _conn.reset_engines()
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn.reset_engines()


@pytest.fixture()
def session() -> Generator[Session, None, None]:
    with sync_session() as s:
        yield s


@pytest.fixture()
def am() -> AlarmManager:
    return AlarmManager()


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

_NOW = datetime(2024, 3, 15, 6, 0, 0)


def _make_job(
    session: Session,
    name: str,
    *,
    status:              str  = "INACTIVE",
    alarm_if_fail:       bool = False,
    alarm_if_terminated: bool = False,
    max_run_alarm:       int  | None = None,
    min_run_alarm:       int  | None = None,
    machine:             str  | None = None,
    last_start:          datetime | None = None,
) -> JobRow:
    row = JobRow(
        job_name            = name,
        job_type            = "CMD",
        status              = status,
        alarm_if_fail       = alarm_if_fail,
        alarm_if_terminated = alarm_if_terminated,
        max_run_alarm       = max_run_alarm,
        min_run_alarm       = min_run_alarm,
        machine             = machine,
        last_start          = last_start,
    )
    session.add(row)
    session.flush()
    return row


def _make_run(
    session: Session,
    job_name: str,
    *,
    status:     str,
    start_time: datetime,
    end_time:   datetime | None = None,
) -> JobRunRow:
    row = JobRunRow(
        run_id     = str(uuid.uuid4()),
        job_name   = job_name,
        status     = status,
        start_time = start_time,
        end_time   = end_time,
        run_date   = start_time.strftime("%Y-%m-%d"),
    )
    session.add(row)
    session.flush()
    return row


def _make_machine(session: Session, name: str, status: str = "UP") -> MachineRow:
    row = MachineRow(
        machine_name = name,
        host         = "10.0.0.1",
        port         = 7520,
        status       = status,
    )
    session.add(row)
    session.flush()
    return row


def _active_alarms(session: Session, job_name: str | None = None) -> list[AlarmRow]:
    from sqlalchemy import select
    stmt = select(AlarmRow).where(AlarmRow.cleared_at.is_(None))
    if job_name:
        stmt = stmt.where(AlarmRow.job_name == job_name)
    return list(session.execute(stmt).scalars())


# ===========================================================================
# TestAlarmManagerFailure
# ===========================================================================

class TestAlarmManagerFailure:

    def test_failure_alarm_raised_when_flag_set(self, session, am):
        _make_job(session, "job_a", status=5, alarm_if_fail=True)
        new = am.evaluate(session, _NOW)
        assert len(new) == 1
        assert new[0].alarm_type == "ALARM_IF_FAIL"
        assert new[0].job_name == "job_a"

    def test_failure_alarm_message_contains_job_name(self, session, am):
        _make_job(session, "job_a", status=5, alarm_if_fail=True)
        new = am.evaluate(session, _NOW)
        assert "job_a" in new[0].message

    def test_failure_alarm_job_status_recorded(self, session, am):
        _make_job(session, "job_a", status=5, alarm_if_fail=True)
        new = am.evaluate(session, _NOW)
        assert new[0].job_status_at_raise == 5

    def test_no_alarm_when_alarm_if_fail_false(self, session, am):
        _make_job(session, "job_a", status=5, alarm_if_fail=False)
        new = am.evaluate(session, _NOW)
        assert len(new) == 0

    def test_no_alarm_for_inactive_job(self, session, am):
        _make_job(session, "job_a", status=8, alarm_if_fail=True)
        new = am.evaluate(session, _NOW)
        assert len(new) == 0

    def test_no_alarm_for_success_job(self, session, am):
        _make_job(session, "job_a", status=4, alarm_if_fail=True)
        new = am.evaluate(session, _NOW)
        assert len(new) == 0

    def test_alarm_not_raised_if_notified_is_false_by_default(self, session, am):
        _make_job(session, "job_a", status=5, alarm_if_fail=True)
        new = am.evaluate(session, _NOW)
        assert new[0].notified is False


# ===========================================================================
# TestAlarmManagerTerminated
# ===========================================================================

class TestAlarmManagerTerminated:

    def test_terminated_alarm_raised(self, session, am):
        _make_job(session, "job_t", status=6, alarm_if_terminated=True)
        new = am.evaluate(session, _NOW)
        assert len(new) == 1
        assert new[0].alarm_type == "ALARM_IF_TERMINATED"

    def test_no_alarm_when_flag_false(self, session, am):
        _make_job(session, "job_t", status=6, alarm_if_terminated=False)
        new = am.evaluate(session, _NOW)
        assert len(new) == 0

    def test_terminated_alarm_status_recorded(self, session, am):
        _make_job(session, "job_t", status=6, alarm_if_terminated=True)
        new = am.evaluate(session, _NOW)
        assert new[0].job_status_at_raise == 6


# ===========================================================================
# TestAlarmManagerMaxRun
# ===========================================================================

class TestAlarmManagerMaxRun:

    def test_max_run_alarm_raised_after_threshold(self, session, am):
        start = _NOW - timedelta(minutes=91)
        _make_job(session, "slow_job", status=1, max_run_alarm=90, last_start=start)
        new = am.evaluate(session, _NOW)
        assert any(a.alarm_type == "MAX_RUN_ALARM" for a in new)

    def test_no_max_run_alarm_within_threshold(self, session, am):
        start = _NOW - timedelta(minutes=30)
        _make_job(session, "fast_job", status=1, max_run_alarm=90, last_start=start)
        new = am.evaluate(session, _NOW)
        assert not any(a.alarm_type == "MAX_RUN_ALARM" for a in new)

    def test_no_max_run_alarm_if_no_attribute(self, session, am):
        start = _NOW - timedelta(minutes=200)
        _make_job(session, "job_x", status=1, max_run_alarm=None, last_start=start)
        new = am.evaluate(session, _NOW)
        assert not any(a.alarm_type == "MAX_RUN_ALARM" for a in new)

    def test_max_run_alarm_message_contains_minutes(self, session, am):
        start = _NOW - timedelta(minutes=95)
        _make_job(session, "slow_job", status=1, max_run_alarm=90, last_start=start)
        new = am.evaluate(session, _NOW)
        alarm = next(a for a in new if a.alarm_type == "MAX_RUN_ALARM")
        assert "90" in alarm.message
        assert "slow_job" in alarm.message


# ===========================================================================
# TestAlarmManagerMinRun
# ===========================================================================

class TestAlarmManagerMinRun:

    def test_min_run_alarm_raised_for_short_run(self, session, am):
        _make_job(session, "feed_job", status=4, min_run_alarm=10)
        start = _NOW - timedelta(minutes=4)
        end   = _NOW - timedelta(minutes=2)
        _make_run(session, "feed_job", status=4, start_time=start, end_time=end)
        new = am.evaluate(session, _NOW)
        assert any(a.alarm_type == "MIN_RUN_ALARM" for a in new)

    def test_no_min_run_alarm_for_normal_duration(self, session, am):
        _make_job(session, "feed_job", status=4, min_run_alarm=10)
        start = _NOW - timedelta(minutes=15)
        end   = _NOW - timedelta(minutes=1)
        _make_run(session, "feed_job", status=4, start_time=start, end_time=end)
        new = am.evaluate(session, _NOW)
        assert not any(a.alarm_type == "MIN_RUN_ALARM" for a in new)

    def test_no_min_run_alarm_old_run(self, session, am):
        _make_job(session, "feed_job", status=4, min_run_alarm=10)
        start = _NOW - timedelta(hours=2)
        end   = start + timedelta(minutes=1)
        _make_run(session, "feed_job", status=4, start_time=start, end_time=end)
        new = am.evaluate(session, _NOW)
        assert not any(a.alarm_type == "MIN_RUN_ALARM" for a in new)

    def test_min_run_alarm_has_run_id(self, session, am):
        _make_job(session, "feed_job", status=4, min_run_alarm=10)
        start = _NOW - timedelta(minutes=4)
        end   = _NOW - timedelta(minutes=2)
        run = _make_run(session, "feed_job", status=4, start_time=start, end_time=end)
        new = am.evaluate(session, _NOW)
        alarm = next(a for a in new if a.alarm_type == "MIN_RUN_ALARM")
        assert alarm.run_id == run.run_id


# ===========================================================================
# TestAlarmManagerMachineDown
# ===========================================================================

class TestAlarmManagerMachineDown:

    def test_heartbeat_fail_raised_for_running_job_on_down_machine(self, session, am):
        _make_machine(session, "etl-server-01", status="DOWN")
        _make_job(session, "stuck_job", status=1, machine="etl-server-01")
        new = am.evaluate(session, _NOW)
        assert any(a.alarm_type == "HEARTBEAT_FAIL" for a in new)

    def test_heartbeat_fail_not_raised_for_up_machine(self, session, am):
        _make_machine(session, "etl-server-01", status="UP")
        _make_job(session, "ok_job", status=1, machine="etl-server-01")
        new = am.evaluate(session, _NOW)
        assert not any(a.alarm_type == "HEARTBEAT_FAIL" for a in new)

    def test_heartbeat_fail_not_raised_if_no_running_jobs(self, session, am):
        _make_machine(session, "etl-server-01", status="DOWN")
        _make_job(session, "idle_job", status=8, machine="etl-server-01")
        new = am.evaluate(session, _NOW)
        assert not any(a.alarm_type == "HEARTBEAT_FAIL" for a in new)

    def test_heartbeat_fail_message_contains_machine(self, session, am):
        _make_machine(session, "etl-server-01", status="DOWN")
        _make_job(session, "stuck_job", status=1, machine="etl-server-01")
        new = am.evaluate(session, _NOW)
        alarm = next(a for a in new if a.alarm_type == "HEARTBEAT_FAIL")
        assert "etl-server-01" in alarm.message


# ===========================================================================
# TestAlarmManagerDedup
# ===========================================================================

class TestAlarmManagerDedup:

    def test_second_failure_alarm_not_created(self, session, am):
        _make_job(session, "job_a", status=5, alarm_if_fail=True)
        am.evaluate(session, _NOW)
        session.flush()
        new2 = am.evaluate(session, _NOW + timedelta(seconds=10))
        assert not any(a.alarm_type == "ALARM_IF_FAIL" for a in new2)

    def test_second_max_run_alarm_not_created(self, session, am):
        start = _NOW - timedelta(minutes=100)
        _make_job(session, "slow", status=1, max_run_alarm=90, last_start=start)
        am.evaluate(session, _NOW)
        session.flush()
        new2 = am.evaluate(session, _NOW + timedelta(minutes=5))
        assert not any(a.alarm_type == "MAX_RUN_ALARM" for a in new2)

    def test_different_alarm_types_not_deduplicated(self, session, am):
        _make_job(session, "job_a", status=5,
                  alarm_if_fail=True, alarm_if_terminated=True)
        job_a = session.get(JobRow, "job_a")
        new = am.evaluate(session, _NOW)
        assert len([a for a in new if a.alarm_type == "ALARM_IF_FAIL"]) == 1


# ===========================================================================
# TestAlarmManagerAutoResolve
# ===========================================================================

class TestAlarmManagerAutoResolve:

    def test_alarm_auto_cleared_on_success(self, session, am):
        _make_job(session, "job_a", status=5, alarm_if_fail=True)
        am.evaluate(session, _NOW)
        session.flush()

        job = session.get(JobRow, "job_a")
        job.status=4
        session.flush()

        am.evaluate(session, _NOW + timedelta(minutes=5))
        session.flush()

        active = _active_alarms(session, "job_a")
        assert len(active) == 0

    def test_auto_resolve_sets_cleared_by_auto(self, session, am):
        _make_job(session, "job_a", status=5, alarm_if_fail=True)
        am.evaluate(session, _NOW)
        session.flush()

        job = session.get(JobRow, "job_a")
        job.status=4
        session.flush()

        am.evaluate(session, _NOW + timedelta(minutes=5))

        from sqlalchemy import select
        alarm = session.execute(
            select(AlarmRow).where(AlarmRow.job_name == "job_a")
        ).scalar_one()
        assert alarm.cleared_by == "auto"
        assert alarm.cleared_at is not None

    def test_no_auto_resolve_for_still_failing_job(self, session, am):
        _make_job(session, "job_a", status=5, alarm_if_fail=True)
        am.evaluate(session, _NOW)
        session.flush()

        am.evaluate(session, _NOW + timedelta(minutes=5))

        active = _active_alarms(session, "job_a")
        assert len(active) == 1


# ===========================================================================
# TestDispatcher
# ===========================================================================

class TestDispatcher:

    def _mock_dispatcher(self, **urls) -> tuple[Dispatcher, list[dict]]:
        """Returns dispatcher and captured call list."""
        calls: list[dict] = []

        def fake_http_post(url: str, payload: dict) -> bool:
            calls.append({"url": url, "payload": payload})
            return True

        cfg = NotificationConfig()
        cfg.nsm_url        = urls.get("nsm_url")
        cfg.smtp_host      = None
        cfg.slack_url      = urls.get("slack_url")
        cfg.pd_routing_key = urls.get("pd_routing_key")

        d = Dispatcher(config=cfg, http_post_fn=fake_http_post)
        return d, calls

    def _make_alarm_row(self) -> AlarmRow:
        return AlarmRow(
            alarm_id            = str(uuid.uuid4()),
            job_name            = "test_job",
            alarm_type          = "ALARM_IF_FAIL",
            message             = "job failed",
            job_status_at_raise = "FAILURE",
            raised_at           = _NOW,
            notified            = False,
        )

    def test_send_nsm_webhook(self):
        d, calls = self._mock_dispatcher(nsm_url="http://nsm.example.com/webhook")
        alarm = self._make_alarm_row()
        n = d.send(alarm)
        assert n == 1
        assert len(calls) == 1
        assert calls[0]["url"] == "http://nsm.example.com/webhook"
        payload = calls[0]["payload"]
        assert payload["job_name"] == "test_job"
        assert payload["alarm_type"] == "ALARM_IF_FAIL"
        assert "alarm_id" in payload
        assert "ts" in payload

    def test_send_slack_webhook(self):
        d, calls = self._mock_dispatcher(slack_url="http://slack.example.com/hook")
        alarm = self._make_alarm_row()
        n = d.send(alarm)
        assert n == 1
        assert len(calls) == 1
        payload = calls[0]["payload"]
        assert "blocks" in payload

    def test_send_pagerduty(self):
        d, calls = self._mock_dispatcher(pd_routing_key="abc123")
        alarm = self._make_alarm_row()
        n = d.send(alarm)
        assert n == 1
        assert len(calls) == 1
        assert "events.pagerduty.com" in calls[0]["url"]
        pd_payload = calls[0]["payload"]
        assert pd_payload["routing_key"] == "abc123"
        assert pd_payload["event_action"] == "trigger"

    def test_send_multiple_channels(self):
        d, calls = self._mock_dispatcher(
            nsm_url="http://nsm.example.com",
            slack_url="http://slack.example.com",
        )
        alarm = self._make_alarm_row()
        n = d.send(alarm)
        assert n == 2
        assert len(calls) == 2

    def test_notified_flag_set_on_success(self):
        d, _ = self._mock_dispatcher(nsm_url="http://nsm.example.com")
        alarm = self._make_alarm_row()
        d.send(alarm)
        assert alarm.notified is True

    def test_notified_stays_false_when_no_channels(self):
        d, _ = self._mock_dispatcher()
        alarm = self._make_alarm_row()
        d.send(alarm)
        assert alarm.notified is False

    def test_channel_failure_does_not_block_others(self):
        calls: list[dict] = []

        def flaky_post(url: str, payload: dict) -> bool:
            calls.append(url)
            if "nsm" in url:
                raise ConnectionError("nsm down")
            return True

        cfg = NotificationConfig()
        cfg.nsm_url        = "http://nsm.example.com"
        cfg.smtp_host      = None
        cfg.slack_url      = "http://slack.example.com"
        cfg.pd_routing_key = None
        d = Dispatcher(config=cfg, http_post_fn=flaky_post)
        alarm = self._make_alarm_row()
        n = d.send(alarm)
        assert n == 1

    def test_pagerduty_severity_error_for_failure(self):
        d, calls = self._mock_dispatcher(pd_routing_key="key123")
        alarm = self._make_alarm_row()
        d.send(alarm)
        assert calls[0]["payload"]["payload"]["severity"] == "error"

    def test_pagerduty_severity_critical_for_heartbeat(self):
        d, calls = self._mock_dispatcher(pd_routing_key="key123")
        alarm = self._make_alarm_row()
        alarm.alarm_type = "HEARTBEAT_FAIL"
        d.send(alarm)
        assert calls[0]["payload"]["payload"]["severity"] == "critical"

    def test_smtp_send_fn_called(self):
        smtp_calls: list[tuple] = []

        def fake_smtp(cfg, subject: str, body: str) -> bool:
            smtp_calls.append((subject, body))
            return True

        cfg = NotificationConfig()
        cfg.nsm_url     = None
        cfg.smtp_host   = "smtp.example.com"
        cfg.smtp_from   = "autosys@example.com"
        cfg.smtp_to_list = ["ops@example.com"]
        cfg.slack_url   = None
        cfg.pd_routing_key = None
        d = Dispatcher(config=cfg, smtp_send_fn=fake_smtp)
        alarm = self._make_alarm_row()
        n = d.send(alarm)
        assert n == 1
        assert "ALARM_IF_FAIL" in smtp_calls[0][0]
        assert "test_job" in smtp_calls[0][1]


# ===========================================================================
# TestEPSIntegration
# ===========================================================================

class TestEPSIntegration:

    def _make_eps(self, am: AlarmManager, dispatcher=None):
        from autosys.scheduler.event_processor import EventProcessor
        return EventProcessor(
            auto_complete = False,
            alarm_manager = am,
            dispatcher    = dispatcher,
        )

    def test_eps_raises_alarm_on_failure(self, session, am):
        _make_job(session, "failing_job", status=5, alarm_if_fail=True)
        session.commit()

        eps = self._make_eps(am)
        with sync_session() as s:
            eps.process_one_tick(s, now=_NOW)

        with sync_session() as s:
            alarms = _active_alarms(s, "failing_job")
        assert len(alarms) == 1
        assert alarms[0].alarm_type == "ALARM_IF_FAIL"

    def test_eps_no_alarm_without_alarm_manager(self):
        from autosys.scheduler.event_processor import EventProcessor
        with sync_session() as s:
            _make_job(s, "job_x", status=5, alarm_if_fail=True)

        eps = EventProcessor(auto_complete=False, alarm_manager=None)
        with sync_session() as s:
            eps.process_one_tick(s, now=_NOW)

        with sync_session() as s:
            alarms = _active_alarms(s, "job_x")
        assert len(alarms) == 0

    def test_eps_calls_dispatcher_for_new_alarm(self, session, am):
        dispatched: list = []

        class FakeDispatcher:
            def send(self, alarm):
                dispatched.append(alarm.alarm_type)
                return 1

        _make_job(session, "the_job", status=5, alarm_if_fail=True)
        session.commit()

        eps = self._make_eps(am, dispatcher=FakeDispatcher())
        with sync_session() as s:
            eps.process_one_tick(s, now=_NOW)

        assert "ALARM_IF_FAIL" in dispatched
