"""Multi-cycle simulation runner — generates runtime history from JIL alone.

Fires FORCE_STARTJOB on all top-level boxes, runs N ticks per cycle,
resets jobs to INACTIVE between cycles, and accumulates JobRunRow +
AlarmRow history. This produces the runtime data (failure rates, retry
counts, run durations, alarms) that operational_risk.py needs — without
any access to a real AutoSys instance.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import Optional

from loguru import logger
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from autosys.db.repository import jobs as job_repo, events as event_repo
from autosys.db.schema import EventQueueRow, JobRow
from autosys.models.enums import JobStatus
from autosys.scheduler.event_processor import EventProcessor, _stub_dispatch
from autosys.scheduler.failure_injector import FailureInjector
from autosys.notifications.alarm_manager import AlarmManager


def run_simulation(
    session: Session,
    cycles: int = 20,
    ticks_per_cycle: int = 10,
    seed: int = 42,
) -> dict:
    """
    Run a multi-cycle dry-run simulation.

    For each cycle:
      1. Reset all jobs to INACTIVE
      2. Enqueue FORCE_STARTJOB on all top-level BOX jobs
      3. Run N ticks with failure injection + alarm evaluation
      4. Accumulate JobRunRow + AlarmRow history

    Returns a summary dict with counts.
    """
    injector = FailureInjector(seed=seed)
    alarm_mgr = AlarmManager()
    eps = EventProcessor(
        dispatch_fn=_stub_dispatch,
        auto_complete=True,
        alarm_manager=alarm_mgr,
        failure_injector=injector,
    )

    total_runs = 0
    total_alarms = 0
    total_failures = 0

    for cycle in range(cycles):
        # 1. Reset all jobs to INACTIVE
        session.execute(
            update(JobRow).values(
                status=JobStatus.INACTIVE.value,
                last_start=None,
                last_end=None,
            )
        )
        session.flush()

        # 2. Enqueue FORCE_STARTJOB on all top-level boxes AND top-level CMD jobs
        all_rows = job_repo.list_all(session)
        top_level = [
            r.job_name for r in all_rows
            if not r.box_name and (r.job_type or "").upper() in ("BOX", "CMD")
        ]
        for name in top_level:
            session.add(EventQueueRow(
                event_id=str(uuid.uuid4()),
                event_type="FORCE_STARTJOB",
                job_name=name,
            ))
        session.flush()

        # 3. Run ticks
        tick_now = datetime(2024, 1, 1) + timedelta(hours=cycle)
        for tick in range(ticks_per_cycle):
            eps.process_one_tick(session, now=tick_now)
            session.flush()
            tick_now += timedelta(seconds=1)

        session.commit()

        # 4. Count accumulated history
        from autosys.db.schema import JobRunRow, AlarmRow
        run_count = session.execute(
            select(JobRunRow).where(JobRunRow.run_date.isnot(None))
        ).scalars().all()
        alarm_count = session.execute(
            select(AlarmRow)
        ).scalars().all()
        total_runs = len(run_count)
        total_alarms = len(alarm_count)
        total_failures = sum(
            1 for r in run_count if r.status == JobStatus.FAILURE.value
        )

        if (cycle + 1) % 5 == 0:
            logger.info(
                "Simulation cycle %d/%d: %d runs, %d failures, %d alarms",
                cycle + 1, cycles, total_runs, total_failures, total_alarms,
            )

    return {
        "cycles": cycles,
        "total_runs": total_runs,
        "total_failures": total_failures,
        "total_alarms": total_alarms,
        "failure_rate": total_failures / total_runs if total_runs else 0.0,
    }
