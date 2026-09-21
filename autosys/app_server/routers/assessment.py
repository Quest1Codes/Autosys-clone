"""
Assessment router — Phase 1 migration-assessment export for Shinro.

GET  /api/v1/assessment/report               Full complexity report (JSON): T-shirt size, effort,
                                             operational risk, gap tags, and the A1-A10 migration
                                             signals. Risk for jobs with no run history is filled
                                             in by a background dry-run simulation (see below).
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
from autosys.analysis.complexity import (
    AssessmentSummary, JobAssessment, astronomer_mapping, box_effort_breakdown,
    build_report, compute_summary, export_csv, risk_mitigation,
)
from autosys.analysis.migration_signals import run_all_structural_analyses
from autosys.analysis.operational_risk import fetch_run_stats
from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import (
    AssessmentJobRecord, AssessmentReportResponse, AssessmentSimulation,
    AssessmentSummaryResponse,
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
    rows      = job_repo.list_all(session)
    live      = fetch_run_stats(session, [r.job_name for r in rows])
    dry_run   = getattr(request.app.state, "dry_run", True)
    mode      = "dry_run" if dry_run else "real"
    # BOX jobs never have run rows of their own (they inherit risk from their
    # children), so only non-BOX jobs count as "missing history".
    uncovered = [
        r.job_name for r in rows
        if r.job_name not in live and (r.job_type or "").upper() != "BOX"
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

    # Live history wins per job; simulated stats only fill jobs that have none.
    merged  = {**sim_stats, **live}
    sources = {name: "history" for name in live}
    sources.update({name: "simulated" for name in sim_stats if name not in live})
    sim.jobs_simulated = sum(1 for v in sources.values() if v == "simulated")

    signals = run_all_structural_analyses(session) if rows else {}
    results = build_report(
        rows, box, run_stats=merged, migration_signals=signals, risk_sources=sources,
    )
    return _Assessment(results, compute_summary(results), signals, sim, mode)


def _job_record(a: JobAssessment) -> AssessmentJobRecord:
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


@router.get("/report", response_model=AssessmentReportResponse)
def get_report(
    request:  Request,
    box:      str | None = Query(None, description="SQL LIKE pattern to restrict to matching BOX jobs and their children, e.g. '%risk%'."),
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
    """
    a = _assess(
        session, request, box=box, cycles=simulated_risk.DEFAULT_CYCLES,
        simulate=simulate, wait_s=simulated_risk.default_wait_s(),
    )
    return AssessmentReportResponse(
        generated_at   = datetime.now(),
        box_filter     = box,
        job_count      = len(a.results),
        execution_mode = a.mode,
        simulation     = a.simulation,
        jobs           = [_job_record(r) for r in a.results],
        box_breakdown  = box_effort_breakdown(a.results),
        summary        = _summary_response(a.summary),
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
    return {
        "generated_at": datetime.now().isoformat(),
        "job_count": len(a.results),
        "simulation": a.simulation.model_dump(),
        "jobs": [_job_record(r).model_dump() for r in a.results],
        "box_breakdown": box_effort_breakdown(a.results),
        "summary": _summary_response(a.summary).model_dump(),
        "csv": export_csv(a.results),
    }
