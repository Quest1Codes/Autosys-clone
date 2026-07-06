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
        with sync_session() as session:
            _seed_sequential_box(session)
            session.commit()

        r1 = ssa_client.post("/api/v1/assessment/boxes/seq_box/trace")
        r2 = ssa_client.post("/api/v1/assessment/boxes/seq_box/trace")
        assert r1.json()["outcome"] == r2.json()["outcome"] == "SUCCESS"
        assert r1.json()["wave_count"] == r2.json()["wave_count"] == 3

        job = ssa_client.get("/api/v1/jobs/seq_box").json()
        assert job["status"] == "INACTIVE"
