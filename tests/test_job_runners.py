"""
Phase 3 — Job Type Execution Logic tests.

Tests for each specialized runner:
  - FileWatchJobRunner
  - FtpJobRunner
  - ConnectJobRunner
  - RemoteCmdJobRunner
  - WolJobRunner
  - WebserviceJobRunner
  - StubJobRunner
  - create_runner factory
  - USERDEFINED with JobTypeRow lookup
"""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from autosys.agent.runners import (
    FileWatchJobRunner,
    FtpJobRunner,
    ConnectJobRunner,
    RemoteCmdJobRunner,
    WolJobRunner,
    WebserviceJobRunner,
    StubJobRunner,
    create_runner,
)
from autosys.agent.runner import LocalJobRunner
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.schema import JobRow, JobTypeRow
from autosys.db.repository import jobs as job_repo
from sqlalchemy import select


# ===========================================================================
# Fixtures
# ===========================================================================

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{db_path}")
    reset_engines()
    with sync_session() as session:
        create_all_sync(session)
    yield
    reset_engines()


def _make_job_row(
    job_type: str = "CMD",
    job_name: str = "test_job",
    command: str = "echo hello",
    machine: str = "localhost",
    **kwargs,
) -> JobRow:
    """Create a JobRow in the DB and return it."""
    row = JobRow(
        job_name=job_name,
        job_type=job_type,
        command=command,
        machine=machine,
        status="INACTIVE",
        **kwargs,
    )
    with sync_session() as session:
        session.add(row)
        session.commit()
        session.refresh(row)
    return row


# ===========================================================================
# 1. FileWatchJobRunner
# ===========================================================================

class TestFileWatchJobRunner:

    def test_file_already_exists(self, tmp_path):
        target = tmp_path / "ready.txt"
        target.write_text("done")
        runner = FileWatchJobRunner(
            job_name="fw1", run_id="r1",
            watch_file=str(target),
            watch_file_min_size=0,
            watch_interval=1,
        )
        assert runner.run() == 0

    def test_file_appears_after_delay(self, tmp_path):
        target = tmp_path / "late.txt"
        runner = FileWatchJobRunner(
            job_name="fw2", run_id="r2",
            watch_file=str(target),
            watch_file_min_size=0,
            watch_interval=1,
        )

        def create_file():
            time.sleep(2)
            target.write_text("appeared")

        t = threading.Thread(target=create_file, daemon=True)
        t.start()
        assert runner.run() == 0

    def test_min_size_not_met(self, tmp_path):
        target = tmp_path / "small.txt"
        target.write_text("x")  # 1 byte
        runner = FileWatchJobRunner(
            job_name="fw3", run_id="r3",
            watch_file=str(target),
            watch_file_min_size=100,
            watch_interval=1,
            max_run_secs=3,
        )
        assert runner.run() == -1  # timed out

    def test_kill(self, tmp_path):
        target = tmp_path / "never.txt"
        runner = FileWatchJobRunner(
            job_name="fw4", run_id="r4",
            watch_file=str(target),
            watch_interval=5,
        )
        # Kill after 1 second
        t = threading.Timer(1.0, runner.kill)
        t.start()
        assert runner.run() == -1


# ===========================================================================
# 2. FtpJobRunner
# ===========================================================================

class TestFtpJobRunner:

    @patch("autosys.agent.runners.ftplib")
    def test_ftp_get_success(self, mock_ftplib):
        mock_ftp = MagicMock()
        mock_ftplib.FTP.return_value = mock_ftp

        runner = FtpJobRunner(
            job_name="ftp1", run_id="r1",
            ftp_server="ftp.example.com",
            ftp_user="user",
            ftp_type="GET",
            ftp_src="/remote/file.txt",
            ftp_dest="/local/file.txt",
        )
        with patch("builtins.open", mock_open := MagicMock()):
            assert runner.run() == 0
        mock_ftp.login.assert_called_once_with("user", "")
        mock_ftp.retrbinary.assert_called_once()

    @patch("autosys.agent.runners.ftplib")
    def test_ftp_put_success(self, mock_ftplib):
        mock_ftp = MagicMock()
        mock_ftplib.FTP.return_value = mock_ftp

        runner = FtpJobRunner(
            job_name="ftp2", run_id="r2",
            ftp_server="ftp.example.com",
            ftp_user="user",
            ftp_type="PUT",
            ftp_src="/local/file.txt",
            ftp_dest="/remote/file.txt",
        )
        with patch("builtins.open", mock_open := MagicMock()):
            assert runner.run() == 0
        mock_ftp.storbinary.assert_called_once()

    @patch("autosys.agent.runners.ftplib")
    def test_ftp_del_success(self, mock_ftplib):
        mock_ftp = MagicMock()
        mock_ftplib.FTP.return_value = mock_ftp

        runner = FtpJobRunner(
            job_name="ftp3", run_id="r3",
            ftp_server="ftp.example.com",
            ftp_user="user",
            ftp_type="DEL",
            ftp_src="/remote/file.txt",
            ftp_dest="",
        )
        assert runner.run() == 0
        mock_ftp.delete.assert_called_once_with("/remote/file.txt")

    @patch("autosys.agent.runners.ftplib")
    def test_ftp_connection_failure(self, mock_ftplib):
        mock_ftplib.FTP.side_effect = ConnectionRefusedError("Connection refused")
        runner = FtpJobRunner(
            job_name="ftp4", run_id="r4",
            ftp_server="bad.example.com",
            ftp_user="user",
            ftp_type="GET",
            ftp_src="/remote/file.txt",
            ftp_dest="/local/file.txt",
        )
        assert runner.run() == 1


# ===========================================================================
# 3. ConnectJobRunner
# ===========================================================================

class TestConnectJobRunner:

    def test_connect_success(self):
        # Start a local TCP server
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]

        runner = ConnectJobRunner(
            job_name="conn1", run_id="r1",
            host="127.0.0.1", port=port, timeout=5,
        )
        # Run in thread so we can accept
        result = []
        t = threading.Thread(target=lambda: result.append(runner.run()), daemon=True)
        t.start()
        conn, _ = srv.accept()
        conn.close()
        t.join(timeout=5)
        srv.close()
        assert result[0] == 0

    def test_connect_failure_refused(self):
        # Use a port that's almost certainly not listening
        runner = ConnectJobRunner(
            job_name="conn2", run_id="r2",
            host="127.0.0.1", port=1, timeout=2,
        )
        assert runner.run() == 1

    def test_connect_failure_timeout(self):
        # Use a non-routable address to trigger timeout
        runner = ConnectJobRunner(
            job_name="conn3", run_id="r3",
            host="192.0.2.1", port=9999, timeout=1,
        )
        assert runner.run() == 1


# ===========================================================================
# 4. RemoteCmdJobRunner
# ===========================================================================

class TestRemoteCmdJobRunner:

    def test_remotecmd_success(self):
        runner = RemoteCmdJobRunner(
            command="echo hello",
            job_name="rc1", run_id="r1",
            host="localhost",
        )
        # Mock subprocess.Popen to simulate ssh
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12345
            mock_proc.stdout = iter(["hello\n"])
            mock_proc.wait.return_value = 0
            mock_popen.return_value = mock_proc
            assert runner.run() == 0

    def test_remotecmd_failure(self):
        runner = RemoteCmdJobRunner(
            command="false",
            job_name="rc2", run_id="r2",
            host="localhost",
        )
        with patch("subprocess.Popen") as mock_popen:
            mock_proc = MagicMock()
            mock_proc.pid = 12346
            mock_proc.stdout = iter([])
            mock_proc.wait.return_value = 1
            mock_popen.return_value = mock_proc
            assert runner.run() == 1


# ===========================================================================
# 5. WolJobRunner
# ===========================================================================

class TestWolJobRunner:

    def test_wol_success(self):
        runner = WolJobRunner(
            job_name="wol1", run_id="r1",
            mac_address="AA:BB:CC:DD:EE:FF",
        )
        with patch("socket.socket") as mock_socket_cls:
            mock_sock = MagicMock()
            mock_socket_cls.return_value = mock_sock
            assert runner.run() == 0
            mock_sock.sendto.assert_called_once()
            mock_sock.close.assert_called_once()

    def test_wol_invalid_mac(self):
        runner = WolJobRunner(
            job_name="wol2", run_id="r2",
            mac_address="invalid",
        )
        assert runner.run() == 1

    def test_wol_magic_packet_format(self):
        packet = WolJobRunner._build_magic_packet("AA:BB:CC:DD:EE:FF")
        assert len(packet) == 102  # 6 + 16*6
        assert packet[:6] == b"\xff" * 6
        mac_bytes = bytes.fromhex("AABBCCDDEEFF")
        assert packet[6:12] == mac_bytes


# ===========================================================================
# 6. WebserviceJobRunner
# ===========================================================================

class TestWebserviceJobRunner:

    def test_webservice_get_success(self):
        runner = WebserviceJobRunner(
            job_name="ws1", run_id="r1",
            url="http://example.com/api",
            method="GET",
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.getcode.return_value = 200
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            assert runner.run() == 0

    def test_webservice_post_success(self):
        runner = WebserviceJobRunner(
            job_name="ws2", run_id="r2",
            url="http://example.com/api",
            method="POST",
            body=b'{"key": "value"}',
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_resp = MagicMock()
            mock_resp.getcode.return_value = 201
            mock_resp.__enter__ = MagicMock(return_value=mock_resp)
            mock_resp.__exit__ = MagicMock(return_value=False)
            mock_urlopen.return_value = mock_resp
            assert runner.run() == 0

    def test_webservice_404_failure(self):
        import urllib.error
        runner = WebserviceJobRunner(
            job_name="ws3", run_id="r3",
            url="http://example.com/notfound",
            method="GET",
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = urllib.error.HTTPError(
                url="http://example.com/notfound",
                code=404, msg="Not Found", hdrs=None, fp=None,
            )
            assert runner.run() == 1

    def test_webservice_connection_error(self):
        runner = WebserviceJobRunner(
            job_name="ws4", run_id="r4",
            url="http://nonexistent.invalid",
            method="GET",
            timeout=1,
        )
        with patch("urllib.request.urlopen") as mock_urlopen:
            mock_urlopen.side_effect = ConnectionError("DNS resolution failed")
            assert runner.run() == 1


# ===========================================================================
# 7. StubJobRunner
# ===========================================================================

class TestStubJobRunner:

    @pytest.mark.parametrize("job_type", ["SAP", "PEOPLESOFT", "INFORMATICA", "MICROFOCUS"])
    def test_stub_returns_success(self, job_type):
        runner = StubJobRunner(
            job_type=job_type,
            job_name=f"stub_{job_type}", run_id="r1",
        )
        assert runner.run() == 0
        assert runner.exit_code == 0


# ===========================================================================
# 8. create_runner factory
# ===========================================================================

class TestCreateRunner:

    def test_cmd_creates_local_runner(self):
        row = _make_job_row(job_type="CMD", command="echo hi")
        runner = create_runner(row=row, run_id="r1", command="echo hi")
        assert isinstance(runner, LocalJobRunner)

    def test_filewatch_creates_filewatch_runner(self):
        row = _make_job_row(
            job_type="FILEWATCH",
            watch_file="/tmp/test.txt",
            watch_file_min_size=0,
            watch_interval=60,
        )
        runner = create_runner(row=row, run_id="r1")
        assert isinstance(runner, FileWatchJobRunner)
        assert runner.watch_file == "/tmp/test.txt"

    def test_ftp_creates_ftp_runner(self):
        row = _make_job_row(
            job_type="FTP",
            ftp_server="ftp.example.com",
            ftp_user="test",
            ftp_type="GET",
            ftp_src="/remote/f",
            ftp_dest="/local/f",
        )
        runner = create_runner(row=row, run_id="r1")
        assert isinstance(runner, FtpJobRunner)
        assert runner.ftp_server == "ftp.example.com"

    def test_connect_creates_connect_runner(self):
        row = _make_job_row(job_type="CONNECT", machine="127.0.0.1")
        runner = create_runner(row=row, run_id="r1")
        assert isinstance(runner, ConnectJobRunner)
        assert runner.host == "127.0.0.1"

    def test_remotecmd_creates_remotecmd_runner(self):
        row = _make_job_row(job_type="REMOTECMD", command="echo remote", machine="localhost")
        runner = create_runner(row=row, run_id="r1", command="echo remote")
        assert isinstance(runner, RemoteCmdJobRunner)
        assert runner.host == "localhost"

    def test_wol_creates_wol_runner(self):
        row = _make_job_row(job_type="WOL", command="AA:BB:CC:DD:EE:FF")
        runner = create_runner(row=row, run_id="r1")
        assert isinstance(runner, WolJobRunner)
        assert runner.mac_address == "AA:BB:CC:DD:EE:FF"

    def test_webservice_creates_webservice_runner(self):
        row = _make_job_row(job_type="WEBSERVICE", command="http://example.com/api")
        runner = create_runner(row=row, run_id="r1")
        assert isinstance(runner, WebserviceJobRunner)
        assert runner.url == "http://example.com/api"

    @pytest.mark.parametrize("job_type", ["SAP", "PEOPLESOFT", "INFORMATICA", "MICROFOCUS"])
    def test_stub_types_create_stub_runner(self, job_type):
        row = _make_job_row(job_type=job_type, command="")
        runner = create_runner(row=row, run_id="r1")
        assert isinstance(runner, StubJobRunner)
        assert runner.job_type == job_type

    def test_userdefined_with_type_template(self):
        # Insert a JobTypeRow
        with sync_session() as session:
            session.add(JobTypeRow(
                type_name="MY_CUSTOM",
                command_template="echo %%JOB_NAME%% %%MACHINE%%",
            ))
            session.commit()

        row = _make_job_row(
            job_type="USERDEFINED",
            job_name="ud1",
            command="MY_CUSTOM",
            machine="localhost",
        )
        runner = create_runner(row=row, run_id="r1", command="MY_CUSTOM")
        assert isinstance(runner, LocalJobRunner)
        assert "echo" in runner.command
        assert "ud1" in runner.command

    def test_userdefined_fallback_when_type_not_found(self):
        row = _make_job_row(
            job_type="USERDEFINED",
            job_name="ud2",
            command="echo fallback",
        )
        runner = create_runner(row=row, run_id="r1", command="echo fallback")
        assert isinstance(runner, LocalJobRunner)
        assert runner.command == "echo fallback"

    def test_unknown_type_falls_back_to_local(self):
        row = _make_job_row(job_type="UNKNOWN", command="echo unknown")
        runner = create_runner(row=row, run_id="r1", command="echo unknown")
        assert isinstance(runner, LocalJobRunner)


# ===========================================================================
# 9. Integration — all runners have consistent interface
# ===========================================================================

class TestRunnerInterface:

    @pytest.mark.parametrize("job_type,kwargs", [
        ("CMD", {"command": "echo hi"}),
        ("FILEWATCH", {"watch_file": "/tmp/nonexist", "watch_interval": 1}),
        ("CONNECT", {"machine": "127.0.0.1"}),
        ("WOL", {"command": "AA:BB:CC:DD:EE:FF"}),
        ("WEBSERVICE", {"command": "http://example.com"}),
        ("SAP", {"command": ""}),
        ("PEOPLESOFT", {"command": ""}),
        ("INFORMATICA", {"command": ""}),
        ("MICROFOCUS", {"command": ""}),
    ])
    def test_runner_has_run_kill_pid_command(self, job_type, kwargs):
        row = _make_job_row(job_type=job_type, **kwargs)
        runner = create_runner(row=row, run_id="r1", command=kwargs.get("command", ""))
        assert hasattr(runner, "run")
        assert hasattr(runner, "kill")
        assert hasattr(runner, "pid")
        assert hasattr(runner, "command")
        assert callable(runner.run)
        assert callable(runner.kill)
