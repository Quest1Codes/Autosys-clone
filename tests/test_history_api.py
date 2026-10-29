"""
History API: POST /api/v1/history/import, GET /api/v1/history/coverage.

Covers what the unit tests in test_run_history_ingest.py can't: role
enforcement, the multipart boundary, oversized-upload rejection, and that
the route's response actually reflects the ingest report.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


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


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):
    """Like `client`, but with real auth on -- for the role-check tests."""
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'api_auth.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTOSYS_JWT_SECRET", "test-secret-not-the-insecure-default")
    monkeypatch.setenv("AUTOSYS_USERS", '{"op": {"password": "x", "role": "operator"},'
                                        ' "view": {"password": "x", "role": "viewer"}}')
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    from autosys.app_server.main import create_app
    connection.reset_engines()
    create_all_sync()
    with TestClient(create_app(start_eps=False)) as c:
        yield c
    connection.reset_engines()


def _seed_job(client_db_url_env, name="a"):
    from autosys.db.connection import sync_session
    from autosys.db.schema import JobRow
    with sync_session() as s:
        s.add(JobRow(job_name=name, job_type="CMD"))
        s.commit()


CSV = b"job_name,status,start_time,end_time\na,SU,2026-01-01T09:00:00Z,2026-01-01T09:05:00Z\n"


def test_import_persists_and_returns_the_report(client):
    _seed_job(client)
    r = client.post("/api/v1/history/import", files={"file": ("r.csv", CSV, "text/csv")})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "OK"
    assert body["format"] == "csv"
    assert body["n_rows_loaded"] == 1
    assert body["import_id"] is not None


def test_reimport_via_api_is_idempotent(client):
    _seed_job(client)
    client.post("/api/v1/history/import", files={"file": ("r.csv", CSV, "text/csv")})
    r2 = client.post("/api/v1/history/import", files={"file": ("r.csv", CSV, "text/csv")})
    body = r2.json()
    assert body["n_rows_loaded"] == 0
    assert body["n_rows_duplicate"] == 1


def test_rejected_file_returns_200_with_reject_reason_not_a_500(client):
    _seed_job(client)
    r = client.post("/api/v1/history/import", files={"file": ("r.csv", b"", "text/csv")})
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "REJECTED"


def test_declared_timezone_is_accepted_as_a_query_param(client):
    _seed_job(client)
    naive = b"job_name,status,start_time\na,SU,2026-01-01 09:00:00\n"
    r = client.post("/api/v1/history/import?declared_timezone=UTC",
                    files={"file": ("r.csv", naive, "text/csv")})
    assert r.json()["status"] == "OK"


def test_coverage_reflects_imported_data(client):
    _seed_job(client)
    client.post("/api/v1/history/import", files={"file": ("r.csv", CSV, "text/csv")})
    r = client.get("/api/v1/history/coverage")
    assert r.status_code == 200
    body = r.json()
    assert body["jobs_total"] == 1
    # one run is below MIN_OBSERVED_RUNS -- correctly "insufficient", not "sufficient"
    assert body["jobs_observed_insufficient"] == 1
    assert body["jobs_observed_sufficient"] == 0
    assert body["jobs_with_imported_evidence"] == 1
    assert body["jobs_with_dry_run_evidence"] == 0


def test_coverage_distinguishes_imported_from_dry_run_evidence(client):
    """The mutual-exclusivity signal WCC's toolbar uses: a job whose only
    run came from the dry-run EPS (source="simulated") must count toward
    jobs_with_dry_run_evidence, never jobs_with_imported_evidence, and vice
    versa for an imported row -- they are deliberately not the same bucket."""
    _seed_job(client)
    from autosys.db.connection import sync_session
    from autosys.db.schema import JobRunRow
    from autosys.models.enums import JobStatus
    with sync_session() as s:
        s.add(JobRunRow(run_id="sim-1", job_name="a", status=JobStatus.SUCCESS.value,
                        source="simulated", retry_count=0, run_date="2026-01-01"))
        s.commit()
    r = client.get("/api/v1/history/coverage")
    body = r.json()
    assert body["jobs_with_dry_run_evidence"] == 1
    assert body["jobs_with_imported_evidence"] == 0

    r2 = client.post("/api/v1/history/import", files={"file": ("r.csv", CSV, "text/csv")})
    # The import itself is rejected, not just discouraged -- see
    # import_history's mutual-exclusivity check. Letting it through and
    # merely reporting both counts "honestly" was the original design, but
    # it depended entirely on the UI's own disable-check never going stale,
    # and Run All Boxes writes its simulated rows gradually in the
    # background (the live dry-run EPS keeps cascading after the click
    # returns) -- a real timing window where the UI's snapshot reads zero
    # simulated evidence right before more of it lands. This endpoint is now
    # the authoritative gate, checked fresh in the same request.
    assert r2.status_code == 409
    r3 = client.get("/api/v1/history/coverage")
    body3 = r3.json()
    assert body3["jobs_with_dry_run_evidence"] == 1
    assert body3["jobs_with_imported_evidence"] == 0


def test_oversized_content_length_is_rejected_before_reading(client):
    _seed_job(client)
    # A tiny body claiming to be 500 MB via Content-Length: the route must
    # reject on the header alone, before attempting to read the body.
    r = client.post(
        "/api/v1/history/import",
        files={"file": ("r.csv", CSV, "text/csv")},
        headers={"content-length": str(500 * 1024 * 1024)},
    )
    assert r.status_code == 413


class TestRoleEnforcement:
    def _token(self, role: str) -> str:
        from autosys.app_server.auth import encode_token
        return encode_token("u", role)

    def test_viewer_cannot_import(self, auth_client):
        headers = {"Authorization": f"Bearer {self._token('viewer')}"}
        r = auth_client.post("/api/v1/history/import", files={"file": ("r.csv", CSV, "text/csv")},
                            headers=headers)
        assert r.status_code == 403

    def test_operator_can_import(self, auth_client):
        from autosys.db.connection import sync_session
        from autosys.db.schema import JobRow
        with sync_session() as s:
            s.add(JobRow(job_name="a", job_type="CMD"))
            s.commit()
        headers = {"Authorization": f"Bearer {self._token('operator')}"}
        r = auth_client.post("/api/v1/history/import", files={"file": ("r.csv", CSV, "text/csv")},
                            headers=headers)
        assert r.status_code == 200
        assert r.json()["status"] == "OK"

    def test_viewer_can_read_coverage(self, auth_client):
        headers = {"Authorization": f"Bearer {self._token('viewer')}"}
        r = auth_client.get("/api/v1/history/coverage", headers=headers)
        assert r.status_code == 200

    def test_no_token_is_rejected(self, auth_client):
        r = auth_client.post("/api/v1/history/import", files={"file": ("r.csv", CSV, "text/csv")})
        assert r.status_code in (401, 403)
