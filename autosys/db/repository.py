"""
AutoSys database repository layer.

Abstracts all SQLAlchemy row-level operations behind clean domain-facing
functions.  Every function accepts an already-open SQLAlchemy ``Session``
so the caller controls transaction boundaries.

Three repositories
-------------------
JobRepository
    insert_job / update_job / delete_job / autorep queries.

EventRepository
    Enqueue and dequeue events from the event_queue table.
    This is the write side of the Event Processor's work queue.

GlobalVarRepository
    Upsert and read global variables (the SET_GLOBAL / GET_GLOBAL table).

Mapping helpers
---------------
_job_to_row_kwargs(job: Job) -> dict
    Convert a Pydantic Job to DB column kwargs.
    List fields (days_of_week, start_times) → comma-separated strings.

_row_to_job(row: JobRow) -> Job
    Convert a DB row back to the right Pydantic subclass.
    Delegates to parse_job() which handles the type dispatch.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import delete, func, or_, select, update
from sqlalchemy.orm import Session

from autosys.models.enums import JobStatus
from autosys.timeutil import utcnow
from autosys.db.schema import (
    AlarmRow,
    EventHistoryRow,
    EventQueueRow,
    GlobalVariableRow,
    JobOutputRow,
    JobRow,
    JobRunRow,
    MachineRow,
    CalendarRow,
    VirtualResourceRow,
    JobTypeRow,
    MonitorRow,
    BlobRow,
    GlobRow,
    ExternalInstanceRow,
    ConnectionProfileRow,
)
from autosys.models.job import Job, parse_job
from autosys.models.event import Event

# Keep IN (...) lists under every backend's bind-parameter limit (SQLite's
# historical default is 999).
_IN_CHUNK = 900


def _chunks(names):
    names = sorted(set(names))
    for i in range(0, len(names), _IN_CHUNK):
        yield names[i:i + _IN_CHUNK]


# ===========================================================================
# Mapping helpers
# ===========================================================================

# Pydantic fields that are list[str] in memory but stored as comma-separated
# strings in the DB (mirrors how real AutoSys stores them in Oracle).
# start_mins is NOT one of them: Job.start_mins is already the string
# "0,15,30,45", and joining a string joins its characters -- it was stored as
# "0,,,1,5,,,3,0,,,4,5" (audit PARSER-02).
_LIST_ATTRS = frozenset({"days_of_week", "start_times"})

# Columns that exist on JobRow but are runtime state (not JIL definitions)
# — we never overwrite these during an import.
_RUNTIME_COLS = frozenset({
    "status", "last_start", "last_end", "last_run_date",
    "created_at", "updated_at",
})


def _job_to_row_kwargs(job: Job) -> dict:
    """
    Convert a Pydantic Job to a flat kwargs dict for constructing a JobRow.

    Transformations:
    - list[str] fields → comma-separated string  (or None if empty)
    - None values → omitted entirely  (DB column default applies)
    - Enum values → their .value string  (already str for str-enums)
    """
    data = job.model_dump(exclude_none=True)

    # extra_attrs (dict) -> JSON text column; always written so an update
    # replaces the stored bag.
    extras = data.pop("extra_attrs", None) or {}
    data["extra_attrs_json"] = json.dumps(extras) if extras else None

    for key in _LIST_ATTRS:
        if key in data:
            lst = data[key]
            if isinstance(lst, (list, tuple)):
                data[key] = ",".join(lst) if lst else None

    # Remove runtime state fields — they default in the DB and must not be
    # overwritten by an import.
    for key in _RUNTIME_COLS:
        data.pop(key, None)

    return data


def _row_to_job(row: JobRow) -> Job:
    """
    Convert a DB JobRow back to the right Pydantic Job subclass.

    Reads every non-None column value into a dict, then delegates to
    parse_job() which dispatches on job_type and runs Pydantic validators.
    The validators handle comma-separated → list conversion for schedule
    fields automatically.
    """
    data: dict = {}
    for col in row.__table__.columns:
        val = getattr(row, col.name)
        if val is None:
            continue
        if col.name == "extra_attrs_json":
            try:
                data["extra_attrs"] = json.loads(val)
            except (TypeError, ValueError):
                pass
            continue
        data[col.name] = val
    try:
        return parse_job(data)
    except Exception:
        # A definition that was loaded leniently (CMD without a command, ...)
        # must still be readable.
        from autosys.models.job import parse_job_lenient
        return parse_job_lenient(data)[0]


# ===========================================================================
# JobRepository
# ===========================================================================

class JobRepository:
    """
    CRUD operations on the ``jobs`` table.

    All methods are synchronous (use the sync_session() context manager from
    autosys.db.connection).  The async scheduler uses the same logic via
    async_session in a later phase.
    """

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def upsert(self, session: Session, job: Job) -> str:
        """
        Insert or update a job definition row.

        Implements AutoSys insert_job / update_job / override_job semantics:
        - If the job does not exist → INSERT  → returns "inserted"
        - If the job already exists → UPDATE all JIL-defined columns while
          preserving runtime state (status, last_start, etc.) → "updated"

        Parameters
        ----------
        session:
            Open SQLAlchemy session (caller manages commit).
        job:
            Validated Pydantic Job model.

        Returns
        -------
        str
            ``"inserted"`` or ``"updated"``
        """
        kwargs = _job_to_row_kwargs(job)
        existing: Optional[JobRow] = session.get(JobRow, job.job_name)

        if existing is None:
            # ``status:`` on insert_job sets the job's initial status; every
            # other runtime column is left to the scheduler.
            initial = getattr(job, "status", None)
            if initial not in (None, JobStatus.INACTIVE.value):
                kwargs["status"] = int(initial)
            row = JobRow(**kwargs)
            session.add(row)
            return "inserted"
        else:
            # Preserve runtime state; only overwrite JIL-defined columns.
            for key, value in kwargs.items():
                if key not in _RUNTIME_COLS:
                    setattr(existing, key, value)
            existing.updated_at = utcnow()
            return "updated"

    def delete(self, session: Session, job_name: str) -> bool:
        """
        Remove a job definition row.

        Cascades to job_runs and alarms via the ORM relationship.
        Returns True if the row existed, False if it was already absent.
        """
        row: Optional[JobRow] = session.get(JobRow, job_name)
        if row is None:
            return False
        self._clear_dependents(session, job_name)
        session.delete(row)
        return True

    @staticmethod
    def _clear_dependents(session: Session, job_name: str) -> None:
        """
        Remove what would block deleting *job_name* (audit ING-13).

        Alarms and captured output reference the job (and its runs) with
        NOT NULL foreign keys and no cascade, so a job that had ever raised an
        alarm or produced output could not be deleted: the delete failed and
        the job stayed. Monitors and blobs only point at it, so they are
        unlinked, not deleted. Runs cascade through the ORM relationship.
        """
        runs = select(JobRunRow.run_id).where(JobRunRow.job_name == job_name)
        session.execute(delete(AlarmRow).where(
            or_(AlarmRow.job_name == job_name, AlarmRow.run_id.in_(runs))))
        session.execute(delete(JobOutputRow).where(
            or_(JobOutputRow.job_name == job_name, JobOutputRow.run_id.in_(runs))))
        for model in (MonitorRow, BlobRow):
            session.execute(update(model).where(model.job_name == job_name).values(job_name=None))

    def rename(self, session: Session, old_name: str, new_name: str) -> bool:
        """
        Rename a job and update all dependency references.

        Returns True if the job existed, False otherwise.

        The mutations below are ordered so the rename is FK-safe with
        checking left ON throughout: a full copy is inserted under
        ``new_name`` first, every dependent row is repointed at it (now
        valid, since ``new_name`` already exists), and only then is the
        ``old_name`` row -- now unreferenced -- deleted. This matters
        because the "disable FK checking, rename the PK, re-enable it"
        approach a raw PRAGMA/SET toggle below is a best-effort attempt at
        (still made, for whatever direct callers outside a transaction it
        still helps) is a silent no-op on SQLite when this runs inside an
        open transaction or savepoint -- which every stanza does under the
        tolerant JIL ingester's per-op isolation -- leaving FK checking ON
        and the PK rename failing with a FOREIGN KEY constraint error.
        """
        row: Optional[JobRow] = session.get(JobRow, old_name)
        if row is None:
            return False

        session.flush()

        # No FK toggling: the insert-then-repoint order below is FK-safe as is.
        # The PostgreSQL toggle (SET session_replication_role) needs superuser;
        # under the bundle's role it failed and aborted the transaction, so
        # every rename failed on PostgreSQL (audit ING-03).

        # Insert a full copy of the row under new_name.
        cols = {c.name: getattr(row, c.name) for c in JobRow.__table__.columns
                if c.name != "job_name"}
        session.execute(JobRow.__table__.insert().values(job_name=new_name, **cols))
        session.flush()

        # Repoint every FK-referencing row at new_name (new_name now exists,
        # so this is valid even with FK checking on).
        for table_col in (
            (JobRow.__table__, JobRow.box_name),        # other jobs' box_name
            (JobRunRow.__table__, JobRunRow.job_name),
            (EventQueueRow.__table__, EventQueueRow.job_name),
            (EventHistoryRow.__table__, EventHistoryRow.job_name),
            (JobOutputRow.__table__, JobOutputRow.job_name),
            (AlarmRow.__table__, AlarmRow.job_name),
            (MonitorRow.__table__, MonitorRow.job_name),
            (BlobRow.__table__, BlobRow.job_name),
        ):
            table, col = table_col
            session.execute(table.update().where(col == old_name).values({col.name: new_name}))
        session.flush()

        # old_name is now unreferenced -- safe to drop.
        session.execute(JobRow.__table__.delete().where(JobRow.job_name == old_name))

        # Repoint references in other jobs' conditions, token by token
        # (audit ING-04: str.replace turned s(job_ab) into s(job_xb)).
        from autosys.analysis.condition_refs import rename_job_refs
        cond_cols = (JobRow.condition, JobRow.box_success, JobRow.box_failure)
        for j in session.scalars(select(JobRow).where(or_(
                *(c.contains(old_name, autoescape=True) for c in cond_cols)))):
            for c in cond_cols:
                value = getattr(j, c.key)
                renamed = rename_job_refs(value, old_name, new_name)
                if renamed != value:
                    setattr(j, c.key, renamed)

        return True

    def delete_box(self, session: Session, box_name: str) -> int:
        """
        Delete a BOX job and all its children.

        Returns the number of jobs deleted (box + children).
        """
        row: Optional[JobRow] = session.get(JobRow, box_name)
        if row is None:
            return 0

        # Every level of nesting, not only direct children (audit ING-13:
        # grandchildren in a nested box were left behind).
        levels: list[list[str]] = [[box_name]]
        seen = {box_name}
        while levels[-1]:
            nxt = [n for n in session.scalars(
                select(JobRow.job_name).where(JobRow.box_name.in_(levels[-1]))) if n not in seen]
            seen.update(nxt)
            levels.append(nxt)

        count = 0
        for level in reversed(levels):           # deepest first, so no FK points at a deleted box
            for name in level:
                self._clear_dependents(session, name)
                session.delete(session.get(JobRow, name))
                count += 1
            session.flush()
        return count

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get(self, session: Session, job_name: str) -> Optional[Job]:
        """
        Return the Pydantic Job for *job_name*, or None if not found.
        """
        row: Optional[JobRow] = session.get(JobRow, job_name)
        return _row_to_job(row) if row else None

    def get_row(self, session: Session, job_name: str) -> Optional[JobRow]:
        """Return the raw ORM row, or None if not found."""
        return session.get(JobRow, job_name)

    def list_all(self, session: Session) -> list[JobRow]:
        """
        Return all job rows ordered by job_name.

        The Scheduler ACE uses this on startup to rebuild its in-memory
        run queue from the persisted state.
        """
        return list(session.scalars(
            select(JobRow).order_by(JobRow.job_name)
        ))

    def list_status_only(
        self, session: Session, names: Optional[set[str]] = None,
    ) -> list[tuple[str, int]]:
        """
        Return (job_name, status) for every job -- a two-column projection,
        not full ORM rows.

        Exists for build_status_snapshot(), which is called up to four
        times per EPS tick and only ever reads these two columns. Measured
        at 85,000 jobs: 0.167s vs 1.203s for the equivalent list_all() scan
        (dev/task3.../02, section 3.1).

        *names* restricts the result to those jobs (task E6): callers that
        evaluate a handful of conditions should not read the whole estate.
        """
        if names is None:
            return list(session.execute(
                select(JobRow.job_name, JobRow.status)
            ).all())
        out: list[tuple[str, int]] = []
        for chunk in _chunks(names):
            out.extend(session.execute(
                select(JobRow.job_name, JobRow.status).where(JobRow.job_name.in_(chunk))
            ).all())
        return out

    def last_end_times(
        self, session: Session, names: Optional[set[str]] = None,
    ) -> list[tuple[str, Optional[datetime]]]:
        """
        Return (job_name, last_end) -- a two-column projection for
        build_last_times_snapshot(), which used to load every full ORM row.
        *names* restricts the result as in list_status_only().
        """
        if names is None:
            return list(session.execute(select(JobRow.job_name, JobRow.last_end)).all())
        out: list[tuple[str, Optional[datetime]]] = []
        for chunk in _chunks(names):
            out.extend(session.execute(
                select(JobRow.job_name, JobRow.last_end).where(JobRow.job_name.in_(chunk))
            ).all())
        return out

    def list_stuck_starting(self, session: Session) -> list[JobRow]:
        """
        Return non-BOX jobs currently in STARTING status.

        Backs the EPS's stuck-STARTING recovery pass (a previous
        run/restart left these mid-dispatch) -- was a full list_all() scan
        with the filter applied in Python. Measured at 85,000 jobs: 0.009s
        vs the full scan (dev/task3.../02, section 3.1).
        """
        return list(session.scalars(
            select(JobRow)
            .where(JobRow.job_type != "BOX")
            .where(JobRow.status == JobStatus.STARTING.value)
        ))

    def list_schedulable(self, session: Session) -> list[JobRow]:
        """
        Return jobs that have a start_times attribute set.

        Backs the EPS's time-trigger scan (time_trigger.get_triggered_jobs):
        is_triggered() returns False immediately for any row with no
        start_times, so scanning the other rows at all is wasted work --
        was a full list_all() scan. Measured at 85,000 jobs: 0.034s for
        the ~2,361 rows that actually have a schedule, vs the full scan
        (dev/task3.../02, section 3.1).
        """
        return list(session.scalars(
            select(JobRow).where(JobRow.start_times.isnot(None))
        ))

    def search(self, session: Session, pattern: str) -> list[JobRow]:
        """Alias for list_by_pattern — used by the REST API."""
        return self.list_by_pattern(session, pattern)

    def list_by_pattern(self, session: Session, pattern: str) -> list[JobRow]:
        """
        Return rows whose job_name matches an SQL LIKE *pattern*.

        AutoSys uses ``%`` as a wildcard in ``autorep -J %`` to mean "all
        jobs".  We translate that directly to a SQL LIKE clause.

        Parameters
        ----------
        pattern:
            A SQL LIKE pattern, e.g. ``"%"`` for all jobs, ``"etl%"`` for
            jobs whose names start with ``etl``.
        """
        return list(session.scalars(
            select(JobRow)
            .where(JobRow.job_name.like(pattern))
            .order_by(JobRow.job_name)
        ))

    def count_children(self, session: Session, box_name: str) -> int:
        """Return the number of jobs inside a BOX."""
        return session.scalar(
            select(func.count()).select_from(JobRow)
            .where(JobRow.box_name == box_name)
        ) or 0

    def get_children(self, session: Session, box_name: str) -> list[JobRow]:
        """
        Return all direct children of a BOX job.

        Children are jobs whose ``box_name`` attribute equals *box_name*.
        They are returned ordered by name so condition evaluation is
        deterministic (Phase 8 will add priority ordering).
        """
        return list(session.scalars(
            select(JobRow)
            .where(JobRow.box_name == box_name)
            .order_by(JobRow.job_name)
        ))

    def get_running_boxes(self, session: Session) -> list[JobRow]:
        """
        Return all BOX jobs currently in RUNNING or ACTIVATED state.

        ACTIVATED = BOX has been started, children not yet running.
        RUNNING   = at least one child is running.
        Both states need BoxManager to evaluate/cascade children.
        """
        return list(session.scalars(
            select(JobRow)
            .where(JobRow.job_type == "BOX")
            .where(JobRow.status.in_([
                JobStatus.RUNNING.value,
                JobStatus.ACTIVATED.value,
            ]))
            .order_by(JobRow.job_name)
        ))

    def get_box_row(self, session: Session, box_name: str) -> Optional[JobRow]:
        """
        Return the BOX job row for *box_name*, or None if not found or not a BOX.

        Used by the event processor to check if a KILLJOB target is a BOX.
        """
        row = session.get(JobRow, box_name)
        return row if (row and row.job_type == "BOX") else None


# ===========================================================================
# EventRepository
# ===========================================================================

class EventRepository:
    """
    Operations on the ``event_queue`` and ``event_history`` tables.

    In real AutoSys, events enter the queue via:
    - ``sendevent`` CLI
    - The REST API (SSA)
    - The Scheduler ACE itself (internal state-change events)

    The Event Processor (Phase 4) dequeues them and drives the state machine.
    """

    def enqueue(self, session: Session, event: Event) -> str:
        """
        Add an event to the queue and return its event_id.

        Also writes to event_history so operators can audit every event
        even after it has been processed.

        Parameters
        ----------
        session:
            Open SQLAlchemy session.
        event:
            Validated Pydantic Event model.

        Returns
        -------
        str
            The UUID event_id of the queued event.
        """
        row = EventQueueRow(
            event_id   = event.event_id,
            event_type = str(event.event_type),
            job_name   = event.job_name,
            global_name  = event.global_name,
            global_value = event.global_value,
            new_status   = event.new_status.value if hasattr(event.new_status, 'value') else event.new_status,
            source     = str(event.source),
            processed  = False,
        )
        session.add(row)

        # Mirror into history for audit trail
        hist = EventHistoryRow(
            event_id     = event.event_id,
            event_type   = str(event.event_type),
            job_name     = event.job_name,
            global_name  = event.global_name,
            global_value = event.global_value,
            status       = event.new_status.value if hasattr(event.new_status, 'value') else event.new_status,
            source       = str(event.source),
            created_at   = event.created_at,
        )
        session.add(hist)

        return event.event_id

    def dequeue_pending(
        self,
        session: Session,
        limit: int = 100,
    ) -> list[EventQueueRow]:
        """
        Fetch up to *limit* unprocessed events, oldest first (FIFO).

        The Event Processor calls this on every poll tick.
        """
        return list(session.scalars(
            select(EventQueueRow)
            .where(EventQueueRow.processed == False)  # noqa: E712
            .order_by(EventQueueRow.created_at)
            .limit(limit)
        ))

    def mark_processed(self, session: Session, event_id: str) -> None:
        """
        Mark an event as processed so it won't be dequeued again.

        The Event Processor calls this after successfully applying the event.
        """
        row: Optional[EventQueueRow] = session.get(EventQueueRow, event_id)
        if row:
            row.processed    = True
            row.processed_at = utcnow()

    def archive_older_than(self, session: Session, cutoff: datetime) -> int:
        """
        Delete every ``EventHistoryRow`` created before *cutoff*.

        Mirrors real AutoSys's ``archive_events`` utility, which purges old
        processed events from the audit history so it doesn't grow
        unbounded.  Returns the number of rows deleted.
        """
        rows = list(session.scalars(
            select(EventHistoryRow).where(EventHistoryRow.created_at < cutoff)
        ))
        for row in rows:
            session.delete(row)
        return len(rows)


# ===========================================================================
# GlobalVarRepository
# ===========================================================================

class GlobalVarRepository:
    """
    Operations on the ``global_variables`` table.

    AutoSys global variables:
    - Are set via ``sendevent -E SET_GLOBAL -G name -v value``
    - Are expanded at dispatch time as ``%%VARNAME%%`` in command strings
    - Are queried via ``autorep -G varname``
    """

    def set(
        self,
        session: Session,
        name: str,
        value: str,
    ) -> None:
        """
        Upsert a global variable.

        Name is normalised to UPPERCASE (matching AutoSys's behaviour).
        """
        name = name.upper()
        row: Optional[GlobalVariableRow] = session.get(GlobalVariableRow, name)
        if row is None:
            session.add(GlobalVariableRow(
                global_name=name, value=value,
            ))
        else:
            row.value      = value
            row.updated_at = utcnow()

    def get(self, session: Session, name: str) -> Optional[str]:
        """Return a global variable's value, or None if not defined."""
        row: Optional[GlobalVariableRow] = session.get(
            GlobalVariableRow, name.upper()
        )
        return row.value if row else None

    def list_all(self, session: Session) -> list[GlobalVariableRow]:
        """Return all global variables ordered by name."""
        return list(session.scalars(
            select(GlobalVariableRow).order_by(GlobalVariableRow.global_name)
        ))

    def as_dict(self, session: Session) -> dict[str, str]:
        """
        Return all globals as a plain dict.

        Convenience for passing to ``substitute()`` in the variable
        substitution engine.
        """
        return {row.global_name: row.value for row in self.list_all(session)}


# ===========================================================================
# Run History Repository  (one row per job execution attempt)
# ===========================================================================

class RunRepository:
    """
    CRUD for ``job_runs`` — the audit log of every job execution.

    AutoSys retains full run history so operators can inspect previous runs,
    compare durations, and audit exit codes.  ``autorep -J <job>`` in the
    real system shows the last run; this repository provides ``list_runs``
    for the full history.
    """

    def start(
        self,
        session: Session,
        run_id: str,
        job_name: str,
        command: str,
        machine: str,
        run_date: str,
    ) -> JobRunRow:
        """
        Insert a new RUNNING run record at job dispatch time.

        Called by AgentDispatch.dispatch() just before forking the subprocess.
        The PID and exit_code are left NULL here and filled in by
        ``finish()`` when the process exits.
        """
        row = JobRunRow(
            run_id     = run_id,
            job_name   = job_name,
            status     = JobStatus.RUNNING.value,
            start_time = utcnow(),
            machine    = machine,
            run_date   = run_date,
        )
        session.add(row)
        return row

    def finish(
        self,
        session: Session,
        run_id: str,
        status: int,
        exit_code: Optional[int],
        pid: Optional[int] = None,
    ) -> bool:
        """
        Update a run record when the subprocess exits.

        Called by AgentDispatch._run_job() from the background thread, in
        its own session (separate from the dispatch session that created
        the row via start()). That session may not have committed yet --
        SQL transaction isolation means this call's own session/connection
        genuinely cannot see the row until it does, no matter how the row
        is queried. Returns False in that case rather than raising, so the
        caller can retry with a fresh session (see AgentDispatch._run_job,
        which does exactly that with a short bounded backoff) instead of
        the update being silently lost -- confirmed happening for real:
        test_phase5.py's real (non-stub) dispatch tests hit this often
        enough to be a genuine, if narrow, race, not a theoretical one.
        """
        if isinstance(status, str):
            try:
                status = JobStatus[status.upper()].value
            except KeyError:
                pass
        row: Optional[JobRunRow] = session.get(JobRunRow, run_id)
        if row is None:
            return False
        row.status    = status
        row.end_time  = utcnow()
        row.exit_code = exit_code
        if pid is not None:
            row.pid = pid
        return True

    def get_history(
        self,
        session:  Session,
        job_name: Optional[str] = None,
        limit:    int = 20,
    ) -> list[JobRunRow]:
        """
        Return up to *limit* run records, newest first.  If *job_name* is
        given, filter to that job only.  Used by the REST API /runs endpoint.
        """
        from sqlalchemy import select, desc
        q = select(JobRunRow).order_by(desc(JobRunRow.start_time)).limit(limit)
        if job_name is not None:
            q = q.where(JobRunRow.job_name == job_name)
        return list(session.scalars(q))

    def list_runs(
        self,
        session: Session,
        job_name: str,
        limit: int = 20,
    ) -> list[JobRunRow]:
        """
        Return up to *limit* run records for *job_name*, newest first.

        Used by ``autosys jobs history`` to display the run log.
        """
        return self.get_history(session, job_name=job_name, limit=limit)

    def count_runs(self, session: Session, job_name: str) -> int:
        """Return the total number of run records for *job_name* (its ``runnum``)."""
        from sqlalchemy import func, select
        return session.scalar(
            select(func.count()).select_from(JobRunRow)
            .where(JobRunRow.job_name == job_name)
        ) or 0

    def get_run_by_number(
        self, session: Session, job_name: str, run_num: int,
    ) -> Optional[JobRunRow]:
        """
        Return the *run_num*-th run of *job_name* in chronological order
        (1 = first run ever, matching AutoSys's ``-R run_num``).  A negative
        *run_num* counts back from the most recent run (-1 = most recent,
        -2 = one before that), also matching real ``autorep -R``.
        """
        from sqlalchemy import asc, desc, select
        if run_num > 0:
            q = (
                select(JobRunRow).where(JobRunRow.job_name == job_name)
                .order_by(asc(JobRunRow.start_time)).limit(1).offset(run_num - 1)
            )
        else:
            q = (
                select(JobRunRow).where(JobRunRow.job_name == job_name)
                .order_by(desc(JobRunRow.start_time)).limit(1).offset(-run_num - 1)
            )
        return session.scalars(q).first()

    def latest_run_id(self, session: Session, job_name: str) -> Optional[str]:
        """
        Return the run_id of the most recent execution of *job_name*.

        Used by ``autosys jobs tail`` to default to the most recent run.
        """
        from sqlalchemy import select, desc
        row = session.scalars(
            select(JobRunRow)
            .where(JobRunRow.job_name == job_name)
            .order_by(desc(JobRunRow.start_time))
            .limit(1)
        ).first()
        return row.run_id if row else None

    def latest_exit_codes(
        self, session: Session, names: Optional[set[str]] = None,
    ) -> dict[str, Optional[int]]:
        """
        Return {job_name: exit_code} for each job's most recent run.

        Used by the condition evaluator to resolve ``exitcode(job) = N``
        conditions.  A job with no runs yet, or whose latest run hasn't
        finished (exit_code still NULL), is simply absent from the dict —
        ``exitcode(...)`` conditions on it are then unsatisfied.

        *names* restricts the scan to those jobs' runs (task E6). Without it
        this reads every run row ever recorded, which grows with history.
        """
        from sqlalchemy import select, desc
        stmts = (
            [select(JobRunRow).order_by(JobRunRow.job_name, desc(JobRunRow.start_time))]
            if names is None else
            [select(JobRunRow).where(JobRunRow.job_name.in_(chunk))
             .order_by(JobRunRow.job_name, desc(JobRunRow.start_time))
             for chunk in _chunks(names)]
        )
        latest: dict[str, Optional[int]] = {}
        for stmt in stmts:
            for row in session.scalars(stmt):
                if row.job_name not in latest:
                    latest[row.job_name] = row.exit_code
        return latest


# ===========================================================================
# Job Output Repository  (inline stdout/stderr per run)
# ===========================================================================

class OutputRepository:
    """
    Append and read captured output lines from ``job_output``.

    Each line the subprocess writes to stdout/stderr becomes one row here.
    The System Agent writes lines in real-time (from the output-reader thread)
    so that ``autosys jobs tail`` can show partial output of a running job.
    """

    def append(
        self,
        session: Session,
        run_id: str,
        job_name: str,
        line_no: int,
        content: str,
        stream: str = "stdout",
    ) -> None:
        """
        Append one output line for *run_id*.

        Called from the output-reader thread.  Each call opens its own
        session (via ``sync_session()``) in the AgentDispatch implementation,
        so this method just adds the row — the caller commits.
        """
        session.add(JobOutputRow(
            run_id   = run_id,
            job_name = job_name,
            line_no  = line_no,
            stream   = stream,
            content  = content,
        ))

    def get_lines(
        self,
        session: Session,
        run_id:  str,
        offset:  int = 0,
        limit:   int = 5000,
    ) -> list[JobOutputRow]:
        """
        Return output lines for *run_id*, ordered by line_no.

        Parameters
        ----------
        offset:
            Skip the first *offset* lines (0-based).
        limit:
            Maximum number of lines to return.
        """
        from sqlalchemy import select
        return list(session.scalars(
            select(JobOutputRow)
            .where(JobOutputRow.run_id == run_id)
            .order_by(JobOutputRow.line_no)
            .offset(offset)
            .limit(limit)
        ))

    def get_lines_for_job(
        self,
        session: Session,
        job_name: str,
        run_id: Optional[str] = None,
    ) -> list[JobOutputRow]:
        """
        Return output lines for a job.  If *run_id* is None, uses the
        most recent run for *job_name*.
        """
        from sqlalchemy import select
        if run_id is None:
            run_repo = RunRepository()
            run_id = run_repo.latest_run_id(session, job_name)
        if run_id is None:
            return []
        return self.get_lines(session, run_id)


# ===========================================================================
# Module-level singleton instances
# ===========================================================================

# ===========================================================================
# Machine Repository  (System Agent registry)
# ===========================================================================

class MachineRepository:
    """
    CRUD for the ``machines`` table — the System Agent registry.

    In real AutoSys, machine definitions are imported via JIL with
    ``insert_machine:`` syntax or registered automatically when an agent
    starts up and POSTs its address to the Scheduler.  Here we provide a
    clean Python API for both paths.

    The Scheduler looks up machines here to find ``host:port`` when
    dispatching remote jobs.  The ``status`` column tracks agent health:
      - ``"UNKNOWN"``  — registered but never pinged
      - ``"UP"``       — heartbeat responded successfully
      - ``"DOWN"``     — heartbeat failed (alarm raised if alarm_if_fail=1)
    """

    def register(
        self,
        session:      Session,
        machine_name: str,
        host:         str,
        port:         int   = 7520,
        status:       str   = "UNKNOWN",
        description:  Optional[str] = None,
        members_json: Optional[str] = None,
    ) -> MachineRow:
        """
        Upsert a machine record.

        If *machine_name* already exists, update ``host``, ``port``,
        ``status``, and ``description`` in-place.  This is called both
        by ``autosys machine register`` and by the agent server on startup.
        """
        row: Optional[MachineRow] = session.get(MachineRow, machine_name)
        if row is None:
            row = MachineRow(
                machine_name = machine_name,
                host         = host,
                port         = port,
                status       = status,
                description  = description,
                members_json = members_json,
            )
            session.add(row)
            session.flush()   # make it persistent so subsequent session.get() calls find it
        else:
            row.host        = host
            row.port        = port
            row.status      = status
            if description is not None:
                row.description = description
            if members_json is not None:
                row.members_json = members_json
        return row

    def get(self, session: Session, machine_name: str) -> Optional[MachineRow]:
        """Look up a machine by name.  Returns None if not registered."""
        return session.get(MachineRow, machine_name)

    def list_all(self, session: Session) -> list[MachineRow]:
        """Return all registered machines."""
        from sqlalchemy import select
        return list(session.scalars(select(MachineRow).order_by(MachineRow.machine_name)))

    def update_heartbeat(
        self,
        session:      Session,
        machine_name: str,
        status:       str = "UP",
    ) -> None:
        """
        Update last_heartbeat and status after a ping.

        Called by:
          - The agent server on receipt of a HEARTBEAT request (marks itself UP)
          - The CHECK_HEARTBEAT event handler (marks UP or DOWN depending on
            whether the agent responded)
        """
        row = session.get(MachineRow, machine_name)
        if row is None:
            return
        row.status         = status
        row.last_heartbeat = utcnow()

    def set_status(
        self,
        session:      Session,
        machine_name: str,
        status:       str,
    ) -> None:
        """Set machine status without updating heartbeat timestamp."""
        row = session.get(MachineRow, machine_name)
        if row:
            row.status = status

    def delete(self, session: Session, machine_name: str) -> bool:
        """Delete a machine registration. Returns True if it existed."""
        row = session.get(MachineRow, machine_name)
        if row is None:
            return False
        session.delete(row)
        return True


# ---------------------------------------------------------------------------
# Calendar Repository (Phase 11)
# ---------------------------------------------------------------------------

class CalendarRepository:
    def get(self, session: Session, name: str) -> Optional[CalendarRow]:
        return session.get(CalendarRow, name)

    def list_all(self, session: Session) -> list[CalendarRow]:
        return list(session.scalars(select(CalendarRow)).all())

    def upsert(self, session: Session, calendar: CalendarRow) -> None:
        session.merge(calendar)
        session.flush()

    def delete(self, session: Session, name: str) -> bool:
        row = session.get(CalendarRow, name)
        if row:
            session.delete(row)
            session.flush()
            return True
        return False

    def add_date(self, session: Session, name: str, date_str: str) -> bool:
        """Append a single date to a calendar's date list (deduplicated, sorted)."""
        row = session.get(CalendarRow, name)
        if row is None:
            return False
        dates = json.loads(row.dates_json) if row.dates_json else []
        if date_str not in dates:
            dates.append(date_str)
            dates.sort()
            row.dates_json = json.dumps(dates)
        return True

    def add_dates(self, session: Session, name: str, date_list: list[str]) -> int:
        """Append multiple dates to a calendar. Returns number of dates added."""
        row = session.get(CalendarRow, name)
        if row is None:
            return 0
        dates = json.loads(row.dates_json) if row.dates_json else []
        added = 0
        for d in date_list:
            if d not in dates:
                dates.append(d)
                added += 1
        dates.sort()
        row.dates_json = json.dumps(dates)
        return added

    def remove_date(self, session: Session, name: str, date_str: str) -> bool:
        """Remove a single date from a calendar's date list."""
        row = session.get(CalendarRow, name)
        if row is None:
            return False
        dates = json.loads(row.dates_json) if row.dates_json else []
        if date_str in dates:
            dates.remove(date_str)
            row.dates_json = json.dumps(dates)
            return True
        return False


# ===========================================================================
# Resource Repository
# ===========================================================================

class ResourceRepository:
    """CRUD for virtual resources (ujo_resource table)."""

    def upsert(self, session: Session, name: str, max_load: int = 1,
               description: Optional[str] = None) -> str:
        row: Optional[VirtualResourceRow] = session.get(VirtualResourceRow, name)
        if row is None:
            session.add(VirtualResourceRow(
                resource_name=name, max_load=max_load,
                description=description,
            ))
            return "inserted"
        row.max_load = max_load
        if description is not None:
            row.description = description
        return "updated"

    def delete(self, session: Session, name: str) -> bool:
        row = session.get(VirtualResourceRow, name)
        if row is None:
            return False
        session.delete(row)
        return True

    def get(self, session: Session, name: str) -> Optional[VirtualResourceRow]:
        return session.get(VirtualResourceRow, name)

    def list_all(self, session: Session) -> list[VirtualResourceRow]:
        return list(session.scalars(
            select(VirtualResourceRow).order_by(VirtualResourceRow.resource_name)
        ))


# ===========================================================================
# Job Type Repository
# ===========================================================================

class JobTypeRepository:
    """CRUD for user-defined job types (ujo_job_type table)."""

    def upsert(self, session: Session, type_name: str,
               command_template: Optional[str] = None,
               description: Optional[str] = None) -> str:
        row: Optional[JobTypeRow] = session.get(JobTypeRow, type_name)
        if row is None:
            session.add(JobTypeRow(
                type_name=type_name,
                command_template=command_template,
                description=description,
            ))
            return "inserted"
        if command_template is not None:
            row.command_template = command_template
        if description is not None:
            row.description = description
        return "updated"

    def delete(self, session: Session, type_name: str) -> bool:
        row = session.get(JobTypeRow, type_name)
        if row is None:
            return False
        session.delete(row)
        return True

    def get(self, session: Session, type_name: str) -> Optional[JobTypeRow]:
        return session.get(JobTypeRow, type_name)

    def list_all(self, session: Session) -> list[JobTypeRow]:
        return list(session.scalars(
            select(JobTypeRow).order_by(JobTypeRow.type_name)
        ))


# ===========================================================================
# Monitor Repository
# ===========================================================================

class MonitorRepository:
    """CRUD for monbro definitions (ujo_monbro table)."""

    def upsert(self, session: Session, name: str, monbro_type: str,
               job_name: Optional[str] = None,
               attributes_json: Optional[str] = None) -> str:
        row: Optional[MonitorRow] = session.get(MonitorRow, name)
        if row is None:
            session.add(MonitorRow(
                monbro_name=name, monbro_type=monbro_type,
                job_name=job_name, attributes_json=attributes_json,
            ))
            return "inserted"
        row.monbro_type = monbro_type
        if job_name is not None:
            row.job_name = job_name
        if attributes_json is not None:
            row.attributes_json = attributes_json
        return "updated"

    def delete(self, session: Session, name: str) -> bool:
        row = session.get(MonitorRow, name)
        if row is None:
            return False
        session.delete(row)
        return True

    def get(self, session: Session, name: str) -> Optional[MonitorRow]:
        return session.get(MonitorRow, name)

    def list_all(self, session: Session) -> list[MonitorRow]:
        return list(session.scalars(
            select(MonitorRow).order_by(MonitorRow.monbro_name)
        ))


# ===========================================================================
# Blob Repository
# ===========================================================================

class BlobRepository:
    """CRUD for binary large objects (ujo_blob table)."""

    def insert(self, session: Session, blob_name: str, content: str,
               job_name: Optional[str] = None) -> int:
        row = BlobRow(blob_name=blob_name, content=content, job_name=job_name)
        session.add(row)
        session.flush()
        return row.blob_id

    def delete(self, session: Session, blob_name: str) -> int:
        rows = session.scalars(
            select(BlobRow).where(BlobRow.blob_name == blob_name)
        ).all()
        for r in rows:
            session.delete(r)
        return len(rows)

    def get(self, session: Session, blob_name: str) -> list[BlobRow]:
        return list(session.scalars(
            select(BlobRow).where(BlobRow.blob_name == blob_name)
        ))

    def list_all(self, session: Session) -> list[BlobRow]:
        return list(session.scalars(
            select(BlobRow).order_by(BlobRow.blob_name)
        ))


# ===========================================================================
# Glob Repository
# ===========================================================================

class GlobRepository:
    """CRUD for global named blobs (ujo_glob table)."""

    def upsert(self, session: Session, glob_name: str, content: str) -> str:
        row: Optional[GlobRow] = session.get(GlobRow, glob_name)
        if row is None:
            session.add(GlobRow(glob_name=glob_name, content=content))
            return "inserted"
        row.content = content
        return "updated"

    def delete(self, session: Session, glob_name: str) -> bool:
        row = session.get(GlobRow, glob_name)
        if row is None:
            return False
        session.delete(row)
        return True

    def get(self, session: Session, glob_name: str) -> Optional[GlobRow]:
        return session.get(GlobRow, glob_name)

    def list_all(self, session: Session) -> list[GlobRow]:
        return list(session.scalars(
            select(GlobRow).order_by(GlobRow.glob_name)
        ))


# ===========================================================================
# External Instance Repository
# ===========================================================================

class ExternalInstanceRepository:
    """CRUD for cross-instance definitions (ujo_xinst table)."""

    def upsert(self, session: Session, xinst_name: str,
               instance_name: str, host: str, port: int = 9000,
               description: Optional[str] = None) -> str:
        row: Optional[ExternalInstanceRow] = session.get(ExternalInstanceRow, xinst_name)
        if row is None:
            session.add(ExternalInstanceRow(
                xinst_name=xinst_name, instance_name=instance_name,
                host=host, port=port, description=description,
            ))
            return "inserted"
        row.instance_name = instance_name
        row.host = host
        row.port = port
        if description is not None:
            row.description = description
        return "updated"

    def delete(self, session: Session, xinst_name: str) -> bool:
        row = session.get(ExternalInstanceRow, xinst_name)
        if row is None:
            return False
        session.delete(row)
        return True

    def get(self, session: Session, xinst_name: str) -> Optional[ExternalInstanceRow]:
        return session.get(ExternalInstanceRow, xinst_name)

    def list_all(self, session: Session) -> list[ExternalInstanceRow]:
        return list(session.scalars(
            select(ExternalInstanceRow).order_by(ExternalInstanceRow.xinst_name)
        ))


# ===========================================================================
# Connection Profile Repository
# ===========================================================================

class ConnectionProfileRepository:
    """CRUD for connection profiles (ujo_connection_profile table)."""

    def upsert(self, session: Session, profile_name: str, profile_type: str,
               attributes_json: Optional[str] = None) -> str:
        row: Optional[ConnectionProfileRow] = session.get(ConnectionProfileRow, profile_name)
        if row is None:
            session.add(ConnectionProfileRow(
                profile_name=profile_name, profile_type=profile_type,
                attributes_json=attributes_json,
            ))
            return "inserted"
        row.profile_type = profile_type
        if attributes_json is not None:
            row.attributes_json = attributes_json
        return "updated"

    def delete(self, session: Session, profile_name: str) -> bool:
        row = session.get(ConnectionProfileRow, profile_name)
        if row is None:
            return False
        session.delete(row)
        return True

    def get(self, session: Session, profile_name: str) -> Optional[ConnectionProfileRow]:
        return session.get(ConnectionProfileRow, profile_name)

    def list_all(self, session: Session) -> list[ConnectionProfileRow]:
        return list(session.scalars(
            select(ConnectionProfileRow).order_by(ConnectionProfileRow.profile_name)
        ))


#: Default singleton — import and use directly in commands.
jobs      = JobRepository()
events    = EventRepository()
globs     = GlobalVarRepository()
runs      = RunRepository()
output    = OutputRepository()
machines  = MachineRepository()
calendars = CalendarRepository()
resources = ResourceRepository()
job_types = JobTypeRepository()
monitors  = MonitorRepository()
blobs     = BlobRepository()
globs2    = GlobRepository()
xinsts    = ExternalInstanceRepository()
profiles  = ConnectionProfileRepository()
