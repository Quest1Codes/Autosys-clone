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


@pytest.fixture(autouse=True)
def fresh_sim_cache():
    """The simulated-risk cache is process-global; isolate it per test."""
    from autosys.analysis import simulated_risk
    simulated_risk.reset_cache()
    yield
    simulated_risk.reset_cache()


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

    def test_retries_and_timeout_are_not_complexity(self, isolated_db):
        """
        n_retrys -> retries=N and term_run_time -> execution_timeout are
        one-line keyword arguments in a DAG, so neither may lift a job out of
        its structural tier. Scoring them as M put 163 of the reference
        estate's 227 M-tier jobs there on those two attributes alone.
        """
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.models.job import parse_job
        from autosys.analysis.complexity import build_report

        with sync_session() as session:
            job_repo.upsert(session, parse_job({
                "job_name": "retry_only", "job_type": "CMD", "command": "echo hi",
                "machine": "m1", "n_retrys": 2, "term_run_time": 35,
            }))
            job_repo.upsert(session, parse_job({
                "job_name": "many_retries", "job_type": "CMD", "command": "echo hi",
                "machine": "m1", "n_retrys": 9,
            }))
            session.commit()

        with sync_session() as session:
            results = build_report(job_repo.list_all(session))

        by_name = {r.job_name: r for r in results}
        assert by_name["retry_only"].size == "XS"
        assert by_name["many_retries"].size == "XS"       # not L either
        for r in results:
            assert "n_retrys" not in r.drivers
            assert "term_run_time" not in r.drivers

    def test_calendar_still_scores_m_alongside_retries(self, isolated_db):
        """The retry/timeout exclusion must not suppress a real M signal."""
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.models.job import parse_job
        from autosys.analysis.complexity import build_report

        with sync_session() as session:
            job_repo.upsert(session, parse_job({
                "job_name": "cal_job", "job_type": "CMD", "command": "echo hi",
                "machine": "m1", "n_retrys": 2, "term_run_time": 35,
                "run_calendar": "month_end",
            }))
            session.commit()

        with sync_session() as session:
            results = build_report(job_repo.list_all(session))

        assert results[0].size == "M"
        assert "run_calendar=month_end" in results[0].drivers

    def test_box_chain_is_charged_once_not_per_box(self, isolated_db):
        """
        Splitting an N-box chain into separate DAGs is one design decision.
        Only the box the chain ends at carries the XL re-architecture driver;
        every box upstream of it drops to L. Without this, one 8-box business
        flow manufactures 2 XL "projects" here (and 22 on the real estate).
        """
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.models.job import parse_job
        from autosys.analysis.complexity import build_report

        with sync_session() as session:
            prev = None
            for i in range(1, 9):                        # b1 -> ... -> b8
                kwargs = {"job_name": f"b{i}", "job_type": "BOX"}
                if prev:
                    kwargs["condition"] = f"success({prev})"
                job_repo.upsert(session, parse_job(kwargs))
                prev = f"b{i}"
            session.commit()

        with sync_session() as session:
            results = build_report(job_repo.list_all(session))

        by_name = {r.job_name: r for r in results}
        assert by_name["b8"].size == "XL"                # depth 8, chain terminal
        assert "chain terminal" in by_name["b8"].drivers
        assert by_name["b7"].size == "L"                 # depth 7 but interior
        assert "inside a longer chain" in by_name["b7"].drivers
        assert [r.job_name for r in results if r.size == "XL"] == ["b8"]

    def test_separate_chains_each_keep_their_terminal(self, isolated_db):
        """Two independent chains are two design decisions, so two XLs."""
        from autosys.db.connection import sync_session
        from autosys.db.repository import jobs as job_repo
        from autosys.models.job import parse_job
        from autosys.analysis.complexity import build_report

        with sync_session() as session:
            for prefix in ("x", "y"):
                prev = None
                for i in range(1, 9):
                    kwargs = {"job_name": f"{prefix}{i}", "job_type": "BOX"}
                    if prev:
                        kwargs["condition"] = f"success({prev})"
                    job_repo.upsert(session, parse_job(kwargs))
                    prev = f"{prefix}{i}"
            session.commit()

        with sync_session() as session:
            results = build_report(job_repo.list_all(session))

        assert sorted(r.job_name for r in results if r.size == "XL") == ["x8", "y8"]

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

        # simulate=false: this test is about seeded history -> risk, not the
        # automatic simulation (covered in TestSimulatedRisk below).
        r = ssa_client.get("/api/v1/assessment/report", params={"simulate": "false"})
        assert r.status_code == 200
        data = r.json()

        job_a = next(j for j in data["jobs"] if j["job_name"] == "a")
        assert job_a["risk"] == "HIGH"
        assert "failure rate" in job_a["risk_drivers"]
        for field in ("risk", "risk_drivers", "blast_radius", "gap_tags"):
            assert field in job_a

        assert data["summary"]["risk_counts"]["HIGH"] == 2  # job "a" + seq_box (inherited)
        assert "gap_severity_counts" in data["summary"]

    def test_report_includes_ai_token_budget_fields(self, ssa_client, isolated_db):
        """Indicative Otto Token Budget -- additive contract, not a usage/cost
        quote. See ai_token_estimate.py."""
        from autosys.db.connection import sync_session
        with sync_session() as session:
            _seed_sequential_box(session)
            session.commit()

        r = ssa_client.get("/api/v1/assessment/report")
        assert r.status_code == 200
        data = r.json()

        budget = data["ai_token_budget"]
        assert budget is not None
        assert budget["confidence"] == "LOW"
        assert budget["calibration_status"] == "NOT_CALIBRATED"
        assert budget["min_tokens"] <= budget["max_tokens"]

        job_a = next(j for j in data["jobs"] if j["job_name"] == "a")
        for field in (
            "ai_route", "ai_pattern_key", "ai_estimate_role",
            "estimated_otto_tokens_min", "estimated_otto_tokens_max",
            "ai_estimate_confidence",
        ):
            assert field in job_a
        assert job_a["estimated_otto_tokens_min"] <= job_a["estimated_otto_tokens_max"]


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



# ---------------------------------------------------------------------------
# Automatic dry-run simulation feeding the report's operational risk
# ---------------------------------------------------------------------------

@pytest.fixture()
def sim_client(isolated_db) -> Generator[TestClient, None, None]:
    """Like ssa_client, but in dry-run mode (create_app defaults to real mode)."""
    from autosys.app_server.main import create_app
    app = create_app(start_eps=False, dry_run=True)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c

def _report(client, **params):
    r = client.get("/api/v1/assessment/report", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _wait_for_ready(client, timeout_s: float = 60.0, **params):
    import time
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        data = _report(client, **params)
        if data["simulation"]["status"] != "pending":
            return data
        time.sleep(0.2)
    raise AssertionError("simulation never left 'pending'")


def _seed_boxes(isolated_db):
    from autosys.db.connection import sync_session
    with sync_session() as session:
        _seed_sequential_box(session)
        session.commit()


class TestSimulatedRisk:

    def test_empty_db_needs_no_simulation(self, sim_client):
        assert _report(sim_client)["simulation"]["status"] == "not_needed"

    def test_jobs_without_history_get_simulated_risk(self, sim_client, isolated_db, monkeypatch):
        monkeypatch.setenv("AUTOSYS_REPORT_SIM_WAIT_S", "60")
        _seed_boxes(isolated_db)

        data = _report(sim_client)
        sim = data["simulation"]
        assert sim["status"] == "ready"
        assert sim["total_runs"] > 0
        assert sim["cycles"] == 20
        assert sim["jobs_simulated"] == 3        # a, b, c — the BOX inherits from them

        for j in data["jobs"]:
            assert j["risk_source"] == "simulated"
            assert j["risk"] != "NO_DATA"
        assert data["summary"]["risk_counts"]["NO_DATA"] == 0

    def test_simulation_leaves_live_db_untouched(self, sim_client, isolated_db, monkeypatch):
        from sqlalchemy import func, select
        from autosys.db.connection import sync_session
        from autosys.db.schema import AlarmRow, EventQueueRow, JobRow, JobRunRow
        monkeypatch.setenv("AUTOSYS_REPORT_SIM_WAIT_S", "60")
        _seed_boxes(isolated_db)

        with sync_session() as s:
            before = {r.job_name: (r.status, r.last_start) for r in s.scalars(select(JobRow))}

        assert _report(sim_client)["simulation"]["status"] == "ready"

        with sync_session() as s:
            after = {r.job_name: (r.status, r.last_start) for r in s.scalars(select(JobRow))}
            assert after == before
            for model in (JobRunRow, AlarmRow, EventQueueRow):
                assert s.scalar(select(func.count()).select_from(model)) == 0

    def test_live_history_wins_over_simulated(self, sim_client, isolated_db, monkeypatch):
        from autosys.db.connection import sync_session
        from autosys.models.enums import JobStatus
        monkeypatch.setenv("AUTOSYS_REPORT_SIM_WAIT_S", "60")
        with sync_session() as session:
            _seed_sequential_box(session)
            for _ in range(5):
                _seed_run(session, "a", JobStatus.FAILURE.value)
            session.commit()

        data = _report(sim_client)
        by_name = {j["job_name"]: j for j in data["jobs"]}
        assert by_name["a"]["risk_source"] == "history"
        assert by_name["a"]["risk"] == "HIGH"
        assert by_name["b"]["risk_source"] == "simulated"
        assert by_name["c"]["risk_source"] == "simulated"
        assert data["simulation"]["jobs_simulated"] == 2   # b, c

    def test_fully_covered_db_needs_no_simulation(self, sim_client, isolated_db):
        from autosys.db.connection import sync_session
        from autosys.models.enums import JobStatus
        with sync_session() as session:
            _seed_sequential_box(session)
            for name in ("a", "b", "c"):        # BOX jobs have no run rows of their own
                _seed_run(session, name, JobStatus.SUCCESS.value)
            session.commit()

        data = _report(sim_client)
        assert data["simulation"]["status"] == "not_needed"
        assert all(j["risk_source"] == "history" for j in data["jobs"])   # box inherits history

    def test_simulate_false_skips(self, sim_client, isolated_db):
        _seed_boxes(isolated_db)
        data = _report(sim_client, simulate="false")
        assert data["simulation"]["status"] == "skipped"
        assert all(j["risk"] == "NO_DATA" and j["risk_source"] == "none" for j in data["jobs"])

    def test_env_switch_disables_simulation(self, sim_client, isolated_db, monkeypatch):
        monkeypatch.setenv("AUTOSYS_REPORT_SIMULATE", "0")
        _seed_boxes(isolated_db)
        assert _report(sim_client)["simulation"]["status"] == "skipped"

    def test_real_execution_mode_never_simulates(self, sim_client, isolated_db):
        _seed_boxes(isolated_db)
        sim_client.app.state.dry_run = False
        data = _report(sim_client)
        assert data["execution_mode"] == "real"
        assert data["simulation"]["status"] == "skipped"
        assert all(j["risk"] == "NO_DATA" for j in data["jobs"])

    def test_pending_then_ready_and_cached(self, sim_client, isolated_db, monkeypatch):
        import threading
        from autosys.analysis import simulated_risk
        monkeypatch.setenv("AUTOSYS_REPORT_SIM_WAIT_S", "0.05")
        _seed_boxes(isolated_db)

        gate, calls, real = threading.Event(), [], simulated_risk._simulate

        def slow(snap, cycles, seed):
            calls.append(1)
            gate.wait(timeout=30)
            return real(snap, cycles, seed)

        monkeypatch.setattr(simulated_risk, "_simulate", slow)

        first = _report(sim_client)
        assert first["simulation"]["status"] == "pending"
        assert all(j["risk"] == "NO_DATA" for j in first["jobs"])
        assert first["job_count"] == 4                      # the report itself is still complete

        # A second request while it is in flight must not start another run.
        assert _report(sim_client)["simulation"]["status"] == "pending"
        assert len(calls) == 1

        gate.set()
        ready = _wait_for_ready(sim_client)
        assert ready["simulation"]["status"] == "ready"
        assert all(j["risk_source"] == "simulated" for j in ready["jobs"])

        _report(sim_client)
        assert len(calls) == 1                              # cached

    def test_cache_ignores_runtime_status_but_not_definitions(self, isolated_db):
        from autosys.analysis import simulated_risk as sr
        from autosys.db.connection import sync_session
        from autosys.db.schema import JobRow
        _seed_boxes(isolated_db)

        def key():
            with sync_session() as s:
                return sr._snapshot_key(sr.take_snapshot(s), 20, 42)

        k0 = key()
        with sync_session() as s:                            # live scheduler churn
            s.get(JobRow, "a").status = 4
            s.commit()
        assert key() == k0

        with sync_session() as s:                            # real definition change
            s.get(JobRow, "b").n_retrys = 3
            s.commit()
        assert key() != k0

    def test_failed_simulation_does_not_break_report(self, sim_client, isolated_db, monkeypatch):
        from autosys.analysis import simulated_risk
        monkeypatch.setenv("AUTOSYS_REPORT_SIM_WAIT_S", "30")
        _seed_boxes(isolated_db)

        def boom(snap, cycles, seed):
            raise RuntimeError("scratch db exploded")

        monkeypatch.setattr(simulated_risk, "_simulate", boom)
        data = _report(sim_client)
        assert data["simulation"]["status"] == "failed"
        assert "scratch db exploded" in data["simulation"]["error"]
        assert data["job_count"] == 4
        assert all(j["risk"] == "NO_DATA" for j in data["jobs"])

    def test_jil_import_warms_the_simulation(self, sim_client, isolated_db, monkeypatch):
        import time
        from autosys.analysis import simulated_risk
        monkeypatch.setenv("AUTOSYS_REPORT_WARM_DELAY_S", "0.05")

        jil = """
insert_job: warm_box
job_type: BOX

insert_job: warm_cmd
job_type: CMD
command: echo hi
machine: m1
box_name: warm_box
"""
        r = sim_client.post("/api/v1/jil/import", json={"content": jil})
        assert r.status_code == 200 and r.json()["success"]

        deadline = time.time() + 60
        while time.time() < deadline:
            with simulated_risk._lock:
                states = list(simulated_risk._states.values())
            if states and states[0].status == "ready":
                break
            time.sleep(0.1)
        else:
            raise AssertionError("import did not trigger a simulation")

        # ...so the very next report is already answered from cache.
        monkeypatch.setenv("AUTOSYS_REPORT_SIM_WAIT_S", "0")
        assert _report(sim_client)["simulation"]["status"] == "ready"

    def test_dry_run_jil_import_does_not_warm(self, sim_client, monkeypatch):
        from autosys.analysis import simulated_risk
        monkeypatch.setenv("AUTOSYS_REPORT_WARM_DELAY_S", "0.05")
        sim_client.post("/api/v1/jil/import", json={
            "content": "insert_job: x\njob_type: CMD\ncommand: echo hi\nmachine: m1\n",
            "dry_run": True,
        })
        import time
        time.sleep(0.4)
        assert simulated_risk._states == {}

    def test_report_carries_migration_signals(self, sim_client, isolated_db):
        _seed_boxes(isolated_db)
        data = _report(sim_client, simulate="false")
        job_a = next(j for j in data["jobs"] if j["job_name"] == "a")
        for field in (
            "risk_source", "machine_concentration", "command_dialect", "box_nesting_depth",
            "has_cross_box_dep", "schedule_burst_count", "has_notifications",
            "has_hardcoded_logs", "timezone", "astronomer_mapping", "risk_mitigation",
        ):
            assert field in job_a
        assert job_a["machine_concentration"]
        assert job_a["astronomer_mapping"]
        assert data["summary"]["machine_count"] == 1        # m1
        assert "dialect_counts" in data["summary"]["migration_signals"]
        assert {b["box_name"] for b in data["box_breakdown"]} >= {"seq_box"}

    def test_migration_report_blocks_and_is_isolated(self, sim_client, isolated_db):
        from sqlalchemy import func, select
        from autosys.db.connection import sync_session
        from autosys.db.schema import JobRunRow
        _seed_boxes(isolated_db)

        r = sim_client.get("/api/v1/assessment/migration-report", params={"cycles": 3})
        assert r.status_code == 200
        data = r.json()
        assert data["simulation"]["status"] == "ready"
        assert data["simulation"]["cycles"] == 3
        assert data["csv"].startswith("job_name,")
        assert all(j["risk_source"] == "simulated" for j in data["jobs"])

        with sync_session() as s:                            # previously this wrote history
            assert s.scalar(select(func.count()).select_from(JobRunRow)) == 0

    def test_migration_report_empty_db_404(self, sim_client):
        assert sim_client.get("/api/v1/assessment/migration-report").status_code == 404
