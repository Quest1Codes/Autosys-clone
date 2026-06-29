"""
LocalJobRunner — executes a single job's command as an OS subprocess.

This is the core of the System Agent.  In real AutoSys the System Agent is a
long-running C daemon (the "Remote Agent" process) on each worker machine that
receives dispatch requests over TCP and launches the job process.  Here we
implement the same contract as a Python class for local execution.

Lifecycle
---------
1. The ``AgentDispatch`` instantiates a ``LocalJobRunner`` per job.
2. ``runner.run()`` is called in a **background thread** — it blocks until
   the subprocess exits and returns the exit code.
3. The output-reader thread reads stdout (merged with stderr) line-by-line
   and calls ``output_callback`` for each line.
4. ``runner.kill()`` can be called from any thread to SIGTERM → SIGKILL the
   running process (used by the KILLJOB event handler).

%%VAR%% expansion
-----------------
The command string is expanded via ``autosys.parser.variable_sub.substitute``
*before* being handed to the shell.  This includes ``%%DATE%%``, ``%%TIME%%``,
user-defined globals, and the ``%%AUTORUN%%`` flag.

Platform notes
--------------
We use ``shell=True`` so the command string is interpreted by ``/bin/sh``,
matching AutoSys's behaviour (it also shells out via the local shell).  This
means commands like ``"echo hello && ls /tmp"`` work as expected.
"""

from __future__ import annotations

import subprocess
import threading
from datetime import datetime
from typing import Callable, Optional

from loguru import logger


# Type alias for the output callback
OutputCallback = Callable[[str, int, str, str], None]
# signature: (run_id, line_no, stream, content) → None


class LocalJobRunner:
    """
    Runs one job command locally via ``subprocess.Popen``.

    Parameters
    ----------
    command:
        The shell command to execute (already %%VAR%%-expanded).
    job_name:
        Used for log messages.
    run_id:
        UUID of the ``job_runs`` record for this execution.
    max_run_secs:
        If set, the process is killed after this many seconds
        (derived from ``max_run_alarm`` minutes × 60).
    output_callback:
        Called once per output line with ``(run_id, line_no, stream, content)``.
        Runs inside the output-reader thread.

    Attributes set after ``run()``
    --------------------------------
    pid:
        OS process ID.  ``None`` before the process starts.
    exit_code:
        The process exit code (0 = success, non-zero = failure, −1 if killed).
        ``None`` before the process finishes.
    """

    def __init__(
        self,
        command:         str,
        job_name:        str,
        run_id:          str,
        max_run_secs:    Optional[float] = None,
        output_callback: Optional[OutputCallback] = None,
    ) -> None:
        self.command         = command
        self.job_name        = job_name
        self.run_id          = run_id
        self.max_run_secs    = max_run_secs
        self.output_callback = output_callback

        self.pid:       Optional[int] = None
        self.exit_code: Optional[int] = None

        self._proc:          Optional[subprocess.Popen] = None
        self._reader_thread: Optional[threading.Thread]  = None
        self._line_no        = 0
        self._lock           = threading.Lock()

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self) -> int:
        """
        Start the subprocess and block until it exits.

        Returns
        -------
        int
            The process exit code.  Returns ``-1`` if the process was killed
            by the watchdog (``max_run_alarm`` exceeded) or by ``kill()``.

        Notes
        -----
        This method is designed to be called from a background thread so it
        can block without stalling the event processor loop.
        """
        logger.info(
            "[agent] Running %r  cmd=%r",
            self.job_name, self.command[:80],
        )

        # Merge stderr into stdout so we get a single chronological stream.
        # This matches AutoSys default behaviour (stdout_file captures both).
        self._proc = subprocess.Popen(
            self.command,
            shell     = True,
            stdout    = subprocess.PIPE,
            stderr    = subprocess.STDOUT,
            text      = True,
        )
        self.pid = self._proc.pid
        logger.debug("[agent] %r started  pid=%d", self.job_name, self.pid)

        # Start output reader
        self._reader_thread = threading.Thread(
            target  = self._read_output,
            daemon  = True,
            name    = f"reader-{self.job_name}",
        )
        self._reader_thread.start()

        # Wait for completion (with optional watchdog timeout)
        try:
            self.exit_code = self._proc.wait(timeout=self.max_run_secs)
        except subprocess.TimeoutExpired:
            logger.warning(
                "[agent] %r exceeded max_run_alarm (%ds) — killing",
                self.job_name, self.max_run_secs,
            )
            self.kill()
            self.exit_code = -1

        # Drain remaining output
        if self._reader_thread.is_alive():
            self._reader_thread.join(timeout=5)

        logger.info(
            "[agent] %r finished  exit_code=%d  pid=%d",
            self.job_name, self.exit_code, self.pid,
        )
        return self.exit_code

    def kill(self, wait_secs: float = 5.0) -> None:
        """
        Send SIGTERM to the running process, then SIGKILL if it doesn't exit.

        Safe to call from any thread.  No-op if the process has already
        exited.
        """
        with self._lock:
            proc = self._proc

        if proc is None or proc.poll() is not None:
            return   # already finished

        logger.info(
            "[agent] kill: sending SIGTERM to %r (pid=%d)",
            self.job_name, proc.pid,
        )
        proc.terminate()
        try:
            proc.wait(timeout=wait_secs)
        except subprocess.TimeoutExpired:
            logger.warning(
                "[agent] kill: SIGTERM timed out for %r — sending SIGKILL",
                self.job_name,
            )
            proc.kill()

    # ------------------------------------------------------------------
    # Output capture (runs in background thread)
    # ------------------------------------------------------------------

    def _read_output(self) -> None:
        """
        Read lines from the merged stdout/stderr pipe and invoke the callback.

        Each line is stripped of its trailing newline.  Empty lines are
        preserved (they might be intentional padding in job output).

        The callback is invoked synchronously in this thread.  If the
        callback is slow (e.g. DB write), the output buffer can fill up
        and block the subprocess — use a separate thread for DB writes in
        production (Phase 6).
        """
        assert self._proc is not None
        assert self._proc.stdout is not None

        for raw_line in self._proc.stdout:
            content = raw_line.rstrip("\n")
            with self._lock:
                self._line_no += 1
                line_no = self._line_no

            logger.debug("[%s] %s", self.job_name, content)
            if self.output_callback:
                try:
                    self.output_callback(self.run_id, line_no, "stdout", content)
                except Exception as exc:
                    logger.warning(
                        "[agent] output_callback error for %r line %d: %s",
                        self.job_name, line_no, exc,
                    )
