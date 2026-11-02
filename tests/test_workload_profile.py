"""
Workload profile measured from run history (analysis/workload_profile.py).

Covers the evidence rules (imported history preferred over live, simulated
history never used), each measured figure, scoping to the jobs a report
covers, and that GET /api/v1/assessment/report carries the block.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from autosys.models.enums import JobStatus


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'wl.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    connection.reset_engines()
    create_all_sync()
    yield
    connection.reset_engines()


_seq = iter(range(1_000_000))


def _jobs(*names, job_type="CMD"):
    from autosys.db.connection import sync_session
    from autosys.db.schema import JobRow
    with sync_session() as s:
        for n in names:
            s.add(JobRow(job_name=n, job_type=job_type))
        s.commit()


def _run(job, start, minutes, source="imported"):
    from autosys.db.connection import sync_session
    from autosys.db.schema import JobRunRow
    end = start + timedelta(minutes=minutes) if minutes is not None else None
    with sync_session() as s:
        s.add(JobRunRow(
            run_id=f"r{next(_seq)}", job_name=job, status=JobStatus.SUCCESS.value,
            start_time=start, end_time=end, source=source, retry_count=0,
            run_date=start.strftime("%Y-%m-%d"),
            duration_secs=minutes * 60 if minutes is not None else None))
        s.commit()


def _profile(job_names=None):
    from autosys.analysis.workload_profile import compute_workload_profile
    from autosys.db.connection import sync_session
    with sync_session() as s:
        return compute_workload_profile(s, job_names)


D0 = datetime(2026, 3, 2, 2, 0)


def test_no_history_says_what_is_needed(db):
    _jobs("a")
    p = _profile()
    assert p.source is None
    assert "run-history export" in p.reason
    assert p.runs_per_job_per_day is None


def test_simulated_history_is_never_measured(db):
    _jobs("a")
    _run("a", D0, 5, source="simulated")
    assert _profile().source is None


def test_imported_history_wins_over_live(db):
    _jobs("a")
    _run("a", D0, 5, source="live")
    _run("a", D0 + timedelta(days=1), 30, source="imported")
    p = _profile()
    assert p.source == "imported"
    assert p.avg_task_minutes == 30


def test_live_history_is_used_when_nothing_was_imported(db):
    _jobs("a")
    _run("a", D0, 5, source="live")
    assert _profile().source == "live"


def test_a_dry_run_instance_can_exclude_its_own_runs(db):
    from autosys.analysis.workload_profile import compute_workload_profile
    from autosys.db.connection import sync_session
    _jobs("a")
    _run("a", D0, 5, source="live")
    with sync_session() as s:
        assert compute_workload_profile(s, None, sources=("imported",)).source is None


def test_runs_per_day_uses_distinct_days_not_first_to_last_span(db):
    _jobs("a", "b")
    for day in range(7):
        _run("a", D0 + timedelta(days=day), 10)
        _run("b", D0 + timedelta(days=day, hours=12), 20)
    p = _profile()
    assert p.days_observed == 7
    assert p.runs_per_job_per_day == 1.0     # 14 runs / 2 jobs / 7 days
    assert p.avg_task_minutes == 15.0
    assert p.sufficient_window is True


def test_peak_hour_is_the_busiest_start_hour(db):
    _jobs("a", "b", "c")
    _run("a", D0, 5)                          # 02:00
    _run("b", D0 + timedelta(minutes=30), 5)  # 02:30
    _run("c", D0 + timedelta(hours=9), 5)     # 11:00
    p = _profile()
    assert p.peak_hour == 2
    assert p.peak_hour_share == pytest.approx(2 / 3, abs=1e-4)


def test_peak_concurrency_counts_overlaps_only(db):
    _jobs("a", "b", "c", "d")
    _run("a", D0, 60)                              # 02:00-03:00
    _run("b", D0 + timedelta(minutes=10), 30)      # 02:10-02:40  overlaps a
    _run("c", D0 + timedelta(minutes=20), 5)       # 02:20-02:25  overlaps a, b -> 3
    _run("d", D0 + timedelta(minutes=60), 10)      # starts as a ends: not an overlap
    p = _profile()
    assert p.peak_concurrent_runs == 3
    assert p.peak_concurrency_at == D0 + timedelta(minutes=20)


def test_runs_without_an_end_time_are_left_out_of_concurrency(db):
    _jobs("a", "b")
    _run("a", D0, 60)
    _run("b", D0 + timedelta(minutes=5), None)
    p = _profile()
    assert p.total_runs == 2
    assert p.peak_concurrent_runs == 1


def test_scoped_to_the_jobs_the_report_covers(db):
    _jobs("in_scope", "elsewhere")
    _run("in_scope", D0, 10)
    _run("elsewhere", D0, 50)
    p = _profile(["in_scope"])
    assert p.jobs_with_history == 1
    assert p.avg_task_minutes == 10


def test_report_carries_the_workload_block(db):
    from autosys.app_server.main import create_app
    _jobs("a")
    _run("a", D0, 12)
    # A file watcher's run is a wait (hours), not work: it must not move the
    # task duration or concurrency the report hands to platform sizing.
    _jobs("watcher", job_type="FILEWATCH")
    _run("watcher", D0, 600)
    with TestClient(create_app(start_eps=False)) as client:
        body = client.get("/api/v1/assessment/report", params={"simulate": False}).json()
    assert body["workload"]["source"] == "imported"
    assert body["workload"]["avg_task_minutes"] == 12
