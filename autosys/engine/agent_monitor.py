"""Agent health monitor — checks agent heartbeats and raises alarms on failure."""
from __future__ import annotations

from datetime import datetime
from typing import Optional
import uuid

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from autosys.db.schema import MachineRow, AlarmRow
from autosys.agent.protocol import send_message, HeartbeatRequest


class AgentHealthMonitor:
    """
    Monitors System Agent health by sending heartbeat requests.

    For each registered machine, sends a heartbeat. If the agent doesn't
    respond, raises an alarm and marks the machine as DOWN.
    """

    def __init__(self, timeout: float = 5.0) -> None:
        self.timeout = timeout
        self._send_fn = send_message

    def check_all(self, session: Session) -> tuple[int, int]:
        """
        Check all registered machines.

        Returns (alive_count, dead_count).
        """
        machines = list(session.scalars(select(MachineRow)))
        alive = 0
        dead = 0
        for m in machines:
            if self._check_one(session, m):
                alive += 1
            else:
                dead += 1
        return (alive, dead)

    def _check_one(self, session: Session, machine: MachineRow) -> bool:
        """Send heartbeat to one machine. Returns True if alive."""
        try:
            resp = self._send_fn(machine.host, machine.port,
                                 HeartbeatRequest(), timeout=self.timeout)
            if resp.get("type") == "alive":
                return True
        except Exception:
            pass

        # Agent is down — raise alarm
        self._raise_alarm(session, machine)
        return False

    def _raise_alarm(self, session: Session, machine: MachineRow) -> None:
        """Raise an alarm for a downed agent."""
        session.add(AlarmRow(
            alarm_id=str(uuid.uuid4()),
            job_name=machine.machine_name,
            alarm_type="AGENT_DOWN",
            message=f"Agent {machine.machine_name} at {machine.host}:{machine.port} is not responding",
            raised_at=datetime.utcnow(),
        ))
        logger.warning("Agent %s is DOWN", machine.machine_name)
