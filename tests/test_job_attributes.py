"""
Phase 4 — Missing Job Attributes tests.

Tests for 10 new JIL attributes + 2 existing (priority, timezone):
  1. JIL parsing — attribute is parsed and coerced correctly
  2. DB persistence — import via CLI, verify DB row
  3. JIL writer round-trip — export to JIL, re-parse, verify
  4. autorep output — new columns appear
  5. REST API — JobDetailResponse includes new fields
"""

from __future__ import annotations

import textwrap
from click.testing import CliRunner

import pytest

from autosys.cli.main import autosys
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.repository import jobs as job_repo
from autosys.parser.jil_parser import JILParser
from autosys.parser.jil_writer import job_to_jil


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


def _parse(jil: str):
    return JILParser().parse_text(textwrap.dedent(jil))


def _run_import(jil_text: str, tmp_path):
    jil_file = tmp_path / "test.jil"
    jil_file.write_text(textwrap.dedent(jil_text))
    runner = CliRunner()
    result = runner.invoke(autosys, ["jil", "import", str(jil_file)])
    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
    return result


# ===========================================================================
# 1. JIL Parsing — each attribute is parsed and coerced correctly
# ===========================================================================

class TestJILParsing:

    def test_auto_delete_bool(self):
        ops = _parse("""
            insert_job: j1   job_type: CMD
            command: echo hi
            machine: localhost
            auto_delete: 1
        """)
        assert ops[0].job.auto_delete is True

    def test_auto_delete_default_false(self):
        ops = _parse("""
            insert_job: j2   job_type: CMD
            command: echo hi
            machine: localhost
        """)
        assert ops[0].job.auto_delete is False

    def test_application_string(self):
        ops = _parse("""
            insert_job: j3   job_type: CMD
            command: echo hi
            machine: localhost
            application: payroll
        """)
        assert ops[0].job.application == "payroll"

    def test_sub_application_string(self):
        ops = _parse("""
            insert_job: j4   job_type: CMD
            command: echo hi
            machine: localhost
            sub_application: night_batch
        """)
        assert ops[0].job.sub_application == "night_batch"

    def test_command_timeout_int(self):
        ops = _parse("""
            insert_job: j5   job_type: CMD
            command: echo hi
            machine: localhost
            command_timeout: 30
        """)
        assert ops[0].job.command_timeout == 30

    def test_continuous_bool(self):
        ops = _parse("""
            insert_job: j6   job_type: FILEWATCH
            watch_file: /tmp/test.txt
            continuous: 1
        """)
        assert ops[0].job.continuous is True

    def test_cpu_usage_int(self):
        ops = _parse("""
            insert_job: j7   job_type: CMD
            command: echo hi
            machine: localhost
            cpu_usage: 80
        """)
        assert ops[0].job.cpu_usage == 80

    def test_disk_space_int(self):
        ops = _parse("""
            insert_job: j8   job_type: CMD
            command: echo hi
            machine: localhost
            disk_space: 500
        """)
        assert ops[0].job.disk_space == 500

    def test_auth_string(self):
        ops = _parse("""
            insert_job: j9   job_type: CMD
            command: echo hi
            machine: localhost
            auth_string: token_abc123
        """)
        assert ops[0].job.auth_string == "token_abc123"

    def test_connection_retry_int(self):
        ops = _parse("""
            insert_job: j10   job_type: CONNECT
            machine: localhost
            connection_retry: 3
        """)
        assert ops[0].job.connection_retry == 3

    def test_connection_timeout_int(self):
        ops = _parse("""
            insert_job: j11   job_type: CONNECT
            machine: localhost
            connection_timeout: 15
        """)
        assert ops[0].job.connection_timeout == 15

    def test_priority_int(self):
        ops = _parse("""
            insert_job: j12   job_type: CMD
            command: echo hi
            machine: localhost
            priority: 10
        """)
        assert ops[0].job.priority == 10

    def test_timezone_string(self):
        ops = _parse("""
            insert_job: j13   job_type: CMD
            command: echo hi
            machine: localhost
            timezone: America/New_York
        """)
        assert ops[0].job.timezone == "America/New_York"


# ===========================================================================
# 2. DB Persistence — attributes survive import → DB → read back
# ===========================================================================

class TestDBPersistence:

    def test_all_attributes_persist(self, tmp_path):
        _run_import("""
            insert_job: db_job   job_type: CMD
            command: echo hi
            machine: localhost
            auto_delete: 1
            application: billing
            sub_application: monthly
            command_timeout: 60
            continuous: 0
            cpu_usage: 75
            disk_space: 200
            auth_string: secret_token
            connection_retry: 2
            connection_timeout: 10
            priority: 5
            timezone: UTC
        """, tmp_path)

        with sync_session() as session:
            row = job_repo.get_row(session, "db_job")
            assert row is not None
            assert row.auto_delete is True
            assert row.application == "billing"
            assert row.sub_application == "monthly"
            assert row.command_timeout == 60
            assert row.continuous is False
            assert row.cpu_usage == 75
            assert row.disk_space == 200
            assert row.auth_string == "secret_token"
            assert row.connection_retry == 2
            assert row.connection_timeout == 10
            assert row.priority == 5
            assert row.timezone == "UTC"

    def test_defaults_when_not_specified(self, tmp_path):
        _run_import("""
            insert_job: def_job   job_type: CMD
            command: echo hi
            machine: localhost
        """, tmp_path)

        with sync_session() as session:
            row = job_repo.get_row(session, "def_job")
            assert row is not None
            assert row.auto_delete is False
            assert row.application is None
            assert row.sub_application is None
            assert row.command_timeout is None
            assert row.continuous is False
            assert row.cpu_usage is None
            assert row.disk_space is None
            assert row.auth_string is None
            assert row.connection_retry is None
            assert row.connection_timeout is None

    def test_update_modifies_attributes(self, tmp_path):
        _run_import("""
            insert_job: upd_job   job_type: CMD
            command: echo hi
            machine: localhost
            application: old_app
        """, tmp_path)
        _run_import("""
            update_job: upd_job
            application: new_app
            command_timeout: 45
        """, tmp_path)

        with sync_session() as session:
            row = job_repo.get_row(session, "upd_job")
            assert row.application == "new_app"
            assert row.command_timeout == 45


# ===========================================================================
# 3. JIL Writer Round-Trip — parse → write → parse = same values
# ===========================================================================

class TestJILWriterRoundTrip:

    def test_round_trip_all_attributes(self):
        ops = _parse("""
            insert_job: rt_job   job_type: CMD
            command: echo hi
            machine: localhost
            auto_delete: 1
            application: payroll
            sub_application: weekly
            command_timeout: 120
            cpu_usage: 90
            disk_space: 1000
            auth_string: my_auth
            connection_retry: 5
            connection_timeout: 30
            priority: 8
            timezone: Europe/London
        """)
        job = ops[0].job

        jil_text = job_to_jil(job)

        # Re-parse
        ops2 = JILParser().parse_text(jil_text)
        job2 = ops2[0].job

        assert job2.auto_delete is True
        assert job2.application == "payroll"
        assert job2.sub_application == "weekly"
        assert job2.command_timeout == 120
        assert job2.cpu_usage == 90
        assert job2.disk_space == 1000
        assert job2.auth_string == "my_auth"
        assert job2.connection_retry == 5
        assert job2.connection_timeout == 30
        assert job2.priority == 8
        assert job2.timezone == "Europe/London"

    def test_bool_defaults_not_emitted(self):
        ops = _parse("""
            insert_job: be_job   job_type: CMD
            command: echo hi
            machine: localhost
        """)
        jil_text = job_to_jil(ops[0].job)
        assert "auto_delete" not in jil_text
        assert "continuous" not in jil_text


# ===========================================================================
# 4. autorep Output — new columns appear
# ===========================================================================

class TestAutorepOutput:

    def test_autorep_shows_type_and_application(self, tmp_path):
        _run_import("""
            insert_job: ar_job   job_type: CMD
            command: echo hi
            machine: localhost
            application: reporting
        """, tmp_path)

        runner = CliRunner()
        result = runner.invoke(autosys, ["autorep", "-J", "ar_job", "-q"])
        assert result.exit_code == 0
        lines = result.output.strip().split("\n")
        # TSV output: job_name, last_start, last_end, status, run_count, type, application
        parts = lines[0].split("\t")
        assert parts[0] == "ar_job"
        assert "CMD" in parts[5]
        assert "reporting" in parts[6]


# ===========================================================================
# 5. REST API — JobDetailResponse includes new fields
# ===========================================================================

class TestRestAPI:

    def test_job_detail_response_has_new_fields(self, tmp_path):
        from autosys.app_server.schemas import JobDetailResponse

        _run_import("""
            insert_job: api_job   job_type: CMD
            command: echo hi
            machine: localhost
            application: test_app
            auto_delete: 1
            command_timeout: 20
        """, tmp_path)

        with sync_session() as session:
            row = job_repo.get_row(session, "api_job")
            data = {}
            for col in row.__table__.columns:
                val = getattr(row, col.name)
                if col.name in ("start_times", "days_of_week", "start_mins"):
                    data[col.name] = val.split(",") if val else []
                else:
                    data[col.name] = val
            detail = JobDetailResponse(**data)
            assert detail.application == "test_app"
            assert detail.auto_delete is True
            assert detail.command_timeout == 20
            assert detail.sub_application is None
            assert detail.continuous is False
            assert detail.priority is None
            assert detail.timezone is None
