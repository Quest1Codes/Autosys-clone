"""
JIL API: never read server files, and never report failure as success.

SEC-05 / ING-09: `insert_blob` / `insert_glob` with `blob_file:` made the API
open() and read whatever path the JIL named -- the bundle's DB password
files, /proc/self/environ (JWT secret, users) -- into ujo_blob; a viewer's
/validate with `blob_file: /dev/zero` grew the process to 2.3 GB. The
command-line import-dir already ran with read_files=False; the API did not.

ING-07: a stanza that failed to store (e.g. a PostgreSQL column overflow)
was reported as success with zero warnings. It is now counted in n_failed.
"""
from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'api.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    from autosys.app_server.main import create_app
    connection.reset_engines()
    create_all_sync()
    with TestClient(create_app(start_eps=False)) as c:
        yield c
    connection.reset_engines()


def _post(client, route, jil):
    r = client.post(f"/api/v1/jil/{route}", json={"content": jil})
    assert r.status_code == 200, r.text
    return r.json()


def test_import_does_not_read_blob_file(client, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("SUPERSECRET-pg-password")
    _post(client, "import", f"insert_blob: LOOT\nblob_file: {secret}\n")
    _post(client, "import", f"insert_glob: G1\nblob_file: {secret}\n")
    from autosys.db.connection import sync_session
    from autosys.db.schema import BlobRow, GlobRow
    with sync_session() as s:
        stored = [b.blob_data if hasattr(b, "blob_data") else None for b in s.scalars(select(BlobRow))]
        rows = [r.__dict__ for r in s.scalars(select(BlobRow))] + [r.__dict__ for r in s.scalars(select(GlobRow))]
    assert not any("SUPERSECRET" in json.dumps(r, default=str) for r in rows), stored


def test_validate_with_dev_zero_returns_quickly(client):
    start = time.perf_counter()
    _post(client, "validate", "insert_blob: Z\nblob_file: /dev/zero\n")
    assert time.perf_counter() - start < 5


def test_stanza_that_fails_to_store_is_reported(client, monkeypatch):
    import autosys.parser.jil_ingest as ingest

    real = ingest.apply_operation

    def flaky(session, op, *a, **kw):
        if getattr(getattr(op, "job", None), "job_name", None) == "BAD":
            raise RuntimeError("simulated database rejection")
        return real(session, op, *a, **kw)

    monkeypatch.setattr(ingest, "apply_operation", flaky)
    res = _post(client, "import",
                "insert_job: GOOD  job_type: CMD\ncommand: x\nmachine: m\n\n"
                "insert_job: BAD  job_type: CMD\ncommand: x\nmachine: m\n")
    assert res["n_failed"] == 1
    assert res["n_inserted"] == 1
