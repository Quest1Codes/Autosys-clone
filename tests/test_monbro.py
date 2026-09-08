"""
Phase 6 — Monitor/Report (monbro) tests.

Tests:
- MonitorEvaluator for FILE_MONITOR, DISK_MONITOR, LOG_MONITOR, TEXT_MONITOR
- Report generation (JOB_REPORT, ALARM_REPORT)
- MonitorRepository CRUD
"""

from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timedelta
from typing import Generator

import pytest

from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.schema import (
    JobRow, MonitorRow, AlarmRow, EventQueueRow, JobRunRow, ReportRow,
)
from autosys.db.repository import jobs as job_repo, monitors as mon_repo
from autosys.models.enums import JobStatus
from autosys.engine.monitor_evaluator import (
    MonitorEvaluator, generate_job_report, generate_alarm_report,
)


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_monbro.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines()
    create_all_sync(drop_first=False)
    yield
    reset_engines()


def _make_job(session, name, **kwargs):
    row = JobRow(
        job_name=name,
        job_type=kwargs.get("job_type", "CMD"),
        command=kwargs.get("command", "echo hi"),
        machine=kwargs.get("machine", "localhost"),
        status=kwargs.get("status", JobStatus.INACTIVE.value),
        owner=kwargs.get("owner", "test"),
    )
    session.add(row)
    session.flush()
    return row


# ===========================================================================
# MonitorRepository CRUD
# ===========================================================================

class TestMonitorRepository:

    def test_upsert_insert(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "job1")
            s.commit()
        with sync_session() as s:
            result = mon_repo.upsert(s, "watch_file1", "FILE_MONITOR",
                                     job_name="job1", attributes_json='{"path": "/tmp/test"}')
            s.commit()
        assert result == "inserted"
        with sync_session() as s:
            row = mon_repo.get(s, "watch_file1")
            assert row is not None
            assert row.monbro_type == "FILE_MONITOR"
            assert row.job_name == "job1"

    def test_upsert_update(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "job1")
            _make_job(s, "job2")
            s.commit()
        with sync_session() as s:
            mon_repo.upsert(s, "watch_file2", "FILE_MONITOR",
                            job_name="job1", attributes_json='{"path": "/tmp/a"}')
            s.commit()
        with sync_session() as s:
            result = mon_repo.upsert(s, "watch_file2", "LOG_MONITOR",
                                     job_name="job2", attributes_json='{"path": "/tmp/b"}')
            s.commit()
        assert result == "updated"
        with sync_session() as s:
            row = mon_repo.get(s, "watch_file2")
            assert row.monbro_type == "LOG_MONITOR"
            assert row.job_name == "job2"

    def test_delete(self, fresh_db):
        with sync_session() as s:
            mon_repo.upsert(s, "to_delete", "FILE_MONITOR")
            s.commit()
        with sync_session() as s:
            assert mon_repo.delete(s, "to_delete") is True
            s.commit()
        with sync_session() as s:
            assert mon_repo.get(s, "to_delete") is None

    def test_delete_not_found(self, fresh_db):
        with sync_session() as s:
            assert mon_repo.delete(s, "nonexistent") is False

    def test_list_all(self, fresh_db):
        with sync_session() as s:
            mon_repo.upsert(s, "mon_b", "FILE_MONITOR")
            mon_repo.upsert(s, "mon_a", "CPU_MONITOR")
            s.commit()
        with sync_session() as s:
            rows = mon_repo.list_all(s)
            assert len(rows) == 2
            assert rows[0].monbro_name == "mon_a"  # ordered by name


# ===========================================================================
# MonitorEvaluator — FILE_MONITOR
# ===========================================================================

class TestFileMonitor:

    def test_file_not_found_no_event(self, fresh_db, tmp_path):
        with sync_session() as s:
            _make_job(s, "file_job")
            s.add(MonitorRow(
                monbro_name="watch1", monbro_type="FILE_MONITOR",
                job_name="file_job",
                attributes_json=json.dumps({"path": str(tmp_path / "nonexistent.txt")}),
            ))
            s.commit()
            evaluator = MonitorEvaluator()
            alarms, events = evaluator.tick(s)
            s.commit()
        assert alarms == 0
        assert events == 0

    def test_file_found_raises_event_and_alarm(self, fresh_db, tmp_path):
        trigger_file = tmp_path / "trigger.txt"
        trigger_file.write_text("hello")
        with sync_session() as s:
            _make_job(s, "file_job2")
            s.add(MonitorRow(
                monbro_name="watch2", monbro_type="FILE_MONITOR",
                job_name="file_job2",
                attributes_json=json.dumps({"path": str(trigger_file)}),
            ))
            s.commit()
            evaluator = MonitorEvaluator()
            alarms, events = evaluator.tick(s)
            s.commit()
        assert alarms == 1
        assert events == 1
        # Verify alarm was raised
        with sync_session() as s:
            alarm_rows = list(s.scalars(
                s.query(AlarmRow).statement.where(AlarmRow.job_name == "file_job2")
                if hasattr(s.query(AlarmRow).statement, 'where')
                else __import__("sqlalchemy").select(AlarmRow).where(AlarmRow.job_name == "file_job2")
            ))
            assert len(alarm_rows) >= 1
        # Verify event was enqueued
        with sync_session() as s:
            event_rows = list(s.scalars(
                __import__("sqlalchemy").select(EventQueueRow).where(
                    EventQueueRow.job_name == "file_job2",
                    EventQueueRow.event_type == "STARTJOB",
                )
            ))
            assert len(event_rows) >= 1


# ===========================================================================
# MonitorEvaluator — DISK_MONITOR
# ===========================================================================

class TestDiskMonitor:

    def test_disk_space_ok(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "disk_job_ok")
            s.add(MonitorRow(
                monbro_name="disk1", monbro_type="DISK_MONITOR",
                job_name="disk_job_ok",
                attributes_json=json.dumps({"path": "/", "threshold_gb": 0}),
            ))
            s.commit()
            evaluator = MonitorEvaluator()
            alarms, events = evaluator.tick(s)
            s.commit()
        # With threshold 0, free space should always be above
        assert alarms == 0

    def test_disk_space_low(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "disk_job_low")
            s.add(MonitorRow(
                monbro_name="disk2", monbro_type="DISK_MONITOR",
                job_name="disk_job_low",
                attributes_json=json.dumps({"path": "/", "threshold_gb": 999999}),
            ))
            s.commit()
            evaluator = MonitorEvaluator()
            alarms, events = evaluator.tick(s)
            s.commit()
        # With huge threshold, free space should be below
        assert alarms == 1


# ===========================================================================
# MonitorEvaluator — LOG_MONITOR and TEXT_MONITOR
# ===========================================================================

class TestLogTextMonitor:

    def test_log_monitor_pattern_found(self, fresh_db, tmp_path):
        log_file = tmp_path / "app.log"
        log_file.write_text("INFO: starting\nERROR: something went wrong\n")
        with sync_session() as s:
            _make_job(s, "log_job1")
            s.add(MonitorRow(
                monbro_name="log1", monbro_type="LOG_MONITOR",
                job_name="log_job1",
                attributes_json=json.dumps({"path": str(log_file), "pattern": "ERROR"}),
            ))
            s.commit()
            evaluator = MonitorEvaluator()
            alarms, events = evaluator.tick(s)
            s.commit()
        assert alarms == 1

    def test_log_monitor_pattern_not_found(self, fresh_db, tmp_path):
        log_file = tmp_path / "clean.log"
        log_file.write_text("INFO: all good\n")
        with sync_session() as s:
            _make_job(s, "log_job2")
            s.add(MonitorRow(
                monbro_name="log2", monbro_type="LOG_MONITOR",
                job_name="log_job2",
                attributes_json=json.dumps({"path": str(log_file), "pattern": "ERROR"}),
            ))
            s.commit()
            evaluator = MonitorEvaluator()
            alarms, events = evaluator.tick(s)
            s.commit()
        assert alarms == 0

    def test_text_monitor_content_found(self, fresh_db, tmp_path):
        text_file = tmp_path / "data.txt"
        text_file.write_text("the quick brown fox\n")
        with sync_session() as s:
            _make_job(s, "text_job1")
            s.add(MonitorRow(
                monbro_name="text1", monbro_type="TEXT_MONITOR",
                job_name="text_job1",
                attributes_json=json.dumps({"path": str(text_file), "content": "quick"}),
            ))
            s.commit()
            evaluator = MonitorEvaluator()
            alarms, events = evaluator.tick(s)
            s.commit()
        assert alarms == 1

    def test_text_monitor_content_not_found(self, fresh_db, tmp_path):
        text_file = tmp_path / "data2.txt"
        text_file.write_text("nothing here\n")
        with sync_session() as s:
            _make_job(s, "text_job2")
            s.add(MonitorRow(
                monbro_name="text2", monbro_type="TEXT_MONITOR",
                job_name="text_job2",
                attributes_json=json.dumps({"path": str(text_file), "content": "missing"}),
            ))
            s.commit()
            evaluator = MonitorEvaluator()
            alarms, events = evaluator.tick(s)
            s.commit()
        assert alarms == 0

    def test_unknown_monitor_type_skipped(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "unknown_job")
            s.add(MonitorRow(
                monbro_name="unknown1", monbro_type="UNKNOWN_TYPE",
                job_name="unknown_job",
                attributes_json="{}",
            ))
            s.commit()
            evaluator = MonitorEvaluator()
            alarms, events = evaluator.tick(s)
            s.commit()
        assert alarms == 0
        assert events == 0

    def test_invalid_json_attributes_skipped(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "bad_json_job")
            s.add(MonitorRow(
                monbro_name="bad_json", monbro_type="FILE_MONITOR",
                job_name="bad_json_job",
                attributes_json="not valid json",
            ))
            s.commit()
            evaluator = MonitorEvaluator()
            alarms, events = evaluator.tick(s)
            s.commit()
        assert alarms == 0
        assert events == 0

    def test_no_monitors(self, fresh_db):
        with sync_session() as s:
            evaluator = MonitorEvaluator()
            alarms, events = evaluator.tick(s)
            s.commit()
        assert alarms == 0
        assert events == 0


# ===========================================================================
# Report generation
# ===========================================================================

class TestReportGeneration:

    def test_job_report_empty(self, fresh_db):
        now = datetime.utcnow()
        with sync_session() as s:
            report = generate_job_report(s, now - timedelta(days=1), now)
        assert report["report_type"] == "JOB_REPORT"
        assert report["total_runs"] == 0
        assert report["by_status"] == {}

    def test_job_report_with_runs(self, fresh_db):
        now = datetime.utcnow()
        with sync_session() as s:
            _make_job(s, "rpt_job")
            s.add(JobRunRow(
                run_id=1, job_name="rpt_job", status="SUCCESS",
                start_time=now - timedelta(hours=1),
                end_time=now,
            ))
            s.add(JobRunRow(
                run_id=2, job_name="rpt_job", status="FAILURE",
                start_time=now - timedelta(hours=2),
                end_time=now - timedelta(hours=1),
            ))
            s.commit()
            report = generate_job_report(s, now - timedelta(days=1), now)
        assert report["total_runs"] == 2
        assert report["by_status"].get("SUCCESS") == 1
        assert report["by_status"].get("FAILURE") == 1

    def test_alarm_report_empty(self, fresh_db):
        now = datetime.utcnow()
        with sync_session() as s:
            report = generate_alarm_report(s, now - timedelta(days=1), now)
        assert report["report_type"] == "ALARM_REPORT"
        assert report["total_alarms"] == 0

    def test_alarm_report_with_alarms(self, fresh_db):
        now = datetime.utcnow()
        with sync_session() as s:
            _make_job(s, "a_job")
            _make_job(s, "b_job")
            s.add(AlarmRow(
                alarm_id=str(uuid.uuid4()),
                job_name="a_job", alarm_type="FAILURE",
                message="test", raised_at=now,
            ))
            s.add(AlarmRow(
                alarm_id=str(uuid.uuid4()),
                job_name="b_job", alarm_type="FAILURE",
                message="test2", raised_at=now,
                cleared_at=now,
            ))
            s.commit()
            report = generate_alarm_report(s, now - timedelta(days=1), now)
        assert report["total_alarms"] == 2
        assert report["cleared"] == 1
        assert report["by_type"].get("FAILURE") == 2
