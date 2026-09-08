"""
Phase 3 test suite — CLI commands and DB repository layer.

Coverage
--------
1.  JobRepository    — upsert (insert / update), get, delete, list, pattern
2.  EventRepository  — enqueue, dequeue_pending, mark_processed
3.  GlobalVarRepository — set, get, as_dict
4.  JIL writer       — job_to_jil, jobs_to_jil round-trip
5.  CLI: jil import  — full file, dry-run, quiet, second import = update
6.  CLI: jil export  — single job, --all
7.  CLI: jil validate — valid file, parse error exit code
8.  CLI: sendevent   — STARTJOB, SET_GLOBAL, missing -J error, missing job warning
9.  CLI: autorep     — single job, %, TSV quiet mode
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

import pytest
from click.testing import CliRunner

from autosys.cli.main import autosys
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.repository import (
    JobRepository, EventRepository, GlobalVarRepository,
    jobs as job_repo, events as event_repo, globs as glob_repo,
)
from autosys.models.job import CmdJob, BoxJob
from autosys.models.event import Event
from autosys.parser.jil_writer import job_to_jil, jobs_to_jil
from autosys.parser.jil_parser import parse_jil

_DEMO_JIL = Path(__file__).parents[1] / "examples" / "demo_etl.jil"

# ===========================================================================
# Test DB fixture
# ===========================================================================

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    """
    Each test gets its own fresh SQLite DB so tests don't interfere.

    Sets AUTOSYS_DB_URL to a temp file, resets the engine cache so the
    new URL is picked up immediately, and creates the schema.
    """
    db_path = tmp_path / "test_autosys.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("AUTOSYS_DB_URL", url)
    reset_engines()          # discard any previously cached engine
    create_all_sync(drop_first=False)
    yield url
    reset_engines()          # clean up after the test


# ===========================================================================
# 1. JobRepository
# ===========================================================================

class TestJobRepository:

    def _cmd_job(self, name="extract_sales"):
        return CmdJob(
            job_name="extract_sales" if name == "extract_sales" else name,
            job_type="CMD",
            command="/scripts/extract.sh",
            machine="localhost",
            owner="svc_demo",
        )

    def _box_job(self, name="demo_etl_box"):
        return BoxJob(
            job_name=name,
            job_type="BOX",
            owner="svc_demo",
        )

    def test_insert_returns_inserted(self):
        with sync_session() as session:
            action = job_repo.upsert(session, self._cmd_job())
        assert action == "inserted"

    def test_second_upsert_returns_updated(self):
        with sync_session() as session:
            job_repo.upsert(session, self._cmd_job())
        with sync_session() as session:
            action = job_repo.upsert(session, self._cmd_job())
        assert action == "updated"

    def test_get_returns_job_after_insert(self):
        with sync_session() as session:
            job_repo.upsert(session, self._cmd_job())
        with sync_session() as session:
            result = job_repo.get(session, "extract_sales")
        assert result is not None
        assert result.job_name == "extract_sales"

    def test_get_returns_none_for_unknown(self):
        with sync_session() as session:
            result = job_repo.get(session, "no_such_job")
        assert result is None

    def test_delete_returns_true_when_found(self):
        with sync_session() as session:
            job_repo.upsert(session, self._cmd_job())
        with sync_session() as session:
            result = job_repo.delete(session, "extract_sales")
        assert result is True

    def test_delete_returns_false_when_missing(self):
        with sync_session() as session:
            result = job_repo.delete(session, "no_such_job")
        assert result is False

    def test_list_all_returns_all_jobs(self):
        with sync_session() as session:
            job_repo.upsert(session, self._cmd_job("job_a"))
            job_repo.upsert(session, self._cmd_job("job_b"))
            job_repo.upsert(session, self._box_job("box_x"))
        with sync_session() as session:
            rows = job_repo.list_all(session)
        names = {r.job_name for r in rows}
        # localhost machine is seeded but not a job — only our 3 jobs
        assert "job_a" in names
        assert "job_b" in names
        assert "box_x" in names

    def test_list_by_pattern_wildcard(self):
        with sync_session() as session:
            job_repo.upsert(session, self._cmd_job("etl_extract"))
            job_repo.upsert(session, self._cmd_job("etl_load"))
            job_repo.upsert(session, self._cmd_job("report_gen"))
        with sync_session() as session:
            rows = job_repo.list_by_pattern(session, "etl%")
        assert len(rows) == 2
        assert all(r.job_name.startswith("etl") for r in rows)

    def test_update_preserves_status(self):
        with sync_session() as session:
            job_repo.upsert(session, self._cmd_job())
        # In a separate session, set status to RUNNING (simulates a running job)
        with sync_session() as session:
            row = job_repo.get_row(session, "extract_sales")
            row.status=1
        # Re-import the job (simulates update_job JIL stanza)
        with sync_session() as session:
            job_repo.upsert(session, self._cmd_job())
        # Status should still be RUNNING (runtime state preserved)
        with sync_session() as session:
            row = job_repo.get_row(session, "extract_sales")
        assert row.status == 1

    def test_days_of_week_list_round_trips(self):
        box = BoxJob(
            job_name="sched_box",
            job_type="BOX",
            days_of_week=["mo", "tu", "we", "th", "fr"],
        )
        with sync_session() as session:
            job_repo.upsert(session, box)
        with sync_session() as session:
            result = job_repo.get(session, "sched_box")
        assert result.days_of_week == ["mo", "tu", "we", "th", "fr"]

    def test_start_times_list_round_trips(self):
        box = BoxJob(
            job_name="timed_box",
            job_type="BOX",
            start_times=["06:00", "18:00"],
        )
        with sync_session() as session:
            job_repo.upsert(session, box)
        with sync_session() as session:
            result = job_repo.get(session, "timed_box")
        assert result.start_times == ["06:00", "18:00"]


# ===========================================================================
# 2. EventRepository
# ===========================================================================

class TestEventRepository:

    def _event(self, etype="STARTJOB", job="extract_sales"):
        return Event(event_type=etype, job_name=job, source="cli")

    def test_enqueue_returns_uuid(self):
        with sync_session() as session:
            eid = event_repo.enqueue(session, self._event())
        assert len(eid) == 36   # UUID

    def test_dequeue_pending_returns_event(self):
        with sync_session() as session:
            event_repo.enqueue(session, self._event())
        with sync_session() as session:
            rows = event_repo.dequeue_pending(session)
        assert len(rows) == 1
        assert rows[0].event_type == "STARTJOB"

    def test_mark_processed_removes_from_pending(self):
        with sync_session() as session:
            eid = event_repo.enqueue(session, self._event())
        with sync_session() as session:
            event_repo.mark_processed(session, eid)
        with sync_session() as session:
            rows = event_repo.dequeue_pending(session)
        assert rows == []

    def test_enqueue_writes_to_history(self):
        from autosys.db.schema import EventHistoryRow
        from sqlalchemy import select
        with sync_session() as session:
            eid = event_repo.enqueue(session, self._event())
        with sync_session() as session:
            hist = session.scalar(
                select(EventHistoryRow).where(EventHistoryRow.event_id == eid)
            )
        assert hist is not None
        assert hist.event_type == "STARTJOB"

    def test_set_global_event_has_global_name(self):
        ev = Event(
            event_type="SET_GLOBAL",
            global_name="RUN_DATE",
            global_value="20260625",
            source="cli",
        )
        with sync_session() as session:
            event_repo.enqueue(session, ev)
        with sync_session() as session:
            rows = event_repo.dequeue_pending(session)
        assert rows[0].global_name  == "RUN_DATE"
        assert rows[0].global_value == "20260625"


# ===========================================================================
# 3. GlobalVarRepository
# ===========================================================================

class TestGlobalVarRepository:

    def test_set_and_get(self):
        with sync_session() as session:
            glob_repo.set(session, "MY_DATE", "20260625")
        with sync_session() as session:
            val = glob_repo.get(session, "MY_DATE")
        assert val == "20260625"

    def test_name_uppercased(self):
        with sync_session() as session:
            glob_repo.set(session, "my_var", "hello")
        with sync_session() as session:
            val = glob_repo.get(session, "MY_VAR")
        assert val == "hello"

    def test_second_set_updates_value(self):
        with sync_session() as session:
            glob_repo.set(session, "X", "first")
        with sync_session() as session:
            glob_repo.set(session, "X", "second")
        with sync_session() as session:
            val = glob_repo.get(session, "X")
        assert val == "second"

    def test_get_missing_returns_none(self):
        with sync_session() as session:
            val = glob_repo.get(session, "NO_SUCH_VAR")
        assert val is None

    def test_as_dict(self):
        with sync_session() as session:
            glob_repo.set(session, "A", "1")
            glob_repo.set(session, "B", "2")
        with sync_session() as session:
            d = glob_repo.as_dict(session)
        assert d["A"] == "1"
        assert d["B"] == "2"


# ===========================================================================
# 4. JIL writer
# ===========================================================================

class TestJILWriter:

    def test_box_job_header(self):
        job = BoxJob(job_name="my_box", job_type="BOX", owner="svc_demo")
        jil = job_to_jil(job)
        assert jil.startswith("insert_job: my_box   job_type: BOX")

    def test_cmd_job_header(self):
        job = CmdJob(job_name="my_cmd", job_type="CMD",
                     command="/scripts/run.sh", machine="localhost")
        jil = job_to_jil(job)
        assert jil.startswith("insert_job: my_cmd   job_type: CMD")

    def test_command_emitted(self):
        job = CmdJob(job_name="j", job_type="CMD",
                     command="/scripts/run.sh", machine="localhost")
        jil = job_to_jil(job)
        assert "command: /scripts/run.sh" in jil

    def test_machine_emitted(self):
        job = CmdJob(job_name="j", job_type="CMD",
                     command="/scripts/run.sh", machine="etl-server-01")
        jil = job_to_jil(job)
        assert "machine: etl-server-01" in jil

    def test_boolean_true_emitted_as_1(self):
        job = BoxJob(job_name="b", job_type="BOX", alarm_if_fail=True)
        jil = job_to_jil(job)
        assert "alarm_if_fail: 1" in jil

    def test_boolean_false_default_not_emitted(self):
        job = BoxJob(job_name="b", job_type="BOX", alarm_if_fail=False)
        jil = job_to_jil(job)
        assert "alarm_if_fail" not in jil

    def test_int_default_not_emitted(self):
        job = CmdJob(job_name="j", job_type="CMD",
                     command="x", machine="m", n_retrys=0)
        jil = job_to_jil(job)
        assert "n_retrys" not in jil   # 0 is default → skip

    def test_nondefault_int_emitted(self):
        job = CmdJob(job_name="j", job_type="CMD",
                     command="x", machine="m", n_retrys=3)
        jil = job_to_jil(job)
        assert "n_retrys: 3" in jil

    def test_days_of_week_comma_joined(self):
        job = BoxJob(job_name="b", job_type="BOX",
                     days_of_week=["mo", "tu", "we", "th", "fr"])
        jil = job_to_jil(job)
        assert "days_of_week: mo,tu,we,th,fr" in jil

    def test_start_times_quoted(self):
        job = BoxJob(job_name="b", job_type="BOX", start_times=["06:00"])
        jil = job_to_jil(job)
        assert 'start_times: "06:00"' in jil

    def test_update_op(self):
        job = BoxJob(job_name="b", job_type="BOX")
        jil = job_to_jil(job, op="update")
        assert jil.startswith("update_job:")

    def test_jobs_to_jil_multiple(self):
        jobs = [
            BoxJob(job_name="box1", job_type="BOX"),
            CmdJob(job_name="cmd1", job_type="CMD",
                   command="x", machine="m"),
        ]
        text = jobs_to_jil(jobs)
        assert "insert_job: box1" in text
        assert "insert_job: cmd1" in text
        # Blank line between stanzas
        assert "\n\n" in text

    def test_jobs_to_jil_round_trip_parseable(self):
        """Export jobs to JIL, re-parse — should produce the same job names."""
        jobs_in = [
            BoxJob(job_name="my_box", job_type="BOX", owner="svc_demo"),
            CmdJob(job_name="my_cmd", job_type="CMD",
                   command="/x", machine="localhost"),
        ]
        jil_text = jobs_to_jil(jobs_in)
        ops = parse_jil(jil_text)
        names_out = {op.job.job_name for op in ops}
        assert names_out == {"my_box", "my_cmd"}

    def test_none_attrs_not_emitted(self):
        job = CmdJob(job_name="j", job_type="CMD",
                     command="x", machine="m",
                     description=None, owner=None)
        jil = job_to_jil(job)
        assert "description" not in jil
        assert "owner" not in jil


# ===========================================================================
# 5. CLI: jil import
# ===========================================================================

class TestCLIJilImport:

    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(autosys, list(args), catch_exceptions=False)

    def test_import_demo_jil_exit_0(self):
        result = self._run("jil", "import", str(_DEMO_JIL))
        assert result.exit_code == 0

    def test_import_reports_7_jobs(self):
        result = self._run("jil", "import", str(_DEMO_JIL))
        assert "7 jobs imported" in result.output

    def test_import_shows_inserted(self):
        result = self._run("jil", "import", str(_DEMO_JIL))
        assert "INSERTED" in result.output

    def test_import_second_time_shows_updated(self):
        self._run("jil", "import", str(_DEMO_JIL))
        result = self._run("jil", "import", str(_DEMO_JIL))
        assert "UPDATED" in result.output

    def test_import_dry_run_does_not_persist(self):
        self._run("jil", "import", "--dry-run", str(_DEMO_JIL))
        with sync_session() as session:
            rows = job_repo.list_all(session)
        # Only the localhost machine row is seeded — no jobs
        assert len(rows) == 0

    def test_import_dry_run_output_says_validation_ok(self):
        result = self._run("jil", "import", "--dry-run", str(_DEMO_JIL))
        assert "Validation OK" in result.output

    def test_import_quiet_flag_no_per_job_lines(self):
        result = self._run("jil", "import", "--quiet", str(_DEMO_JIL))
        assert "INSERTED" not in result.output
        assert "7 jobs imported" in result.output

    def test_import_persists_to_db(self):
        self._run("jil", "import", str(_DEMO_JIL))
        with sync_session() as session:
            job = job_repo.get(session, "demo_etl_box")
        assert job is not None
        assert job.job_type == "BOX"

    def test_import_cmd_job_command_preserved(self):
        self._run("jil", "import", str(_DEMO_JIL))
        with sync_session() as session:
            job = job_repo.get(session, "check_source_ready")
        assert "%%DATE%%" in job.command


# ===========================================================================
# 6. CLI: jil export
# ===========================================================================

class TestCLIJilExport:

    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(autosys, list(args), catch_exceptions=False)

    def test_export_single_job(self):
        # First import
        self._run("jil", "import", str(_DEMO_JIL))
        result = self._run("jil", "export", "demo_etl_box")
        assert result.exit_code == 0
        assert "insert_job: demo_etl_box" in result.output
        assert "job_type: BOX" in result.output

    def test_export_missing_job_exits_nonzero(self):
        result = self._run("jil", "export", "no_such_job")
        assert result.exit_code != 0

    def test_export_all_includes_all_jobs(self):
        self._run("jil", "import", str(_DEMO_JIL))
        result = self._run("jil", "export", "--all", "ignored")
        assert result.exit_code == 0
        assert "demo_etl_box" in result.output
        assert "nightly_cleanup" in result.output

    def test_export_contains_condition(self):
        self._run("jil", "import", str(_DEMO_JIL))
        result = self._run("jil", "export", "extract_sales")
        assert "condition:" in result.output
        assert "success(check_source_ready)" in result.output


# ===========================================================================
# 7. CLI: jil validate
# ===========================================================================

class TestCLIJilValidate:

    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(autosys, list(args), catch_exceptions=False)

    def test_validate_valid_file_exits_0(self):
        result = self._run("jil", "validate", str(_DEMO_JIL))
        assert result.exit_code == 0

    def test_validate_output_says_valid(self):
        result = self._run("jil", "validate", str(_DEMO_JIL))
        assert "JIL file is valid" in result.output

    def test_validate_shows_ok_per_job(self):
        result = self._run("jil", "validate", str(_DEMO_JIL))
        assert "OK" in result.output

    def test_validate_does_not_write_to_db(self):
        self._run("jil", "validate", str(_DEMO_JIL))
        with sync_session() as session:
            rows = job_repo.list_all(session)
        assert len(rows) == 0

    def test_validate_invalid_file_exits_nonzero(self, tmp_path):
        bad_jil = tmp_path / "bad.jil"
        bad_jil.write_text("insert_job: bad_job   job_type: CMD\n"
                           "command: /x\n"
                           # Missing required 'machine' for CMD job
                           )
        result = self._run("jil", "validate", str(bad_jil))
        assert result.exit_code != 0


# ===========================================================================
# 8. CLI: sendevent
# ===========================================================================

class TestCLISendEvent:

    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(autosys, list(args), catch_exceptions=False)

    def test_startjob_exit_0(self):
        result = self._run(
            "sendevent", "-E", "STARTJOB", "-J", "extract_sales"
        )
        assert result.exit_code == 0

    def test_startjob_shows_event_queued(self):
        result = self._run(
            "sendevent", "-E", "STARTJOB", "-J", "extract_sales"
        )
        assert "Event queued" in result.output
        assert "STARTJOB" in result.output

    def test_startjob_shows_event_id(self):
        result = self._run(
            "sendevent", "-E", "STARTJOB", "-J", "extract_sales"
        )
        assert "event_id" in result.output

    def test_startjob_missing_j_exits_nonzero(self):
        result = self._run("sendevent", "-E", "STARTJOB")
        assert result.exit_code != 0

    def test_startjob_enqueues_in_db(self):
        self._run("sendevent", "-E", "STARTJOB", "-J", "extract_sales")
        with sync_session() as session:
            rows = event_repo.dequeue_pending(session)
        assert any(r.event_type == "STARTJOB" for r in rows)

    def test_set_global_enqueues(self):
        result = self._run(
            "sendevent", "-E", "SET_GLOBAL",
            "-G", "RUN_DATE", "-v", "20260625"
        )
        assert result.exit_code == 0
        assert "SET_GLOBAL" in result.output

    def test_set_global_missing_g_exits_nonzero(self):
        result = self._run("sendevent", "-E", "SET_GLOBAL", "-v", "val")
        assert result.exit_code != 0

    def test_unknown_job_shows_warning_not_error(self):
        result = self._run(
            "sendevent", "-E", "STARTJOB", "-J", "no_such_job"
        )
        # Should still exit 0 — event is queued even for unknown jobs
        assert result.exit_code == 0

    def test_change_status_requires_s(self):
        result = self._run(
            "sendevent", "-E", "CHANGE_STATUS", "-J", "some_job"
        )
        assert result.exit_code != 0


# ===========================================================================
# 9. CLI: autorep
# ===========================================================================

class TestCLIAutorep:

    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(autosys, list(args), catch_exceptions=False)

    def test_autorep_single_job(self):
        self._run("jil", "import", str(_DEMO_JIL))
        result = self._run("autorep", "-J", "demo_etl_box")
        assert result.exit_code == 0
        assert "demo_etl_box" in result.output

    def test_autorep_all_shows_7_jobs(self):
        self._run("jil", "import", str(_DEMO_JIL))
        result = self._run("autorep", "-J", "%")
        assert result.exit_code == 0
        # All 7 job names should appear
        for name in [
            "demo_etl_box", "check_source_ready", "extract_sales",
            "generate_report", "load_to_warehouse", "send_success_email",
            "nightly_cleanup",
        ]:
            assert name in result.output

    def test_autorep_status_code_shown(self):
        self._run("jil", "import", str(_DEMO_JIL))
        result = self._run("autorep", "-J", "%")
        # "IN" is the two-letter code for INACTIVE
        assert "IN" in result.output

    def test_autorep_no_match_exits_0(self):
        result = self._run("autorep", "-J", "no_such_job")
        assert result.exit_code == 0

    def test_autorep_wildcard_pattern(self):
        self._run("jil", "import", str(_DEMO_JIL))
        result = self._run("autorep", "-J", "demo%")
        assert "demo_etl_box" in result.output
        # Jobs without "demo_" prefix should NOT appear
        assert "nightly_cleanup" not in result.output

    def test_autorep_quiet_tsv(self):
        self._run("jil", "import", str(_DEMO_JIL))
        result = self._run("autorep", "-J", "demo_etl_box", "-q")
        assert result.exit_code == 0
        # TSV output — tab-separated, no rich markup
        assert "\t" in result.output
        assert "demo_etl_box" in result.output
