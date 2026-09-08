"""
Phase 9 — WCC Web Dashboard tests (~40 tests).

TestAlarmAPI        — GET/POST /api/v1/alarms via the SSA
TestWCCPages        — HTML page routes of the WCC app
TestWCCJsonAPI      — JSON data routes of the WCC app
TestExtractDeps     — _extract_deps() helper unit tests
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_p9.db"
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


@pytest.fixture()
def wcc_client(isolated_db) -> Generator[TestClient, None, None]:
    from autosys.wcc.app import create_wcc_app
    with TestClient(create_wcc_app(), raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_BOX_JIL = """
insert_job: demo_etl_box   job_type: BOX
owner: svc_demo
start_times: "06:00"

insert_job: check_source_ready   job_type: CMD
box_name: demo_etl_box
command: /scripts/check.sh
machine: etl-server-01

insert_job: extract_sales   job_type: CMD
box_name: demo_etl_box
command: /scripts/run.sh
machine: etl-server-01
condition: success(check_source_ready)
"""

_STANDALONE_JIL = """
insert_job: nightly_cleanup   job_type: CMD
command: /scripts/clean.sh
machine: etl-server-01
start_times: "02:00"
"""


def _import(client: TestClient, jil: str) -> None:
    r = client.post("/api/v1/jil/import", json={"content": jil, "dry_run": False})
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True


def _seed_alarm(job_name: str, alarm_type: str = "FAILURE", message: str = "test alarm") -> str:
    from autosys.db.connection import sync_session
    from autosys.db.schema import AlarmRow
    alarm_id = str(uuid.uuid4())
    with sync_session() as s:
        s.add(AlarmRow(
            alarm_id=alarm_id, job_name=job_name, alarm_type=alarm_type,
            message=message, raised_at=datetime.utcnow(),
        ))
    return alarm_id


# ===========================================================================
# Alarm REST API (SSA endpoints)
# ===========================================================================

class TestAlarmAPI:

    def test_list_alarms_empty(self, ssa_client):
        r = ssa_client.get("/api/v1/alarms")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_alarms_returns_active(self, ssa_client):
        _import(ssa_client, _BOX_JIL)
        _seed_alarm("demo_etl_box", "FAILURE", "box failed")
        r = ssa_client.get("/api/v1/alarms")
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["job_name"] == "demo_etl_box"
        assert data[0]["alarm_type"] == "FAILURE"
        assert data[0]["active"] is True
        assert data[0]["cleared_at"] is None

    def test_list_alarms_filter_active_true(self, ssa_client):
        _import(ssa_client, _BOX_JIL)
        a1 = _seed_alarm("demo_etl_box")
        a2 = _seed_alarm("extract_sales")
        ssa_client.post(f"/api/v1/alarms/{a1}/resolve")

        r = ssa_client.get("/api/v1/alarms", params={"active": "true"})
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["alarm_id"] == a2

    def test_list_alarms_filter_active_false(self, ssa_client):
        _import(ssa_client, _BOX_JIL)
        a1 = _seed_alarm("demo_etl_box")
        _seed_alarm("extract_sales")
        ssa_client.post(f"/api/v1/alarms/{a1}/resolve")

        r = ssa_client.get("/api/v1/alarms", params={"active": "false"})
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["alarm_id"] == a1
        assert data[0]["active"] is False
        assert data[0]["cleared_at"] is not None

    def test_list_alarms_filter_by_job(self, ssa_client):
        _import(ssa_client, _BOX_JIL)
        _seed_alarm("demo_etl_box")
        _seed_alarm("extract_sales")

        r = ssa_client.get("/api/v1/alarms", params={"job": "extract_sales"})
        assert r.status_code == 200
        data = r.json()
        assert len(data) == 1
        assert data[0]["job_name"] == "extract_sales"

    def test_resolve_alarm_success(self, ssa_client):
        _import(ssa_client, _BOX_JIL)
        alarm_id = _seed_alarm("demo_etl_box")

        r = ssa_client.post(f"/api/v1/alarms/{alarm_id}/resolve")
        assert r.status_code == 200
        data = r.json()
        assert data["active"] is False
        assert data["cleared_at"] is not None
        assert data["cleared_by"] == "anon"

    def test_resolve_alarm_not_found(self, ssa_client):
        r = ssa_client.post(f"/api/v1/alarms/{uuid.uuid4()}/resolve")
        assert r.status_code == 404

    def test_resolve_alarm_already_resolved(self, ssa_client):
        _import(ssa_client, _BOX_JIL)
        alarm_id = _seed_alarm("demo_etl_box")
        ssa_client.post(f"/api/v1/alarms/{alarm_id}/resolve")
        r = ssa_client.post(f"/api/v1/alarms/{alarm_id}/resolve")
        assert r.status_code == 409

    def test_raise_alarm_via_post(self, ssa_client):
        _import(ssa_client, _BOX_JIL)
        r = ssa_client.post(
            "/api/v1/alarms",
            params={"job_name": "demo_etl_box", "alarm_type": "MAX_RUN", "message": "too slow"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["alarm_type"] == "MAX_RUN"
        assert data["active"] is True
        assert data["message"] == "too slow"

    def test_alarm_response_schema(self, ssa_client):
        _import(ssa_client, _BOX_JIL)
        _seed_alarm("demo_etl_box", "FAILURE", "uh oh")
        r = ssa_client.get("/api/v1/alarms")
        item = r.json()[0]
        for field in ("alarm_id", "job_name", "alarm_type", "message", "raised_at", "active"):
            assert field in item, f"missing field {field!r}"


# ===========================================================================
# WCC JSON API
# ===========================================================================

class TestWCCJsonAPI:

    def test_api_jobs_empty(self, wcc_client):
        r = wcc_client.get("/api/wcc/jobs")
        assert r.status_code == 200
        data = r.json()
        assert data["jobs"] == []
        assert data["total"] == 0

    def test_api_jobs_returns_all(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        r = wcc_client.get("/api/wcc/jobs")
        assert r.status_code == 200
        data = r.json()
        assert data["total"] == 3
        names = {j["job_name"] for j in data["jobs"]}
        assert {"demo_etl_box", "extract_sales", "check_source_ready"} == names

    def test_api_jobs_has_status_class(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        jobs = wcc_client.get("/api/wcc/jobs").json()["jobs"]
        assert all("status_cls" in j for j in jobs)
        assert all(j["status_cls"].startswith("status-") for j in jobs)

    def test_api_jobs_filter_running(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        data = wcc_client.get("/api/wcc/jobs", params={"status": 1}).json()
        assert data["total"] == 0

    def test_api_box_nodes_edges(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        r = wcc_client.get("/api/wcc/boxes/demo_etl_box")
        assert r.status_code == 200
        data = r.json()
        assert "nodes" in data and "edges" in data
        node_ids = {n["id"] for n in data["nodes"]}
        assert node_ids == {"demo_etl_box", "extract_sales", "check_source_ready"}

    def test_api_box_edges_correct(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        edges = wcc_client.get("/api/wcc/boxes/demo_etl_box").json()["edges"]
        assert any(
            e["source"] == "check_source_ready" and e["target"] == "extract_sales"
            for e in edges
        )

    def test_api_box_missing_returns_error(self, wcc_client):
        r = wcc_client.get("/api/wcc/boxes/nosuchbox")
        assert r.status_code == 200
        assert "error" in r.json()

    def test_api_runs_empty(self, wcc_client):
        r = wcc_client.get("/api/wcc/runs")
        assert r.status_code == 200
        assert r.json()["runs"] == []

    def test_api_alarms_empty(self, wcc_client):
        r = wcc_client.get("/api/wcc/alarms")
        assert r.status_code == 200
        data = r.json()
        assert data["alarms"] == []
        assert data["n_active"] == 0

    def test_api_alarms_active_count(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        _seed_alarm("demo_etl_box")
        _seed_alarm("extract_sales")
        r = wcc_client.get("/api/wcc/alarms")
        data = r.json()
        assert data["n_active"] == 2

    def test_api_alarms_filter_active(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        a1 = _seed_alarm("demo_etl_box")
        _seed_alarm("extract_sales")
        from autosys.db.connection import sync_session
        from autosys.db.schema import AlarmRow
        with sync_session() as s:
            row = s.get(AlarmRow, a1)
            row.cleared_at = datetime.utcnow()
            row.cleared_by = "admin"

        r = wcc_client.get("/api/wcc/alarms", params={"active": "true"})
        data = r.json()
        assert data["n_active"] == 1
        assert all(a["active"] for a in data["alarms"])

    def test_api_run_output_empty(self, wcc_client):
        fake_run_id = str(uuid.uuid4())
        r = wcc_client.get(f"/api/wcc/runs/{fake_run_id}/output")
        assert r.status_code == 200
        assert r.json()["lines"] == []


# ===========================================================================
# _extract_deps helper
# ===========================================================================

class TestExtractDeps:

    def _deps(self, condition: str) -> list[str]:
        from autosys.wcc.app import _extract_deps
        return _extract_deps(condition)

    def test_none_returns_empty(self):
        assert self._deps(None) == []

    def test_empty_returns_empty(self):
        assert self._deps("") == []

    def test_single_success(self):
        assert self._deps("success(job_a)") == ["job_a"]

    def test_shorthand_s(self):
        assert self._deps("s(job_a)") == ["job_a"]

    def test_compound_and(self):
        deps = self._deps("success(job_a) & success(job_b)")
        assert set(deps) == {"job_a", "job_b"}

    def test_compound_or(self):
        deps = self._deps("success(job_a) | failure(job_b)")
        assert set(deps) == {"job_a", "job_b"}

    def test_failure_shorthand(self):
        assert self._deps("f(some_job)") == ["some_job"]

    def test_done_shorthand(self):
        assert self._deps("d(j1) & s(j2)") == ["j1", "j2"]

    def test_no_valid_predicates(self):
        assert self._deps("value(MY_VAR) = \"yes\"") == []
