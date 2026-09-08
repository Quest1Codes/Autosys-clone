"""
Box dry-run state-machine trace — Phase 1 migration assessment.

Exports the *realized* execution shape of a BOX: which children activate in
which wave, how many ticks the dependency chain takes to unwind, and the
box's final outcome — captured by actually running the box through the same
dry-run machinery `autosys scheduler serve --dry-run` uses (EventProcessor +
BoxManager with the stub dispatcher), rather than reading the static FSM
table in `scheduler/state_machine.py` (that table is a fixed, generic
transition graph shared by every job — it says nothing about *this* box's
dependency shape).

This is a pure export: the target box and all its descendants are reset to
INACTIVE, driven through a synthetic tick loop, and the resulting trace is
returned.  The caller (the REST router) is responsible for rolling back the
session afterward so no run leaves a trace in the database — repeated calls
are safe, including against a database also used by a live
`scheduler serve --dry-run` process.

Run-window / calendar gating
-----------------------------
`ignore_run_window` / `ignore_calendar` are accepted for API forward
compatibility but currently have no effect: in this simulator, `run_window`
is only checked when a CMD job is activated via a top-level STARTJOB event
(`EventProcessor._activate_cmd`); children cascaded by `BoxManager` during a
box run are gated purely by their `condition` dependency expression, and
`run_calendar`/`exclude_calendar` only gate autonomous time-triggered
STARTJOB events, not box-internal cascades. So a box trace already reflects
real dependency gating without any bypass needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from autosys.analysis.dependency_graph import dependency_wave
from autosys.db.repository import jobs as job_repo, events as event_repo
from autosys.db.schema import JobRow
from autosys.models.event import Event
from autosys.scheduler.event_processor import EventProcessor, _stub_dispatch
from autosys.scheduler.state_machine import _norm_status, TERMINAL_STATES


class BoxNotFoundError(Exception):
    """Raised when the requested box_name does not exist."""


class NotABoxError(Exception):
    """Raised when box_name exists but is not a job_type=BOX."""


@dataclass
class BoxTraceTransition:
    tick:     int
    job_name: str
    old:      str
    new:      str
    ts:       str


@dataclass
class BoxTraceJob:
    job_name:       str
    job_type:       str
    condition:      Optional[str]
    wave:           Optional[int]
    activated_tick: Optional[int]
    final_status:   str


@dataclass
class BoxTrace:
    box_name:     str
    triggered_at: str
    completed_at: Optional[str]
    outcome:      str
    tick_count:   int
    wave_count:   int
    jobs:         list[BoxTraceJob] = field(default_factory=list)
    transitions:  list[BoxTraceTransition] = field(default_factory=list)


def _collect_descendants(session: Session, box_name: str) -> list[JobRow]:
    """
    Recursively collect every descendant of a BOX (children, grandchildren, ...).

    A superset of BoxManager.reset_children, which only resets one level —
    nested boxes need their own children reset too before a clean trace run.
    """
    result: list[JobRow] = []
    seen: set[str] = set()
    frontier = [box_name]
    while frontier:
        parent = frontier.pop()
        for child in job_repo.get_children(session, parent):
            if child.job_name in seen:
                continue
            seen.add(child.job_name)
            result.append(child)
            if (child.job_type or "").upper() == "BOX":
                frontier.append(child.job_name)
    return result


def run_box_trace(
    session: Session,
    box_name: str,
    *,
    max_ticks: int = 50,
    ignore_run_window: bool = False,
    ignore_calendar: bool = False,
    now: Optional[datetime] = None,
) -> BoxTrace:
    """
    Reset *box_name* (and all descendants) to INACTIVE, force-start it under
    the dry-run stub dispatcher, and record the resulting transition trace.

    Never commits — the caller must roll back the session afterward.
    Raises BoxNotFoundError / NotABoxError for a missing or non-BOX job_name.
    """
    box = job_repo.get_row(session, box_name)
    if box is None:
        raise BoxNotFoundError(box_name)
    if (box.job_type or "").upper() != "BOX":
        raise NotABoxError(box_name)

    descendants = _collect_descendants(session, box_name)
    scope = {box_name} | {d.job_name for d in descendants}

    now = now or datetime.now()
    box.status, box.last_start, box.last_end = "INACTIVE", None, None
    for d in descendants:
        d.status, d.last_start, d.last_end = "INACTIVE", None, None
    session.flush()

    transitions:    list[BoxTraceTransition] = []
    activated_tick: dict[str, int] = {}
    tick_box = {"n": -1}

    def _recorder(payload: dict) -> None:
        job_name = payload.get("job_name")
        if job_name not in scope:
            return
        tick = tick_box["n"]
        transitions.append(BoxTraceTransition(
            tick     = tick,
            job_name = job_name,
            old      = payload["old"],
            new      = payload["new"],
            ts       = payload["ts"],
        ))
        if _norm_status(payload["new"]) in ("STARTING", "ACTIVATED") and job_name not in activated_tick:
            activated_tick[job_name] = tick

    processor = EventProcessor(
        dispatch_fn      = _stub_dispatch,
        auto_complete    = True,
        on_status_change = _recorder,
    )

    event_repo.enqueue(session, Event(
        event_type = "FORCE_STARTJOB",
        job_name   = box_name,
        source     = "internal",
    ))
    # sync_session() is configured with autoflush=False, so the queued event
    # must be flushed explicitly before the first process_one_tick() call
    # can see it via dequeue_pending().
    session.flush()

    triggered_at = now
    tick_now     = now
    for tick in range(max_ticks):
        tick_box["n"] = tick
        processor.process_one_tick(session, now=tick_now)
        session.flush()
        if _norm_status(box.status) in TERMINAL_STATES:
            break
        tick_now = tick_now + timedelta(seconds=1)

    is_terminal  = _norm_status(box.status) in TERMINAL_STATES
    completed_at = tick_now if is_terminal else None
    outcome      = _norm_status(box.status) if is_terminal else "INCOMPLETE"

    # Wave = dependency-graph depth, computed only for children that were
    # actually activated during this trace (skipped/blocked children get
    # wave=None). See dependency_graph.dependency_wave for why this — not
    # tick number — is the meaningful complexity signal under the instant
    # stub dispatcher.
    condition_by_name = {d.job_name: d.condition for d in descendants}
    wave_memo: dict[str, int] = {}
    jobs: list[BoxTraceJob] = []
    for d in descendants:
        tick_act = activated_tick.get(d.job_name)
        wave = (
            dependency_wave(d.job_name, condition_by_name, scope, wave_memo)
            if tick_act is not None else None
        )
        jobs.append(BoxTraceJob(
            job_name       = d.job_name,
            job_type       = (d.job_type or "CMD").upper(),
            condition      = d.condition,
            wave           = wave,
            activated_tick = tick_act,
            final_status   = _norm_status(d.status),
        ))

    return BoxTrace(
        box_name     = box_name,
        triggered_at = triggered_at.isoformat(),
        completed_at = completed_at.isoformat() if completed_at else None,
        outcome      = outcome,
        tick_count   = tick_box["n"] + 1,
        wave_count   = max((j.wave for j in jobs if j.wave is not None), default=0),
        jobs         = jobs,
        transitions  = transitions,
    )
