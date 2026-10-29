"""
Assessment router — Phase 1 migration-assessment export for Shinro.

GET  /api/v1/assessment/report               Full complexity report (JSON): T-shirt size, effort,
                                             operational risk, gap tags, the A1-A10 migration
                                             signals, and an Indicative Otto Token Budget. Risk
                                             for jobs with no run history is filled in by a
                                             background dry-run simulation (see below).
GET  /api/v1/assessment/migration-report     Same data plus a CSV export; always simulates and
                                             blocks until the simulation finishes.
POST /api/v1/assessment/boxes/{box}/trace     Dry-run state-machine trace for one BOX.

The trace endpoint drives a box through the same dry-run stub-dispatch machinery as
`autosys scheduler serve --dry-run`, then rolls back the session so nothing is persisted.

Automatic simulation
--------------------
A freshly imported JIL has no run history, so every job would score NO_DATA risk. When the
server is in dry-run mode, jobs without history get simulated history from
`analysis.simulated_risk`, which runs the dry-run machinery against a throwaway in-memory DB —
the live DB (job statuses, event queue, history) is never touched. Jobs that already have
history keep it. Each job's `risk_source` says which one it got, and `simulation.status`
reports whether the simulation is ready, still pending, failed or skipped. In real execution
mode nothing is simulated.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy.orm import Session

from autosys.analysis.box_trace import (
    BoxNotFoundError, NotABoxError, run_box_trace,
)
from autosys.analysis import simulated_risk
from autosys.analysis.ai_token_estimate import AITokenEstimate, compute_token_budget
from autosys.analysis.complexity import (
    AssessmentSummary, JobAssessment, astronomer_mapping, box_effort_breakdown,
    build_report, compute_summary, export_csv, risk_mitigation,
)
from autosys.analysis.migration_signals import run_all_structural_analyses
from autosys.analysis.operational_risk import (
    MIN_OBSERVED_RUNS, MIN_OBSERVED_WINDOW_DAYS, fetch_run_stats,
)
from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import (
    AITokenBudgetResponse,
    AssessmentCoverage, AssessmentJobRecord, AssessmentReportResponse,
    AssessmentSimulation, AssessmentSummaryResponse,
    BoxTraceJobEntry, BoxTraceRequest, BoxTraceResponse, BoxTraceTransitionEntry,
)
from autosys.db.repository import jobs as job_repo

router = APIRouter(prefix="/api/v1/assessment", tags=["assessment"])


@dataclass
class _Assessment:
    results:    list[JobAssessment]
    summary:    AssessmentSummary
    signals:    dict
    simulation: AssessmentSimulation
    coverage:   AssessmentCoverage
    mode:       str


def _assess(
    session:  Session,
    request:  Request,
    *,
    box:      str | None,
    cycles:   int,
    simulate: bool,
    wait_s:   float | None,
    force:    bool = False,
) -> _Assessment:
    """
    Score every job, filling in simulated risk for jobs that have no run history.

    *force* simulates even in real execution mode (safe because the simulation is
    isolated); *wait_s* bounds how long to wait for it (None = until it finishes).
    """
    rows       = job_repo.list_all(session)
    job_names  = [r.job_name for r in rows]

    # Real evidence only. Never includes "simulated" -- this is the fix for
    # F0 (dev/task10-productionization/04-simulator-flaws.md): before the
    # JobRunRow.source column existed, the live dry-run event processor wrote
    # FailureInjector output into this same table with no provenance tag, and
    # this function read every row back as observed fact. "imported" is a
    # client's real run-history export (see parser/run_history_ingest.py
    # once it exists); it wins over "live" per job because it is the
    # client's own estate, not this instance's incidental execution history.
    imported = fetch_run_stats(session, job_names, sources=("imported",))
    live     = fetch_run_stats(session, job_names, sources=("live",))
    observed = {**live, **imported}

    dry_run   = getattr(request.app.state, "dry_run", True)
    mode      = "dry_run" if dry_run else "real"
    # BOX jobs never have run rows of their own (they inherit risk from their
    # children). A job with real evidence -- even too little of it to score,
    # see MIN_OBSERVED_RUNS -- is never simulated: replacing a handful of real
    # observations with FailureInjector fiction would be worse than reporting
    # NO_DATA, and would make risk_source flip from observed to simulated as
    # a client sends *more* data, which is backwards.
    uncovered = [
        name for name in job_names
        if name not in observed
        and (next(r for r in rows if r.job_name == name).job_type or "").upper() != "BOX"
    ]

    sim_stats: dict = {}
    if not rows or not uncovered:
        sim = AssessmentSimulation(status="not_needed")
    elif not simulate or not simulated_risk.simulation_enabled():
        sim = AssessmentSimulation(status="skipped", reason="simulation disabled")
    elif not dry_run and not force:
        sim = AssessmentSimulation(
            status="skipped", reason="real execution mode — simulated risk is never mixed into real history",
        )
    else:
        out = simulated_risk.ensure_simulation(session, cycles=cycles, wait_s=wait_s)
        sim_stats = out.stats
        sim = AssessmentSimulation(
            status=out.status, error=out.error, cycles=out.cycles, seed=out.seed,
            total_runs=out.summary.get("total_runs", 0),
            total_failures=out.summary.get("total_failures", 0),
            total_alarms=out.summary.get("total_alarms", 0),
            failure_rate=round(out.summary.get("failure_rate", 0.0), 4),
        )

    # Observed wins per job over simulated. Within "observed", score_
    # operational_risk itself decides sufficient vs insufficient sample
    # (RunStats.has_sufficient_sample) -- the split here is only for the
    # risk_source label and the coverage counts, not for which stats dict a
    # job's score is computed from.
    merged  = {**sim_stats, **observed}
    sources: dict[str, str] = {}
    for name, stats in observed.items():
        sources[name] = "observed" if stats.has_sufficient_sample() else "observed_insufficient"
    for name in sim_stats:
        sources.setdefault(name, "simulated")
    sim.jobs_simulated = sum(1 for v in sources.values() if v == "simulated")

    non_box_total = sum(1 for r in rows if (r.job_type or "").upper() != "BOX")
    jobs_observed = sum(1 for v in sources.values() if v == "observed")
    coverage = AssessmentCoverage(
        jobs_total                 = len(rows),
        jobs_observed              = jobs_observed,
        jobs_observed_insufficient = sum(1 for v in sources.values() if v == "observed_insufficient"),
        jobs_simulated             = sim.jobs_simulated,
        jobs_no_history            = sum(
            1 for r in rows
            if (r.job_type or "").upper() != "BOX" and r.job_name not in sources
        ),
        coverage_pct               = round(jobs_observed / non_box_total, 4) if non_box_total else 0.0,
        min_runs_threshold         = MIN_OBSERVED_RUNS,
        min_window_days_threshold  = MIN_OBSERVED_WINDOW_DAYS,
        alarms_observed            = any(st.alarms_observed for st in merged.values()),
    )

    signals = run_all_structural_analyses(session) if rows else {}
    results = build_report(
        rows, box, run_stats=merged, migration_signals=signals, risk_sources=sources,
    )
    return _Assessment(results, compute_summary(results), signals, sim, coverage, mode)


def _job_record(
    a: JobAssessment, ai_estimate: AITokenEstimate | None = None
) -> AssessmentJobRecord:
    return AssessmentJobRecord(
        job_name=a.job_name, job_type=a.job_type, box_name=a.box_name,
        size=a.size, effort_h=a.effort_h, drivers=a.drivers,
        risk=a.risk, risk_drivers=a.risk_drivers, risk_source=a.risk_source,
        blast_radius=a.blast_radius, gap_tags=a.gap_tags,
        machine_concentration=a.machine_concentration, command_dialect=a.command_dialect,
        box_nesting_depth=a.box_nesting_depth, has_cross_box_dep=a.has_cross_box_dep,
        schedule_burst_count=a.schedule_burst_count, has_notifications=a.has_notifications,
        has_hardcoded_logs=a.has_hardcoded_logs, timezone=a.timezone,
        astronomer_mapping=astronomer_mapping(a), risk_mitigation=risk_mitigation(a),
        # Indicative Otto Token Budget (pre-pilot ROM, see ai_token_estimate.py)
        # -- additive, LOW-confidence planning fields, not a usage or cost quote.
        # Absent (ai_estimate=None) only if the caller didn't compute a budget
        # for this result set; fields then keep their empty/zero defaults.
        ai_route=ai_estimate.route if ai_estimate else "",
        ai_pattern_key=ai_estimate.pattern_key if ai_estimate else "",
        ai_estimate_role=ai_estimate.role if ai_estimate else "",
        estimated_otto_tokens_min=ai_estimate.min_tokens if ai_estimate else 0,
        estimated_otto_tokens_max=ai_estimate.max_tokens if ai_estimate else 0,
        ai_estimate_confidence=ai_estimate.confidence if ai_estimate else "",
    )


def _summary_response(s: AssessmentSummary) -> AssessmentSummaryResponse:
    return AssessmentSummaryResponse(
        counts=s.counts, hours=s.hours, total_jobs=s.total_jobs,
        raw_hours=s.raw_hours, platform_h=s.platform_h,
        testing_h=s.testing_h, pm_h=s.pm_h, training_h=s.training_h,
        total_h=s.total_h, total_days=s.total_days,
        risk_counts=s.risk_counts, gap_severity_counts=s.gap_severity_counts,
        machine_count=s.machine_count,
        migration_signals={
            "cross_box_dep_count":     s.cross_box_dep_count,
            "max_box_nesting":         s.max_box_nesting,
            "max_schedule_burst":      s.max_schedule_burst,
            "notification_job_count":  s.notification_job_count,
            "hardcoded_log_job_count": s.hardcoded_log_job_count,
            "timezone_count":          s.timezone_count,
            "dialect_counts":          s.dialect_counts,
        },
    )


def _ai_token_budget_response(ai_budget) -> AITokenBudgetResponse:
    return AITokenBudgetResponse(
        min_tokens=ai_budget.min_tokens, max_tokens=ai_budget.max_tokens,
        confidence=ai_budget.confidence, calibration_status=ai_budget.calibration_status,
        pattern_count=ai_budget.pattern_count,
        architecture_pattern_count=ai_budget.architecture_pattern_count,
        by_route=ai_budget.by_route, by_tier=ai_budget.by_tier,
        disclaimer=ai_budget.disclaimer, pricing_note=ai_budget.pricing_note,
    )


@router.get("/report", response_model=AssessmentReportResponse)
def get_report(
    request:  Request,
    box:      str | None = Query(None, max_length=255, description="SQL LIKE pattern to restrict to matching BOX jobs and their children, e.g. '%risk%'."),
    simulate: bool       = Query(True, description="Fill in risk for jobs with no run history from a dry-run simulation (dry-run mode only)."),
    session:  Session     = Depends(get_session),
    _user:    CurrentUser = Depends(get_current_user),
):
    """
    T-shirt-size every job (optionally filtered to a BOX pattern), estimate effort, and
    score operational risk.

    Jobs with no run history get simulated risk (``risk_source: "simulated"``) — the
    simulation runs in the background on a scratch DB and is cached, so the first call
    after importing JIL may report ``simulation.status: "pending"``; call again shortly.
    A job with real run history (this instance's own execution, or an imported client
    export) is never simulated; see ``risk_source`` on AssessmentJobRecord and the
    ``coverage`` block for how much of the estate each category covers.
    """
    a = _assess(
        session, request, box=box, cycles=simulated_risk.DEFAULT_CYCLES,
        simulate=simulate, wait_s=simulated_risk.default_wait_s(),
    )
    ai_budget = compute_token_budget(a.results)

    return AssessmentReportResponse(
        generated_at   = datetime.now(),
        box_filter     = box,
        job_count      = len(a.results),
        execution_mode = a.mode,
        simulation     = a.simulation,
        coverage       = a.coverage,
        jobs           = [_job_record(r, ai_budget.by_job.get(r.job_name)) for r in a.results],
        box_breakdown  = box_effort_breakdown(a.results),
        summary        = _summary_response(a.summary),
        ai_token_budget = _ai_token_budget_response(ai_budget),
    )


@router.post("/boxes/{box_name}/trace", response_model=BoxTraceResponse)
def trace_box(
    box_name: str,
    body:     BoxTraceRequest = BoxTraceRequest(),
    session:  Session         = Depends(get_session),
    _user:    CurrentUser     = Depends(get_current_user),
):
    """
    Reset BOX_NAME (and descendants) and run it under the dry-run stub
    dispatcher, returning the resulting activation trace. Non-mutating —
    the session is rolled back before this returns.
    """
    try:
        trace = run_box_trace(
            session, box_name,
            max_ticks         = body.max_ticks,
            ignore_run_window = body.ignore_run_window,
            ignore_calendar   = body.ignore_calendar,
        )
    except BoxNotFoundError:
        session.rollback()
        raise HTTPException(status_code=404, detail=f"Box '{box_name}' not found")
    except NotABoxError:
        session.rollback()
        raise HTTPException(status_code=400, detail=f"'{box_name}' is not a BOX job")

    session.rollback()

    return BoxTraceResponse(
        box_name     = trace.box_name,
        triggered_at = trace.triggered_at,
        completed_at = trace.completed_at,
        outcome      = trace.outcome,
        tick_count   = trace.tick_count,
        wave_count   = trace.wave_count,
        jobs         = [
            BoxTraceJobEntry(
                job_name=j.job_name, job_type=j.job_type, condition=j.condition,
                wave=j.wave, activated_tick=j.activated_tick, final_status=j.final_status,
            )
            for j in trace.jobs
        ],
        transitions  = [
            BoxTraceTransitionEntry(tick=t.tick, job_name=t.job_name, old=t.old, new=t.new, ts=t.ts)
            for t in trace.transitions
        ],
    )


@router.get("/migration-report")
def get_migration_report(
    request: Request,
    cycles:  int = Query(simulated_risk.DEFAULT_CYCLES, ge=1, le=200, description="Number of simulation cycles for runtime data generation."),
    session: Session = Depends(get_session),
    _user:   CurrentUser = Depends(get_current_user),
):
    """
    Full migration report: the same data as ``/report`` plus a CSV export.

    Always simulates (even in real execution mode — the simulation is isolated) and
    blocks until it finishes, so unlike ``/report`` it never returns "pending".
    """
    if not job_repo.list_all(session):
        raise HTTPException(status_code=404, detail="No jobs found. Import JIL files first.")

    a = _assess(
        session, request, box=None, cycles=cycles, simulate=True, wait_s=None, force=True,
    )
    ai_budget = compute_token_budget(a.results)
    return {
        "generated_at": datetime.now().isoformat(),
        "job_count": len(a.results),
        "simulation": a.simulation.model_dump(),
        "coverage": a.coverage.model_dump(),
        "jobs": [_job_record(r, ai_budget.by_job.get(r.job_name)).model_dump() for r in a.results],
        "box_breakdown": box_effort_breakdown(a.results),
        "summary": _summary_response(a.summary).model_dump(),
        "ai_token_budget": _ai_token_budget_response(ai_budget).model_dump(),
        "csv": export_csv(a.results),
    }
