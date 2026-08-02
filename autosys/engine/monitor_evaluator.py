"""
Monitor Evaluator — evaluates monbro definitions each EPS tick.

Supported monitor types:
- FILE_MONITOR: Watch for file existence — triggers STARTJOB event for linked job
- CPU_MONITOR: Check CPU usage threshold — raises alarm if exceeded
- DISK_MONITOR: Check disk space — raises alarm if below threshold
- PROCESS_MONITOR: Check if process is running — raises alarm if not found
- LOG_MONITOR: Scan log file for pattern — raises alarm if pattern found
- TEXT_MONITOR: Watch text file for specific content — raises alarm if found

Usage
-----
    from autosys.engine.monitor_evaluator import MonitorEvaluator

    evaluator = MonitorEvaluator()
    evaluator.tick(session)  # call every EPS tick
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from datetime import datetime
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from autosys.db.schema import MonitorRow, AlarmRow, EventQueueRow, JobRunRow
from autosys.db.repository import jobs as job_repo
import uuid


class MonitorEvaluator:
    """
    Evaluates all monitor definitions each tick.

    For each monitor, checks its condition and raises alarms or enqueues
    events as appropriate.
    """

    def __init__(self) -> None:
        self._alarm_count = 0
        self._event_count = 0

    def tick(self, session: Session) -> tuple[int, int]:
        """
        Evaluate all monitors.

        Returns (alarms_raised, events_enqueued).
        """
        self._alarm_count = 0
        self._event_count = 0

        monitors = list(session.scalars(select(MonitorRow)))
        for mon in monitors:
            try:
                self._evaluate_one(session, mon)
            except Exception as exc:
                logger.warning(
                    "MonitorEvaluator: error evaluating %r: %s",
                    mon.monbro_name, exc,
                )

        return (self._alarm_count, self._event_count)

    def _evaluate_one(self, session: Session, mon: MonitorRow) -> None:
        """Evaluate a single monitor definition."""
        attrs = {}
        if mon.attributes_json:
            try:
                attrs = json.loads(mon.attributes_json)
            except json.JSONDecodeError:
                logger.warning("Monitor %r: invalid JSON attributes", mon.monbro_name)
                return

        handler = _HANDLERS.get(mon.monbro_type)
        if handler is None:
            logger.debug("Monitor %r: no handler for type %r", mon.monbro_name, mon.monbro_type)
            return

        result = handler(attrs)
        if result is None:
            return

        if result.get("alarm"):
            self._raise_alarm(session, mon, result["alarm"])
            self._alarm_count += 1

        if result.get("event") and mon.job_name:
            self._enqueue_event(session, mon, result["event"])
            self._event_count += 1

    def _raise_alarm(self, session: Session, mon: MonitorRow, message: str) -> None:
        """Raise an alarm for this monitor."""
        alarm = AlarmRow(
            alarm_id=str(uuid.uuid4()),
            job_name=mon.job_name or mon.monbro_name,
            alarm_type=mon.monbro_type,
            message=message,
            raised_at=datetime.utcnow(),
        )
        session.add(alarm)

    def _enqueue_event(self, session: Session, mon: MonitorRow, event_type: str) -> None:
        """Enqueue an event for the monitor's linked job."""
        session.add(EventQueueRow(
            event_id=str(uuid.uuid4()),
            event_type=event_type,
            job_name=mon.job_name,
            source="internal",
        ))


# ---------------------------------------------------------------------------
# Monitor type handlers
# ---------------------------------------------------------------------------

def _handle_file_monitor(attrs: dict) -> Optional[dict]:
    """Check if a file exists. Returns event if file found."""
    path = attrs.get("path")
    if not path:
        return None
    if os.path.exists(path):
        return {"event": "STARTJOB", "alarm": f"File appeared: {path}"}
    return None


def _handle_cpu_monitor(attrs: dict) -> Optional[dict]:
    """Check CPU usage against threshold."""
    threshold = attrs.get("threshold", 90)
    try:
        load = subprocess.check_output(
            ["sh", "-c", "top -l 1 -n 0 | grep 'CPU usage' | awk '{print $3}' | tr -d '%'"],
            timeout=5,
            text=True,
        ).strip()
        cpu_usage = float(load)
    except Exception:
        try:
            load = os.getloadavg()[0] * 100 / os.cpu_count()
            cpu_usage = load
        except Exception:
            return None

    if cpu_usage > threshold:
        return {"alarm": f"CPU usage {cpu_usage:.1f}% exceeds threshold {threshold}%"}
    return None


def _handle_disk_monitor(attrs: dict) -> Optional[dict]:
    """Check disk space against threshold."""
    path = attrs.get("path", "/")
    threshold_gb = attrs.get("threshold_gb", 1)
    try:
        usage = shutil.disk_usage(path)
        free_gb = usage.free / (1024 ** 3)
        if free_gb < threshold_gb:
            return {"alarm": f"Disk free {free_gb:.1f}GB below threshold {threshold_gb}GB on {path}"}
    except Exception:
        return None
    return None


def _handle_process_monitor(attrs: dict) -> Optional[dict]:
    """Check if a process is running."""
    process_name = attrs.get("process")
    if not process_name:
        return None
    try:
        result = subprocess.run(
            ["pgrep", "-f", process_name],
            capture_output=True, timeout=5,
        )
        if result.returncode != 0:
            return {"alarm": f"Process '{process_name}' not found"}
    except Exception:
        return None
    return None


def _handle_log_monitor(attrs: dict) -> Optional[dict]:
    """Scan a log file for a pattern."""
    path = attrs.get("path")
    pattern = attrs.get("pattern")
    if not path or not pattern:
        return None
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            for line in f:
                if pattern in line:
                    return {"alarm": f"Pattern '{pattern}' found in log {path}"}
    except Exception:
        return None
    return None


def _handle_text_monitor(attrs: dict) -> Optional[dict]:
    """Watch a text file for specific content."""
    path = attrs.get("path")
    content = attrs.get("content")
    if not path or not content:
        return None
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r") as f:
            text = f.read()
            if content in text:
                return {"alarm": f"Content '{content}' found in {path}"}
    except Exception:
        return None
    return None


_HANDLERS = {
    "FILE_MONITOR": _handle_file_monitor,
    "CPU_MONITOR": _handle_cpu_monitor,
    "DISK_MONITOR": _handle_disk_monitor,
    "PROCESS_MONITOR": _handle_process_monitor,
    "LOG_MONITOR": _handle_log_monitor,
    "TEXT_MONITOR": _handle_text_monitor,
}


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def generate_job_report(
    session: Session,
    date_from: datetime,
    date_to: datetime,
) -> dict:
    """Generate a job run summary report."""
    runs = list(session.scalars(
        select(JobRunRow).where(
            JobRunRow.start_time >= date_from,
            JobRunRow.start_time <= date_to,
        )
    ))
    total = len(runs)
    by_status = {}
    for r in runs:
        status = r.status or "UNKNOWN"
        by_status[status] = by_status.get(status, 0) + 1
    return {
        "report_type": "JOB_REPORT",
        "date_from": date_from.isoformat() + "Z",
        "date_to": date_to.isoformat() + "Z",
        "total_runs": total,
        "by_status": by_status,
    }


def generate_alarm_report(
    session: Session,
    date_from: datetime,
    date_to: datetime,
) -> dict:
    """Generate an alarm summary report."""
    alarms = list(session.scalars(
        select(AlarmRow).where(
            AlarmRow.raised_at >= date_from,
            AlarmRow.raised_at <= date_to,
        )
    ))
    total = len(alarms)
    by_type = {}
    cleared = 0
    for a in alarms:
        by_type[a.alarm_type] = by_type.get(a.alarm_type, 0) + 1
        if a.cleared_at is not None:
            cleared += 1
    return {
        "report_type": "ALARM_REPORT",
        "date_from": date_from.isoformat() + "Z",
        "date_to": date_to.isoformat() + "Z",
        "total_alarms": total,
        "cleared": cleared,
        "by_type": by_type,
    }
