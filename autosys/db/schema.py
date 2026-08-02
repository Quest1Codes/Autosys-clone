"""
SQLAlchemy ORM table definitions — the RDBMS layer of the AutoSys clone.

Each class here maps to one database table and mirrors a Pydantic model
from autosys/models/.  Keeping them separate lets the engine layer work
with SQLAlchemy sessions while the API and parser work with Pydantic models.

Table inventory
---------------
1.  JobRow           — job definitions (all ~50 JIL attributes)
2.  JobRunRow        — per-run history (one row per run attempt / retry)
3.  EventQueueRow    — pending events waiting for the EPS to process
4.  EventHistoryRow  — immutable audit log of every event ever raised
5.  GlobalVariableRow— AutoSys global variables (SET_GLOBAL / value() )
6.  CalendarRow      — named calendars (run_calendar / exclude_calendar)
7.  AlarmRow         — raised/cleared alarms
8.  VirtualResourceRow — virtual resources (max_load / job_load)
9.  MachineRow       — System Agent registry (machine name → host:port)
10. JobOutputRow     — captured stdout/stderr per run
11. JobTypeRow       — user-defined job types (command templates)
12. MonitorRow       — monbro definitions (file/cpu/disk monitors)
13. BlobRow          — binary large objects tied to jobs
14. GlobRow          — global named blobs (not tied to a job)
15. ExternalInstanceRow — cross-instance definitions
16. ConnectionProfileRow — connection profiles (Hadoop, AWS, etc.)

Relationship diagram (conceptual)
----------------------------------
MachineRow  <──  JobRow (machine FK)
CalendarRow <──  JobRow (run_calendar / exclude_calendar FK)
JobRow      <──  JobRunRow  (job_name FK)
JobRow      <──  EventQueueRow / EventHistoryRow (job_name FK)
JobRunRow   <──  AlarmRow  (run_id FK)
"""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship


class Base(DeclarativeBase):
    """Shared declarative base — all ORM classes inherit from this."""
    pass


# ---------------------------------------------------------------------------
# 1. Jobs table
# ---------------------------------------------------------------------------

class JobRow(Base):
    """
    Stores one row per job definition.

    When the JIL parser processes an ``insert_job`` stanza it calls
    ``db.upsert_job()``, which writes/updates a row here.
    ``update_job`` and ``override_job`` also write here.
    ``delete_job`` removes the row.

    The ``status`` column is the *current* runtime state managed by the
    Scheduler ACE state machine — it is updated in place (not in job_runs)
    so that a single ``SELECT * FROM jobs`` gives operators an instant
    snapshot of the whole workload.
    """
    __tablename__ = "ujo_job"

    # --- Identity ---
    job_name        = Column(String(255), primary_key=True)
    job_type        = Column(String(16),  nullable=False, index=True)
    description     = Column(Text)
    owner           = Column(String(128))
    permission      = Column(String(64))
    run_as_user     = Column(String(128))
    group           = Column(String(128), index=True)

    # --- Execution (CMD) ---
    command         = Column(Text)
    # No FK on machine — AutoSys validates machine reachability at dispatch
    # time, not at JIL import time.  Unknown machines are legal in a JIL file.
    machine         = Column(String(255), index=True)
    run_window      = Column(String(32))
    profile         = Column(Text)
    std_out_file    = Column(Text)
    std_err_file    = Column(Text)
    std_in_file     = Column(Text)
    envvars         = Column(Text)
    chk_files       = Column(Text)
    ulimit          = Column(String(128))

    # --- BOX container ---
    box_name        = Column(String(255), ForeignKey("ujo_job.job_name"), index=True)
    box_success     = Column(String(255))
    box_failure     = Column(String(255))
    box_terminator  = Column(Boolean, default=False, nullable=False)

    # --- Scheduling ---
    # Stored as comma-separated strings to keep the schema flat (mirrors
    # how AutoSys itself serialises them in its Oracle schema).
    start_times          = Column(String(256))   # "06:00,18:00"
    start_mins           = Column(String(128))   # "0,15,30,45"
    days_of_week         = Column(String(64))    # "mo,tu,we,th,fr"
    # Calendars are validated at runtime, not import time, so no FK here.
    run_calendar         = Column(String(128))
    exclude_calendar     = Column(String(128))
    date_conditions      = Column(Boolean, default=False, nullable=False)
    term_run_time        = Column(Integer)       # minutes
    avg_runtime          = Column(Integer)
    must_complete_times  = Column(String(256))
    must_start_times     = Column(String(256))
    priority             = Column(Integer)
    timezone             = Column(String(64))

    # --- Dependencies ---
    condition            = Column(Text)

    # --- Reliability ---
    n_retrys             = Column(Integer, default=0, nullable=False)
    max_exit_success     = Column(Integer, nullable=True)   # None → only 0 = SUCCESS
    max_run_alarm        = Column(Integer)       # minutes
    min_run_alarm        = Column(Integer)       # minutes
    alarm_if_fail        = Column(Boolean, default=False, nullable=False)
    alarm_if_terminated  = Column(Boolean, default=False, nullable=False)
    fail_codes           = Column(String(256))

    # --- Virtual resources ---
    job_load             = Column(Integer, default=1, nullable=False)
    max_load             = Column(Integer)
    resources            = Column(Text)
    auto_hold            = Column(Boolean, default=False, nullable=False)

    # --- Extended attributes (Phase 4) ---
    auto_delete          = Column(Boolean, default=False, nullable=False)
    application          = Column(String(255))
    sub_application      = Column(String(255))
    command_timeout      = Column(Integer)
    continuous           = Column(Boolean, default=False, nullable=False)
    cpu_usage            = Column(Integer)
    disk_space           = Column(Integer)
    auth_string          = Column(Text)
    connection_retry     = Column(Integer)
    connection_timeout   = Column(Integer)

    # --- Notifications ---
    notification_msg          = Column(Text)
    notification_emailaddress = Column(Text)
    notification_type         = Column(String(32))
    send_report               = Column(Boolean, default=False, nullable=False)

    # --- FTP-specific ---
    ftp_server    = Column(String(255))
    ftp_user      = Column(String(128))
    ftp_type      = Column(String(8))       # GET | PUT | DEL
    ftp_src       = Column(Text)
    ftp_dest      = Column(Text)

    # --- FILEWATCH-specific ---
    watch_file          = Column(Text)
    watch_file_min_size = Column(Integer, default=0, nullable=False)
    watch_interval      = Column(Integer, default=60, nullable=False)

    # --- Runtime tracking ---
    status        = Column(Integer, default=8)   # JobStatus.INACTIVE
    last_start    = Column(DateTime, nullable=True)
    last_end        = Column(DateTime)
    last_run_date   = Column(String(10))   # "YYYY-MM-DD"

    # --- Metadata ---
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at      = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    # --- Relationships ---
    runs     = relationship("JobRunRow", back_populates="job", cascade="all, delete-orphan")
    alarms   = relationship("AlarmRow",  back_populates="job")

    # Self-referential: one BOX job → many child jobs.
    # 'children' is the one-to-many side (parent BOX → its children).
    # 'parent_box' is the many-to-one side (child → its parent BOX).
    # remote_side on parent_box tells SQLAlchemy that job_name is the
    # "one" (primary key) side of the join.
    children = relationship(
        "JobRow",
        foreign_keys="[JobRow.box_name]",
        back_populates="parent_box",
        lazy="select",
    )
    parent_box = relationship(
        "JobRow",
        foreign_keys="[JobRow.box_name]",
        back_populates="children",
        remote_side="[JobRow.job_name]",
        lazy="select",
    )

    def __repr__(self) -> str:
        return f"<JobRow {self.job_name!r} type={self.job_type} status={self.status}>"


# ---------------------------------------------------------------------------
# 2. Job Runs table
# ---------------------------------------------------------------------------

class JobRunRow(Base):
    """
    One row per execution attempt (including retries).

    AutoSys keeps full run history so operators can audit every attempt.
    The retry_count column lets you see which attempt succeeded or failed.

    Important: the ``status`` here is the terminal status of *this run*.
    The live status is on JobRow.status.
    """
    __tablename__ = "ujo_job_runs"
    __table_args__ = (
        Index("ix_job_runs_job_date", "job_name", "run_date"),
    )

    run_id      = Column(String(255), primary_key=True)
    job_name    = Column(String(255), ForeignKey("ujo_job.job_name"), index=True)
    status      = Column(Integer, nullable=False, index=True)
    start_time   = Column(DateTime)
    end_time     = Column(DateTime)
    exit_code    = Column(Integer)                                 # None until finished
    machine      = Column(String(255))
    retry_count  = Column(Integer, default=0, nullable=False)
    run_date     = Column(String(10))                              # "YYYY-MM-DD"
    pid          = Column(Integer)                                 # OS PID on the agent
    stdout_path  = Column(Text)
    stderr_path  = Column(Text)
    created_at   = Column(DateTime, default=datetime.utcnow, nullable=False)

    # --- Relationships ---
    job    = relationship("JobRow",   back_populates="runs")
    alarms = relationship("AlarmRow", back_populates="run")

    def __repr__(self) -> str:
        return (
            f"<JobRunRow {self.run_id[:8]}… job={self.job_name!r} "
            f"status={self.status} retry={self.retry_count}>"
        )


# ---------------------------------------------------------------------------
# 3. Event Queue table  (EPS input queue)
# ---------------------------------------------------------------------------

class EventQueueRow(Base):
    """
    The event queue consumed by the Event Processor Service (EPS).

    The Scheduler ACE event loop polls this table every tick.
    Items are soft-deleted (processed=True) rather than hard-deleted so
    that the EventHistoryRow insert and the queue update can be done in
    one transaction.

    Sources that write here:
      - CLI (sendevent command)
      - REST API (/api/v1/events endpoint)
      - Scheduler itself (time triggers, retry logic)
      - System Agent callbacks (job completion)
    """
    __tablename__ = "ujo_event"
    __table_args__ = (
        Index("ix_event_queue_unprocessed", "processed", "created_at"),
    )

    event_id      = Column(String(36),  primary_key=True)          # UUID
    event_type    = Column(String(32),  nullable=False, index=True)
    # No FK on job_name — real AutoSys allows events for jobs that haven't
    # been imported yet (or have been deleted).  The Event Processor handles
    # "job not found" gracefully.
    job_name      = Column(String(255), index=True)
    new_status    = Column(Integer)      # for CHANGE_STATUS
    global_name   = Column(String(128))  # for SET_GLOBAL
    global_value  = Column(Text)         # for SET_GLOBAL
    comment       = Column(Text)         # for COMMENT
    resource_name = Column(String(255))  # for RELEASE_RESOURCE
    source        = Column(String(16),  default="internal", nullable=False)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    processed     = Column(Boolean, default=False, nullable=False)
    processed_at  = Column(DateTime)

    def __repr__(self) -> str:
        return (
            f"<EventQueueRow {self.event_type} job={self.job_name!r} "
            f"processed={self.processed}>"
        )


# ---------------------------------------------------------------------------
# 4. Event History table  (immutable audit log)
# ---------------------------------------------------------------------------

class EventHistoryRow(Base):
    """
    Immutable append-only copy of every event that was ever processed.

    This is what ``autosys autorep -E`` or the WCC event history page
    queries.  Rows are never updated or deleted.
    """
    __tablename__ = "ujo_proc_event"
    __table_args__ = (
        Index("ix_event_hist_job_date", "job_name", "created_at"),
    )

    event_id      = Column(String(36),  primary_key=True)
    event_type    = Column(String(32),  nullable=False)
    job_name      = Column(String(255))
    status        = Column(Integer)      # was new_status
    global_name   = Column(String(128))
    global_value  = Column(Text)
    comment       = Column(Text)         # for COMMENT
    resource_name = Column(String(255))  # for RELEASE_RESOURCE
    source        = Column(String(16),  nullable=False)
    created_at    = Column(DateTime,    nullable=False)
    metadata_json = Column(Text)         # JSON blob with extra context

    def __repr__(self) -> str:
        return f"<EventHistoryRow {self.event_type} job={self.job_name!r} at={self.created_at}>"


# ---------------------------------------------------------------------------
# 5. Global Variables table
# ---------------------------------------------------------------------------

class GlobalVariableRow(Base):
    """
    AutoSys global variables — set via SET_GLOBAL events or ``set_global`` CLI.

    Values are readable in job commands as %%VARNAME%% and testable in
    conditions as ``value(VARNAME) = "expected"``.

    Note: built-in variables (%%DATE%%, %%YYYY%%, etc.) are NOT stored here —
    they are resolved at command-expansion time by variable_substitution.py.
    """
    __tablename__ = "ujo_glob_var"

    global_name  = Column(String(128), primary_key=True)    # always uppercase
    value       = Column(Text, nullable=False)
    updated_at  = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)
    updated_by  = Column(String(128))

    def __repr__(self) -> str:
        return f"<GlobalVariableRow {self.global_name}={self.value!r}>"


# ---------------------------------------------------------------------------
# 6. Calendars table
# ---------------------------------------------------------------------------

class CalendarRow(Base):
    """
    Named calendar — a list of dates used for scheduling.

    run_calendar: job only runs on dates listed here.
    exclude_calendar: job SKIPS dates listed here (even if days_of_week matches).

    The dates are stored as a JSON array of "YYYY-MM-DD" strings.
    """
    __tablename__ = "ujo_calendar"

    calendar_name = Column(String(128), primary_key=True)
    dates_json    = Column(Text, nullable=False)   # JSON: ["2025-01-01", ...]
    description   = Column(Text)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at    = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<CalendarRow {self.calendar_name!r}>"


# ---------------------------------------------------------------------------
# 7. Alarms table
# ---------------------------------------------------------------------------

class AlarmRow(Base):
    """
    Alarms raised by the alarm_manager.

    Active alarms (cleared_at IS NULL) show up in the WCC alarm console and
    can be forwarded to NSM Event Management (port 1721).
    """
    __tablename__ = "alarms"
    __table_args__ = (
        Index("ix_alarms_active", "cleared_at"),
    )

    alarm_id            = Column(String(36),  primary_key=True)   # UUID
    job_name            = Column(String(255), ForeignKey("ujo_job.job_name"), nullable=False, index=True)
    run_id              = Column(String(36),  ForeignKey("ujo_job_runs.run_id"))
    alarm_type          = Column(String(32),  nullable=False, index=True)
    message             = Column(Text,        nullable=False)
    job_status_at_raise = Column(String(32))
    raised_at           = Column(DateTime,    nullable=False)
    cleared_at          = Column(DateTime)
    cleared_by          = Column(String(128))
    notified            = Column(Boolean, default=False, nullable=False)

    # --- Relationships ---
    job = relationship("JobRow",    back_populates="alarms")
    run = relationship("JobRunRow", back_populates="alarms")

    def __repr__(self) -> str:
        state = "active" if self.cleared_at is None else "cleared"
        return f"<AlarmRow {self.alarm_type} job={self.job_name!r} {state}>"


# ---------------------------------------------------------------------------
# 8. Virtual Resources table
# ---------------------------------------------------------------------------

class VirtualResourceRow(Base):
    """
    Virtual resources control job concurrency on a machine.

    A job with job_load=2 on a machine with max_load=4 can have at most
    two such jobs running simultaneously.  Jobs that would exceed max_load
    wait in QUE_WAIT state until a slot is free.
    """
    __tablename__ = "ujo_resource"

    resource_name = Column(String(255), primary_key=True)
    max_load      = Column(Integer, nullable=False)
    current_load  = Column(Integer, default=0, nullable=False)
    description   = Column(Text)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<VirtualResourceRow {self.resource_name} {self.current_load}/{self.max_load}>"


# ---------------------------------------------------------------------------
# 9. Machines table  (System Agent registry)
# ---------------------------------------------------------------------------

class MachineRow(Base):
    """
    Registry of System Agents known to the Scheduler.

    The machine column in a JIL CMD job references machine_name here.
    The Scheduler ACE looks up host:port to dispatch jobs to the correct
    System Agent process.

    Maps to: CA AutoSys "machine definition" (also defined via JIL with
    insert_machine: syntax in real AutoSys).
    """
    __tablename__ = "ujo_machine"

    machine_name    = Column(String(255), primary_key=True)
    host            = Column(String(255), nullable=False)
    port            = Column(Integer, default=7520, nullable=False)
    status          = Column(String(16), default="UNKNOWN", nullable=False, index=True)
    last_heartbeat  = Column(DateTime)
    description     = Column(Text)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    # --- Relationships ---
    # viewonly=True avoids FK ambiguity with the machine string column on JobRow.
    # Use this to navigate from a MachineRow to the jobs assigned to it.
    jobs = relationship(
        "JobRow",
        primaryjoin="MachineRow.machine_name == foreign(JobRow.machine)",
        lazy="select",
        viewonly=True,
    )

    def __repr__(self) -> str:
        return f"<MachineRow {self.machine_name!r} {self.host}:{self.port} {self.status}>"


# ---------------------------------------------------------------------------
# 10. Job Output table  (inline stdout/stderr storage per run)
# ---------------------------------------------------------------------------

class JobOutputRow(Base):
    """
    Stores captured stdout/stderr output from a single job execution.

    One row per output line.  The System Agent writes to this table in real
    time as the subprocess produces output.  This lets operators view partial
    output of a running job via ``autosys jobs tail``.

    In real AutoSys this data lives in flat files on the agent machine
    (the path is stored in ``job_runs.stdout_path``).  We centralise it in
    the DB so that any CLI or API can read it without SSH access.

    Columns
    -------
    run_id:
        Foreign key → ``job_runs.run_id`` UUID.  All lines for one execution
        share the same run_id.
    job_name:
        Denormalised for fast ``WHERE job_name = ?`` queries from the tail
        command without a join.
    line_no:
        1-based sequential line number within this run.  Merged-stream output
        (stdout + stderr interleaved) is ordered by ``line_no ASC``.
    stream:
        ``'stdout'`` or ``'stderr'``.  Phase 5 merges both into stdout by
        default (``stderr=subprocess.STDOUT``), but the column is kept for
        completeness.
    content:
        The raw text of the line (newline stripped).
    """

    __tablename__ = "ujo_job_output"
    __table_args__ = (
        Index("ix_job_output_run",      "run_id", "line_no"),
        Index("ix_job_output_job_name", "job_name"),
    )

    id           = Column(Integer, primary_key=True, autoincrement=True)
    run_id       = Column(String(255), ForeignKey("ujo_job_runs.run_id"), nullable=False, index=True)
    job_name     = Column(String(255), ForeignKey("ujo_job.job_name"), nullable=False, index=True)
    line_no      = Column(Integer,     nullable=False)
    stream       = Column(String(6),   nullable=False, default="stdout")
    content      = Column(Text,        nullable=False, default="")
    created_at   = Column(DateTime,    default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<JobOutputRow run={self.run_id[:8]}… "
            f"line={self.line_no} [{self.stream}] {self.content[:40]!r}>"
        )


# ---------------------------------------------------------------------------
# 11. Job Types table  (user-defined job types)
# ---------------------------------------------------------------------------

class JobTypeRow(Base):
    """User-defined job types with custom command templates."""
    __tablename__ = "ujo_job_type"

    type_name        = Column(String(128), primary_key=True)
    command_template = Column(Text)
    description      = Column(Text)
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<JobTypeRow {self.type_name}>"


# ---------------------------------------------------------------------------
# 12. Monitor / Report table  (monbro definitions)
# ---------------------------------------------------------------------------

class MonitorRow(Base):
    """Monitor and report definitions (file watchers, CPU monitors, etc.)."""
    __tablename__ = "ujo_monbro"

    monbro_name      = Column(String(128), primary_key=True)
    monbro_type      = Column(String(32), nullable=False)
    job_name         = Column(String(255), ForeignKey("ujo_job.job_name"), nullable=True)
    attributes_json  = Column(Text)
    created_at       = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<MonitorRow {self.monbro_name} type={self.monbro_type}>"


# ---------------------------------------------------------------------------
# 13. Blob table  (binary large objects tied to jobs)
# ---------------------------------------------------------------------------

class BlobRow(Base):
    """Binary large objects associated with jobs (scripts, config files)."""
    __tablename__ = "ujo_blob"

    blob_id     = Column(Integer, primary_key=True, autoincrement=True)
    blob_name   = Column(String(255), nullable=False, index=True)
    job_name    = Column(String(255), ForeignKey("ujo_job.job_name"), nullable=True)
    content     = Column(Text, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<BlobRow {self.blob_name} job={self.job_name}>"


# ---------------------------------------------------------------------------
# 14. Glob table  (global named blobs)
# ---------------------------------------------------------------------------

class GlobRow(Base):
    """Global named blobs not tied to a specific job."""
    __tablename__ = "ujo_glob"

    glob_name   = Column(String(255), primary_key=True)
    content     = Column(Text, nullable=False)
    created_at  = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<GlobRow {self.glob_name}>"


# ---------------------------------------------------------------------------
# 15. External Instance table  (cross-instance dependencies)
# ---------------------------------------------------------------------------

class ExternalInstanceRow(Base):
    """External AutoSys instance definitions for cross-instance job dependencies."""
    __tablename__ = "ujo_xinst"

    xinst_name      = Column(String(128), primary_key=True)
    instance_name   = Column(String(255), nullable=False)
    host            = Column(String(255), nullable=False)
    port            = Column(Integer, default=9000, nullable=False)
    description     = Column(Text)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<ExternalInstanceRow {self.xinst_name} → {self.host}:{self.port}>"


# ---------------------------------------------------------------------------
# 16. Connection Profile table  (Hadoop, AWS, Hive, etc.)
# ---------------------------------------------------------------------------

class ConnectionProfileRow(Base):
    """Connection profiles for cloud and enterprise integrations."""
    __tablename__ = "ujo_connection_profile"

    profile_name    = Column(String(128), primary_key=True)
    profile_type    = Column(String(64), nullable=False)
    attributes_json = Column(Text)
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<ConnectionProfileRow {self.profile_name} type={self.profile_type}>"


# ---------------------------------------------------------------------------
# 17. Scheduler Lock table  (HA distributed lock for tie-breaker)
# ---------------------------------------------------------------------------

class SchedulerLockRow(Base):
    """
    Distributed lock for HA tie-breaker scheduler.

    Only one EventProcessor can hold the lock at a time.  The lock holder
    updates ``last_heartbeat`` every tick.  If the heartbeat is stale
    (older than ``heartbeat_timeout`` seconds), a standby EPS can steal it.
    """
    __tablename__ = "ujo_scheduler_lock"

    lock_id          = Column(String(64), primary_key=True)
    instance_id      = Column(String(128), nullable=False)
    role             = Column(String(32), nullable=False, default="primary")
    is_active        = Column(Boolean, default=True, nullable=False)
    last_heartbeat   = Column(DateTime, default=datetime.utcnow, nullable=False)
    acquired_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    heartbeat_timeout = Column(Integer, default=5, nullable=False)

    def __repr__(self) -> str:
        return f"<SchedulerLockRow {self.lock_id} active={self.is_active} by={self.instance_id}>"


# ---------------------------------------------------------------------------
# 18. Report Row table  (generated reports)
# ---------------------------------------------------------------------------

class ReportRow(Base):
    """Generated report metadata and content."""
    __tablename__ = "ujo_report"

    report_id     = Column(String(64), primary_key=True)
    report_type   = Column(String(32), nullable=False)
    date_from     = Column(DateTime, nullable=False)
    date_to       = Column(DateTime, nullable=False)
    content_json  = Column(Text)
    created_at    = Column(DateTime, default=datetime.utcnow, nullable=False)

    def __repr__(self) -> str:
        return f"<ReportRow {self.report_id} type={self.report_type}>"
