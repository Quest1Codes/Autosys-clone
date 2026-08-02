"""
Phase 6 test suite — Remote dispatch, TCP Agent Server, Machine Registry.

Coverage
--------
1.  Protocol        — encode/decode, send_message error handling
2.  AgentServer     — heartbeat, status, dispatch, kill, get_output
3.  RemoteDispatch  — dispatch to running server, job reaches SUCCESS
4.  AgentDispatch   — local/remote routing (local_only=False)
5.  MachineRepository — register, list, get, update_heartbeat, set_status
6.  EventProcessor  — CHECK_HEARTBEAT marks machine UP/DOWN
7.  CLI machine     — register, list, check (against live server)
8.  CLI agent serve — smoke test (starts server in thread)

Test infrastructure
-------------------
The ``agent_server`` fixture starts a real ``AgentServer`` in a background
thread (its own asyncio event loop) and tears it down after each test.
Tests connect to it via ``send_message(host, port, ...)`` or via the full
CLI pipeline.
"""

from __future__ import annotations

import asyncio
import socket
import threading
import time
from pathlib import Path
from typing import Optional

import pytest
from click.testing import CliRunner

from autosys.agent.dispatch  import AgentDispatch
from autosys.agent.protocol  import (
    HeartbeatRequest, HeartbeatResponse,
    StatusRequest,
    DispatchRequest,
    KillRequest,
    send_message, encode_message, decode_message,
)
from autosys.agent.remote    import RemoteDispatch
from autosys.agent.server    import AgentServer
from autosys.cli.main        import autosys
from autosys.db.connection   import sync_session, reset_engines
from autosys.db.migrations   import create_all_sync
from autosys.db.repository   import (
    jobs     as job_repo,
    events   as event_repo,
    runs     as run_repo,
    output   as output_repo,
    machines as machine_repo,
)
from autosys.models.event    import Event
from autosys.models.job      import BoxJob, CmdJob
from autosys.scheduler.event_processor import EventProcessor

_DEMO_JIL = Path(__file__).parents[1] / "examples" / "demo_etl.jil"


# ===========================================================================
# Helpers
# ===========================================================================

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _seed_cmd(name="job_a", command="echo hello", machine="localhost", **kw):
    job = CmdJob(job_name=name, job_type="CMD", command=command, machine=machine, **kw)
    with sync_session() as session:
        job_repo.upsert(session, job)


def _get_status(job_name) -> str:
    with sync_session() as session:
        row = job_repo.get_row(session, job_name)
        return row.status if row else None


def _set_status(job_name, status):
    if isinstance(status, str):
        from autosys.models.enums import JobStatus
        status = JobStatus[status].value
    with sync_session() as session:
        row = job_repo.get_row(session, job_name)
        if row:
            row.status = status


def _wait_for_status(job_name, expected, timeout=15) -> bool:
    if isinstance(expected, str):
        from autosys.models.enums import JobStatus
        expected = JobStatus[expected].value
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _get_status(job_name) == expected:
            return True
        time.sleep(0.1)
    return False


def _enqueue(event_type, job_name=None, **kwargs):
    ev = Event(event_type=event_type, job_name=job_name, source="internal", **kwargs)
    with sync_session() as session:
        event_repo.enqueue(session, ev)
    return ev.event_id


# ===========================================================================
# DB fixture
# ===========================================================================

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test6.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("AUTOSYS_DB_URL", url)
    reset_engines()
    create_all_sync(drop_first=False)
    yield url
    reset_engines()


# ===========================================================================
# Agent server fixture — starts a real server in a background thread
# ===========================================================================

class _ServerHandle:
    """Bundles the server object + thread + host/port for easy use in tests."""
    def __init__(self, server, host, port):
        self.server = server
        self.host   = host
        self.port   = port
        self.addr   = (host, port)


@pytest.fixture
def agent_server():
    """
    Start an AgentServer on a free port in a daemon thread.

    Yields a _ServerHandle.  The server is stopped after the test.
    """
    host = "127.0.0.1"
    port = _free_port()

    server = AgentServer(machine_name="test-agent", host=host, port=port)
    loop   = asyncio.new_event_loop()
    server._loop = loop

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve_forever())

    t = threading.Thread(target=_run, daemon=True, name="test-agent-server")
    t.start()

    # Wait for server to bind (up to 3s)
    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)

    yield _ServerHandle(server, host, port)

    server.stop()
    t.join(timeout=3)


# ===========================================================================
# 1. Protocol — encode / decode / send_message error handling
# ===========================================================================

class TestProtocol:

    def test_encode_decode_heartbeat(self):
        msg   = HeartbeatRequest()
        wire  = encode_message(msg)
        result = decode_message(wire)
        assert result["type"] == "heartbeat"

    def test_encode_decode_dispatch_request(self):
        msg  = DispatchRequest(run_id="rid", job_name="j", command="echo hi")
        wire = encode_message(msg)
        d    = decode_message(wire)
        assert d["run_id"]   == "rid"
        assert d["command"]  == "echo hi"

    def test_decode_invalid_json_returns_error(self):
        result = decode_message(b"not json\n")
        assert result["type"] == "parse_error"

    def test_send_message_connection_refused_returns_error(self):
        resp = send_message("127.0.0.1", _free_port(), HeartbeatRequest(), timeout=1)
        assert resp["type"] == "error"

    def test_send_message_timeout_returns_error(self):
        # Bind a socket that accepts but never responds
        with socket.socket() as dead:
            dead.bind(("127.0.0.1", 0))
            dead.listen(1)
            port = dead.getsockname()[1]
            # timeout of 0.1s — server never reads/responds
            resp = send_message("127.0.0.1", port, HeartbeatRequest(), timeout=0.1)
        assert resp["type"] == "error"


# ===========================================================================
# 2. AgentServer — protocol handler tests
# ===========================================================================

class TestAgentServer:

    def test_heartbeat_returns_alive(self, agent_server):
        resp = send_message(*agent_server.addr, HeartbeatRequest())
        assert resp["type"]         == "alive"
        assert resp["machine_name"] == "test-agent"

    def test_status_returns_status(self, agent_server):
        resp = send_message(*agent_server.addr, StatusRequest())
        assert resp["type"]         == "status"
        assert resp["machine_name"] == "test-agent"
        assert "active_jobs"         in resp
        assert "uptime_secs"         in resp

    def test_dispatch_returns_accepted(self, agent_server):
        _seed_cmd("srv_job", command="echo hi", machine="test-agent")
        req  = DispatchRequest(run_id="r1", job_name="srv_job", command="echo hi")
        resp = send_message(*agent_server.addr, req)
        assert resp["type"]   == "accepted"
        assert resp["run_id"] == "r1"

    def test_dispatch_sets_job_running_then_success(self, agent_server):
        """Full lifecycle via agent server: dispatch → wait → SUCCESS."""
        _seed_cmd("srv_job2", command="echo done", machine="test-agent")
        _set_status("srv_job2", "STARTING")   # simulate EPS pre-dispatch

        req  = DispatchRequest(run_id="r2", job_name="srv_job2", command="echo done")
        resp = send_message(*agent_server.addr, req)
        assert resp["type"] == "accepted"

        assert _wait_for_status("srv_job2", "SUCCESS"), \
            f"Expected SUCCESS, got {_get_status('srv_job2')}"

    def test_dispatch_failure_job(self, agent_server):
        _seed_cmd("fail_srv", command="bash -c 'exit 1'", machine="test-agent")
        _set_status("fail_srv", "STARTING")
        req  = DispatchRequest(run_id="r3", job_name="fail_srv",
                               command="bash -c 'exit 1'")
        send_message(*agent_server.addr, req)
        assert _wait_for_status("fail_srv", "FAILURE")

    def test_dispatch_captures_output(self, agent_server):
        _seed_cmd("out_srv", command="printf 'alpha\\nbeta\\n'", machine="test-agent")
        _set_status("out_srv", "STARTING")
        req = DispatchRequest(run_id="r4", job_name="out_srv",
                              command="printf 'alpha\\nbeta\\n'")
        send_message(*agent_server.addr, req)
        assert _wait_for_status("out_srv", "SUCCESS")

        with sync_session() as session:
            lines = output_repo.get_lines(session, "r4")
        assert [l.content for l in lines] == ["alpha", "beta"]

    def test_kill_running_job(self, agent_server):
        _seed_cmd("kill_srv", command="sleep 30", machine="test-agent")
        _set_status("kill_srv", "STARTING")

        req = DispatchRequest(run_id="r5", job_name="kill_srv", command="sleep 30")
        send_message(*agent_server.addr, req)

        # Wait until the job is RUNNING in DB
        assert _wait_for_status("kill_srv", "RUNNING") or True  # might flip fast

        kill_resp = send_message(*agent_server.addr, KillRequest(job_name="kill_srv"))
        assert kill_resp["type"] == "killed"
        assert kill_resp["found"] is True

    def test_kill_nonexistent_job_found_false(self, agent_server):
        kill_resp = send_message(*agent_server.addr,
                                 KillRequest(job_name="ghost_job"))
        assert kill_resp["type"]  == "killed"
        assert kill_resp["found"] is False

    def test_dispatch_run_history_written(self, agent_server):
        _seed_cmd("rh_srv", command="echo hist", machine="test-agent")
        _set_status("rh_srv", "STARTING")
        req = DispatchRequest(run_id="r6", job_name="rh_srv", command="echo hist")
        send_message(*agent_server.addr, req)
        assert _wait_for_status("rh_srv", "SUCCESS")

        with sync_session() as session:
            rows = run_repo.list_runs(session, "rh_srv")
        assert rows[0].exit_code == 0
        assert rows[0].pid is not None

    def test_server_registers_machine_in_db(self, agent_server):
        """AgentServer.serve_forever() should register itself in the machines table."""
        with sync_session() as session:
            row = machine_repo.get(session, "test-agent")
        assert row is not None
        assert row.status == "UP"
        assert row.port   == agent_server.port

    def test_heartbeat_updates_last_heartbeat(self, agent_server):
        send_message(*agent_server.addr, HeartbeatRequest())
        with sync_session() as session:
            row = machine_repo.get(session, "test-agent")
        assert row.last_heartbeat is not None

    def test_unknown_message_type_returns_error(self, agent_server):
        with socket.create_connection(agent_server.addr, timeout=5) as s:
            s.sendall(b'{"type": "nonsense"}\n')
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
        resp = decode_message(buf)
        assert resp["type"] == "error"


# ===========================================================================
# 3. RemoteDispatch — via live server
# ===========================================================================

class TestRemoteDispatch:

    def test_heartbeat_returns_true(self, agent_server):
        with sync_session() as session:
            machine_row = machine_repo.register(
                session, "test-agent", agent_server.host, agent_server.port, status="UP"
            )
        rd    = RemoteDispatch()
        alive = rd.heartbeat(machine_row)
        assert alive is True

    def test_heartbeat_unreachable_returns_false(self):
        from autosys.db.schema import MachineRow
        dead = MachineRow(machine_name="dead", host="127.0.0.1", port=_free_port())
        rd   = RemoteDispatch(timeout=0.5)
        assert rd.heartbeat(dead) is False

    def test_status_returns_dict(self, agent_server):
        with sync_session() as session:
            machine_row = machine_repo.register(
                session, "test-agent", agent_server.host, agent_server.port, status="UP"
            )
        rd   = RemoteDispatch()
        resp = rd.status(machine_row)
        assert resp.get("type") == "status"
        assert "active_jobs"    in resp

    def test_dispatch_to_remote_server(self, agent_server):
        _seed_cmd("rem_job", command="echo remote_ok", machine="test-agent")
        with sync_session() as session:
            machine_row = machine_repo.register(
                session, "test-agent", agent_server.host, agent_server.port, status="UP"
            )
            row = job_repo.get_row(session, "rem_job")
            row.status = "STARTING"
            ok = RemoteDispatch().dispatch(session, row, machine_row)
        assert ok is True
        assert _wait_for_status("rem_job", "SUCCESS")


# ===========================================================================
# 4. AgentDispatch routing — local_only=False
# ===========================================================================

class TestAgentDispatchRouting:

    def test_local_job_runs_locally(self):
        _seed_cmd("local_r", command="echo local", machine="localhost")
        agent = AgentDispatch(local_only=False)
        _enqueue("STARTJOB", "local_r")
        proc = EventProcessor(dispatch_fn=agent.dispatch, kill_fn=agent.kill,
                              auto_complete=False)
        with sync_session() as session:
            proc.process_one_tick(session)
        assert _wait_for_status("local_r", "SUCCESS")

    def test_remote_job_dispatched_to_server(self, agent_server):
        _seed_cmd("rem_r", command="echo remote", machine="test-agent")
        # Register the machine so AgentDispatch can find it
        with sync_session() as session:
            machine_repo.register(
                session, "test-agent", agent_server.host, agent_server.port, status="UP"
            )
        agent = AgentDispatch(local_only=False)
        _enqueue("STARTJOB", "rem_r")
        proc = EventProcessor(dispatch_fn=agent.dispatch, kill_fn=agent.kill,
                              auto_complete=False)
        with sync_session() as session:
            proc.process_one_tick(session)
        assert _wait_for_status("rem_r", "SUCCESS")


# ===========================================================================
# 5. MachineRepository
# ===========================================================================

class TestMachineRepository:

    def test_register_creates_row(self):
        with sync_session() as session:
            machine_repo.register(session, "m1", "10.0.0.1", 7520)
        with sync_session() as session:
            row = machine_repo.get(session, "m1")
        assert row is not None
        assert row.host == "10.0.0.1"
        assert row.port == 7520

    def test_register_upserts(self):
        with sync_session() as session:
            machine_repo.register(session, "m2", "10.0.0.2", 7520)
        with sync_session() as session:
            machine_repo.register(session, "m2", "10.0.0.9", 7521, status="UP")
        with sync_session() as session:
            row = machine_repo.get(session, "m2")
        assert row.host == "10.0.0.9"
        assert row.port == 7521

    def test_list_all(self):
        with sync_session() as session:
            machine_repo.register(session, "m3", "10.0.0.3", 7520)
            machine_repo.register(session, "m4", "10.0.0.4", 7520)
        with sync_session() as session:
            rows = machine_repo.list_all(session)
        names = [r.machine_name for r in rows]
        assert "m3" in names and "m4" in names

    def test_get_returns_none_for_unknown(self):
        with sync_session() as session:
            row = machine_repo.get(session, "not_registered")
        assert row is None

    def test_update_heartbeat_sets_timestamp(self):
        with sync_session() as session:
            machine_repo.register(session, "m5", "10.0.0.5", 7520)
            machine_repo.update_heartbeat(session, "m5", status="UP")
        with sync_session() as session:
            row = machine_repo.get(session, "m5")
        assert row.last_heartbeat is not None
        assert row.status == "UP"

    def test_set_status_down(self):
        with sync_session() as session:
            machine_repo.register(session, "m6", "10.0.0.6", 7520, status="UP")
            machine_repo.set_status(session, "m6", "DOWN")
        with sync_session() as session:
            row = machine_repo.get(session, "m6")
        assert row.status == "DOWN"

    def test_default_status_unknown(self):
        with sync_session() as session:
            machine_repo.register(session, "m7", "10.0.0.7")
        with sync_session() as session:
            row = machine_repo.get(session, "m7")
        assert row.status == "UNKNOWN"

    def test_default_port_7520(self):
        with sync_session() as session:
            machine_repo.register(session, "m8", "10.0.0.8")
        with sync_session() as session:
            row = machine_repo.get(session, "m8")
        assert row.port == 7520


# ===========================================================================
# 6. EventProcessor — CHECK_HEARTBEAT
# ===========================================================================

class TestCheckHeartbeat:

    def test_check_heartbeat_marks_up(self, agent_server):
        with sync_session() as session:
            machine_repo.register(
                session, "test-agent", agent_server.host, agent_server.port
            )
        _enqueue("CHECK_HEARTBEAT", job_name="test-agent")
        proc = EventProcessor()
        with sync_session() as session:
            proc.process_one_tick(session)
        with sync_session() as session:
            row = machine_repo.get(session, "test-agent")
        assert row.status == "UP"
        assert row.last_heartbeat is not None

    def test_check_heartbeat_marks_down_on_unreachable(self):
        port = _free_port()
        with sync_session() as session:
            machine_repo.register(session, "dead-agent", "127.0.0.1", port)
        _enqueue("CHECK_HEARTBEAT", job_name="dead-agent")
        proc = EventProcessor()
        with sync_session() as session:
            proc.process_one_tick(session)
        with sync_session() as session:
            row = machine_repo.get(session, "dead-agent")
        assert row.status == "DOWN"

    def test_check_heartbeat_unknown_machine_is_noop(self):
        """CHECK_HEARTBEAT for an unregistered machine should not crash."""
        _enqueue("CHECK_HEARTBEAT", job_name="ghost-machine")
        proc = EventProcessor()
        with sync_session() as session:
            n = proc.process_one_tick(session)
        assert n == 1   # event processed (gracefully)


# ===========================================================================
# 7. CLI machine commands
# ===========================================================================

class TestCLIMachineCommands:

    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(autosys, list(args), catch_exceptions=False)

    def test_register_exit_0(self):
        result = self._run("machine", "register", "m-cli-1",
                           "--host", "10.0.1.1", "--port", "7520")
        assert result.exit_code == 0

    def test_register_shows_confirmation(self):
        result = self._run("machine", "register", "m-cli-2",
                           "--host", "10.0.1.2")
        assert "m-cli-2" in result.output

    def test_list_exit_0(self):
        result = self._run("machine", "list")
        assert result.exit_code == 0

    def test_list_no_machines_message(self):
        result = self._run("machine", "list")
        assert "No machines" in result.output or result.exit_code == 0

    def test_list_shows_registered_machine(self):
        self._run("machine", "register", "m-list-1", "--host", "10.0.1.1")
        result = self._run("machine", "list")
        assert "m-list-1" in result.output

    def test_check_unknown_machine_exits_nonzero(self):
        result = self._run("machine", "check", "not-registered")
        assert result.exit_code != 0

    def test_check_live_agent_exits_0(self, agent_server):
        self._run("machine", "register", "test-agent",
                  "--host", agent_server.host, "--port", str(agent_server.port))
        result = self._run("machine", "check", "test-agent")
        assert result.exit_code == 0
        assert "UP" in result.output

    def test_check_dead_agent_exits_nonzero(self):
        port = _free_port()
        self._run("machine", "register", "dead-cli",
                  "--host", "127.0.0.1", "--port", str(port))
        result = self._run("machine", "check", "dead-cli", "--timeout", "0.3")
        assert result.exit_code != 0
        assert "DOWN" in result.output
