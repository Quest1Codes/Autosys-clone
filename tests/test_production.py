"""
Phase 8 — Production Hardening tests.

Tests for:
1. DB retry logic (retry_db decorator)
2. Structured logging configuration
3. Metrics endpoint (Prometheus format)
4. Health check endpoints (liveness, readiness)
5. Graceful shutdown (signal handling in EventProcessor)
"""

from __future__ import annotations

import os
import time
from unittest.mock import patch, MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.exc import OperationalError
from sqlalchemy import text

from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines()
    with sync_session() as session:
        create_all_sync(session)
    yield
    reset_engines()


@pytest.fixture()
def client():
    from autosys.app_server.main import create_app
    app = create_app(start_eps=False)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ===========================================================================
# 1. DB Retry Logic
# ===========================================================================

class TestDBRetry:

    def test_retry_succeeds_on_second_attempt(self):
        from autosys.db.retry import retry_db

        call_count = 0

        @retry_db(max_attempts=3, base_delay=0.01, backoff_factor=2.0)
        def flaky_operation():
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise OperationalError("stmt", {}, Exception("connection lost"))
            return "success"

        result = flaky_operation()
        assert result == "success"
        assert call_count == 2

    def test_retry_fails_after_max_attempts(self):
        from autosys.db.retry import retry_db

        call_count = 0

        @retry_db(max_attempts=3, base_delay=0.01, backoff_factor=2.0)
        def always_fails():
            nonlocal call_count
            call_count += 1
            raise OperationalError("stmt", {}, Exception("connection lost"))

        with pytest.raises(OperationalError):
            always_fails()
        assert call_count == 3

    def test_retry_does_not_retry_non_operational_errors(self):
        from autosys.db.retry import retry_db

        call_count = 0

        @retry_db(max_attempts=3, base_delay=0.01)
        def raises_value_error():
            nonlocal call_count
            call_count += 1
            raise ValueError("not a DB error")

        with pytest.raises(ValueError):
            raises_value_error()
        assert call_count == 1

    def test_retry_succeeds_first_time(self):
        from autosys.db.retry import retry_db

        @retry_db(max_attempts=3, base_delay=0.01)
        def succeeds():
            return "ok"

        assert succeeds() == "ok"


# ===========================================================================
# 2. Structured Logging
# ===========================================================================

class TestStructuredLogging:

    def test_correlation_id_set_and_get(self):
        from autosys.logging_config import set_correlation_id, get_correlation_id

        cid = set_correlation_id("test-123")
        assert cid == "test-123"
        assert get_correlation_id() == "test-123"

    def test_correlation_id_auto_generated(self):
        from autosys.logging_config import set_correlation_id, get_correlation_id

        cid = set_correlation_id()
        assert len(cid) > 0
        assert get_correlation_id() == cid

    def test_setup_logging_text_format(self, monkeypatch):
        from autosys.logging_config import setup_logging

        monkeypatch.setenv("AUTOSYS_LOG_FORMAT", "text")
        monkeypatch.setenv("AUTOSYS_LOG_LEVEL", "DEBUG")
        # Should not raise
        setup_logging()

    def test_setup_logging_json_format(self, monkeypatch):
        from autosys.logging_config import setup_logging

        monkeypatch.setenv("AUTOSYS_LOG_FORMAT", "json")
        monkeypatch.setenv("AUTOSYS_LOG_LEVEL", "INFO")
        # Should not raise
        setup_logging()

    def test_json_serializer_produces_valid_json(self):
        from autosys.logging_config import _json_serializer
        import json as json_mod
        from datetime import datetime

        record = {
            "time": datetime(2025, 1, 1, 12, 0, 0),
            "level": MagicMock(name="INFO"),
            "message": "test message",
            "exception": None,
            "extra": {},
        }
        record["level"].name = "INFO"

        result = _json_serializer(record)
        parsed = json_mod.loads(result)
        assert parsed["message"] == "test message"
        assert parsed["level"] == "INFO"
        assert "timestamp" in parsed
        assert "correlation_id" in parsed


# ===========================================================================
# 3. Metrics Endpoint
# ===========================================================================

class TestMetricsEndpoint:

    def test_metrics_returns_200(self, client):
        resp = client.get("/api/v1/metrics")
        assert resp.status_code == 200
        assert "text/plain" in resp.headers.get("content-type", "")

    def test_metrics_contains_expected_metrics(self, client):
        resp = client.get("/api/v1/metrics")
        text = resp.text
        assert "autosys_jobs_total" in text
        assert "autosys_event_queue_depth" in text
        assert "autosys_events_processed_total" in text
        assert "autosys_agent_heartbeat_seconds" in text

    def test_metrics_has_prometheus_format(self, client):
        resp = client.get("/api/v1/metrics")
        text = resp.text
        # Prometheus format uses # HELP and # TYPE comments
        assert "# HELP" in text
        assert "# TYPE" in text

    def test_metrics_disabled_returns_404(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_METRICS_ENABLED", "false")
        from autosys.app_server.main import create_app
        app = create_app(start_eps=False)
        with TestClient(app) as c:
            resp = c.get("/api/v1/metrics")
            assert resp.status_code == 404

    def test_metrics_includes_job_status_counts(self, client):
        from autosys.db.schema import JobRow
        with sync_session() as session:
            session.add(JobRow(
                job_name="metrics_test_job",
                job_type="CMD",
                command="echo hi",
                machine="localhost",
                status=0,
            ))
            session.commit()

        resp = client.get("/api/v1/metrics")
        assert "autosys_jobs_total" in resp.text
        assert 'status="INACTIVE"' in resp.text


# ===========================================================================
# 4. Health Check Endpoints
# ===========================================================================

class TestHealthEndpoints:

    def test_health_live_returns_200(self, client):
        resp = client.get("/health/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_health_root_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"

    def test_health_ready_returns_200(self, client):
        resp = client.get("/health/ready")
        assert resp.status_code == 200
        body = resp.json()
        assert body["db"] == "connected"

    def test_health_ready_includes_eps_status(self, client):
        resp = client.get("/health/ready")
        body = resp.json()
        assert "details" in body
        assert "eps" in body["details"]

    def test_health_ready_returns_503_on_db_failure(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "sqlite:///nonexistent/path/test.db")
        reset_engines()
        from autosys.app_server.main import create_app
        app = create_app(start_eps=False)
        with TestClient(app, raise_server_exceptions=False) as c:
            resp = c.get("/health/ready")
            assert resp.status_code == 503


# ===========================================================================
# 5. Graceful Shutdown
# ===========================================================================

class TestGracefulShutdown:

    def test_stop_sets_running_false(self):
        from autosys.scheduler.event_processor import EventProcessor

        processor = EventProcessor(poll_interval=0.01)
        processor._running = True
        processor.stop()
        assert processor._running is False

    def test_run_forever_exits_on_stop(self):
        import asyncio
        from autosys.scheduler.event_processor import EventProcessor

        async def _test():
            processor = EventProcessor(poll_interval=0.05)

            async def stop_after_delay():
                await asyncio.sleep(0.15)
                processor.stop()

            stop_task = asyncio.create_task(stop_after_delay())
            await processor.run_forever(shutdown_timeout=1.0)
            await stop_task
            assert processor._running is False

        asyncio.run(_test())

    def test_run_forever_restores_signal_handlers(self):
        import asyncio
        import signal
        from autosys.scheduler.event_processor import EventProcessor

        original_sigterm = signal.getsignal(signal.SIGTERM)
        original_sigint = signal.getsignal(signal.SIGINT)

        async def _test():
            processor = EventProcessor(poll_interval=0.01)

            async def stop_immediately():
                await asyncio.sleep(0.05)
                processor.stop()

            stop_task = asyncio.create_task(stop_immediately())
            await processor.run_forever(shutdown_timeout=1.0)
            await stop_task

        asyncio.run(_test())

        assert signal.getsignal(signal.SIGTERM) == original_sigterm
        assert signal.getsignal(signal.SIGINT) == original_sigint

    def test_run_forever_handles_tick_errors(self):
        import asyncio
        from autosys.scheduler.event_processor import EventProcessor

        async def _test():
            processor = EventProcessor(poll_interval=0.01)

            call_count = 0
            original_tick = processor.process_one_tick

            def failing_tick(session):
                nonlocal call_count
                call_count += 1
                if call_count <= 2:
                    raise RuntimeError("simulated tick error")
                processor.stop()
                return 0

            processor.process_one_tick = failing_tick

            await processor.run_forever(shutdown_timeout=1.0)
            assert call_count >= 3  # errored twice, then stopped

        asyncio.run(_test())
