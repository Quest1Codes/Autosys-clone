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

from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from autosys.models.enums import JobStatus
from autosys.db.schema import (
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


# ===========================================================================
# Mapping helpers
# ===========================================================================

# Pydantic fields that are list[str] in memory but stored as comma-separated
# strings in the DB (mirrors how real AutoSys stores them in Oracle).
_LIST_ATTRS = frozenset({"days_of_week", "start_times", "start_mins"})

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

    for key in _LIST_ATTRS:
        if key in data:
            lst = data[key]
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
        if val is not None:
            data[col.name] = val
    return parse_job(data)


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
            row = JobRow(**kwargs)
            session.add(row)
            return "inserted"
        else:
            # Preserve runtime state; only overwrite JIL-defined columns.
            for key, value in kwargs.items():
                if key not in _RUNTIME_COLS:
                    setattr(existing, key, value)
            existing.updated_at = datetime.now()
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
        session.delete(row)
        return True

    def rename(self, session: Session, old_name: str, new_name: str) -> bool:
        """
        Rename a job and update all dependency references.

        Returns True if the job existed, False otherwise.
        """
        row: Optional[JobRow] = session.get(JobRow, old_name)
        if row is None:
            return False

        session.flush()
        session.expire(row)

        # Disable FK checking during PK rename
        from autosys.db.connection import is_sqlite
        _is_sqlite = is_sqlite()
        if _is_sqlite:
            session.execute(text("PRAGMA foreign_keys=OFF"))
        else:
            session.execute(text("SET session_replication_role = 'replica'"))

        # Rename the job PK
        session.execute(
            JobRow.__table__.update()
            .where(JobRow.job_name == old_name)
            .values(job_name=new_name)
        )

        # Update FK references in job_runs
        session.execute(
            JobRunRow.__table__.update()
            .where(JobRunRow.job_name == old_name)
            .values(job_name=new_name)
        )

        # Update event_queue references
        session.execute(
            EventQueueRow.__table__.update()
            .where(EventQueueRow.job_name == old_name)
            .values(job_name=new_name)
        )

        # Update event_history references
        session.execute(
            EventHistoryRow.__table__.update()
            .where(EventHistoryRow.job_name == old_name)
            .values(job_name=new_name)
        )

        # Update job_output references
        session.execute(
            JobOutputRow.__table__.update()
            .where(JobOutputRow.job_name == old_name)
            .values(job_name=new_name)
        )

        # Update all jobs that reference old_name in box_name
        session.execute(
            JobRow.__table__.update()
            .where(JobRow.box_name == old_name)
            .values(box_name=new_name)
        )

        # Re-enable FK
        if _is_sqlite:
            session.execute(text("PRAGMA foreign_keys=ON"))
        else:
            session.execute(text("SET session_replication_role = 'origin'"))

        # Update condition strings (safe, no FK involved)
        all_jobs = session.scalars(
            select(JobRow).where(JobRow.condition.isnot(None))
        ).all()
        for j in all_jobs:
            if j.condition and old_name in j.condition:
                j.condition = j.condition.replace(old_name, new_name)

        return True

    def delete_box(self, session: Session, box_name: str) -> int:
        """
        Delete a BOX job and all its children.

        Returns the number of jobs deleted (box + children).
        """
        row: Optional[JobRow] = session.get(JobRow, box_name)
        if row is None:
            return 0

        children = session.scalars(
            select(JobRow).where(JobRow.box_name == box_name)
        ).all()
        count = 1 + len(children)  # box + children

        for child in children:
            session.delete(child)
        session.delete(row)
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
            row.processed_at = datetime.now()


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
            row.updated_at = datetime.now()

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
        from datetime import datetime as _dt
        row = JobRunRow(
            run_id     = run_id,
            job_name   = job_name,
            status     = JobStatus.RUNNING.value,
            start_time = datetime.now(),
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
    ) -> None:
        """
        Update a run record when the subprocess exits.

        Called by AgentDispatch._run_job() from the background thread,
        in its own session (separate from the dispatch session).
        """
        from datetime import datetime as _dt
        if isinstance(status, str):
            try:
                status = JobStatus[status.upper()].value
            except KeyError:
                pass
        row: Optional[JobRunRow] = session.get(JobRunRow, run_id)
        if row is None:
            return
        row.status    = status
        row.end_time  = _dt.now()
        row.exit_code = exit_code
        if pid is not None:
            row.pid = pid

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
    ) -> MachineRow:
        """
        Upsert a machine record.

        If *machine_name* already exists, update ``host``, ``port``,
        ``status``, and ``description`` in-place.  This is called both
        by ``autosys machine register`` and by the agent server on startup.
        """
        from datetime import datetime as _dt
        row: Optional[MachineRow] = session.get(MachineRow, machine_name)
        if row is None:
            row = MachineRow(
                machine_name = machine_name,
                host         = host,
                port         = port,
                status       = status,
                description  = description,
            )
            session.add(row)
            session.flush()   # make it persistent so subsequent session.get() calls find it
        else:
            row.host        = host
            row.port        = port
            row.status      = status
            if description is not None:
                row.description = description
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
        from datetime import datetime as _dt
        row = session.get(MachineRow, machine_name)
        if row is None:
            return
        row.status         = status
        row.last_heartbeat = _dt.now()

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
