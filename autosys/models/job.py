"""
Pydantic models for AutoSys job definitions.

Hierarchy
---------
Job           — base class holding all ~50 JIL attributes
  ├── CmdJob       — job_type: CMD  (shell command)
  ├── BoxJob       — job_type: BOX  (workflow container)
  ├── FilewatchJob — job_type: FILEWATCH
  ├── FtpJob       — job_type: FTP
  └── ConnectJob   — job_type: CONNECT

The discriminated-union helper ``parse_job()`` at the bottom lets
you build the right subclass from a raw dict produced by the JIL parser.

Real AutoSys reference attributes covered
------------------------------------------
Identity        : job_name, job_type, description, owner, permission, run_as_user
Execution       : command, machine, run_window, profile,
                  std_out_file, std_err_file, std_in_file
BOX container   : box_name, box_success, box_failure, box_terminator
Scheduling      : start_times, start_mins, days_of_week,
                  run_calendar, exclude_calendar,
                  date_conditions, term_run_time
Dependencies    : condition
Reliability     : n_retrys, max_run_alarm, min_run_alarm,
                  alarm_if_fail, alarm_if_terminated
Resources       : job_load, max_load
Notifications   : notification_msg, notification_emailaddress,
                  notification_type, send_report
FTP-specific    : ftp_server, ftp_user, ftp_type, ftp_src, ftp_dest
FILEWATCH-spec. : watch_file, watch_file_min_size, watch_interval
Runtime state   : status, last_start, last_end, last_run_date
Metadata        : created_at, updated_at
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional, List, Annotated

from pydantic import BaseModel, Field, field_validator, model_validator

from autosys.models.enums import (
    JobType,
    JobStatus,
    DayOfWeek,
    FtpType,
    NotificationType,
)


# ---------------------------------------------------------------------------
# Base Job model — every JIL attribute lives here
# ---------------------------------------------------------------------------

class Job(BaseModel):
    """
    Base representation of an AutoSys job definition.

    Every field corresponds to a JIL attribute.  Fields that are not
    applicable to a given job_type are left as None and validated by the
    concrete subclasses.
    """

    model_config = {"use_enum_values": True}

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------
    job_name: str = Field(
        ...,
        description="Unique job identifier. Must match [A-Za-z0-9_.-]+.",
        pattern=r"^[A-Za-z0-9_.:-]+$",
    )
    job_type: JobType = Field(..., description="BOX | CMD | FTP | FILEWATCH | CONNECT")
    description: Optional[str] = Field(None, description="Free-text description.")
    owner: Optional[str] = Field(None, description="OS user that owns this job definition.")
    permission: Optional[str] = Field(
        None,
        description=(
            "AutoSys permission string, e.g. 'gx,mx,me' "
            "(group-execute, machine-execute, machine-edit)."
        ),
    )
    run_as_user: Optional[str] = Field(
        None,
        description="OS user to run the job as (may differ from owner).",
    )

    # ------------------------------------------------------------------
    # Execution  (CMD jobs)
    # ------------------------------------------------------------------
    command: Optional[str] = Field(
        None,
        description="Shell command to execute. Supports %%VAR%% substitution.",
    )
    machine: Optional[str] = Field(
        None,
        description=(
            "Target machine name (must exist in the machines table). "
            "The Scheduler ACE routes the job to the System Agent on this host."
        ),
    )
    run_window: Optional[str] = Field(
        None,
        description=(
            "Time window during which the job is allowed to run, e.g. '08:00-18:00'. "
            "Job will not start outside this window."
        ),
    )
    profile: Optional[str] = Field(
        None,
        description="Shell profile / environment script to source before executing command.",
    )
    std_out_file: Optional[str] = Field(None, description="Path to capture stdout.")
    std_err_file: Optional[str] = Field(None, description="Path to capture stderr.")
    std_in_file: Optional[str] = Field(None, description="Path to redirect as stdin.")

    # ------------------------------------------------------------------
    # BOX container  (BOX jobs and their children)
    # ------------------------------------------------------------------
    box_name: Optional[str] = Field(
        None,
        description=(
            "Name of the parent BOX job.  When set, this job only runs "
            "while its BOX is ACTIVATED."
        ),
    )
    box_success: Optional[str] = Field(
        None,
        description=(
            "Job name whose SUCCESS triggers the BOX to be marked SUCCESS. "
            "Defaults to: all child jobs reached SUCCESS."
        ),
    )
    box_failure: Optional[str] = Field(
        None,
        description="Job name whose FAILURE triggers the BOX to be marked FAILURE.",
    )
    box_terminator: bool = Field(
        False,
        description=(
            "If True, when this job reaches a terminal state it also "
            "terminates the parent BOX (and all remaining sibling jobs)."
        ),
    )

    # ------------------------------------------------------------------
    # Scheduling
    # ------------------------------------------------------------------
    start_times: List[str] = Field(
        default_factory=list,
        description=(
            'List of HH:MM start times, e.g. ["06:00", "18:00"]. '
            "The Scheduler fires a STARTJOB event at each listed time "
            "on matching days."
        ),
    )
    start_mins: Optional[str] = Field(
        None,
        description=(
            "Comma-separated minute offsets within every hour, e.g. '0,15,30,45'. "
            "Alternative to start_times for sub-hourly jobs."
        ),
    )
    days_of_week: List[DayOfWeek] = Field(
        default_factory=list,
        description=(
            "Days on which the job is scheduled. "
            "Empty list means not scheduled by time (dependency-only)."
        ),
    )
    run_calendar: Optional[str] = Field(
        None,
        description="Named calendar (from calendars table) defining dates the job SHOULD run.",
    )
    exclude_calendar: Optional[str] = Field(
        None,
        description="Named calendar of dates to SKIP even if days_of_week matches.",
    )
    date_conditions: bool = Field(
        False,
        description=(
            "If True, the condition expression is evaluated against job "
            "statuses from the *current date* only (not prior-day carry-overs)."
        ),
    )
    term_run_time: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Maximum run time in minutes.  If the job is still RUNNING after "
            "this many minutes the Scheduler sends KILLJOB automatically."
        ),
    )

    # ------------------------------------------------------------------
    # Dependencies
    # ------------------------------------------------------------------
    condition: Optional[str] = Field(
        None,
        description=(
            "Dependency condition expression. Evaluated by condition_evaluator.py. "
            "Examples:\n"
            "  success(extract_sales)\n"
            "  success(job_a) & success(job_b)\n"
            "  success(job_a) & (success(job_b) | failure(job_c))\n"
            "  value(MY_GLOBAL) = \"YES\""
        ),
    )

    # ------------------------------------------------------------------
    # Reliability
    # ------------------------------------------------------------------
    n_retrys: int = Field(
        0,
        ge=0,
        description=(
            "Number of automatic retries on FAILURE before the job is "
            "marked permanently FAILURE.  Each retry transitions the job "
            "through RESTART → STARTING → RUNNING."
        ),
    )
    max_run_alarm: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Raise an alarm if the job has been RUNNING for more than this "
            "many minutes (watchdog timer).  The job continues running — "
            "only an alarm is raised, not a kill."
        ),
    )
    min_run_alarm: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Raise an alarm if the job finishes in LESS than this many minutes "
            "(signals unexpectedly short / skipped processing)."
        ),
    )
    alarm_if_fail: bool = Field(
        False,
        description="Raise an ALARM_IF_FAIL alarm when the job reaches FAILURE state.",
    )
    alarm_if_terminated: bool = Field(
        False,
        description="Raise an ALARM_IF_TERMINATED alarm when the job is killed.",
    )

    # ------------------------------------------------------------------
    # Virtual resources  (concurrency control)
    # ------------------------------------------------------------------
    job_load: int = Field(
        1,
        ge=1,
        description=(
            "Units of virtual resource this job consumes while running. "
            "Used together with max_load on the resource definition."
        ),
    )
    max_load: Optional[int] = Field(
        None,
        ge=1,
        description=(
            "Maximum total job_load that may run simultaneously on this machine. "
            "Jobs that would exceed this wait in QUE_WAIT state."
        ),
    )

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    notification_msg: Optional[str] = Field(
        None,
        description="Message text sent in notification emails / NSM alerts.",
    )
    notification_emailaddress: Optional[str] = Field(
        None,
        description="Comma-separated email recipients for job notifications.",
    )
    notification_type: Optional[NotificationType] = Field(
        None,
        description="Delivery method: EMAIL | SNMP | NSM | REMEDY.",
    )
    send_report: bool = Field(
        False,
        description="If True, attach a run report to the notification.",
    )

    # ------------------------------------------------------------------
    # FTP-specific attributes  (job_type: FTP)
    # ------------------------------------------------------------------
    ftp_server: Optional[str] = Field(None, description="FTP server hostname.")
    ftp_user: Optional[str] = Field(None, description="FTP login username.")
    ftp_type: Optional[FtpType] = Field(None, description="GET | PUT | DEL")
    ftp_src: Optional[str] = Field(None, description="Source path (remote for GET, local for PUT).")
    ftp_dest: Optional[str] = Field(None, description="Destination path.")

    # ------------------------------------------------------------------
    # FILEWATCH-specific attributes  (job_type: FILEWATCH)
    # ------------------------------------------------------------------
    watch_file: Optional[str] = Field(
        None,
        description="Full path (or glob) of the file to watch for.",
    )
    watch_file_min_size: int = Field(
        0,
        ge=0,
        description=(
            "Minimum file size in bytes before the watch is satisfied. "
            "0 means any non-empty file (or just existence)."
        ),
    )
    watch_interval: int = Field(
        60,
        ge=1,
        description="Polling interval in seconds for FILEWATCH jobs.",
    )

    # ------------------------------------------------------------------
    # Runtime state  (written by the Scheduler / EPS, not the JIL parser)
    # ------------------------------------------------------------------
    status: JobStatus = Field(
        JobStatus.INACTIVE,
        description="Current job state.  Managed by state_machine.py.",
    )
    last_start: Optional[datetime] = Field(
        None,
        description="Timestamp when the most recent run started.",
    )
    last_end: Optional[datetime] = Field(
        None,
        description="Timestamp when the most recent run ended.",
    )
    last_run_date: Optional[date] = Field(
        None,
        description="Calendar date of the most recent run (YYYY-MM-DD).",
    )

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @field_validator("start_times", mode="before")
    @classmethod
    def normalise_start_times(cls, v: object) -> list[str]:
        """Accept a comma-separated string OR a list; normalise to list."""
        if isinstance(v, str):
            return [t.strip().strip('"') for t in v.split(",") if t.strip()]
        return list(v) if v else []

    @field_validator("days_of_week", mode="before")
    @classmethod
    def normalise_days(cls, v: object) -> list[str]:
        """Accept 'mo,tu,we,th,fr' or ['mo','tu',...] or 'all'."""
        if isinstance(v, str):
            raw = [d.strip().lower() for d in v.split(",") if d.strip()]
            if raw == ["all"]:
                return [d.value for d in DayOfWeek.all_days()]
            return raw
        return list(v) if v else []


# ---------------------------------------------------------------------------
# Concrete subclasses — add type-specific validation
# ---------------------------------------------------------------------------

class CmdJob(Job):
    """
    job_type: CMD — runs a shell command on a System Agent.

    Required JIL attributes: command, machine
    """
    job_type: JobType = JobType.CMD

    @model_validator(mode="after")
    def require_command_and_machine(self) -> "CmdJob":
        if not self.command:
            raise ValueError("CMD job requires 'command'")
        if not self.machine:
            raise ValueError("CMD job requires 'machine'")
        return self


class BoxJob(Job):
    """
    job_type: BOX — a workflow container for grouping child jobs.

    A BOX has its own schedule (start_times / days_of_week).  It
    activates (→ ACTIVATED state) at the scheduled time; child jobs
    then run based on their individual conditions.

    BOX success/failure rules:
      • BOX → SUCCESS when all non-box_terminator children reach SUCCESS
        (or when the designated box_success job succeeds).
      • BOX → FAILURE when any child reaches FAILURE
        (or when the designated box_failure job fails).
      • BOX → TERMINATED when a box_terminator child terminates.
    """
    job_type: JobType = JobType.BOX

    @model_validator(mode="after")
    def no_command_allowed(self) -> "BoxJob":
        if self.command:
            raise ValueError("BOX jobs cannot have a 'command' attribute")
        return self


class FilewatchJob(Job):
    """
    job_type: FILEWATCH — polls for a file to appear / reach minimum size.

    The System Agent checks for the file every watch_interval seconds.
    Once found (and >= watch_file_min_size bytes), it reports SUCCESS.
    """
    job_type: JobType = JobType.FILEWATCH

    @model_validator(mode="after")
    def require_watch_file(self) -> "FilewatchJob":
        if not self.watch_file:
            raise ValueError("FILEWATCH job requires 'watch_file'")
        return self


class FtpJob(Job):
    """
    job_type: FTP — transfers files between machines.

    The System Agent performs the transfer using the OS FTP/SFTP client.
    """
    job_type: JobType = JobType.FTP

    @model_validator(mode="after")
    def require_ftp_fields(self) -> "FtpJob":
        missing = [f for f in ("ftp_server", "ftp_user", "ftp_type", "ftp_src", "ftp_dest")
                   if not getattr(self, f)]
        if missing:
            raise ValueError(f"FTP job missing required fields: {missing}")
        return self


class ConnectJob(Job):
    """
    job_type: CONNECT — tests TCP connectivity to machine:port.

    Uses the machine attribute as the target host.
    Success means the TCP handshake completed within run_window (if set).
    """
    job_type: JobType = JobType.CONNECT

    @model_validator(mode="after")
    def require_machine(self) -> "ConnectJob":
        if not self.machine:
            raise ValueError("CONNECT job requires 'machine'")
        return self


# ---------------------------------------------------------------------------
# Factory function — build the right subclass from a raw JIL dict
# ---------------------------------------------------------------------------

_JOB_TYPE_MAP: dict[str, type[Job]] = {
    JobType.CMD:       CmdJob,
    JobType.BOX:       BoxJob,
    JobType.FILEWATCH: FilewatchJob,
    JobType.FTP:       FtpJob,
    JobType.CONNECT:   ConnectJob,
}


def parse_job(data: dict) -> Job:
    """
    Build the correct Job subclass from a raw dictionary.

    Typically called by the JIL parser after it has tokenised a JIL stanza.

    Example
    -------
    >>> j = parse_job({
    ...     "job_name": "extract_sales",
    ...     "job_type": "CMD",
    ...     "command": "/scripts/extract.sh",
    ...     "machine": "etl-server-01",
    ... })
    >>> type(j).__name__
    'CmdJob'
    >>> j.status
    'INACTIVE'
    """
    raw_type = data.get("job_type", "").upper()
    cls = _JOB_TYPE_MAP.get(raw_type, Job)
    return cls.model_validate(data)
