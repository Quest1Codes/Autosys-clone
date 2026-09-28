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

import os
import re
import subprocess
import threading
from datetime import datetime
from typing import Callable, Optional

from loguru import logger

try:
    import resource  # POSIX-only (core of ulimit support)
except ImportError:  # pragma: no cover - Windows
    resource = None  # type: ignore[assignment]


# Type alias for the output callback
OutputCallback = Callable[[str, int, str, str], None]
# signature: (run_id, line_no, stream, content) → None


# ---------------------------------------------------------------------------
# envvars — "parm_name=parm_value[, parm_name=parm_value...]"
# ---------------------------------------------------------------------------

def parse_envvars(spec: str) -> dict[str, str]:
    """
    Parse an AutoSys ``envvars`` attribute string into a ``{name: value}`` dict.

    Format: ``envvars: parm_name=parm_value[, parm_name=parm_value...]``.
    Values may be single- or double-quoted (the surrounding quotes are
    stripped) if they contain spaces or commas.  This is a simple
    top-level-comma split, not a full shell-quote parser.
    """
    result: dict[str, str] = {}
    if not spec:
        return result
    for part in spec.split(","):
        part = part.strip()
        if not part or "=" not in part:
            continue
        name, _, value = part.partition("=")
        name  = name.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            value = value[1:-1]
        if name:
            result[name] = value
    return result


# ---------------------------------------------------------------------------
# ulimit — 'resource_type="soft_value,hard_value"[, resource_type="..."...]'
# ---------------------------------------------------------------------------

# AutoSys single-letter resource codes → (resource.RLIMIT_* attr name, unit).
#
# Unit-conversion note: AutoSys documents c/f as "blocks" and d/m/s as KB,
# while n/t are plain counts/seconds.  ``resource.setrlimit`` wants bytes for
# the size-based limits.  We treat both "blocks" and "KB" as 1024-byte units
# for simplicity — a documented best-effort approximation, since the exact
# block size is platform/filesystem-dependent and AutoSys's own docs don't
# pin it down further.
_ULIMIT_RESOURCE_MAP: dict[str, tuple[str, int]] = {
    "c": ("RLIMIT_CORE",   1024),  # core file size (blocks) -> bytes
    "d": ("RLIMIT_DATA",   1024),  # data segment (KB) -> bytes
    "f": ("RLIMIT_FSIZE",  1024),  # max file size (blocks) -> bytes
    "m": ("RLIMIT_AS",     1024),  # process virtual size (KB) -> bytes
    "n": ("RLIMIT_NOFILE", 1),     # number of open files -> count
    "s": ("RLIMIT_STACK",  1024),  # stack size (KB) -> bytes
    "t": ("RLIMIT_CPU",    1),     # CPU time (seconds) -> seconds
}

_ULIMIT_PAIR_RE = re.compile(r'([A-Za-z])\s*=\s*"([^"]*)"')


def parse_ulimit(spec: str) -> dict[str, tuple[str, str]]:
    """
    Parse an AutoSys ``ulimit`` attribute string.

    Format: ``resource_type="soft_value,hard_value"[, resource_type="..."]``,
    e.g. ``n="256,512", t="60,120"``.  Values are a decimal number or the
    literal ``unlimited``.

    Returns ``{resource_code: (soft_raw, hard_raw)}`` — raw string values,
    unit conversion happens in ``build_ulimit_preexec_fn``.  Matching on
    quoted ``code="..."`` pairs means commas *inside* the quotes (separating
    soft/hard) don't get confused with the pair-separator comma.
    """
    limits: dict[str, tuple[str, str]] = {}
    if not spec:
        return limits
    for code, values in _ULIMIT_PAIR_RE.findall(spec):
        parts = [v.strip() for v in values.split(",")]
        if not parts or not parts[0]:
            continue
        soft = parts[0]
        hard = parts[1] if len(parts) > 1 else parts[0]
        limits[code.lower()] = (soft, hard)
    return limits


def _ulimit_raw_to_value(raw: str, unit: int) -> int:
    if raw.strip().lower() == "unlimited":
        return resource.RLIM_INFINITY  # type: ignore[union-attr]
    return int(float(raw.strip())) * unit


def build_ulimit_preexec_fn(spec: str, job_name: str = "") -> Optional[Callable[[], None]]:
    """
    Build a ``preexec_fn`` for ``subprocess.Popen`` that applies the given
    AutoSys ``ulimit`` spec via the POSIX ``resource`` module.

    Returns ``None`` on non-POSIX platforms (e.g. Windows, where the
    ``resource`` module doesn't exist and ``Popen`` doesn't accept
    ``preexec_fn`` at all) or when *spec* has no recognised resource codes.
    """
    if resource is None or not spec:
        return None
    limits = parse_ulimit(spec)
    if not limits:
        return None

    def _preexec() -> None:
        # Runs in the forked child, before exec — failures here must not
        # raise past this function or the child process will die oddly.
        for code, (soft_raw, hard_raw) in limits.items():
            mapping = _ULIMIT_RESOURCE_MAP.get(code)
            if mapping is None:
                continue
            attr_name, unit = mapping
            rlimit_const = getattr(resource, attr_name, None)
            if rlimit_const is None:
                continue
            try:
                soft = _ulimit_raw_to_value(soft_raw, unit)
                hard = _ulimit_raw_to_value(hard_raw, unit)
                resource.setrlimit(rlimit_const, (soft, hard))
            except (ValueError, OSError) as exc:
                logger.warning(
                    f"[agent] {job_name!r}: failed to set ulimit {code}="
                    f"{soft_raw!r},{hard_raw!r}: {exc}"
                )

    return _preexec


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
    env:
        Optional dict of extra/overriding environment variables (parsed from
        the JIL ``envvars`` attribute).  Merged on top of ``os.environ`` —
        the subprocess still inherits the parent's environment.
    std_in_file:
        Optional path (from the JIL ``std_in_file`` attribute) whose contents
        are redirected to the subprocess's stdin.  If the file can't be
        opened, a warning is logged and the job still runs with no stdin
        redirection.
    ulimit:
        Optional raw ``ulimit`` attribute spec, e.g. ``n="256,512"``. Applied
        via ``resource.setrlimit`` in the child process (POSIX only; no-op
        elsewhere).

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
        envvars:         Optional[str] = None,
        std_in_file:     Optional[str] = None,
        ulimit:          Optional[str] = None,
    ) -> None:
        self.command         = command
        self.job_name        = job_name
        self.run_id          = run_id
        self.max_run_secs    = max_run_secs
        self.output_callback = output_callback
        self.envvars         = envvars
        self.std_in_file     = std_in_file
        self.ulimit          = ulimit

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
        logger.info(f"[agent] Running {self.job_name!r}  cmd={self.command[:80]!r}")

        # envvars: inherit the parent environment, then overlay the job's
        # own env var assignments on top.
        env = None
        if self.envvars:
            env = os.environ.copy()
            env.update(parse_envvars(self.envvars))

        # std_in_file: redirect stdin from the given file. If it can't be
        # opened, log a warning and fall back to no stdin redirection rather
        # than failing the dispatch outright.
        stdin_fh = None
        if self.std_in_file:
            try:
                stdin_fh = open(self.std_in_file, "r")
            except OSError as exc:
                logger.warning(
                    f"[agent] {self.job_name!r}: std_in_file {self.std_in_file!r} "
                    f"could not be opened ({exc}) — running with no stdin redirection"
                )
                stdin_fh = None

        # ulimit: applied inside the forked child via preexec_fn (POSIX only).
        preexec_fn = build_ulimit_preexec_fn(self.ulimit, self.job_name) if self.ulimit else None

        # Merge stderr into stdout so we get a single chronological stream.
        # This matches AutoSys default behaviour (stdout_file captures both).
        self._proc = subprocess.Popen(
            self.command,
            shell      = True,
            stdout     = subprocess.PIPE,
            stderr     = subprocess.STDOUT,
            stdin      = stdin_fh,
            env        = env,
            preexec_fn = preexec_fn,
            text       = True,
        )
        self.pid = self._proc.pid
        logger.debug(f"[agent] {self.job_name!r} started  pid={self.pid}")

        # The child now has its own duplicated fd for stdin; close our copy.
        if stdin_fh is not None:
            stdin_fh.close()

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
            logger.warning(f"[agent] {self.job_name!r} exceeded max_run_alarm ({self.max_run_secs}s) — killing")
            self.kill()
            self.exit_code = -1

        # Drain remaining output
        if self._reader_thread.is_alive():
            self._reader_thread.join(timeout=5)

        logger.info(f"[agent] {self.job_name!r} finished  exit_code={self.exit_code}  pid={self.pid}")
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

        logger.info(f"[agent] kill: sending SIGTERM to {self.job_name!r} (pid={proc.pid})")
        proc.terminate()
        try:
            proc.wait(timeout=wait_secs)
        except subprocess.TimeoutExpired:
            logger.warning(f"[agent] kill: SIGTERM timed out for {self.job_name!r} — sending SIGKILL")
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

            logger.debug(f"[{self.job_name}] {content}")
            if self.output_callback:
                try:
                    self.output_callback(self.run_id, line_no, "stdout", content)
                except Exception as exc:
                    logger.warning(f"[agent] output_callback error for {self.job_name!r} line {line_no}: {exc}")
