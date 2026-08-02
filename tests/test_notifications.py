"""
Phase 5 — Notification Delivery tests.

Tests for:
1. SnmpNotifier — SNMP trap construction and UDP send
2. RemedyNotifier — REST API ticket creation
3. Per-job notification_type routing
4. Retry logic with exponential backoff
5. Backward compat (no notification_type → all channels)
"""

from __future__ import annotations

import uuid
from datetime import datetime

import pytest

from autosys.db.schema import AlarmRow, JobRow
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.notifications.config import NotificationConfig
from autosys.notifications.dispatcher import Dispatcher
from autosys.notifications.snmp_notifier import SnmpNotifier
from autosys.notifications.remedy_notifier import RemedyNotifier


# ===========================================================================
# Fixtures
# ===========================================================================

_NOW = datetime(2025, 1, 15, 10, 30, 0)


def _make_alarm_row(job_name: str = "test_job") -> AlarmRow:
    return AlarmRow(
        alarm_id            = str(uuid.uuid4()),
        job_name            = job_name,
        alarm_type          = "ALARM_IF_FAIL",
        message             = "job failed",
        job_status_at_raise = "FAILURE",
        raised_at           = _NOW,
        notified            = False,
    )


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    reset_engines()
    with sync_session() as session:
        create_all_sync(session)
    yield
    reset_engines()


def _insert_job(session, job_name: str, notification_type: str | None = None):
    job = JobRow(
        job_name=job_name,
        job_type="CMD",
        command="echo hi",
        machine="localhost",
        status=5,
        alarm_if_fail=True,
        notification_type=notification_type,
    )
    session.add(job)
    session.commit()


# ===========================================================================
# 1. SnmpNotifier
# ===========================================================================

class TestSnmpNotifier:

    def test_snmp_send_calls_udp(self):
        calls: list[tuple] = []

        def fake_send(host: str, port: int, data: bytes) -> bool:
            calls.append((host, port, data))
            return True

        notifier = SnmpNotifier(
            host="nms.example.com",
            port=162,
            community="public",
            send_fn=fake_send,
        )
        alarm = _make_alarm_row()
        ok = notifier.send(alarm)

        assert ok is True
        assert len(calls) == 1
        assert calls[0][0] == "nms.example.com"
        assert calls[0][1] == 162
        assert isinstance(calls[0][2], bytes)
        assert len(calls[0][2]) > 0

    def test_snmp_pdu_starts_with_sequence(self):
        def fake_send(host, port, data):
            return True

        notifier = SnmpNotifier(host="nms", send_fn=fake_send)
        alarm = _make_alarm_row()
        pdu = notifier._build_trap_pdu(alarm)

        # SNMP message starts with 0x30 (SEQUENCE)
        assert pdu[0] == 0x30

    def test_snmp_send_failure_returns_false(self):
        def failing_send(host, port, data):
            raise ConnectionError("network down")

        notifier = SnmpNotifier(host="nms", send_fn=failing_send)
        alarm = _make_alarm_row()
        ok = notifier.send(alarm)
        assert ok is False

    def test_snmp_community_in_pdu(self):
        def fake_send(host, port, data):
            # Check community string "public" is in the PDU
            assert b"public" in data
            return True

        notifier = SnmpNotifier(
            host="nms", community="public", send_fn=fake_send,
        )
        alarm = _make_alarm_row()
        assert notifier.send(alarm) is True

    def test_snmp_custom_community(self):
        captured = {}

        def fake_send(host, port, data):
            captured["data"] = data
            return True

        notifier = SnmpNotifier(
            host="nms", community="private", send_fn=fake_send,
        )
        alarm = _make_alarm_row()
        notifier.send(alarm)
        assert b"private" in captured["data"]


# ===========================================================================
# 2. RemedyNotifier
# ===========================================================================

class TestRemedyNotifier:

    def test_remedy_send_creates_ticket(self):
        calls: list[dict] = []

        def fake_post(url: str, payload: dict, headers: dict) -> bool:
            calls.append({"url": url, "payload": payload, "headers": headers})
            return True

        notifier = RemedyNotifier(
            api_url="https://remedy.example.com/api/arsys/v1",
            api_token="token123",
            http_post_fn=fake_post,
        )
        alarm = _make_alarm_row()
        ok = notifier.send(alarm)

        assert ok is True
        assert len(calls) == 1
        assert "remedy.example.com" in calls[0]["url"]
        assert calls[0]["headers"]["Authorization"] == "Bearer token123"
        vals = calls[0]["payload"]["values"]
        assert "ALARM_IF_FAIL" in vals["Summary"]
        assert vals["Custom_Field_3"] == "test_job"

    def test_remedy_send_failure_returns_false(self):
        def failing_post(url, payload, headers):
            raise ConnectionError("remedy down")

        notifier = RemedyNotifier(
            api_url="https://remedy.example.com",
            api_token="tok",
            http_post_fn=failing_post,
        )
        alarm = _make_alarm_row()
        ok = notifier.send(alarm)
        assert ok is False

    def test_remedy_ticket_has_alarm_id(self):
        captured = {}

        def fake_post(url, payload, headers):
            captured["payload"] = payload
            return True

        notifier = RemedyNotifier(
            api_url="https://remedy.example.com",
            api_token="tok",
            http_post_fn=fake_post,
        )
        alarm = _make_alarm_row()
        notifier.send(alarm)
        assert captured["payload"]["values"]["Custom_Field_1"] == alarm.alarm_id


# ===========================================================================
# 3. Per-job notification_type routing
# ===========================================================================

class TestPerJobRouting:

    def test_email_routing_only_sends_email(self):
        smtp_calls: list[tuple] = []

        def fake_smtp(cfg, subject, body):
            smtp_calls.append((subject, body))
            return True

        cfg = NotificationConfig()
        cfg.nsm_url = "http://nsm.example.com"
        cfg.smtp_host = "smtp.example.com"
        cfg.smtp_from = "autosys@example.com"
        cfg.smtp_to_list = ["ops@example.com"]
        cfg.slack_url = "http://slack.example.com"
        cfg.pd_routing_key = "key123"
        cfg.snmp_host = None
        cfg.remedy_url = None

        http_calls: list[dict] = []

        def fake_http_post(url, payload):
            http_calls.append({"url": url})
            return True

        d = Dispatcher(config=cfg, http_post_fn=fake_http_post, smtp_send_fn=fake_smtp)

        with sync_session() as session:
            _insert_job(session, "email_job", notification_type="EMAIL")

        alarm = _make_alarm_row("email_job")
        n = d.send(alarm)

        assert n == 1
        assert len(smtp_calls) == 1
        assert len(http_calls) == 0  # NSM and Slack not called

    def test_snmp_routing_only_sends_snmp(self):
        snmp_calls: list[tuple] = []

        def fake_snmp_send(host, port, data):
            snmp_calls.append((host, port, data))
            return True

        cfg = NotificationConfig()
        cfg.nsm_url = "http://nsm.example.com"
        cfg.smtp_host = "smtp.example.com"
        cfg.smtp_from = "autosys@example.com"
        cfg.smtp_to_list = ["ops@example.com"]
        cfg.slack_url = "http://slack.example.com"
        cfg.pd_routing_key = "key123"
        cfg.snmp_host = "nms.local"
        cfg.remedy_url = None

        http_calls: list[dict] = []

        def fake_http_post(url, payload):
            http_calls.append({"url": url})
            return True

        d = Dispatcher(
            config=cfg,
            http_post_fn=fake_http_post,
            snmp_send_fn=fake_snmp_send,
        )

        with sync_session() as session:
            _insert_job(session, "snmp_job", notification_type="SNMP")

        alarm = _make_alarm_row("snmp_job")
        n = d.send(alarm)

        assert n == 1
        assert len(snmp_calls) == 1
        assert len(http_calls) == 0

    def test_nsm_routing_only_sends_nsm(self):
        cfg = NotificationConfig()
        cfg.nsm_url = "http://nsm.example.com"
        cfg.smtp_host = "smtp.example.com"
        cfg.smtp_from = "autosys@example.com"
        cfg.smtp_to_list = ["ops@example.com"]
        cfg.slack_url = "http://slack.example.com"
        cfg.snmp_host = None
        cfg.remedy_url = None

        http_calls: list[dict] = []

        def fake_http_post(url, payload):
            http_calls.append({"url": url})
            return True

        d = Dispatcher(config=cfg, http_post_fn=fake_http_post)

        with sync_session() as session:
            _insert_job(session, "nsm_job", notification_type="NSM")

        alarm = _make_alarm_row("nsm_job")
        n = d.send(alarm)

        assert n == 1
        assert len(http_calls) == 1
        assert "nsm.example.com" in http_calls[0]["url"]

    def test_remedy_routing_only_sends_remedy(self):
        remedy_calls: list[dict] = []

        def fake_remedy_post(url, payload, headers):
            remedy_calls.append({"url": url})
            return True

        cfg = NotificationConfig()
        cfg.nsm_url = "http://nsm.example.com"
        cfg.smtp_host = "smtp.example.com"
        cfg.smtp_from = "autosys@example.com"
        cfg.smtp_to_list = ["ops@example.com"]
        cfg.snmp_host = None
        cfg.remedy_url = "https://remedy.example.com"
        cfg.remedy_token = "tok"

        http_calls: list[dict] = []

        def fake_http_post(url, payload):
            http_calls.append({"url": url})
            return True

        d = Dispatcher(
            config=cfg,
            http_post_fn=fake_http_post,
            remedy_post_fn=fake_remedy_post,
        )

        with sync_session() as session:
            _insert_job(session, "remedy_job", notification_type="REMEDY")

        alarm = _make_alarm_row("remedy_job")
        n = d.send(alarm)

        assert n == 1
        assert len(remedy_calls) == 1
        assert len(http_calls) == 0

    def test_no_notification_type_sends_all(self):
        """Backward compat: job without notification_type → all channels."""
        cfg = NotificationConfig()
        cfg.nsm_url = "http://nsm.example.com"
        cfg.smtp_host = "smtp.example.com"
        cfg.smtp_from = "autosys@example.com"
        cfg.smtp_to_list = ["ops@example.com"]
        cfg.slack_url = None
        cfg.pd_routing_key = None
        cfg.snmp_host = None
        cfg.remedy_url = None

        http_calls: list[dict] = []

        def fake_http_post(url, payload):
            http_calls.append({"url": url})
            return True

        smtp_calls: list[tuple] = []

        def fake_smtp(cfg, subject, body):
            smtp_calls.append((subject, body))
            return True

        d = Dispatcher(
            config=cfg,
            http_post_fn=fake_http_post,
            smtp_send_fn=fake_smtp,
        )

        with sync_session() as session:
            _insert_job(session, "plain_job", notification_type=None)

        alarm = _make_alarm_row("plain_job")
        n = d.send(alarm)

        assert n == 2  # NSM + Email
        assert len(http_calls) == 1
        assert len(smtp_calls) == 1


# ===========================================================================
# 4. Retry logic
# ===========================================================================

class TestRetryLogic:

    def test_retry_succeeds_on_second_attempt(self):
        attempts = {"n": 0}

        def flaky_send():
            attempts["n"] += 1
            if attempts["n"] < 2:
                return False
            return True

        cfg = NotificationConfig()
        cfg.nsm_url = "http://nsm.example.com"
        cfg.smtp_host = None
        cfg.slack_url = None
        cfg.pd_routing_key = None
        cfg.snmp_host = None
        cfg.remedy_url = None

        d = Dispatcher(config=cfg, max_retries=3)
        ok = d._send_with_retry(flaky_send)
        assert ok is True
        assert attempts["n"] == 2

    def test_retry_exhausted_returns_false(self):
        attempts = {"n": 0}

        def always_fail():
            attempts["n"] += 1
            return False

        cfg = NotificationConfig()
        d = Dispatcher(config=cfg, max_retries=3)
        ok = d._send_with_retry(always_fail)
        assert ok is False
        assert attempts["n"] == 3

    def test_retry_catches_exception(self):
        attempts = {"n": 0}

        def always_throws():
            attempts["n"] += 1
            raise ConnectionError("boom")

        cfg = NotificationConfig()
        d = Dispatcher(config=cfg, max_retries=3)
        ok = d._send_with_retry(always_throws)
        assert ok is False
        assert attempts["n"] == 3

    def test_retry_succeeds_after_exception(self):
        attempts = {"n": 0}

        def fails_then_succeeds():
            attempts["n"] += 1
            if attempts["n"] < 2:
                raise ConnectionError("transient")
            return True

        cfg = NotificationConfig()
        d = Dispatcher(config=cfg, max_retries=3)
        ok = d._send_with_retry(fails_then_succeeds)
        assert ok is True
        assert attempts["n"] == 2


# ===========================================================================
# 5. NotificationConfig
# ===========================================================================

class TestNotificationConfig:

    def test_snmp_config_defaults(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_SNMP_HOST", "nms.local")
        monkeypatch.delenv("AUTOSYS_SNMP_PORT", raising=False)
        monkeypatch.delenv("AUTOSYS_SNMP_COMMUNITY", raising=False)
        cfg = NotificationConfig()
        assert cfg.snmp_host == "nms.local"
        assert cfg.snmp_port == 162
        assert cfg.snmp_community == "public"

    def test_remedy_config(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_REMEDY_API_URL", "https://remedy.example.com")
        monkeypatch.setenv("AUTOSYS_REMEDY_API_TOKEN", "secret")
        cfg = NotificationConfig()
        assert cfg.remedy_url == "https://remedy.example.com"
        assert cfg.remedy_token == "secret"

    def test_any_enabled_with_snmp(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_SNMP_HOST", "nms.local")
        monkeypatch.delenv("NSM_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("PD_ROUTING_KEY", raising=False)
        monkeypatch.delenv("AUTOSYS_REMEDY_API_URL", raising=False)
        cfg = NotificationConfig()
        assert cfg.any_enabled is True

    def test_any_enabled_false(self, monkeypatch):
        monkeypatch.delenv("NSM_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("SMTP_HOST", raising=False)
        monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
        monkeypatch.delenv("PD_ROUTING_KEY", raising=False)
        monkeypatch.delenv("AUTOSYS_SNMP_HOST", raising=False)
        monkeypatch.delenv("AUTOSYS_REMEDY_API_URL", raising=False)
        cfg = NotificationConfig()
        assert cfg.any_enabled is False
