"""
Runs router — job execution history and stdout output.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import RunResponse, OutputLineResponse
from autosys.db.schema          import JobRunRow, JobOutputRow
from autosys.db.repository      import runs as run_repo, output as output_repo

router = APIRouter(prefix="/api/v1/runs", tags=["runs"])


def _run_to_resp(row: JobRunRow) -> RunResponse:
    duration = None
    if row.start_time and row.end_time:
        duration = (row.end_time - row.start_time).total_seconds()
    return RunResponse(
        run_id           = row.run_id,
        job_name         = row.job_name,
        status           = row.status or "RUNNING",
        exit_code        = row.exit_code,
        machine          = row.machine,
        run_date         = row.run_date,
        start_time       = row.start_time,
        end_time         = row.end_time,
        duration_seconds = duration,
    )


@router.get("", response_model=list[RunResponse])
def list_runs(
    job:     Optional[str] = Query(None, description="Filter by job_name"),
    limit:   int           = Query(20, ge=1, le=200),
    session: Session       = Depends(get_session),
    _user:   CurrentUser   = Depends(get_current_user),
):
    """List run history, newest first."""
    rows = run_repo.get_history(session, job_name=job, limit=limit)
    return [_run_to_resp(r) for r in rows]


@router.get("/{run_id}", response_model=RunResponse)
def get_run(
    run_id:  str,
    session: Session     = Depends(get_session),
    _user:   CurrentUser = Depends(get_current_user),
):
    """Get a single run record by run_id."""
    row = session.get(JobRunRow, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    return _run_to_resp(row)


@router.get("/{run_id}/output", response_model=list[OutputLineResponse])
def get_run_output(
    run_id:  str,
    offset:  int         = Query(0, ge=0, description="Skip first N lines"),
    limit:   int         = Query(500, ge=1, le=5000),
    session: Session     = Depends(get_session),
    _user:   CurrentUser = Depends(get_current_user),
):
    """Get captured stdout lines for a run."""
    run = session.get(JobRunRow, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")
    lines = output_repo.get_lines(session, run_id, offset=offset, limit=limit)
    return [OutputLineResponse(seq=l.line_no, line=l.content) for l in lines]
