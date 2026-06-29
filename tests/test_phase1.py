"""
Phase 1 test suite — DB schema + Pydantic models.

What is tested
--------------
1.  Enums          — every enum has the expected members; string values match real AutoSys
2.  Job models     — valid construction, subclass dispatch, validators (required fields,
                     forbidden fields, days_of_week normalisation, start_times normalisation)
3.  JobRun model   — duration_seconds, is_terminal properties
4.  Event model    — validation rules per event_type, EventHistory inheritance
5.  Alarm model    — is_active property
6.  Calendar model — date parsing, contains(), load_from_file()
7.  VirtualResource— available_slots, is_saturated, can_accept()
8.  GlobalVariable — name uppercasing, AUTOSYS_BUILTIN_GLOBALS dict
9.  DB schema      — create_all_sync() creates all 9 tables; seed inserts localhost + calendars
10. Round-trip     — Pydantic → JobRow → SELECT → same values
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, date, timedelta, timezone
from pathlib import Path

import pytest

# Point at an in-memory SQLite DB for tests — must be set BEFORE any autosys import
os.environ.setdefault("AUTOSYS_DB_URL", "sqlite:///file::memory:?cache=shared&uri=true")

from autosys.models.enums import (
    AlarmType, DayOfWeek, EventSource, EventType,
    FtpType, JobStatus, JobType, MachineStatus, NotificationType,
)
from autosys.models.job import (
    Job, CmdJob, BoxJob, FilewatchJob, FtpJob, ConnectJob, parse_job,
)
from autosys.models.job_run import JobRun
from autosys.models.event import Event, EventHistory
from autosys.models.alarm import Alarm
from autosys.models.calendar import Calendar
from autosys.models.resource import VirtualResource
from autosys.models.global_var import GlobalVariable, AUTOSYS_BUILTIN_GLOBALS
from autosys.db.schema import (
    Base, JobRow, JobRunRow, EventQueueRow, EventHistoryRow,
    GlobalVariableRow, CalendarRow, AlarmRow, VirtualResourceRow, MachineRow,
)
from autosys.db.migrations import create_all_sync, list_tables_sync
from autosys.db.connection import sync_session


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(scope="session", autouse=True)
def fresh_db():
    """Create all tables once for the whole test session (in-memory SQLite)."""
    # Use a file-based temp DB so sync and async engines share the same file
    db_file = tempfile.mktemp(suffix=".db")
    os.environ["AUTOSYS_DB_URL"] = f"sqlite:///{db_file}"
    create_all_sync(drop_first=False)
    yield
    # Cleanup
    Path(db_file).unlink(missing_ok=True)


# ===========================================================================
# 1. Enums
# ===========================================================================

class TestEnums:

    def test_job_type_members(self):
        assert set(JobType) == {
            JobType.BOX, JobType.CMD, JobType.FTP,
            JobType.FILEWATCH, JobType.CONNECT,
        }

    def test_job_status_has_12_states(self):
        # Real AutoSys has 13 states including ACTIVATED (BOX-only)
        expected = {
            "INACTIVE", "WAIT_REPLY", "ON_HOLD", "ON_ICE", "STARTING",
            "RUNNING", "SUCCESS", "FAILURE", "TERMINATED", "RESTART",
            "REFRESH_DEPENDENCIES", "ACTIVATED", "QUE_WAIT",
        }
        assert {s.value for s in JobStatus} == expected

    def test_event_type_has_11_members(self):
        assert len(EventType) == 11

    def test_alarm_type_members(self):
        assert AlarmType.MAX_RUN_ALARM.value == "MAX_RUN_ALARM"
        assert AlarmType.HEARTBEAT_FAIL.value == "HEARTBEAT_FAIL"

    def test_day_of_week_helpers(self):
        assert len(DayOfWeek.weekdays()) == 5
        assert len(DayOfWeek.all_days()) == 7
        assert DayOfWeek.ALL.value == "all"

    def test_all_enums_are_strings(self):
        """All enums inherit from str so they serialise to plain strings in JSON."""
        for enum_cls in (JobType, JobStatus, EventType, AlarmType, DayOfWeek):
            for member in enum_cls:
                assert isinstance(member.value, str), f"{enum_cls.__name__}.{member.name}"


# ===========================================================================
# 2. Job models
# ===========================================================================

class TestJobModels:

    # --- CmdJob ---

    def test_cmd_job_valid(self):
        j = CmdJob(
            job_name="extract_sales",
            job_type="CMD",
            command="/scripts/extract.sh --date %%DATE%%",
            machine="etl-server-01",
            owner="svc_demo",
            n_retrys=2,
            alarm_if_fail=True,
            max_run_alarm=60,
        )
        assert j.job_name == "extract_sales"
        assert j.status == "INACTIVE"          # use_enum_values=True → plain string
        assert j.n_retrys == 2
        assert j.alarm_if_fail is True

    def test_cmd_job_missing_command_raises(self):
        with pytest.raises(Exception, match="command"):
            CmdJob(job_name="bad", job_type="CMD", machine="host")

    def test_cmd_job_missing_machine_raises(self):
        with pytest.raises(Exception, match="machine"):
            CmdJob(job_name="bad", job_type="CMD", command="echo hi")

    # --- BoxJob ---

    def test_box_job_valid(self):
        b = BoxJob(
            job_name="demo_etl_box",
            job_type="BOX",
            owner="svc_demo",
            start_times=["06:00"],
            days_of_week=["mo", "tu", "we", "th", "fr"],
            exclude_calendar="us_holidays",
            alarm_if_fail=True,
            max_run_alarm=120,
        )
        assert b.job_type == "BOX"
        assert "06:00" in b.start_times

    def test_box_job_with_command_raises(self):
        with pytest.raises(Exception, match="command"):
            BoxJob(job_name="bad_box", job_type="BOX", command="echo hi")

    # --- FilewatchJob ---

    def test_filewatch_job_valid(self):
        fw = FilewatchJob(
            job_name="watch_inbound",
            job_type="FILEWATCH",
            watch_file="/data/inbound/sales_%%DATE%%.csv",
            watch_file_min_size=1024,
            watch_interval=30,
            machine="etl-server-01",
        )
        assert fw.watch_file_min_size == 1024

    def test_filewatch_missing_watch_file_raises(self):
        with pytest.raises(Exception, match="watch_file"):
            FilewatchJob(job_name="bad", job_type="FILEWATCH")

    # --- FtpJob ---

    def test_ftp_job_valid(self):
        ftp = FtpJob(
            job_name="ftp_transfer",
            job_type="FTP",
            ftp_server="ftp.example.com",
            ftp_user="svc_ftp",
            ftp_type="GET",
            ftp_src="/remote/file.csv",
            ftp_dest="/local/file.csv",
        )
        assert ftp.ftp_type == "GET"

    def test_ftp_job_missing_fields_raises(self):
        with pytest.raises(Exception):
            FtpJob(job_name="bad", job_type="FTP", ftp_server="x")

    # --- parse_job factory ---

    def test_parse_job_returns_correct_subclass(self):
        assert type(parse_job({"job_name": "a", "job_type": "BOX"})).__name__ == "BoxJob"
        assert type(parse_job({"job_name": "b", "job_type": "CMD",
                                "command": "x", "machine": "m"})).__name__ == "CmdJob"
        assert type(parse_job({"job_name": "c", "job_type": "FILEWATCH",
                                "watch_file": "/f"})).__name__ == "FilewatchJob"

    # --- Normalisation validators ---

    def test_start_times_from_comma_string(self):
        j = CmdJob(
            job_name="j", job_type="CMD", command="x", machine="m",
            start_times='"06:00","18:00"',
        )
        assert j.start_times == ["06:00", "18:00"]

    def test_days_of_week_all_expands(self):
        j = CmdJob(
            job_name="j2", job_type="CMD", command="x", machine="m",
            days_of_week="all",
        )
        assert len(j.days_of_week) == 7

    def test_days_of_week_from_comma_string(self):
        j = CmdJob(
            job_name="j3", job_type="CMD", command="x", machine="m",
            days_of_week="mo,tu,we,th,fr",
        )
        assert j.days_of_week == ["mo", "tu", "we", "th", "fr"]

    def test_job_name_pattern_rejects_spaces(self):
        with pytest.raises(Exception):
            CmdJob(job_name="bad name", job_type="CMD", command="x", machine="m")

    def test_default_status_is_inactive(self):
        j = parse_job({"job_name": "x", "job_type": "BOX"})
        assert j.status == "INACTIVE"


# ===========================================================================
# 3. JobRun model
# ===========================================================================

class TestJobRunModel:

    def test_duration_seconds_none_when_not_finished(self):
        r = JobRun(job_name="my_job", start_time=datetime.utcnow())
        assert r.duration_seconds is None

    def test_duration_seconds_computed(self):
        t0 = datetime(2025, 1, 1, 6, 0, 0)
        t1 = datetime(2025, 1, 1, 6, 5, 30)
        r = JobRun(job_name="my_job", start_time=t0, end_time=t1, status="SUCCESS")
        assert r.duration_seconds == pytest.approx(330.0)

    def test_is_terminal_for_success(self):
        r = JobRun(job_name="j", status="SUCCESS")
        assert r.is_terminal is True

    def test_is_terminal_for_failure(self):
        r = JobRun(job_name="j", status="FAILURE")
        assert r.is_terminal is True

    def test_is_terminal_for_terminated(self):
        r = JobRun(job_name="j", status="TERMINATED")
        assert r.is_terminal is True

    def test_not_terminal_while_running(self):
        r = JobRun(job_name="j", status="RUNNING")
        assert r.is_terminal is False

    def test_run_id_is_uuid(self):
        r = JobRun(job_name="j")
        assert len(r.run_id) == 36
        assert r.run_id.count("-") == 4

    def test_retry_count_defaults_to_zero(self):
        r = JobRun(job_name="j")
        assert r.retry_count == 0


# ===========================================================================
# 4. Event model
# ===========================================================================

class TestEventModel:

    def test_startjob_requires_job_name(self):
        with pytest.raises(Exception, match="job_name"):
            Event(event_type="STARTJOB")

    def test_change_status_requires_new_status(self):
        with pytest.raises(Exception, match="new_status"):
            Event(event_type="CHANGE_STATUS", job_name="my_job")

    def test_set_global_requires_name_and_value(self):
        with pytest.raises(Exception):
            Event(event_type="SET_GLOBAL", global_name="MY_VAR")  # missing value

    def test_valid_startjob_event(self):
        e = Event(event_type="STARTJOB", job_name="extract_sales", source="cli")
        assert e.processed is False
        assert e.event_type == "STARTJOB"
        assert len(e.event_id) == 36

    def test_valid_set_global_event(self):
        e = Event(
            event_type="SET_GLOBAL",
            global_name="RUN_DATE",
            global_value="20250625",
            source="cli",
        )
        assert e.job_name is None

    def test_valid_change_status_event(self):
        e = Event(
            event_type="CHANGE_STATUS",
            job_name="my_job",
            new_status="ON_HOLD",
        )
        assert e.new_status == "ON_HOLD"

    def test_force_startjob_requires_job_name(self):
        with pytest.raises(Exception, match="job_name"):
            Event(event_type="FORCE_STARTJOB")

    def test_event_history_inherits_event(self):
        h = EventHistory(
            event_type="KILLJOB",
            job_name="my_job",
            metadata_json='{"pid": 12345, "signal": "SIGTERM"}',
        )
        assert h.processed is False
        assert h.metadata_json is not None


# ===========================================================================
# 5. Alarm model
# ===========================================================================

class TestAlarmModel:

    def test_alarm_is_active_when_not_cleared(self):
        a = Alarm(
            job_name="failing_job",
            alarm_type="ALARM_IF_FAIL",
            message="Job failed after 3 retries",
            raised_at=datetime.utcnow(),
        )
        assert a.is_active is True

    def test_alarm_inactive_when_cleared(self):
        a = Alarm(
            job_name="failing_job",
            alarm_type="MAX_RUN_ALARM",
            message="Job exceeded 120 min",
            raised_at=datetime.utcnow(),
            cleared_at=datetime.utcnow(),
            cleared_by="ops_team",
        )
        assert a.is_active is False

    def test_alarm_id_is_uuid(self):
        a = Alarm(
            job_name="j",
            alarm_type="ALARM_IF_FAIL",
            message="x",
            raised_at=datetime.utcnow(),
        )
        assert len(a.alarm_id) == 36


# ===========================================================================
# 6. Calendar model
# ===========================================================================

class TestCalendarModel:

    def test_calendar_from_date_list(self):
        cal = Calendar(
            calendar_name="us_holidays",
            dates=[date(2025, 1, 1), date(2025, 7, 4), date(2025, 12, 25)],
        )
        assert cal.contains(date(2025, 7, 4)) is True
        assert cal.contains(date(2025, 7, 5)) is False

    def test_calendar_from_string_list(self):
        cal = Calendar(
            calendar_name="test_cal",
            dates=["2025-01-01", "2025-06-19"],
        )
        assert cal.contains(date(2025, 6, 19)) is True

    def test_calendar_from_bulk_string(self):
        """Simulates reading a raw .cal file content."""
        raw = "2025-01-01  # New Year\n2025-07-04  # Independence Day\n# comment only\n"
        cal = Calendar(calendar_name="bulk_test", dates=raw)
        assert len(cal.dates) == 2
        assert cal.contains(date(2025, 1, 1)) is True

    def test_load_from_file(self, tmp_path):
        cal_file = tmp_path / "company_holidays.cal"
        cal_file.write_text(
            "# Company holidays\n"
            "2025-01-01   # New Year\n"
            "2025-12-25   # Christmas\n"
        )
        cal = Calendar.load_from_file(str(cal_file))
        assert cal.calendar_name == "company_holidays"
        assert len(cal.dates) == 2

    def test_calendar_name_pattern(self):
        with pytest.raises(Exception):
            Calendar(calendar_name="bad name!", dates=[])


# ===========================================================================
# 7. VirtualResource model
# ===========================================================================

class TestVirtualResourceModel:

    def test_available_slots(self):
        r = VirtualResource(resource_name="etl_slots", max_load=4, current_load=1)
        assert r.available_slots == 3

    def test_is_saturated_false(self):
        r = VirtualResource(resource_name="r", max_load=4, current_load=3)
        assert r.is_saturated is False

    def test_is_saturated_true(self):
        r = VirtualResource(resource_name="r", max_load=4, current_load=4)
        assert r.is_saturated is True

    def test_can_accept_true(self):
        r = VirtualResource(resource_name="r", max_load=4, current_load=2)
        assert r.can_accept(2) is True

    def test_can_accept_false(self):
        r = VirtualResource(resource_name="r", max_load=4, current_load=3)
        assert r.can_accept(2) is False

    def test_current_load_defaults_to_zero(self):
        r = VirtualResource(resource_name="r", max_load=8)
        assert r.current_load == 0
        assert r.available_slots == 8


# ===========================================================================
# 8. GlobalVariable model
# ===========================================================================

class TestGlobalVariableModel:

    def test_name_uppercased_automatically(self):
        gv = GlobalVariable(name="run_date", value="20250625")
        assert gv.name == "RUN_DATE"

    def test_name_already_upper_unchanged(self):
        gv = GlobalVariable(name="MY_VAR", value="hello")
        assert gv.name == "MY_VAR"

    def test_builtin_globals_present(self):
        required = {"DATE", "YYYY", "MM", "DD", "TIME", "AUTORUN"}
        assert required.issubset(set(AUTOSYS_BUILTIN_GLOBALS.keys()))

    def test_builtin_globals_are_strings(self):
        for k, v in AUTOSYS_BUILTIN_GLOBALS.items():
            assert isinstance(v, str), f"AUTOSYS_BUILTIN_GLOBALS[{k!r}] is not a str"

    def test_name_pattern_rejects_spaces(self):
        with pytest.raises(Exception):
            GlobalVariable(name="bad var", value="x")


# ===========================================================================
# 9. DB schema — table creation
# ===========================================================================

class TestDBSchema:

    EXPECTED_TABLES = {
        "jobs", "job_runs", "event_queue", "event_history",
        "global_variables", "calendars", "alarms",
        "virtual_resources", "machines",
    }

    def test_all_9_tables_created(self):
        tables = set(list_tables_sync())
        missing = self.EXPECTED_TABLES - tables
        assert not missing, f"Missing tables: {missing}"

    def test_machines_table_has_localhost(self):
        with sync_session() as session:
            machine = session.get(MachineRow, "localhost")
        assert machine is not None
        assert machine.host == "127.0.0.1"
        assert machine.port == 7520

    def test_calendars_table_has_us_holidays(self):
        with sync_session() as session:
            cal = session.get(CalendarRow, "us_holidays")
        assert cal is not None
        dates = json.loads(cal.dates_json)
        assert len(dates) > 0
        # All dates should parse as ISO format
        for d in dates:
            date.fromisoformat(d)


# ===========================================================================
# 10. Round-trip: Pydantic → ORM row → SELECT → compare
# ===========================================================================

class TestRoundTrip:

    def test_job_pydantic_to_orm_and_back(self):
        """Write a CmdJob via ORM, read it back, verify key fields match."""
        pydantic_job = CmdJob(
            job_name   = "rt_extract_sales",
            job_type   = "CMD",
            command    = "/scripts/extract.sh --date %%DATE%%",
            machine    = "localhost",
            owner      = "svc_demo",
            n_retrys   = 2,
            alarm_if_fail = True,
            max_run_alarm = 60,
            start_times   = ["06:00"],
            days_of_week  = "mo,tu,we,th,fr",
            exclude_calendar = "us_holidays",
            condition  = "success(check_source_ready)",
        )

        with sync_session() as session:
            row = JobRow(
                job_name      = pydantic_job.job_name,
                job_type      = pydantic_job.job_type,
                command       = pydantic_job.command,
                machine       = pydantic_job.machine,
                owner         = pydantic_job.owner,
                n_retrys      = pydantic_job.n_retrys,
                alarm_if_fail = pydantic_job.alarm_if_fail,
                max_run_alarm = pydantic_job.max_run_alarm,
                start_times   = ",".join(pydantic_job.start_times),
                days_of_week  = ",".join(pydantic_job.days_of_week),
                exclude_calendar = pydantic_job.exclude_calendar,
                condition     = pydantic_job.condition,
                status        = pydantic_job.status,
            )
            session.add(row)

        with sync_session() as session:
            fetched = session.get(JobRow, "rt_extract_sales")

        assert fetched is not None
        assert fetched.job_name   == "rt_extract_sales"
        assert fetched.command    == "/scripts/extract.sh --date %%DATE%%"
        assert fetched.n_retrys   == 2
        assert fetched.alarm_if_fail is True
        assert fetched.status     == "INACTIVE"
        assert "06:00" in fetched.start_times
        assert fetched.condition  == "success(check_source_ready)"

    def test_event_queue_write_and_read(self):
        """Write an event to the queue, read it back unprocessed."""
        import uuid as _uuid
        eid = str(_uuid.uuid4())

        with sync_session() as session:
            session.add(EventQueueRow(
                event_id   = eid,
                event_type = "STARTJOB",
                job_name   = "rt_extract_sales",
                source     = "cli",
            ))

        with sync_session() as session:
            row = session.get(EventQueueRow, eid)

        assert row is not None
        assert row.event_type == "STARTJOB"
        assert row.processed  is False

    def test_global_variable_write_and_read(self):
        with sync_session() as session:
            session.add(GlobalVariableRow(
                name       = "RUN_DATE",
                value      = "20250625",
                updated_by = "test",
            ))

        with sync_session() as session:
            gv = session.get(GlobalVariableRow, "RUN_DATE")

        assert gv is not None
        assert gv.value == "20250625"
