"""
Phase 8 — Cross-Instance (xinst) tests.

Tests:
- ExternalInstanceRepository CRUD
- RemoteInstanceClient with mock HTTP
- resolve_xinst_condition with remote job references
"""

from __future__ import annotations

import pytest
from datetime import datetime

from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.schema import JobRow, ExternalInstanceRow
from autosys.db.repository import xinsts as xinst_repo
from autosys.models.enums import JobStatus
from autosys.engine.xinst_client import RemoteInstanceClient, resolve_xinst_condition


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_xinst.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines()
    create_all_sync(drop_first=False)
    yield
    reset_engines()


# ===========================================================================
# ExternalInstanceRepository CRUD
# ===========================================================================

class TestXinstRepository:

    def test_upsert_insert(self, fresh_db):
        with sync_session() as s:
            result = xinst_repo.upsert(s, "prod1", "production", "prod.example.com", 9000)
            s.commit()
        assert result == "inserted"
        with sync_session() as s:
            row = xinst_repo.get(s, "prod1")
            assert row is not None
            assert row.instance_name == "production"
            assert row.host == "prod.example.com"
            assert row.port == 9000

    def test_upsert_update(self, fresh_db):
        with sync_session() as s:
            xinst_repo.upsert(s, "dev1", "dev", "dev.example.com", 9000)
            s.commit()
        with sync_session() as s:
            result = xinst_repo.upsert(s, "dev1", "dev-updated", "dev2.example.com", 9001)
            s.commit()
        assert result == "updated"
        with sync_session() as s:
            row = xinst_repo.get(s, "dev1")
            assert row.instance_name == "dev-updated"
            assert row.host == "dev2.example.com"
            assert row.port == 9001

    def test_delete(self, fresh_db):
        with sync_session() as s:
            xinst_repo.upsert(s, "to_del", "temp", "temp.example.com", 9000)
            s.commit()
        with sync_session() as s:
            assert xinst_repo.delete(s, "to_del") is True
            s.commit()
        with sync_session() as s:
            assert xinst_repo.get(s, "to_del") is None

    def test_delete_not_found(self, fresh_db):
        with sync_session() as s:
            assert xinst_repo.delete(s, "nonexistent") is False

    def test_list_all(self, fresh_db):
        with sync_session() as s:
            xinst_repo.upsert(s, "inst_b", "b", "b.example.com", 9000)
            xinst_repo.upsert(s, "inst_a", "a", "a.example.com", 9000)
            s.commit()
        with sync_session() as s:
            rows = xinst_repo.list_all(s)
            assert len(rows) >= 2
            assert rows[0].xinst_name == "inst_a"

    def test_upsert_with_description(self, fresh_db):
        with sync_session() as s:
            xinst_repo.upsert(s, "desc1", "prod", "p.example.com", 9000,
                              description="Production instance")
            s.commit()
        with sync_session() as s:
            row = xinst_repo.get(s, "desc1")
            assert row.description == "Production instance"


# ===========================================================================
# RemoteInstanceClient
# ===========================================================================

class TestRemoteInstanceClient:

    def test_get_job_status_success(self):
        def mock_get(url):
            assert "prod.example.com" in url
            assert "/api/v1/jobs/my_job" in url
            return {"status": "SUCCESS", "job_name": "my_job"}

        client = RemoteInstanceClient(
            host="prod.example.com", port=9000, http_get_fn=mock_get,
        )
        assert client.get_job_status("my_job") == "SUCCESS"

    def test_get_job_status_failure(self):
        def mock_get(url):
            return {"status": "FAILURE"}

        client = RemoteInstanceClient(
            host="fail.example.com", port=9000, http_get_fn=mock_get,
        )
        assert client.get_job_status("job1") == "FAILURE"

    def test_get_job_status_error_returns_none(self):
        def mock_get(url):
            raise ConnectionError("timeout")

        client = RemoteInstanceClient(
            host="err.example.com", port=9000, http_get_fn=mock_get,
        )
        assert client.get_job_status("job1") is None

    def test_get_job_status_not_found(self):
        def mock_get(url):
            raise FileNotFoundError("404")

        client = RemoteInstanceClient(
            host="nf.example.com", port=9000, http_get_fn=mock_get,
        )
        assert client.get_job_status("missing") is None


# ===========================================================================
# resolve_xinst_condition
# ===========================================================================

class TestResolveXinstCondition:

    def test_no_xinst_references(self, fresh_db):
        with sync_session() as s:
            result = resolve_xinst_condition(s, "success(local_job)", {"local_job": "SUCCESS"})
        assert result is True

    def test_xinst_success_condition(self, fresh_db, monkeypatch):
        # Set up xinst definition
        with sync_session() as s:
            xinst_repo.upsert(s, "prod", "production", "prod.example.com", 9000)
            s.commit()

        # Mock the default HTTP GET
        import autosys.engine.xinst_client as xinst_mod
        def mock_get(url):
            return {"status": "SUCCESS"}
        monkeypatch.setattr(xinst_mod, "_default_http_get", mock_get)

        with sync_session() as s:
            result = resolve_xinst_condition(
                s, "success(prod:remote_job)", {"local_job": "INACTIVE"},
            )
        assert result is True

    def test_xinst_failure_condition(self, fresh_db, monkeypatch):
        with sync_session() as s:
            xinst_repo.upsert(s, "dev", "dev", "dev.example.com", 9000)
            s.commit()

        import autosys.engine.xinst_client as xinst_mod
        def mock_get(url):
            return {"status": "FAILURE"}
        monkeypatch.setattr(xinst_mod, "_default_http_get", mock_get)

        with sync_session() as s:
            result = resolve_xinst_condition(
                s, "success(dev:remote_job)", {},
            )
        assert result is False

    def test_xinst_instance_not_found(self, fresh_db):
        with sync_session() as s:
            result = resolve_xinst_condition(
                s, "success(unknown:job)", {},
            )
        # Unknown instance → job not found → not SUCCESS → False
        assert result is False

    def test_xinst_combined_local_and_remote(self, fresh_db, monkeypatch):
        with sync_session() as s:
            xinst_repo.upsert(s, "prod", "production", "prod.example.com", 9000)
            s.commit()

        import autosys.engine.xinst_client as xinst_mod
        def mock_get(url):
            return {"status": "SUCCESS"}
        monkeypatch.setattr(xinst_mod, "_default_http_get", mock_get)

        with sync_session() as s:
            result = resolve_xinst_condition(
                s,
                "success(local_job) & success(prod:remote_job)",
                {"local_job": "SUCCESS"},
            )
        assert result is True

    def test_empty_condition(self, fresh_db):
        with sync_session() as s:
            result = resolve_xinst_condition(s, "", {})
        assert result is True

    def test_none_condition(self, fresh_db):
        with sync_session() as s:
            result = resolve_xinst_condition(s, None, {})
        assert result is True
