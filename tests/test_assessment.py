"""
Assessment API tests — Phase 1 migration-assessment export for Shinro.

TestComplexityModule   — unit tests for autosys.analysis.complexity
TestBoxTrace           — unit tests for autosys.analysis.box_trace.run_box_trace
TestAssessmentReportAPI — GET /api/v1/assessment/report
TestBoxTraceAPI        — POST /api/v1/assessment/boxes/{box}/trace
"""
from __future__ import annotations

from typing import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_assessment.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection as _conn
    _conn.reset_engines()
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn.reset_engines()


@pytest.fixture()
def ssa_client(isolated_db) -> Generator[TestClient, None, None]:
    from autosys.app_server.main import create_app
    with TestClient(create_app(start_eps=False), raise_server_exceptions=True) as c:
        yield c


def _seed_sequential_box(session, box_name="seq_box"):
    """box -> a -> b -> c, a strictly sequential 3-deep dependency chain."""
    from autosys.db.repository import jobs as job_repo
    from autosys.models.job import parse_job

    box = parse_job({"job_name": box_name, "job_type": "BOX"})
    a = parse_job({"job_name": "a", "job_type": "CMD", "command": "echo hi",
                   "machine": "m1", "box_name": box_name})
    b = parse_job({"job_name": "b", "job_type": "CMD", "command": "echo hi",
                   "machine": "m1", "box_name": box_name, "condition": "success(a)"})
    c = parse_job({"job_name": "c", "job_type": "CMD", "command": "echo hi",
                   "machine": "m1", "box_name": box_name, "condition": "success(b)"})
    for j in (box, a, b, c):
        job_repo.upsert(session, j)


def _seed_fanout_box(session, box_name="fan_box"):
    """box -> {p1, p2, p3}, three independent parallel children."""
    from autosys.db.repository import jobs as job_repo
    from autosys.models.job import parse_job

    box = parse_job({"job_name": box_name, "job_type": "BOX"})
    children = [
        parse_job({"job_name": f"p{i}", "job_type": "CMD", "command": "echo hi",
                   "machine": "m1", "box_name": box_name})
        for i in (1, 2, 3)
    ]
    job_repo.upsert(session, box)
    for c in children:
        job_repo.upsert(session, c)


def _seed_chain_box(session, box_name: str, depth: int):
    """box -> d1 -> d2 -> ... -> d{depth}, a strictly sequential chain."""
    from autosys.db.repository import jobs as job_repo
    from autosys.models.job import parse_job

    box = parse_job({"job_name": box_name, "job_type": "BOX"})
    job_repo.upsert(session, box)
    prev = None
    for i in range(1, depth + 1):
        name = f"d{i}"
        kwargs = {"job_name": name, "job_type": "CMD", "command": "echo hi",
                  "machine": "m1", "box_name": box_name}
        if prev is not None:
            kwargs["condition"] = f"success({prev})"
        job_repo.upsert(session, parse_job(kwargs))
        prev = name


def _seed_run(session, job_name: str, status: int, *, retry_count: int = 0, run_date: str = "2026-01-01"):
    """Insert a single JobRunRow directly (bypassing RunRepository's dispatch-time flow)."""
    import uuid
    from datetime import datetime
    from autosys.db.schema import JobRunRow

    session.add(JobRunRow(
        run_id=str(uuid.uuid4()), job_name=job_name, status=status,
        start_time=datetime.utcnow(), end_time=datetime.utcnow(),
        exit_code=0, machine="m1", retry_count=retry_count, run_date=run_date,
    ))


def _seed_alarm(session, job_name: str, *, cleared: bool):
    import uuid
    from datetime import datetime
    from autosys.db.schema import AlarmRow

    session.add(AlarmRow(
        alarm_id=str(uuid.uuid4()), job_name=job_name, alarm_type="JOB_FAILURE",
        message="failed", raised_at=datetime.utcnow(),
        cleared_at=datetime.utcnow() if cleared else None,
    ))


# ---------------------------------------------------------------------------
# Complexity module unit tests
# ---------------------------------------------------------------------------

class TestComplexityModule:

    def test_xs_job_no_dependencies(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.models.job import parse_job
        from autosys.analysis.complexity import build_report

        with sync_session() as session:
            job_repo.upsert(session, parse_job({
                "job_name": "simple_cmd", "job_type": "CMD",
                "command": "echo hi", "machine": "m1",
            }))
            session.commit()

        with sync_session() as session:
            rows = job_repo.list_all(session)
            results = build_report(rows)

        assert len(results) == 1
        assert results[0].size == "XS"
        # New-axis defaults when build_report is called without run_stats.
        assert results[0].risk == "NO_DATA"
        assert results[0].blast_radius == 0
        assert results[0].gap_tags == ""

    def test_deep_chain_elevates_to_l(self, isolated_db):
        """A 4-deep sequential chain gets flagged even with zero &/| operators."""
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.analysis.complexity import build_report

        with sync_session() as session:
            _seed_chain_box(session, "deep_box", depth=4)
            session.commit()

        with sync_session() as session:
            rows = job_repo.list_all(session)
            results = build_report(rows)

        by_name = {r.job_name: r for r in results}
        assert by_name["d4"].size == "L"
        assert "dependency chain depth=4" in by_name["d4"].drivers
        # The chain's own T-shirt effort is untouched by anything but structure.
        assert by_name["d1"].size in ("XS", "S")

    def test_very_deep_chain_elevates_to_xl(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.analysis.complexity import build_report

        with sync_session() as session:
            _seed_chain_box(session, "deep_box", depth=7)
            session.commit()

        with sync_session() as session:
            rows = job_repo.list_all(session)
            results = build_report(rows)

        by_name = {r.job_name: r for r in results}
        assert by_name["d7"].size == "XL"

    def test_blast_radius_counts_fan_in(self, isolated_db):
        """extract_sales-style hub: two children depend on the same upstream job."""
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.models.job import parse_job
        from autosys.analysis.complexity import build_report

        with sync_session() as session:
            job_repo.upsert(session, parse_job({
                "job_name": "hub", "job_type": "CMD", "command": "echo hi", "machine": "m1",
            }))
            job_repo.upsert(session, parse_job({
                "job_name": "dep1", "job_type": "CMD", "command": "echo hi", "machine": "m1",
                "condition": "success(hub)",
            }))
            job_repo.upsert(session, parse_job({
                "job_name": "dep2", "job_type": "CMD", "command": "echo hi", "machine": "m1",
                "condition": "success(hub)",
            }))
            session.commit()

        with sync_session() as session:
            rows = job_repo.list_all(session)
            results = build_report(rows)

        by_name = {r.job_name: r for r in results}
        assert by_name["hub"].blast_radius == 2
        assert by_name["dep1"].blast_radius == 0

    def test_gap_tags_and_risk_wired_into_summary(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.models.job import parse_job
        from autosys.analysis.complexity import build_report, compute_summary
        from autosys.analysis.operational_risk import fetch_run_stats
        from autosys.models.enums import JobStatus

        with sync_session() as session:
            job_repo.upsert(session, parse_job({
                "job_name": "watcher", "job_type": "FILEWATCH",
                "watch_file": "/tmp/x", "machine": "m1",
            }))
            for _ in range(5):
                _seed_run(session, "watcher", JobStatus.FAILURE.value)
            session.commit()

        with sync_session() as session:
            rows = job_repo.list_all(session)
            run_stats = fetch_run_stats(session, [r.job_name for r in rows])
            results = build_report(rows, run_stats=run_stats)
            summary = compute_summary(results)

        watcher = next(r for r in results if r.job_name == "watcher")
        assert watcher.gap_tags == "file-watcher"
        assert watcher.risk == "HIGH"
        assert summary.gap_severity_counts["GREEN"] == 1
        assert summary.risk_counts["HIGH"] == 1

    def test_ftp_job_scores_xl(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.models.job import parse_job
        from autosys.analysis.complexity import build_report

        with sync_session() as session:
            job_repo.upsert(session, parse_job({
                "job_name": "ftp_job", "job_type": "FTP",
                "ftp_server": "host", "ftp_user": "u", "ftp_type": "GET",
                "ftp_src": "/a", "ftp_dest": "/b",
            }))
            session.commit()

        with sync_session() as session:
            rows = job_repo.list_all(session)
            results = build_report(rows)

        assert results[0].size == "XL"

    def test_compute_summary_effort_overhead(self, isolated_db):
        from autosys.analysis.complexity import (
            JobAssessment, compute_summary, EFFORT_HOURS,
        )
        results = [
            JobAssessment("j1", "CMD", "", "XS", EFFORT_HOURS["XS"], ""),
            JobAssessment("j2", "CMD", "", "S", EFFORT_HOURS["S"], ""),
        ]
        summary = compute_summary(results)
        assert summary.raw_hours == EFFORT_HOURS["XS"] + EFFORT_HOURS["S"]
        assert summary.total_h == (
            summary.raw_hours
            + summary.platform_h + summary.testing_h + summary.pm_h + summary.training_h
        )


# ---------------------------------------------------------------------------
# box_trace unit tests
# ---------------------------------------------------------------------------

class TestBoxTrace:

    def test_sequential_chain_wave_count_3(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.analysis.box_trace import run_box_trace

        with sync_session() as session:
            _seed_sequential_box(session)
            session.commit()

        with sync_session() as session:
            trace = run_box_trace(session, "seq_box")
            session.rollback()

        assert trace.outcome == "SUCCESS"
        assert trace.wave_count == 3
        waves = {j.job_name: j.wave for j in trace.jobs}
        assert waves == {"a": 1, "b": 2, "c": 3}

    def test_fanout_box_wave_count_1(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.analysis.box_trace import run_box_trace

        with sync_session() as session:
            _seed_fanout_box(session)
            session.commit()

        with sync_session() as session:
            trace = run_box_trace(session, "fan_box")
            session.rollback()

        assert trace.outcome == "SUCCESS"
        assert trace.wave_count == 1
        assert all(j.wave == 1 for j in trace.jobs)

    def test_trace_does_not_mutate_db(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.analysis.box_trace import run_box_trace

        with sync_session() as session:
            _seed_sequential_box(session)
            session.commit()

        with sync_session() as session:
            run_box_trace(session, "seq_box")
            session.rollback()

        with sync_session() as session:
            row = job_repo.get_row(session, "seq_box")
            # Never activated for real — still whatever upsert() left it as.
            assert row.last_start is None

    def test_unknown_box_raises(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.analysis.box_trace import run_box_trace, BoxNotFoundError

        with sync_session() as session:
            with pytest.raises(BoxNotFoundError):
                run_box_trace(session, "does_not_exist")
            session.rollback()

    def test_non_box_raises(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.analysis.box_trace import run_box_trace, NotABoxError

        with sync_session() as session:
            _seed_sequential_box(session)
            session.commit()

        with sync_session() as session:
            with pytest.raises(NotABoxError):
                run_box_trace(session, "a")
            session.rollback()


# ---------------------------------------------------------------------------
# dependency_graph unit tests
# ---------------------------------------------------------------------------

class TestDependencyGraph:

    def test_fan_in_counts_on_sequential_chain(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.analysis.dependency_graph import fan_in_counts

        with sync_session() as session:
            _seed_sequential_box(session)
            session.commit()

        with sync_session() as session:
            rows = job_repo.list_all(session)
            all_by_name = {r.job_name: r for r in rows}
            counts = fan_in_counts(all_by_name)

        # a <- b <- c: a is referenced once (by b), b once (by c), c never.
        assert counts["a"] == 1
        assert counts["b"] == 1
        assert counts["c"] == 0
        assert counts["seq_box"] == 0

    def test_fan_in_counts_on_fanout_box(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.analysis.dependency_graph import fan_in_counts

        with sync_session() as session:
            _seed_fanout_box(session)
            session.commit()

        with sync_session() as session:
            rows = job_repo.list_all(session)
            all_by_name = {r.job_name: r for r in rows}
            counts = fan_in_counts(all_by_name)

        assert counts == {"fan_box": 0, "p1": 0, "p2": 0, "p3": 0}


# ---------------------------------------------------------------------------
# gap_analysis unit tests
# ---------------------------------------------------------------------------

class TestGapAnalysis:

    def test_filewatcher_tags_green(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.gap_analysis import compute_gap_tags, GAP_CATALOGUE

        row = JobRow(job_name="w", job_type="FILEWATCH", watch_file="/tmp/x")
        tags = compute_gap_tags(row)
        assert tags == ["file-watcher"]
        assert GAP_CATALOGUE["file-watcher"][0] == "GREEN"

    def test_ftp_tags_red(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.gap_analysis import compute_gap_tags, GAP_CATALOGUE

        row = JobRow(job_name="f", job_type="FTP")
        tags = compute_gap_tags(row)
        assert tags == ["ftp-db-jobtype"]
        assert GAP_CATALOGUE["ftp-db-jobtype"][0] == "RED"

    def test_cross_instance_condition_tags_red(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.gap_analysis import compute_gap_tags

        row = JobRow(job_name="j", job_type="CMD", condition="success(other^remote_instance)")
        assert "cross-instance-event" in compute_gap_tags(row)

    def test_business_globals_tag_green(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.gap_analysis import compute_gap_tags, GAP_CATALOGUE

        row = JobRow(job_name="j", job_type="CMD", command="run.sh %%ACCOUNT_ID%%")
        tags = compute_gap_tags(row)
        assert tags == ["global-variables"]
        assert GAP_CATALOGUE["global-variables"][0] == "GREEN"

    def test_calendar_and_sla_tag_yellow(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.gap_analysis import compute_gap_tags, GAP_CATALOGUE

        row = JobRow(job_name="j", job_type="CMD", run_calendar="us_holidays",
                     max_run_alarm=30)
        tags = compute_gap_tags(row)
        assert "complex-calendar" in tags and "sla-management" in tags
        assert GAP_CATALOGUE["complex-calendar"][0] == "YELLOW"
        assert GAP_CATALOGUE["sla-management"][0] == "YELLOW"

    def test_plain_cmd_no_tags(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.gap_analysis import compute_gap_tags

        row = JobRow(job_name="j", job_type="CMD", command="echo hi")
        assert compute_gap_tags(row) == []


# ---------------------------------------------------------------------------
# operational_risk unit tests
# ---------------------------------------------------------------------------

class TestOperationalRisk:

    def test_no_history_is_no_data(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.operational_risk import score_operational_risk

        row = JobRow(job_name="j", job_type="CMD", status=8)  # INACTIVE
        risk, drivers = score_operational_risk(row, None)
        assert risk == "NO_DATA"

    def test_high_failure_rate_is_high(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.operational_risk import score_operational_risk, RunStats

        row = JobRow(job_name="j", job_type="CMD", status=8)
        stats = RunStats(total_runs=10, failures=4, terminations=0, retried_runs=0,
                          active_alarms=0, cleared_alarms=0)
        risk, drivers = score_operational_risk(row, stats)
        assert risk == "HIGH"
        assert any("failure rate" in d for d in drivers)

    def test_on_hold_status_is_high_even_with_clean_history(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.operational_risk import score_operational_risk, RunStats
        from autosys.models.enums import JobStatus

        row = JobRow(job_name="j", job_type="CMD", status=JobStatus.ON_HOLD.value)
        stats = RunStats(total_runs=20, failures=0, terminations=0, retried_runs=0,
                          active_alarms=0, cleared_alarms=0)
        risk, drivers = score_operational_risk(row, stats)
        assert risk == "HIGH"
        assert any("ON_HOLD" in d for d in drivers)

    def test_medium_retry_rate_is_medium(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.operational_risk import score_operational_risk, RunStats

        row = JobRow(job_name="j", job_type="CMD", status=8)
        stats = RunStats(total_runs=10, failures=0, terminations=0, retried_runs=3,
                          active_alarms=0, cleared_alarms=0)
        risk, drivers = score_operational_risk(row, stats)
        assert risk == "MEDIUM"

    def test_clean_history_is_none(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.operational_risk import score_operational_risk, RunStats

        row = JobRow(job_name="j", job_type="CMD", status=8)
        stats = RunStats(total_runs=10, failures=0, terminations=0, retried_runs=0,
                          active_alarms=0, cleared_alarms=0)
        risk, drivers = score_operational_risk(row, stats)
        assert risk == "NONE"

    def test_past_retries_with_no_current_issues_is_low(self):
        from autosys.db.schema import JobRow
        from autosys.analysis.operational_risk import score_operational_risk, RunStats

        row = JobRow(job_name="j", job_type="CMD", status=8)
        # One retried run out of 10 — below the medium threshold (0.2) but not zero.
        stats = RunStats(total_runs=10, failures=0, terminations=0, retried_runs=1,
                          active_alarms=0, cleared_alarms=2)
        risk, drivers = score_operational_risk(row, stats)
        assert risk == "LOW"

    def test_fetch_run_stats_aggregates_runs_and_alarms(self, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.models.job import parse_job
        from autosys.analysis.operational_risk import fetch_run_stats
        from autosys.models.enums import JobStatus

        with sync_session() as session:
            job_repo.upsert(session, parse_job({
                "job_name": "flaky", "job_type": "CMD", "command": "echo hi", "machine": "m1",
            }))
            _seed_run(session, "flaky", JobStatus.FAILURE.value, retry_count=1)
            _seed_run(session, "flaky", JobStatus.FAILURE.value, retry_count=1)
            _seed_run(session, "flaky", JobStatus.SUCCESS.value)
            _seed_run(session, "flaky", JobStatus.TERMINATED.value)
            _seed_alarm(session, "flaky", cleared=False)
            _seed_alarm(session, "flaky", cleared=True)
            session.commit()

        with sync_session() as session:
            stats = fetch_run_stats(session, ["flaky", "no_history_job"])

        assert "no_history_job" not in stats
        s = stats["flaky"]
        assert s.total_runs == 4
        assert s.failures == 2
        assert s.terminations == 1
        assert s.retried_runs == 2
        assert s.active_alarms == 1
        assert s.cleared_alarms == 1


# ---------------------------------------------------------------------------
# REST API tests
# ---------------------------------------------------------------------------

class TestAssessmentReportAPI:

    def test_report_empty_db(self, ssa_client):
        r = ssa_client.get("/api/v1/assessment/report")
        assert r.status_code == 200
        data = r.json()
        assert data["job_count"] == 0
        assert data["jobs"] == []

    def test_report_matches_seeded_jobs(self, ssa_client, isolated_db):
        from autosys.db.connection import sync_session
        with sync_session() as session:
            _seed_sequential_box(session)
            session.commit()

        r = ssa_client.get("/api/v1/assessment/report")
        assert r.status_code == 200
        data = r.json()
        assert data["job_count"] == 4  # box + a + b + c
        names = {j["job_name"] for j in data["jobs"]}
        assert names == {"seq_box", "a", "b", "c"}
        assert data["summary"]["total_jobs"] == 4

    def test_report_box_filter(self, ssa_client, isolated_db):
        from autosys.db.connection import sync_session
        with sync_session() as session:
            _seed_sequential_box(session, "seq_box")
            _seed_fanout_box(session, "fan_box")
            session.commit()

        r = ssa_client.get("/api/v1/assessment/report", params={"box": "%seq%"})
        assert r.status_code == 200
        names = {j["job_name"] for j in r.json()["jobs"]}
        assert names == {"seq_box", "a", "b", "c"}

    def test_report_includes_risk_and_gap_fields(self, ssa_client, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.models.enums import JobStatus

        with sync_session() as session:
            _seed_sequential_box(session)
            for _ in range(5):
                _seed_run(session, "a", JobStatus.FAILURE.value)
            session.commit()

        r = ssa_client.get("/api/v1/assessment/report")
        assert r.status_code == 200
        data = r.json()

        job_a = next(j for j in data["jobs"] if j["job_name"] == "a")
        assert job_a["risk"] == "HIGH"
        assert "failure rate" in job_a["risk_drivers"]
        for field in ("risk", "risk_drivers", "blast_radius", "gap_tags"):
            assert field in job_a

        assert data["summary"]["risk_counts"]["HIGH"] == 2  # job "a" + seq_box (inherited)
        assert "gap_severity_counts" in data["summary"]


class TestBoxTraceAPI:

    def test_trace_sequential_box(self, ssa_client, isolated_db):
        from autosys.db.connection import sync_session
        with sync_session() as session:
            _seed_sequential_box(session)
            session.commit()

        r = ssa_client.post("/api/v1/assessment/boxes/seq_box/trace")
        assert r.status_code == 200
        data = r.json()
        assert data["outcome"] == "SUCCESS"
        assert data["wave_count"] == 3

    def test_trace_unknown_box_404(self, ssa_client):
        r = ssa_client.post("/api/v1/assessment/boxes/nope/trace")
        assert r.status_code == 404

    def test_trace_non_box_400(self, ssa_client, isolated_db):
        from autosys.db.connection import sync_session
        with sync_session() as session:
            _seed_sequential_box(session)
            session.commit()

        r = ssa_client.post("/api/v1/assessment/boxes/a/trace")
        assert r.status_code == 400

    def test_trace_is_idempotent_and_non_mutating(self, ssa_client, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.models.enums import JobStatus
        with sync_session() as session:
            _seed_sequential_box(session)
            session.commit()

        r1 = ssa_client.post("/api/v1/assessment/boxes/seq_box/trace")
        r2 = ssa_client.post("/api/v1/assessment/boxes/seq_box/trace")
        assert r1.json()["outcome"] == r2.json()["outcome"] == "SUCCESS"
        assert r1.json()["wave_count"] == r2.json()["wave_count"] == 3

        job = ssa_client.get("/api/v1/jobs/seq_box").json()
        assert job["status"] == JobStatus.INACTIVE.value
