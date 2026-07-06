"""
RemoteDispatch — schedules jobs on remote System Agent servers.

This module is the scheduler-side counterpart of ``server.py``.  When the
``AgentDispatch`` router detects that a job's ``machine`` attribute names a
registered (non-local) agent, it delegates here.

``RemoteDispatch`` opens a short-lived TCP connection to the agent, sends a
``DispatchRequest``, and returns.  The agent runs the job in its own thread,
writes output and status directly to the shared DB, and the scheduler picks
up the status change on its next tick — exactly the same way a local job
works, just with a TCP hop in the middle.

Why synchronous sockets?
------------------------
The event processor's ``process_one_tick`` is a regular synchronous function.
Using ``asyncio.run()`` inside it would fail if it is already inside an event
loop (the ``run_forever`` daemon uses asyncio).  Instead we use Python's
built-in blocking ``socket`` module:

    - No extra dependencies
    - Works in any thread context
    - The agent server handles concurrency on its end

For Phase 7 (high-throughput, hundreds of jobs/second) you would switch to
an async connection pool here — but correctness comes before performance.

Machine lookup
--------------
``RemoteDispatch`` reads the ``machines`` table to find ``host:port`` for the
target machine name.  If the machine is not registered, the dispatch is
skipped and the job stays in STARTING.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy.orm import Session

from autosys.agent.protocol import (
    send_message,
    DispatchRequest, KillRequest, HeartbeatRequest, StatusRequest,
)
from autosys.db.repository import runs as run_repo
from autosys.db.schema import JobRow, MachineRow
from autosys.parser.variable_sub import substitute, UndefinedVariableError


def _now() -> datetime:
    """Return current time in UTC."""
    from datetime import timezone
    return datetime.now(timezone.utc).replace(tzinfo=None)


# ===========================================================================
# RemoteDispatch
# ===========================================================================

class RemoteDispatch:
    """
    Dispatches a job to a registered remote System Agent.

    Parameters
    ----------
    timeout:
        Socket timeout in seconds for each message.  Real AutoSys uses a
        configurable timeout (``AGENT_CONNECT_TIMEOUT``); we default to 10s.
    """

    def __init__(self, timeout: float = 10.0) -> None:
        self.timeout = timeout

    # ------------------------------------------------------------------
    # dispatch — called by AgentDispatch._dispatch_remote
    # ------------------------------------------------------------------

    def dispatch(
        self,
        session:  Session,
        row:      JobRow,
        machine_row: MachineRow,
    ) -> bool:
        """
        Send a DISPATCH request to the remote agent.

        Parameters
        ----------
        session:
            Open session (from the EPS tick).  Used to look up globals and
            create the run history record.
        row:
            The job to dispatch (already in STARTING state).
        machine_row:
            The registered agent for ``row.machine``.

        Returns
        -------
        bool
            True if the agent accepted the dispatch, False otherwise.
        """
        now    = _now()
        run_id = str(uuid.uuid4())

        # %%VAR%% expansion happens on the Scheduler side
        from autosys.db.repository import globs as glob_repo
        globals_dict = glob_repo.as_dict(session)
        command = _expand_command(row, globals_dict, now)

        # Transition job to RUNNING
        row.status     = "RUNNING"
        row.last_start = now

        # Send DISPATCH to the remote agent
        req  = DispatchRequest(
            run_id           = run_id,
            job_name         = row.job_name,
            command          = command,
            max_run_secs     = (row.max_run_alarm * 60) if row.max_run_alarm else None,
            max_exit_success = row.max_exit_success,
        )
        resp = send_message(machine_row.host, machine_row.port, req, timeout=self.timeout)

        if resp.get("type") == "accepted":
            logger.info(
                "[remote] dispatched %r → %s:%d  run_id=%s",
                row.job_name, machine_row.host, machine_row.port, run_id[:8],
            )
            return True
        else:
            logger.error(
                "[remote] dispatch REJECTED for %r: %s",
                row.job_name, resp,
            )
            # Roll back the optimistic RUNNING status
            row.status = "FAILURE"
            run_repo.finish(session, run_id, "FAILURE", -1)
            return False

    # ------------------------------------------------------------------
    # kill
    # ------------------------------------------------------------------

    def kill(
        self,
        session:     Session,
        row:         JobRow,
        machine_row: MachineRow,
    ) -> bool:
        """
        Send a KILL request to the remote agent.

        Returns True if the agent found and signalled the process.
        """
        req  = KillRequest(job_name=row.job_name)
        resp = send_message(machine_row.host, machine_row.port, req, timeout=self.timeout)

        found = resp.get("found", False)
        if found:
            logger.info(
                "[remote] kill sent for %r on %s:%d",
                row.job_name, machine_row.host, machine_row.port,
            )
        else:
            logger.warning(
                "[remote] kill: agent %s:%d could not find %r",
                machine_row.host, machine_row.port, row.job_name,
            )
        return found

    # ------------------------------------------------------------------
    # heartbeat
    # ------------------------------------------------------------------

    def heartbeat(self, machine_row: MachineRow) -> bool:
        """
        Ping the agent and return True if it responds ``"alive"``.

        Called by the CHECK_HEARTBEAT event handler.
        """
        resp = send_message(
            machine_row.host, machine_row.port,
            HeartbeatRequest(),
            timeout=self.timeout,
        )
        alive = resp.get("type") == "alive"
        logger.debug(
            "[remote] heartbeat %s:%d → %s",
            machine_row.host, machine_row.port,
            "ALIVE" if alive else f"DEAD ({resp})",
        )
        return alive

    # ------------------------------------------------------------------
    # status
    # ------------------------------------------------------------------

    def status(self, machine_row: MachineRow) -> dict:
        """Return the agent's status dict (active jobs, uptime, etc.)."""
        return send_message(
            machine_row.host, machine_row.port,
            StatusRequest(),
            timeout=self.timeout,
        )


# ===========================================================================
# %%VAR%% expansion helper (same as dispatch.py's version)
# ===========================================================================

def _expand_command(
    row:          JobRow,
    globals_dict: dict[str, str],
    now:          datetime,
) -> str:
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
        logger.warning("[remote] variable expansion: %s", exc)
        return command
