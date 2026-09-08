"""
Specialized job runners for non-CMD job types.

All runners follow the same interface as ``LocalJobRunner``:
  - ``run() -> int``  (blocks until done, returns exit code; 0 = success)
  - ``kill()``        (signal early termination)
  - ``pid``           (process ID or None)
  - ``command``       (for logging / run record)

Runners implemented here:
  - FileWatchJobRunner   — polls for file existence + min size
  - FtpJobRunner         — FTP GET/PUT/DEL via ftplib
  - ConnectJobRunner     — TCP connect test via socket
  - RemoteCmdJobRunner   — SSH command via subprocess
  - WolJobRunner         — Wake-on-LAN magic packet via UDP
  - WebserviceJobRunner  — HTTP call via urllib
  - StubJobRunner        — logs warning, returns SUCCESS (enterprise types)

``create_runner()`` factory dispatches by ``JobType``.
"""

from __future__ import annotations

import os
import socket
import struct
import subprocess
import threading
import time
import urllib.request
import urllib.error
import ftplib
from typing import Optional, Callable

from loguru import logger

from autosys.agent.runner import LocalJobRunner, OutputCallback
from autosys.db.schema import JobRow


# ---------------------------------------------------------------------------
# 1. FileWatchJobRunner
# ---------------------------------------------------------------------------

class FileWatchJobRunner:
    """Polls for a file to appear and reach a minimum size."""

    def __init__(
        self,
        job_name: str,
        run_id: str,
        watch_file: str,
        watch_file_min_size: int = 0,
        watch_interval: int = 60,
        max_run_secs: Optional[float] = None,
        output_callback: Optional[OutputCallback] = None,
    ) -> None:
        self.command = f"FILEWATCH {watch_file}"
        self.job_name = job_name
        self.run_id = run_id
        self.watch_file = watch_file
        self.watch_file_min_size = watch_file_min_size
        self.watch_interval = watch_interval
        self.max_run_secs = max_run_secs
        self.output_callback = output_callback
        self.pid: Optional[int] = None
        self.exit_code: Optional[int] = None
        self._killed = threading.Event()

    def run(self) -> int:
        logger.info(
            "[agent] FILEWATCH %r  file=%r  min_size=%d  interval=%ds",
            self.job_name, self.watch_file, self.watch_file_min_size,
            self.watch_interval,
        )
        deadline = None
        if self.max_run_secs:
            deadline = time.monotonic() + self.max_run_secs

        while not self._killed.is_set():
            if os.path.exists(self.watch_file):
                size = os.path.getsize(self.watch_file)
                if size >= self.watch_file_min_size:
                    logger.info(
                        "[agent] FILEWATCH %r — file found (%d bytes)",
                        self.job_name, size,
                    )
                    self.exit_code = 0
                    return 0
                else:
                    logger.debug(
                        "[agent] FILEWATCH %r — file exists but %d < %d bytes",
                        self.job_name, size, self.watch_file_min_size,
                    )
            if deadline and time.monotonic() >= deadline:
                logger.warning(
                    "[agent] FILEWATCH %r — timed out after %ds",
                    self.job_name, int(self.max_run_secs),
                )
                self.exit_code = -1
                return -1
            self._killed.wait(self.watch_interval)

        self.exit_code = -1
        return -1

    def kill(self, wait_secs: float = 5.0) -> None:
        self._killed.set()


# ---------------------------------------------------------------------------
# 2. FtpJobRunner
# ---------------------------------------------------------------------------

class FtpJobRunner:
    """FTP GET/PUT/DEL via stdlib ftplib."""

    def __init__(
        self,
        job_name: str,
        run_id: str,
        ftp_server: str,
        ftp_user: str,
        ftp_type: str,
        ftp_src: str,
        ftp_dest: str,
        ftp_password: Optional[str] = None,
        max_run_secs: Optional[float] = None,
        output_callback: Optional[OutputCallback] = None,
    ) -> None:
        self.command = f"FTP {ftp_type} {ftp_src} → {ftp_dest}"
        self.job_name = job_name
        self.run_id = run_id
        self.ftp_server = ftp_server
        self.ftp_user = ftp_user
        self.ftp_type = ftp_type.upper()
        self.ftp_src = ftp_src
        self.ftp_dest = ftp_dest
        self.ftp_password = ftp_password or ""
        self.max_run_secs = max_run_secs
        self.output_callback = output_callback
        self.pid: Optional[int] = None
        self.exit_code: Optional[int] = None
        self._proc: Optional[subprocess.Popen] = None

    def run(self) -> int:
        logger.info(
            "[agent] FTP %r  type=%s  server=%s  src=%s  dest=%s",
            self.job_name, self.ftp_type, self.ftp_server,
            self.ftp_src, self.ftp_dest,
        )
        try:
            ftp = ftplib.FTP(self.ftp_server, timeout=30)
            ftp.login(self.ftp_user, self.ftp_password)

            if self.ftp_type == "GET":
                with open(self.ftp_dest, "wb") as f:
                    ftp.retrbinary(f"RETR {self.ftp_src}", f.write)
            elif self.ftp_type == "PUT":
                with open(self.ftp_src, "rb") as f:
                    ftp.storbinary(f"STOR {self.ftp_dest}", f)
            elif self.ftp_type == "DEL":
                ftp.delete(self.ftp_src)
            else:
                logger.error("[agent] FTP %r — unknown type %r", self.job_name, self.ftp_type)
                self.exit_code = 1
                return 1

            ftp.quit()
            logger.info("[agent] FTP %r — SUCCESS", self.job_name)
            self.exit_code = 0
            return 0

        except Exception as exc:
            logger.error("[agent] FTP %r — FAILURE: %s", self.job_name, exc)
            self.exit_code = 1
            return 1

    def kill(self, wait_secs: float = 5.0) -> None:
        pass


# ---------------------------------------------------------------------------
# 3. ConnectJobRunner
# ---------------------------------------------------------------------------

class ConnectJobRunner:
    """TCP connect test — returns 0 if connection succeeds."""

    def __init__(
        self,
        job_name: str,
        run_id: str,
        host: str,
        port: int = 7520,
        timeout: float = 10.0,
        max_run_secs: Optional[float] = None,
        output_callback: Optional[OutputCallback] = None,
    ) -> None:
        self.command = f"CONNECT {host}:{port}"
        self.job_name = job_name
        self.run_id = run_id
        self.host = host
        self.port = port
        self.timeout = timeout
        self.max_run_secs = max_run_secs
        self.output_callback = output_callback
        self.pid: Optional[int] = None
        self.exit_code: Optional[int] = None

    def run(self) -> int:
        logger.info(
            "[agent] CONNECT %r  host=%s  port=%d  timeout=%ss",
            self.job_name, self.host, self.port, self.timeout,
        )
        try:
            with socket.create_connection(
                (self.host, self.port), timeout=self.timeout,
            ) as sock:
                peer = sock.getpeername()
                logger.info(
                    "[agent] CONNECT %r — SUCCESS (connected to %s)",
                    self.job_name, f"{peer[0]}:{peer[1]}",
                )
            self.exit_code = 0
            return 0
        except (socket.timeout, ConnectionRefusedError, OSError) as exc:
            logger.warning(
                "[agent] CONNECT %r — FAILURE: %s", self.job_name, exc,
            )
            self.exit_code = 1
            return 1

    def kill(self, wait_secs: float = 5.0) -> None:
        pass


# ---------------------------------------------------------------------------
# 4. RemoteCmdJobRunner
# ---------------------------------------------------------------------------

class RemoteCmdJobRunner:
    """Runs a command on a remote machine via SSH."""

    def __init__(
        self,
        command: str,
        job_name: str,
        run_id: str,
        host: str,
        max_run_secs: Optional[float] = None,
        output_callback: Optional[OutputCallback] = None,
    ) -> None:
        self.command = command
        self.job_name = job_name
        self.run_id = run_id
        self.host = host
        self.max_run_secs = max_run_secs
        self.output_callback = output_callback
        self.pid: Optional[int] = None
        self.exit_code: Optional[int] = None
        self._proc: Optional[subprocess.Popen] = None
        self._lock = threading.Lock()

    def run(self) -> int:
        ssh_cmd = ["ssh", "-o", "ConnectTimeout=10", self.host, self.command]
        logger.info(
            "[agent] REMOTECMD %r  host=%s  cmd=%r",
            self.job_name, self.host, self.command[:80],
        )
        self._proc = subprocess.Popen(
            ssh_cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.pid = self._proc.pid

        line_no = 0
        assert self._proc.stdout is not None
        for raw_line in self._proc.stdout:
            content = raw_line.rstrip("\n")
            line_no += 1
            if self.output_callback:
                try:
                    self.output_callback(self.run_id, line_no, "stdout", content)
                except Exception:
                    pass

        self.exit_code = self._proc.wait()
        logger.info(
            "[agent] REMOTECMD %r — exit_code=%d", self.job_name, self.exit_code,
        )
        return self.exit_code

    def kill(self, wait_secs: float = 5.0) -> None:
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        proc.terminate()
        try:
            proc.wait(timeout=wait_secs)
        except subprocess.TimeoutExpired:
            proc.kill()


# ---------------------------------------------------------------------------
# 5. WolJobRunner
# ---------------------------------------------------------------------------

class WolJobRunner:
    """Sends a Wake-on-LAN magic packet via UDP broadcast."""

    def __init__(
        self,
        job_name: str,
        run_id: str,
        mac_address: str,
        broadcast_ip: str = "255.255.255.255",
        port: int = 9,
        max_run_secs: Optional[float] = None,
        output_callback: Optional[OutputCallback] = None,
    ) -> None:
        self.command = f"WOL {mac_address}"
        self.job_name = job_name
        self.run_id = run_id
        self.mac_address = mac_address
        self.broadcast_ip = broadcast_ip
        self.port = port
        self.max_run_secs = max_run_secs
        self.output_callback = output_callback
        self.pid: Optional[int] = None
        self.exit_code: Optional[int] = None

    @staticmethod
    def _build_magic_packet(mac: str) -> bytes:
        mac_clean = mac.replace(":", "").replace("-", "").replace(".", "")
        if len(mac_clean) != 12:
            raise ValueError(f"Invalid MAC address: {mac}")
        mac_bytes = bytes.fromhex(mac_clean)
        return b"\xff" * 6 + mac_bytes * 16

    def run(self) -> int:
        logger.info(
            "[agent] WOL %r  mac=%s  broadcast=%s:%d",
            self.job_name, self.mac_address, self.broadcast_ip, self.port,
        )
        try:
            packet = self._build_magic_packet(self.mac_address)
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.sendto(packet, (self.broadcast_ip, self.port))
            sock.close()
            logger.info("[agent] WOL %r — magic packet sent", self.job_name)
            self.exit_code = 0
            return 0
        except Exception as exc:
            logger.error("[agent] WOL %r — FAILURE: %s", self.job_name, exc)
            self.exit_code = 1
            return 1

    def kill(self, wait_secs: float = 5.0) -> None:
        pass


# ---------------------------------------------------------------------------
# 6. WebserviceJobRunner
# ---------------------------------------------------------------------------

class WebserviceJobRunner:
    """HTTP call via urllib — returns 0 on 2xx response."""

    def __init__(
        self,
        job_name: str,
        run_id: str,
        url: str,
        method: str = "GET",
        headers: Optional[dict] = None,
        body: Optional[bytes] = None,
        timeout: float = 30.0,
        max_run_secs: Optional[float] = None,
        output_callback: Optional[OutputCallback] = None,
    ) -> None:
        self.command = f"WEBSERVICE {method} {url}"
        self.job_name = job_name
        self.run_id = run_id
        self.url = url
        self.method = method.upper()
        self.headers = headers or {}
        self.body = body
        self.timeout = timeout
        self.max_run_secs = max_run_secs
        self.output_callback = output_callback
        self.pid: Optional[int] = None
        self.exit_code: Optional[int] = None

    def run(self) -> int:
        logger.info(
            "[agent] WEBSERVICE %r  %s %s", self.job_name, self.method, self.url,
        )
        try:
            req = urllib.request.Request(
                self.url, method=self.method, headers=self.headers,
                data=self.body,
            )
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                status_code = resp.getcode()
                logger.info(
                    "[agent] WEBSERVICE %r — HTTP %d", self.job_name, status_code,
                )
                if 200 <= status_code < 300:
                    self.exit_code = 0
                    return 0
                else:
                    self.exit_code = 1
                    return 1
        except urllib.error.HTTPError as exc:
            logger.warning(
                "[agent] WEBSERVICE %r — HTTP %d", self.job_name, exc.code,
            )
            self.exit_code = 1
            return 1
        except Exception as exc:
            logger.error(
                "[agent] WEBSERVICE %r — FAILURE: %s", self.job_name, exc,
            )
            self.exit_code = 1
            return 1

    def kill(self, wait_secs: float = 5.0) -> None:
        pass


# ---------------------------------------------------------------------------
# 7. StubJobRunner
# ---------------------------------------------------------------------------

class StubJobRunner:
    """Logs a warning and returns SUCCESS immediately. Used for enterprise types."""

    def __init__(
        self,
        job_type: str,
        job_name: str,
        run_id: str,
        command: str = "",
        max_run_secs: Optional[float] = None,
        output_callback: Optional[OutputCallback] = None,
    ) -> None:
        self.command = command or f"STUB({job_type})"
        self.job_name = job_name
        self.run_id = run_id
        self.job_type = job_type
        self.max_run_secs = max_run_secs
        self.output_callback = output_callback
        self.pid: Optional[int] = None
        self.exit_code: Optional[int] = None

    def run(self) -> int:
        logger.warning(
            "[agent] Job type %r for %r is a stub — no real execution",
            self.job_type, self.job_name,
        )
        self.exit_code = 0
        return 0

    def kill(self, wait_secs: float = 5.0) -> None:
        pass


# ---------------------------------------------------------------------------
# Runner factory
# ---------------------------------------------------------------------------

_STUB_TYPES = frozenset({"SAP", "PEOPLESOFT", "INFORMATICA", "MICROFOCUS"})


def create_runner(
    row: JobRow,
    run_id: str,
    command: str = "",
    max_run_secs: Optional[float] = None,
    output_callback: Optional[OutputCallback] = None,
) -> object:
    """
    Create the appropriate runner for *row* based on ``row.job_type``.

    Parameters
    ----------
    row:
        The ``JobRow`` from the DB (has job_type, command, machine, etc.).
    run_id:
        UUID for this execution.
    command:
        Pre-expanded command string (used by CMD, REMOTECMD, USERDEFINED).
    max_run_secs:
        Optional timeout in seconds.
    output_callback:
        Optional callback for stdout lines.

    Returns
    -------
    A runner object with ``run() -> int``, ``kill()``, ``pid``, ``command``.
    """
    jt = str(row.job_type).upper()
    job_name = row.job_name

    if jt == "CMD":
        return LocalJobRunner(
            command=command,
            job_name=job_name,
            run_id=run_id,
            max_run_secs=max_run_secs,
            output_callback=output_callback,
        )

    if jt == "FILEWATCH":
        return FileWatchJobRunner(
            job_name=job_name,
            run_id=run_id,
            watch_file=row.watch_file or "",
            watch_file_min_size=row.watch_file_min_size or 0,
            watch_interval=row.watch_interval or 60,
            max_run_secs=max_run_secs,
            output_callback=output_callback,
        )

    if jt == "FTP":
        password = os.environ.get(f"FTP_PASSWORD_{job_name}", "")
        return FtpJobRunner(
            job_name=job_name,
            run_id=run_id,
            ftp_server=row.ftp_server or "",
            ftp_user=row.ftp_user or "",
            ftp_type=str(row.ftp_type or "GET"),
            ftp_src=row.ftp_src or "",
            ftp_dest=row.ftp_dest or "",
            ftp_password=password,
            max_run_secs=max_run_secs,
            output_callback=output_callback,
        )

    if jt == "CONNECT":
        host = row.machine or "localhost"
        port = row.port if hasattr(row, "port") and row.port else 7520
        return ConnectJobRunner(
            job_name=job_name,
            run_id=run_id,
            host=host,
            port=port,
            max_run_secs=max_run_secs,
            output_callback=output_callback,
        )

    if jt == "REMOTECMD":
        host = row.machine or "localhost"
        return RemoteCmdJobRunner(
            command=command,
            job_name=job_name,
            run_id=run_id,
            host=host,
            max_run_secs=max_run_secs,
            output_callback=output_callback,
        )

    if jt == "WOL":
        mac = getattr(row, "mac_address", None) or row.command or ""
        return WolJobRunner(
            job_name=job_name,
            run_id=run_id,
            mac_address=mac,
            max_run_secs=max_run_secs,
            output_callback=output_callback,
        )

    if jt == "WEBSERVICE":
        url = row.command or ""
        method = "GET"
        return WebserviceJobRunner(
            job_name=job_name,
            run_id=run_id,
            url=url,
            method=method,
            max_run_secs=max_run_secs,
            output_callback=output_callback,
        )

    if jt in _STUB_TYPES:
        return StubJobRunner(
            job_type=jt,
            job_name=job_name,
            run_id=run_id,
            command=command,
            max_run_secs=max_run_secs,
            output_callback=output_callback,
        )

    if jt == "USERDEFINED":
        from autosys.db.connection import sync_session
        from autosys.db.schema import JobTypeRow
        from sqlalchemy import select
        type_name = row.command or ""
        with sync_session() as session:
            jt_row = session.scalars(
                select(JobTypeRow).where(JobTypeRow.type_name == type_name)
            ).first()
        if jt_row and jt_row.command_template:
            template = jt_row.command_template
            expanded = template
            for attr in ("job_name", "machine", "description", "owner"):
                val = getattr(row, attr, None)
                if val is not None:
                    expanded = expanded.replace(f"%%{attr.upper()}%%", str(val))
            return LocalJobRunner(
                command=expanded,
                job_name=job_name,
                run_id=run_id,
                max_run_secs=max_run_secs,
                output_callback=output_callback,
            )
        logger.warning(
            "[agent] USERDEFINED %r — type %r not found, falling back to command",
            job_name, type_name,
        )
        return LocalJobRunner(
            command=command,
            job_name=job_name,
            run_id=run_id,
            max_run_secs=max_run_secs,
            output_callback=output_callback,
        )

    # Fallback: treat as CMD
    logger.warning(
        "[agent] Unknown job_type %r for %r — treating as CMD",
        jt, job_name,
    )
    return LocalJobRunner(
        command=command,
        job_name=job_name,
        run_id=run_id,
        max_run_secs=max_run_secs,
        output_callback=output_callback,
    )
