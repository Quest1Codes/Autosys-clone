"""
AutoSys Application Server (SSA) — FastAPI entry point.

Responsibilities
----------------
1. Mount all REST routers under /api/v1/
2. Expose /api/v1/auth/{login,callback,logout,me} (Cognito OIDC sign-in)
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
import hmac
import os
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Depends, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from loguru import logger

from autosys.analysis import simulated_risk
from autosys.app_server.broadcaster import EventBroadcaster
from autosys.app_server.deps        import (
    get_session, get_current_user, user_from_m2m_token, user_from_session_cookie, CurrentUser,
)
from autosys.app_server.schemas     import HealthResponse, ExecutionModeResponse
from autosys.app_server.routers     import jobs, events, runs, machines, globals as globals_router, jil as jil_router, alarms as alarms_router, assessment, metrics as metrics_router, history as history_router, estate as estate_router
from autosys.db.connection          import sync_session


# ---------------------------------------------------------------------------
# Module-level broadcaster singleton
# ---------------------------------------------------------------------------

_broadcaster = EventBroadcaster()


def get_broadcaster() -> EventBroadcaster:
    """FastAPI dependency — returns the module-level broadcaster."""
    return _broadcaster


# ---------------------------------------------------------------------------
# Real-execution opt-in
# ---------------------------------------------------------------------------

# Defined in autosys.safety, which the agent runners also enforce; re-exported
# here because cli/scheduler_cmd.py and existing callers import them from main.
from autosys.safety import (  # noqa: E402, F401  (re-exported)
    DRY_RUN_ONLY_BUILD, REAL_EXECUTION_ENV_VAR, real_execution_allowed, real_execution_error,
)


# ---------------------------------------------------------------------------
# EPS processor construction — shared by startup and the live mode toggle
# ---------------------------------------------------------------------------

def _build_eps_processor(dry_run: bool, eps_poll_interval: float):
    """
    Build an ``EventProcessor`` wired for either dry-run (stub dispatcher,
    instant completion, failure injection + alarms) or real execution
    (``AgentDispatch``, real subprocess dispatch). Used by ``create_app``
    startup.

    Raises ``RuntimeError`` if real execution is requested without the
    ``AUTOSYS_ALLOW_REAL_EXECUTION`` opt-in. The CLI checks the same condition
    up front (``cli/scheduler_cmd.py``) so the normal failure is a clean
    "Refusing to start"; this is the backstop for any other caller of
    ``create_app``.
    """
    from autosys.scheduler.event_processor import EventProcessor, _stub_dispatch
    if not dry_run:
        problem = real_execution_error()
        if problem:
            raise RuntimeError(problem)
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
    # Defaults to dry-run: the unsafe mode has to be asked for by name. This
    # also matches what routers/assessment.py already assumes when the
    # attribute is missing (``getattr(app.state, "dry_run", True)``).
    dry_run: bool = True,
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
            # gather(return_exceptions=True) absorbs the CancelledError of the
            # task we just cancelled ourselves, but still propagates it if
            # *this* coroutine is being cancelled -- a bare
            # `except CancelledError: pass` swallowed both.
            await asyncio.gather(eps_task, return_exceptions=True)
            logger.info("Event Processor background task stopped")

        logger.info("AutoSys App Server stopped")

    app = FastAPI(
        title       = "AutoSys Clone — Application Server",
        description = "REST API for the AutoSys workload scheduler clone.",
        version     = "0.1.0",
        lifespan    = lifespan,
        # Interactive docs can invoke PUT/POST/DELETE endpoints straight from
        # a browser form. No client-facing deployment of this server has any
        # legitimate reason to expose that.
        docs_url    = None,
        redoc_url   = None,
        openapi_url = None,
    )

    # CORS — no browser code needs to reach this API cross-origin: the WCC
    # frontend is served by nginx and calls it via a same-origin relative
    # path (/api/v1/..., see nginx.conf), and Shinro's tool-execution worker
    # calls it server-to-server, which CORS does not govern at all. Default
    # is therefore "no origins allowed"; a deployment that genuinely needs
    # browser JS to call this cross-origin sets AUTOSYS_CORS_ORIGINS to an
    # explicit comma-separated allowlist (e.g. WCC's own Ingress hostname).
    _cors_origins = [o.strip() for o in os.environ.get("AUTOSYS_CORS_ORIGINS", "").split(",") if o.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins     = _cors_origins,
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
    app.include_router(history_router.router)
    app.include_router(estate_router.router)
    app.include_router(alarms_router.router)
    app.include_router(assessment.router)
    app.include_router(metrics_router.router)

    # --- Auth ---
    _register_auth_routes(app)

    # --- WebSocket ---
    _register_ws_routes(app)

    # --- Health ---
    _register_health_routes(app)

    # --- Settings (execution-mode, read-only) ---
    _register_settings_routes(app)

    return app


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

def _only_same_origin_path(next_path: Optional[str]) -> str:
    """
    Keep ``?next=`` inside this app. Anything that isn't an absolute path
    starting with exactly one ``/`` is replaced with the WCC home page --
    this is the one input in the whole login round trip that arrives
    before any token has been checked, so it must not be trusted as a
    redirect target (an open redirect could point a signed-in session at
    an attacker's page immediately after a real Cognito login).
    """
    if next_path and next_path.startswith("/") and not next_path.startswith("//"):
        return next_path
    return "/"


def _register_auth_routes(app: FastAPI) -> None:

    @app.get("/api/v1/auth/login", tags=["auth"])
    def login(next: Optional[str] = None):
        """
        Start a Cognito sign-in. Mints state/nonce/PKCE values, stashes them
        in a short-lived signed cookie (this app keeps no server-side
        session store), and redirects the browser to Cognito's hosted UI.
        """
        import secrets as _secrets
        from autosys.app_server.auth import (
            authorization_url, encode_state_token, generate_pkce_pair, oidc_settings,
        )

        cfg = oidc_settings()
        state = _secrets.token_urlsafe(32)
        nonce = _secrets.token_urlsafe(32)
        code_verifier, code_challenge = generate_pkce_pair()
        redirect_uri = f"{cfg.public_url}/api/v1/auth/callback"

        response = RedirectResponse(
            authorization_url(
                cfg, redirect_uri=redirect_uri, state=state, nonce=nonce, code_challenge=code_challenge,
            )
        )
        response.set_cookie(
            "wcc_oidc",
            encode_state_token(
                state=state, nonce=nonce, code_verifier=code_verifier,
                next_path=_only_same_origin_path(next),
            ),
            max_age    = 600,
            httponly   = True,
            secure     = not cfg.public_url.startswith("http://localhost"),
            samesite   = "lax",
            path       = "/api/v1/auth",
        )
        return response

    @app.get("/api/v1/auth/callback", tags=["auth"])
    async def callback(request: Request, code: Optional[str] = None, state: Optional[str] = None, error: Optional[str] = None):
        """
        Finish the Cognito round trip: check state, exchange the code for
        tokens (PKCE, no client secret -- this is a public client), verify
        the ID token, and map the user's Cognito groups to a simulator
        role. No simulator group -> 403, not a session: this app's roles
        are opt-in per group, never implied by any other access the user
        might have.
        """
        from autosys.app_server.auth import (
            _JWT_TTL, decode_state_token, encode_session_token, exchange_code_for_tokens,
            oidc_settings, role_from_groups, verify_id_token,
        )

        if error or not code or not state:
            raise HTTPException(status_code=400, detail=f"Sign-in failed: {error or 'missing code/state'}")

        raw_state = request.cookies.get("wcc_oidc")
        if not raw_state:
            raise HTTPException(status_code=400, detail="Sign-in session expired -- please try again")
        try:
            saved = decode_state_token(raw_state)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Sign-in session invalid: {exc}")
        if not hmac.compare_digest(saved["state"], state):
            raise HTTPException(status_code=400, detail="State mismatch")

        cfg = oidc_settings()
        redirect_uri = f"{cfg.public_url}/api/v1/auth/callback"
        try:
            tokens = await exchange_code_for_tokens(
                cfg, code=code, redirect_uri=redirect_uri, code_verifier=saved["code_verifier"],
            )
            claims = verify_id_token(cfg, tokens["id_token"], expected_nonce=saved["nonce"])
        except Exception as exc:
            raise HTTPException(status_code=401, detail=f"Sign-in failed: {exc}")

        role = role_from_groups(claims.get("cognito:groups", []))
        if role is None:
            no_access_html = (
                "<h1>No simulator access</h1>"
                "<p>Your account signed in, but is not a member of any simulator group "
                "(simulator-admin, simulator-operator or simulator-viewer). "
                "Ask your administrator to add you to one.</p>"
            )
            response = HTMLResponse(status_code=403, content=no_access_html)
            response.delete_cookie("wcc_oidc", path="/api/v1/auth")
            return response

        username = claims.get("email") or claims.get("sub")
        response = RedirectResponse(saved["next"])
        response.set_cookie(
            "wcc_session",
            encode_session_token(username, role),
            max_age    = _JWT_TTL,
            httponly   = True,
            secure     = not cfg.public_url.startswith("http://localhost"),
            samesite   = "lax",
            path       = "/",
        )
        response.delete_cookie("wcc_oidc", path="/api/v1/auth")
        return response

    @app.post("/api/v1/auth/logout", tags=["auth"])
    def logout():
        """Clear the session cookie and hand back Cognito's own logout URL."""
        from autosys.app_server.auth import logout_url, oidc_settings

        cfg = oidc_settings()
        response = JSONResponse({"logout_url": logout_url(cfg, redirect_uri=cfg.public_url + "/")})
        response.delete_cookie("wcc_session", path="/")
        return response

    @app.get("/api/v1/auth/me", tags=["auth"])
    def me(user: CurrentUser = Depends(get_current_user)):
        """What the SPA shows instead of decoding a JWT client-side."""
        return {"username": user.username, "role": user.role}


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

        Connect with any WebSocket client, cookie jar attached::

            wscat --header "Cookie: wcc_session=<token>" -c "ws://localhost:9000/api/v1/ws/events"
        """
        # Same sign-in as the REST API (audit SEC-03): the wcc_session cookie
        # for a human, or a Bearer header for Shinro's worker. Rejected
        # before accepting -- no more ?token= (audit SEC-03 originally
        # closed that gap for query-string tokens; a session cookie in a URL
        # would be the same mistake over again).
        from autosys.app_server.auth import is_auth_enabled

        if is_auth_enabled():
            auth = ws.headers.get("authorization", "")
            bearer = auth[7:] if auth.lower().startswith("bearer ") else None
            try:
                if bearer:
                    user_from_m2m_token(bearer)
                else:
                    session = ws.cookies.get("wcc_session")
                    if not session:
                        raise HTTPException(status_code=401, detail="Not signed in")
                    user_from_session_cookie(session)
            except HTTPException:
                await ws.close(code=1008)   # policy violation
                return
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
# Settings — dry-run / real-run mode, read-only
# ---------------------------------------------------------------------------
#
# The mode is fixed for the lifetime of the process, chosen by create_app's
# own dry_run parameter (i.e. how "autosys scheduler serve" was invoked).
# There used to be a PUT here that let anyone flip a running server between
# dry-run and real execution over the network -- removed outright, not
# gated behind auth, because no client-facing deployment of this server
# should be able to do that at all. GET stays: it's just a read of which
# mode this process is running in, and the WCC header badge (and V3's
# planned estate-overview page) both want it.

def _register_settings_routes(app: FastAPI) -> None:

    @app.get("/api/v1/settings/execution-mode", response_model=ExecutionModeResponse, tags=["settings"])
    def get_execution_mode():
        return ExecutionModeResponse(
            dry_run=getattr(app.state, "dry_run", True),
            dry_run_only=DRY_RUN_ONLY_BUILD,
        )
