"""
Estate workload profile from real run history -- the timing facts a target
platform is sized from.

Sizing an Airflow platform needs how often tasks run, how long they run and
how many overlap at the busiest moment; job definitions alone cannot say.
This module measures those from JobRunRow, using REAL evidence only:
"imported" rows (the client's own run-history export) when there are any,
otherwise "live" rows (this instance's own execution). "simulated" rows are
never used -- the FailureInjector spaces its cycles in synthetic time (see
operational_risk.RunStats.has_sufficient_sample), so their timing would be
fiction presented as measurement.

Every figure is computed in SQL with portable SQLAlchemy constructs (no raw
SQL; see tests/test_postgres_compat.py), so it scales to an 86K-job estate
without loading run rows into Python.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Optional

from sqlalchemy import extract, func, literal, select, union_all
from sqlalchemy.orm import Session

from autosys.analysis.operational_risk import MIN_OBSERVED_WINDOW_DAYS
from autosys.db.schema import JobRunRow

# Real-evidence sources in order of preference: a client's export describes
# their estate; this instance's own runs only describe what was replayed here.
REAL_SOURCES = ("imported", "live")


@dataclass
class WorkloadProfile:
    """Workload facts for the jobs an assessment covers.

    ``source`` is None when no real run history exists; every measured field
    is then None and ``reason`` says why. ``peak_hour`` is the clock hour
    (0-23, in the timezone run times are stored in -- UTC for imported
    history) in which the largest share of runs start.
    """
    source:                Optional[str] = None
    reason:                str = ""
    jobs_with_history:     int = 0
    total_runs:            int = 0
    days_observed:         int = 0
    first_run:             Optional[datetime] = None
    last_run:              Optional[datetime] = None
    runs_per_job_per_day:  Optional[float] = None
    avg_task_minutes:      Optional[float] = None
    peak_hour:             Optional[int] = None
    peak_hour_share:       Optional[float] = None
    peak_concurrent_runs:  Optional[int] = None
    peak_concurrency_at:   Optional[datetime] = None
    sufficient_window:     bool = False

    def as_dict(self) -> dict:
        return asdict(self)


def _scoped(stmt, job_names: Optional[list[str]], source: str):
    stmt = stmt.where(JobRunRow.source == source)
    if job_names is not None:
        stmt = stmt.where(JobRunRow.job_name.in_(job_names))
    return stmt


def compute_workload_profile(
    session: Session, job_names: Optional[list[str]] = None,
    sources: tuple[str, ...] = REAL_SOURCES,
) -> WorkloadProfile:
    """Measure the workload of *job_names* (all jobs when None).

    *sources* narrows the evidence further: a dry-run instance passes only
    ("imported",), because its own "live" runs are stub dispatches whose
    durations say nothing about the client's estate.

    Callers pass the task-like jobs only (not boxes or file watchers) when the
    figures are meant for worker sizing; see routers/assessment.py.

    Uses the first of *sources* that has any rows for those jobs.
    ``runs_per_job_per_day`` is averaged over the jobs that have history and
    the distinct calendar days on which any of them ran, so a 7-day export
    of daily jobs reads 1.0, not 7/6 as a first-to-last span would give.
    """
    if job_names is not None and not job_names:
        return WorkloadProfile(reason="no executable jobs in scope")

    for source in (src for src in REAL_SOURCES if src in sources):
        totals = session.execute(_scoped(select(
            func.count(),
            func.count(func.distinct(JobRunRow.job_name)),
            func.count(func.distinct(JobRunRow.run_date)),
            func.min(JobRunRow.start_time),
            func.max(JobRunRow.start_time),
            func.avg(JobRunRow.duration_secs),
        ), job_names, source)).one()
        if totals[0]:
            break
    else:
        return WorkloadProfile(
            reason="no real run history: import the client's AutoSys run-history export to "
                   "measure run frequency, durations and peak concurrency (simulated history "
                   "has no real timing)")

    total_runs, jobs, days, first, last, avg_secs = totals
    profile = WorkloadProfile(
        source=source, jobs_with_history=jobs, total_runs=total_runs, days_observed=days,
        first_run=first, last_run=last,
        runs_per_job_per_day=round(total_runs / jobs / days, 4) if jobs and days else None,
        avg_task_minutes=round(float(avg_secs) / 60, 4) if avg_secs is not None else None,
        sufficient_window=days >= MIN_OBSERVED_WINDOW_DAYS,
    )

    # Busiest clock hour across the whole window: the share of all runs that
    # start in it is the batch-window concentration a sizing model needs.
    hour = extract("hour", JobRunRow.start_time).label("hour")
    by_hour = session.execute(_scoped(
        select(hour, func.count().label("runs")).where(JobRunRow.start_time.is_not(None)),
        job_names, source).group_by(hour).order_by(func.count().desc(), hour)).first()
    if by_hour:
        profile.peak_hour = int(by_hour.hour)
        profile.peak_hour_share = round(by_hour.runs / total_runs, 4)

    # Peak concurrency, measured rather than estimated: +1 at every start, -1
    # at every end, running sum in time order; its maximum is the most runs
    # ever in flight at once. Ends sort before starts at the same instant so
    # back-to-back runs are not counted as overlapping. Runs with no end time
    # (still running or never closed) are left out rather than guessed.
    finished = JobRunRow.start_time.is_not(None) & JobRunRow.end_time.is_not(None)
    events = union_all(
        _scoped(select(JobRunRow.start_time.label("at"), literal(1).label("d"))
                .where(finished), job_names, source),
        _scoped(select(JobRunRow.end_time.label("at"), literal(-1).label("d"))
                .where(finished), job_names, source),
    ).subquery()
    running = select(
        events.c.at,
        func.sum(events.c.d).over(order_by=(events.c.at, events.c.d), rows=(None, 0))
        .label("in_flight"),
    ).subquery()
    peak = session.execute(
        select(running.c.in_flight, running.c.at)
        .order_by(running.c.in_flight.desc(), running.c.at).limit(1)).first()
    if peak:
        profile.peak_concurrent_runs = int(peak.in_flight)
        profile.peak_concurrency_at = peak.at
    return profile
