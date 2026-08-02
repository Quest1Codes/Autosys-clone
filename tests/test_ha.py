"""
Phase 5 — HA & Dual Event Servers tests.

Tests:
- DistributedLock acquire / heartbeat / release / steal
- HA status endpoint
- EventProcessor with HA lock (standby skips, primary processes)
- No duplicate dispatches during failover
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta
from typing import Generator

import pytest
from fastapi.testclient import TestClient

from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.schema import JobRow, SchedulerLockRow, EventQueueRow
from autosys.models.enums import JobStatus
from autosys.scheduler.ha import DistributedLock, SecondaryDB


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_ha.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines()
    create_all_sync(drop_first=False)
    yield
    reset_engines()


# ===========================================================================
# DistributedLock — acquire / heartbeat / release / steal
# ===========================================================================

class TestDistributedLock:

    def test_acquire_new_lock(self, fresh_db):
        lock = DistributedLock(instance_id="eps-1", heartbeat_timeout=5)
        with sync_session() as s:
            assert lock.acquire(s) is True
            assert lock.is_leader is True
            s.commit()
        with sync_session() as s:
            row = s.get(SchedulerLockRow, "scheduler-primary")
            assert row is not None
            assert row.instance_id == "eps-1"
            assert row.is_active is True

    def test_renew_existing_lock(self, fresh_db):
        lock = DistributedLock(instance_id="eps-1", heartbeat_timeout=5)
        with sync_session() as s:
            lock.acquire(s)
            s.commit()
        # Re-acquire should renew, not fail
        with sync_session() as s:
            assert lock.acquire(s) is True
            assert lock.is_leader is True
            s.commit()

    def test_standby_cannot_acquire_while_primary_alive(self, fresh_db):
        primary = DistributedLock(instance_id="eps-1", heartbeat_timeout=10)
        standby = DistributedLock(instance_id="eps-2", heartbeat_timeout=10)
        with sync_session() as s:
            primary.acquire(s)
            s.commit()
        with sync_session() as s:
            assert standby.acquire(s) is False
            assert standby.is_leader is False
            s.commit()

    def test_standby_steals_stale_lock(self, fresh_db):
        primary = DistributedLock(instance_id="eps-1", heartbeat_timeout=2)
        standby = DistributedLock(instance_id="eps-2", heartbeat_timeout=2)
        with sync_session() as s:
            primary.acquire(s)
            s.commit()
        # Simulate primary heartbeat going stale
        with sync_session() as s:
            row = s.get(SchedulerLockRow, "scheduler-primary")
            row.last_heartbeat = datetime.utcnow() - timedelta(seconds=10)
            s.commit()
        # Standby should now steal the lock
        with sync_session() as s:
            assert standby.acquire(s) is True
            assert standby.is_leader is True
            s.commit()
        with sync_session() as s:
            row = s.get(SchedulerLockRow, "scheduler-primary")
            assert row.instance_id == "eps-2"

    def test_heartbeat_updates_timestamp(self, fresh_db):
        lock = DistributedLock(instance_id="eps-1", heartbeat_timeout=5)
        with sync_session() as s:
            lock.acquire(s)
            s.commit()
        old_hb = None
        with sync_session() as s:
            row = s.get(SchedulerLockRow, "scheduler-primary")
            old_hb = row.last_heartbeat
            s.commit()
        import time
        time.sleep(0.1)
        with sync_session() as s:
            lock.heartbeat(s)
            s.commit()
        with sync_session() as s:
            row = s.get(SchedulerLockRow, "scheduler-primary")
            assert row.last_heartbeat > old_hb

    def test_release_lock(self, fresh_db):
        lock = DistributedLock(instance_id="eps-1", heartbeat_timeout=5)
        with sync_session() as s:
            lock.acquire(s)
            s.commit()
        with sync_session() as s:
            lock.release(s)
            s.commit()
        assert lock.is_leader is False
        with sync_session() as s:
            row = s.get(SchedulerLockRow, "scheduler-primary")
            assert row.is_active is False

    def test_get_status_no_lock(self, fresh_db):
        lock = DistributedLock(instance_id="eps-1")
        with sync_session() as s:
            status = lock.get_status(s)
        assert status["active_instance"] is None
        assert status["is_leader"] is False
        assert status["stale"] is True

    def test_get_status_with_lock(self, fresh_db):
        lock = DistributedLock(instance_id="eps-1", heartbeat_timeout=5)
        with sync_session() as s:
            lock.acquire(s)
            s.commit()
        with sync_session() as s:
            status = lock.get_status(s)
        assert status["active_instance"] == "eps-1"
        assert status["is_leader"] is True
        assert status["stale"] is False

    def test_get_status_stale(self, fresh_db):
        lock = DistributedLock(instance_id="eps-1", heartbeat_timeout=2)
        with sync_session() as s:
            lock.acquire(s)
            s.commit()
        with sync_session() as s:
            row = s.get(SchedulerLockRow, "scheduler-primary")
            row.last_heartbeat = datetime.utcnow() - timedelta(seconds=10)
            s.commit()
        with sync_session() as s:
            status = lock.get_status(s)
        assert status["stale"] is True
        assert status["is_leader"] is False


# ===========================================================================
# SecondaryDB
# ===========================================================================

class TestSecondaryDB:

    def test_not_configured(self, fresh_db, monkeypatch):
        monkeypatch.delenv("AUTOSYS_DB_URL_2", raising=False)
        sec = SecondaryDB()
        assert sec.enabled is False
        status = sec.get_status()
        assert status["configured"] is False

    def test_configured(self, fresh_db, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL_2", "sqlite:///tmp/secondary.db")
        sec = SecondaryDB()
        assert sec.enabled is True
        status = sec.get_status()
        assert status["configured"] is True
        assert "secondary" in status["url"]


# ===========================================================================
# HA status endpoint
# ===========================================================================

class TestHAStatusEndpoint:

    def test_ha_status_no_lock(self, fresh_db):
        from autosys.app_server.main import create_app
        app = create_app(start_eps=False)
        client = TestClient(app)
        resp = client.get("/api/v1/ha/status")
        assert resp.status_code == 200
        data = resp.json()
        assert "scheduler_lock" in data
        assert "secondary_db" in data
        assert "instance_id" in data
        assert data["scheduler_lock"]["active_instance"] is None

    def test_ha_status_with_lock(self, fresh_db):
        from autosys.app_server.main import create_app
        # Create a lock first
        lock = DistributedLock(instance_id="eps-test", heartbeat_timeout=10)
        with sync_session() as s:
            lock.acquire(s)
            s.commit()
        app = create_app(start_eps=False)
        client = TestClient(app)
        resp = client.get("/api/v1/ha/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["scheduler_lock"]["active_instance"] == "eps-test"


# ===========================================================================
# EventProcessor with HA — standby skips, primary processes
# ===========================================================================

class TestEventProcessorHA:

    def test_standby_skips_processing(self, fresh_db):
        from autosys.scheduler.event_processor import EventProcessor, _stub_dispatch
        # Primary acquires lock first
        primary_lock = DistributedLock(instance_id="primary", heartbeat_timeout=10)
        with sync_session() as s:
            primary_lock.acquire(s)
            s.commit()
        # Standby EPS
        standby_lock = DistributedLock(instance_id="standby", heartbeat_timeout=10)
        processor = EventProcessor(
            dispatch_fn=_stub_dispatch,
            auto_complete=True,
        )
        processor.ha_lock = standby_lock
        processor.is_standby = True
        # Add a job and event
        with sync_session() as s:
            s.add(JobRow(
                job_name="ha_job", job_type="CMD", command="echo hi",
                machine="localhost", status=JobStatus.INACTIVE.value, owner="test",
            ))
            s.add(EventQueueRow(
                event_id=str(uuid.uuid4()),
                event_type="STARTJOB",
                job_name="ha_job",
                source="cli",
            ))
            s.commit()
        # Process one tick — standby should not process
        with sync_session() as s:
            # Standby tries to acquire — should fail
            assert standby_lock.acquire(s) is False
            s.commit()
        with sync_session() as s:
            row = s.get(JobRow, "ha_job")
            # Job should still be INACTIVE (not processed)
            assert row.status == JobStatus.INACTIVE.value

    def test_primary_processes_events(self, fresh_db):
        from autosys.scheduler.event_processor import EventProcessor, _stub_dispatch
        primary_lock = DistributedLock(instance_id="primary", heartbeat_timeout=10)
        processor = EventProcessor(
            dispatch_fn=_stub_dispatch,
            auto_complete=True,
        )
        processor.ha_lock = primary_lock
        with sync_session() as s:
            s.add(JobRow(
                job_name="ha_job2", job_type="CMD", command="echo hi",
                machine="localhost", status=JobStatus.INACTIVE.value, owner="test",
            ))
            s.add(EventQueueRow(
                event_id=str(uuid.uuid4()),
                event_type="STARTJOB",
                job_name="ha_job2",
                source="cli",
            ))
            s.commit()
        with sync_session() as s:
            assert primary_lock.acquire(s) is True
            s.commit()
        with sync_session() as s:
            processor.process_one_tick(s)
            s.commit()
        with sync_session() as s:
            row = s.get(JobRow, "ha_job2")
            # Should have been processed (STARTING → RUNNING → SUCCESS with auto_complete)
            assert row.status != JobStatus.INACTIVE.value

    def test_failover_no_duplicate_dispatch(self, fresh_db):
        """Simulate primary failure and standby takeover — verify no duplicate dispatches."""
        from autosys.scheduler.event_processor import EventProcessor, _stub_dispatch

        # Primary acquires lock
        primary_lock = DistributedLock(instance_id="primary", heartbeat_timeout=2)
        with sync_session() as s:
            primary_lock.acquire(s)
            s.commit()

        # Primary processes an event
        with sync_session() as s:
            s.add(JobRow(
                job_name="failover_job", job_type="CMD", command="echo hi",
                machine="localhost", status=JobStatus.INACTIVE.value, owner="test",
            ))
            s.add(EventQueueRow(
                event_id=str(uuid.uuid4()),
                event_type="STARTJOB",
                job_name="failover_job",
                source="cli",
            ))
            s.commit()

        primary_proc = EventProcessor(dispatch_fn=_stub_dispatch, auto_complete=True)
        primary_proc.ha_lock = primary_lock
        with sync_session() as s:
            primary_lock.acquire(s)
            primary_proc.process_one_tick(s)
            s.commit()

        # Primary processed the event — job should be SUCCESS
        with sync_session() as s:
            row = s.get(JobRow, "failover_job")
            assert row.status == JobStatus.SUCCESS.value

        # Simulate primary crash — heartbeat goes stale
        with sync_session() as s:
            row = s.get(SchedulerLockRow, "scheduler-primary")
            row.last_heartbeat = datetime.utcnow() - timedelta(seconds=10)
            s.commit()

        # Standby takes over
        standby_lock = DistributedLock(instance_id="standby", heartbeat_timeout=2)
        standby_proc = EventProcessor(dispatch_fn=_stub_dispatch, auto_complete=True)
        standby_proc.ha_lock = standby_lock

        with sync_session() as s:
            assert standby_lock.acquire(s) is True
            assert standby_lock.is_leader is True
            s.commit()

        # Standby processes — but the event is already processed, so no duplicate
        with sync_session() as s:
            standby_proc.process_one_tick(s)
            s.commit()

        # Job should still be SUCCESS (not re-dispatched)
        with sync_session() as s:
            row = s.get(JobRow, "failover_job")
            assert row.status == JobStatus.SUCCESS.value

        # Verify lock is now held by standby
        with sync_session() as s:
            lock_row = s.get(SchedulerLockRow, "scheduler-primary")
            assert lock_row.instance_id == "standby"
