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
Identity        : job_name, job_type, description, owner, permission
Execution       : command, machine, run_window, profile,
                  std_out_file, std_err_file, std_in_file
BOX container   : box_name, box_success, box_failure, box_terminator, job_terminator
Scheduling      : start_times, start_mins, days_of_week,
                  run_calendar, exclude_calendar,
                  date_conditions, term_run_time
Dependencies    : condition
Reliability     : n_retrys, max_exit_success, max_run_alarm, min_run_alarm,
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
from autosys.timeutil import utcnow
from typing import Any, Optional, List, Annotated

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
        min_length=1,
        description=(
            "Unique job identifier.  The PDF restricts object names to "
            "[A-Za-z0-9_.:#@-]; names outside that set are accepted (a real "
            "estate can contain them) and reported by "
            "``job_name_warnings()``."
        ),
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
    group: Optional[str] = Field(None, description="Job group name.")

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
    envvars: Optional[str] = Field(None, description="Environment variables.")
    chk_files: Optional[str] = Field(None, description="Check files.")
    ulimit: Optional[str] = Field(None, description="ulimit settings.")

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
            "Dependency condition expression whose evaluation to True triggers the BOX to be marked SUCCESS. "
            "Defaults to: all child jobs reached SUCCESS."
        ),
    )
    box_failure: Optional[str] = Field(
        None,
        description="Dependency condition expression whose evaluation to True triggers the BOX to be marked FAILURE.",
    )
    box_terminator: bool = Field(
        False,
        description=(
            "If True, when this job completes with FAILURE or TERMINATED "
            "the parent BOX is immediately forced to TERMINATED, without "
            "waiting for other sibling jobs to finish."
        ),
    )
    job_terminator: bool = Field(
        False,
        description=(
            "If True, this job is itself terminated whenever the parent "
            "BOX completes with FAILURE or TERMINATED (e.g. because a "
            "sibling box_terminator job failed).  The mirror image of "
            "box_terminator: box_terminator propagates child → box, "
            "job_terminator propagates box → child."
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
        ge=0,
        description=(
            "Maximum run time in minutes (0 = no limit, the AutoSys default).  "
            "If the job is still RUNNING after "
            "this many minutes the Scheduler sends KILLJOB automatically."
        ),
    )
    avg_runtime: Optional[int] = Field(None, description="Average runtime in minutes.")
    must_complete_times: Optional[str] = Field(None, description="Must complete times.")
    must_start_times: Optional[str] = Field(None, description="Must start times.")
    priority: Optional[int] = Field(None, description="Job priority.")
    timezone: Optional[str] = Field(None, description="Timezone for the job.")

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
    max_exit_success: Optional[int] = Field(
        None,
        ge=0,
        description=(
            "Maximum exit code that is still considered SUCCESS. "
            "If the process exits with a code <= max_exit_success the job "
            "is marked SUCCESS; any higher code is FAILURE. "
            "Defaults to None (only exit code 0 = SUCCESS)."
        ),
    )
    max_run_alarm: Optional[int] = Field(
        None,
        ge=0,
        description=(
            "Raise an alarm if the job has been RUNNING for more than this "
            "many minutes (watchdog timer).  The job continues running — "
            "only an alarm is raised, not a kill."
        ),
    )
    min_run_alarm: Optional[int] = Field(
        None,
        ge=0,
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
    fail_codes: Optional[str] = Field(None, description="Specific exit codes that mean FAILURE.")

    # ------------------------------------------------------------------
    # Virtual resources  (concurrency control)
    # ------------------------------------------------------------------
    job_load: int = Field(
        1,
        ge=0,
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
    resources: Optional[str] = Field(None, description="Virtual resources required.")
    auto_hold: bool = Field(False, description="Automatically place job ON_HOLD when created.")

    # ------------------------------------------------------------------
    # Extended attributes (Phase 4)
    # ------------------------------------------------------------------
    auto_delete: bool = Field(
        False,
        description="(-1 / unset = False; N>0 hours is coerced to True.)  If True, auto-delete the job definition after it reaches a terminal state.",
    )
    application: Optional[str] = Field(
        None,
        description="Associate this job with an application name for grouping and filtering.",
    )
    sub_application: Optional[str] = Field(
        None,
        description="Sub-grouping within the application.",
    )
    command_timeout: Optional[int] = Field(
        None,
        ge=1,
        description="Kill the command after this many seconds (distinct from max_run_alarm which only raises an alarm).",
    )
    continuous: bool = Field(
        False,
        description="If True, the job monitors continuously (e.g. FILEWATCH loops instead of one-shot).",
    )
    cpu_usage: Optional[str] = Field(
        None,
        pattern=r"^(?i:FREE|USED)$",
        description="OMCPU jobs: monitor available (FREE, the default) or used (USED) CPU.",
    )
    disk_space: Optional[str] = Field(
        None,
        pattern=r"^(?i:FREE|USED)$",
        description="OMD jobs: monitor available (FREE, the default) or used (USED) disk space.",
    )
    auth_string: Optional[str] = Field(
        None,
        description="Authorization string passed to the agent for execution.",
    )
    connection_retry: Optional[int] = Field(
        None,
        ge=0,
        description="Number of times to retry connection for CONNECT/WEBSERVICE jobs.",
    )
    connection_timeout: Optional[int] = Field(
        None,
        ge=1,
        description="Connection timeout in seconds for CONNECT/WEBSERVICE jobs.",
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
    # (The vendor-doc vocabulary ftp_server_name / ftp_remote_name / ... is kept
    # in ``extra_attrs`` and mapped onto the legacy fields by FtpJob.)

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
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    # ------------------------------------------------------------------
    # Attributes we have no dedicated field for (kept verbatim, in order)
    # ------------------------------------------------------------------
    extra_attrs: dict[str, str] = Field(
        default_factory=dict,
        description="JIL attributes without a dedicated model field (string values).",
    )

    # ------------------------------------------------------------------
    # Validators
    # ------------------------------------------------------------------

    @model_validator(mode="before")
    @classmethod
    def _collect_extras(cls, data: Any) -> Any:
        """Move every input key that is not a model field into ``extra_attrs``."""
        if not isinstance(data, dict):
            return data
        fields = cls.model_fields
        known = {}
        extras: dict[str, str] = dict(data.get("extra_attrs") or {})
        for k, v in data.items():
            if k in fields:
                known[k] = v
            elif k in ("job_name", "job_type"):
                known[k] = v
            else:
                extras[k] = v if isinstance(v, str) else _extra_str(v)
        known["extra_attrs"] = extras
        return known

    @field_validator("auto_delete", mode="before")
    @classmethod
    def _auto_delete_unset(cls, v: object) -> object:
        """autorep -q emits ``auto_delete: -1`` (unset); treat as False, N>0 as True."""
        if isinstance(v, str) and v.strip().lstrip("-").isdigit():
            v = int(v.strip())
        if isinstance(v, int) and not isinstance(v, bool):
            return v > 0
        return v

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

def _extra_str(v: object) -> str:
    if isinstance(v, bool):
        return "1" if v else "0"
    return str(v)


def _extra_get(job: Job, key: str) -> Optional[str]:
    """Case-insensitive lookup in ``job.extra_attrs``."""
    lk = key.lower()
    for k, v in job.extra_attrs.items():
        if k.lower() == lk and v not in (None, ""):
            return v
    return None


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
      • BOX → SUCCESS when all children reach SUCCESS
        (or when the designated box_success job succeeds).
      • BOX → FAILURE when any child reaches FAILURE and no box_failure
        override is set (or when the designated box_failure job fails).
      • BOX → TERMINATED immediately when a child with box_terminator: 1
        reaches FAILURE or TERMINATED, without waiting for its siblings.
      • Any child with job_terminator: 1 that hasn't finished when the BOX
        reaches FAILURE or TERMINATED is itself force-terminated.
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
        self._map_pdf_vocabulary()
        missing = [f for f in ("ftp_server", "ftp_user", "ftp_type", "ftp_src", "ftp_dest")
                   if not getattr(self, f)]
        if missing:
            raise ValueError(f"FTP job missing required fields: {missing}")
        return self

    def _map_pdf_vocabulary(self) -> None:
        """Fill legacy ftp_* fields from the vendor-doc names when absent.

        ftp_server_name -> ftp_server; ftp_transfer_direction UPLOAD/DOWNLOAD
        (default DOWNLOAD) -> ftp_type PUT/GET; DOWNLOAD: src=remote,
        dest=local; UPLOAD: src=local, dest=remote; ftp_user defaults to the
        owner (part before '@') else 'anonymous'.  Only kicks in when the
        vendor-doc server/remote/local names are all present.
        """
        server = _extra_get(self, "ftp_server_name")
        remote = _extra_get(self, "ftp_remote_name")
        local = _extra_get(self, "ftp_local_name")
        if not (server and remote and local):
            return
        direction = (_extra_get(self, "ftp_transfer_direction") or "DOWNLOAD").upper()
        put = direction == "UPLOAD"
        if not self.ftp_server:
            self.ftp_server = server
        if not self.ftp_type:
            self.ftp_type = FtpType.PUT.value if put else FtpType.GET.value
        if not self.ftp_src:
            self.ftp_src = local if put else remote
        if not self.ftp_dest:
            self.ftp_dest = remote if put else local
        if not self.ftp_user:
            owner = (self.owner or "").split("@")[0]
            self.ftp_user = owner or "anonymous"


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

class SapJob(Job):
    job_type: JobType = JobType.SAP

class PeoplesoftJob(Job):
    job_type: JobType = JobType.PEOPLESOFT

class InformaticaJob(Job):
    job_type: JobType = JobType.INFORMATICA

class MicrofocusJob(Job):
    job_type: JobType = JobType.MICROFOCUS

class WebserviceJob(Job):
    job_type: JobType = JobType.WEBSERVICE

class RemotecmdJob(Job):
    job_type: JobType = JobType.REMOTECMD

class WolJob(Job):
    job_type: JobType = JobType.WOL

class UserdefinedJob(Job):
    job_type: JobType = JobType.USERDEFINED


# ---------------------------------------------------------------------------
# Factory function — build the right subclass from a raw JIL dict
# ---------------------------------------------------------------------------

_JOB_TYPE_MAP: dict[str, type[Job]] = {
    JobType.CMD.value:       CmdJob,
    JobType.BOX.value:       BoxJob,
    JobType.FILEWATCH.value: FilewatchJob,
    JobType.FTP.value:       FtpJob,
    JobType.CONNECT.value:   ConnectJob,
    JobType.SAP.value:       SapJob,
    JobType.PEOPLESOFT.value: PeoplesoftJob,
    JobType.INFORMATICA.value: InformaticaJob,
    JobType.MICROFOCUS.value: MicrofocusJob,
    JobType.WEBSERVICE.value: WebserviceJob,
    JobType.REMOTECMD.value: RemotecmdJob,
    JobType.WOL.value:       WolJob,
    JobType.USERDEFINED.value: UserdefinedJob,
}

# Vendor-doc job_type spellings -> canonical internal value.  FT is a
# different job type (File Trigger), NOT an alias of FTP.
_JOB_TYPE_ALIASES: dict[str, str] = {
    "C": "CMD",
    "B": "BOX",
    "F": "FILEWATCH",
    "FW": "FILEWATCH",
    "PS": "PEOPLESOFT",
}


def normalize_job_type(raw: object) -> str:
    """Canonical JobType value for a JIL job_type token (case-insensitive).

    Missing/empty -> "CMD" (the AutoSys default).  Unknown values are returned
    upper-cased so pydantic reports them.
    """
    if raw is None:
        return JobType.CMD.value
    if isinstance(raw, JobType):
        return raw.value
    t = str(raw).strip().upper()
    if not t:
        return JobType.CMD.value
    return _JOB_TYPE_ALIASES.get(t, t)


_VALID_JOB_TYPES = frozenset(t.value for t in JobType)


def parse_job(data: dict) -> Job:
    """
    Build the correct Job subclass from a raw dictionary.

    job_type is case-insensitive, accepts the vendor aliases (c, b, f, fw, ps)
    and defaults to CMD when absent.  Every key that is not a model field is
    preserved in ``Job.extra_attrs``.  Types without a dedicated subclass use
    the generic ``Job`` (no required command/machine).

    >>> j = parse_job({"job_name": "x", "job_type": "c",
    ...                "command": "ls", "machine": "m"})
    >>> type(j).__name__
    'CmdJob'
    """
    data = dict(data)
    canon = normalize_job_type(data.get("job_type"))
    if canon not in _VALID_JOB_TYPES:
        # A user-defined job type (insert_job_type) or a type this simulator
        # does not know.  The parser cannot know the site's user-defined
        # types, so keep the job as USERDEFINED with the original name in
        # extra_attrs["user_job_type"] (jil_writer emits it back).
        extras = dict(data.get("extra_attrs") or {})
        extras["user_job_type"] = str(data.get("job_type")).strip()
        data["extra_attrs"] = extras
        canon = JobType.USERDEFINED.value
    data["job_type"] = canon
    cls = _JOB_TYPE_MAP.get(canon, Job)
    return cls.model_validate(data)


def job_name_warnings(name: str) -> list[str]:
    """Non-fatal: object names outside the PDF's character set."""
    import re as _re
    if _re.fullmatch(r"[A-Za-z0-9_.:#@-]+", name or ""):
        return []
    return [f"job name {name!r} contains characters outside "
            "[A-Za-z0-9_.:#@-] (accepted as written)"]


def parse_job_lenient(data: dict) -> tuple[Job, list[str]]:
    """
    Like :func:`parse_job` but never raises for bad *content*.

    Returns ``(job, warnings)``.  Strategy, in order:

    1. the strict parse;
    2. attributes whose value is invalid for their typed field are moved into
       ``extra_attrs`` verbatim (nothing lost) and the parse is retried;
    3. definitions that break a cross-field rule (CMD without command/machine,
       BOX with a command, an FTP job missing fields ...) are built as the
       generic :class:`Job`, which enforces none of those rules.
    """
    from pydantic import ValidationError

    warnings: list[str] = []
    data = dict(data)
    for _ in range(64):
        try:
            return parse_job(data), warnings
        except ValidationError as exc:
            moved = False
            for err in exc.errors():
                loc = err.get("loc") or ()
                field = loc[0] if loc else None
                if (isinstance(field, str) and field in data
                        and field not in ("job_name", "job_type", "extra_attrs")):
                    extras = dict(data.get("extra_attrs") or {})
                    extras[field] = data.pop(field) if isinstance(data[field], str) \
                        else _extra_str(data.pop(field))
                    data["extra_attrs"] = extras
                    warnings.append(
                        f"invalid value for {field}: {err.get('msg')} "
                        "(kept as text in extra_attrs)")
                    moved = True
            if not moved:
                msgs = "; ".join(str(e.get("msg", "")).replace("Value error, ", "")
                                 for e in exc.errors())
                warnings.append(f"{msgs} (loaded with the generic job model)")
                break
    canon = normalize_job_type(data.get("job_type"))
    if canon not in _VALID_JOB_TYPES:
        extras = dict(data.get("extra_attrs") or {})
        extras["user_job_type"] = str(data.get("job_type")).strip()
        data["extra_attrs"] = extras
        canon = JobType.USERDEFINED.value
    data["job_type"] = canon
    return Job.model_validate(data), warnings


# ---------------------------------------------------------------------------
# Non-fatal attribute / job-type consistency check
# ---------------------------------------------------------------------------

# prefix -> job types that legitimately use it
_PREFIX_FAMILIES: dict[str, frozenset[str]] = {
    "ftp_":   frozenset({"FTP", "FT"}),
    "sap_":   frozenset({"SAP", "SAPBDC", "SAPBWIP", "SAPBWPC", "SAPDA",
                         "SAPEVT", "SAPJC", "SAPPM"}),
    "oozie_": frozenset({"OOZIE"}),
    "j2ee_":  frozenset({"ENTYBEAN", "SESSBEAN", "JAVARMI", "POJO", "JMSPUB",
                         "JMSSUB", "JMXMAG", "JMXMAS", "JMXMC", "JMXMOP",
                         "JMXMREM", "JMXSUB"}),
    "zos_":   frozenset({"ZOS", "ZOSM", "ZOSDST"}),
}


def attribute_job_type_warnings(job: Job) -> list[str]:
    """Return warnings for attributes that belong to another job type family.

    Never raises.  Only covers prefixes with an unambiguous family (ftp_, sap_,
    oozie_, j2ee_, zos_).
    """
    jtype = job.job_type.value if isinstance(job.job_type, JobType) else str(job.job_type)
    names = list(job.extra_attrs.keys())
    for fname in Job.model_fields:
        if getattr(job, fname, None) not in (None, "", [], False, 0) and fname.startswith("ftp_"):
            names.append(fname)
    warnings: list[str] = []
    seen: set[str] = set()
    for name in names:
        low = name.lower()
        for prefix, types in _PREFIX_FAMILIES.items():
            if low.startswith(prefix) and jtype not in types:
                if name in seen:
                    continue
                seen.add(name)
                warnings.append(
                    f"attribute '{name}' does not apply to job_type {jtype} "
                    f"(expected one of: {', '.join(sorted(types))})"
                )
    return warnings
