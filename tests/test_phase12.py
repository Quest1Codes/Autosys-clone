import asyncio
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest

from autosys.agent.server import AgentServer
from autosys.db.connection import reset_engines, sync_session
from autosys.db.migrations import create_all_sync
from autosys.db.repository import jobs as job_repo, runs as run_repo
from autosys.db.schema import AlarmRow
from sqlalchemy import select

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]

class _ServerHandle:
    def __init__(self, server, host, port, name):
        self.server = server
        self.host = host
        self.port = port
        self.name = name

def _start_agent_server(machine_name: str) -> _ServerHandle:
    host = "127.0.0.1"
    port = _free_port()

    server = AgentServer(machine_name=machine_name, host=host, port=port)
    loop = asyncio.new_event_loop()
    server._loop = loop

    def _run():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(server.serve_forever())

    t = threading.Thread(target=_run, daemon=True, name=f"{machine_name}-thread")
    t.start()

    deadline = time.time() + 3
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=0.2):
                break
        except OSError:
            time.sleep(0.05)

    return _ServerHandle(server, host, port, machine_name)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test12.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("AUTOSYS_DB_URL", url)
    reset_engines()
    create_all_sync(drop_first=False)
    yield url
    reset_engines()

@pytest.fixture
def agent_1():
    handle = _start_agent_server("etl-server-01")
    yield handle
    handle.server.stop()

@pytest.fixture
def agent_2():
    handle = _start_agent_server("etl-server-02")
    yield handle
    handle.server.stop()

@pytest.fixture
def app_server(isolated_db, monkeypatch):
    port = _free_port()
    
    # We must ensure the subprocess knows which DB URL to use!
    env = os.environ.copy()
    env["AUTOSYS_DB_URL"] = isolated_db
    env["AUTOSYS_START_SCHEDULER"] = "true"  # Ensure lifespan task starts EPS

    # Find the autosys CLI binary — could be next to sys.executable, in the
    # user scripts dir, or on PATH (depending on how it was installed).
    import shutil, site
    autosys_bin = str(Path(sys.executable).parent / "autosys")
    if not Path(autosys_bin).exists():
        for d in site.getuserbase() and [Path(site.getuserbase()) / "bin"] or []:
            cand = str(d / "autosys")
            if Path(cand).exists():
                autosys_bin = cand
                break
        else:
            autosys_bin = shutil.which("autosys") or autosys_bin
    proc = subprocess.Popen(
        [autosys_bin, "scheduler", "serve", "--port", str(port), "--host", "127.0.0.1"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True
    )
    
    # Wait for ready
    url = f"http://127.0.0.1:{port}"
    deadline = time.time() + 5
    ready = False
    while time.time() < deadline:
        try:
            r = httpx.get(f"{url}/health")
            if r.status_code == 200:
                ready = True
                break
        except Exception:
            time.sleep(0.1)
    
    if not ready:
        proc.kill()
        out, _ = proc.communicate()
        pytest.fail(f"App server failed to start: {out}")
        
    yield url
    
    proc.kill()
    proc.wait(timeout=2)

# ---------------------------------------------------------------------------
# Test
# ---------------------------------------------------------------------------

def test_phase12_end_to_end(agent_1, agent_2, app_server):
    # 1. Register the two agent machines pointing to our test fixtures
    client = httpx.Client(base_url=app_server)
    
    r = client.post("/api/v1/machines", json={
        "machine_name": agent_1.name,
        "host": agent_1.host,
        "port": agent_1.port,
        "machine_type": "agent",
        "description": "Agent 1"
    })
    assert r.status_code in (200, 201), r.text
    
    r = client.post("/api/v1/machines", json={
        "machine_name": agent_2.name,
        "host": agent_2.host,
        "port": agent_2.port,
        "machine_type": "agent",
        "description": "Agent 2"
    })
    assert r.status_code in (200, 201), r.text

    # 2. Import examples/demo_etl.jil via REST API
    jil_path = Path(__file__).parents[1] / "examples" / "demo_etl.jil"
    jil_text = jil_path.read_text()
    
    # Replace the fake commands with sleep so they exit with 0 successfully
    import re
    jil_text = re.sub(r'command:.*', 'command: sleep 0.1', jil_text)
    
    r = client.post("/api/v1/jil/import", json={"content": jil_text})
    assert r.status_code in (200, 201), r.text

    # 3. Fire STARTJOB demo_etl_box
    r = client.post("/api/v1/jobs/demo_etl_box/sendevent", json={"event_type": "STARTJOB"})
    assert r.status_code in (200, 201, 202), r.text

    # 4. Poll until demo_etl_box reaches SUCCESS (wait up to 10 seconds)
    deadline = time.time() + 10
    success = False
    while time.time() < deadline:
        r = client.get("/api/v1/jobs/demo_etl_box")
        if r.status_code == 200 and r.json()["status"] == 4:
            success = True
            break
        time.sleep(0.5)

    if not success:
        # Print status of all jobs for debugging
        jobs_resp = client.get("/api/v1/jobs").json()
        print("Jobs state:", jobs_resp)
        pytest.fail("demo_etl_box did not reach SUCCESS")

    # 5. Assert the dependency chain ran in the correct order
    with sync_session() as session:
        # All runs
        runs = run_repo.get_history(session, limit=100)
        if len(runs) < 4:
            jobs_resp = client.get("/api/v1/jobs").json()
            print("Jobs state:", jobs_resp)
            print("Runs:", runs)
        assert len(runs) >= 4  # 4 children
        
        runs_by_job = {r.job_name: r for r in runs if r.job_name != "demo_etl_box"}
        assert "check_source_ready" in runs_by_job
        assert "extract_sales" in runs_by_job
        assert "generate_report" in runs_by_job
        assert "load_to_warehouse" in runs_by_job
        assert "send_success_email" in runs_by_job
        
        c = runs_by_job["check_source_ready"]
        e = runs_by_job["extract_sales"]
        g = runs_by_job["generate_report"]
        l = runs_by_job["load_to_warehouse"]
        s = runs_by_job["send_success_email"]
        
        # Dependency assertions
        assert e.start_time >= c.end_time
        assert g.start_time >= e.end_time
        assert l.start_time >= e.end_time
        assert s.start_time >= g.end_time
        assert s.start_time >= l.end_time
        
        # All have non-null exit codes (0 for success)
        for job_name, run in runs_by_job.items():
            assert run.exit_code == 0
            
        # Assert no unresolved alarms
        stmt = select(AlarmRow).where(AlarmRow.cleared_at.is_(None))
        alarms = session.execute(stmt).scalars().all()
        assert len(alarms) == 0
