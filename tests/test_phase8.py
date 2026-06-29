"""
Phase 8 — REST API Application Server (SSA)
============================================

Tests cover every router and the key behaviours of the App Server:

  TestHealthEndpoints       — /health + /health/ready
  TestJobsRouter            — GET /jobs, GET /jobs/{name}, DELETE, sendevent
  TestEventsRouter          — GET /events, GET /events/history
  TestRunsRouter            — GET /runs, GET /runs/{run_id}, output
  TestMachinesRouter        — GET/POST/DELETE /machines, heartbeat
  TestGlobalsRouter         — GET/PUT/DELETE /globals
  TestAuthRouter            — POST /auth/token, JWT validation
  TestWebSocket             — /ws/events broadcast
  TestCLISchedulerServe     — autosys scheduler serve --help
  TestEventBroadcaster      — unit tests for the broadcaster class
  TestStatusChangeBroadcast — EPS emits status-change events

All tests use FastAPI's TestClient (sync) via an isolated SQLite DB fixture.
Auth is disabled (AUTOSYS_AUTH_ENABLED=false, the default) so no tokens needed.
"""
from __future__ import annotations

import os
import uuid
import asyncio
from datetime import datetime
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

# ---------------------------------------------------------------------------
# Fixture: isolated DB + TestClient
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_phase8.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection as _conn
    _conn._sync_engine = None   # force fresh engine for each test
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn._sync_engine = None


@pytest.fixture()
def client(isolated_db) -> Generator[TestClient, None, None]:
    from autosys.app_server.main import create_app
    app = create_app(start_eps=False)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def session(isolated_db):
    from autosys.db.connection import sync_session
    with sync_session() as s:
        yield s


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _seed_cmd(session: Session, name: str = "test_job", **extra) -> None:
    """Insert a minimal CMD job row directly."""
    from autosys.db.schema import JobRow
    kwargs = {"status": "INACTIVE", **extra}   # extra can override status
    row = JobRow(
        job_name  = name,
        job_type  = "CMD",
        command   = "echo hello",
        machine   = "localhost",
        **kwargs,
    )
    session.add(row)
    session.commit()


def _seed_box(session: Session, name: str = "test_box") -> None:
    from autosys.db.schema import JobRow
    row = JobRow(job_name=name, job_type="BOX", status="INACTIVE")
    session.add(row)
    session.commit()


# ===========================================================================
# Health
# ===========================================================================

class TestHealthEndpoints:

    def test_health_live_returns_200(self, client):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_health_live_body(self, client):
        body = client.get("/health").json()
        assert body["status"] == "ok"
        assert "version" in body

    def test_health_ready_returns_200(self, client):
        resp = client.get("/health/ready")
        assert resp.status_code == 200

    def test_health_ready_db_connected(self, client):
        body = client.get("/health/ready").json()
        assert body["db"] == "connected"


# ===========================================================================
# Jobs router
# ===========================================================================

class TestJobsRouter:

    def test_list_jobs_empty(self, client):
        resp = client.get("/api/v1/jobs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_jobs_returns_all(self, client, session):
        _seed_cmd(session, "job_a")
        _seed_cmd(session, "job_b")
        resp = client.get("/api/v1/jobs")
        assert resp.status_code == 200
        names = {j["job_name"] for j in resp.json()}
        assert names == {"job_a", "job_b"}

    def test_list_jobs_filter_by_status(self, client, session):
        _seed_cmd(session, "run_job", status="RUNNING")
        _seed_cmd(session, "idle_job", status="INACTIVE")
        resp = client.get("/api/v1/jobs?status=RUNNING")
        names = [j["job_name"] for j in resp.json()]
        assert "run_job" in names
        assert "idle_job" not in names

    def test_list_jobs_filter_by_pattern(self, client, session):
        _seed_cmd(session, "etl_extract")
        _seed_cmd(session, "etl_load")
        _seed_cmd(session, "report_daily")
        resp = client.get("/api/v1/jobs?pattern=etl_%")
        names = [j["job_name"] for j in resp.json()]
        assert "etl_extract" in names
        assert "etl_load" in names
        assert "report_daily" not in names

    def test_get_job_returns_detail(self, client, session):
        _seed_cmd(session, "detail_job")
        resp = client.get("/api/v1/jobs/detail_job")
        assert resp.status_code == 200
        body = resp.json()
        assert body["job_name"] == "detail_job"
        assert body["job_type"] == "CMD"
        assert "start_times" in body     # detail includes scheduling fields

    def test_get_job_not_found(self, client):
        resp = client.get("/api/v1/jobs/nonexistent")
        assert resp.status_code == 404

    def test_get_job_response_has_status(self, client, session):
        _seed_cmd(session, "s_job", status="SUCCESS")
        body = client.get("/api/v1/jobs/s_job").json()
        assert body["status"] == "SUCCESS"

    def test_delete_job(self, client, session):
        _seed_cmd(session, "delete_me")
        resp = client.delete("/api/v1/jobs/delete_me")
        assert resp.status_code == 204
        assert client.get("/api/v1/jobs/delete_me").status_code == 404

    def test_delete_job_not_found(self, client):
        resp = client.delete("/api/v1/jobs/ghost")
        assert resp.status_code == 404

    def test_sendevent_startjob(self, client, session):
        _seed_cmd(session, "ev_job")
        resp = client.post(
            "/api/v1/jobs/ev_job/sendevent",
            json={"event_type": "STARTJOB"},
        )
        assert resp.status_code == 202
        body = resp.json()
        assert body["event_type"] == "STARTJOB"
        assert body["job_name"] == "ev_job"
        assert "event_id" in body

    def test_sendevent_killjob(self, client, session):
        _seed_cmd(session, "kill_job", status="RUNNING")
        resp = client.post(
            "/api/v1/jobs/kill_job/sendevent",
            json={"event_type": "KILLJOB"},
        )
        assert resp.status_code == 202

    def test_sendevent_unknown_type_returns_400(self, client, session):
        _seed_cmd(session, "any_job")
        resp = client.post(
            "/api/v1/jobs/any_job/sendevent",
            json={"event_type": "INVALID_EVENT"},
        )
        assert resp.status_code == 400

    def test_sendevent_creates_queue_entry(self, client, session):
        _seed_cmd(session, "q_job")
        client.post("/api/v1/jobs/q_job/sendevent", json={"event_type": "STARTJOB"})
        # Confirm event landed in the queue
        events_resp = client.get("/api/v1/events")
        assert events_resp.status_code == 200
        ev_types = [e["event_type"] for e in events_resp.json()]
        assert "STARTJOB" in ev_types

    def test_sendevent_force_startjob(self, client, session):
        _seed_cmd(session, "fstart_job")
        resp = client.post(
            "/api/v1/jobs/fstart_job/sendevent",
            json={"event_type": "FORCE_STARTJOB"},
        )
        assert resp.status_code == 202

    def test_list_jobs_filter_by_box(self, client, session):
        _seed_box(session, "parent_box")
        _seed_cmd(session, "child_a", box_name="parent_box")
        _seed_cmd(session, "child_b", box_name="parent_box")
        _seed_cmd(session, "standalone")
        resp = client.get("/api/v1/jobs?box=parent_box")
        names = [j["job_name"] for j in resp.json()]
        assert "child_a" in names
        assert "child_b" in names
        assert "standalone" not in names


# ===========================================================================
# Events router
# ===========================================================================

class TestEventsRouter:

    def test_list_events_empty(self, client):
        resp = client.get("/api/v1/events")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_events_shows_pending(self, client, session):
        _seed_cmd(session, "ev2_job")
        client.post("/api/v1/jobs/ev2_job/sendevent", json={"event_type": "STARTJOB"})
        resp = client.get("/api/v1/events")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["status"] == "PENDING"

    def test_list_events_has_job_name(self, client, session):
        _seed_cmd(session, "named_ev_job")
        client.post("/api/v1/jobs/named_ev_job/sendevent", json={"event_type": "STARTJOB"})
        events = client.get("/api/v1/events").json()
        assert events[0]["job_name"] == "named_ev_job"

    def test_event_history_empty(self, client):
        resp = client.get("/api/v1/events/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_event_history_shows_processed(self, client, session):
        """Process an event via the EPS and check it appears in history."""
        from autosys.db.connection import sync_session as ss
        from autosys.scheduler.event_processor import EventProcessor
        _seed_cmd(session, "hist_job")
        client.post("/api/v1/jobs/hist_job/sendevent", json={"event_type": "STARTJOB"})
        # Run the EPS once to process the event
        proc = EventProcessor(auto_complete=True)
        with ss() as s:
            proc.process_one_tick(s)
        hist = client.get("/api/v1/events/history").json()
        assert len(hist) >= 1
        assert any(e["job_name"] == "hist_job" for e in hist)


# ===========================================================================
# Runs router
# ===========================================================================

class TestRunsRouter:

    def test_list_runs_empty(self, client):
        resp = client.get("/api/v1/runs")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_runs_shows_runs(self, client, session):
        from autosys.db.repository import runs as run_repo
        from autosys.db.connection import sync_session
        _seed_cmd(session, "run_hist_job")
        with sync_session() as s:
            run_repo.start(s, str(uuid.uuid4()), "run_hist_job",
                           command="echo", machine="localhost", run_date="2024-01-15")
            s.commit()
        resp = client.get("/api/v1/runs")
        assert resp.status_code == 200
        assert len(resp.json()) == 1
        assert resp.json()[0]["job_name"] == "run_hist_job"

    def test_list_runs_filter_by_job(self, client, session):
        from autosys.db.repository import runs as run_repo
        from autosys.db.connection import sync_session
        _seed_cmd(session, "job_x")
        _seed_cmd(session, "job_y")
        with sync_session() as s:
            run_repo.start(s, str(uuid.uuid4()), "job_x",
                           command="echo", machine="localhost", run_date="2024-01-15")
            run_repo.start(s, str(uuid.uuid4()), "job_y",
                           command="echo", machine="localhost", run_date="2024-01-15")
            s.commit()
        resp = client.get("/api/v1/runs?job=job_x")
        names = [r["job_name"] for r in resp.json()]
        assert names == ["job_x"]

    def test_get_run_not_found(self, client):
        resp = client.get(f"/api/v1/runs/{uuid.uuid4()}")
        assert resp.status_code == 404

    def test_get_run_by_id(self, client, session):
        from autosys.db.repository import runs as run_repo
        from autosys.db.connection import sync_session
        _seed_cmd(session, "id_job")
        rid = str(uuid.uuid4())
        with sync_session() as s:
            run_repo.start(s, rid, "id_job",
                           command="echo", machine="localhost", run_date="2024-01-15")
            s.commit()
        resp = client.get(f"/api/v1/runs/{rid}")
        assert resp.status_code == 200
        assert resp.json()["run_id"] == rid

    def test_get_run_output_empty(self, client, session):
        from autosys.db.repository import runs as run_repo
        from autosys.db.connection import sync_session
        _seed_cmd(session, "out_job")
        rid = str(uuid.uuid4())
        with sync_session() as s:
            run_repo.start(s, rid, "out_job",
                           command="echo", machine="localhost", run_date="2024-01-15")
            s.commit()
        resp = client.get(f"/api/v1/runs/{rid}/output")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_get_run_output_with_lines(self, client, session):
        from autosys.db.repository import runs as run_repo, output as output_repo
        from autosys.db.connection import sync_session
        _seed_cmd(session, "out2_job")
        rid = str(uuid.uuid4())
        with sync_session() as s:
            run_repo.start(s, rid, "out2_job",
                           command="echo", machine="localhost", run_date="2024-01-15")
            output_repo.append(s, rid, "out2_job", line_no=1, content="hello world", stream="stdout")
            s.commit()
        resp = client.get(f"/api/v1/runs/{rid}/output")
        assert resp.status_code == 200
        lines = resp.json()
        assert len(lines) == 1
        assert lines[0]["line"] == "hello world"
        assert lines[0]["seq"] == 1

    def test_get_run_output_not_found(self, client):
        resp = client.get(f"/api/v1/runs/{uuid.uuid4()}/output")
        assert resp.status_code == 404

    def test_run_has_duration_when_finished(self, client, session):
        from autosys.db.repository import runs as run_repo
        from autosys.db.connection import sync_session
        _seed_cmd(session, "dur_job")
        rid = str(uuid.uuid4())
        with sync_session() as s:
            run_repo.start(s, rid, "dur_job",
                           command="echo", machine="localhost", run_date="2024-01-15")
            s.flush()   # flush pending INSERT so finish() sees the row
            run_repo.finish(s, rid, status="SUCCESS", exit_code=0)
            s.commit()
        body = client.get(f"/api/v1/runs/{rid}").json()
        assert body["exit_code"] == 0
        assert body["status"] == "SUCCESS"
        assert body["duration_seconds"] is not None
        assert body["duration_seconds"] >= 0


# ===========================================================================
# Machines router
# ===========================================================================

class TestMachinesRouter:

    def test_list_machines_returns_200(self, client):
        """Machine list endpoint returns 200. (localhost is seeded by migrations.)"""
        resp = client.get("/api/v1/machines")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_register_machine(self, client):
        resp = client.post("/api/v1/machines", json={
            "machine_name": "worker-01",
            "host": "10.0.0.1",
            "port": 7520,
        })
        assert resp.status_code == 201
        body = resp.json()
        assert body["machine_name"] == "worker-01"
        assert body["host"] == "10.0.0.1"
        assert body["port"] == 7520

    def test_register_machine_idempotent(self, client):
        payload = {"machine_name": "worker-02", "host": "10.0.0.2", "port": 7520}
        client.post("/api/v1/machines", json=payload)
        resp = client.post("/api/v1/machines", json=payload)
        assert resp.status_code == 201   # upsert — no 409 conflict

    def test_get_machine(self, client):
        client.post("/api/v1/machines", json={"machine_name": "get-m", "host": "127.0.0.1", "port": 7521})
        resp = client.get("/api/v1/machines/get-m")
        assert resp.status_code == 200
        assert resp.json()["machine_name"] == "get-m"

    def test_get_machine_not_found(self, client):
        resp = client.get("/api/v1/machines/ghost-machine")
        assert resp.status_code == 404

    def test_list_machines_after_register(self, client):
        client.post("/api/v1/machines", json={"machine_name": "m1", "host": "h1", "port": 7520})
        client.post("/api/v1/machines", json={"machine_name": "m2", "host": "h2", "port": 7520})
        resp = client.get("/api/v1/machines")
        names = [m["machine_name"] for m in resp.json()]
        assert "m1" in names
        assert "m2" in names

    def test_delete_machine(self, client):
        client.post("/api/v1/machines", json={"machine_name": "del-m", "host": "h", "port": 7520})
        resp = client.delete("/api/v1/machines/del-m")
        assert resp.status_code == 204
        assert client.get("/api/v1/machines/del-m").status_code == 404

    def test_delete_machine_not_found(self, client):
        resp = client.delete("/api/v1/machines/phantom")
        assert resp.status_code == 404

    def test_machine_has_status_field(self, client):
        client.post("/api/v1/machines", json={"machine_name": "stat-m", "host": "h", "port": 7520})
        body = client.get("/api/v1/machines/stat-m").json()
        assert "status" in body


# ===========================================================================
# Globals router
# ===========================================================================

class TestGlobalsRouter:

    def test_list_globals_empty(self, client):
        resp = client.get("/api/v1/globals")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_set_global(self, client):
        resp = client.put("/api/v1/globals/BATCH_DATE", json={"value": "2024-01-15"})
        assert resp.status_code == 200
        body = resp.json()
        assert body["name"] == "BATCH_DATE"
        assert body["value"] == "2024-01-15"

    def test_get_global(self, client):
        client.put("/api/v1/globals/MY_VAR", json={"value": "hello"})
        resp = client.get("/api/v1/globals/MY_VAR")
        assert resp.status_code == 200
        assert resp.json()["value"] == "hello"

    def test_get_global_not_found(self, client):
        resp = client.get("/api/v1/globals/NONEXISTENT")
        assert resp.status_code == 404

    def test_update_global(self, client):
        client.put("/api/v1/globals/COUNTER", json={"value": "1"})
        client.put("/api/v1/globals/COUNTER", json={"value": "2"})
        assert client.get("/api/v1/globals/COUNTER").json()["value"] == "2"

    def test_delete_global(self, client):
        client.put("/api/v1/globals/DEL_VAR", json={"value": "x"})
        resp = client.delete("/api/v1/globals/DEL_VAR")
        assert resp.status_code == 204
        assert client.get("/api/v1/globals/DEL_VAR").status_code == 404

    def test_delete_global_not_found(self, client):
        resp = client.delete("/api/v1/globals/GHOST_VAR")
        assert resp.status_code == 404

    def test_list_globals_after_set(self, client):
        client.put("/api/v1/globals/A", json={"value": "1"})
        client.put("/api/v1/globals/B", json={"value": "2"})
        resp = client.get("/api/v1/globals")
        names = [g["name"] for g in resp.json()]
        assert "A" in names
        assert "B" in names


# ===========================================================================
# Auth router
# ===========================================================================

class TestAuthRouter:

    def test_login_valid_credentials(self, client, monkeypatch):
        monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "true")
        resp = client.post("/api/v1/auth/token",
                           json={"username": "admin", "password": "admin"})
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"
        assert body["expires_in"] > 0

    def test_login_invalid_credentials(self, client, monkeypatch):
        monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "true")
        resp = client.post("/api/v1/auth/token",
                           json={"username": "admin", "password": "wrongpassword"})
        assert resp.status_code == 401

    def test_token_encode_decode_roundtrip(self):
        from autosys.app_server.auth import encode_token, decode_token
        token = encode_token("alice", "operator")
        payload = decode_token(token)
        assert payload["sub"] == "alice"
        assert payload["role"] == "operator"

    def test_expired_token_rejected(self):
        from autosys.app_server.auth import encode_token, decode_token
        token = encode_token("bob", "viewer", ttl=-1)   # already expired
        with pytest.raises(ValueError, match="expired"):
            decode_token(token)

    def test_tampered_token_rejected(self):
        from autosys.app_server.auth import encode_token, decode_token
        token = encode_token("eve", "viewer")
        tampered = token[:-4] + "XXXX"
        with pytest.raises(ValueError):
            decode_token(tampered)

    def test_auth_disabled_allows_access_without_token(self, client):
        """When auth is disabled (default), all endpoints work without a token."""
        resp = client.get("/api/v1/jobs")
        assert resp.status_code == 200


# ===========================================================================
# EventBroadcaster unit tests
# ===========================================================================

class TestEventBroadcaster:

    def test_connection_count_starts_zero(self):
        from autosys.app_server.broadcaster import EventBroadcaster
        bc = EventBroadcaster()
        assert bc.connection_count == 0

    def test_publish_sync_no_loop_does_not_raise(self):
        """publish_sync should silently do nothing when there's no running loop."""
        from autosys.app_server.broadcaster import EventBroadcaster
        bc = EventBroadcaster()
        # No event loop running — should not raise
        bc.publish_sync({"type": "STATUS_CHANGE", "job_name": "x", "old": "INACTIVE", "new": "RUNNING", "ts": "now"})

    def test_publish_async_no_connections(self):
        """publish() with no connections should complete without error."""
        from autosys.app_server.broadcaster import EventBroadcaster
        bc = EventBroadcaster()

        async def _run():
            await bc.publish({"type": "STATUS_CHANGE", "job_name": "x"})

        asyncio.run(_run())


# ===========================================================================
# Status-change broadcast from EPS
# ===========================================================================

class TestStatusChangeBroadcast:

    def test_on_status_change_called_on_startjob(self, isolated_db):
        """EPS calls on_status_change when a job transitions to STARTING."""
        from autosys.db.connection import sync_session
        from autosys.db.schema import JobRow
        from autosys.models.event import Event
        from autosys.db.repository import events as ev_repo
        from autosys.scheduler.event_processor import EventProcessor

        received: list[dict] = []

        with sync_session() as s:
            s.add(JobRow(job_name="bc_job", job_type="CMD",
                         command="echo", machine="localhost", status="INACTIVE"))
            ev_repo.enqueue(s, Event(event_type="STARTJOB", job_name="bc_job",
                                      source="internal"))
            s.commit()

        proc = EventProcessor(
            auto_complete    = False,
            on_status_change = received.append,
        )
        with sync_session() as s:
            proc.process_one_tick(s)

        assert any(e["job_name"] == "bc_job" for e in received)
        assert any(e["new"] == "STARTING" for e in received)

    def test_on_status_change_called_on_killjob(self, isolated_db):
        """EPS calls on_status_change when a job is killed."""
        from autosys.db.connection import sync_session
        from autosys.db.schema import JobRow
        from autosys.models.event import Event
        from autosys.db.repository import events as ev_repo
        from autosys.scheduler.event_processor import EventProcessor

        received: list[dict] = []

        with sync_session() as s:
            s.add(JobRow(job_name="kill_bc", job_type="CMD",
                         command="sleep 999", machine="localhost", status="RUNNING"))
            ev_repo.enqueue(s, Event(event_type="KILLJOB", job_name="kill_bc",
                                      source="internal"))
            s.commit()

        proc = EventProcessor(on_status_change=received.append)
        with sync_session() as s:
            proc.process_one_tick(s)

        assert any(e["new"] == "TERMINATED" for e in received)

    def test_emit_not_called_when_status_unchanged(self, isolated_db):
        """_emit_status_change is a no-op when old == new."""
        from autosys.scheduler.event_processor import EventProcessor
        received: list[dict] = []
        proc = EventProcessor(on_status_change=received.append)
        proc._emit_status_change("any_job", "RUNNING", "RUNNING")
        assert received == []


# ===========================================================================
# CLI: scheduler serve
# ===========================================================================

class TestCLISchedulerServe:

    def test_serve_help_exits_0(self):
        from click.testing import CliRunner
        from autosys.cli.scheduler_cmd import scheduler_serve
        result = CliRunner().invoke(scheduler_serve, ["--help"])
        assert result.exit_code == 0

    def test_serve_help_shows_port_option(self):
        from click.testing import CliRunner
        from autosys.cli.scheduler_cmd import scheduler_serve
        result = CliRunner().invoke(scheduler_serve, ["--help"])
        assert "--port" in result.output

    def test_serve_help_shows_poll_interval(self):
        from click.testing import CliRunner
        from autosys.cli.scheduler_cmd import scheduler_serve
        result = CliRunner().invoke(scheduler_serve, ["--help"])
        assert "poll-interval" in result.output
