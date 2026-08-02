"""
Prometheus-format metrics endpoint.

GET /api/v1/metrics — returns Prometheus text-format metrics:
  - autosys_jobs_total{status="..."} — gauge by job status
  - autosys_events_processed_total — counter
  - autosys_event_queue_depth — gauge
  - autosys_job_run_duration_seconds{job_name="..."} — summary
  - autosys_agent_heartbeat_seconds{machine="..."} — gauge

Controlled by AUTOSYS_METRICS_ENABLED env var (default: true).
"""
from __future__ import annotations

import os
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Response
from loguru import logger
from sqlalchemy import select, func, text

from autosys.db.connection import sync_session
from autosys.db.schema import JobRow, EventQueueRow, EventHistoryRow, JobRunRow, MachineRow

router = APIRouter(prefix="/api/v1", tags=["metrics"])


def _is_metrics_enabled() -> bool:
    return os.environ.get("AUTOSYS_METRICS_ENABLED", "true").lower() == "true"


def _collect_metrics() -> str:
    """Collect all metrics and return Prometheus text format."""
    lines: list[str] = []

    with sync_session() as session:
        # --- Job status counts ---
        rows = session.execute(
            select(JobRow.status, func.count())
            .group_by(JobRow.status)
        ).all()
        status_map = {0: "INACTIVE", 1: "ACTIVATED", 2: "STARTING", 3: "RUNNING",
                       4: "SUCCESS", 5: "FAILURE", 6: "TERMINATED", 7: "ON_HOLD",
                       8: "ON_ICE", 9: "RESTART", 10: "QUE_WAIT", 11: "PEND_MACH",
                       12: "RESOURCE"}
        lines.append("# HELP autosys_jobs_total Total jobs by status")
        lines.append("# TYPE autosys_jobs_total gauge")
        for status_code, count in rows:
            label = status_map.get(status_code, f"UNKNOWN_{status_code}")
            lines.append(f'autsys_jobs_total{{status="{label}"}} {count}')

        # --- Event queue depth ---
        queue_depth = session.execute(
            select(func.count()).where(EventQueueRow.processed == False)  # noqa: E712
        ).scalar() or 0
        lines.append("# HELP autosys_event_queue_depth Number of unprocessed events in queue")
        lines.append("# TYPE autosys_event_queue_depth gauge")
        lines.append(f"autosys_event_queue_depth {queue_depth}")

        # --- Events processed total ---
        processed = session.execute(
            select(func.count()).where(EventQueueRow.processed == True)  # noqa: E712
        ).scalar() or 0
        lines.append("# HELP autosys_events_processed_total Total events processed")
        lines.append("# TYPE autosys_events_processed_total counter")
        lines.append(f"autosys_events_processed_total {processed}")

        # --- Job run durations (last 100 runs) ---
        runs = session.execute(
            select(JobRunRow.job_name, JobRunRow.start_time, JobRunRow.end_time)
            .where(JobRunRow.end_time.isnot(None))
            .order_by(JobRunRow.end_time.desc())
            .limit(100)
        ).all()
        lines.append("# HELP autosys_job_run_duration_seconds Job run duration in seconds")
        lines.append("# TYPE autosys_job_run_duration_seconds summary")
        durations: list[float] = []
        for job_name, start, end in runs:
            if start and end:
                duration = (end - start).total_seconds()
                durations.append(duration)
                lines.append(f'autsys_job_run_duration_seconds{{job_name="{job_name}"}} {duration:.3f}')

        # --- Agent heartbeat age ---
        machines = session.execute(
            select(MachineRow.machine_name, MachineRow.last_heartbeat)
        ).all()
        lines.append("# HELP autosys_agent_heartbeat_seconds Seconds since last agent heartbeat")
        lines.append("# TYPE autosys_agent_heartbeat_seconds gauge")
        now = datetime.utcnow()
        for name, hb in machines:
            if hb:
                age = (now - hb).total_seconds()
                lines.append(f'autsys_agent_heartbeat_seconds{{machine="{name}"}} {age:.1f}')

    return "\n".join(lines) + "\n"


@router.get("/metrics")
def get_metrics():
    """Return Prometheus-format metrics."""
    if not _is_metrics_enabled():
        return Response(content="", media_type="text/plain", status_code=404)

    try:
        metrics_text = _collect_metrics()
        return Response(content=metrics_text, media_type="text/plain; version=0.0.4")
    except Exception as exc:
        logger.error("Metrics collection error: {}", exc)
        return Response(
            content=f"# Error collecting metrics: {exc}\n",
            media_type="text/plain",
            status_code=500,
        )
