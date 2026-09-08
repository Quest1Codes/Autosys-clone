"""Phase 10 — User-Defined Job Types tests."""
from __future__ import annotations
import pytest
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.repository import job_types as jt_repo

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'test_jt.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines(); create_all_sync(); yield; reset_engines()

class TestJobTypes:

    def test_upsert_insert(self, fresh_db):
        with sync_session() as s:
            r = jt_repo.upsert(s, "ETL_PULL", "python etl.py --source={SRC}", "ETL pull template")
            s.commit()
        assert r == "inserted"
        with sync_session() as s:
            row = jt_repo.get(s, "ETL_PULL")
            assert row.command_template == "python etl.py --source={SRC}"

    def test_upsert_update(self, fresh_db):
        with sync_session() as s:
            jt_repo.upsert(s, "CUSTOM", "echo hi", "v1"); s.commit()
        with sync_session() as s:
            r = jt_repo.upsert(s, "CUSTOM", "echo bye", "v2"); s.commit()
        assert r == "updated"
        with sync_session() as s:
            row = jt_repo.get(s, "CUSTOM")
            assert row.command_template == "echo bye"

    def test_delete(self, fresh_db):
        with sync_session() as s:
            jt_repo.upsert(s, "DEL_TYPE", "echo x"); s.commit()
        with sync_session() as s:
            assert jt_repo.delete(s, "DEL_TYPE") is True; s.commit()
        with sync_session() as s:
            assert jt_repo.get(s, "DEL_TYPE") is None

    def test_delete_not_found(self, fresh_db):
        with sync_session() as s:
            assert jt_repo.delete(s, "nope") is False

    def test_list_all(self, fresh_db):
        with sync_session() as s:
            jt_repo.upsert(s, "type_b", "cmd_b")
            jt_repo.upsert(s, "type_a", "cmd_a"); s.commit()
        with sync_session() as s:
            rows = jt_repo.list_all(s)
            assert len(rows) >= 2
            assert rows[0].type_name == "type_a"

    def test_command_template_with_placeholders(self, fresh_db):
        template = "spark-submit --class {MAIN_CLASS} {JAR_PATH} --date {DATE}"
        with sync_session() as s:
            jt_repo.upsert(s, "SPARK_JOB", template, "Spark template"); s.commit()
        with sync_session() as s:
            row = jt_repo.get(s, "SPARK_JOB")
            assert "{MAIN_CLASS}" in row.command_template


class TestCommandTemplateExpansion:

    def test_expand_placeholders(self):
        from autosys.engine.job_type_expander import expand_command_template
        result = expand_command_template("python etl.py --source={SRC} --dest={DST}",
                                          {"SRC": "mysql://db", "DST": "s3://bucket"})
        assert result == "python etl.py --source=mysql://db --dest=s3://bucket"

    def test_expand_no_placeholders(self):
        from autosys.engine.job_type_expander import expand_command_template
        assert expand_command_template("echo hello", {}) == "echo hello"

    def test_expand_missing_placeholder(self):
        from autosys.engine.job_type_expander import expand_command_template
        result = expand_command_template("echo {MISSING}", {})
        assert "{MISSING}" in result

    def test_expand_multiple_same_placeholder(self):
        from autosys.engine.job_type_expander import expand_command_template
        result = expand_command_template("echo {NAME} && echo {NAME}", {"NAME": "world"})
        assert result == "echo world && echo world"
