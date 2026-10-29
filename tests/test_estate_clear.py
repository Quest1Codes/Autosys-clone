"""
DELETE /api/v1/estate and `autosys estate clear` -- wipe the whole imported
job estate. See routers/estate.py's module docstring for the safety model:
admin-only, the caller must echo back the exact current job count, and
every removed job is archived first (same mechanism jil import
--replace-estate already uses), so this is recoverable, not just logged.
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner
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


@pytest.fixture()
def auth_client(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'api_auth.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTOSYS_JWT_SECRET", "test-secret-not-the-insecure-default")
    monkeypatch.setenv("AUTOSYS_USERS", '{"op": {"password": "x", "role": "operator"},'
                                        ' "adm": {"password": "x", "role": "admin"}}')
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    from autosys.app_server.main import create_app
    connection.reset_engines()
    create_all_sync()
    with TestClient(create_app(start_eps=False)) as c:
        yield c
    connection.reset_engines()


def _seed(n: int):
    from autosys.db.connection import sync_session
    from autosys.db.schema import JobRow
    with sync_session() as s:
        for i in range(n):
            s.add(JobRow(job_name=f"job{i}", job_type="CMD"))
        s.commit()


class TestAPI:
    def test_wrong_count_is_refused_and_nothing_is_deleted(self, client):
        _seed(5)
        r = client.delete("/api/v1/estate", params={"confirm_job_count": 3})
        assert r.status_code == 409
        assert "5" in r.json()["detail"]
        assert len(client.get("/api/v1/jobs").json()) == 5

    def test_correct_count_removes_everything(self, client):
        _seed(5)
        r = client.delete("/api/v1/estate", params={"confirm_job_count": 5})
        assert r.status_code == 200
        assert r.json() == {"jobs_removed": 5, "jobs_failed": 0, "archive_path": ""}
        assert client.get("/api/v1/jobs").json() == []

    def test_missing_confirm_param_is_rejected(self, client):
        _seed(5)
        r = client.delete("/api/v1/estate")
        assert r.status_code == 422   # FastAPI's own required-query-param rejection

    def test_run_history_and_alarms_are_removed_too(self, client):
        _seed(1)
        from autosys.db.connection import sync_session
        from autosys.db.schema import JobRunRow, AlarmRow
        from autosys.models.enums import JobStatus
        with sync_session() as s:
            s.add(JobRunRow(run_id="r1", job_name="job0", status=JobStatus.FAILURE.value,
                            source="live", retry_count=0, run_date="2026-01-01"))
            s.add(AlarmRow(alarm_id="a1", job_name="job0", alarm_type="JOB_FAILURE",
                           message="x", raised_at=__import__("datetime").datetime(2026, 1, 1)))
            s.commit()

        r = client.delete("/api/v1/estate", params={"confirm_job_count": 1})
        assert r.status_code == 200

        with sync_session() as s:
            assert s.execute(select(JobRunRow)).first() is None
            assert s.execute(select(AlarmRow)).first() is None

    def test_clearing_an_empty_estate_is_a_no_op(self, client):
        r = client.delete("/api/v1/estate", params={"confirm_job_count": 0})
        assert r.status_code == 200
        assert r.json() == {"jobs_removed": 0, "jobs_failed": 0, "archive_path": ""}

    def test_cleared_estate_can_be_reimported(self, client):
        """The point of archiving before delete: the estate isn't gone for
        good, it's recoverable by re-importing. Exercised here with a fresh
        import rather than the archive itself (a full restore-from-archive
        round trip belongs to jil_ingest's own test coverage of
        remove_jobs_not_in, which this reuses verbatim)."""
        _seed(3)
        client.delete("/api/v1/estate", params={"confirm_job_count": 3})
        r = client.post("/api/v1/jil/import",
                        json={"content": "insert_job: new_job   job_type: CMD\ncommand: /bin/true\n"})
        assert r.status_code == 200
        assert r.json()["success"] is True
        assert len(client.get("/api/v1/jobs").json()) == 1


class TestRoleEnforcement:
    def _token(self, role: str) -> str:
        from autosys.app_server.auth import encode_token
        return encode_token("u", role)

    def test_operator_cannot_clear(self, auth_client):
        _seed(5)
        headers = {"Authorization": f"Bearer {self._token('operator')}"}
        r = auth_client.delete("/api/v1/estate", params={"confirm_job_count": 5}, headers=headers)
        assert r.status_code == 403
        assert len(auth_client.get("/api/v1/jobs", headers=headers).json()) == 5

    def test_viewer_cannot_clear(self, auth_client):
        _seed(5)
        headers = {"Authorization": f"Bearer {self._token('viewer')}"}
        r = auth_client.delete("/api/v1/estate", params={"confirm_job_count": 5}, headers=headers)
        assert r.status_code == 403

    def test_admin_can_clear(self, auth_client):
        _seed(5)
        headers = {"Authorization": f"Bearer {self._token('admin')}"}
        r = auth_client.delete("/api/v1/estate", params={"confirm_job_count": 5}, headers=headers)
        assert r.status_code == 200
        assert r.json()["jobs_removed"] == 5


class TestCLI:
    def test_without_yes_reports_and_does_nothing(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'cli.db'}")
        from autosys.db.migrations import create_all_sync
        create_all_sync()
        _seed(4)
        from autosys.cli.estate_cmd import estate_group
        res = CliRunner().invoke(estate_group, ["clear"])
        assert res.exit_code == 0
        assert "4 job(s) would be removed" in res.output
        from autosys.db.repository import jobs as job_repo
        from autosys.db.connection import sync_session
        with sync_session() as s:
            assert len(job_repo.list_all(s)) == 4

    def test_with_yes_removes_everything(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'cli2.db'}")
        from autosys.db.migrations import create_all_sync
        create_all_sync()
        _seed(4)
        from autosys.cli.estate_cmd import estate_group
        res = CliRunner().invoke(estate_group, ["clear", "--yes"])
        assert res.exit_code == 0
        assert "Removed 4 job(s)" in res.output
        from autosys.db.repository import jobs as job_repo
        from autosys.db.connection import sync_session
        with sync_session() as s:
            assert len(job_repo.list_all(s)) == 0
