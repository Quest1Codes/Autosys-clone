"""
Run-history import — loading a client's real AutoSys execution history.

Companion to jil_ingest.py, but for ``ujo_job_runs`` rather than job
definitions. A freshly imported JIL has no run history, so operational risk
for every job comes from FailureInjector simulation (see
analysis/simulated_risk.py) -- a generated number, clearly labelled as such,
but still a guess. A real export from the client's AutoSys turns that guess
into evidence: rows written here carry ``JobRunRow.source = "imported"``,
which outranks both "live" (this instance's own incidental execution) and
"simulated" in routers/assessment.py's precedence merge, and which
analysis/operational_risk.fetch_run_stats can read back filtered to exactly
this provenance.

See dev/task9-cost-calculator/05-cost-estimation-questions.md §3a for the
client-facing contract this module implements, and
dev/task10-productionization/04-simulator-flaws.md F1/F2/F4 for why it
matters (fabricated failure rates and durations feeding cost/concurrency
figures that reach a commercial proposal).

Contract
--------
* **Schema is fixed, file syntax is not.** We accept whatever a client's
  *existing* tooling already produces -- a bank will not build a custom
  extraction for a vendor, and we realistically get one attempt. Two
  encodings of the same eight columns:
    - delimited text (CSV/TSV/pipe), header row required, delimiter
      auto-detected
    - JSON Lines, one object per line, same column names as keys
* **No exception escapes for content reasons**, matching jil_ingest.py's
  doctrine: a malformed row is skipped and counted, never a whole-file
  failure. The exceptions, both file-level and both about not being able to
  trust *any* row in the file rather than one row being bad:
    - the header is missing a required column
    - a naive timestamp appears with no declared timezone (silently
      assuming UTC can shift an entire estate's history by up to 14 hours)
* **No input is lost silently.** Every disposition is counted exactly
  (RunImportRow + this module's issue-code counters); a capped sample of
  rejected rows is kept for the operator to inspect, but the full 7.7M-row
  file itself is never archived verbatim the way JIL stanzas are -- unlike a
  JIL stanza, a run-history row has no lossy typed-table translation to
  recover from, and archiving millions of rows of machine names and
  timestamps a second time only doubles storage and duplicates disclosure of
  confidential data for no offsetting benefit.
* **Idempotent.** Re-importing the same file, or a file with an overlapping
  window, inserts nothing twice: see _make_run_id.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import BinaryIO, Iterator, Optional
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from loguru import logger
from sqlalchemy import func, select, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from autosys.db.schema import JobRow, JobRunRow, RunImportRow
from autosys.models.enums import JobStatus
from autosys.parser.jil_ingest import _connection_lost, decode_bytes
from autosys.scheduler.state_machine import STATUS_ABBREV

OK, WARN, REJECTED = "OK", "WARN", "REJECTED"

# A job's status, once terminal, is evidence this module can count. Anything
# else (RUNNING, STARTING, ...) describes a run that had not finished at
# export time and carries no outcome to score.
_ABBREV_TO_NAME: dict[str, str] = {v: k for k, v in STATUS_ABBREV.items()}
_TERMINAL_COUNTED: frozenset[str] = frozenset({
    "SUCCESS", "FAILURE", "TERMINATED", "ON_ICE", "ON_NOEXEC",
})

# ---------------------------------------------------------------------------
# Column contract (see the module docstring / the client-facing contract doc)
# ---------------------------------------------------------------------------

REQUIRED_COLUMNS: frozenset[str] = frozenset({"job_name", "status", "start_time"})
KNOWN_COLUMNS: frozenset[str] = REQUIRED_COLUMNS | frozenset({
    "end_time", "exit_code", "machine", "attempt", "run_id",
})

# Matched case-insensitively after stripping whitespace and collapsing
# spaces/dashes to underscores -- a header column reaches this lookup
# already normalised by _normalise_key.
_COLUMN_ALIASES: dict[str, str] = {
    "job_name": "job_name", "jobname": "job_name", "job": "job_name",
    "name": "job_name",
    "status": "status", "job_status": "status", "jobstatus": "status",
    "terminal_status": "status", "result": "status",
    "start_time": "start_time", "starttime": "start_time",
    "start": "start_time", "started_at": "start_time",
    "run_start": "start_time",
    "end_time": "end_time", "endtime": "end_time", "end": "end_time",
    "ended_at": "end_time", "run_end": "end_time", "finish_time": "end_time",
    "exit_code": "exit_code", "exitcode": "exit_code", "rc": "exit_code",
    "return_code": "exit_code",
    "machine": "machine", "host": "machine", "hostname": "machine",
    "agent": "machine",
    # Semantics, not just a spelling question: this column is stored
    # straight into JobRunRow.retry_count, and operational_risk.py's retry
    # rate is retried_runs/total_runs where retried_runs counts
    # retry_count > 0. That means it must be a RETRY count -- 0 for a
    # first/only attempt, N for N retries after it -- not a 1-indexed
    # attempt number. Sending AutoSys's own `ntry` as-is is correct only if
    # ntry is itself 0-indexed at your site; if it's 1-indexed (first try =
    # ntry 1), subtract 1 before exporting, or omit the column -- a value we
    # silently got wrong here would inflate every job's apparent retry rate
    # without any visible error. We do not guess which convention a given
    # export uses; the column is taken at face value, deliberately.
    "attempt": "attempt", "ntry": "attempt", "retry": "attempt",
    "retry_count": "attempt", "attempt_number": "attempt",
    "run_id": "run_id", "runid": "run_id", "id": "run_id", "event_id": "run_id",
}

# Present but never stored, even if a client's export includes them: one
# file-level warning naming each, the column is dropped. Command lines and
# output paths/content are exactly the kind of thing a run-history export
# should not need to carry, and we do not want to be the reason a bank's
# confidential job output ends up at rest in our database.
_REFUSED_COLUMNS: frozenset[str] = frozenset({
    "command", "cmd", "std_out_file", "std_err_file", "stdout_path",
    "stderr_path", "output", "stdout", "stderr", "message", "envvars",
    "env", "auth_string", "password", "secret", "token", "credential",
})

_AUTOSYS_TS_RE = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})\s+(\d{1,2}):(\d{2}):(\d{2})$")

# sha256 hex digest truncated to this many characters for the run_id hash --
# JobRunRow.run_id is String(255); the "imp:" prefix plus 40 hex chars is
# comfortably under that with room for the prefix and any future scheme.
_RUN_ID_HASH_LEN = 40

_MAX_ISSUE_SAMPLES = 200
_MAX_NAME_SAMPLES = 50

# Rows per INSERT statement and rows between commits. Chosen the same way
# jil_ingest.py's commit_every is: large enough that millions of rows do not
# mean millions of round trips, small enough that a connection loss partway
# through loses at most one batch's work, not the whole file. expunge_all()
# is not needed here (unlike jil_ingest's per-stanza ORM objects): rows are
# built as plain dicts and executed via Core insert(), never added to the
# session's identity map, so there is nothing to grow.
_BATCH_ROWS = 5000
_COMMIT_EVERY_ROWS = 50000

# A fixed 64-bit number distinct from jil_ingest's _IMPORT_LOCK_KEY, so a
# history import and a JIL import never block each other -- they touch
# different tables and have no row-ordering hazard between them (see
# jil_ingest._serialise_imports's docstring for why JIL import needs its own
# lock in the first place).
_HISTORY_IMPORT_LOCK_KEY = int.from_bytes(b"HISTIMPT", "big")


def _serialise_history_imports(session: Session) -> None:
    """Make concurrent run-history imports on PostgreSQL take turns, for the
    same reason jil_ingest._serialise_imports does -- see that docstring.
    A distinct lock key so this never contends with a JIL import."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"),
                        {"k": _HISTORY_IMPORT_LOCK_KEY})


def _normalise_key(raw: str) -> str:
    return re.sub(r"[\s-]+", "_", raw.strip().lower())


# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

@dataclass
class RunHistoryReport:
    path:              str
    status:            str = OK
    format:            str = ""          # "csv" | "tsv" | "pipe" | "jsonl"
    encoding:          str = ""
    declared_timezone: Optional[str] = None
    key_kind:          str = ""          # "r" (run_id given) | "t" (start_time) | "mixed"
    reject_reason:     str = ""

    window_start: Optional[datetime] = None
    window_end:   Optional[datetime] = None

    n_rows_read:        int = 0
    n_rows_loaded:       int = 0
    n_rows_skipped:      int = 0
    n_rows_duplicate:    int = 0
    n_rows_unknown_job:  int = 0
    n_jobs:              int = 0

    issue_codes: dict[str, int] = field(default_factory=dict)
    issue_samples: list[dict] = field(default_factory=list)
    unknown_job_samples: list[str] = field(default_factory=list)

    import_id: Optional[int] = None

    # Set only when the run stopped early because the database connection
    # itself went away -- mirrors jil_ingest.IngestSummary's aborted/
    # abort_reason/resume-point fields.
    aborted:       bool = False
    abort_reason:  str = ""

    def _bump(self, code: str) -> None:
        self.issue_codes[code] = self.issue_codes.get(code, 0) + 1

    def _note(self, code: str, row: int, message: str, **extra) -> None:
        self._bump(code)
        if len(self.issue_samples) < _MAX_ISSUE_SAMPLES:
            self.issue_samples.append({"code": code, "row": row, "message": message, **extra})

    def as_dict(self) -> dict:
        return {
            "path": self.path, "status": self.status, "format": self.format,
            "encoding": self.encoding, "declared_timezone": self.declared_timezone,
            "key_kind": self.key_kind, "reject_reason": self.reject_reason,
            "window_start": self.window_start.isoformat() if self.window_start else None,
            "window_end": self.window_end.isoformat() if self.window_end else None,
            "n_rows_read": self.n_rows_read, "n_rows_loaded": self.n_rows_loaded,
            "n_rows_skipped": self.n_rows_skipped, "n_rows_duplicate": self.n_rows_duplicate,
            "n_rows_unknown_job": self.n_rows_unknown_job, "n_jobs": self.n_jobs,
            "issue_codes": self.issue_codes, "issue_samples": self.issue_samples,
            "unknown_job_samples": self.unknown_job_samples,
            "import_id": self.import_id,
            "aborted": self.aborted, "abort_reason": self.abort_reason,
        }


# ---------------------------------------------------------------------------
# Status mapping
# ---------------------------------------------------------------------------

def _status_lookup(raw: Optional[str]) -> tuple[Optional[int], str]:
    """
    Resolve a status token to (JobStatus value, classification).

    classification is one of:
      "counted"      a terminal outcome this module can score
      "non_terminal" a real status, but not a finished run (RUNNING,
                     STARTING, ON_HOLD, the internal REFRESH_DEPENDENCIES
                     state, ...) -- caught by the catch-all branch below so
                     a status this module has never heard of defaults to
                     "skip, don't count as evidence" rather than crashing
      "unknown"      not a status this module recognises at all
    """
    token = (raw or "").strip()
    if not token:
        return None, "unknown"
    upper = token.upper()
    name: Optional[str] = None
    if upper in _ABBREV_TO_NAME:
        name = _ABBREV_TO_NAME[upper]
    elif upper in JobStatus.__members__:
        name = upper
    else:
        try:
            name = JobStatus(int(token)).name
        except (ValueError, KeyError):
            name = None
    if name is None:
        return None, "unknown"
    value = JobStatus[name].value
    return (value, "counted") if name in _TERMINAL_COUNTED else (value, "non_terminal")


# ---------------------------------------------------------------------------
# Timestamp parsing
# ---------------------------------------------------------------------------

def _parse_timestamp(raw: Optional[str], declared_tz: Optional[str]) -> tuple[Optional[datetime], str]:
    """
    Return (UTC-naive datetime or None, issue_code).

    issue_code is "" for success (including a legitimately blank input --
    end_time is optional), "bad_timestamp" for a string that parses as
    neither ISO 8601 nor AutoSys's own MM/DD/YYYY HH:MM:SS, "naive_no_tz"
    for a timestamp with no UTC offset when the caller declared none (the
    one case the file-level caller turns into a hard reject, not a
    per-row skip), or "bad_timezone" if *declared_tz* itself cannot be
    resolved.
    """
    token = (raw or "").strip()
    if not token:
        return None, ""

    dt: Optional[datetime] = None
    had_offset = False
    try:
        iso_token = token[:-1] + "+00:00" if token.endswith(("Z", "z")) else token
        dt = datetime.fromisoformat(iso_token)
        had_offset = dt.tzinfo is not None
    except ValueError:
        m = _AUTOSYS_TS_RE.match(token)
        if m:
            mo, d, y, h, mi, s = (int(g) for g in m.groups())
            try:
                dt = datetime(y, mo, d, h, mi, s)
            except ValueError:
                dt = None

    if dt is None:
        return None, "bad_timestamp"
    if had_offset:
        return dt.astimezone(timezone.utc).replace(tzinfo=None), ""

    if declared_tz is None:
        return None, "naive_no_tz"
    if declared_tz.upper() == "UTC":
        return dt, ""
    try:
        localized = dt.replace(tzinfo=ZoneInfo(declared_tz))
    except ZoneInfoNotFoundError:
        return None, "bad_timezone"
    return localized.astimezone(timezone.utc).replace(tzinfo=None), ""


def _check_timezone_resolvable(declared_tz: Optional[str]) -> Optional[str]:
    """None if *declared_tz* is usable (or not given), else an error message.

    Checked once, up front, rather than letting every row fail identically --
    a server missing IANA tzdata (unlikely on this image; verified present on
    the UBI 9 micro build, but not guaranteed on every environment this ever
    runs on) should fail the file once with a clear reason, not row by row.
    """
    if declared_tz is None or declared_tz.upper() == "UTC":
        return None
    try:
        ZoneInfo(declared_tz)
    except ZoneInfoNotFoundError:
        return (f"timezone {declared_tz!r} is not resolvable on this server "
                "(unrecognised IANA name, or this server's tzdata is unavailable)")
    return None


# ---------------------------------------------------------------------------
# run_id / idempotency
# ---------------------------------------------------------------------------

def _make_run_id(job_name: str, raw_run_id: str, start_time_utc: Optional[datetime],
                  attempt: Optional[int]) -> tuple[str, str]:
    """
    Return (run_id, key_kind).

    key_kind "r": an explicit run_id was supplied in the row -- the
    strongest key, since it is the source system's own identifier for this
    exact run.
    key_kind "t": no run_id given, falls back to (job_name, start_time,
    attempt). start_time is a required column, so this branch is always
    available when "r" is not.

    Hashed (sha256, truncated) rather than stored as a readable composite:
    job_name alone can be 255 characters, which would overflow
    JobRunRow.run_id (String(255)) once combined with a timestamp and an
    "imp:" prefix. key_kind is part of the hash input specifically so that
    the same run described once with a run_id and once without (e.g. an
    export tool is swapped mid-project) produces two DIFFERENT ids rather
    than silently colliding or silently failing to dedupe -- see
    allow_key_change in ingest_run_history_stream for what happens when a
    job's key_kind changes across imports.
    """
    if raw_run_id:
        key_kind = "r"
        material = f"r\x1f{job_name}\x1f{raw_run_id}"
    else:
        key_kind = "t"
        ts = start_time_utc.isoformat() if start_time_utc else ""
        material = f"t\x1f{job_name}\x1f{ts}\x1f{attempt or 0}"
    digest = hashlib.sha256(material.encode("utf-8")).hexdigest()[:_RUN_ID_HASH_LEN]
    return f"imp:{digest}", key_kind


# ---------------------------------------------------------------------------
# Row source: delimited text or JSON Lines, normalised to the same shape
# ---------------------------------------------------------------------------

def _sniff_format_and_delimiter(first_line: str) -> tuple[str, Optional[str]]:
    """("jsonl", None) or ("csv"|"tsv"|"pipe", delimiter)."""
    stripped = first_line.strip()
    if stripped.startswith("{"):
        return "jsonl", None
    counts = {d: stripped.count(d) for d in (",", "\t", "|")}
    delim = max(counts, key=counts.get)
    if counts[delim] == 0:
        delim = ","    # single column file, or nothing recognisable -- comma is the fallback
    fmt = {",": "csv", "\t": "tsv", "|": "pipe"}[delim]
    return fmt, delim


def _delimited_rows(text_stream: Iterator[str], header_line: str, delimiter: str
                    ) -> tuple[dict[int, str], list[str], Iterator[list[str]]]:
    """
    Parse *header_line* into {column_index: canonical_name}, report any
    unknown/refused column names, and return a row iterator over the rest
    of *text_stream* using the same delimiter.
    """
    header_cells = next(csv.reader([header_line], delimiter=delimiter))
    index_to_column: dict[int, str] = {}
    unknown: list[str] = []
    for i, cell in enumerate(header_cells):
        key = _normalise_key(cell)
        if key in _REFUSED_COLUMNS:
            unknown.append(f"{cell} (refused -- not stored)")
            continue
        canonical = _COLUMN_ALIASES.get(key)
        if canonical:
            index_to_column[i] = canonical
        elif key:
            unknown.append(cell)
    return index_to_column, unknown, csv.reader(text_stream, delimiter=delimiter)


def _canonical_row_from_delimited(cells: list[str], index_to_column: dict[int, str]) -> dict[str, str]:
    row: dict[str, str] = {}
    for i, col in index_to_column.items():
        if i < len(cells):
            row[col] = cells[i]
    return row


def _canonical_row_from_json(obj: dict) -> tuple[dict[str, str], list[str]]:
    row: dict[str, str] = {}
    unknown: list[str] = []
    for raw_key, value in obj.items():
        key = _normalise_key(str(raw_key))
        if key in _REFUSED_COLUMNS:
            continue
        canonical = _COLUMN_ALIASES.get(key)
        if canonical:
            row[canonical] = "" if value is None else str(value)
        elif key:
            unknown.append(str(raw_key))
    return row, unknown


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def _jsonl_row_source(text_stream: Iterator[str], first_line: str, report: "RunHistoryReport"
                      ) -> Iterator[tuple[int, dict[str, str]]]:
    def _parse_one(n: int, line: str) -> Optional[dict[str, str]]:
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            report._note("bad_json", n, "line is not valid JSON")
            return None
        if not isinstance(obj, dict):
            report._note("bad_json", n, "line is not a JSON object")
            return None
        row, unknown = _canonical_row_from_json(obj)
        for col in unknown:
            report._note("unknown_column", n, f"field {col!r} is not recognised and was ignored")
        return row

    n = 1
    parsed = _parse_one(n, first_line)
    if parsed is not None:
        yield n, parsed
    for n, line in enumerate(text_stream, start=2):
        if not line.strip():
            continue
        parsed = _parse_one(n, line)
        if parsed is not None:
            yield n, parsed


def _delimited_row_source(row_reader: Iterator[list[str]], index_to_column: dict[int, str],
                          report: "RunHistoryReport") -> Iterator[tuple[int, dict[str, str]]]:
    for n, cells in enumerate(row_reader, start=2):      # 1 was the header
        if not cells or (len(cells) == 1 and not cells[0].strip()):
            continue
        yield n, _canonical_row_from_delimited(cells, index_to_column)


class _FileLevelReject(Exception):
    """
    Raised internally to unwind out of row processing straight to
    finalising the RunImportRow -- a hard reject (naive timestamp with no
    declared timezone, discovered mid-file) is file-level, not a per-row
    skip, so the moment it is seen nothing after it should be processed.
    report.status / report.reject_reason are set by the raiser before this
    is raised; the catcher just needs to stop and finalise.
    """


def ingest_run_history_stream(
    session: Session,
    fh: BinaryIO,
    path_label: str,
    *,
    declared_timezone: Optional[str] = None,
    on_conflict: str = "skip",
    batch_rows: int = _BATCH_ROWS,
    commit_every_rows: int = _COMMIT_EVERY_ROWS,
) -> RunHistoryReport:
    """
    Load one run-history export from *fh* (any binary file-like object --
    an open file, or FastAPI's UploadFile.file, which is already spooled to
    disk for a large upload) into ``ujo_job_runs`` with
    ``source="imported"``.

    *path_label* is a human-readable name for the audit trail (a real file
    path, or an api:history:<user>:<uuid> synthetic label for an API
    upload -- see RunImportRow.path's docstring for why it is not unique).
    Every call creates exactly one RunImportRow, including a call that gets
    rejected immediately -- a failed attempt is still part of the audit
    trail, not something that should vanish.

    *declared_timezone* is an IANA name ("America/New_York") or "UTC",
    required only if the file contains naive timestamps; a file that turns
    out to need one but wasn't given one is rejected outright (see
    _parse_timestamp) -- silently assuming UTC can shift an entire estate's
    history by up to 14 hours.

    *on_conflict*: "skip" (default) -- ON CONFLICT DO NOTHING, a duplicate
    run_id is silently not re-inserted, which is what makes re-importing
    the same file idempotent. "replace" -- ON CONFLICT DO UPDATE, for a
    corrected re-export of a file already partly loaded; use deliberately,
    since it means a second file can overwrite what a first one wrote.

    Streams throughout: never reads the whole file into memory, inserts in
    batches of *batch_rows*, commits every *commit_every_rows*. A
    multi-gigabyte file is the expected case (86K jobs x weeks of history),
    not an edge case.
    """
    report = RunHistoryReport(path=path_label, declared_timezone=declared_timezone)

    # Created first, unconditionally, status="IN_PROGRESS" -- so that every
    # call this function makes leaves exactly one durable audit row, even
    # one that is about to be rejected in the next few lines. It is
    # finalised (status OK/WARN/REJECTED, real counters) in the `finally`
    # below, UNLESS the database connection itself is lost, in which case
    # there is nothing left to write to and the row stays "IN_PROGRESS" --
    # visibly distinct from a row that completed with nothing to report.
    placeholder = RunImportRow(path=path_label, status="IN_PROGRESS",
                               declared_timezone=declared_timezone)
    session.add(placeholder)
    session.flush()
    report.import_id = placeholder.import_id
    session.commit()

    try:
        tz_problem = _check_timezone_resolvable(declared_timezone)
        if tz_problem:
            report.status = REJECTED
            report.reject_reason = tz_problem
            raise _FileLevelReject(tz_problem)

        head = fh.read(65536)
        fh.seek(0)
        if not head.strip():
            report.status = REJECTED
            report.reject_reason = "file is empty"
            raise _FileLevelReject(report.reject_reason)
        _, encoding, decode_issues = decode_bytes(head)
        report.encoding = encoding
        for issue in decode_issues:
            report._note("encoding", 0, issue["message"])

        text_stream = io.TextIOWrapper(fh, encoding=encoding, errors="replace", newline="")

        first_line = ""
        for line in text_stream:
            if line.strip():
                first_line = line
                break
        if not first_line:
            report.status = REJECTED
            report.reject_reason = "file has no non-blank lines"
            raise _FileLevelReject(report.reject_reason)

        fmt, delimiter = _sniff_format_and_delimiter(first_line)
        report.format = fmt

        if fmt == "jsonl":
            row_source = _jsonl_row_source(text_stream, first_line, report)
        else:
            try:
                index_to_column, unknown_columns, row_reader = _delimited_rows(
                    text_stream, first_line, delimiter)
            except csv.Error as exc:
                report.status = REJECTED
                report.reject_reason = f"could not parse header line: {exc}"
                raise _FileLevelReject(report.reject_reason)
            missing = REQUIRED_COLUMNS - set(index_to_column.values())
            if missing:
                report.status = REJECTED
                report.reject_reason = (
                    f"header is missing required column(s): {', '.join(sorted(missing))}"
                )
                raise _FileLevelReject(report.reject_reason)
            for col in unknown_columns:
                report._note("unknown_column", 0, f"column {col!r} is not recognised and was ignored")
            row_source = _delimited_row_source(row_reader, index_to_column, report)

        _ingest_rows(session, row_source, report, on_conflict=on_conflict,
                     batch_rows=batch_rows, commit_every_rows=commit_every_rows)

        if report.n_rows_read == 0:
            report.status = REJECTED
            report.reject_reason = "no data rows found"
        elif report.n_rows_loaded == 0:
            report.status = WARN
        elif report.n_rows_skipped > 0 or report.n_rows_unknown_job > 0:
            unknown_status_rate = report.issue_codes.get("unknown_status", 0) / max(report.n_rows_read, 1)
            report.status = WARN
            if unknown_status_rate > 0.05:
                report._note("high_unknown_status_rate", 0,
                            f"{unknown_status_rate:.0%} of rows had an unrecognised status -- "
                            "likely a status vocabulary this module doesn't know yet, worth checking")
        else:
            report.status = OK

    except _FileLevelReject:
        pass   # status/reject_reason already set by the raiser; fall through to finalise

    except Exception as exc:
        if _connection_lost(exc):
            try:
                session.rollback()
            except Exception:
                pass
            report.aborted = True
            report.abort_reason = f"{type(exc).__name__}: {exc}"
            logger.error(
                "database connection lost importing run history {!r} -- "
                "{} row(s) loaded before the connection dropped (RunImportRow {} left "
                "IN_PROGRESS as the resume marker): {}",
                path_label, report.n_rows_loaded, report.import_id, report.abort_reason,
            )
            return report
        raise

    _write_import_row(session, report)
    return report


def _ingest_rows(
    session: Session,
    row_source: Iterator[tuple[int, dict[str, str]]],
    report: RunHistoryReport,
    *,
    on_conflict: str,
    batch_rows: int,
    commit_every_rows: int,
) -> None:
    known_jobs = set(session.execute(select(JobRow.job_name)).scalars())
    # A client's export tool is not guaranteed to preserve AutoSys's own
    # casing convention for job names, even though the same job obviously
    # means the same thing either way. An exact-match-only lookup here would
    # silently dump every row for such a job into "unknown_job" -- which
    # reads as "this job was never imported" rather than "this job's name
    # was cased differently in the export." Built once per import, not per
    # row: same cost as the exact-match set already was.
    known_jobs_ci: dict[str, str] = {name.upper(): name for name in known_jobs}

    batch: list[dict] = []
    since_commit = 0
    key_kinds_seen: set[str] = set()

    for n, row in row_source:
        report.n_rows_read += 1
        job_name = (row.get("job_name") or "").strip()
        if not job_name:
            report._note("missing_job_name", n, "job_name is blank")
            report.n_rows_skipped += 1
            continue
        if job_name not in known_jobs:
            canonical = known_jobs_ci.get(job_name.upper())
            if canonical is not None:
                report._note("job_name_case_mismatch", n,
                            f"{job_name!r} matched {canonical!r} by case-insensitive lookup")
                job_name = canonical
            else:
                report.n_rows_unknown_job += 1
                if len(report.unknown_job_samples) < _MAX_NAME_SAMPLES:
                    report.unknown_job_samples.append(job_name)
                report._note("unknown_job", n, f"{job_name!r} is not in the imported JIL")
                continue

        status_value, classification = _status_lookup(row.get("status"))
        if classification == "unknown":
            report._note("unknown_status", n, f"status {row.get('status')!r} not recognised")
            report.n_rows_skipped += 1
            continue
        if classification == "non_terminal":
            report._note("non_terminal_status", n,
                        f"status {row.get('status')!r} is not a finished run")
            report.n_rows_skipped += 1
            continue

        start_dt, start_issue = _parse_timestamp(row.get("start_time"), report.declared_timezone)
        if start_issue == "naive_no_tz":
            report.status = REJECTED
            report.reject_reason = (
                f"row {n}: naive timestamp {row.get('start_time')!r} with no declared "
                "timezone -- the whole file is rejected rather than guessing UTC, which "
                "would silently shift every timestamp by up to 14 hours"
            )
            raise _FileLevelReject(report.reject_reason)
        if start_issue:
            report._note(start_issue, n, f"start_time {row.get('start_time')!r}")
            report.n_rows_skipped += 1
            continue
        if start_dt is None:
            report._note("missing_start_time", n, "start_time is blank")
            report.n_rows_skipped += 1
            continue

        end_dt, end_issue = _parse_timestamp(row.get("end_time"), report.declared_timezone)
        if end_issue == "naive_no_tz":
            report.status = REJECTED
            report.reject_reason = (
                f"row {n}: naive end_time {row.get('end_time')!r} with no declared timezone"
            )
            raise _FileLevelReject(report.reject_reason)
        if end_issue:
            report._note(end_issue, n, f"end_time {row.get('end_time')!r}")
            end_dt = None

        duration_secs: Optional[int] = None
        if end_dt is not None:
            delta = (end_dt - start_dt).total_seconds()
            if delta < 0:
                code = "dst_suspected" if delta > -3600 else "clock_skew"
                report._note(code, n, f"end_time before start_time by {-delta:.0f}s")
            else:
                duration_secs = int(delta)

        plausible = 2000 <= start_dt.year <= datetime.now(timezone.utc).year + 1
        if not plausible:
            report._note("implausible_timestamp", n, f"start_time year {start_dt.year}")

        attempt_raw = row.get("attempt")
        attempt: Optional[int] = None
        if attempt_raw not in (None, ""):
            try:
                attempt = int(float(attempt_raw))
            except (TypeError, ValueError):
                pass

        exit_code: Optional[int] = None
        if row.get("exit_code") not in (None, ""):
            try:
                exit_code = int(float(row["exit_code"]))
            except (TypeError, ValueError):
                pass

        machine = (row.get("machine") or "").strip()[:255] or None

        run_id, key_kind = _make_run_id(job_name, (row.get("run_id") or "").strip(),
                                        start_dt, attempt)
        key_kinds_seen.add(key_kind)

        batch.append({
            "run_id": run_id, "job_name": job_name, "status": status_value,
            "start_time": start_dt, "end_time": end_dt, "exit_code": exit_code,
            "machine": machine, "retry_count": attempt or 0,
            "run_date": start_dt.strftime("%Y-%m-%d"), "source": "imported",
            "import_id": report.import_id, "duration_secs": duration_secs,
        })

        if plausible:
            if report.window_start is None or start_dt < report.window_start:
                report.window_start = start_dt
            if report.window_end is None or start_dt > report.window_end:
                report.window_end = start_dt

        if len(batch) >= batch_rows:
            inserted = _flush_batch(session, batch, on_conflict)
            report.n_rows_loaded += inserted
            report.n_rows_duplicate += len(batch) - inserted
            since_commit += len(batch)
            batch = []
            if since_commit >= commit_every_rows:
                session.commit()
                since_commit = 0

    if batch:
        inserted = _flush_batch(session, batch, on_conflict)
        report.n_rows_loaded += inserted
        report.n_rows_duplicate += len(batch) - inserted

    session.commit()

    # Not implemented: cross-import key-kind consistency (refusing a job
    # whose key_kind changed between two SEPARATE import attempts). Checking
    # that would mean a per-job lookup against every prior import, which is
    # real cost at 86K jobs for a fairly rare situation (an export tool
    # changing mid-project). What IS caught: a single file mixing both key
    # kinds, recorded below as "mixed".
    report.key_kind = "mixed" if len(key_kinds_seen) > 1 else (next(iter(key_kinds_seen), ""))

    distinct_jobs = session.execute(
        select(func.count(func.distinct(JobRunRow.job_name)))
        .where(JobRunRow.import_id == report.import_id)
    ).scalar()
    report.n_jobs = distinct_jobs or 0


def _flush_batch(session: Session, batch: list[dict], on_conflict: str) -> int:
    """INSERT a batch with ON CONFLICT on run_id; return how many rows were
    actually inserted (duplicates = len(batch) - this)."""
    dialect = session.get_bind().dialect.name
    if dialect == "postgresql":
        from sqlalchemy.dialects.postgresql import insert as pg_insert
        stmt = pg_insert(JobRunRow).values(batch)
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as sqlite_insert
        stmt = sqlite_insert(JobRunRow).values(batch)
    else:  # pragma: no cover -- only sqlite/postgresql are supported dialects
        raise RuntimeError(f"unsupported dialect for run-history import: {dialect}")

    if on_conflict == "replace":
        update_cols = {c: getattr(stmt.excluded, c) for c in
                       ("status", "start_time", "end_time", "exit_code", "machine",
                        "retry_count", "run_date", "duration_secs")}
        stmt = stmt.on_conflict_do_update(index_elements=["run_id"], set_=update_cols)
    else:
        stmt = stmt.on_conflict_do_nothing(index_elements=["run_id"])

    stmt = stmt.returning(JobRunRow.run_id)
    result = session.execute(stmt)
    return len(result.fetchall())


def _write_import_row(session: Session, report: RunHistoryReport) -> None:
    """Finalise the RunImportRow created at the top of
    ingest_run_history_stream with the real counters and final status."""
    row = session.get(RunImportRow, report.import_id)
    if row is None:    # pragma: no cover -- only if the provisional insert failed silently
        return
    row.encoding = report.encoding
    row.key_kind = report.key_kind
    row.window_start = report.window_start
    row.window_end = report.window_end
    row.n_rows_read = report.n_rows_read
    row.n_rows_loaded = report.n_rows_loaded
    row.n_rows_skipped = report.n_rows_skipped
    row.n_rows_duplicate = report.n_rows_duplicate
    row.n_rows_unknown_job = report.n_rows_unknown_job
    row.n_jobs = report.n_jobs
    row.status = report.status
    row.issues_json = json.dumps({
        "counts": report.issue_codes,
        "samples": report.issue_samples,
        "unknown_job_samples": report.unknown_job_samples,
        "reject_reason": report.reject_reason,
    })
    session.commit()


def ingest_run_history_file(
    session_factory,
    fh: BinaryIO,
    path_label: str,
    *,
    declared_timezone: Optional[str] = None,
    on_conflict: str = "skip",
) -> RunHistoryReport:
    """
    CLI convenience wrapper: opens a session via *session_factory* (e.g.
    autosys.db.connection.sync_session), serialises against concurrent
    history imports, and delegates to ingest_run_history_stream.

    The API route does not use this -- it already has an open session from
    FastAPI's dependency injection and calls ingest_run_history_stream
    directly.
    """
    with session_factory() as session:
        _serialise_history_imports(session)
        return ingest_run_history_stream(
            session, fh, path_label,
            declared_timezone=declared_timezone, on_conflict=on_conflict,
        )
