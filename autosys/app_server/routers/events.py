"""
Events router — inspect the event queue and history.

Schema notes:
  EventQueueRow  — has processed (bool) + processed_at, no status/attribute cols.
                   status is derived: "PENDING" or "PROCESSED".
                   attribute is derived from global_value (SET_GLOBAL events).
  EventHistoryRow— has event_id, event_type, job_name, source, created_at,
                   global_value, metadata_json.  No processed_at column.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from sqlalchemy import select

from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import EventResponse
from autosys.db.schema          import EventQueueRow, EventHistoryRow

router = APIRouter(prefix="/api/v1/events", tags=["events"])


def _queue_row_to_resp(row: EventQueueRow) -> EventResponse:
    return EventResponse(
        event_id     = row.event_id,
        event_type   = row.event_type,
        job_name     = row.job_name,
        status       = "PROCESSED" if row.processed else "PENDING",
        attribute    = row.global_value,   # populated for SET_GLOBAL events
        source       = row.source,
        created_at   = row.created_at,
        processed_at = row.processed_at,
    )


def _hist_row_to_resp(row: EventHistoryRow) -> EventResponse:
    return EventResponse(
        event_id     = row.event_id,
        event_type   = row.event_type,
        job_name     = row.job_name,
        status       = "PROCESSED",
        attribute    = row.global_value,   # populated for SET_GLOBAL events
        source       = row.source,
        created_at   = row.created_at,
        processed_at = None,               # EventHistoryRow has no processed_at
    )


@router.get("", response_model=list[EventResponse])
def list_pending_events(
    limit:   int          = Query(100, ge=1, le=1000),
    session: Session      = Depends(get_session),
    _user:   CurrentUser  = Depends(get_current_user),
):
    """Return all PENDING events in the queue (FIFO order)."""
    rows = session.scalars(
        select(EventQueueRow)
        .where(EventQueueRow.processed == False)   # noqa: E712
        .order_by(EventQueueRow.created_at)
        .limit(limit)
    ).all()
    return [_queue_row_to_resp(r) for r in rows]


@router.get("/history", response_model=list[EventResponse])
def event_history(
    job:     Optional[str] = Query(None, description="Filter by job_name"),
    limit:   int           = Query(50, ge=1, le=500),
    session: Session       = Depends(get_session),
    _user:   CurrentUser   = Depends(get_current_user),
):
    """Return processed events from event_history, newest first."""
    q = select(EventHistoryRow).order_by(EventHistoryRow.created_at.desc()).limit(limit)
    if job:
        q = q.where(EventHistoryRow.job_name == job)
    rows = session.scalars(q).all()
    return [_hist_row_to_resp(r) for r in rows]
