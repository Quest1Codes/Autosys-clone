"""
Phase 7 — Blob & Glob Support tests.

Tests:
- BlobRepository insert/get/delete/list with content verification
- GlobRepository upsert/get/delete/list with content verification
- Glob substitution in job commands (%%GLOB:name%%)
- Blob content retrieval and verification
"""

from __future__ import annotations

import pytest
from datetime import datetime

from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.schema import JobRow, BlobRow, GlobRow
from autosys.db.repository import blobs as blob_repo, globs2 as glob_repo
from autosys.models.enums import JobStatus


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_blob_glob.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines()
    create_all_sync(drop_first=False)
    yield
    reset_engines()


def _make_job(session, name, **kwargs):
    row = JobRow(
        job_name=name,
        job_type=kwargs.get("job_type", "CMD"),
        command=kwargs.get("command", "echo hi"),
        machine=kwargs.get("machine", "localhost"),
        status=kwargs.get("status", JobStatus.INACTIVE.value),
        owner=kwargs.get("owner", "test"),
    )
    session.add(row)
    session.flush()
    return row


# ===========================================================================
# BlobRepository
# ===========================================================================

class TestBlobRepository:

    def test_insert_and_get(self, fresh_db):
        with sync_session() as s:
            _make_job(s, "blob_job")
            s.commit()
        with sync_session() as s:
            blob_id = blob_repo.insert(s, "my_blob", "echo hello", job_name="blob_job")
            s.commit()
        assert blob_id > 0
        with sync_session() as s:
            rows = blob_repo.get(s, "my_blob")
            assert len(rows) == 1
            assert rows[0].content == "echo hello"
            assert rows[0].job_name == "blob_job"

    def test_insert_without_job(self, fresh_db):
        with sync_session() as s:
            blob_id = blob_repo.insert(s, "free_blob", "some content")
            s.commit()
        assert blob_id > 0
        with sync_session() as s:
            rows = blob_repo.get(s, "free_blob")
            assert len(rows) == 1
            assert rows[0].job_name is None

    def test_delete(self, fresh_db):
        with sync_session() as s:
            blob_repo.insert(s, "del_blob", "content")
            s.commit()
        with sync_session() as s:
            count = blob_repo.delete(s, "del_blob")
            s.commit()
        assert count == 1
        with sync_session() as s:
            rows = blob_repo.get(s, "del_blob")
            assert len(rows) == 0

    def test_delete_not_found(self, fresh_db):
        with sync_session() as s:
            count = blob_repo.delete(s, "nonexistent_blob")
            s.commit()
        assert count == 0

    def test_list_all(self, fresh_db):
        with sync_session() as s:
            blob_repo.insert(s, "blob_b", "content_b")
            blob_repo.insert(s, "blob_a", "content_a")
            s.commit()
        with sync_session() as s:
            rows = blob_repo.list_all(s)
            assert len(rows) >= 2
            # Should be ordered by blob_name
            names = [r.blob_name for r in rows]
            assert "blob_a" in names
            assert "blob_b" in names

    def test_multiple_blobs_same_name(self, fresh_db):
        with sync_session() as s:
            blob_repo.insert(s, "multi", "version1")
            blob_repo.insert(s, "multi", "version2")
            s.commit()
        with sync_session() as s:
            rows = blob_repo.get(s, "multi")
            assert len(rows) == 2
        with sync_session() as s:
            count = blob_repo.delete(s, "multi")
            s.commit()
        assert count == 2

    def test_blob_content_verification(self, fresh_db):
        content = "#!/bin/bash\necho 'Hello World'\nexit 0\n"
        with sync_session() as s:
            blob_repo.insert(s, "script_blob", content)
            s.commit()
        with sync_session() as s:
            rows = blob_repo.get(s, "script_blob")
            assert rows[0].content == content


# ===========================================================================
# GlobRepository
# ===========================================================================

class TestGlobRepository:

    def test_upsert_insert(self, fresh_db):
        with sync_session() as s:
            result = glob_repo.upsert(s, "my_glob", "global content")
            s.commit()
        assert result == "inserted"
        with sync_session() as s:
            row = glob_repo.get(s, "my_glob")
            assert row is not None
            assert row.content == "global content"

    def test_upsert_update(self, fresh_db):
        with sync_session() as s:
            glob_repo.upsert(s, "upd_glob", "original")
            s.commit()
        with sync_session() as s:
            result = glob_repo.upsert(s, "upd_glob", "updated")
            s.commit()
        assert result == "updated"
        with sync_session() as s:
            row = glob_repo.get(s, "upd_glob")
            assert row.content == "updated"

    def test_delete(self, fresh_db):
        with sync_session() as s:
            glob_repo.upsert(s, "del_glob", "content")
            s.commit()
        with sync_session() as s:
            assert glob_repo.delete(s, "del_glob") is True
            s.commit()
        with sync_session() as s:
            assert glob_repo.get(s, "del_glob") is None

    def test_delete_not_found(self, fresh_db):
        with sync_session() as s:
            assert glob_repo.delete(s, "nonexistent") is False

    def test_list_all(self, fresh_db):
        with sync_session() as s:
            glob_repo.upsert(s, "glob_b", "b_content")
            glob_repo.upsert(s, "glob_a", "a_content")
            s.commit()
        with sync_session() as s:
            rows = glob_repo.list_all(s)
            assert len(rows) >= 2
            # Should be ordered by glob_name
            assert rows[0].glob_name <= rows[1].glob_name

    def test_glob_content_verification(self, fresh_db):
        content = "export PATH=/usr/local/bin:$PATH\nexport JAVA_HOME=/opt/java\n"
        with sync_session() as s:
            glob_repo.upsert(s, "env_glob", content)
            s.commit()
        with sync_session() as s:
            row = glob_repo.get(s, "env_glob")
            assert row.content == content


# ===========================================================================
# Glob substitution in job commands
# ===========================================================================

class TestGlobSubstitution:

    def test_substitute_glob_in_command(self, fresh_db):
        from autosys.engine.glob_substitution import substitute_globs

        with sync_session() as s:
            glob_repo.upsert(s, "DB_CONN", "host=db.example.com port=5432")
            s.commit()
        with sync_session() as s:
            result = substitute_globs(s, "psql %%GLOB:DB_CONN%% -c 'SELECT 1'")
        assert result == "psql host=db.example.com port=5432 -c 'SELECT 1'"

    def test_substitute_multiple_globs(self, fresh_db):
        from autosys.engine.glob_substitution import substitute_globs

        with sync_session() as s:
            glob_repo.upsert(s, "HOST", "server1.example.com")
            glob_repo.upsert(s, "PORT", "8080")
            s.commit()
        with sync_session() as s:
            result = substitute_globs(s, "curl http://%%GLOB:HOST%%:%%GLOB:PORT%%/api")
        assert result == "curl http://server1.example.com:8080/api"

    def test_substitute_no_globs(self, fresh_db):
        from autosys.engine.glob_substitution import substitute_globs

        with sync_session() as s:
            result = substitute_globs(s, "echo hello world")
        assert result == "echo hello world"

    def test_substitute_glob_not_found(self, fresh_db):
        from autosys.engine.glob_substitution import substitute_globs

        with sync_session() as s:
            result = substitute_globs(s, "echo %%GLOB:MISSING%%")
        # When glob is not found, the placeholder should remain
        assert "%%GLOB:MISSING%%" in result
