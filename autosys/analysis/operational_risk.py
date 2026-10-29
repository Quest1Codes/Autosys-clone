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
from datetime import datetime
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


# A job needs this much evidence before an observed failure rate is reported
# as a percentage at all. At n=3 a single failure reads 33% -- past the HIGH
# boundary -- while its 95% Wilson interval spans roughly 2%-79%, i.e. it is
# equally consistent with a job that essentially never fails. 10 is the
# smallest round n at which one failure (10%) does not by itself reach MEDIUM
# and a HIGH verdict needs >=3 independent failures. The window rule runs
# alongside it because n alone would let a job that ran 10 times inside one
# bad hour look like a trend; it also scales with cadence (a daily job needs
# 10 days, a weekly job 10 weeks).
MIN_OBSERVED_RUNS        = 10
MIN_OBSERVED_WINDOW_DAYS = 7

# Chunk size for the job_name IN clause. PostgreSQL caps a statement at 65535
# bind parameters and SQLite's default SQLITE_MAX_VARIABLE_NUMBER is far
# lower, so an estate-wide call (86K names) must not be one statement.
_IN_CHUNK = 900


@dataclass
class RunStats:
    total_runs:    int = 0
    failures:      int = 0
    terminations:  int = 0
    retried_runs:  int = 0   # runs where retry_count > 0
    active_alarms:  int = 0  # AlarmRow.cleared_at IS NULL
    cleared_alarms: int = 0
    # Coverage/provenance, populated from the same grouped query -- no extra
    # round trip. first_run/last_run are the job's own observed window.
    first_run:     Optional[datetime] = None
    last_run:      Optional[datetime] = None
    avg_duration_secs: Optional[float] = None
    # False means "no alarm source contributed to this job", which is NOT the
    # same as "zero alarms". score_operational_risk says so rather than
    # silently scoring the job as if it had a clean alarm record.
    alarms_observed: bool = False

    @property
    def observed_window_days(self) -> float:
        """Days between this job's first and last observed run (0 if unknown)."""
        if not self.first_run or not self.last_run:
            return 0.0
        return (self.last_run - self.first_run).total_seconds() / 86400.0

    def has_sufficient_sample(
        self,
        min_runs: int = MIN_OBSERVED_RUNS,
        min_window_days: int = MIN_OBSERVED_WINDOW_DAYS,
        *,
        require_window: bool = True,
    ) -> bool:
        """
        True when a failure rate from these stats is worth reporting.

        The window check only applies when *require_window* is True AND
        first_run/last_run are actually known.

        *require_window=False* is for FailureInjector-simulated stats: the
        simulation's "cycles" are a synthetic unit (scheduler/
        simulation_runner.py spaces them timedelta(hours=cycle) apart, so
        even the default 20 cycles spans under a day), not elapsed wall
        time, so gating them against a *calendar* window is meaningless and
        was found to force every simulated job back to NO_DATA -- exactly
        defeating what the simulation exists to provide. The count check
        still applies: a job the simulator only activated once or twice
        should not report a confident rate either.

        For real (observed) stats the window check is the real guard: an
        estate watched for six hours stays NO_DATA regardless of run count.
        None for first_run/last_run means "not known" (e.g. a RunStats built
        by hand, as plenty of this module's own tests do), not "the window
        is zero" -- gating on an absent fact would wrongly force every such
        caller through NO_DATA.
        """
        # A job that failed every single time it ran needs no statistical
        # backing to be meaningful, unlike a middling rate: "33% over 3
        # runs" could be one unlucky draw, but "100% over 2+ runs" has no
        # plausible noise interpretation -- zero successes ever observed.
        # Bypasses BOTH the count and window checks below for that one
        # case; a non-unanimous rate on a thin sample still needs them.
        if self.total_runs >= 2 and self.failures == self.total_runs:
            return True
        if self.total_runs < min_runs:
            return False
        if not require_window:
            return True
        if self.first_run is None or self.last_run is None:
            return True
        return self.observed_window_days >= min_window_days


def _chunked(names: list[str]):
    """Yield *names* in slices small enough for one IN clause."""
    for i in range(0, len(names), _IN_CHUNK):
        yield names[i:i + _IN_CHUNK]


def fetch_run_stats(
    session: Session,
    job_names: list[str],
    *,
    sources: Optional[tuple[str, ...]] = None,
) -> dict[str, RunStats]:
    """
    Return {job_name: RunStats} for every name in *job_names* that has at
    least one JobRunRow or AlarmRow. Names with no history are simply
    absent from the result — callers should treat a missing key as
    "no data", not "zero risk".

    *sources* filters on JobRunRow.source (see the column's docstring):
    ``("imported",)`` is real client evidence, ``("live",)`` is this
    instance's own execution history, ``("simulated",)`` is FailureInjector
    output. ``None`` (the default) applies no filter, which is what every
    pre-existing caller wants -- notably analysis/simulated_risk.py, which
    calls this against a scratch database where every row is legitimately
    synthetic and filtering would return nothing.

    Two grouped aggregate queries per chunk of job names (not N+1 per job).
    The chunking exists because an estate-wide call passes ~86K names and a
    single IN clause would exceed PostgreSQL's 65535 bind-parameter cap.
    """
    if not job_names:
        return {}

    stats: dict[str, RunStats] = {}

    for chunk in _chunked(job_names):
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
                func.min(JobRunRow.start_time).label("first_run"),
                func.max(JobRunRow.start_time).label("last_run"),
                func.avg(JobRunRow.duration_secs).label("avg_duration_secs"),
            )
            .where(JobRunRow.job_name.in_(chunk))
            .group_by(JobRunRow.job_name)
        )
        if sources is not None:
            run_stmt = run_stmt.where(JobRunRow.source.in_(sources))
        for (job_name, total_runs, failures, terminations, retried_runs,
             first_run, last_run, avg_dur) in session.execute(run_stmt):
            stats[job_name] = RunStats(
                total_runs   = total_runs or 0,
                failures     = failures or 0,
                terminations = terminations or 0,
                retried_runs = retried_runs or 0,
                first_run    = first_run,
                last_run     = last_run,
                avg_duration_secs = float(avg_dur) if avg_dur is not None else None,
            )

        alarm_stmt = (
            select(
                AlarmRow.job_name,
                func.sum(case((AlarmRow.cleared_at.is_(None), 1), else_=0)).label("active"),
                func.sum(case((AlarmRow.cleared_at.is_not(None), 1), else_=0)).label("cleared"),
            )
            .where(AlarmRow.job_name.in_(chunk))
            .group_by(AlarmRow.job_name)
        )
        for job_name, active, cleared in session.execute(alarm_stmt):
            # AlarmRow has no source/provenance column (alarm import is
            # Phase 3 -- see dev/task10-productionization/04-simulator-flaws.md
            # §5). Without this guard, a sources=("imported",) call would
            # manufacture a RunStats(total_runs=0, ...) entry for every job
            # that has ANY alarm regardless of its run provenance, and that
            # phantom entry then wins a precedence merge against a correct
            # "live"/"simulated" entry for the same job in routers/
            # assessment.py's {**live, **imported} -- silently erasing real
            # run counts. Confirmed live: check_sanctions_lists (7 real
            # "live" runs, 1 cleared alarm) read back as 0 runs until this
            # guard was added. Alarms may only enrich a job already present
            # under the requested source; sources=None (every other caller)
            # is unaffected, since every job is already eligible.
            if sources is not None and job_name not in stats:
                continue
            rec = stats.setdefault(job_name, RunStats())
            rec.active_alarms   = active or 0
            rec.cleared_alarms  = cleared or 0
            rec.alarms_observed = True

    return stats


def score_operational_risk(
    row: JobRow, stats: Optional[RunStats],
    *, min_runs: int = MIN_OBSERVED_RUNS, min_window_days: int = MIN_OBSERVED_WINDOW_DAYS,
    require_window: bool = True,
) -> tuple[str, list[str]]:
    """
    Assign a risk level to *row* given its (possibly absent) RunStats.

    Priority: HIGH -> MEDIUM -> LOW -> NONE, with NO_DATA short-circuiting
    everything when there's no history to judge from at all, or when there is
    history but not enough of it to be worth a verdict (see
    RunStats.has_sufficient_sample) -- at n=3 a single failure reads 33%,
    which is noise dressed as a finding, not a result.

    *require_window* should be False for a job whose stats are
    FailureInjector output (risk_source == "simulated") -- see
    RunStats.has_sufficient_sample's docstring for why a calendar window
    doesn't mean anything for a synthetic simulation. True (the default) is
    correct for real evidence, live or imported.
    """
    current_status = _norm_status(row.status)

    if stats is None:
        return "NO_DATA", ["no run history"]

    if not stats.has_sufficient_sample(min_runs, min_window_days, require_window=require_window):
        # No percentage anywhere in this branch, deliberately -- a number
        # that appears in a report gets quoted regardless of the caveat next
        # to it. Report the sample size instead, not a rate computed from it.
        return "NO_DATA", [
            f"{stats.total_runs} observed run(s) over "
            f"{stats.observed_window_days:.0f} day(s) — below the "
            f"{min_runs}-run / {min_window_days}-day minimum; "
            "failure rate not reported"
        ]

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

    # Alarms are one of the HIGH/MEDIUM signals above, so a job scored
    # without any alarm source contributing (stats.alarms_observed is False
    # only when NEITHER the live scheduler NOR an import supplied alarm rows)
    # is systematically biased toward a lower verdict than one with alarm
    # data. Say so rather than let the gap read as "checked, found clean".
    # Does not change high/medium/low itself -- see
    # dev/task10-productionization/04-simulator-flaws.md F0/§5.
    missing_note = [] if stats.alarms_observed else ["alarm history not available"]

    if high:
        return "HIGH", high + missing_note
    if medium:
        return "MEDIUM", medium + missing_note

    if stats.cleared_alarms > 0 or stats.retried_runs > 0:
        drivers.append(f"{stats.cleared_alarms} cleared alarm(s), {stats.retried_runs} past retried run(s)")
        return "LOW", drivers + missing_note

    return "NONE", ["clean run history, no alarms"] + missing_note
