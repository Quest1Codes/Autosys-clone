"""
AgentDispatch — the real System Agent dispatcher for Phase 5.

This module replaces ``_stub_dispatch`` in ``EventProcessor`` when the
``autosys agent start`` command is used.  Each job is launched as a real
OS subprocess via ``LocalJobRunner``.

How it integrates with the Event Processor
------------------------------------------
The ``EventProcessor`` has a ``dispatch_fn`` and a ``kill_fn`` parameter.
In Phase 4 (stub) these point to simple functions that immediately flip
the job to RUNNING/TERMINATED.  In Phase 5 we pass:

    agent = AgentDispatch()
    processor = EventProcessor(
        dispatch_fn = agent.dispatch,
        kill_fn     = agent.kill,
    )

``dispatch`` is called synchronously inside the EPS tick when a CMD job
reaches STARTING.  It:
  1. Expands %%VAR%% tokens in the command.
  2. Creates a ``job_runs`` record.
  3. Sets the job's DB status to RUNNING.
  4. Launches ``LocalJobRunner.run()`` in a background thread.

``kill`` is called by the KILLJOB handler and signals the running process.

Machine filtering
-----------------
Real AutoSys dispatches to the System Agent on ``job.machine`` over TCP.
In Phase 5 we only support local execution (localhost + the current
hostname).  Jobs targeting other machines are SKIPPED and left in
STARTING state with a warning.

Phase 6 will add SSH-based remote dispatch so that jobs targeting
``etl-server-01`` can actually run on that machine.
"""

from __future__ import annotations

import socket
import threading
import uuid
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo, runs as run_repo, output as output_repo
from autosys.db.schema import JobRow
from autosys.agent.runner import LocalJobRunner
from autosys.agent.runners import create_runner
from autosys.models.enums import JobStatus
from autosys.parser.variable_sub import substitute, UndefinedVariableError
from autosys.models.event import Event


def _now() -> datetime:
    """Return current time in UTC (matches schema column defaults)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _exit_code_to_status(exit_code: int, max_exit_success: Optional[int]) -> str:
    """
    Map a subprocess exit code to 'SUCCESS' or 'FAILURE'.

    If max_exit_success is set, any exit code <= max_exit_success is SUCCESS.
    Otherwise only exit code 0 is SUCCESS (AutoSys default).
    """
    threshold = max_exit_success if max_exit_success is not None else 0
    return "SUCCESS" if exit_code <= threshold else "FAILURE"


# ---------------------------------------------------------------------------
# Machine resolution helpers
# ---------------------------------------------------------------------------

_LOCAL_MACHINE_ALIASES = frozenset({"localhost", "127.0.0.1", "::1", ""})

def _is_local_machine(machine: Optional[str]) -> bool:
    """
    Return True if *machine* resolves to the current host.

    We consider a job local if its machine attribute is:
      - None / empty (JIL field omitted — defaults to local)
      - "localhost" or "127.0.0.1"
      - The current hostname (from ``socket.gethostname()``)
    """
    if not machine or machine.lower() in _LOCAL_MACHINE_ALIASES:
        return True
    return machine.lower() == socket.gethostname().lower()


# ---------------------------------------------------------------------------
# AgentDispatch
# ---------------------------------------------------------------------------

class AgentDispatch:
    """
    Unified dispatcher — routes to local execution or remote TCP agent.

    Phase 5: local_only=True (the default) — runs jobs on this machine only.
    Phase 6: local_only=False — looks up non-local machines in the machines
    table and sends dispatch requests to the registered agent server.

    Thread-safe: ``dispatch`` and ``kill`` may be called from the event
    processor tick while ``_run_job`` threads run concurrently.
    """

    def __init__(self, local_only: bool = True) -> None:
        self.local_only  = local_only
        # Maps job_name → (LocalJobRunner, run_id) for LOCAL active executions
        self._active: dict[str, tuple[LocalJobRunner, str]] = {}
        self._lock   = threading.Lock()

    # ------------------------------------------------------------------
    # dispatch — called by EventProcessor when job reaches STARTING
    # ------------------------------------------------------------------

    def dispatch(self, session: Session, row: JobRow) -> None:
        """
        Route the job to local or remote execution.

        Side-effects on *row*:
          - ``row.status``     ← "RUNNING"  (on successful dispatch)
          - ``row.last_start`` ← now
        """
        now     = _now()
        machine = row.machine or "localhost"

        if _is_local_machine(machine):
            self._dispatch_local(session, row, now)
        elif not self.local_only:
            # If the registered machine's host resolves to localhost, run locally.
            from autosys.db.repository import machines as machine_repo
            machine_row = machine_repo.get(session, machine)
            if machine_row and _is_local_machine(machine_row.host):
                logger.info(
                    "[agent] %r → %r registered as localhost-equivalent, dispatching locally",
                    row.job_name, machine,
                )
                self._dispatch_local(session, row, now)
            else:
                self._dispatch_remote(session, row, machine)
        else:
            logger.warning(
                "[agent] %r targets %r (not local) — "
                "use AgentDispatch(local_only=False) for remote dispatch.",
                row.job_name, machine,
            )
            row.status   = JobStatus.FAILURE.value
            row.last_end = now

    def _dispatch_local(self, session: Session, row: JobRow, now: datetime) -> None:
        """Fork the job locally (same machine as the scheduler)."""
        from autosys.db.repository import globs as glob_repo
        globals_dict = glob_repo.as_dict(session)
        command  = _expand_command(row, globals_dict, now)
        run_id   = str(uuid.uuid4())
        machine  = row.machine or "localhost"

        run_repo.start(
            session,
            run_id   = run_id,
            job_name = row.job_name,
            command  = command,
            machine  = machine,
            run_date = now.strftime("%Y-%m-%d"),
        )

        row.status     = JobStatus.RUNNING.value
        row.last_start = now

        runner = create_runner(
            row             = row,
            run_id          = run_id,
            command         = command,
            max_run_secs    = (row.max_run_alarm * 60) if row.max_run_alarm else None,
            output_callback = self._on_output_line,
        )
        with self._lock:
            self._active[row.job_name] = (runner, run_id)

        # Capture retry config before session closes
        n_retrys        = row.n_retrys or 0
        max_exit_success = row.max_exit_success

        t = threading.Thread(
            target = self._run_job,
            args   = (row.job_name, run_id, runner, n_retrys, max_exit_success),
            daemon = True,
            name   = f"agent-{row.job_name}",
        )
        t.start()
        logger.info(
            "[agent] local dispatch %r  run_id=%s  machine=%s",
            row.job_name, run_id[:8], machine,
        )

    def _dispatch_remote(self, session: Session, row: JobRow, machine: str) -> None:
        """Send a DISPATCH message to a registered remote agent server."""
        from autosys.db.repository import machines as machine_repo
        from autosys.agent.remote import RemoteDispatch

        machine_row = machine_repo.get(session, machine)
        if machine_row is None:
            logger.warning(
                "[agent] remote dispatch: machine %r not registered — "
                "job %r → FAILURE. Run 'autosys machine register %s' first.",
                machine, row.job_name, machine,
            )
            row.status   = JobStatus.FAILURE.value
            row.last_end = _now()
            return

        rd = RemoteDispatch()
        rd.dispatch(session, row, machine_row)

    # ------------------------------------------------------------------
    # kill — called by EventProcessor KILLJOB handler
    # ------------------------------------------------------------------

    def kill(self, session: Session, row: JobRow) -> None:
        """
        Signal the running subprocess to terminate (local or remote).

        For local jobs: send SIGTERM via the runner.
        For remote jobs: send a KILL message to the agent server.
        """
        machine = row.machine or "localhost"

        if _is_local_machine(machine):
            # Local kill
            with self._lock:
                entry = self._active.get(row.job_name)
            if entry is None:
                logger.warning("[agent] kill: no active local runner for %r", row.job_name)
                return
            runner, run_id = entry
            runner.kill()
            logger.info("[agent] kill: SIGTERM sent to %r (run_id=%s)", row.job_name, run_id[:8])
        elif not self.local_only:
            # Remote kill
            from autosys.db.repository import machines as machine_repo
            from autosys.agent.remote import RemoteDispatch
            machine_row = machine_repo.get(session, machine)
            if machine_row:
                RemoteDispatch().kill(session, row, machine_row)
            else:
                logger.warning("[agent] kill: machine %r not registered", machine)

    # ------------------------------------------------------------------
    # Background thread — blocks until subprocess finishes
    # ------------------------------------------------------------------

    def _run_job(
        self,
        job_name:         str,
        run_id:           str,
        runner:           LocalJobRunner,
        n_retrys:         int = 0,
        max_exit_success: Optional[int] = None,
    ) -> None:
        """
        Execute the job and update the DB when it finishes.

        Implements the n_retrys retry loop: if a job fails and retries remain,
        it transitions FAILURE → RESTART → re-runs the command.

        Runs entirely in a background thread so the EPS tick is not blocked.
        Opens its own DB session (separate from the dispatch session which has
        already been committed).
        """
        was_killed  = False
        retry_count = 0

        while True:
            try:
                exit_code = runner.run()
            except Exception as exc:
                logger.error("[agent] unhandled error in %r: %s", job_name, exc)
                exit_code = -1

            with self._lock:
                self._active.pop(job_name, None)

            now    = _now()
            status = _exit_code_to_status(exit_code, max_exit_success)

            # Check for KILLJOB (EPS may have set TERMINATED while we ran)
            with sync_session() as session:
                job_row = job_repo.get_row(session, job_name)
                if job_row and job_row.status == JobStatus.TERMINATED.value:
                    status     = "TERMINATED"
                    was_killed = True

            if status == "FAILURE" and not was_killed and retry_count < n_retrys:
                retry_count += 1
                logger.info(
                    "[agent] %r FAILURE — retry %d/%d",
                    job_name, retry_count, n_retrys,
                )
                with sync_session() as session:
                    job_row = job_repo.get_row(session, job_name)
                    if job_row:
                        job_row.status = JobStatus.RESTART.value
                    run_repo.finish(
                        session,
                        run_id    = run_id,
                        status    = "FAILURE",
                        exit_code = exit_code,
                        pid       = runner.pid,
                    )

                # Re-create a fresh runner for the next attempt
                import uuid
                run_id = str(uuid.uuid4())
                with sync_session() as session:
                    job_row = job_repo.get_row(session, job_name)
                    if job_row:
                        runner = create_runner(
                            row             = job_row,
                            run_id          = run_id,
                            command         = runner.command,
                            max_run_secs    = runner.max_run_secs,
                            output_callback = self._on_output_line,
                        )
                    else:
                        runner = LocalJobRunner(
                            command         = runner.command,
                            job_name        = job_name,
                            run_id          = run_id,
                            max_run_secs    = runner.max_run_secs,
                            output_callback = self._on_output_line,
                        )
                with self._lock:
                    self._active[job_name] = (runner, run_id)

                with sync_session() as session:
                    job_row = job_repo.get_row(session, job_name)
                    if job_row:
                        job_row.status     = JobStatus.RUNNING.value
                        job_row.last_start = _now()
                    run_repo.start(
                        session,
                        run_id   = run_id,
                        job_name = job_name,
                        command  = runner.command,
                        machine  = "localhost",
                        run_date = now.strftime("%Y-%m-%d"),
                    )
                continue  # back to top of while loop

            # Terminal: no more retries (or SUCCESS/TERMINATED)
            with sync_session() as session:
                job_row = job_repo.get_row(session, job_name)
                if job_row:
                    if job_row.status != JobStatus.TERMINATED.value:
                        job_row.status   = JobStatus[status].value
                        job_row.last_end = now
                    else:
                        status = "TERMINATED"

                run_repo.finish(
                    session,
                    run_id    = run_id,
                    status    = status,
                    exit_code = exit_code,
                    pid       = runner.pid,
                )
            break

        logger.info(
            "[agent] %r completed  status=%s  exit_code=%d  retries=%d%s",
            job_name, status, exit_code, retry_count,
            "  (killed)" if was_killed else "",
        )

    # ------------------------------------------------------------------
    # Output callback — called per stdout line from reader thread
    # ------------------------------------------------------------------

    def _on_output_line(
        self,
        run_id:  str,
        line_no: int,
        stream:  str,
        content: str,
    ) -> None:
        """
        Store one output line in the ``job_output`` table.

        Called synchronously from the output-reader thread.  Uses its own
        session per line to avoid holding a long-lived transaction.

        Phase 6 optimisation: batch writes (e.g. every 100 lines or 500 ms)
        to reduce per-line round-trip overhead.
        """
        # Retrieve job_name from the active runners dict
        job_name = None
        with self._lock:
            for jn, (runner, rid) in self._active.items():
                if rid == run_id:
                    job_name = jn
                    break

        with sync_session() as session:
            output_repo.append(
                session,
                run_id   = run_id,
                job_name = job_name or "unknown",
                line_no  = line_no,
                stream   = stream,
                content  = content,
            )


# ---------------------------------------------------------------------------
# %%VAR%% expansion helper
# ---------------------------------------------------------------------------

def _expand_command(
    row:          JobRow,
    globals_dict: dict[str, str],
    now:          datetime,
) -> str:
    """
    Expand %%VAR%% tokens in the job's command.

    Resolution order mirrors real AutoSys:
      1. Built-in vars: %%DATE%%, %%YYYY%%, %%MM%%, %%DD%%, %%TIME%%,
         %%AUTORUN%%
      2. User-defined globals (from ``global_variables`` table)

    Unknown variables are left unexpanded (``strict=False``) rather than
    raising an error — the job should still run; undefined vars produce a
    literal ``%%VAR%%`` in the command which will fail at the shell level
    with a clear error message.
    """
    command = row.command or ""
    if "%%" not in command:
        return command

    try:
        return substitute(
            template      = command,
            globals       = globals_dict,
            dispatch_time = now,
            autorun       = True,
            strict        = False,
        )
    except UndefinedVariableError as exc:
        logger.warning("[agent] variable expansion: %s", exc)
        return command
