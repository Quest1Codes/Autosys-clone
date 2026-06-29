"""
Jobs router — GET/DELETE jobs, POST sendevent.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import (
    JobResponse, JobDetailResponse,
    SendEventRequest, SendEventResponse,
)
from autosys.db.repository import jobs as job_repo, events as event_repo
from autosys.models.event  import Event

router = APIRouter(prefix="/api/v1/jobs", tags=["jobs"])


def _row_to_response(row) -> JobResponse:
    return JobResponse(
        job_name      = row.job_name,
        job_type      = row.job_type or "CMD",
        status        = row.status   or "INACTIVE",
        machine       = row.machine,
        box_name      = row.box_name,
        condition     = row.condition,
        owner         = row.owner,
        command       = row.command,
        alarm_if_fail = bool(row.alarm_if_fail),
        last_start    = row.last_start,
        last_end      = row.last_end,
        last_run_date = row.last_run_date,
    )


def _row_to_detail(row) -> JobDetailResponse:
    import json as _json
    def _load_list(v):
        if not v:
            return []
        try:
            return _json.loads(v)
        except Exception:
            return [x.strip() for x in v.split(",") if x.strip()]

    return JobDetailResponse(
        job_name           = row.job_name,
        job_type           = row.job_type or "CMD",
        status             = row.status   or "INACTIVE",
        machine            = row.machine,
        box_name           = row.box_name,
        condition          = row.condition,
        owner              = row.owner,
        command            = row.command,
        alarm_if_fail      = bool(row.alarm_if_fail),
        last_start         = row.last_start,
        last_end           = row.last_end,
        last_run_date      = row.last_run_date,
        start_times        = _load_list(row.start_times),
        days_of_week       = _load_list(row.days_of_week),
        run_calendar       = row.run_calendar,
        exclude_calendar   = row.exclude_calendar,
        n_retrys           = row.n_retrys or 0,
        max_run_alarm      = row.max_run_alarm or 0,
        min_run_alarm      = row.min_run_alarm or 0,
        term_run_time      = row.term_run_time or 0,
        alarm_if_terminated= bool(row.alarm_if_terminated),
        description        = row.description,
        std_out_file       = row.std_out_file,
        std_err_file       = row.std_err_file,
    )


@router.get("", response_model=list[JobResponse])
def list_jobs(
    status:  Optional[str] = Query(None, description="Filter by status e.g. RUNNING"),
    box:     Optional[str] = Query(None, description="Filter to children of this BOX"),
    pattern: Optional[str] = Query(None, description="Glob pattern e.g. etl_%"),
    session: Session       = Depends(get_session),
    _user:   CurrentUser   = Depends(get_current_user),
):
    """List all jobs, with optional filters."""
    if pattern:
        rows = job_repo.search(session, pattern)
    elif box:
        rows = job_repo.get_children(session, box)
    else:
        rows = job_repo.list_all(session)

    if status:
        rows = [r for r in rows if (r.status or "INACTIVE") == status.upper()]

    return [_row_to_response(r) for r in rows]


@router.get("/{job_name}", response_model=JobDetailResponse)
def get_job(
    job_name: str,
    session:  Session     = Depends(get_session),
    _user:    CurrentUser = Depends(get_current_user),
):
    """Get full detail for a single job."""
    row = job_repo.get_row(session, job_name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_name}' not found")
    return _row_to_detail(row)


@router.delete("/{job_name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job(
    job_name: str,
    session:  Session     = Depends(get_session),
    user:     CurrentUser = Depends(get_current_user),
):
    """Delete a job definition."""
    user.require_role("admin")
    row = job_repo.get_row(session, job_name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Job '{job_name}' not found")
    job_repo.delete(session, job_name)


@router.post("/{job_name}/sendevent", response_model=SendEventResponse,
             status_code=status.HTTP_202_ACCEPTED)
def sendevent(
    job_name: str,
    body:     SendEventRequest,
    session:  Session     = Depends(get_session),
    user:     CurrentUser = Depends(get_current_user),
):
    """
    Enqueue an event for a job.

    Examples: STARTJOB, KILLJOB, FORCE_STARTJOB, JOB_ON_HOLD, JOB_OFF_HOLD,
              JOB_ON_ICE, JOB_OFF_ICE, CHANGE_STATUS, SET_GLOBAL
    """
    user.require_role("operator", "admin")

    allowed = {
        "STARTJOB", "FORCE_STARTJOB", "KILLJOB",
        "JOB_ON_HOLD", "JOB_OFF_HOLD", "JOB_ON_ICE", "JOB_OFF_ICE",
        "CHANGE_STATUS", "SET_GLOBAL", "CHECK_HEARTBEAT",
    }
    if body.event_type.upper() not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown event type '{body.event_type}'. Allowed: {sorted(allowed)}",
        )

    # Validate job exists (except SET_GLOBAL which targets a variable name)
    if body.event_type.upper() != "SET_GLOBAL":
        row = job_repo.get_row(session, job_name)
        if row is None:
            raise HTTPException(status_code=404, detail=f"Job '{job_name}' not found")

    ev = Event(
        event_type = body.event_type.upper(),
        job_name   = job_name,
        source     = "api",
        attribute  = body.attribute,
    )
    event_repo.enqueue(session, ev)

    from datetime import datetime
    return SendEventResponse(
        event_id   = ev.event_id,
        event_type = ev.event_type,
        job_name   = ev.job_name,
        queued_at  = datetime.now(),
    )
