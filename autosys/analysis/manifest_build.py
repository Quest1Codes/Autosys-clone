"""
T39 follow-up: the /manifest build as a background, cached computation.

The synchronous ``GET /manifest`` handler measured ~500s at ~100k jobs on
the old ``/report`` pipeline's equivalent work (classification +
disposition + size over every row). This module moves that work off the
request path using the same in-process pattern
``analysis/simulated_risk.py`` established: a cache keyed by a hash of
what the result depends on, a daemon thread doing the build, and an
``ensure_*`` entry point whose ``wait_s`` bounds how long a caller
blocks before getting "pending" back.

Cache key = hash of every non-runtime JobRow column + sorted calendar
names + the rules file's version + the answers the build was given. A
JIL re-import changes the job columns; a calendar add/remove changes
the calendar list; a rules redeploy changes the version -- each yields a
new key, so a stale manifest can never be served. ``JobDisposition``s are
serialized to plain dicts at build time, so nothing ORM-bound or
session-bound is held in the cache.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import inspect as sa_inspect
from sqlalchemy import select
from sqlalchemy.orm import Session

from autosys.analysis.classification import load_rules
from autosys.analysis.disposition import build_manifest
from autosys.analysis.size_model import size
from autosys.db.schema import CalendarRow, JobRow

logger = logging.getLogger(__name__)

# Same runtime-column exclusion as simulated_risk._RUNTIME_JOB_COLS:
# the build reads job *definitions*, so a status change must not
# invalidate the cache (and a definition edit always must).
_RUNTIME_JOB_COLS = frozenset({
    "status", "last_start", "last_end", "last_run_date", "created_at", "updated_at",
})
# A failed build is retried after this long even with an unchanged
# estate, so a transient error doesn't stick forever.
_RETRY_FAILED_AFTER_S = 60.0
_MAX_CACHED_KEYS = 4


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

@dataclass
class _ManifestState:
    key: str
    status: str = "running"          # running | ready | failed
    started_at: float = field(default_factory=time.time)
    finished_at: float | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""
    done: threading.Event = field(default_factory=threading.Event)


@dataclass
class ManifestOutcome:
    """What a caller gets back: 'ready' | 'pending' | 'failed' | 'empty'.
    `payload` is the ManifestResponse-shaped dict when ready."""
    status: str
    payload: dict[str, Any] = field(default_factory=dict)
    error: str = ""


_lock = threading.Lock()
_states: dict[str, _ManifestState] = {}


def reset_cache() -> None:
    """Drop every cached result (tests)."""
    with _lock:
        _states.clear()


# ---------------------------------------------------------------------------
# Key
# ---------------------------------------------------------------------------

def _estate_key(session: Session, answers: dict | None) -> tuple[str, int]:
    """Hash what a manifest depends on, and the job count (0 -> 'empty').

    JobRow's full non-runtime column set (not just job_name/job_type):
    a JIL edit that changes a command or condition but keeps the name
    must invalidate the cache. Calendars feed
    ``build_manifest``'s defined-calendar check; the rules version feeds
    classification and disposition defaults; answers feed both.
    """
    job_cols = [
        a.key for a in sa_inspect(JobRow).mapper.column_attrs
        if a.key not in _RUNTIME_JOB_COLS
    ]
    rows = session.execute(
        select(*[getattr(JobRow, c) for c in job_cols])
    ).all()
    calendars = session.scalars(select(CalendarRow.calendar_name)).all()
    body = {
        "jobs": sorted(tuple(str(v) for v in row) for row in rows),
        "calendars": sorted(calendars),
        "rules": load_rules().meta.get("version", ""),
        "answers": answers or {},
    }
    return hashlib.sha256(
        json.dumps(body, sort_keys=True, default=str).encode()
    ).hexdigest()[:32], len(rows)


# ---------------------------------------------------------------------------
# The build itself (worker thread, own session)
# ---------------------------------------------------------------------------

def _build_payload(session: Session, answers: dict | None) -> dict[str, Any]:
    """The ManifestResponse-shaped dict for the estate this session sees.
    Runs inside the worker's own session -- SQLAlchemy sessions aren't
    thread-safe, so the request's session is never touched here."""
    rows = list(session.scalars(select(JobRow)))
    manifest = build_manifest(rows, session)
    vector = size(rows)
    return {
        "manifest": [
            {"job_name": m.job_name, "class_": m.class_, "best": m.best,
             "worst": m.worst, "rule_id": m.rule_id}
            for m in manifest
        ],
        "size_vector": {
            "low": vector.low, "high": vector.high, "cu": vector.cu,
            "families": vector.families, "row_count": vector.row_count,
            "dominant_order": [list(pair) for pair in vector.dominant_order()],
            "constructs": vector.constructs,
            "cutover_group_sizes": list(vector.cutover_group_sizes),
        },
        "rules_version": load_rules().meta.get("version", ""),
    }


def _worker(state: _ManifestState, answers: dict | None) -> None:
    from autosys.db.connection import sync_session

    try:
        with sync_session() as session:
            state.payload = _build_payload(session, answers)
            # Re-key on the estate the build actually saw: a JIL re-import
            # between the request-time key and this build would otherwise
            # file the new estate's manifest under the old estate's key,
            # where a wrong request could hit it.
            real_key, _ = _estate_key(session, answers)
        if real_key != state.key:
            with _lock:
                _states[real_key] = state
                state.key = real_key
        state.status = "ready"
        logger.info(
            "Manifest build ready: %d jobs in %.1fs",
            state.payload["size_vector"]["row_count"],
            time.time() - state.started_at,
        )
    except Exception as exc:
        # stdlib logging only interpolates % args -- a "{}"-style call here
        # would raise inside this handler and leave the state stuck on
        # "running" forever (the same latent bug simulated_risk.py's
        # exception paths carry; see tests/test_assessment.py's 202->500 case)
        logger.exception("Manifest build failed")
        state.error = str(exc) or type(exc).__name__
        state.status = "failed"
    finally:
        state.finished_at = time.time()
        state.done.set()


def _request(key: str, answers: dict | None) -> _ManifestState:
    """Return the state for this estate key, starting a build if needed."""
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

        state = _ManifestState(key=key)
        _states[key] = state
        while len(_states) > _MAX_CACHED_KEYS:
            _states.pop(next(iter(_states)))

    threading.Thread(
        target=_worker, args=(state, answers),
        name="autosys-manifest-build", daemon=True,
    ).start()
    return state


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ensure_manifest(
    session: Session,
    *,
    answers: dict | None = None,
    wait_s: float | None = None,
) -> ManifestOutcome:
    """
    Return the manifest payload for the current estate, starting a build
    if none is cached. *wait_s* bounds how long to wait for an in-flight
    build (None = block until it finishes -- the historical synchronous
    behaviour); on timeout the outcome is 'pending' and the caller should
    answer 202 so the client polls.
    """
    key, job_count = _estate_key(session, answers)
    if not job_count:
        return ManifestOutcome(status="empty")

    state = _request(key, answers)
    if state.status == "running":
        state.done.wait(timeout=wait_s)

    if state.status == "ready":
        return ManifestOutcome("ready", payload=state.payload)
    if state.status == "failed":
        return ManifestOutcome("failed", error=state.error)
    return ManifestOutcome("pending")
