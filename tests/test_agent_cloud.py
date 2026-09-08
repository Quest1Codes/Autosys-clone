"""Phase 15-16 — Agent Monitoring + Cloud & Airflow tests."""
from __future__ import annotations

import json, pytest, uuid
from datetime import datetime

from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.schema import MachineRow, AlarmRow, JobRow
from autosys.models.enums import JobStatus
from autosys.engine.agent_monitor import AgentHealthMonitor
from autosys.engine.cloud_integration import AirflowClient, CloudJobSubmitter

@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'test_agent_cloud.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    reset_engines(); create_all_sync(); yield; reset_engines()


# ===========================================================================
# Agent Health Monitor
# ===========================================================================

class TestAgentHealthMonitor:

    def test_all_agents_alive(self, fresh_db):
        def mock_send(host, port, msg, timeout=5.0):
            return {"type": "alive", "machine_name": "test"}

        with sync_session() as s:
            # Delete seeded machines first
            from sqlalchemy import delete
            s.execute(delete(MachineRow))
            s.add(MachineRow(machine_name="m1", host="localhost", port=7520))
            s.add(MachineRow(machine_name="m2", host="localhost", port=7521))
            s.commit()

        monitor = AgentHealthMonitor()
        monitor._send_fn = mock_send
        with sync_session() as s:
            alive, dead = monitor.check_all(s)
            s.commit()
        assert alive == 2
        assert dead == 0

    def test_agent_down_raises_alarm(self, fresh_db):
        def mock_send(host, port, msg, timeout=5.0):
            return {"type": "error", "reason": "connection refused"}

        with sync_session() as s:
            from sqlalchemy import delete
            s.execute(delete(MachineRow))
            _make_job(s, "m1")
            s.add(MachineRow(machine_name="m1", host="badhost", port=7520))
            s.commit()

        monitor = AgentHealthMonitor()
        monitor._send_fn = mock_send
        with sync_session() as s:
            alive, dead = monitor.check_all(s)
            s.commit()
        assert alive == 0
        assert dead == 1
        with sync_session() as s:
            from sqlalchemy import select
            alarms = list(s.scalars(select(AlarmRow).where(AlarmRow.alarm_type == "AGENT_DOWN")))
            assert len(alarms) >= 1

    def test_mixed_alive_and_dead(self, fresh_db):
        def mock_send(host, port, msg, timeout=5.0):
            if "good" in host:
                return {"type": "alive", "machine_name": "good"}
            return {"type": "error"}

        with sync_session() as s:
            from sqlalchemy import delete
            s.execute(delete(MachineRow))
            _make_job(s, "good")
            _make_job(s, "bad")
            s.add(MachineRow(machine_name="good", host="goodhost", port=7520))
            s.add(MachineRow(machine_name="bad", host="badhost", port=7520))
            s.commit()

        monitor = AgentHealthMonitor()
        monitor._send_fn = mock_send
        with sync_session() as s:
            alive, dead = monitor.check_all(s)
            s.commit()
        assert alive == 1
        assert dead == 1

    def test_no_machines(self, fresh_db):
        with sync_session() as s:
            from sqlalchemy import delete
            s.execute(delete(MachineRow))
            s.commit()
        monitor = AgentHealthMonitor()
        with sync_session() as s:
            alive, dead = monitor.check_all(s)
            s.commit()
        assert alive == 0
        assert dead == 0


# ===========================================================================
# Airflow Client
# ===========================================================================

class TestAirflowClient:

    def test_trigger_dag(self):
        captured = {}
        def mock_post(url, headers, body):
            captured["url"] = url
            captured["body"] = json.loads(body)
            return {"dag_run_id": "test-run-1", "state": "running"}

        client = AirflowClient("http://airflow:8080/api/v1", http_post_fn=mock_post)
        result = client.trigger_dag("my_dag", conf={"key": "value"})
        assert result["dag_run_id"] == "test-run-1"
        assert "my_dag" in captured["url"]
        assert captured["body"]["conf"]["key"] == "value"

    def test_trigger_dag_no_conf(self):
        def mock_post(url, headers, body):
            data = json.loads(body)
            assert data["conf"] == {}
            return {"dag_run_id": "run-2"}

        client = AirflowClient("http://airflow:8080/api/v1", http_post_fn=mock_post)
        result = client.trigger_dag("simple_dag")
        assert result["dag_run_id"] == "run-2"


# ===========================================================================
# Cloud Job Submitter
# ===========================================================================

class TestCloudJobSubmitter:

    def test_submit_aws_batch(self):
        captured = {}
        def mock_post(url, headers, body):
            captured["url"] = url
            captured["body"] = json.loads(body)
            return {"jobId": "aws-job-123"}

        submitter = CloudJobSubmitter(http_post_fn=mock_post)
        result = submitter.submit_aws_batch(
            job_definition="my-def", job_queue="my-queue",
            command=["echo", "hello"], region="us-west-2",
        )
        assert result["jobId"] == "aws-job-123"
        assert "us-west-2" in captured["url"]
        assert captured["body"]["jobDefinition"] == "my-def"
        assert captured["body"]["containerOverrides"]["command"] == ["echo", "hello"]

    def test_submit_gcp_cloudrun(self):
        captured = {}
        def mock_post(url, headers, body):
            captured["url"] = url
            captured["body"] = json.loads(body)
            return {"status": "submitted"}

        submitter = CloudJobSubmitter(http_post_fn=mock_post)
        result = submitter.submit_gcp_cloudrun(
            service_url="https://run.googleapis.com/jobs/run",
            command="python main.py",
        )
        assert result["status"] == "submitted"
        assert captured["body"]["command"] == "python main.py"


def _make_job(session, name):
    session.add(JobRow(
        job_name=name, job_type="CMD", command="echo hi",
        machine="localhost", status=JobStatus.INACTIVE.value, owner="test",
    ))
    session.flush()
