"""
V1 hardening — surfaces removed from the client-facing build.

See dev/task3-simulator-jil-and-dryrun-visibility/architect-output/03-recommendation-and-plan.md
(V1) in the shinro repo for the full rationale. Covered elsewhere:
  - GET /ui, /static/*            -> tests/test_jil_ui.py::TestUIRoute
  - DELETE /jobs/{name}            -> tests/test_phase8.py::TestJobsRouter
  - POST /jobs/{name}/sendevent    -> tests/test_phase8.py::TestJobsRouter

This file covers the rest: interactive docs, the execution-mode write, and
that CORS no longer allows every origin by default.
"""
from __future__ import annotations

from typing import Generator

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_v1_hardening.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection as _conn
    _conn.reset_engines()
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn.reset_engines()


@pytest.fixture()
def client(isolated_db) -> Generator[TestClient, None, None]:
    from autosys.app_server.main import create_app
    with TestClient(create_app(start_eps=False), raise_server_exceptions=True) as c:
        yield c


class TestInteractiveDocsRemoved:

    def test_docs_removed(self, client):
        assert client.get("/docs").status_code == 404

    def test_redoc_removed(self, client):
        assert client.get("/redoc").status_code == 404

    def test_openapi_json_removed(self, client):
        assert client.get("/openapi.json").status_code == 404


class TestExecutionModeReadOnly:

    def test_get_execution_mode_still_works(self, client):
        resp = client.get("/api/v1/settings/execution-mode")
        assert resp.status_code == 200
        assert "dry_run" in resp.json()

    def test_put_execution_mode_removed(self, client):
        """No client-facing deployment should be able to flip a running
        server between dry-run and real execution over the network."""
        resp = client.put("/api/v1/settings/execution-mode", json={"dry_run": False})
        assert resp.status_code in (404, 405)


class TestCORSDefaultDeny:

    def test_no_access_control_header_for_arbitrary_origin(self, client):
        """
        Default AUTOSYS_CORS_ORIGINS is empty -- no browser-JS origin is
        allowed by default. The WCC frontend itself never needs this: it's
        served by nginx and calls this API via a same-origin relative path
        (see nginx.conf), which CORS does not gate at all.
        """
        resp = client.get(
            "/api/v1/settings/execution-mode",
            headers={"Origin": "https://evil.example.com"},
        )
        assert "access-control-allow-origin" not in {k.lower() for k in resp.headers}
