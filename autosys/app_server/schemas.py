"""
API schemas — request/response DTOs.

Kept separate from domain models (autosys.models.*) so the API shape can
evolve independently from the internal data model.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional, Any
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Jobs
# ---------------------------------------------------------------------------

class JobResponse(BaseModel):
    """Summary view of a job — used in list responses."""
    job_name:     str
    job_type:     str
    status:       str
    machine:      Optional[str] = None
    box_name:     Optional[str] = None
    condition:    Optional[str] = None
    owner:        Optional[str] = None
    command:      Optional[str] = None
    alarm_if_fail: bool         = True
    last_start:   Optional[datetime] = None
    last_end:     Optional[datetime] = None
    last_run_date: Optional[str] = None

    model_config = {"from_attributes": True}


class JobDetailResponse(JobResponse):
    """Full job detail — returned by GET /api/v1/jobs/{name}."""
    start_times:       list[str]       = []
    days_of_week:      list[str]       = []
    run_calendar:      Optional[str]   = None
    exclude_calendar:  Optional[str]   = None
    n_retrys:          int             = 0
    max_run_alarm:     int             = 0
    min_run_alarm:     int             = 0
    term_run_time:     int             = 0
    alarm_if_terminated: bool          = False
    description:       Optional[str]   = None
    std_out_file:      Optional[str]   = None
    std_err_file:      Optional[str]   = None

    model_config = {"from_attributes": True}


class SendEventRequest(BaseModel):
    """Body for POST /api/v1/jobs/{name}/sendevent."""
    event_type: str = Field(..., description="e.g. STARTJOB, KILLJOB, FORCE_STARTJOB, JOB_ON_HOLD")
    attribute:  Optional[str] = Field(None, description="Extra attribute (e.g. new value for SET_GLOBAL)")


class SendEventResponse(BaseModel):
    event_id:   str
    event_type: str
    job_name:   Optional[str]
    queued_at:  datetime


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

class EventResponse(BaseModel):
    event_id:     str
    event_type:   str
    job_name:     Optional[str] = None
    status:       str
    attribute:    Optional[str] = None
    source:       Optional[str] = None
    created_at:   Optional[datetime] = None
    processed_at: Optional[datetime] = None

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------

class RunResponse(BaseModel):
    run_id:           str
    job_name:         str
    status:           str
    exit_code:        Optional[int]   = None
    machine:          Optional[str]   = None
    run_date:         Optional[str]   = None
    start_time:       Optional[datetime] = None
    end_time:         Optional[datetime] = None
    duration_seconds: Optional[float] = None

    model_config = {"from_attributes": True}


class OutputLineResponse(BaseModel):
    seq:  int
    line: str

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Machines
# ---------------------------------------------------------------------------

class MachineResponse(BaseModel):
    machine_name:    str
    host:            str
    port:            int
    status:          str
    last_heartbeat:  Optional[datetime] = None
    description:     Optional[str]      = None

    model_config = {"from_attributes": True}


class RegisterMachineRequest(BaseModel):
    machine_name: str
    host:         str
    port:         int  = 7520
    description:  Optional[str] = None


# ---------------------------------------------------------------------------
# Global variables
# ---------------------------------------------------------------------------

class GlobalVarResponse(BaseModel):
    name:  str
    value: str

    model_config = {"from_attributes": True}


class SetGlobalRequest(BaseModel):
    value: str


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class TokenRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type:   str = "bearer"
    expires_in:   int


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------

class HealthResponse(BaseModel):
    status:   str          # "ok" | "degraded"
    db:       str          # "connected" | "error"
    version:  str = "0.1.0"
    details:  dict[str, Any] = {}


# ---------------------------------------------------------------------------
# WebSocket event broadcast
# ---------------------------------------------------------------------------

class WsStatusChange(BaseModel):
    """Broadcast on the /api/v1/ws/events channel when a job status changes."""
    type:     str      = "STATUS_CHANGE"
    job_name: str
    old:      str
    new:      str
    ts:       str      # ISO 8601
