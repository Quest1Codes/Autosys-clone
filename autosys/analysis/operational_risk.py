"""
Operational-risk scoring — Phase 1 migration assessment.

Complements complexity.py's purely structural T-shirt sizing with a second,
orthogonal axis: how much operational trouble has this job *actually*
caused, based on its live FSM state (JobRow.status, see
scheduler/state_machine.py) and its run/alarm history (JobRunRow, AlarmRow).

Deliberately kept out of the effort estimate: a job's JIL can be trivial
(XS) while its legacy-scheduler track record is terrible (HIGH risk), or
vice versa. Migration engineering effort (size/effort_h) and operational
risk answer different questions and must not be blended — see
docs/strategy-and-approach.md's T-shirt sizing model, which this
deliberately does not perturb.

This is the only autosys.analysis module that touches the database — the
DB aggregation (fetch_run_stats) is isolated from the pure scoring function
(score_operational_risk) so the scoring logic itself stays unit-testable
with plain objects, mirroring complexity.py's testability.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from autosys.db.schema import AlarmRow, JobRow, JobRunRow
from autosys.models.enums import JobStatus
from autosys.scheduler.state_machine import _norm_status

RISK_LEVELS = ("NO_DATA", "NONE", "LOW", "MEDIUM", "HIGH")

# Live FSM states that themselves indicate operational trouble, independent
# of run-history stats — a job parked on hold/ice/suspended or mid-retry
# loop at analysis time is a signal on its own.
AT_RISK_STATUSES: frozenset[str] = frozenset({"ON_HOLD", "SUSPENDED"})
ELEVATED_STATUSES: frozenset[str] = frozenset({"ON_ICE", "RESTART"})

# Thresholds — tunable without touching the scoring logic below.
HIGH_FAILURE_RATE   = 0.30
MEDIUM_FAILURE_RATE = 0.10
MEDIUM_RETRY_RATE   = 0.20
HIGH_ACTIVE_ALARMS  = 2
MEDIUM_ACTIVE_ALARMS = 1


@dataclass
class RunStats:
    total_runs:    int = 0
    failures:      int = 0
    terminations:  int = 0
    retried_runs:  int = 0   # runs where retry_count > 0
    active_alarms:  int = 0  # AlarmRow.cleared_at IS NULL
    cleared_alarms: int = 0


def fetch_run_stats(session: Session, job_names: list[str]) -> dict[str, RunStats]:
    """
    Return {job_name: RunStats} for every name in *job_names* that has at
    least one JobRunRow or AlarmRow. Names with no history are simply
    absent from the result — callers should treat a missing key as
    "no data", not "zero risk".

    Two grouped aggregate queries total (not N+1 per job).
    """
    if not job_names:
        return {}

    stats: dict[str, RunStats] = {}

    run_stmt = (
        select(
            JobRunRow.job_name,
            func.count().label("total_runs"),
            func.sum(
                case((JobRunRow.status == JobStatus.FAILURE.value, 1), else_=0)
            ).label("failures"),
            func.sum(
                case((JobRunRow.status == JobStatus.TERMINATED.value, 1), else_=0)
            ).label("terminations"),
            func.sum(
                case((JobRunRow.retry_count > 0, 1), else_=0)
            ).label("retried_runs"),
        )
        .where(JobRunRow.job_name.in_(job_names))
        .group_by(JobRunRow.job_name)
    )
    for job_name, total_runs, failures, terminations, retried_runs in session.execute(run_stmt):
        stats[job_name] = RunStats(
            total_runs   = total_runs or 0,
            failures     = failures or 0,
            terminations = terminations or 0,
            retried_runs = retried_runs or 0,
        )

    alarm_stmt = (
        select(
            AlarmRow.job_name,
            func.sum(case((AlarmRow.cleared_at.is_(None), 1), else_=0)).label("active"),
            func.sum(case((AlarmRow.cleared_at.is_not(None), 1), else_=0)).label("cleared"),
        )
        .where(AlarmRow.job_name.in_(job_names))
        .group_by(AlarmRow.job_name)
    )
    for job_name, active, cleared in session.execute(alarm_stmt):
        rec = stats.setdefault(job_name, RunStats())
        rec.active_alarms  = active or 0
        rec.cleared_alarms = cleared or 0

    return stats


def score_operational_risk(
    row: JobRow, stats: Optional[RunStats]
) -> tuple[str, list[str]]:
    """
    Assign a risk level to *row* given its (possibly absent) RunStats.

    Priority: HIGH -> MEDIUM -> LOW -> NONE, with NO_DATA short-circuiting
    everything when there's no history to judge from at all.
    """
    current_status = _norm_status(row.status)

    if stats is None:
        return "NO_DATA", ["no run history"]

    drivers: list[str] = []
    high:   list[str] = []
    medium: list[str] = []

    failure_rate = stats.failures / stats.total_runs if stats.total_runs else 0.0
    retry_rate   = stats.retried_runs / stats.total_runs if stats.total_runs else 0.0

    if failure_rate >= HIGH_FAILURE_RATE:
        high.append(f"failure rate {failure_rate:.0%} over {stats.total_runs} runs")
    if stats.active_alarms >= HIGH_ACTIVE_ALARMS:
        high.append(f"{stats.active_alarms} active alarms")
    if stats.terminations >= 1:
        high.append(f"{stats.terminations} manual termination(s)")
    if current_status in AT_RISK_STATUSES:
        high.append(f"currently {current_status}")

    if failure_rate >= MEDIUM_FAILURE_RATE:
        medium.append(f"failure rate {failure_rate:.0%} over {stats.total_runs} runs")
    if retry_rate >= MEDIUM_RETRY_RATE:
        medium.append(f"retry rate {retry_rate:.0%} over {stats.total_runs} runs")
    if stats.active_alarms == MEDIUM_ACTIVE_ALARMS:
        medium.append(f"{stats.active_alarms} active alarm")
    if current_status in ELEVATED_STATUSES:
        medium.append(f"currently {current_status}")

    if high:
        return "HIGH", high
    if medium:
        return "MEDIUM", medium

    if stats.cleared_alarms > 0 or stats.retried_runs > 0:
        drivers.append(f"{stats.cleared_alarms} cleared alarm(s), {stats.retried_runs} past retried run(s)")
        return "LOW", drivers

    return "NONE", ["clean run history, no alarms"]
