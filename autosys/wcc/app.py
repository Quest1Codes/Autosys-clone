"""
WCC — Workload Control Centre API server (port 8080).

Serves JSON API endpoints and SSE live updates consumed by the React
frontend in wcc-frontend/.  All HTML rendering has been removed.

Routes
------
GET  /api/wcc/jobs              JSON — full job list
GET  /api/wcc/jobs/{name}       JSON — single job detail + runs + children
GET  /api/wcc/boxes/{name}      JSON — box graph nodes + edges
GET  /api/wcc/runs              JSON — run history (optional ?job= filter)
GET  /api/wcc/alarms            JSON — alarm list (optional ?active= filter)
GET  /api/wcc/runs/{id}/output  JSON — stdout/stderr lines for a run
GET  /api/sse/jobs              SSE  — live job status stream (poll every 2s)
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, AsyncGenerator

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from autosys.db.connection import sync_session
from autosys.db.schema import JobRow, JobRunRow, AlarmRow, JobOutputRow
from autosys.models.job import JobStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _status_name(val) -> str:
    """Convert integer or string DB status to canonical name string."""
    if isinstance(val, int):
        try:
            return JobStatus(val).name
        except ValueError:
            return "INACTIVE"
    return str(val) if val else "INACTIVE"


def _extract_deps(condition: str | None) -> list[str]:
    """Return job names referenced in a condition expression."""
    if not condition:
        return []
    return re.findall(r'(?:success|failure|done|notrunning|terminated|activated|[sfdnta])\((\w+)\)', condition)


def _fmt_dt(dt: datetime | None) -> str:
    if dt is None:
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _job_dict(row: JobRow) -> dict:
    sname = _status_name(row.status)
    return {
        "job_name":   row.job_name,
        "job_type":   row.job_type,
        "status":     sname,
        "status_cls": f"status-{sname.lower()}",
        "machine":    row.machine or "—",
        "box_name":   row.box_name,
        "owner":      row.owner or "—",
        "last_start": _fmt_dt(row.last_start),
        "last_end":   _fmt_dt(row.last_end),
        "condition":  row.condition,
    }


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_wcc_app() -> FastAPI:
    app = FastAPI(
        title       = "AutoSys WCC API",
        description = "Workload Control Centre — JSON API + SSE for wcc-frontend",
        version     = "0.2.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # ── JSON data API ─────────────────────────────────────────────────────────

    @app.get("/api/wcc/jobs")
    def api_jobs(status: Optional[str] = Query(None)):
        with sync_session() as session:
            rows = session.execute(
                select(JobRow).order_by(JobRow.job_name)
            ).scalars().all()
        jobs = [_job_dict(r) for r in rows]
        if status:
            jobs = [j for j in jobs if j["status"] == status.upper()]
        return {"jobs": jobs, "total": len(jobs)}

    @app.get("/api/wcc/jobs/{name}")
    def api_job_detail(name: str):
        with sync_session() as session:
            row = session.get(JobRow, name)
            if row is None:
                return JSONResponse(status_code=404, content={"error": f"Job '{name}' not found"})
            runs = session.execute(
                select(JobRunRow)
                .where(JobRunRow.job_name == name)
                .order_by(JobRunRow.start_time.desc())
                .limit(20)
            ).scalars().all()
            children = []
            if row.job_type == "BOX":
                children = session.execute(
                    select(JobRow).where(JobRow.box_name == name)
                ).scalars().all()

        job = _job_dict(row)
        job["runs"] = [
            {
                "run_id":     r.run_id,
                "status":     r.status or "RUNNING",
                "exit_code":  r.exit_code,
                "machine":    r.machine,
                "start_time": _fmt_dt(r.start_time),
                "end_time":   _fmt_dt(r.end_time),
            }
            for r in runs
        ]
        job["children"] = [
            {"job_name": c.job_name, "job_type": c.job_type, "status": _status_name(c.status)}
            for c in children
        ]
        return job

    @app.get("/api/wcc/boxes/{name}")
    def api_box(name: str):
        with sync_session() as session:
            box = session.get(JobRow, name)
            if box is None:
                return {"error": f"Box '{name}' not found"}
            children = session.execute(
                select(JobRow).where(JobRow.box_name == name)
            ).scalars().all()

        all_jobs = [box] + list(children)
        nodes = [{"id": j.job_name, "type": j.job_type, "status": _status_name(j.status)} for j in all_jobs]
        edges = []
        for j in all_jobs:
            for dep in _extract_deps(j.condition):
                if any(n["id"] == dep for n in nodes):
                    edges.append({"source": dep, "target": j.job_name})
        return {"nodes": nodes, "edges": edges}

    @app.get("/api/wcc/runs")
    def api_runs(job: Optional[str] = Query(None), limit: int = Query(20, le=200)):
        with sync_session() as session:
            stmt = select(JobRunRow).order_by(JobRunRow.start_time.desc()).limit(limit)
            if job:
                stmt = stmt.where(JobRunRow.job_name == job)
            rows = session.execute(stmt).scalars().all()

        def run_dict(r: JobRunRow) -> dict:
            dur = None
            if r.start_time and r.end_time:
                dur = int((r.end_time - r.start_time).total_seconds())
            return {
                "run_id":     r.run_id,
                "job_name":   r.job_name,
                "status":     r.status or "RUNNING",
                "exit_code":  r.exit_code,
                "machine":    r.machine,
                "run_date":   r.run_date,
                "start_time": _fmt_dt(r.start_time),
                "end_time":   _fmt_dt(r.end_time),
                "duration_s": dur,
            }

        return {"runs": [run_dict(r) for r in rows]}

    @app.get("/api/wcc/alarms")
    def api_alarms(active: Optional[bool] = Query(None)):
        with sync_session() as session:
            stmt = select(AlarmRow).order_by(AlarmRow.raised_at.desc()).limit(200)
            if active is True:
                stmt = stmt.where(AlarmRow.cleared_at.is_(None))
            elif active is False:
                stmt = stmt.where(AlarmRow.cleared_at.is_not(None))
            rows = session.execute(stmt).scalars().all()

        def alarm_dict(r: AlarmRow) -> dict:
            return {
                "alarm_id":   r.alarm_id,
                "job_name":   r.job_name,
                "alarm_type": r.alarm_type,
                "message":    r.message,
                "raised_at":  _fmt_dt(r.raised_at),
                "cleared_at": _fmt_dt(r.cleared_at) if r.cleared_at else None,
                "active":     r.cleared_at is None,
            }

        alarms = [alarm_dict(r) for r in rows]
        return {"alarms": alarms, "n_active": sum(1 for a in alarms if a["active"])}

    @app.get("/api/wcc/runs/{run_id}/output")
    def api_run_output(run_id: str, offset: int = Query(0), limit: int = Query(500, le=5000)):
        with sync_session() as session:
            rows = session.execute(
                select(JobOutputRow)
                .where(JobOutputRow.run_id == run_id)
                .order_by(JobOutputRow.line_no)
                .offset(offset)
                .limit(limit)
            ).scalars().all()
        return {"lines": [{"seq": r.line_no, "line": r.content} for r in rows]}

    # ── SSE live job status stream ────────────────────────────────────────────

    @app.get("/api/sse/jobs")
    async def sse_jobs():
        """
        Server-Sent Events endpoint.  Polls the DB every 2 seconds and pushes
        the full job status snapshot to the browser.
        """
        async def event_generator() -> AsyncGenerator[str, None]:
            while True:
                with sync_session() as session:
                    rows = session.execute(
                        select(JobRow).order_by(JobRow.job_name)
                    ).scalars().all()
                payload = json.dumps([
                    {"job_name": r.job_name, "status": _status_name(r.status)}
                    for r in rows
                ])
                yield f"data: {payload}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    # ── Serve React frontend (if built) ──────────────────────────────────────

    _frontend_dist = Path(__file__).parents[2] / "wcc-frontend" / "dist"
    if _frontend_dist.exists():
        app.mount("/assets", StaticFiles(directory=_frontend_dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa_fallback(full_path: str):
            """Serve the React SPA for any non-API route."""
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"error": "Not found"})
            index = _frontend_dist / "index.html"
            if index.exists():
                return FileResponse(str(index))
            return HTMLResponse(status_code=404, content="<h1>Frontend not built. Run: cd wcc-frontend && npm run build</h1>")

    return app


# ---------------------------------------------------------------------------
# Direct run (python -m autosys.wcc.app)
# ---------------------------------------------------------------------------

app = create_wcc_app()
