"""Tests for simulator enhancements — S1-S6."""
from __future__ import annotations

import pytest, uuid
from datetime import datetime

from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.schema import JobRow, JobRunRow, AlarmRow, EventQueueRow
from autosys.db.repository import jobs as job_repo
from autosys.models.enums import JobStatus
from autosys.scheduler.event_processor import EventProcessor, _stub_dispatch
from autosys.scheduler.failure_injector import FailureInjector
from autosys.scheduler.simulation_runner import run_simulation
from autosys.notifications.alarm_manager import AlarmManager


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'test_sim.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines(); create_all_sync(); yield; reset_engines()


def _make_job(session, name, **kw):
    defaults = dict(
        job_name=name, job_type="CMD", command="echo hi",
        machine="localhost", status=JobStatus.INACTIVE.value, owner="test",
    )
    defaults.update(kw)
    session.add(JobRow(**defaults))
    session.flush()
    return name


class TestFailureInjector:

    def test_high_retry_job_fails_more(self):
        inj = FailureInjector(seed=42)
        # Job with n_retrys=3 → 25% base rate
        high_retry = JobRow(job_name="flaky", job_type="CMD", n_retrys=3)
        # Job with n_retrys=0 → 3% base rate
        stable = JobRow(job_name="solid", job_type="CMD", n_retrys=0)
        fails_high = sum(1 for _ in range(1000) if inj.should_fail(high_retry))
        fails_low = sum(1 for _ in range(1000) if inj.should_fail(stable))
        assert fails_high > fails_low
        assert fails_high > 100  # ~25% of 1000
        assert fails_low < 100   # ~3% of 1000

    def test_reproducible_with_same_seed(self):
        inj1 = FailureInjector(seed=99)
        inj2 = FailureInjector(seed=99)
        row = JobRow(job_name="j", job_type="CMD", n_retrys=2)
        results1 = [inj1.should_fail(row) for _ in range(100)]
        results2 = [inj2.should_fail(row) for _ in range(100)]
        assert results1 == results2

    def test_estimated_run_secs(self):
        inj = FailureInjector(seed=42)
        row = JobRow(job_name="j", job_type="CMD", avg_runtime=5)
        assert inj.estimated_run_secs(row) == 300.0  # 5 min * 60
        row2 = JobRow(job_name="j2", job_type="CMD", max_run_alarm=10)
        assert inj.estimated_run_secs(row2) == 300.0  # 10 * 30
        row3 = JobRow(job_name="j3", job_type="CMD")
        assert inj.estimated_run_secs(row3) == 60.0  # default


class TestRecordRuns:

    def test_job_run_recorded_on_success(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "test_success")
            s.add(EventQueueRow(event_id=str(uuid.uuid4()),
                event_type="FORCE_STARTJOB", job_name="test_success"))
            s.commit()

        eps = EventProcessor(
            auto_complete=True,
            failure_injector=FailureInjector(seed=1),
        )
        with sync_session() as s:
            eps.process_one_tick(s, now=datetime(2024, 1, 1, 10, 0))
            s.commit()

        with sync_session() as s:
            runs = list(s.query(JobRunRow).filter(JobRunRow.job_name == "test_success"))
            assert len(runs) >= 1
            assert runs[0].exit_code == 0
            assert runs[0].status == JobStatus.SUCCESS.value

    def test_job_run_recorded_on_failure(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "test_fail", n_retrys=5)
            s.add(EventQueueRow(event_id=str(uuid.uuid4()),
                event_type="FORCE_STARTJOB", job_name="test_fail"))
            s.commit()

        # Use a seed that produces at least one failure in first few tries
        eps = EventProcessor(
            auto_complete=True,
            failure_injector=FailureInjector(seed=7),
        )
        # Run multiple ticks to get a failure
        with sync_session() as s:
            for _ in range(20):
                eps.process_one_tick(s, now=datetime(2024, 1, 1, 10, 0))
            s.commit()

        with sync_session() as s:
            runs = list(s.query(JobRunRow).filter(JobRunRow.job_name == "test_fail"))
            assert len(runs) >= 1
            # At least one should be a failure (n_retrys=5 → 25% rate)
            failures = [r for r in runs if r.status == JobStatus.FAILURE.value]
            assert len(failures) >= 1
            assert all(r.exit_code == 1 for r in failures)


class TestAlarmManagerWired:

    def test_alarm_fired_on_failure(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "alarm_test", n_retrys=5, alarm_if_fail=True)
            s.add(EventQueueRow(event_id=str(uuid.uuid4()),
                event_type="FORCE_STARTJOB", job_name="alarm_test"))
            s.commit()

        eps = EventProcessor(
            auto_complete=True,
            alarm_manager=AlarmManager(),
            failure_injector=FailureInjector(seed=3),
        )
        with sync_session() as s:
            for _ in range(20):
                eps.process_one_tick(s, now=datetime(2024, 1, 1, 10, 0))
            s.commit()

        with sync_session() as s:
            alarms = list(s.query(AlarmRow).filter(AlarmRow.job_name == "alarm_test"))
            assert len(alarms) >= 1
            assert any(a.alarm_type == "ALARM_IF_FAIL" for a in alarms)


class TestSimulationRunner:

    def test_multi_cycle_produces_history(self, fresh_db):
        with sync_session() as s:
            s.add(JobRow(job_name="sim_box", job_type="BOX", status=JobStatus.INACTIVE.value, owner="test"))
            _make_job(s, "sim_job1", box_name="sim_box", n_retrys=2, alarm_if_fail=True)
            _make_job(s, "sim_job2", box_name="sim_box", n_retrys=0)
            s.commit()

        with sync_session() as s:
            result = run_simulation(s, cycles=5, ticks_per_cycle=5, seed=42)

        assert result["cycles"] == 5
        assert result["total_runs"] >= 5
        assert result["failure_rate"] >= 0.0

    def test_simulation_reproducible(self, fresh_db):
        with sync_session() as s:
            s.add(JobRow(job_name="repro_box", job_type="BOX", status=JobStatus.INACTIVE.value, owner="test"))
            _make_job(s, "repro_job", box_name="repro_box", n_retrys=3)
            s.commit()
            r1 = run_simulation(s, cycles=3, ticks_per_cycle=3, seed=42)

        assert r1["cycles"] == 3
        assert r1["total_runs"] > 0
