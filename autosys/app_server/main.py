"""
AutoSys Application Server (SSA) — FastAPI entry point.

Responsibilities
----------------
1. Mount all REST routers under /api/v1/
2. Expose /api/v1/auth/token (JWT login)
3. Expose /api/v1/ws/events  (WebSocket live status feed)
4. Expose /health endpoints
5. Optionally start the Event Processor as a background asyncio task
   (used by ``autosys scheduler serve``)

Design notes
------------
- All REST endpoints use *sync* FastAPI route functions (def, not async def).
  FastAPI runs them in a threadpool automatically, so SQLAlchemy sync sessions
  are safe without wrapping in run_in_executor.
- The WebSocket endpoint is async (required by Starlette).
- The broadcaster singleton is stored on app.state so it can be injected
  anywhere and replaced in tests.
- The EPS background task is started in the lifespan context manager; cancelling
  it on shutdown drains any in-flight tick gracefully.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from pathlib import Path

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger

from autosys.analysis import simulated_risk
from autosys.app_server.broadcaster import EventBroadcaster
from autosys.app_server.deps        import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas     import (
    TokenRequest, TokenResponse, HealthResponse,
    ExecutionModeResponse, SetExecutionModeRequest,
)
from autosys.app_server.routers     import jobs, events, runs, machines, globals as globals_router, jil as jil_router, alarms as alarms_router, assessment, metrics as metrics_router
from autosys.db.connection          import sync_session


# ---------------------------------------------------------------------------
# Module-level broadcaster singleton
# ---------------------------------------------------------------------------

_broadcaster = EventBroadcaster()


def get_broadcaster() -> EventBroadcaster:
    """FastAPI dependency — returns the module-level broadcaster."""
    return _broadcaster


# ---------------------------------------------------------------------------
# EPS processor construction — shared by startup and the live mode toggle
# ---------------------------------------------------------------------------

def _build_eps_processor(dry_run: bool, eps_poll_interval: float):
    """
    Build an ``EventProcessor`` wired for either dry-run (stub dispatcher,
    instant completion, failure injection + alarms) or real execution
    (``AgentDispatch``, real subprocess dispatch). Shared by the initial
    ``create_app`` startup and by ``PUT /api/v1/settings/execution-mode``
    so both paths build an identical, correctly-paired dispatcher +
    ``auto_complete`` combination.
    """
    from autosys.scheduler.event_processor import EventProcessor, _stub_dispatch
    if dry_run:
        from autosys.notifications.alarm_manager import AlarmManager
        from autosys.scheduler.failure_injector import FailureInjector
        return EventProcessor(
            poll_interval    = eps_poll_interval,
            on_status_change = _broadcaster.publish_sync,
            dispatch_fn      = _stub_dispatch,
            auto_complete    = True,
            alarm_manager    = AlarmManager(),
            failure_injector = FailureInjector(seed=42),
        )
    from autosys.agent.dispatch import AgentDispatch
    agent = AgentDispatch(local_only=False)
    return EventProcessor(
        poll_interval    = eps_poll_interval,
        on_status_change = _broadcaster.publish_sync,
        dispatch_fn      = agent.dispatch,
        kill_fn          = agent.kill,
        auto_complete    = False,
    )


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app(
    start_eps: bool = False,
    eps_poll_interval: float = 1.0,
    dry_run: bool = False,
    ha: bool = False,
    tie_breaker: bool = False,
) -> FastAPI:
    """
    Create and return the FastAPI application.

    Parameters
    ----------
    start_eps:
        If True, start the Event Processor as a background asyncio task in
        the lifespan hook.  Used by ``autosys scheduler serve``.
    eps_poll_interval:
        EPS tick interval in seconds (only relevant when start_eps=True).
    dry_run:
        If True, use the stub dispatcher (no real subprocess / no remote
        agent required).  Jobs transition STARTING → RUNNING → SUCCESS
        instantly so the full state machine can be exercised without any
        target machines.  Intended for local dev and migration analysis.
    ha:
        If True, enable HA mode with distributed lock for tie-breaker
        scheduling.  The EPS acquires a DB-row lock before processing.
    tie_breaker:
        If True (with ha=True), run as standby tie-breaker scheduler.
        Only processes events when the primary's heartbeat is stale.
    """

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Store broadcaster on app state for WebSocket endpoint
        app.state.broadcaster = _broadcaster
        logger.info("AutoSys App Server starting")

        app.state.dry_run    = dry_run
        app.state.ha         = ha
        app.state.tie_breaker = tie_breaker
        app.state.eps_task   = None
        # The simulated-risk cache is in-memory, so a restart loses it — rebuild it
        # for whatever JIL is already in the DB (no-op on an empty DB).
        if dry_run and simulated_risk.simulation_enabled():
            simulated_risk.schedule_warm(delay_s=0)
        if start_eps:
            processor = _build_eps_processor(dry_run, eps_poll_interval)
            logger.info(
                "Event Processor running in {} mode",
                "DRY-RUN (stub dispatcher + failure injection + alarms)" if dry_run else "REAL",
            )
            # HA mode: wrap processor with distributed lock
            if ha:
                from autosys.scheduler.ha import DistributedLock
                ha_lock = DistributedLock(
                    heartbeat_timeout=int(eps_poll_interval * 5),
                )
                processor.ha_lock = ha_lock
                if tie_breaker:
                    processor.is_standby = True
                    logger.info("HA: running as standby tie-breaker")
                else:
                    logger.info("HA: running as primary")
            app.state.eps_task = asyncio.create_task(
                processor.run_forever(),
                name="eps-background",
            )
            logger.info("Event Processor background task started (poll={}s)", eps_poll_interval)

        yield

        eps_task: Optional[asyncio.Task] = app.state.eps_task
        if eps_task is not None and not eps_task.done():
            eps_task.cancel()
            try:
                await eps_task
            except asyncio.CancelledError:
                pass
            logger.info("Event Processor background task stopped")

        logger.info("AutoSys App Server stopped")

    app = FastAPI(
        title       = "AutoSys Clone — Application Server",
        description = "REST API for the AutoSys workload scheduler clone.",
        version     = "0.1.0",
        lifespan    = lifespan,
    )

    # CORS — allow all origins in development; tighten in production via env var
    app.add_middleware(
        CORSMiddleware,
        allow_origins     = ["*"],
        allow_credentials = True,
        allow_methods     = ["*"],
        allow_headers     = ["*"],
    )

    # --- REST routers ---
    app.include_router(jobs.router)
    app.include_router(events.router)
    app.include_router(runs.router)
    app.include_router(machines.router)
    app.include_router(globals_router.router)
    app.include_router(jil_router.router)
    app.include_router(alarms_router.router)
    app.include_router(assessment.router)
    app.include_router(metrics_router.router)

    # --- Auth ---
    _register_auth_routes(app)

    # --- WebSocket ---
    _register_ws_routes(app)

    # --- Health ---
    _register_health_routes(app)

    # --- Settings (execution-mode toggle) ---
    _register_settings_routes(app, eps_poll_interval, start_eps)

    # --- Static UI ---
    _mount_static(app)

    return app


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

def _register_auth_routes(app: FastAPI) -> None:

    @app.post("/api/v1/auth/token", response_model=TokenResponse, tags=["auth"])
    def login(body: TokenRequest):
        """
        Exchange username + password for a JWT access token.

        Users are configured via the AUTOSYS_USERS environment variable
        (JSON object).  Defaults: admin/admin, operator/operator, viewer/viewer.
        """
        from autosys.app_server.auth import authenticate, encode_token, _JWT_TTL
        user = authenticate(body.username, body.password)
        if user is None:
            raise HTTPException(
                status_code = status.HTTP_401_UNAUTHORIZED,
                detail      = "Invalid username or password",
                headers     = {"WWW-Authenticate": "Bearer"},
            )
        token = encode_token(body.username, user["role"])
        return TokenResponse(
            access_token = token,
            token_type   = "bearer",
            expires_in   = _JWT_TTL,
        )


# ---------------------------------------------------------------------------
# WebSocket routes
# ---------------------------------------------------------------------------

def _register_ws_routes(app: FastAPI) -> None:

    @app.websocket("/api/v1/ws/events")
    async def ws_events(ws: WebSocket):
        """
        Subscribe to live job status change events.

        Every time a job transitions to a new status the EPS publishes a
        JSON message here::

            {"type": "STATUS_CHANGE", "job_name": "...", "old": "RUNNING",
             "new": "SUCCESS", "ts": "2024-01-15T06:00:00"}

        Connect with any WebSocket client::

            wscat -c ws://localhost:9000/api/v1/ws/events
        """
        broadcaster: EventBroadcaster = app.state.broadcaster
        await broadcaster.connect(ws)
        try:
            while True:
                # Keep-alive: drain any ping frames from the client
                await ws.receive_text()
        except WebSocketDisconnect:
            broadcaster.disconnect(ws)


# ---------------------------------------------------------------------------
# Health routes
# ---------------------------------------------------------------------------

def _register_health_routes(app: FastAPI) -> None:

    @app.get("/health", response_model=HealthResponse, tags=["health"])
    @app.get("/health/live", response_model=HealthResponse, tags=["health"])
    def health_live():
        """Liveness probe — returns 200 if the process is running."""
        return HealthResponse(status="ok", db="unknown")

    @app.get("/health/ready", response_model=HealthResponse, tags=["health"])
    def health_ready():
        """
        Readiness probe — checks that the DB is reachable and EPS is running.
        Returns 200 if ready, 503 if the DB connection fails.
        """
        try:
            with sync_session() as session:
                session.execute(__import__("sqlalchemy").text("SELECT 1"))
            db_status = "connected"
        except Exception as exc:
            logger.error("Health check DB error: {}", exc)
            raise HTTPException(
                status_code = status.HTTP_503_SERVICE_UNAVAILABLE,
                detail      = f"Database unavailable: {exc}",
            )
        # Check EPS status from app state
        eps_status = "running" if getattr(app.state, "eps_running", False) else "stopped"
        return HealthResponse(
            status="ok",
            db=db_status,
            details={"eps": eps_status},
        )

    @app.get("/api/v1/ha/status", tags=["ha"])
    def ha_status():
        """HA status — returns scheduler lock and secondary DB info."""
        from autosys.scheduler.ha import DistributedLock, SecondaryDB

        lock = DistributedLock()
        sec = SecondaryDB()
        with sync_session() as session:
            lock_status = lock.get_status(session)
        return {
            "scheduler_lock": lock_status,
            "secondary_db": sec.get_status(),
            "instance_id": lock.instance_id,
        }


# ---------------------------------------------------------------------------
# Settings — live dry-run / real-run toggle
# ---------------------------------------------------------------------------

def _register_settings_routes(app: FastAPI, eps_poll_interval: float, start_eps: bool) -> None:

    @app.get("/api/v1/settings/execution-mode", response_model=ExecutionModeResponse, tags=["settings"])
    def get_execution_mode():
        return ExecutionModeResponse(dry_run=getattr(app.state, "dry_run", True))

    @app.put("/api/v1/settings/execution-mode", response_model=ExecutionModeResponse, tags=["settings"])
    async def set_execution_mode(
        body: SetExecutionModeRequest,
        user: CurrentUser = Depends(get_current_user),
    ):
        """
        Switch the running Event Processor between dry-run (stub dispatcher,
        no real subprocesses) and real execution (``AgentDispatch``), without
        restarting the container. Admin only.
        """
        user.require_role("admin")

        if not start_eps:
            raise HTTPException(
                status_code = status.HTTP_409_CONFLICT,
                detail      = "This server was not started with an Event Processor (autosys scheduler serve).",
            )

        old_task: Optional[asyncio.Task] = getattr(app.state, "eps_task", None)
        if old_task is not None and not old_task.done():
            old_task.cancel()
            try:
                await old_task
            except asyncio.CancelledError:
                pass

        processor = _build_eps_processor(body.dry_run, eps_poll_interval)
        if getattr(app.state, "ha", False):
            from autosys.scheduler.ha import DistributedLock
            processor.ha_lock = DistributedLock(heartbeat_timeout=int(eps_poll_interval * 5))
            processor.is_standby = getattr(app.state, "tie_breaker", False)

        app.state.dry_run  = body.dry_run
        app.state.eps_task = asyncio.create_task(processor.run_forever(), name="eps-background")
        logger.info("Execution mode switched to {} by {}", "DRY-RUN" if body.dry_run else "REAL", user.username)
        return ExecutionModeResponse(dry_run=body.dry_run)


# ---------------------------------------------------------------------------
# Static UI
# ---------------------------------------------------------------------------

_STATIC_DIR = Path(__file__).parent / "static"


def _mount_static(app: FastAPI) -> None:
    """Serve the single-page JIL UI at GET /ui and its static assets."""

    if not _STATIC_DIR.exists():
        logger.warning("Static directory not found: {}", _STATIC_DIR)
        return

    @app.get("/ui", tags=["ui"], include_in_schema=False)
    def serve_ui():
        return FileResponse(str(_STATIC_DIR / "index.html"))

    app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
