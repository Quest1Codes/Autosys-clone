"""
Phase 7 — PostgreSQL Production Support tests.

Tests for:
1. Dialect detection helpers (is_sqlite, is_postgresql)
2. Connection pooling configuration
3. _get_db_url async URL conversion for PostgreSQL
4. rename() FK disable/enable is dialect-aware (no PRAGMA on non-SQLite)
5. Schema creation works cross-dialect (tested with SQLite, validates no SQLite-specific SQL)

NOTE: These tests use SQLite to validate cross-dialect compatibility.
A @pytest.mark.postgresql marker is provided for integration tests against
a real PostgreSQL instance.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch, MagicMock

from autosys.db.connection import (
    _get_db_url,
    is_sqlite,
    is_postgresql,
    _pool_kwargs,
    reset_engines,
    get_sync_engine,
)
from autosys.db.schema import Base, JobRow, JobRunRow, EventQueueRow, EventHistoryRow
from autosys.db.repository import JobRepository
from autosys.db.connection import sync_session
from autosys.db.migrations import create_all_sync


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    reset_engines()
    with sync_session() as session:
        create_all_sync(session)
    yield
    reset_engines()


# ===========================================================================
# 1. Dialect detection
# ===========================================================================

class TestDialectDetection:

    def test_is_sqlite_true(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "sqlite:///tmp/test.db")
        reset_engines()
        assert is_sqlite() is True
        assert is_postgresql() is False

    def test_is_postgresql_true(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "postgresql://user:pass@localhost/autosys")
        reset_engines()
        assert is_postgresql() is True
        assert is_sqlite() is False

    def test_is_postgresql_with_asyncpg_prefix(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "postgresql+asyncpg://user:pass@localhost/autosys")
        reset_engines()
        assert is_postgresql() is True

    def test_is_postgresql_with_psycopg2_prefix(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "postgresql+psycopg2://user:pass@localhost/autosys")
        reset_engines()
        assert is_postgresql() is True


# ===========================================================================
# 2. Connection pooling
# ===========================================================================

class TestConnectionPooling:

    def test_pool_kwargs_sqlite_returns_empty(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "sqlite:///tmp/test.db")
        reset_engines()
        kwargs = _pool_kwargs()
        assert kwargs == {}

    def test_pool_kwargs_postgres_returns_config(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "postgresql://user:pass@localhost/autosys")
        reset_engines()
        kwargs = _pool_kwargs()
        assert "pool_size" in kwargs
        assert "max_overflow" in kwargs
        assert "pool_pre_ping" in kwargs
        assert "pool_recycle" in kwargs
        assert kwargs["pool_size"] == 5
        assert kwargs["max_overflow"] == 10
        assert kwargs["pool_pre_ping"] is True
        assert kwargs["pool_recycle"] == 1800

    def test_pool_kwargs_custom_values(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "postgresql://user:pass@localhost/autosys")
        monkeypatch.setenv("AUTOSYS_DB_POOL_SIZE", "20")
        monkeypatch.setenv("AUTOSYS_DB_MAX_OVERFLOW", "50")
        monkeypatch.setenv("AUTOSYS_DB_POOL_RECYCLE", "3600")
        reset_engines()
        kwargs = _pool_kwargs()
        assert kwargs["pool_size"] == 20
        assert kwargs["max_overflow"] == 50
        assert kwargs["pool_recycle"] == 3600


# ===========================================================================
# 3. URL conversion
# ===========================================================================

class TestUrlConversion:

    def test_sqlite_async_url(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "sqlite:///tmp/test.db")
        reset_engines()
        url = _get_db_url(async_=True)
        assert "aiosqlite" in url

    def test_postgresql_async_url(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "postgresql://user:pass@localhost/autosys")
        reset_engines()
        url = _get_db_url(async_=True)
        assert "asyncpg" in url
        assert "postgresql+asyncpg://" in url

    def test_postgresql_async_url_already_has_asyncpg(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "postgresql+asyncpg://user:pass@localhost/autosys")
        reset_engines()
        url = _get_db_url(async_=True)
        assert url == "postgresql+asyncpg://user:pass@localhost/autosys"

    def test_sync_url_pins_psycopg2(self, monkeypatch):
        # SQLAlchemy >= 2.1 changed the *default* DBAPI for a bare
        # "postgresql://" URL from psycopg2 to psycopg (v3); pyproject.toml
        # only pins psycopg2-binary, so a bare URL must be rewritten or a
        # fresh install starts failing with "No module named 'psycopg'".
        monkeypatch.setenv("AUTOSYS_DB_URL", "postgresql://user:pass@localhost/autosys")
        reset_engines()
        url = _get_db_url(async_=False)
        assert url == "postgresql+psycopg2://user:pass@localhost/autosys"

    def test_sync_url_already_pinned_unchanged(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "postgresql+psycopg2://user:pass@localhost/autosys")
        reset_engines()
        url = _get_db_url(async_=False)
        assert url == "postgresql+psycopg2://user:pass@localhost/autosys"

    def test_sync_sqlite_url_unchanged(self, monkeypatch):
        monkeypatch.setenv("AUTOSYS_DB_URL", "sqlite:///tmp/test.db")
        reset_engines()
        url = _get_db_url(async_=False)
        assert url == "sqlite:///tmp/test.db"


# ===========================================================================
# 4. rename() dialect-aware FK handling
# ===========================================================================

class TestRenameDialectAware:

    def test_rename_works_on_sqlite(self):
        """Verify rename still works on SQLite (uses PRAGMA)."""
        with sync_session() as session:
            job = JobRow(
                job_name="old_name",
                job_type="CMD",
                command="echo hi",
                machine="localhost",
                status=0,
            )
            session.add(job)
            session.commit()

        repo = JobRepository()
        with sync_session() as session:
            result = repo.rename(session, "old_name", "new_name")
            session.commit()
            assert result is True

        with sync_session() as session:
            assert session.get(JobRow, "new_name") is not None
            assert session.get(JobRow, "old_name") is None

    def test_rename_needs_no_superuser_on_postgres(self):
        """rename must not toggle FK checks: on PostgreSQL that needs
        superuser, and the failed SET aborted the transaction (audit ING-03).
        The real-PostgreSQL check is in tests/test_import_reconcile.py."""
        import autosys.db.repository as repo_mod
        import inspect
        source = inspect.getsource(repo_mod.JobRepository.rename)
        assert 'execute(text("SET session_replication_role' not in source


# ===========================================================================
# 5. Schema creation cross-dialect
# ===========================================================================

class TestSchemaCreation:

    def test_create_all_uses_sqlalchemy_metadata(self):
        """Verify create_all uses Base.metadata (cross-dialect compatible)."""
        import autosys.db.migrations as mig
        import inspect
        source = inspect.getsource(mig.create_all_sync)
        assert "Base.metadata.create_all" in source
        assert "Base.metadata.drop_all" in source

    def test_no_raw_sql_in_migrations(self):
        """Verify migrations don't contain raw SQLite-specific SQL."""
        import autosys.db.migrations as mig
        import inspect
        source = inspect.getsource(mig)
        assert "PRAGMA" not in source
        assert "autoincrement" not in source.lower()

    def test_all_tables_created_on_sqlite(self):
        """Verify all expected tables are created (validates schema is dialect-agnostic)."""
        from autosys.db.migrations import list_tables_sync
        tables = list_tables_sync()
        expected = [
            "ujo_job",
            "ujo_job_runs",
            "ujo_machine",
            "ujo_calendar",
            "ujo_event",
            "ujo_proc_event",
            "alarms",
        ]
        for t in expected:
            assert t in tables, f"Table {t} not found in {tables}"


# ===========================================================================
# 6. PostgreSQL integration test marker
# ===========================================================================

@pytest.mark.postgresql
class TestPostgreSQLIntegration:
    """Tests that require a real PostgreSQL instance.

    Run with: pytest tests/test_postgres_compat.py -m postgresql
    Set: AUTOSYS_DB_URL=postgresql://user:pass@localhost/autosys_test
    """

    def test_schema_creation_on_postgres(self, monkeypatch):
        pg_url = "postgresql://autosys:autosys@localhost/autosys_test"
        monkeypatch.setenv("AUTOSYS_DB_URL", pg_url)
        reset_engines()
        try:
            with sync_session() as session:
                create_all_sync(session)
            from autosys.db.migrations import list_tables_sync
            tables = list_tables_sync()
        except Exception as exc:
            pytest.skip(f"PostgreSQL not available: {exc}")
        # Outside the try: AssertionError is an Exception, so an assert inside
        # it turned a real failure into a "PostgreSQL not available" skip.
        assert "ujo_job" in tables
