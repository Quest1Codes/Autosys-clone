"""
Phase 18 — Notification dispatcher and box manager coverage tests.

Targets uncovered lines in:
- notifications/dispatcher.py (channel routing, retry, payload building)
- notifications/snmp_notifier.py
- notifications/remedy_notifier.py
- scheduler/box_manager.py
- scheduler/condition_evaluator.py
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.schema import JobRow, AlarmRow
from autosys.models.enums import JobStatus
from autosys.notifications.config import NotificationConfig
from autosys.notifications.dispatcher import Dispatcher


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_notif.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines()
    create_all_sync(drop_first=False)
    yield
    reset_engines()


def _make_alarm(job_name="test_job", alarm_type="FAILURE", message="test"):
    return AlarmRow(
        alarm_id=str(uuid.uuid4()),
        job_name=job_name,
        alarm_type=alarm_type,
        message=message,
        raised_at=datetime.utcnow(),
    )


def _make_job(session, name, **kwargs):
    row = JobRow(
        job_name=name,
        job_type=kwargs.get("job_type", "CMD"),
        command=kwargs.get("command", "echo hi"),
        machine=kwargs.get("machine", "localhost"),
        status=kwargs.get("status", JobStatus.INACTIVE.value),
        notification_type=kwargs.get("notification_type"),
    )
    session.add(row)
    session.flush()
    return row


def _make_cfg(monkeypatch, **env_vars):
    """Create NotificationConfig with specific env vars set."""
    for k, v in env_vars.items():
        monkeypatch.setenv(k, v)
    return NotificationConfig()


# ===========================================================================
# Dispatcher — channel routing
# ===========================================================================

class TestDispatcherRouting:

    def test_send_nsm_channel(self, fresh_db, monkeypatch):
        cfg = _make_cfg(monkeypatch, NSM_WEBHOOK_URL="http://localhost:9999/nsm")
        sent = []
        def mock_post(url, payload):
            sent.append((url, payload))
            return True
        d = Dispatcher(cfg, http_post_fn=mock_post)
        with sync_session() as s:
            _make_job(s, "nsm_job")
            s.commit()
            alarm = _make_alarm("nsm_job")
            n = d.send(alarm)
        assert n >= 1
        assert any("nsm" in url for url, _ in sent)

    def test_send_email_channel(self, fresh_db, monkeypatch):
        cfg = _make_cfg(monkeypatch,
            SMTP_HOST="localhost", SMTP_FROM="a@b.com", SMTP_TO="c@d.com",
        )
        def mock_smtp(config, subject, body):
            return True
        d = Dispatcher(cfg, smtp_send_fn=mock_smtp)
        with sync_session() as s:
            _make_job(s, "email_job")
            s.commit()
            alarm = _make_alarm("email_job")
            n = d.send(alarm)
        assert n >= 1

    def test_send_slack_channel(self, fresh_db, monkeypatch):
        cfg = _make_cfg(monkeypatch, SLACK_WEBHOOK_URL="http://localhost:9999/slack")
        sent = []
        def mock_post(url, payload):
            sent.append(url)
            return True
        d = Dispatcher(cfg, http_post_fn=mock_post)
        with sync_session() as s:
            _make_job(s, "slack_job")
            s.commit()
            alarm = _make_alarm("slack_job")
            n = d.send(alarm)
        assert n >= 1
        assert any("slack" in u for u in sent)

    def test_send_pagerduty_channel(self, fresh_db, monkeypatch):
        cfg = _make_cfg(monkeypatch, PD_ROUTING_KEY="test-key-123")
        sent = []
        def mock_post(url, payload):
            sent.append((url, payload))
            return True
        d = Dispatcher(cfg, http_post_fn=mock_post)
        with sync_session() as s:
            _make_job(s, "pd_job")
            s.commit()
            alarm = _make_alarm("pd_job")
            n = d.send(alarm)
        assert n >= 1
        assert any("pagerduty" in u for u, _ in sent)

    def test_job_notification_type_email(self, fresh_db, monkeypatch):
        cfg = _make_cfg(monkeypatch,
            SMTP_HOST="localhost", SMTP_FROM="a@b.com", SMTP_TO="c@d.com",
        )
        def mock_smtp(config, subject, body):
            return True
        d = Dispatcher(cfg, smtp_send_fn=mock_smtp)
        with sync_session() as s:
            _make_job(s, "typed_email_job", notification_type="EMAIL")
            s.commit()
            alarm = _make_alarm("typed_email_job")
            n = d.send(alarm)
        assert n >= 1

    def test_job_notification_type_nsm(self, fresh_db, monkeypatch):
        cfg = _make_cfg(monkeypatch, NSM_WEBHOOK_URL="http://localhost:9999/nsm")
        sent = []
        def mock_post(url, payload):
            sent.append(url)
            return True
        d = Dispatcher(cfg, http_post_fn=mock_post)
        with sync_session() as s:
            _make_job(s, "typed_nsm_job", notification_type="NSM")
            s.commit()
            alarm = _make_alarm("typed_nsm_job")
            n = d.send(alarm)
        assert n >= 1

    def test_no_channels_configured(self, fresh_db, monkeypatch):
        cfg = _make_cfg(monkeypatch)
        d = Dispatcher(cfg)
        with sync_session() as s:
            _make_job(s, "no_chan_job")
            s.commit()
            alarm = _make_alarm("no_chan_job")
            n = d.send(alarm)
        assert n == 0

    def test_alarm_notified_flag_set(self, fresh_db, monkeypatch):
        cfg = _make_cfg(monkeypatch, SLACK_WEBHOOK_URL="http://localhost:9999/slack")
        def mock_post(url, payload):
            return True
        d = Dispatcher(cfg, http_post_fn=mock_post)
        with sync_session() as s:
            _make_job(s, "flag_job")
            s.commit()
            alarm = _make_alarm("flag_job")
            d.send(alarm)
        assert alarm.notified is True


# ===========================================================================
# Dispatcher — retry logic
# ===========================================================================

class TestDispatcherRetry:

    def test_retry_succeeds_on_second_attempt(self, fresh_db, monkeypatch):
        cfg = _make_cfg(monkeypatch, SLACK_WEBHOOK_URL="http://localhost:9999/slack")
        calls = [0]
        def flaky_post(url, payload):
            calls[0] += 1
            if calls[0] == 1:
                raise ConnectionError("first attempt fails")
            return True
        d = Dispatcher(cfg, http_post_fn=flaky_post, max_retries=3)
        with sync_session() as s:
            _make_job(s, "retry_job")
            s.commit()
            alarm = _make_alarm("retry_job")
            n = d.send(alarm)
        assert n >= 1

    def test_retry_exhausted_returns_false(self, fresh_db, monkeypatch):
        cfg = _make_cfg(monkeypatch, SLACK_WEBHOOK_URL="http://localhost:9999/slack")
        def always_fail(url, payload):
            raise ConnectionError("always fails")
        d = Dispatcher(cfg, http_post_fn=always_fail, max_retries=2)
        with sync_session() as s:
            _make_job(s, "fail_job")
            s.commit()
            alarm = _make_alarm("fail_job")
            n = d.send(alarm)
        assert n == 0


# ===========================================================================
# Dispatcher — payload and helpers
# ===========================================================================

class TestDispatcherHelpers:

    def test_build_payload(self):
        alarm = _make_alarm("payload_job", "MAX_RUN_ALARM", "took too long")
        payload = Dispatcher._build_payload(alarm)
        assert payload["job_name"] == "payload_job"
        assert payload["alarm_type"] == "MAX_RUN_ALARM"
        assert payload["message"] == "took too long"
        assert "ts" in payload
        assert "alarm_id" in payload

    def test_get_job_notification_type_none(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "no_type_job")
            s.commit()
        result = Dispatcher._get_job_notification_type("no_type_job")
        assert result is None

    def test_get_job_notification_type_set(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "typed_job", notification_type="EMAIL")
            s.commit()
        result = Dispatcher._get_job_notification_type("typed_job")
        assert result == "EMAIL"

    def test_get_job_notification_type_not_found(self, fresh_db):
        result = Dispatcher._get_job_notification_type("nonexistent_job")
        assert result is None


# ===========================================================================
# SNMP notifier
# ===========================================================================

class TestSnmpNotifier:

    def test_snmp_send_success(self):
        from autosys.notifications.snmp_notifier import SnmpNotifier
        calls = []
        def mock_send(host, port, data):
            calls.append((host, port, data))
            return True
        notifier = SnmpNotifier(
            host="localhost", port=161, community="public", send_fn=mock_send,
        )
        alarm = _make_alarm("snmp_job", "FAILURE", "snmp test")
        result = notifier.send(alarm)
        assert result is True
        assert len(calls) == 1
        assert isinstance(calls[0][2], bytes)

    def test_snmp_send_failure(self):
        from autosys.notifications.snmp_notifier import SnmpNotifier
        def mock_send(*args, **kwargs):
            raise ConnectionError("SNMP timeout")
        notifier = SnmpNotifier(
            host="localhost", port=161, community="public", send_fn=mock_send,
        )
        alarm = _make_alarm("snmp_fail_job", "FAILURE", "snmp fail")
        result = notifier.send(alarm)
        assert result is False


# ===========================================================================
# Remedy notifier
# ===========================================================================

class TestRemedyNotifier:

    def test_remedy_send_success(self):
        from autosys.notifications.remedy_notifier import RemedyNotifier
        calls = []
        def mock_post(url, payload, headers=None):
            calls.append((url, payload))
            return True
        notifier = RemedyNotifier(
            api_url="http://remedy.example.com/api",
            api_token="test-token",
            http_post_fn=mock_post,
        )
        alarm = _make_alarm("remedy_job", "FAILURE", "remedy test")
        result = notifier.send(alarm)
        assert result is True
        assert len(calls) == 1

    def test_remedy_send_failure(self):
        from autosys.notifications.remedy_notifier import RemedyNotifier
        def mock_post(*args, **kwargs):
            raise ConnectionError("Remedy API down")
        notifier = RemedyNotifier(
            api_url="http://remedy.example.com/api",
            api_token="test-token",
            http_post_fn=mock_post,
        )
        alarm = _make_alarm("remedy_fail_job", "FAILURE", "remedy fail")
        result = notifier.send(alarm)
        assert result is False


# ===========================================================================
# Condition evaluator edge cases
# ===========================================================================

class TestConditionEvaluator:

    def test_none_condition_is_satisfied(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied(None, {}) is True

    def test_empty_condition_is_satisfied(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied("", {}) is True

    def test_success_condition_met(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied("success(job_a)", {"job_a": "SUCCESS"}) is True

    def test_success_condition_not_met(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied("success(job_a)", {"job_a": "FAILURE"}) is False

    def test_done_condition_met(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied("done(job_a)", {"job_a": "SUCCESS"}) is True
        assert is_satisfied("done(job_a)", {"job_a": "FAILURE"}) is True

    def test_failure_condition_met(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied("failure(job_a)", {"job_a": "FAILURE"}) is True

    def test_notrunning_condition_met(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied("notrunning(job_a)", {"job_a": "SUCCESS"}) is True
        assert is_satisfied("notrunning(job_a)", {"job_a": "RUNNING"}) is False

    def test_compound_and_both_met(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        snap = {"job_a": "SUCCESS", "job_b": "SUCCESS"}
        assert is_satisfied("success(job_a) & success(job_b)", snap) is True

    def test_compound_and_one_not_met(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        snap = {"job_a": "SUCCESS", "job_b": "FAILURE"}
        assert is_satisfied("success(job_a) & success(job_b)", snap) is False

    def test_compound_or_either_met(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        snap = {"job_a": "SUCCESS", "job_b": "FAILURE"}
        assert is_satisfied("success(job_a) | success(job_b)", snap) is True

    def test_shorthand_s(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied("s(job_a)", {"job_a": "SUCCESS"}) is True

    def test_shorthand_f(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied("f(job_a)", {"job_a": "FAILURE"}) is True

    def test_shorthand_d(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied("d(job_a)", {"job_a": "SUCCESS"}) is True

    def test_value_condition_with_globals(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied(
            'value(MY_VAR) = "yes"',
            {},
            globals_dict={"MY_VAR": "yes"},
        ) is True

    def test_value_condition_not_matched(self):
        from autosys.scheduler.condition_evaluator import is_satisfied
        assert is_satisfied(
            'value(MY_VAR) = "yes"',
            {},
            globals_dict={"MY_VAR": "no"},
        ) is False
