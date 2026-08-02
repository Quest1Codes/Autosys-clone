"""
Phase 12 — WCC Dashboard Hardening tests.

Tests for:
1. New /api/wcc/jobs/{name} endpoint (job detail with runs and children)
2. SSE endpoint returns valid event stream
3. SPA fallback (when React build exists)
4. Responsive CSS (verify media queries exist)
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
    db_path = tmp_path / "test_p12.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection as _conn
    _conn.reset_engines()
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn.reset_engines()


@pytest.fixture()
def wcc_client(isolated_db) -> Generator[TestClient, None, None]:
    from autosys.wcc.app import create_wcc_app
    with TestClient(create_wcc_app(), raise_server_exceptions=True) as c:
        yield c


@pytest.fixture()
def ssa_client(isolated_db) -> Generator[TestClient, None, None]:
    from autosys.app_server.main import create_app
    with TestClient(create_app(start_eps=False), raise_server_exceptions=True) as c:
        yield c


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


def _import(client: TestClient, jil: str) -> None:
    r = client.post("/api/v1/jil/import", json={"content": jil, "dry_run": False})
    assert r.status_code == 200, r.text
    assert r.json()["success"] is True


# ===========================================================================
# 1. Job Detail API endpoint
# ===========================================================================

class TestJobDetailAPI:

    def test_job_detail_returns_job(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        r = wcc_client.get("/api/wcc/jobs/extract_sales")
        assert r.status_code == 200
        data = r.json()
        assert data["job_name"] == "extract_sales"
        assert data["job_type"] == "CMD"
        assert data["machine"] == "etl-server-01"

    def test_job_detail_includes_runs(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        r = wcc_client.get("/api/wcc/jobs/extract_sales")
        data = r.json()
        assert "runs" in data
        assert isinstance(data["runs"], list)

    def test_job_detail_box_includes_children(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        r = wcc_client.get("/api/wcc/jobs/demo_etl_box")
        data = r.json()
        assert "children" in data
        child_names = {c["job_name"] for c in data["children"]}
        assert {"check_source_ready", "extract_sales"} == child_names

    def test_job_detail_not_found(self, wcc_client):
        r = wcc_client.get("/api/wcc/jobs/no_such_job")
        assert r.status_code == 404
        assert "error" in r.json()

    def test_job_detail_cmd_has_no_children(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        r = wcc_client.get("/api/wcc/jobs/extract_sales")
        data = r.json()
        assert data["children"] == []

    def test_job_detail_has_condition(self, wcc_client, ssa_client):
        _import(ssa_client, _BOX_JIL)
        r = wcc_client.get("/api/wcc/jobs/extract_sales")
        data = r.json()
        assert data["condition"] == "success(check_source_ready)"


# ===========================================================================
# 2. SSE endpoint (source inspection — avoids hanging on infinite stream)
# ===========================================================================

class TestSSEEndpoint:

    def test_sse_route_exists(self):
        import inspect
        from autosys.wcc.app import create_wcc_app
        source = inspect.getsource(create_wcc_app)
        assert "/api/sse/jobs" in source
        assert "text/event-stream" in source
        assert "StreamingResponse" in source

    def test_sse_uses_async_generator(self):
        import inspect
        from autosys.wcc.app import create_wcc_app
        source = inspect.getsource(create_wcc_app)
        assert "async def" in source
        assert "event_generator" in source
        assert "asyncio.sleep" in source

    def test_sse_polls_db_for_jobs(self):
        import inspect
        from autosys.wcc.app import create_wcc_app
        source = inspect.getsource(create_wcc_app)
        assert "JobRow" in source
        assert "json.dumps" in source


# ===========================================================================
# 3. Responsive CSS
# ===========================================================================

class TestResponsiveCSS:

    def test_media_queries_exist(self):
        from pathlib import Path
        css_path = Path(__file__).parents[1] / "wcc-frontend" / "src" / "styles" / "global.css"
        css = css_path.read_text()
        assert "@media (max-width: 768px)" in css
        assert "@media (max-width: 480px)" in css

    def test_responsive_hides_columns_on_mobile(self):
        from pathlib import Path
        css_path = Path(__file__).parents[1] / "wcc-frontend" / "src" / "styles" / "global.css"
        css = css_path.read_text()
        assert "display: none" in css
        assert "nth-child" in css

    def test_responsive_wraps_toolbar(self):
        from pathlib import Path
        css_path = Path(__file__).parents[1] / "wcc-frontend" / "src" / "styles" / "global.css"
        css = css_path.read_text()
        assert "flex-wrap" in css


# ===========================================================================
# 4. WCC app structure
# ===========================================================================

class TestWCCAppStructure:

    def test_wcc_serves_react_if_built(self):
        """Verify WCC app checks for frontend dist directory."""
        import inspect
        from autosys.wcc.app import create_wcc_app
        source = inspect.getsource(create_wcc_app)
        assert "dist" in source
        assert "StaticFiles" in source or "FileResponse" in source

    def test_wcc_has_spa_fallback(self):
        """Verify SPA fallback route exists in source."""
        import inspect
        from autosys.wcc.app import create_wcc_app
        source = inspect.getsource(create_wcc_app)
        assert "spa_fallback" in source or "full_path" in source

    def test_wcc_has_job_detail_endpoint(self):
        """Verify /api/wcc/jobs/{name} endpoint exists in source."""
        import inspect
        from autosys.wcc.app import create_wcc_app
        source = inspect.getsource(create_wcc_app)
        assert "/api/wcc/jobs/{name}" in source or "api/wcc/jobs/" in source
