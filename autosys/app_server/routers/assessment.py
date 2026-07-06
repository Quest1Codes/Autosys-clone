"""
Assessment router — Phase 1 migration-assessment export for Shinro.

GET  /api/v1/assessment/report               Full T-shirt-size complexity report (JSON).
POST /api/v1/assessment/boxes/{box}/trace     Dry-run state-machine trace for one BOX.

Both endpoints are read-only: the trace endpoint drives a box through the
same dry-run stub-dispatch machinery as `autosys scheduler serve --dry-run`,
then rolls back the session so nothing is persisted.
"""
from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from autosys.analysis.box_trace import (
    BoxNotFoundError, NotABoxError, run_box_trace,
)
from autosys.analysis.complexity import build_report, compute_summary
from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import (
    AssessmentJobRecord, AssessmentReportResponse, AssessmentSummaryResponse,
    BoxTraceJobEntry, BoxTraceRequest, BoxTraceResponse, BoxTraceTransitionEntry,
)
from autosys.db.repository import jobs as job_repo

router = APIRouter(prefix="/api/v1/assessment", tags=["assessment"])


@router.get("/report", response_model=AssessmentReportResponse)
def get_report(
    box:     str | None    = Query(None, description="SQL LIKE pattern to restrict to matching BOX jobs and their children, e.g. '%risk%'."),
    session: Session        = Depends(get_session),
    _user:   CurrentUser    = Depends(get_current_user),
):
    """T-shirt-size every job (optionally filtered to a BOX pattern) and estimate effort."""
    rows    = job_repo.list_all(session)
    results = build_report(rows, box)
    summary = compute_summary(results)

    return AssessmentReportResponse(
        generated_at = datetime.now(),
        box_filter   = box,
        job_count    = len(results),
        jobs         = [
            AssessmentJobRecord(
                job_name=r.job_name, job_type=r.job_type, box_name=r.box_name,
                size=r.size, effort_h=r.effort_h, drivers=r.drivers,
            )
            for r in results
        ],
        summary = AssessmentSummaryResponse(
            counts=summary.counts, hours=summary.hours, total_jobs=summary.total_jobs,
            raw_hours=summary.raw_hours, platform_h=summary.platform_h,
            testing_h=summary.testing_h, pm_h=summary.pm_h, training_h=summary.training_h,
            total_h=summary.total_h, total_days=summary.total_days,
        ),
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
