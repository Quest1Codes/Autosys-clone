"""Multi-cycle simulation runner — generates runtime history from JIL alone.

Fires FORCE_STARTJOB on the top-level jobs that depend on no other local job,
lets every conditioned top-level job start when its condition is met (audit
SEM-04: all of them used to be force-started, ignoring inter-box conditions),
ticks until the cycle settles, resets jobs to INACTIVE between cycles, and
accumulates JobRunRow + AlarmRow history. This produces the runtime data (failure rates, retry
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
from autosys.scheduler.condition_evaluator import referenced_job_names
from autosys.scheduler.event_processor import EventProcessor, _stub_dispatch
from autosys.scheduler.failure_injector import FailureInjector
from autosys.notifications.alarm_manager import AlarmManager


_IN_FLIGHT = (JobStatus.STARTING.value, JobStatus.RUNNING.value, JobStatus.ACTIVATED.value)


def _busy(session: Session) -> bool:
    """Events still queued, or a job/box still in flight."""
    if session.execute(select(EventQueueRow.event_id)
                       .where(EventQueueRow.processed == False).limit(1)).first():  # noqa: E712
        return True
    return session.execute(select(JobRow.job_name)
                           .where(JobRow.status.in_(_IN_FLIGHT)).limit(1)).first() is not None


_STALL_TICKS = 3


def _status_shape(session: Session) -> tuple:
    """How many jobs are in each status, plus when the last one changed."""
    from sqlalchemy import func
    counts = tuple(sorted(session.execute(
        select(JobRow.status, func.count()).group_by(JobRow.status)).all()))
    return counts, session.execute(select(func.max(JobRow.last_end))).scalar()


_SETTLED_STATUSES = (JobStatus.SUCCESS.value, JobStatus.FAILURE.value,
                     JobStatus.TERMINATED.value, JobStatus.ON_ICE.value)


def _release_blocked(session: Session, top_level: list) -> int:
    """FORCE_STARTJOB top-level jobs still INACTIVE whose every local upstream
    exists and has finished this cycle. Returns how many were queued."""
    status = dict(session.execute(select(JobRow.job_name, JobRow.status)).all())
    queued = 0
    for r in top_level:
        refs = referenced_job_names(r.condition)
        if not refs or status.get(r.job_name) != JobStatus.INACTIVE.value:
            continue
        if all(status.get(n) in _SETTLED_STATUSES for n in refs):
            session.add(EventQueueRow(event_id=str(uuid.uuid4()),
                                      event_type="FORCE_STARTJOB", job_name=r.job_name))
            queued += 1
    session.flush()
    return queued


def run_simulation(
    session: Session,
    cycles: int = 20,
    ticks_per_cycle: int = 10,
    seed: int = 42,
    max_ticks_per_cycle: int = 2000,
) -> dict:
    """
    Run a multi-cycle dry-run simulation.

    For each cycle:
      1. Reset all jobs to INACTIVE
      2. Enqueue FORCE_STARTJOB on top-level jobs with no local dependency;
         the rest start when their condition is met
      3. Run at least *ticks_per_cycle* ticks, then keep ticking while work is
         queued or running (up to *max_ticks_per_cycle*), with failure
         injection + alarm evaluation
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

        # 2. Force-start the roots; conditioned top-level jobs wait for their
        #    upstream like they would in AutoSys.
        all_rows = job_repo.list_all(session)
        top_level = [
            r for r in all_rows
            if not r.box_name and (r.job_type or "").upper() in ("BOX", "CMD")
        ]
        roots = [r.job_name for r in top_level if not referenced_job_names(r.condition)]
        tick_now = datetime(2024, 1, 1) + timedelta(hours=cycle)
        eps.mark_waiting((r.job_name for r in top_level if referenced_job_names(r.condition)),
                         tick_now.strftime("%Y-%m-%d"))
        for name in roots:
            session.add(EventQueueRow(
                event_id=str(uuid.uuid4()),
                event_type="FORCE_STARTJOB",
                job_name=name,
            ))
        session.flush()

        # 3. Run ticks until the cycle settles. A fixed 10 ticks cut off
        #    longer dependency chains, and more than 100 queued starts.
        #    Also stop when nothing moves for a few ticks: a box can wait on a
        #    job that never runs, and would otherwise hold the cycle open.
        still, last = 0, None
        for tick in range(max_ticks_per_cycle):
            n_events = eps.process_one_tick(session, now=tick_now)
            session.flush()
            tick_now += timedelta(seconds=1)
            if tick + 1 < ticks_per_cycle:
                continue
            settled = not _busy(session)
            if not settled:
                shape = _status_shape(session)
                still = still + 1 if (n_events == 0 and shape == last) else 0
                last = shape
                settled = still >= _STALL_TICKS
            if settled:
                # Like an operator after a failure: force-start the top-level
                # jobs left blocked behind a finished upstream, so jobs
                # downstream of a failure still run (in order) and get
                # history. A job whose upstream does not exist stays unrun.
                if not _release_blocked(session, top_level):
                    break
                still, last = 0, None

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
