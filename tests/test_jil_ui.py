"""
JIL UI API tests — POST /api/v1/jil/validate and /api/v1/jil/import.

Coverage
--------
TestValidate
    test_validate_valid_jil          — happy path, all jobs returned
    test_validate_multi_type         — BOX + CMD stanzas recognised
    test_validate_invalid_jil        — parse error returns success=False + error
    test_validate_empty_content      — empty string → error (no stanzas or parse error)

TestImport
    test_import_dry_run              — dry_run=True: jobs listed, DB untouched
    test_import_persists_to_db       — dry_run=False: jobs inserted in DB
    test_import_update_existing      — second import of same job → updated count
    test_import_delete_stanza        — delete_job stanza removes job from DB
    test_import_invalid_jil          — parse error → success=False, DB untouched

TestUIRoute
    test_ui_endpoint_returns_html    — GET /ui → 200 with HTML body
"""
from __future__ import annotations

import textwrap
from typing import Generator

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Fixtures (mirrors test_phase8.py pattern)
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_jil_ui.db"
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
    app = create_app(start_eps=False)
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c


# ---------------------------------------------------------------------------
# JIL snippets
# ---------------------------------------------------------------------------

_SINGLE_CMD = textwrap.dedent("""\
    insert_job: job_alpha   job_type: CMD
    command: echo hello
    machine: localhost
""")

_BOX_WITH_CHILD = textwrap.dedent("""\
    insert_job: my_box   job_type: BOX
    owner: svc_test

    insert_job: child_cmd   job_type: CMD
    box_name: my_box
    command: /scripts/run.sh
    machine: localhost
""")

_INVALID_JIL = "this is not valid jil at all !!!"

_DELETE_STANZA = "delete_job: job_alpha\n"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _post_validate(client: TestClient, content: str, **extra) -> dict:
    resp = client.post("/api/v1/jil/validate", json={"content": content, **extra})
    assert resp.status_code == 200
    return resp.json()


def _post_import(client: TestClient, content: str, dry_run: bool = False) -> dict:
    resp = client.post("/api/v1/jil/import", json={"content": content, "dry_run": dry_run})
    assert resp.status_code == 200
    return resp.json()


def _db_job_count() -> int:
    from autosys.db.connection import sync_session
    from autosys.db.schema import JobRow
    from sqlalchemy import select
    with sync_session() as s:
        return s.execute(select(JobRow)).scalars().all().__len__()


# ===========================================================================
# Validate endpoint
# ===========================================================================

class TestValidate:

    def test_validate_valid_jil(self, client):
        data = _post_validate(client, _SINGLE_CMD)
        assert data["success"] is True
        assert data["error"] is None
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["name"] == "job_alpha"
        assert data["jobs"][0]["type"] == "CMD"
        assert data["jobs"][0]["action"] == "OK"

    def test_validate_multi_type(self, client):
        data = _post_validate(client, _BOX_WITH_CHILD)
        assert data["success"] is True
        names = {j["name"] for j in data["jobs"]}
        assert names == {"my_box", "child_cmd"}
        types = {j["type"] for j in data["jobs"]}
        assert "BOX" in types and "CMD" in types

    def test_validate_invalid_jil(self, client):
        data = _post_validate(client, _INVALID_JIL)
        assert data["success"] is False
        assert data["error"]
        assert data["jobs"] == []

    def test_validate_does_not_write_to_db(self, client):
        _post_validate(client, _SINGLE_CMD)
        assert _db_job_count() == 0

    def test_validate_counts_machines(self, client):
        machine_jil = textwrap.dedent("""\
            insert_machine: build_server
            max_load: 4
            port: 7520
        """)
        data = _post_validate(client, machine_jil)
        assert data["success"] is True
        assert data["n_machines"] == 1
        assert data["jobs"][0]["action"] == "MACHINE"


# ===========================================================================
# Import endpoint
# ===========================================================================

class TestImport:

    def test_import_dry_run(self, client):
        data = _post_import(client, _SINGLE_CMD, dry_run=True)
        assert data["success"] is True
        assert len(data["jobs"]) == 1
        assert data["jobs"][0]["action"] == "OK"
        # No DB writes
        assert _db_job_count() == 0

    def test_import_persists_to_db(self, client):
        data = _post_import(client, _SINGLE_CMD, dry_run=False)
        assert data["success"] is True
        assert data["n_inserted"] == 1
        assert data["n_updated"] == 0
        assert _db_job_count() == 1
        assert data["jobs"][0]["action"] == "INSERTED"

    def test_import_box_with_child(self, client):
        data = _post_import(client, _BOX_WITH_CHILD, dry_run=False)
        assert data["success"] is True
        assert data["n_inserted"] == 2
        assert _db_job_count() == 2

    def test_import_update_existing(self, client):
        _post_import(client, _SINGLE_CMD)
        data = _post_import(client, _SINGLE_CMD)
        assert data["success"] is True
        assert data["n_inserted"] == 0
        assert data["n_updated"] == 1
        assert data["jobs"][0]["action"] == "UPDATED"
        assert _db_job_count() == 1

    def test_import_delete_stanza(self, client):
        _post_import(client, _SINGLE_CMD)
        assert _db_job_count() == 1

        data = _post_import(client, _DELETE_STANZA)
        assert data["success"] is True
        assert data["n_deleted"] == 1
        assert _db_job_count() == 0

    def test_import_invalid_jil(self, client):
        data = _post_import(client, _INVALID_JIL)
        assert data["success"] is False
        assert data["error"]
        assert _db_job_count() == 0

    def test_import_dry_run_does_not_commit_even_with_delete(self, client):
        _post_import(client, _SINGLE_CMD)
        assert _db_job_count() == 1

        data = _post_import(client, _DELETE_STANZA, dry_run=True)
        assert data["success"] is True
        assert data["jobs"][0]["action"] == "DELETED"
        assert _db_job_count() == 1   # still in DB — dry run

    def test_import_returns_machine_count(self, client):
        machine_jil = textwrap.dedent("""\
            insert_machine: worker01
            port: 7520
        """)
        data = _post_import(client, machine_jil, dry_run=False)
        assert data["success"] is True
        assert data["n_machines"] == 1


# ===========================================================================
# UI route
# ===========================================================================

class TestUIRoute:

    def test_ui_endpoint_returns_html(self, client):
        resp = client.get("/ui")
        assert resp.status_code == 200
        assert "text/html" in resp.headers["content-type"]
        assert "AutoSys JIL Runner" in resp.text
