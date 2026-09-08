"""
Phase 2 — JIL Subcommand Persistence tests.

Round-trip tests for every JIL directive:
  1. Parse JIL text with the directive
  2. Import via CLI (jil import)
  3. Verify DB row exists with correct attributes
  4. For delete ops, verify row is gone
  5. For rename_job, verify old name gone + new name exists + dependencies updated
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
from click.testing import CliRunner

from autosys.cli.main import autosys
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.repository import (
    jobs as job_repo,
    machines as machine_repo,
    resources as resource_repo,
    job_types as job_type_repo,
    monitors as monitor_repo,
    blobs as blob_repo,
    globs2 as glob_repo,
    xinsts as xinst_repo,
    profiles as profile_repo,
    calendars as cal_repo,
)
from autosys.db.schema import (
    JobRow, MachineRow, VirtualResourceRow, JobTypeRow, MonitorRow,
    BlobRow, GlobRow, ExternalInstanceRow, ConnectionProfileRow, CalendarRow,
)
from sqlalchemy import select


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Create a fresh in-memory DB for each test."""
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    reset_engines()
    with sync_session() as session:
        create_all_sync(session)
    yield
    reset_engines()


def _run_import(jil_text: str, tmp_path):
    """Write JIL to a temp file and run `autosys jil import` on it."""
    jil_file = tmp_path / "test.jil"
    jil_file.write_text(textwrap.dedent(jil_text))
    runner = CliRunner()
    result = runner.invoke(autosys, ["jil", "import", str(jil_file)])
    assert result.exit_code == 0, f"CLI failed: {result.output}\n{result.exception}"
    return result


# ===========================================================================
# 1. insert_job / update_job / delete_job (existing — smoke test)
# ===========================================================================

class TestJobSubcommands:

    def test_insert_job_persists(self, tmp_path):
        _run_import(
            """
            insert_job: my_cmd   job_type: CMD
            command: echo hello
            machine: localhost
            """, tmp_path,
        )
        with sync_session() as session:
            row = job_repo.get_row(session, "my_cmd")
            assert row is not None
            assert str(row.job_type) == "CMD"

    def test_delete_job_removes_row(self, tmp_path):
        _run_import(
            """
            insert_job: to_delete   job_type: CMD
            command: echo bye
            machine: localhost
            """, tmp_path,
        )
        _run_import("delete_job: to_delete", tmp_path)
        with sync_session() as session:
            assert job_repo.get_row(session, "to_delete") is None

    def test_update_job_modifies_attributes(self, tmp_path):
        _run_import(
            """
            insert_job: upd_job   job_type: CMD
            command: echo old
            machine: localhost
            """, tmp_path,
        )
        _run_import(
            """
            update_job: upd_job
            command: echo new
            """, tmp_path,
        )
        with sync_session() as session:
            row = job_repo.get_row(session, "upd_job")
            assert row.command == "echo new"


# ===========================================================================
# 2. rename_job
# ===========================================================================

class TestRenameJob:

    def test_rename_updates_job_name(self, tmp_path):
        _run_import(
            """
            insert_job: old_name   job_type: CMD
            command: echo hi
            machine: localhost
            """, tmp_path,
        )
        _run_import(
            """
            rename_job: old_name   new_name: new_name
            """, tmp_path,
        )
        with sync_session() as session:
            assert job_repo.get_row(session, "old_name") is None
            assert job_repo.get_row(session, "new_name") is not None

    def test_rename_updates_box_children(self, tmp_path):
        _run_import(
            """
            insert_job: my_box   job_type: BOX
            owner: svc

            insert_job: child1   job_type: CMD
            command: echo hi
            machine: localhost
            box_name: my_box
            """, tmp_path,
        )
        _run_import(
            """
            rename_job: my_box   new_name: renamed_box
            """, tmp_path,
        )
        with sync_session() as session:
            child = job_repo.get_row(session, "child1")
            assert child.box_name == "renamed_box"

    def test_rename_updates_condition_references(self, tmp_path):
        _run_import(
            """
            insert_job: dep_target   job_type: CMD
            command: echo hi
            machine: localhost

            insert_job: dep_consumer   job_type: CMD
            command: echo hi
            machine: localhost
            condition: s(dep_target)
            """, tmp_path,
        )
        _run_import(
            """
            rename_job: dep_target   new_name: renamed_target
            """, tmp_path,
        )
        with sync_session() as session:
            consumer = job_repo.get_row(session, "dep_consumer")
            assert "renamed_target" in consumer.condition
            assert "dep_target" not in consumer.condition


# ===========================================================================
# 3. delete_box
# ===========================================================================

class TestDeleteBox:

    def test_delete_box_removes_children(self, tmp_path):
        _run_import(
            """
            insert_job: del_box   job_type: BOX
            owner: svc

            insert_job: c1   job_type: CMD
            command: echo hi
            machine: localhost
            box_name: del_box

            insert_job: c2   job_type: CMD
            command: echo hi
            machine: localhost
            box_name: del_box
            """, tmp_path,
        )
        _run_import("delete_job: del_box   job_type: BOX", tmp_path)
        with sync_session() as session:
            assert job_repo.get_row(session, "del_box") is None
            assert job_repo.get_row(session, "c1") is None
            assert job_repo.get_row(session, "c2") is None


# ===========================================================================
# 4. override_job
# ===========================================================================

class TestOverrideJob:

    def test_override_applies_as_update(self, tmp_path):
        _run_import(
            """
            insert_job: ovr_job   job_type: CMD
            command: echo old
            machine: localhost
            """, tmp_path,
        )
        _run_import(
            """
            override_job: ovr_job   job_type: CMD
            command: echo overridden
            """, tmp_path,
        )
        with sync_session() as session:
            row = job_repo.get_row(session, "ovr_job")
            assert row.command == "echo overridden"


# ===========================================================================
# 5. insert_machine / update_machine / delete_machine
# ===========================================================================

class TestMachineSubcommands:

    def test_insert_machine_persists(self, tmp_path):
        _run_import(
            """
            insert_machine: srv01
            host: 10.0.0.1
            port: 7520
            """, tmp_path,
        )
        with sync_session() as session:
            row = machine_repo.get(session, "srv01")
            assert row is not None
            assert row.host == "10.0.0.1"
            assert row.port == 7520

    def test_update_machine_modifies(self, tmp_path):
        _run_import(
            """
            insert_machine: srv02
            host: 10.0.0.2
            port: 7520
            """, tmp_path,
        )
        _run_import(
            """
            update_machine: srv02
            host: 10.0.0.99
            port: 9999
            """, tmp_path,
        )
        with sync_session() as session:
            row = machine_repo.get(session, "srv02")
            assert row.host == "10.0.0.99"
            assert row.port == 9999

    def test_delete_machine_removes(self, tmp_path):
        _run_import(
            """
            insert_machine: srv03
            host: 10.0.0.3
            port: 7520
            """, tmp_path,
        )
        _run_import("delete_machine: srv03", tmp_path)
        with sync_session() as session:
            assert machine_repo.get(session, "srv03") is None


# ===========================================================================
# 6. insert_resource / update_resource / delete_resource
# ===========================================================================

class TestResourceSubcommands:

    def test_insert_resource_persists(self, tmp_path):
        _run_import(
            """
            insert_resource: cpu_pool
            max_load: 4
            description: CPU pool
            """, tmp_path,
        )
        with sync_session() as session:
            row = resource_repo.get(session, "cpu_pool")
            assert row is not None
            assert row.max_load == 4
            assert row.description == "CPU pool"

    def test_update_resource_modifies(self, tmp_path):
        _run_import(
            """
            insert_resource: io_pool
            max_load: 2
            """, tmp_path,
        )
        _run_import(
            """
            update_resource: io_pool
            max_load: 8
            """, tmp_path,
        )
        with sync_session() as session:
            row = resource_repo.get(session, "io_pool")
            assert row.max_load == 8

    def test_delete_resource_removes(self, tmp_path):
        _run_import(
            """
            insert_resource: tmp_res
            max_load: 1
            """, tmp_path,
        )
        _run_import("delete_resource: tmp_res", tmp_path)
        with sync_session() as session:
            assert resource_repo.get(session, "tmp_res") is None


# ===========================================================================
# 7. insert_job_type / update_job_type / delete_job_type
# ===========================================================================

class TestJobTypeSubcommands:

    def test_insert_job_type_persists(self, tmp_path):
        _run_import(
            """
            insert_job_type: MY_CUSTOM
            command: /scripts/custom.sh --input %%INPUT%%
            description: Custom ETL
            """, tmp_path,
        )
        with sync_session() as session:
            row = job_type_repo.get(session, "MY_CUSTOM")
            assert row is not None
            assert row.command_template == "/scripts/custom.sh --input %%INPUT%%"
            assert row.description == "Custom ETL"

    def test_update_job_type_modifies(self, tmp_path):
        _run_import(
            """
            insert_job_type: UPD_TYPE
            command: old.sh
            """, tmp_path,
        )
        _run_import(
            """
            update_job_type: UPD_TYPE
            command: new.sh
            """, tmp_path,
        )
        with sync_session() as session:
            row = job_type_repo.get(session, "UPD_TYPE")
            assert row.command_template == "new.sh"

    def test_delete_job_type_removes(self, tmp_path):
        _run_import(
            """
            insert_job_type: DEL_TYPE
            command: bye.sh
            """, tmp_path,
        )
        _run_import("delete_job_type: DEL_TYPE", tmp_path)
        with sync_session() as session:
            assert job_type_repo.get(session, "DEL_TYPE") is None


# ===========================================================================
# 8. insert_monbro / update_monbro / delete_monbro
# ===========================================================================

class TestMonbroSubcommands:

    def test_insert_monbro_persists(self, tmp_path):
        _run_import(
            """
            insert_monbro: file_watch1
            monbro_type: FILE_MONITOR
            """, tmp_path,
        )
        with sync_session() as session:
            row = monitor_repo.get(session, "file_watch1")
            assert row is not None
            assert row.monbro_type == "FILE_MONITOR"

    def test_delete_monbro_removes(self, tmp_path):
        _run_import(
            """
            insert_monbro: del_mon
            monbro_type: CPU_MONITOR
            """, tmp_path,
        )
        _run_import("delete_monbro: del_mon", tmp_path)
        with sync_session() as session:
            assert monitor_repo.get(session, "del_mon") is None


# ===========================================================================
# 9. insert_blob / delete_blob
# ===========================================================================

class TestBlobSubcommands:

    def test_insert_blob_persists(self, tmp_path):
        _run_import(
            """
            insert_blob: my_blob
            blob_name: my_blob
            """, tmp_path,
        )
        with sync_session() as session:
            rows = blob_repo.get(session, "my_blob")
            assert len(rows) >= 1

    def test_delete_blob_removes(self, tmp_path):
        _run_import(
            """
            insert_blob: del_blob
            blob_name: del_blob
            """, tmp_path,
        )
        _run_import("delete_blob: del_blob", tmp_path)
        with sync_session() as session:
            rows = blob_repo.get(session, "del_blob")
            assert len(rows) == 0


# ===========================================================================
# 10. insert_glob / delete_glob
# ===========================================================================

class TestGlobSubcommands:

    def test_insert_glob_persists(self, tmp_path):
        _run_import(
            """
            insert_glob: my_glob
            """, tmp_path,
        )
        with sync_session() as session:
            row = glob_repo.get(session, "my_glob")
            assert row is not None

    def test_delete_glob_removes(self, tmp_path):
        _run_import(
            """
            insert_glob: del_glob
            """, tmp_path,
        )
        _run_import("delete_glob: del_glob", tmp_path)
        with sync_session() as session:
            assert glob_repo.get(session, "del_glob") is None


# ===========================================================================
# 11. insert_xinst / update_xinst / delete_xinst
# ===========================================================================

class TestXinstSubcommands:

    def test_insert_xinst_persists(self, tmp_path):
        _run_import(
            """
            insert_xinst: prod_inst
            instance_name: prod
            host: prod.example.com
            port: 9000
            """, tmp_path,
        )
        with sync_session() as session:
            row = xinst_repo.get(session, "prod_inst")
            assert row is not None
            assert row.host == "prod.example.com"
            assert row.port == 9000

    def test_delete_xinst_removes(self, tmp_path):
        _run_import(
            """
            insert_xinst: del_xinst
            instance_name: test
            host: test.example.com
            """, tmp_path,
        )
        _run_import("delete_xinst: del_xinst", tmp_path)
        with sync_session() as session:
            assert xinst_repo.get(session, "del_xinst") is None


# ===========================================================================
# 12. insert_connectionprofile / delete_connectionprofile
# ===========================================================================

class TestConnectionProfileSubcommands:

    def test_insert_profile_persists(self, tmp_path):
        _run_import(
            """
            insert_connectionprofile: hadoop_prod
            profile_type: HADOOP
            hadoop_host: hd.example.com
            """, tmp_path,
        )
        with sync_session() as session:
            row = profile_repo.get(session, "hadoop_prod")
            assert row is not None
            assert row.profile_type == "HADOOP"

    def test_delete_profile_removes(self, tmp_path):
        _run_import(
            """
            insert_connectionprofile: del_profile
            profile_type: AWS
            """, tmp_path,
        )
        _run_import("delete_connectionprofile: del_profile", tmp_path)
        with sync_session() as session:
            assert profile_repo.get(session, "del_profile") is None


# ===========================================================================
# 13. insert_calendar / delete_calendar (via CLI)
# ===========================================================================

class TestCalendarSubcommands:

    def test_insert_calendar_persists(self, tmp_path):
        _run_import(
            """
            insert_calendar: my_cal
            """, tmp_path,
        )
        with sync_session() as session:
            row = cal_repo.get(session, "my_cal")
            assert row is not None

    def test_delete_calendar_removes(self, tmp_path):
        _run_import(
            """
            insert_calendar: del_cal
            """, tmp_path,
        )
        _run_import("delete_calendar: del_cal", tmp_path)
        with sync_session() as session:
            assert cal_repo.get(session, "del_cal") is None


# ===========================================================================
# 14. Mixed JIL file with multiple subcommand types
# ===========================================================================

class TestMixedJILFile:

    def test_mixed_directives_all_persist(self, tmp_path):
        _run_import(
            """
            insert_machine: mix_srv
            host: 10.0.0.10
            port: 7520

            insert_resource: mix_pool
            max_load: 3

            insert_job_type: MIX_TYPE
            command: /scripts/mix.sh

            insert_job: mix_job   job_type: CMD
            command: echo mixed
            machine: localhost
            """, tmp_path,
        )
        with sync_session() as session:
            assert machine_repo.get(session, "mix_srv") is not None
            assert resource_repo.get(session, "mix_pool") is not None
            assert job_type_repo.get(session, "MIX_TYPE") is not None
            assert job_repo.get_row(session, "mix_job") is not None
