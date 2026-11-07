"""
T26: `compute_prescan()` -- the light, read-only summary meant to replace
the connectivity check's current full `/report` call (N5).
"""
from __future__ import annotations

import pytest

from autosys.analysis.prescan import compute_prescan
from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo
from autosys.db.schema import JobRow
from autosys.models.job import parse_job


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_prescan.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection as _conn
    _conn.reset_engines()
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn.reset_engines()


def _reseed(jobs: list[dict]):
    with sync_session() as s:
        s.query(JobRow).delete()
        s.commit()
    with sync_session() as s:
        for j in jobs:
            job_repo.upsert(s, parse_job(j))
        s.commit()


def _rows():
    with sync_session() as s:
        return job_repo.list_all(s)


class TestJobCountsAndTypes:
    def test_job_count_and_non_box(self):
        _reseed([
            {"job_name": "box1", "job_type": "BOX"},
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo hi", "machine": "m1", "box_name": "box1"},
        ])
        prescan = compute_prescan(_rows())
        assert prescan["job_count"] == 2
        assert prescan["non_box"] == 1

    def test_by_job_type_tally(self):
        _reseed([
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1"},
            {"job_name": "box1", "job_type": "BOX"},
        ])
        prescan = compute_prescan(_rows())
        assert prescan["by_job_type"] == {"CMD": 2, "BOX": 1}


class TestMachinesCalendarsGlobals:
    def test_machines_named_counts_distinct_machines(self):
        _reseed([
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1,m2"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m2"},
        ])
        assert compute_prescan(_rows())["machines_named"] == 2

    def test_calendars_named_counts_distinct_calendars(self):
        _reseed([
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1",
             "run_calendar": "CAL_A", "exclude_calendar": "CAL_B"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1", "run_calendar": "CAL_A"},
        ])
        assert compute_prescan(_rows())["calendars_named"] == 2

    def test_globals_named_counts_distinct_tokens(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/app/run.sh $$BATCH_DATE", "machine": "m1"}])
        assert compute_prescan(_rows())["globals_named"] == 1


class TestClockJobs:
    def test_clock_job_with_no_timezone_counts_toward_both(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1",
                   "start_times": "06:00"}])
        prescan = compute_prescan(_rows())
        assert prescan["clock_jobs"] == 1
        assert prescan["clock_no_tz"] == 1

    def test_clock_job_with_a_timezone_does_not_count_toward_no_tz(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1",
                   "start_times": "06:00", "timezone": "America/New_York"}])
        prescan = compute_prescan(_rows())
        assert prescan["clock_jobs"] == 1
        assert prescan["clock_no_tz"] == 0

    def test_condition_only_job_is_not_a_clock_job(self):
        _reseed([
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1", "condition": "s(a)"},
        ])
        assert compute_prescan(_rows())["clock_jobs"] == 0


class TestDanglingRefs:
    def test_a_condition_naming_an_unknown_job_counts_as_dangling(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1",
                   "condition": "s(ghost_job)"}])
        assert compute_prescan(_rows())["dangling_refs"] == 1

    def test_a_condition_naming_a_real_job_is_not_dangling(self):
        _reseed([
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1", "condition": "s(a)"},
        ])
        assert compute_prescan(_rows())["dangling_refs"] == 0


class TestCredentialsWindowsMainframe:
    def test_credential_shaped_command_counts(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "curl ftp://user:hunter2@host/file", "machine": "m1"}])
        assert compute_prescan(_rows())["credential_jobs"] == 1

    def test_plain_command_does_not_count_as_credential(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/bin/echo hi", "machine": "m1"}])
        assert compute_prescan(_rows())["credential_jobs"] == 0

    def test_windows_shaped_command_counts(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": r"C:\scripts\run.bat", "machine": "m1"}])
        assert compute_prescan(_rows())["windows_jobs"] == 1

    def test_box_rows_are_never_classified_or_counted_toward_windows_or_credentials(self):
        _reseed([{"job_name": "box1", "job_type": "BOX"}])
        prescan = compute_prescan(_rows())
        assert prescan["windows_jobs"] == 0
        assert prescan["credential_jobs"] == 0
        assert prescan["unclassified"] == 0


class TestFamiliesAndUnclassified:
    def test_unclassified_job_counts(self):
        _reseed([{"job_name": "a", "job_type": "CMD", "command": "/opt/frobco/bin/totally_unknown_tool",
                   "machine": "m1"}])
        assert compute_prescan(_rows())["unclassified"] == 1

    def test_mainframe_native_job_counts(self):
        _reseed([{"job_name": "a", "job_type": "ZOS", "machine": "m1"}])
        assert compute_prescan(_rows())["mainframe_native"] == 1

    def test_families_split_wrapper_and_native(self):
        _reseed([
            {"job_name": "a", "job_type": "CMD", "command": "startrfc -h host", "machine": "m1"},  # erp_sap: wrapper
            {"job_name": "b", "job_type": "SAP", "machine": "m1"},  # erp_native_sap: native
        ])
        prescan = compute_prescan(_rows())
        assert prescan["families"]["sap"] == {"wrapper": 1, "native": 1}


class TestFingerprintAndAppGroup:
    def test_fingerprint_is_stable_under_reordering(self):
        jobs = [
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1"},
        ]
        _reseed(jobs)
        fp1 = compute_prescan(_rows())["fingerprint"]
        _reseed(list(reversed(jobs)))
        fp2 = compute_prescan(_rows())["fingerprint"]
        assert fp1 == fp2

    def test_jobs_with_app_or_group_counts(self):
        _reseed([
            {"job_name": "a", "job_type": "CMD", "command": "/bin/echo a", "machine": "m1", "application": "APP1"},
            {"job_name": "b", "job_type": "CMD", "command": "/bin/echo b", "machine": "m1"},
        ])
        assert compute_prescan(_rows())["jobs_with_app_or_group"] == 1


class TestEmptyEstate:
    def test_empty_estate_does_not_crash(self):
        _reseed([])
        prescan = compute_prescan(_rows())
        assert prescan["job_count"] == 0
        assert prescan["families"] == {}
