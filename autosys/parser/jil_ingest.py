"""
Lossless, error-proof JIL ingestion.

Contract
--------
For every JIL file handed to :func:`ingest_file` / :func:`ingest_paths`:

* **No exception escapes for content reasons.**  Bad encoding, malformed
  stanzas, unknown sub-commands, wrong-typed values, duplicate definitions,
  a database error on one stanza ... all become *issues*.
* **No input is lost.**  Every stanza is archived verbatim in
  ``ujo_jil_stanza`` (``raw_text``); the raw texts of a file's stanzas, in
  ``seq`` order, concatenate back to the decoded file exactly.  Anything the
  typed tables cannot represent is still recoverable from the archive.
* **Every stanza gets exactly one disposition**: ``LOADED``,
  ``LOADED_WITH_WARNINGS``, ``ARCHIVE_ONLY`` (valid but no typed table),
  ``QUARANTINED`` (not parseable JIL) or ``DUPLICATE`` (a later definition of
  an already-defined object).

Duplicate policy (``insert_job`` of a name defined earlier)
    ``first``   keep the first definition in the typed tables; later ones are
                archived as DUPLICATE (deterministic when files are processed
                in sorted order)
    ``last``    later definitions overwrite; both stay in the archive
    ``update``  like ``last`` (the interactive ``jil import`` behaviour)

Replacing the estate with a newer export (``replace_estate=True``)
    Later definitions overwrite, and every job not in this run's files is
    removed from the live tables. Each removal is archived first, with the
    job's last definition, in a ``reconcile:<time>`` file, so it is visible
    and recoverable. Skipped when the run stopped early or loaded no jobs.
"""

from __future__ import annotations

import codecs
import copy
import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional

from loguru import logger
from sqlalchemy import delete, insert, select, text
from sqlalchemy.orm import Session

from autosys.db.schema import JilFileRow, JilStanzaRow
from autosys.parser import export_formats
from autosys.parser.jil_apply import ApplyResult, apply_operation
from autosys.parser.jil_parser import JILOperation, JILParser

LOADED, WARN, ARCHIVE, QUARANTINED, DUPLICATE = (
    "LOADED", "LOADED_WITH_WARNINGS", "ARCHIVE_ONLY", "QUARANTINED", "DUPLICATE")


# ===========================================================================
# Decoding
# ===========================================================================

def decode_bytes(data: bytes) -> tuple[str, str, list[dict]]:
    """
    Decode JIL bytes without ever failing.

    Order: BOM-marked UTF-8 / UTF-16 / UTF-32, BOM-less UTF-16 (NUL-interleaved),
    strict UTF-8, strict cp1252 (the usual Windows/legacy encoding), and finally
    latin-1, which maps every byte and therefore cannot fail.
    Returns ``(text, encoding, issues)``.
    """
    issues: list[dict] = []

    def note(enc: str) -> None:
        issues.append({"severity": "warning", "code": "encoding", "line": 0,
                       "message": f"file is not valid UTF-8; decoded as {enc}"})

    if data.startswith(codecs.BOM_UTF8):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig", issues
    if data.startswith((codecs.BOM_UTF32_LE, codecs.BOM_UTF32_BE)):
        try:
            return data.decode("utf-32"), "utf-32", issues
        except UnicodeDecodeError:
            pass
    if data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE)):
        try:
            return data.decode("utf-16"), "utf-16", issues
        except UnicodeDecodeError:
            pass
    if len(data) >= 4 and len(data) % 2 == 0:
        even, odd = data[0::2], data[1::2]
        half = len(data) // 8
        if odd.count(0) > half and even.count(0) == 0:
            try:
                return data.decode("utf-16-le"), "utf-16-le", issues
            except UnicodeDecodeError:
                pass
        if even.count(0) > half and odd.count(0) == 0:
            try:
                return data.decode("utf-16-be"), "utf-16-be", issues
            except UnicodeDecodeError:
                pass
    try:
        return data.decode("utf-8"), "utf-8", issues
    except UnicodeDecodeError:
        pass
    try:
        text = data.decode("cp1252")
        note("cp1252")
        return text, "cp1252", issues
    except UnicodeDecodeError:
        text = data.decode("latin-1")
        note("latin-1")
        return text, "latin-1", issues


def escape_nul(raw: str) -> tuple[str, bool]:
    """Reversible NUL escaping for databases that reject NUL in text columns."""
    if "\x00" not in raw:
        return raw, False
    return raw.replace("\\", "\\\\").replace("\x00", "\\0"), True


def unescape_nul(raw: str) -> str:
    out, i = [], 0
    while i < len(raw):
        c = raw[i]
        if c == "\\" and i + 1 < len(raw):
            out.append("\x00" if raw[i + 1] == "0" else raw[i + 1])
            i += 2
        else:
            out.append(c)
            i += 1
    return "".join(out)


# ===========================================================================
# Reports
# ===========================================================================

@dataclass
class FileReport:
    path:         str
    encoding:     str = "utf-8"
    sha256:       str = ""
    size_bytes:   int = 0
    status:       str = "OK"
    n_stanzas:    int = 0
    n_issues:     int = 0
    results:      list = field(default_factory=list)          # (action, name, detail)
    counters:     dict = field(default_factory=dict)          # summary counters
    dispositions: dict = field(default_factory=dict)
    issues:       list = field(default_factory=list)          # file-level issues
    defined_jobs: set = field(default_factory=set)            # every insert_job name in the file

    def bump(self, key: str, n: int = 1) -> None:
        self.counters[key] = self.counters.get(key, 0) + n


@dataclass
class IngestSummary:
    files:        int = 0
    stanzas:      int = 0
    dispositions: dict = field(default_factory=dict)
    encodings:    dict = field(default_factory=dict)
    file_status:  dict = field(default_factory=dict)
    issue_codes:  dict = field(default_factory=dict)
    counters:     dict = field(default_factory=dict)
    deferred_applied: int = 0

    # Set only when the run stopped early because the database itself went
    # away (not a per-file content problem, which is never fatal to the run).
    # files_committed is the true resume point: everything up to and
    # including that many files is durably in the database; the batch since
    # the last commit was rolled back, even if a few of them are still
    # counted in `files`/`stanzas` above from before the connection died.
    removed_jobs:    list = field(default_factory=list)   # replace_estate only
    aborted:         bool = False
    abort_reason:    str  = ""
    files_committed: int  = 0
    last_path:       str  = ""

    def add(self, r: FileReport, stanza_issue_codes: Iterable[str] = ()) -> None:
        self.files += 1
        self.stanzas += r.n_stanzas
        for k, v in r.dispositions.items():
            self.dispositions[k] = self.dispositions.get(k, 0) + v
        self.encodings[r.encoding] = self.encodings.get(r.encoding, 0) + 1
        self.file_status[r.status] = self.file_status.get(r.status, 0) + 1
        for k, v in r.counters.items():
            self.counters[k] = self.counters.get(k, 0) + v
        for c in stanza_issue_codes:
            self.issue_codes[c] = self.issue_codes.get(c, 0) + 1

    def as_dict(self) -> dict:
        return {
            "files": self.files, "stanzas": self.stanzas,
            "dispositions": self.dispositions, "encodings": self.encodings,
            "file_status": self.file_status, "issue_codes": self.issue_codes,
            "counters": self.counters, "deferred_applied": self.deferred_applied,
            "aborted": self.aborted, "abort_reason": self.abort_reason,
            "files_committed": self.files_committed, "last_path": self.last_path,
            "removed_jobs": self.removed_jobs,
        }


# ===========================================================================
# Core: one file's text
# ===========================================================================

@dataclass
class _Pending:
    """An update/override that ran before its insert_job (applied later)."""
    stanza_id: int
    op: Optional[JILOperation]
    link_job: Optional[str] = None      # set box_name of this job once the box exists
    link_box: Optional[str] = None


def _worst(issues: list[dict]) -> str:
    return "warning" if any(i.get("severity") in ("warning", "error") for i in issues) else "info"


def _prior_definition(session: Session, name: str, exclude_file_id: Optional[int]):
    q = (select(JilFileRow.path, JilStanzaRow.start_line)
         .join(JilFileRow, JilFileRow.file_id == JilStanzaRow.file_id)
         .where(JilStanzaRow.directive == "insert_job",
                JilStanzaRow.object_name == name,
                JilStanzaRow.disposition.in_((LOADED, WARN)))
         .order_by(JilStanzaRow.stanza_id).limit(1))
    if exclude_file_id is not None:
        q = q.where(JilStanzaRow.file_id != exclude_file_id)
    return session.execute(q).first()


# Any fixed 64-bit number; names the "a JIL import is writing" advisory lock.
_IMPORT_LOCK_KEY = 0x4A494C5F494D5054          # "JIL_IMPT"


def _serialise_imports(session: Session) -> None:
    """
    Make concurrent imports on PostgreSQL take turns (audit ING-08).

    Two imports touching the same jobs locked rows in opposite orders and
    deadlocked: 3,000 shared jobs from two threads took 1,512 s with 1,499
    deadlocks, and every deadlock victim's stanza ended ARCHIVE_ONLY. A
    transaction-scoped advisory lock lets one import's transaction finish
    before the other's starts writing. It is released at commit/rollback;
    taking it again in the same transaction is a no-op. SQLite already allows
    only one writer at a time.
    """
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SELECT pg_advisory_xact_lock(:k)"), {"k": _IMPORT_LOCK_KEY})


def ingest_text(
    session: Session,
    text: str,
    path: str,
    *,
    encoding: str = "utf-8",
    sha256: str = "",
    size_bytes: int = 0,
    file_issues: Optional[list[dict]] = None,
    tolerant: bool = True,
    dry_run: bool = False,
    archive: bool = True,
    duplicates: str = "update",
    read_files: bool = False,
    pending: Optional[list] = None,
) -> FileReport:
    """
    Parse *text* and apply every operation, isolating each one.

    With ``tolerant=True`` (default) this never raises for content problems.
    ``tolerant=False`` parses strictly and lets ``LexError`` /
    ``JILParseError`` propagate (the ``--strict`` behaviour).
    """
    report = FileReport(path=path, encoding=encoding, sha256=sha256, size_bytes=size_bytes)
    report.issues = list(file_issues or [])
    local_pending = pending is None and not dry_run
    if local_pending:
        pending = []
        _serialise_imports(session)        # a lone call (API, `jil import`); ingest_paths takes it per file

    has_nul = "\x00" in text
    parse_src = text.replace("\x00", "") if has_nul else text     # spans are line based
    fmt = export_formats.detect(parse_src) if tolerant else None
    if fmt is not None:                  # autocal_asc calendars / autorep -G globals
        return _ingest_export(session, parse_src, fmt, report, path, encoding=encoding,
                              sha256=sha256, size_bytes=size_bytes, dry_run=dry_run,
                              archive=archive)
    ops = JILParser().parse_text(parse_src, tolerant=tolerant)
    if not tolerant:                     # strict parses do not carry source spans
        JILParser._attach_source(parse_src, ops, [])
    report.n_stanzas = len(ops)
    if has_nul:
        from autosys.parser.lexer import normalize_text
        orig = normalize_text(text).split("\n")
        pieces = [l + "\n" for l in orig[:-1]] + [orig[-1]]
        for op in ops:
            op.raw_text = "".join(pieces[op.start_line - 1:op.end_line])
            if "\x00" in op.raw_text:
                op.issues.append({"severity": "warning", "code": "nul_bytes", "line": op.start_line,
                                  "message": "NUL bytes were removed from parsed values "
                                             "(kept, escaped, in the archived raw text)"})

    file_id: Optional[int] = None
    rows: list[dict] = []
    if archive and not dry_run:
        old = session.execute(select(JilFileRow.file_id).where(JilFileRow.path == path)).scalar()
        if old is not None:                          # re-import of the same path
            session.execute(delete(JilStanzaRow).where(JilStanzaRow.file_id == old))
            session.execute(delete(JilFileRow).where(JilFileRow.file_id == old))
            session.flush()
        frow = JilFileRow(path=path, sha256=sha256, size_bytes=size_bytes,
                          encoding=encoding, n_stanzas=len(ops), n_issues=0, status="OK")
        session.add(frow)
        session.flush()
        file_id = frow.file_id

    seen: dict[str, tuple[str, int]] = {}
    n_issues = len(report.issues)
    worst = "OK"
    for seq, op in enumerate(ops):
        issues = list(op.issues)
        if op.op == "insert" and op.job is not None:
            report.defined_jobs.add(op.job.job_name)
        elif op.op == "raw" and op.raw_directive == "insert_job" and op.raw_name:
            report.defined_jobs.add(op.raw_name)        # quarantined, but still in the export
        disposition, result = _apply_one(
            session, op, issues, report, seen, file_id, path,
            dry_run=dry_run, duplicates=duplicates, read_files=read_files, pending=pending)
        report.dispositions[disposition] = report.dispositions.get(disposition, 0) + 1
        n_issues += sum(1 for i in issues if i.get("severity") != "info")
        if disposition == QUARANTINED:
            worst = "ERROR_RECOVERED"
        elif issues and worst == "OK" and _worst(issues) == "warning":
            worst = "WARN"
        report.results.append((result.action, result.name, result.detail))
        if result.counter:
            report.bump(result.counter, result.count)
        if archive and not dry_run:
            rows.append({
                "file_id": file_id, "seq": seq, "start_line": op.start_line,
                "end_line": op.end_line,
                "directive": _label(_directive_of(op), 64),
                "object_name": _label(_name_of(op, result), 255),
                "disposition": disposition, "raw_text": escape_nul(op.raw_text)[0],
                "raw_escaped": escape_nul(op.raw_text)[1],
                "issues_json": json.dumps(issues) if issues else None,
            })
            if pending is not None and getattr(result, "_pending", False):
                pending[-1].stanza_id = -len(rows)      # patched to the real id below

    if archive and not dry_run:
        if rows:
            session.execute(insert(JilStanzaRow), rows)
        frow.n_issues = n_issues
        frow.status = worst if worst != "OK" or not report.issues else "WARN"
        if report.issues and worst == "OK":
            frow.status = "WARN"
        report.status = frow.status
        if pending is not None:
            _resolve_pending_ids(session, file_id, pending)
            if local_pending:
                _apply_pending(session, pending)
                relink_waiting_children(session)
    else:
        report.status = worst
    report.n_issues = n_issues
    return report


def _ingest_export(session, text, fmt, report, path, *, encoding, sha256, size_bytes,
                   dry_run, archive) -> FileReport:
    """
    Store an ``autocal_asc`` or ``autorep -G`` export (audit SEM-09, PARSER-07).

    These used to be quarantined by the JIL lexer, so calendars and global
    variables never reached the simulator. Each definition becomes one
    archived stanza (directive ``calendar`` / ``extended_calendar`` /
    ``set_global``), stored in its own savepoint like a JIL stanza.
    """
    import json as _json
    from autosys.db.repository import calendars as cal_repo, globs as glob_repo
    from autosys.db.schema import CalendarRow, GlobalVariableRow

    blocks = (export_formats.parse_autocal(text) if fmt == "autocal"
              else export_formats.parse_globals(text))
    report.n_stanzas = len(blocks)
    file_id = None
    if archive and not dry_run:
        old = session.execute(select(JilFileRow.file_id).where(JilFileRow.path == path)).scalar()
        if old is not None:
            session.execute(delete(JilStanzaRow).where(JilStanzaRow.file_id == old))
            session.execute(delete(JilFileRow).where(JilFileRow.file_id == old))
            session.flush()
        frow = JilFileRow(path=path, sha256=sha256, size_bytes=size_bytes, encoding=encoding,
                          n_stanzas=len(blocks), n_issues=0, status="OK")
        session.add(frow)
        session.flush()
        file_id = frow.file_id

    def store(b):
        if b.kind == "calendar":
            cal_repo.upsert(session, CalendarRow(calendar_name=b.name, dates_json=_json.dumps(b.dates)))
            return "calendars"
        if b.kind == "extended_calendar":
            rules = "; ".join(f"{k}={v}" for k, v in b.attrs.items() if v)
            cal_repo.upsert(session, CalendarRow(
                calendar_name=b.name, dates_json="[]",
                description=f"extended calendar (rules not simulated): {rules}"[:4000]))
            return "calendars"
        if b.value is not None and b.value.upper() == "DELETE":     # AutoSys: NAME=DELETE removes it
            row = session.get(GlobalVariableRow, b.name.upper())
            if row is not None:
                session.delete(row)
        else:
            glob_repo.set(session, b.name, b.value or "")
        return "globals"

    rows, n_issues, worst = [], 0, "OK"
    for seq, b in enumerate(blocks):
        issues = list(b.issues)
        if b.kind == "other":
            disposition = ARCHIVE
        else:
            disposition = WARN if _worst(issues) == "warning" else LOADED
            try:
                if dry_run:
                    counter = "calendars" if "calendar" in b.kind else "globals"
                else:
                    with session.begin_nested():
                        counter = store(b)
                        session.flush()
                report.bump(counter)
                report.results.append(("CALENDAR" if "calendar" in b.kind else "GLOBAL",
                                       b.name, b.kind))
            except Exception as exc:
                issues.append({"severity": "error", "code": "apply_failed", "line": b.start_line,
                               "message": _error_text(exc)})
                report.bump("failed")
                disposition = ARCHIVE
        report.dispositions[disposition] = report.dispositions.get(disposition, 0) + 1
        n_issues += sum(1 for i in issues if i.get("severity") != "info")
        if issues and _worst(issues) == "warning" and worst == "OK":
            worst = "WARN"
        if file_id is not None:
            rows.append({"file_id": file_id, "seq": seq, "start_line": b.start_line,
                         "end_line": b.end_line, "directive": b.kind if b.kind != "other" else None,
                         "object_name": _label(b.name, 255), "disposition": disposition,
                         "raw_text": escape_nul(b.raw_text)[0], "raw_escaped": escape_nul(b.raw_text)[1],
                         "issues_json": json.dumps(issues) if issues else None})
    if file_id is not None:
        if rows:
            session.execute(insert(JilStanzaRow), rows)
        frow.n_issues = n_issues
        frow.status = worst
    report.status = worst
    report.n_issues = n_issues
    return report


def _label(value: Optional[str], limit: int) -> Optional[str]:
    """Clip an archive lookup label to its column width (audit ING-02).

    directive/object_name are index keys, not data: the stanza's full text is
    always kept in raw_text. Unclipped, one over-long name (an unknown
    sub-command, a corrupted line) failed the file's single bulk archive
    insert on PostgreSQL and rolled back every job in the file -- and an
    `autorep -J ALL -q` export is one file.
    """
    if value is None or len(value) <= limit:
        return value
    return value[:limit - 1] + "…"


def _error_text(exc: BaseException) -> str:
    """First line of a database error only (audit ING-19).

    SQLAlchemy appends the full statement and its bound parameters -- the
    job's command, envvars, auth_string -- which would otherwise be stored in
    ujo_jil_stanza.issues_json.
    """
    first = str(exc).splitlines()[0] if str(exc) else ""
    return f"{type(exc).__name__}: {first}"


def _directive_of(op: JILOperation) -> Optional[str]:
    if op.op == "raw":
        return op.raw_directive
    if op.op == "override_delete":
        return "override_job"
    from autosys.parser.jil_parser import _DIRECTIVE_TO_OP
    for directive, code in _DIRECTIVE_TO_OP.items():
        if code == op.op:
            return directive
    return op.op


def _name_of(op: JILOperation, result: ApplyResult) -> Optional[str]:
    if op.op == "raw":
        return op.raw_name
    for key in ("job_name", "machine_name", "resource_name", "calendar_name", "global_name",
                "blob_name", "xinst_name", "monbro_name", "job_type_name", "profile_name",
                "policy_name", "view_name", "filter_name", "user_name"):
        if key in op.raw_attrs:
            return op.raw_attrs[key]
    return result.name or None


def _apply_one(session, op, issues, report, seen, file_id, path, *,
               dry_run, duplicates, read_files, pending):
    """Apply *op* inside a savepoint; returns (disposition, ApplyResult)."""
    # ---- quarantined / comment-only blocks: nothing to apply -----------------
    if op.op == "raw":
        only_comments = any(i.get("code") == "comments_only" for i in issues)
        return (ARCHIVE if only_comments else QUARANTINED,
                ApplyResult("SKIPPED", op.raw_name or "?",
                            "comments only" if only_comments else "unparseable block (quarantined)",
                            persisted=False))

    # ---- duplicate insert_job ---------------------------------------------------
    if op.op == "insert" and op.job is not None:
        name = op.job.job_name
        prior = None
        if name in seen:
            prior = seen[name]
        elif not dry_run:
            hit = _prior_definition(session, name, file_id)
            if hit:
                prior = (hit[0], hit[1])
        if prior is not None:
            issues.append({"severity": "warning", "code": "duplicate_definition",
                           "line": op.start_line,
                           "message": f"insert_job {name!r} was already defined at "
                                      f"{prior[0]}:{prior[1]}"})
            if duplicates == "first":
                return DUPLICATE, ApplyResult("SKIPPED", name, "duplicate definition (kept first)",
                                              persisted=False)

    # ---- apply in a savepoint --------------------------------------------------------
    try:
        if dry_run:
            result = apply_operation(session, op, dry_run=True, read_files=read_files)
        else:
            with session.begin_nested():
                result = apply_operation(session, op, dry_run=False, read_files=read_files)
                session.flush()
    except Exception as exc:                                    # never escapes
        retried = _retry_without_box(session, op, exc, issues, pending, dry_run, read_files)
        if retried is not None:
            result = retried
            issues.extend(result.issues)
            if op.op == "insert" and op.job is not None:
                seen.setdefault(op.job.job_name, (path, op.start_line))
            return WARN, result
        issues.append({"severity": "error", "code": "apply_failed", "line": op.start_line,
                       "message": _error_text(exc)})
        report.bump("failed")            # surfaced as n_failed by the API (audit ING-07)
        return ARCHIVE, ApplyResult("SKIPPED", getattr(getattr(op, "job", None), "job_name", "?") or "?",
                                    "could not be stored (archived)", persisted=False)

    issues.extend(result.issues)
    if op.op == "insert" and op.job is not None and result.persisted:
        seen.setdefault(op.job.job_name, (path, op.start_line))

    if not result.persisted:
        # update/override of a job that does not exist (yet): retry later in bulk runs
        if result.action == "SKIPPED" and "job not found" in result.detail:
            issues.append({"severity": "warning", "code": "update_unknown_job", "line": op.start_line,
                           "message": f"{op.op}_job of unknown job {op.job.job_name!r}"})
            if pending is not None:
                pending.append(_Pending(0, op))
                setattr(result, "_pending", True)
        return ARCHIVE, result
    return (WARN if _worst(issues) == "warning" else LOADED), result


def _retry_without_box(session, op, exc, issues, pending, dry_run, read_files):
    """A job whose box is defined later (or elsewhere) hits the box_name FK.
    Store it unboxed now and link it in :func:`_apply_pending`."""
    job = getattr(op, "job", None)
    if (job is None and not dry_run and "FOREIGN KEY" in str(exc).upper()
            and op.raw_attrs.get("job_name") and op.op in ("insert_monbro", "update_monbro",
                                                           "insert_blob", "update_blob")):
        ref = op.raw_attrs["job_name"]
        try:
            op2 = copy.copy(op)
            op2.raw_attrs = {k: v for k, v in op.raw_attrs.items() if k != "job_name"}
            with session.begin_nested():
                result = apply_operation(session, op2, dry_run=False, read_files=read_files)
                session.flush()
        except Exception:
            return None
        if not result.persisted:
            return None
        issues.append({"severity": "warning", "code": "unknown_job_reference", "line": op.start_line,
                       "message": f"references job {ref!r} which does not exist; stored without "
                                  "the link (original text kept in the archive)"})
        return result
    if (dry_run or pending is None or job is None or op.op not in ("insert", "update", "override")
            or not getattr(job, "box_name", None) or "FOREIGN KEY" not in str(exc).upper()):
        return None
    box = job.box_name
    try:
        op2 = copy.copy(op)
        op2.job = job.model_copy(update={"box_name": None})
        with session.begin_nested():
            result = apply_operation(session, op2, dry_run=False, read_files=read_files)
            session.flush()
    except Exception:
        return None
    if not result.persisted:
        return None
    issues.append({"severity": "warning", "code": "box_not_yet_defined", "line": op.start_line,
                   "box": box,
                   "message": f"box {box!r} does not exist yet; job stored unboxed and linked "
                              "if the box is ingested later"})
    p = _Pending(0, None, link_job=job.job_name, link_box=box)
    pending.append(p)
    setattr(result, "_pending", True)
    return result


def _resolve_pending_ids(session: Session, file_id: int, pending: list) -> None:
    fixed = [p for p in pending if p.stanza_id <= 0]
    if not fixed:
        return
    ids = {seq: sid for seq, sid in session.execute(
        select(JilStanzaRow.seq, JilStanzaRow.stanza_id).where(JilStanzaRow.file_id == file_id))}
    for p in fixed:
        # rows were appended in seq order, so -len(rows) is (seq + 1)
        p.stanza_id = ids.get(-p.stanza_id - 1, 0)


# ===========================================================================
# Files and directories
# ===========================================================================

def ingest_file(
    session: Session,
    path: str | os.PathLike,
    **kwargs,
) -> FileReport:
    """Read *path* (any bytes), decode, and :func:`ingest_text` it.  Never raises for content."""
    p = Path(path)
    path_str = str(p.resolve())
    try:
        data = p.read_bytes()
    except OSError as exc:                      # unreadable file is an issue, not a crash
        r = FileReport(path=path_str, status="ERROR_RECOVERED")
        r.issues.append({"severity": "error", "code": "unreadable_file", "line": 0,
                         "message": _error_text(exc)})
        r.n_issues = 1
        return r
    text, encoding, enc_issues = decode_bytes(data)
    return ingest_text(
        session, text, path_str, encoding=encoding,
        sha256=hashlib.sha256(data).hexdigest(), size_bytes=len(data),
        file_issues=enc_issues, **kwargs)


def iter_files(root: str | os.PathLike, pattern: str = "*", recursive: bool = True) -> Iterator[Path]:
    """Regular files under *root*, in a stable sorted order (determinism)."""
    root = Path(root)
    if root.is_file():
        yield root
        return
    it = root.rglob(pattern) if recursive else root.glob(pattern)
    for p in sorted(it, key=lambda x: str(x)):
        if p.is_file():
            yield p


def _connection_lost(exc: BaseException) -> bool:
    """
    True when *exc* means the database connection itself is gone -- not a
    content problem with the one file being processed.

    Checked via SQLAlchemy's own ``connection_invalidated`` flag (set when
    the dialect recognises the DBAPI error as a real disconnect), which is
    the cross-dialect-portable way to tell "the Postgres server went away"
    or "this SQLite file just became unreadable" from "this stanza's data
    was bad" -- string-matching driver-specific error text would not
    generalise across dialects, or across a driver upgrade.

    A ``PendingRollbackError`` also counts: it means an *earlier* statement
    on this session already errored and nothing rolled it back, so the
    session is unusable regardless of what triggered that first error.
    """
    from sqlalchemy.exc import DBAPIError, PendingRollbackError
    seen: set[int] = set()
    cur: Optional[BaseException] = exc
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        if isinstance(cur, PendingRollbackError):
            return True
        if isinstance(cur, DBAPIError) and cur.connection_invalidated:
            return True
        cur = cur.__cause__ or cur.__context__
    return False


def ingest_paths(
    session_factory: Callable,
    paths: Iterable[str | os.PathLike],
    *,
    commit_every: int = 200,
    progress: Optional[Callable[[int, FileReport], None]] = None,
    duplicates: str = "first",
    read_files: bool = False,
    replace_estate: bool = False,
    **kwargs,
) -> IngestSummary:
    """
    Ingest many files, committing every *commit_every* files.

    Each file runs inside its own savepoint: an unexpected failure rolls back
    only that file, which is then archived whole as a quarantined block.
    Updates that ran before their ``insert_job`` are applied once all files
    are in.

    That per-file recovery is only for content problems. If the database
    connection itself is lost (the server went away, disk full, ...), one
    file's savepoint failing that way says nothing about the next file's --
    every remaining file would fail identically, just with one log line
    each and no clear stopping point over what could be hundreds of
    thousands of files. This detects that case specifically and aborts the
    whole run there instead, with ``summary.files_committed`` as the exact
    resume point (everything up to it is durably committed).

    ``replace_estate=True`` treats *paths* as the whole current estate (a new
    full export): later definitions win, and jobs not in it are removed and
    archived -- see the module docstring (audit ING-05).
    """
    summary = IngestSummary()
    pending: list = []
    if replace_estate:
        duplicates = "last"
    present: set[str] = set()
    whole_file_failed = False
    with session_factory() as session:
        n = 0
        files_committed = 0
        for n, path in enumerate(paths, start=1):
            try:
                _serialise_imports(session)    # outside the savepoint, so held to commit
                with session.begin_nested():
                    rep = ingest_file(session, path, duplicates=duplicates,
                                      read_files=read_files, pending=pending, **kwargs)
            except Exception as exc:                       # file-level last resort
                if _connection_lost(exc):
                    try:
                        session.rollback()      # discard the uncommitted batch cleanly
                    except Exception:
                        pass
                    summary.aborted = True
                    summary.abort_reason = f"{type(exc).__name__}: {exc}"
                    summary.files_committed = files_committed
                    summary.last_path = str(path)
                    logger.error(
                        "database connection lost while processing {!r} -- stopping "
                        "after {} file(s) durably committed (re-run on what's left to "
                        "resume): {}", str(path), files_committed, summary.abort_reason)
                    return summary
                rep = _archive_whole_file(session, path, exc)
                whole_file_failed = True
            summary.add(rep, _issue_codes(session, rep))
            if replace_estate:
                # every insert_job in the export, stored or not: a job the
                # database refused is still in the estate and must not be removed
                present.update(rep.defined_jobs)
            if progress:
                progress(n, rep)
            if n % commit_every == 0:
                session.commit()
                session.expunge_all()          # keep memory flat at any scale
                files_committed = n
        session.commit()
        files_committed = n
        summary.deferred_applied = _apply_pending(session, pending)
        summary.deferred_applied += relink_waiting_children(session)
        session.commit()
        if replace_estate:
            # Remove nothing unless every file was read: a file that failed
            # whole would make all of its jobs look deleted.
            if present and not whole_file_failed:
                summary.removed_jobs = remove_jobs_not_in(session, present)
                session.commit()
            else:
                logger.warning("replace-estate: {}; nothing removed",
                               "a file could not be read" if whole_file_failed
                               else "no jobs in these files")
    summary.files_committed = files_committed
    return summary


def _issue_codes(session: Session, rep: FileReport) -> list[str]:
    codes = [i["code"] for i in rep.issues]
    if rep.n_issues > len(rep.issues):
        row = session.execute(select(JilFileRow.file_id).where(JilFileRow.path == rep.path)).scalar()
        if row is not None:
            for (raw,) in session.execute(
                    select(JilStanzaRow.issues_json).where(
                        JilStanzaRow.file_id == row, JilStanzaRow.issues_json.is_not(None))):
                try:
                    codes.extend(i["code"] for i in json.loads(raw))
                except (TypeError, ValueError):
                    pass
    return codes


def _archive_whole_file(session: Session, path, exc: Exception) -> FileReport:
    """Last resort: store the entire file as one quarantined block."""
    p = Path(path)
    path_str = str(p.resolve())
    rep = FileReport(path=path_str, status="ERROR_RECOVERED", n_stanzas=1)
    issue = {"severity": "error", "code": "file_failed", "line": 0,
             "message": _error_text(exc)}
    rep.issues.append(issue)
    rep.n_issues = 1
    rep.dispositions[QUARANTINED] = 1
    try:
        data = p.read_bytes()
        text, enc, _ = decode_bytes(data)
        with session.begin_nested():
            old = session.execute(select(JilFileRow.file_id).where(JilFileRow.path == path_str)).scalar()
            if old is not None:
                session.execute(delete(JilStanzaRow).where(JilStanzaRow.file_id == old))
                session.execute(delete(JilFileRow).where(JilFileRow.file_id == old))
            f = JilFileRow(path=path_str, sha256=hashlib.sha256(data).hexdigest(),
                           size_bytes=len(data), encoding=enc, n_stanzas=1, n_issues=1,
                           status="ERROR_RECOVERED")
            session.add(f)
            session.flush()
            session.add(JilStanzaRow(file_id=f.file_id, seq=0, start_line=1,
                                     end_line=text.count("\n") + 1, disposition=QUARANTINED,
                                     raw_text=escape_nul(text)[0], raw_escaped=escape_nul(text)[1],
                                     issues_json=json.dumps([issue])))
        rep.encoding = enc
    except Exception as exc2:                               # pragma: no cover
        logger.error("could not archive {}: {}", path_str, exc2)
    return rep


def _annotate(session, item, code, message, severity="info") -> None:
    row = session.get(JilStanzaRow, item.stanza_id) if item.stanza_id else None
    if row is not None:
        issues = json.loads(row.issues_json) if row.issues_json else []
        issues.append({"severity": severity, "code": code, "line": 0, "message": message})
        row.issues_json = json.dumps(issues)


def _apply_pending(session: Session, pending: list) -> int:
    """Retry update/override operations whose job now exists."""
    applied = 0
    for item in pending:
        try:
            if item.link_job:
                from autosys.db.schema import JobRow
                with session.begin_nested():
                    box_ok = session.execute(select(JobRow.job_name).where(
                        JobRow.job_name == item.link_box)).first() is not None
                    row = session.get(JobRow, item.link_job)
                    if box_ok and row is not None:
                        row.box_name = item.link_box
                        session.flush()
                if not box_ok:
                    _annotate(session, item, "box_never_defined",
                              f"box {item.link_box!r} was never ingested", "warning")
                    continue
                res = ApplyResult("UPDATED", item.link_job)
            else:
                with session.begin_nested():
                    res = apply_operation(session, item.op, read_files=False)
                if not res.persisted:
                    continue
            row = session.get(JilStanzaRow, item.stanza_id) if item.stanza_id else None
            if row is not None:
                issues = json.loads(row.issues_json) if row.issues_json else []
                issues.append({"severity": "info", "code": "applied_after_insert", "line": 0,
                               "message": "applied after the job's insert_job was ingested"})
                row.issues_json = json.dumps(issues)
                row.disposition = WARN
            applied += 1
        except Exception as exc:
            logger.warning("deferred update failed: {}", str(exc).splitlines()[0])
            if item.link_job:
                try:
                    _annotate(session, item, "box_never_defined",
                              f"box {item.link_box!r} could not be linked: {type(exc).__name__}",
                              "warning")
                except Exception:
                    pass
    return applied


_BOX_IN_MESSAGE = re.compile(r"^box '([^']+)' does not exist yet")


def relink_waiting_children(session: Session) -> int:
    """
    Link every job still waiting for a box that now exists (audit ING-06/ING-12).

    A child whose box_name names a box not loaded yet is stored unboxed with
    a ``box_not_yet_defined`` issue. Within one ``ingest_paths`` run the
    pending list links it at the end, but that list lives only for the run:
    the browser posts one file per request, and a resumed CLI run starts a
    fresh list, so a box arriving in a later request or run left its children
    unboxed for good -- while the warning still promised a link. The archive
    remembers every waiting child, so each import ends by linking the ones
    whose box exists now. A job whose box_name has been set since is left
    alone.
    """
    from autosys.db.schema import JobRow
    rows = session.execute(
        select(JilStanzaRow)
        .where(JilStanzaRow.issues_json.like('%"box_not_yet_defined"%'),
               ~JilStanzaRow.issues_json.like('%"box_linked"%'))
    ).scalars().all()
    linked = 0
    for st in rows:
        try:
            issues = json.loads(st.issues_json)
        except (TypeError, ValueError):
            continue
        waiting = next((i for i in issues if i.get("code") == "box_not_yet_defined"), None)
        if waiting is None:
            continue
        box = waiting.get("box")
        if not box:                                   # archived before "box" was recorded
            m = _BOX_IN_MESSAGE.match(waiting.get("message", ""))
            box = m.group(1) if m else None
        job = session.get(JobRow, st.object_name) if st.object_name else None
        if not box or job is None or job.box_name is not None:
            continue
        if session.get(JobRow, box) is None:
            continue
        job.box_name = box
        issues.append({"severity": "info", "code": "box_linked", "line": 0,
                       "message": f"linked to box {box!r} once it was ingested"})
        st.issues_json = json.dumps(issues)
        linked += 1
    if linked:
        session.flush()
    return linked


def remove_jobs_not_in(session: Session, present: set[str]) -> list[str]:
    """
    Remove every job not in *present*; archive each one first (audit ING-05).

    Importing a newer export used to leave jobs that had since been deleted
    in AutoSys in the estate, so the assessment counted jobs that no longer
    exist. Each removed job's last definition is written, as JIL, to a
    ``reconcile:<UTC time>`` archive file with a ``removed_in_new_export``
    issue, so the removal can be reviewed and undone by re-importing that
    text. A job that cannot be removed stays, archived ``ARCHIVE_ONLY`` with
    the reason. Returns the names removed.
    """
    from datetime import datetime, timezone
    from autosys.db.repository import jobs as job_repo
    from autosys.db.schema import JobRow
    from autosys.parser.jil_writer import job_to_jil

    gone = sorted(n for n in session.scalars(select(JobRow.job_name)) if n not in present)
    if not gone:
        return []
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    frow = JilFileRow(path=f"reconcile:{stamp}", sha256="", size_bytes=0, encoding="utf-8",
                      n_stanzas=len(gone), n_issues=0, status="OK")
    session.add(frow)
    session.flush()

    removed: list[str] = []
    rows: list[dict] = []
    for seq, name in enumerate(gone):
        job = job_repo.get(session, name)
        last = job_to_jil(job) if job is not None else ""
        issue = {"severity": "info", "code": "removed_in_new_export", "line": 0,
                 "message": f"not in the export imported at {stamp}; removed from the estate"}
        try:
            with session.begin_nested():
                job_repo.delete(session, name)
                session.flush()
            removed.append(name)
            disposition = LOADED
        except Exception as exc:
            issue = {"severity": "error", "code": "remove_failed", "line": 0,
                     "message": f"not in the export imported at {stamp}, but could not be "
                                f"removed: {_error_text(exc)}"}
            disposition = ARCHIVE
        rows.append({
            "file_id": frow.file_id, "seq": seq, "start_line": None, "end_line": None,
            "directive": "delete_job", "object_name": _label(name, 255),
            "disposition": disposition,
            "raw_text": f"/* last definition before removal */\n{last}\ndelete_job: {name}\n",
            "raw_escaped": False, "issues_json": json.dumps([issue]),
        })
    session.execute(insert(JilStanzaRow), rows)
    frow.n_issues = len(gone) - len(removed)
    frow.status = "WARN" if frow.n_issues else "OK"
    logger.info("replace-estate: removed {} job(s) not in the new export", len(removed))
    return removed
