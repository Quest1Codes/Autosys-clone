"""
AutoSys System Agent TCP Server (Phase 6).

This is the daemon that runs on each worker machine and receives dispatch
requests from the Scheduler ACE.  In real AutoSys this is a C binary
called the "Remote Agent" (cybAgent) that listens on TCP port 7520.

Here we implement the same contract in Python using ``asyncio.start_server``.
Each incoming connection is handled concurrently; the actual subprocess is
launched in a background thread (via ``LocalJobRunner``) so the server is
never blocked waiting for a job to finish.

Startup sequence
----------------
1. ``AgentServer.serve_forever()`` binds the TCP socket.
2. The server registers itself in the ``machines`` table so the Scheduler
   knows the agent is alive and what address to use.
3. Each incoming connection is dispatched to ``_handle_client``.
4. ``_handle_client`` reads one request, routes to the handler, writes
   the response, and closes the connection.

Protocol
--------
Newline-delimited JSON.  See ``autosys/agent/protocol.py``.

DB access
---------
The agent server uses ``sync_session()`` to write run records and output
lines directly to the shared database.  In Phase 6 both the Scheduler
and the agent share the same ``AUTOSYS_DB_URL`` (e.g. a PostgreSQL server
accessible from both machines).  The SQLite URL in tests points to the
same file.

Stopping the server
-------------------
Call ``server.stop()`` from any thread.  The server drains in-flight
connections and exits.
"""

from __future__ import annotations

import asyncio
import threading
from datetime import datetime, timezone
from typing import Optional

from loguru import logger

from autosys.agent.protocol import (
    decode_message, encode_message, encode_dict,
    HeartbeatResponse, StatusResponse,
    DispatchResponse, DispatchRejected,
    KillResponse,
)
from autosys.agent.runner import LocalJobRunner
from autosys.db.connection import sync_session
from autosys.db.repository import (
    jobs as job_repo,
    runs as run_repo,
    output as output_repo,
)


def _now() -> datetime:
    """Return current UTC time (matches schema column defaults)."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _exit_code_to_status(exit_code: int, max_exit_success: Optional[int]) -> str:
    threshold = max_exit_success if max_exit_success is not None else 0
    return "SUCCESS" if exit_code <= threshold else "FAILURE"


class AgentServer:
    """
    System Agent TCP server.

    Parameters
    ----------
    machine_name:
        The logical name of this agent (e.g. ``"etl-server-01"``).
        Must match the ``machine:`` attribute of jobs that should run here.
    host:
        IP address to bind (``"0.0.0.0"`` for all interfaces).
    port:
        TCP port to listen on (default 7520 — same as real AutoSys).
    db_url:
        Override the DB URL (useful for tests).  Defaults to ``AUTOSYS_DB_URL``.
    """

    def __init__(
        self,
        machine_name: str,
        host:         str   = "0.0.0.0",
        port:         int   = 7520,
        db_url:       Optional[str] = None,
    ) -> None:
        self.machine_name = machine_name
        self.host         = host
        self.port         = port

        self._started_at:  datetime               = datetime.now()
        self._active:      dict[str, LocalJobRunner] = {}
        self._active_lock: threading.Lock          = threading.Lock()
        self._server:      Optional[asyncio.AbstractServer] = None
        self._stop_event:  Optional[asyncio.Event] = None
        self._loop:        Optional[asyncio.AbstractEventLoop] = None

    # ------------------------------------------------------------------
    # Public lifecycle
    # ------------------------------------------------------------------

    async def serve_forever(self) -> None:
        """
        Start the TCP server and block until ``stop()`` is called.

        This coroutine is the entry point for ``autosys agent serve``.
        """
        self._loop       = asyncio.get_running_loop()
        self._stop_event = asyncio.Event()

        self._server = await asyncio.start_server(
            self._handle_client,
            self.host,
            self.port,
        )

        addr = self._server.sockets[0].getsockname()
        logger.info(
            "[agent-server] %r listening on %s:%d",
            self.machine_name, addr[0], addr[1],
        )

        # Register in DB
        self._register_machine()

        async with self._server:
            await self._stop_event.wait()

        logger.info("[agent-server] %r stopped", self.machine_name)

    def stop(self) -> None:
        """
        Signal the server to stop (thread-safe).

        Called from the CLI interrupt handler or from tests.
        """
        if self._loop and self._stop_event and not self._stop_event.is_set():
            self._loop.call_soon_threadsafe(self._stop_event.set)

    # ------------------------------------------------------------------
    # Client handler
    # ------------------------------------------------------------------

    async def _handle_client(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """
        Handle one incoming connection: read request → route → write response.

        Each connection is a single request/response exchange.  We close the
        connection after the response so the server never holds open sockets.
        """
        peer = writer.get_extra_info("peername", ("?", "?"))
        try:
            line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not line:
                return

            msg = decode_message(line)
            logger.debug(
                "[agent-server] %r from %s:%s  type=%s",
                self.machine_name, peer[0], peer[1], msg.get("type"),
            )

            response = await self._route(msg)
            writer.write(response)
            await writer.drain()

        except asyncio.TimeoutError:
            logger.warning("[agent-server] client %s:%s timed out", *peer)
        except Exception as exc:
            logger.exception("[agent-server] error handling %s:%s", *peer)
            writer.write(encode_dict({"type": "error", "reason": str(exc)}))
            await writer.drain()
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def _route(self, msg: dict) -> bytes:
        """Dispatch *msg* to the correct handler and return wire bytes."""
        msg_type = msg.get("type")
        handler  = {
            "heartbeat":  self._handle_heartbeat,
            "status":     self._handle_status,
            "dispatch":   self._handle_dispatch,
            "kill":       self._handle_kill,
            "get_output": self._handle_get_output,
        }.get(msg_type)

        if handler is None:
            return encode_dict({"type": "error", "reason": f"unknown type: {msg_type!r}"})

        result = await handler(msg)
        return result

    # ------------------------------------------------------------------
    # Message handlers
    # ------------------------------------------------------------------

    async def _handle_heartbeat(self, msg: dict) -> bytes:
        """
        HEARTBEAT → ALIVE.

        The Scheduler sends CHECK_HEARTBEAT events periodically.  This is the
        exact response that tells the Scheduler the agent is alive.
        """
        logger.debug("[agent-server] heartbeat from Scheduler")
        # Update last_heartbeat in DB
        self._update_heartbeat("UP")
        return encode_message(HeartbeatResponse(machine_name=self.machine_name))

    async def _handle_status(self, msg: dict) -> bytes:
        """STATUS → STATUS RESPONSE with active job list and uptime."""
        uptime = (datetime.now() - self._started_at).total_seconds()
        with self._active_lock:
            active = list(self._active.keys())
        return encode_message(StatusResponse(
            machine_name = self.machine_name,
            host         = self.host,
            port         = self.port,
            active_jobs  = active,
            uptime_secs  = uptime,
        ))

    async def _handle_dispatch(self, msg: dict) -> bytes:
        """
        DISPATCH → ACCEPTED or REJECTED.

        This is the core handler — it forks the job and returns immediately.
        The actual execution happens in a background thread.

        Real AutoSys behaviour:
        - The agent forks the process and returns the PID in the ACK.
        - The Scheduler marks the job RUNNING when it gets the ACK.
        - The agent sends a JOB_COMPLETE callback when the process exits.

        Here we simplified: the agent writes directly to the shared DB
        instead of sending a callback, so no callback protocol is needed.
        """
        run_id           = msg.get("run_id", "")
        job_name         = msg.get("job_name", "")
        command          = msg.get("command", "")
        max_run_secs     = msg.get("max_run_secs")
        max_exit_success = msg.get("max_exit_success")

        if not run_id or not job_name or not command:
            return encode_message(DispatchRejected(
                run_id = run_id,
                reason = "missing run_id, job_name, or command",
            ))

        # Create run history record
        with sync_session() as sess:
            run_repo.start(
                sess,
                run_id   = run_id,
                job_name = job_name,
                command  = command,
                machine  = self.machine_name,
                run_date = _now().strftime("%Y-%m-%d"),
            )

        # Build runner
        runner = LocalJobRunner(
            command         = command,
            job_name        = job_name,
            run_id          = run_id,
            max_run_secs    = max_run_secs,
            output_callback = self._on_output,
        )

        with self._active_lock:
            self._active[job_name] = runner

        # Launch background thread
        t = threading.Thread(
            target = self._run_job_thread,
            args   = (job_name, run_id, runner, max_exit_success),
            daemon = True,
            name   = f"agent-{job_name}",
        )
        t.start()

        logger.info(
            "[agent-server] DISPATCH accepted  job=%r  run_id=%s",
            job_name, run_id[:8],
        )
        return encode_message(DispatchResponse(run_id=run_id))

    async def _handle_kill(self, msg: dict) -> bytes:
        """
        KILL → KILLED (found=True) or KILLED (found=False).

        Sends SIGTERM to the named job's running process.
        """
        job_name = msg.get("job_name", "")
        with self._active_lock:
            runner = self._active.get(job_name)

        if runner is None:
            logger.warning(
                "[agent-server] kill: no active runner for %r", job_name
            )
            return encode_message(KillResponse(job_name=job_name, found=False))

        runner.kill()
        logger.info("[agent-server] kill: SIGTERM sent to %r", job_name)
        return encode_message(KillResponse(job_name=job_name, found=True))

    async def _handle_get_output(self, msg: dict) -> bytes:
        """
        GET_OUTPUT → OUTPUT  with lines from the job_output table.

        The scheduler calls this when the client does ``autosys jobs tail``
        against a job that ran on this agent and the agent does not share the
        same SQLite file as the scheduler.

        In Phase 6 (shared-DB mode) this is not needed — the tail command
        reads directly from the shared ``job_output`` table.  This handler
        exists for Phase 7's multi-DB scenario.
        """
        run_id    = msg.get("run_id", "")
        from_line = msg.get("from_line", 0)

        with sync_session() as session:
            lines = output_repo.get_lines(session, run_id)

        return encode_dict({
            "type":   "output",
            "run_id": run_id,
            "lines":  [
                {"line_no": l.line_no, "stream": l.stream, "content": l.content}
                for l in lines
                if l.line_no > from_line
            ],
        })

    # ------------------------------------------------------------------
    # Background job thread
    # ------------------------------------------------------------------

    def _run_job_thread(
        self,
        job_name:         str,
        run_id:           str,
        runner:           LocalJobRunner,
        max_exit_success: Optional[int] = None,
    ) -> None:
        """
        Blocks until the subprocess exits, then updates the DB.

        Runs in a daemon thread so the server is never blocked.
        Uses its own DB session (the dispatch session is already committed).
        Applies max_exit_success threshold for SUCCESS/FAILURE determination.
        """
        try:
            exit_code = runner.run()
        except Exception as exc:
            logger.error("[agent-server] job %r raised: %s", job_name, exc)
            exit_code = -1
        finally:
            with self._active_lock:
                self._active.pop(job_name, None)

        threshold = max_exit_success if max_exit_success is not None else 0
        status = 4 if exit_code <= threshold else "FAILURE"
        now    = _now()

        with sync_session() as session:
            job_row = job_repo.get_row(session, job_name)
            if job_row:
                if job_row.status != "TERMINATED":
                    job_row.status   = status
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

        logger.info(
            "[agent-server] job %r finished  status=%s  exit_code=%d",
            job_name, status, exit_code,
        )

    # ------------------------------------------------------------------
    # Output callback  (called per line from output-reader thread)
    # ------------------------------------------------------------------

    def _on_output(
        self,
        run_id:  str,
        line_no: int,
        stream:  str,
        content: str,
    ) -> None:
        """Store one stdout/stderr line in the shared DB."""
        # Retrieve job_name from active runners
        job_name = None
        with self._active_lock:
            for jn, r in self._active.items():
                if r.run_id == run_id:
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

    # ------------------------------------------------------------------
    # Machine registry helpers
    # ------------------------------------------------------------------

    def _register_machine(self) -> None:
        """
        Insert or update this machine in the ``machines`` table.

        The Scheduler uses this record to look up the agent's address when
        dispatching jobs.  The ``status`` is set to "UP" on registration
        and updated by heartbeat responses.
        """
        from autosys.db.repository import machines as machine_repo
        with sync_session() as session:
            machine_repo.register(
                session,
                machine_name = self.machine_name,
                host         = self.host,
                port         = self.port,
                status       = "UP",
            )
        logger.info(
            "[agent-server] registered machine %r → %s:%d",
            self.machine_name, self.host, self.port,
        )

    def _update_heartbeat(self, status: str = "UP") -> None:
        """Update the machine's last_heartbeat timestamp."""
        from autosys.db.repository import machines as machine_repo
        with sync_session() as session:
            machine_repo.update_heartbeat(session, self.machine_name, status)
