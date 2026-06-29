"""
Pydantic model for tracking individual AutoSys job execution history.

Background
----------
In real AutoSys, the **Scheduler ACE** (AutoSys Correlated Events) engine
maintains a run-time status table that is updated throughout a job's lifecycle.
This module provides the Python equivalent: a ``JobRun`` record written to the
``job_runs`` table (one row per run *attempt*, including every retry).

Relationship to other tables
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
* ``job_runs.job_name``  →  ``jobs.job_name``   (FK to the job definition)
* Each row is immutable once the run reaches a terminal state.
  In-flight updates (status, pid, etc.) are made via ``UPDATE … WHERE run_id=…``.

Retry semantics
~~~~~~~~~~~~~~~
AutoSys retries are not separate job definitions — they are new ``JobRun``
rows against the same ``job_name``, with ``retry_count`` incremented.
The Scheduler ACE creates the next ``JobRun`` row when it transitions the
job through ``RESTART → STARTING``.

Date tracking
~~~~~~~~~~~~~
``run_date`` stores the *logical* business date the job belongs to (i.e. the
date the Scheduler decided to fire the job), not necessarily the wall-clock
date.  This distinction matters for overnight jobs (e.g. a job scheduled for
23:55 on Monday is logically a Monday run even if it finishes Tuesday at 00:02).

Output path substitution
~~~~~~~~~~~~~~~~~~~~~~~~
AutoSys supports ``%%DATE%%`` and ``%%AUTORUN%%`` tokens in std_out_file /
std_err_file paths.  The System Agent expands these at runtime, so the actual
written path can differ from what is stored in the job definition.
``stdout_path`` / ``stderr_path`` here store the *resolved* path.
"""

from __future__ import annotations

import uuid
from datetime import datetime, date
from typing import Optional

from pydantic import BaseModel, Field

from autosys.models.enums import JobStatus


class JobRun(BaseModel):
    """
    A single execution attempt of an AutoSys job.

    One row is created per dispatch: the very first run produces a ``JobRun``
    with ``retry_count=0``; if the job fails and has retries remaining the
    Scheduler creates a second row with ``retry_count=1``, and so on.

    The EPS (Event Processor Service) and the System Agent both write to this
    model throughout a run's lifecycle:

    1. EPS writes the initial row (status=STARTING) when it dispatches the job.
    2. The System Agent updates ``start_time`` and ``pid`` once the subprocess
       is launched (status=RUNNING).
    3. The System Agent writes ``end_time`` and ``exit_code`` on completion
       (status=SUCCESS / FAILURE / TERMINATED).

    This record is the source of truth for the ``autorep -j <name>`` output
    and the AutoSys GUI job activity view.
    """

    model_config = {"use_enum_values": True}

    # ------------------------------------------------------------------
    # Identity
    # ------------------------------------------------------------------

    run_id: str = Field(
        default_factory=lambda: str(uuid.uuid4()),
        description=(
            "Primary key for this specific run attempt. "
            "Generated as a UUID4 string at row creation time. "
            "In real AutoSys, the equivalent is the internal 'run_num' integer "
            "stored in the ujo_job_runs Oracle table; we use a UUID here for "
            "distributed-safe generation without a sequence."
        ),
    )

    job_name: str = Field(
        ...,
        description=(
            "Name of the AutoSys job that was executed. "
            "Foreign key to ``jobs.job_name``. "
            "In JIL this is the ``insert_job`` or ``update_job`` identifier — "
            "e.g. ``ETL_LOAD_SALES``. "
            "Must match the pattern ``[A-Za-z0-9_.:-]+`` enforced on the Job model."
        ),
    )

    # ------------------------------------------------------------------
    # Run lifecycle state
    # ------------------------------------------------------------------

    status: JobStatus = Field(
        default=JobStatus.STARTING,
        description=(
            "Current runtime state of this execution attempt. "
            "Follows the AutoSys 13-state machine documented in enums.JobStatus. "
            "Initial value is STARTING (EPS has dispatched to System Agent but "
            "the agent has not yet sent back a 'job started' acknowledgement). "
            "Terminal values are SUCCESS, FAILURE, and TERMINATED — once any of "
            "these is set, the row is never updated again."
        ),
    )

    start_time: Optional[datetime] = Field(
        None,
        description=(
            "UTC timestamp at which the System Agent confirmed that the OS "
            "subprocess began executing (i.e. the moment ``fork()``/``exec()`` "
            "succeeded on the remote host). "
            "This is the value shown in AutoSys as 'Start Time' in ``autorep -j``. "
            "Remains None while the job is in STARTING or any pre-run state."
        ),
    )

    end_time: Optional[datetime] = Field(
        None,
        description=(
            "UTC timestamp at which the job reached any terminal state "
            "(SUCCESS, FAILURE, or TERMINATED). "
            "Set by the System Agent when the subprocess exits, or by the EPS "
            "when it processes a KILLJOB event. "
            "Remains None while the job is still running or has not started."
        ),
    )

    exit_code: Optional[int] = Field(
        None,
        description=(
            "The OS-level exit code returned by the subprocess on the System Agent. "
            "Semantics match standard POSIX conventions: "
            "  0   → clean exit → AutoSys maps to SUCCESS. "
            " >0   → error exit → AutoSys maps to FAILURE (unless retries remain). "
            " <0   → killed by signal (e.g. -15 for SIGTERM from KILLJOB). "
            " None → job has not yet finished, is still STARTING/RUNNING, "
            "         or was terminated in a way that no exit code was captured "
            "         (e.g. System Agent lost connectivity). "
            "In real AutoSys the exit code appears in the 'Exit Code' column of "
            "the job activity log and can be tested with ``exitcode()`` in a "
            "condition expression."
        ),
    )

    # ------------------------------------------------------------------
    # Execution environment
    # ------------------------------------------------------------------

    machine: Optional[str] = Field(
        None,
        description=(
            "Hostname of the System Agent that actually executed this job. "
            "Populated by the EPS at dispatch time from the job definition's "
            "``machine`` attribute (or by the load-balancer if machine='any'). "
            "Stored here for audit purposes so that even after the job definition "
            "is updated (new target machine), historical runs retain their "
            "original execution host. "
            "Corresponds to the 'Machine' column in ``autorep -M`` output."
        ),
    )

    retry_count: int = Field(
        default=0,
        ge=0,
        description=(
            "Which retry iteration this row represents. "
            "0 = the first (original) run attempt. "
            "1 = the first retry (job failed once, n_retrys >= 1). "
            "2 = the second retry, and so on. "
            "The maximum value is bounded by the job definition's ``n_retrys`` "
            "attribute. "
            "Each retry produces a *new* JobRun row with the same ``job_name`` "
            "and ``run_date`` but an incremented ``retry_count``, allowing the "
            "full retry chain to be reconstructed from the history table."
        ),
    )

    run_date: Optional[date] = Field(
        None,
        description=(
            "The logical calendar date (YYYY-MM-DD) this job execution belongs to. "
            "This is the *scheduled* date — i.e. the date the Scheduler decided "
            "to fire the job — not necessarily the wall-clock date on which it "
            "actually ran. "
            "Critical for jobs that span midnight: a job scheduled for 23:55 "
            "Monday has run_date=Monday even if it finishes at 00:15 Tuesday. "
            "Used to disambiguate multiple runs of the same job across different "
            "run cycles when querying job history (e.g. 'did JOB_X run on 2024-03-15?'). "
            "In real AutoSys this maps to the 'Run Date' column visible in the "
            "GUI Job Activity page and the ``autorep -j <name> -r <date>`` command."
        ),
    )

    pid: Optional[int] = Field(
        None,
        description=(
            "The OS process ID (PID) of the subprocess on the System Agent. "
            "Populated by the agent immediately after ``fork()``/``exec()``. "
            "The EPS uses this value when processing a KILLJOB event: it sends "
            "SIGTERM (or SIGKILL after a grace period) to this PID on the target "
            "machine via the agent's control channel. "
            "Also useful for ad-hoc debugging: an operator can SSH to ``machine`` "
            "and ``ps -p <pid>`` to inspect the running process. "
            "Remains None until the agent reports the PID (i.e. while STARTING) "
            "and after the process exits."
        ),
    )

    # ------------------------------------------------------------------
    # Output file paths  (resolved at runtime)
    # ------------------------------------------------------------------

    stdout_path: Optional[str] = Field(
        None,
        description=(
            "Fully-resolved filesystem path on the System Agent where stdout "
            "was captured for this specific run. "
            "This path may differ from ``job.std_out_file`` in the job definition "
            "because AutoSys expands runtime tokens before opening the file: "
            "  %%DATE%%     → replaced with YYYYMMDD of run_date "
            "  %%AUTORUN%%  → replaced with a run-sequence counter "
            "  %%JOBNAME%%  → replaced with job_name "
            "For example, a definition with std_out_file='/logs/%%JOBNAME%%.%%DATE%%.out' "
            "yields stdout_path='/logs/ETL_LOAD.20240315.out' for a run on 2024-03-15. "
            "Stored here so operators can locate the log even if the job definition "
            "is updated with a new path template after the run."
        ),
    )

    stderr_path: Optional[str] = Field(
        None,
        description=(
            "Fully-resolved filesystem path on the System Agent where stderr "
            "was captured for this specific run. "
            "Subject to the same %%TOKEN%% expansion as stdout_path (see above). "
            "In AutoSys, stdout and stderr are often written to the same file "
            "(std_err_file may equal std_out_file); this field stores whichever "
            "path the agent actually used regardless."
        ),
    )

    # ------------------------------------------------------------------
    # Audit metadata
    # ------------------------------------------------------------------

    created_at: datetime = Field(
        default_factory=datetime.utcnow,
        description=(
            "UTC timestamp at which this JobRun row was first written to the "
            "database by the EPS. "
            "This is the moment the EPS decided to dispatch the job, which is "
            "slightly earlier than ``start_time`` (which captures the moment the "
            "System Agent confirmed the subprocess started). "
            "The gap between created_at and start_time reflects network latency "
            "and agent startup overhead — useful for diagnosing slow agent response."
        ),
    )

    # ------------------------------------------------------------------
    # Computed properties
    # ------------------------------------------------------------------

    @property
    def duration_seconds(self) -> Optional[float]:
        """
        Wall-clock duration of this run in seconds, or ``None`` if the run
        has not yet completed (or never started).

        Computed as ``(end_time - start_time).total_seconds()``.

        Both ``start_time`` and ``end_time`` must be set for a result to be
        returned.  Partial availability (e.g. start_time set but end_time not
        yet written because the job is still RUNNING) returns ``None``.

        In real AutoSys this value is shown as 'Elapsed Time' in the job
        activity log and is used by the ``min_run_alarm`` / ``max_run_alarm``
        watchdog timers.

        Returns
        -------
        float
            Elapsed seconds (may be fractional).
        None
            If start_time or end_time is not yet available.
        """
        if self.start_time is not None and self.end_time is not None:
            return (self.end_time - self.start_time).total_seconds()
        return None

    @property
    def is_terminal(self) -> bool:
        """
        Return ``True`` if this run has reached a final, immutable state.

        The three terminal states in the AutoSys state machine are:

        * **SUCCESS**    — subprocess exited with code 0 and all post-processing
          (notifications, downstream dependency evaluation) has been triggered.
        * **FAILURE**    — subprocess exited with non-zero code *and* all allowed
          retries (``n_retrys``) have been exhausted.
        * **TERMINATED** — a KILLJOB event was processed and SIGTERM was delivered
          to the subprocess; the agent confirmed the process is gone.

        Once a JobRun is terminal, the EPS will never transition it again.
        A new ``JobRun`` row with ``retry_count`` incremented is created for
        subsequent retry attempts rather than reusing this row.

        ``RESTART`` is intentionally *not* terminal: it is a transient state
        meaning the Scheduler will soon create the next retry row.

        Returns
        -------
        bool
            ``True`` if status is SUCCESS, FAILURE, or TERMINATED.
            ``False`` for all other states (STARTING, RUNNING, RESTART, etc.).
        """
        terminal_states = {
            JobStatus.SUCCESS.value,
            JobStatus.FAILURE.value,
            JobStatus.TERMINATED.value,
        }
        return self.status in terminal_states
