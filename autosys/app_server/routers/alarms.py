"""
Alarms router — list and resolve alarms raised by the scheduler.

GET  /api/v1/alarms                 list alarms (filter by active=true/false)
POST /api/v1/alarms/{id}/resolve    clear an active alarm
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import AlarmResponse
from autosys.db.schema          import AlarmRow

router = APIRouter(prefix="/api/v1/alarms", tags=["alarms"])


def _row_to_resp(row: AlarmRow) -> AlarmResponse:
    return AlarmResponse(
        alarm_id            = row.alarm_id,
        job_name            = row.job_name,
        run_id              = row.run_id,
        alarm_type          = row.alarm_type,
        message             = row.message,
        job_status_at_raise = row.job_status_at_raise,
        raised_at           = row.raised_at,
        cleared_at          = row.cleared_at,
        cleared_by          = row.cleared_by,
        active              = row.cleared_at is None,
    )


@router.get("", response_model=list[AlarmResponse])
def list_alarms(
    active:  Optional[bool] = Query(None, description="true=active only, false=resolved only, omit=all"),
    job:     Optional[str]  = Query(None, description="Filter by job_name"),
    limit:   int            = Query(100, ge=1, le=1000),
    session: Session        = Depends(get_session),
    _user:   CurrentUser    = Depends(get_current_user),
) -> list[AlarmResponse]:
    """List alarms, optionally filtered by active state or job name."""
    stmt = select(AlarmRow).order_by(AlarmRow.raised_at.desc()).limit(limit)

    if active is True:
        stmt = stmt.where(AlarmRow.cleared_at.is_(None))
    elif active is False:
        stmt = stmt.where(AlarmRow.cleared_at.is_not(None))

    if job:
        stmt = stmt.where(AlarmRow.job_name == job)

    rows = session.execute(stmt).scalars().all()
    return [_row_to_resp(r) for r in rows]


@router.post("/{alarm_id}/resolve", response_model=AlarmResponse)
def resolve_alarm(
    alarm_id: str,
    session:  Session     = Depends(get_session),
    user:     CurrentUser = Depends(get_current_user),
) -> AlarmResponse:
    """Clear an active alarm (mark as resolved)."""
    user.require_role("operator", "admin")

    row = session.get(AlarmRow, alarm_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Alarm '{alarm_id}' not found")
    if row.cleared_at is not None:
        raise HTTPException(status_code=409, detail="Alarm is already resolved")

    row.cleared_at = datetime.utcnow()
    row.cleared_by = user.username
    session.flush()
    return _row_to_resp(row)


@router.post("", response_model=AlarmResponse, status_code=201)
def raise_alarm(
    job_name:   str,
    alarm_type: str,
    message:    str,
    run_id:     Optional[str] = None,
    session:    Session       = Depends(get_session),
    user:       CurrentUser   = Depends(get_current_user),
) -> AlarmResponse:
    """Manually raise an alarm (admin only — mainly for testing)."""
    user.require_role("admin")

    row = AlarmRow(
        alarm_id            = str(uuid.uuid4()),
        job_name            = job_name,
        run_id              = run_id,
        alarm_type          = alarm_type,
        message             = message,
        job_status_at_raise = None,
        raised_at           = datetime.utcnow(),
    )
    session.add(row)
    session.flush()
    return _row_to_resp(row)
