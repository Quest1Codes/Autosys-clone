"""Tests for parser/run_history_ingest.py — see its module docstring for the
contract. Covers: idempotency (the central correctness property), timezone
handling, status/column aliasing, unknown-job routing, format detection,
and the row-level failure modes documented there."""
from __future__ import annotations

import io

import pytest
from sqlalchemy import select, func

from autosys.db.connection import sync_session
from autosys.db.schema import JobRow, JobRunRow, RunImportRow
from autosys.parser.run_history_ingest import ingest_run_history_stream


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_run_history.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.db import connection as _conn
    _conn.reset_engines()
    from autosys.db.migrations import create_all_sync
    create_all_sync(drop_first=False)
    yield
    _conn.reset_engines()


def _seed_jobs(session, *names):
    for name in names:
        session.add(JobRow(job_name=name, job_type="CMD"))
    session.commit()


def _ingest(session, text: str, path="test.csv", **kwargs):
    return ingest_run_history_stream(session, io.BytesIO(text.encode("utf-8")), path, **kwargs)


# ---------------------------------------------------------------------------
# The central property
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_reimport_identical_file_inserts_nothing_twice(self):
        csv = ("job_name,status,start_time,end_time\n"
              "a,SU,2026-01-01T09:00:00Z,2026-01-01T09:05:00Z\n"
              "a,FA,2026-01-02T09:00:00Z,2026-01-02T09:05:00Z\n")
        with sync_session() as session:
            _seed_jobs(session, "a")
            r1 = _ingest(session, csv)
            assert r1.n_rows_loaded == 2
            assert r1.n_rows_duplicate == 0

            r2 = _ingest(session, csv)
            assert r2.n_rows_loaded == 0
            assert r2.n_rows_duplicate == 2

            total = session.scalar(select(func.count()).select_from(JobRunRow))
            assert total == 2

    def test_partial_overlap_only_inserts_the_new_rows(self):
        day1 = "job_name,status,start_time\na,SU,2026-01-01T09:00:00Z\n"
        day1and2 = ("job_name,status,start_time\n"
                   "a,SU,2026-01-01T09:00:00Z\n"
                   "a,SU,2026-01-02T09:00:00Z\n")
        with sync_session() as session:
            _seed_jobs(session, "a")
            _ingest(session, day1)
            r2 = _ingest(session, day1and2)
            assert r2.n_rows_loaded == 1          # only day 2 is new
            assert r2.n_rows_duplicate == 1        # day 1 no-ops
            total = session.scalar(select(func.count()).select_from(JobRunRow))
            assert total == 2

    def test_two_different_jobs_same_timestamp_do_not_collide(self):
        """The run_id hash includes job_name, so two jobs that happen to
        start at the exact same instant must not be treated as the same run."""
        csv = ("job_name,status,start_time\n"
              "a,SU,2026-01-01T09:00:00Z\n"
              "b,SU,2026-01-01T09:00:00Z\n")
        with sync_session() as session:
            _seed_jobs(session, "a", "b")
            r = _ingest(session, csv)
            assert r.n_rows_loaded == 2

    def test_on_conflict_replace_overwrites_a_duplicate_row(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            _ingest(session, "job_name,status,start_time\na,SU,2026-01-01T09:00:00Z\n")
            r = _ingest(session, "job_name,status,start_time\na,FA,2026-01-01T09:00:00Z\n",
                       on_conflict="replace")
            assert r.n_rows_loaded == 1
            row = session.execute(select(JobRunRow)).scalar_one()
            from autosys.models.enums import JobStatus
            assert row.status == JobStatus.FAILURE.value


# ---------------------------------------------------------------------------
# Timezone handling
# ---------------------------------------------------------------------------

class TestTimezone:
    def test_naive_timestamp_with_no_declared_tz_is_rejected(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time\na,SU,2026-01-01 09:00:00\n")
            assert r.status == "REJECTED"
            assert "timezone" in r.reject_reason
            assert session.scalar(select(func.count()).select_from(JobRunRow)) == 0

    def test_naive_timestamp_with_declared_tz_is_converted_to_utc(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time\na,SU,2026-01-01 09:00:00\n",
                       declared_timezone="America/New_York")
            assert r.status == "OK"
            row = session.execute(select(JobRunRow)).scalar_one()
            # EST is UTC-5 in January
            assert row.start_time.hour == 14

    def test_offset_aware_timestamp_needs_no_declared_tz(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time\na,SU,2026-01-01T09:00:00+05:00\n")
            assert r.status == "OK"
            row = session.execute(select(JobRunRow)).scalar_one()
            assert row.start_time.hour == 4   # converted to UTC

    def test_z_suffix_is_accepted(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time\na,SU,2026-01-01T09:00:00Z\n")
            assert r.status == "OK"

    def test_autosys_native_timestamp_format(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time\na,SU,01/15/2026 09:00:00\n",
                       declared_timezone="UTC")
            assert r.status == "OK"
            row = session.execute(select(JobRunRow)).scalar_one()
            assert row.start_time.month == 1 and row.start_time.day == 15

    def test_unresolvable_timezone_name_rejects_up_front(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time\na,SU,2026-01-01 09:00:00\n",
                       declared_timezone="Not/A_Real_Zone")
            assert r.status == "REJECTED"
            assert "not resolvable" in r.reject_reason


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

class TestStatus:
    @pytest.mark.parametrize("token", ["SU", "su", "SUCCESS", "success", "4"])
    def test_success_spellings_all_resolve(self, token):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, f"job_name,status,start_time\na,{token},2026-01-01T09:00:00Z\n")
            assert r.n_rows_loaded == 1
            from autosys.models.enums import JobStatus
            row = session.execute(select(JobRunRow)).scalar_one()
            assert row.status == JobStatus.SUCCESS.value

    def test_non_terminal_status_is_skipped_not_imported(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time\na,RUNNING,2026-01-01T09:00:00Z\n")
            assert r.n_rows_loaded == 0
            assert r.issue_codes.get("non_terminal_status") == 1

    def test_unrecognised_status_is_skipped_and_counted(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time\na,BOGUS,2026-01-01T09:00:00Z\n")
            assert r.n_rows_loaded == 0
            assert r.issue_codes.get("unknown_status") == 1


# ---------------------------------------------------------------------------
# Column handling: aliasing, unknown, refused
# ---------------------------------------------------------------------------

class TestColumns:
    def test_alternate_header_spellings_are_matched(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "JobName,Status,StartTime\na,SU,2026-01-01T09:00:00Z\n")
            assert r.n_rows_loaded == 1

    def test_missing_required_column_rejects_the_file(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,start_time\na,2026-01-01T09:00:00Z\n")
            assert r.status == "REJECTED"
            assert "status" in r.reject_reason

    def test_unknown_column_is_ignored_with_a_warning_not_a_failure(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time,some_future_column\n"
                                 "a,SU,2026-01-01T09:00:00Z,whatever\n")
            assert r.n_rows_loaded == 1
            assert r.issue_codes.get("unknown_column") == 1

    def test_refused_column_is_never_stored(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time,command\n"
                                 "a,SU,2026-01-01T09:00:00Z,rm -rf /secret\n")
            assert r.n_rows_loaded == 1
            row = session.execute(select(JobRunRow)).scalar_one()
            # No column on JobRunRow holds free-text command content at all --
            # this asserts the refused value never reached a text column.
            assert row.stdout_path is None and row.stderr_path is None

    def test_attempt_column_maps_to_retry_count(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            _ingest(session, "job_name,status,start_time,attempt\na,FA,2026-01-01T09:00:00Z,2\n")
            row = session.execute(select(JobRunRow)).scalar_one()
            assert row.retry_count == 2


# ---------------------------------------------------------------------------
# Unknown jobs — reconciliation against the JIL
# ---------------------------------------------------------------------------

class TestUnknownJob:
    def test_row_for_a_job_not_in_the_jil_is_counted_not_imported(self):
        with sync_session() as session:
            _seed_jobs(session, "a")      # "ghost" is never seeded
            r = _ingest(session, "job_name,status,start_time\nghost,SU,2026-01-01T09:00:00Z\n")
            assert r.n_rows_loaded == 0
            assert r.n_rows_unknown_job == 1
            assert "ghost" in r.unknown_job_samples
            assert session.scalar(select(func.count()).select_from(JobRunRow)) == 0

    def test_job_name_differing_only_by_case_still_matches(self):
        with sync_session() as session:
            _seed_jobs(session, "DAILY_LOAD_JOB")
            r = _ingest(session, "job_name,status,start_time\ndaily_load_job,SU,2026-01-01T09:00:00Z\n")
            assert r.n_rows_loaded == 1
            assert r.n_rows_unknown_job == 0
            assert r.issue_codes.get("job_name_case_mismatch") == 1
            row = session.scalar(select(JobRunRow))
            assert row.job_name == "DAILY_LOAD_JOB"  # stored under the JIL's own casing


# ---------------------------------------------------------------------------
# Durations and clock skew
# ---------------------------------------------------------------------------

class TestDuration:
    def test_duration_is_computed_from_start_and_end(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            _ingest(session, "job_name,status,start_time,end_time\n"
                             "a,SU,2026-01-01T09:00:00Z,2026-01-01T09:05:00Z\n")
            row = session.execute(select(JobRunRow)).scalar_one()
            assert row.duration_secs == 300

    def test_end_before_start_keeps_the_row_but_drops_duration(self):
        # A 3-hour reversal: too large to be a DST artifact (within 1h),
        # so this exercises the "clock_skew" branch specifically.
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time,end_time\n"
                                 "a,SU,2026-01-01T12:00:00Z,2026-01-01T09:00:00Z\n")
            assert r.n_rows_loaded == 1          # status is still real evidence
            row = session.execute(select(JobRunRow)).scalar_one()
            assert row.duration_secs is None
            assert r.issue_codes.get("clock_skew") == 1

    def test_small_end_before_start_reversal_is_flagged_as_dst(self):
        # A 5-minute reversal (within 1h) is classified separately, since a
        # genuine multi-hour clock problem and a DST fall-back are different
        # things to investigate.
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time,end_time\n"
                                 "a,SU,2026-01-01T09:05:00Z,2026-01-01T09:00:00Z\n")
            assert r.n_rows_loaded == 1
            assert r.issue_codes.get("dst_suspected") == 1

    def test_blank_end_time_is_not_an_error(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time,end_time\na,SU,2026-01-01T09:00:00Z,\n")
            assert r.n_rows_loaded == 1
            row = session.execute(select(JobRunRow)).scalar_one()
            assert row.end_time is None and row.duration_secs is None


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

class TestFormats:
    def test_tsv_is_detected(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name\tstatus\tstart_time\na\tSU\t2026-01-01T09:00:00Z\n")
            assert r.format == "tsv"
            assert r.n_rows_loaded == 1

    def test_pipe_delimited_is_detected(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name|status|start_time\na|SU|2026-01-01T09:00:00Z\n")
            assert r.format == "pipe"
            assert r.n_rows_loaded == 1

    def test_json_lines_is_detected_and_parsed(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            text = ('{"job_name": "a", "status": "SUCCESS", "start_time": "2026-01-01T09:00:00Z"}\n'
                   '{"job_name": "a", "status": "FAILURE", "start_time": "2026-01-02T09:00:00Z"}\n')
            r = _ingest(session, text)
            assert r.format == "jsonl"
            assert r.n_rows_loaded == 2

    def test_malformed_json_line_is_skipped_not_fatal(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            text = ('{"job_name": "a", "status": "SUCCESS", "start_time": "2026-01-01T09:00:00Z"}\n'
                   'not json at all\n'
                   '{"job_name": "a", "status": "FAILURE", "start_time": "2026-01-02T09:00:00Z"}\n')
            r = _ingest(session, text)
            assert r.n_rows_loaded == 2
            assert r.issue_codes.get("bad_json") == 1


# ---------------------------------------------------------------------------
# File-level rejects
# ---------------------------------------------------------------------------

class TestFileLevelRejects:
    def test_empty_file_is_rejected(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "")
            assert r.status == "REJECTED"

    def test_header_only_no_data_rows_is_rejected(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time\n")
            assert r.status == "REJECTED"
            assert "no data rows" in r.reject_reason


# ---------------------------------------------------------------------------
# RunImportRow audit trail
# ---------------------------------------------------------------------------

class TestAuditTrail:
    def test_every_attempt_gets_an_import_row_including_a_rejected_one(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            _ingest(session, "job_name,status,start_time\na,SU,2026-01-01T09:00:00Z\n", path="good.csv")
            _ingest(session, "", path="bad.csv")
            rows = session.execute(select(RunImportRow)).scalars().all()
            assert len(rows) == 2
            statuses = {r.path: r.status for r in rows}
            assert statuses["good.csv"] == "OK"
            assert statuses["bad.csv"] == "REJECTED"

    def test_same_path_can_be_imported_more_than_once(self):
        """Unlike JilFileRow.path, RunImportRow.path is not unique -- a
        recurring daily export legitimately reuses the same filename."""
        with sync_session() as session:
            _seed_jobs(session, "a")
            _ingest(session, "job_name,status,start_time\na,SU,2026-01-01T09:00:00Z\n",
                   path="daily.csv")
            _ingest(session, "job_name,status,start_time\na,SU,2026-01-02T09:00:00Z\n",
                   path="daily.csv")
            count = session.scalar(
                select(func.count()).select_from(RunImportRow).where(RunImportRow.path == "daily.csv")
            )
            assert count == 2

    def test_window_excludes_implausible_timestamps(self):
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, "job_name,status,start_time\n"
                                 "a,SU,2026-01-01T09:00:00Z\n"
                                 "a,SU,1970-01-01T00:00:00Z\n")   # implausible
            assert r.n_rows_loaded == 2       # both rows still imported
            assert r.window_start.year == 2026   # but the 1970 row isn't in the window
            assert r.issue_codes.get("implausible_timestamp") == 1


# ---------------------------------------------------------------------------
# Scoring consumes imported rows correctly (the point of the whole feature)
# ---------------------------------------------------------------------------

class TestFeedsOperationalRisk:
    def test_imported_row_never_gets_simulated_on_top(self):
        """A job with any real evidence, however thin, must never also be
        handed to the FailureInjector -- see routers/assessment.py's
        `uncovered` filter."""
        from autosys.analysis.operational_risk import fetch_run_stats
        with sync_session() as session:
            _seed_jobs(session, "a")
            _ingest(session, "job_name,status,start_time\na,SU,2026-01-01T09:00:00Z\n")
            imported = fetch_run_stats(session, ["a"], sources=("imported",))
            assert imported["a"].total_runs == 1

    def test_ten_runs_across_a_week_is_a_sufficient_sample(self):
        # Jan 1 -> Jan 8 is a full 7-day span (6 days is NOT enough -- the
        # window is last_run - first_run, so same-day start/end gives 0).
        rows = "\n".join(f"a,SU,2026-01-0{d}T09:00:00Z" for d in range(1, 9))   # 8 rows, Jan1-Jan8
        extra = "\n".join(f"a,SU,2026-01-0{d}T15:00:00Z" for d in range(1, 3))  # +2 same-day
        csv = "job_name,status,start_time\n" + rows + "\n" + extra + "\n"
        with sync_session() as session:
            _seed_jobs(session, "a")
            r = _ingest(session, csv)
            assert r.n_rows_loaded == 10
            from autosys.analysis.operational_risk import fetch_run_stats
            stats = fetch_run_stats(session, ["a"], sources=("imported",))["a"]
            assert stats.has_sufficient_sample()
