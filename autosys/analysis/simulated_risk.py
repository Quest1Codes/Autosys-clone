"""
Simulated operational-risk provider — fills the gap when a job has no history.

`operational_risk.score_operational_risk` needs JobRunRow/AlarmRow history, and
a freshly imported JIL has none.  This module produces that history by running
the same dry-run machinery as `scheduler serve --dry-run` (see
scheduler/simulation_runner.py) — but against a throwaway in-memory database
seeded from a snapshot of the live job definitions, never the live DB itself.

Why isolated rather than run in place
--------------------------------------
`run_simulation` resets every job to INACTIVE, commits, and enqueues
FORCE_STARTJOB events.  Inside the client container the live event processor
polls that same DB, and the WCC dashboard displays those statuses, so running it
in place would clobber live job states, feed the live processor phantom events,
and mix synthetic rows into history that real (non-dry-run) execution may also
write.  Here only the aggregated RunStats leave the scratch DB.

Caching
-------
Results are cached in-process, keyed by a hash of the job *definitions* (runtime
columns such as status/last_start are excluded, otherwise the live scheduler
would invalidate the key every tick) plus cycles and seed.  Importing new JIL
changes the key and triggers a fresh simulation; seed is fixed, so an unchanged
job set always yields the same numbers.

Concurrency
-----------
One background thread per key (single-flight).  `ensure_simulation` waits up to
`wait_s` for it and otherwise reports "pending" so a request never blocks longer
than the caller can tolerate — Shinro's connectivity check uses a 5s timeout and
hits the same endpoint as the report.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional

from loguru import logger
from sqlalchemy import create_engine, inspect as sa_inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from autosys.analysis.operational_risk import RunStats, fetch_run_stats
from autosys.db.schema import (
    Base, CalendarRow, GlobalVariableRow, JobRow, MachineRow, VirtualResourceRow,
)

DEFAULT_CYCLES = 20
DEFAULT_SEED = 42
_TICKS_PER_CYCLE = 10

# Tables copied into the scratch DB so the simulation sees the same calendars,
# globals, machines and resources the live one would.
_MODELS = (JobRow, CalendarRow, GlobalVariableRow, MachineRow, VirtualResourceRow)

# Runtime columns the live scheduler rewrites constantly — excluded from the
# cache key so it only changes when a job *definition* changes.
_RUNTIME_JOB_COLS = frozenset({
    "status", "last_start", "last_end", "last_run_date", "created_at", "updated_at",
})

# A failed simulation is retried after this long even if the job set is
# unchanged, so a transient error doesn't stick forever.
_RETRY_FAILED_AFTER_S = 60.0
_MAX_CACHED_KEYS = 4


def simulation_enabled() -> bool:
    """AUTOSYS_REPORT_SIMULATE=0 turns the automatic simulation off entirely."""
    return os.environ.get("AUTOSYS_REPORT_SIMULATE", "1").strip().lower() not in ("0", "false", "no")


def default_warm_delay_s() -> float:
    """Quiet period after the last JIL import before a warm-up simulation starts."""
    try:
        return float(os.environ.get("AUTOSYS_REPORT_WARM_DELAY_S", "5"))
    except ValueError:
        return 5.0


def default_wait_s() -> float:
    """How long a report request waits for a simulation before returning 'pending'."""
    try:
        return float(os.environ.get("AUTOSYS_REPORT_SIM_WAIT_S", "3"))
    except ValueError:
        return 3.0


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class _SimState:
    key: str
    status: str = "running"          # running | ready | failed
    started_at: float = field(default_factory=time.time)
    finished_at: Optional[float] = None
    stats: dict[str, RunStats] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    done: threading.Event = field(default_factory=threading.Event)


@dataclass
class SimOutcome:
    """What a caller gets back: 'ready' | 'pending' | 'failed' | 'empty'."""
    status: str
    stats: dict[str, RunStats] = field(default_factory=dict)
    summary: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    cycles: int = DEFAULT_CYCLES
    seed: int = DEFAULT_SEED


_lock = threading.Lock()
_states: dict[str, _SimState] = {}
_warm_timer: Optional[threading.Timer] = None


def reset_cache() -> None:
    """Drop every cached result and cancel any pending warm-up (tests)."""
    global _warm_timer
    with _lock:
        _states.clear()
        if _warm_timer is not None:
            _warm_timer.cancel()
            _warm_timer = None


# ---------------------------------------------------------------------------
# Snapshot + key
# ---------------------------------------------------------------------------

def _column_keys(model) -> list[str]:
    return [a.key for a in sa_inspect(model).mapper.column_attrs]


def take_snapshot(session: Session) -> dict[str, list[dict[str, Any]]]:
    """Copy the rows a simulation needs out of the live DB as plain dicts."""
    snap: dict[str, list[dict[str, Any]]] = {}
    for model in _MODELS:
        keys = _column_keys(model)
        snap[model.__name__] = [
            {k: getattr(row, k) for k in keys}
            for row in session.scalars(select(model))
        ]
    return snap


def _snapshot_key(snap: dict[str, list[dict[str, Any]]], cycles: int, seed: int) -> str:
    jobs = sorted(
        (
            {k: v for k, v in row.items() if k not in _RUNTIME_JOB_COLS}
            for row in snap["JobRow"]
        ),
        key=lambda r: r["job_name"],
    )
    blob = json.dumps(jobs, sort_keys=True, default=str)
    return f"{hashlib.sha256(blob.encode()).hexdigest()}:{cycles}:{seed}"


# ---------------------------------------------------------------------------
# The simulation itself (runs against a scratch DB)
# ---------------------------------------------------------------------------

def _simulate(
    snap: dict[str, list[dict[str, Any]]], cycles: int, seed: int
) -> tuple[dict[str, RunStats], dict[str, Any]]:
    from autosys.scheduler.simulation_runner import run_simulation

    engine = create_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    try:
        Base.metadata.create_all(engine)
        with Session(engine) as scratch:
            for model in _MODELS:
                scratch.add_all(model(**row) for row in snap[model.__name__])
            scratch.commit()

            summary = run_simulation(
                scratch, cycles=cycles, ticks_per_cycle=_TICKS_PER_CYCLE, seed=seed,
            )
            stats = fetch_run_stats(scratch, [r["job_name"] for r in snap["JobRow"]])
        return stats, summary
    finally:
        engine.dispose()


def _worker(
    state: _SimState, snap: dict[str, list[dict[str, Any]]], cycles: int, seed: int
) -> None:
    try:
        stats, summary = _simulate(snap, cycles, seed)
        state.stats, state.summary = stats, summary
        state.status = "ready"
        logger.info(
            "Simulated risk ready: {} jobs, {} runs in {:.1f}s",
            len(stats), summary.get("total_runs"), time.time() - state.started_at,
        )
    except Exception as exc:  # noqa: BLE001 — surfaced to the caller as status=failed
        logger.exception("Risk simulation failed: {}", exc)
        state.error = str(exc) or type(exc).__name__
        state.status = "failed"
    finally:
        state.finished_at = time.time()
        state.done.set()


def _request(
    snap: dict[str, list[dict[str, Any]]], cycles: int, seed: int
) -> _SimState:
    """Return the state for this snapshot, starting a background run if needed."""
    key = _snapshot_key(snap, cycles, seed)
    with _lock:
        state = _states.get(key)
        if state is not None:
            failed_recently = (
                state.status == "failed"
                and state.finished_at is not None
                and time.time() - state.finished_at < _RETRY_FAILED_AFTER_S
            )
            if state.status in ("running", "ready") or failed_recently:
                return state

        state = _SimState(key=key)
        _states[key] = state
        while len(_states) > _MAX_CACHED_KEYS:
            _states.pop(next(iter(_states)))

    threading.Thread(
        target=_worker, args=(state, snap, cycles, seed),
        name="autosys-risk-sim", daemon=True,
    ).start()
    return state


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def warm(session: Session, cycles: int = DEFAULT_CYCLES, seed: int = DEFAULT_SEED) -> None:
    """Start a simulation for the current job set without waiting for it."""
    snap = take_snapshot(session)
    if snap["JobRow"]:
        _request(snap, cycles, seed)


def schedule_warm(delay_s: Optional[float] = None) -> None:
    """
    Debounced `warm`: start a simulation *delay_s* after the most recent call.

    A bulk JIL import makes one API call per file; restarting the timer on each
    means one simulation for the final job set instead of one per file.  The
    timer opens its own session so callers (request handlers, startup) don't
    have to keep theirs alive.
    """
    global _warm_timer
    if delay_s is None:
        delay_s = default_warm_delay_s()

    def _fire() -> None:
        from autosys.db.connection import sync_session
        try:
            with sync_session() as session:
                warm(session)
        except Exception as exc:  # noqa: BLE001 — warm-up is best effort
            logger.warning("Risk simulation warm-up skipped: {}", exc)

    with _lock:
        if _warm_timer is not None:
            _warm_timer.cancel()
        _warm_timer = threading.Timer(delay_s, _fire)
        _warm_timer.daemon = True
        _warm_timer.start()


def ensure_simulation(
    session: Session,
    *,
    cycles: int = DEFAULT_CYCLES,
    seed: int = DEFAULT_SEED,
    wait_s: Optional[float] = None,
) -> SimOutcome:
    """
    Return simulated RunStats for the current job set, starting a run if none is
    cached.  *wait_s* bounds how long to wait for an in-flight run (None = block
    until it finishes); on timeout the outcome is 'pending' with empty stats.
    """
    snap = take_snapshot(session)
    if not snap["JobRow"]:
        return SimOutcome(status="empty", cycles=cycles, seed=seed)

    state = _request(snap, cycles, seed)
    if state.status == "running":
        state.done.wait(timeout=wait_s)

    if state.status == "ready":
        return SimOutcome("ready", state.stats, state.summary, cycles=cycles, seed=seed)
    if state.status == "failed":
        return SimOutcome("failed", error=state.error, cycles=cycles, seed=seed)
    return SimOutcome("pending", cycles=cycles, seed=seed)
