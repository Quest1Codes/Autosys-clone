"""
WCC — Workload Control Centre web dashboard (Phase 9).

A separate FastAPI application served on port 8080.  Renders Jinja2 HTML
templates backed by direct DB access (same SQLite as the SSA).

Routes
------
GET  /                     Job grid — all jobs with live status
GET  /jobs/{name}          Job detail — run history + stdout viewer
GET  /boxes/{name}         Dependency flow graph (D3.js)
GET  /alarms               Alarm console — active/resolved alarms
GET  /api/sse/jobs         Server-Sent Events — live job status stream
GET  /api/wcc/jobs         JSON — job list (used by SSE + D3 refresh)
GET  /api/wcc/boxes/{name} JSON — box + children (used by D3 graph)
GET  /api/wcc/runs         JSON — run history
GET  /api/wcc/alarms       JSON — alarm list
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Optional, AsyncGenerator

from fastapi import FastAPI, Request, Query
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select

from autosys.db.connection import sync_session
from autosys.db.schema import JobRow, JobRunRow, AlarmRow, JobOutputRow

_HERE       = Path(__file__).parent
_STATIC_DIR = _HERE / "static"
_TPL_DIR    = _HERE / "templates"

templates = Jinja2Templates(directory=str(_TPL_DIR))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STATUS_CLASS = {
    "SUCCESS":    "status-success",
    "FAILURE":    "status-failure",
    "RUNNING":    "status-running",
    "STARTING":   "status-starting",
    "ACTIVATED":  "status-activated",
    "TERMINATED": "status-terminated",
    "ON_HOLD":    "status-hold",
    "ON_ICE":     "status-ice",
    "INACTIVE":   "status-inactive",
}


def _status_cls(status: str) -> str:
    return _STATUS_CLASS.get(status or "INACTIVE", "status-inactive")


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
    return {
        "job_name":   row.job_name,
        "job_type":   row.job_type,
        "status":     row.status or "INACTIVE",
        "status_cls": _status_cls(row.status),
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
        title       = "AutoSys WCC Dashboard",
        description = "Workload Control Centre — Phase 9",
        version     = "0.1.0",
    )

    if _STATIC_DIR.exists():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")

    # ── HTML pages ───────────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    def job_grid(
        request: Request,
        status:  Optional[str] = Query(None),
        machine: Optional[str] = Query(None),
        owner:   Optional[str] = Query(None),
        q:       Optional[str] = Query(None),
    ):
        with sync_session() as session:
            rows = session.execute(
                select(JobRow).order_by(JobRow.job_name)
            ).scalars().all()

        jobs = [_job_dict(r) for r in rows]

        if status:
            jobs = [j for j in jobs if j["status"] == status.upper()]
        if machine:
            jobs = [j for j in jobs if machine.lower() in j["machine"].lower()]
        if owner:
            jobs = [j for j in jobs if owner.lower() in j["owner"].lower()]
        if q:
            jobs = [j for j in jobs if q.lower() in j["job_name"].lower()]

        status_counts: dict[str, int] = {}
        for j in jobs:
            status_counts[j["status"]] = status_counts.get(j["status"], 0) + 1

        return templates.TemplateResponse(request, "jobs.html", {
            "jobs":          jobs,
            "status_counts": status_counts,
            "filter_status": status or "",
            "filter_machine": machine or "",
            "filter_owner":  owner or "",
            "filter_q":      q or "",
            "total":         len(jobs),
        })

    @app.get("/jobs/{name}", response_class=HTMLResponse)
    def job_detail(request: Request, name: str, run_limit: int = Query(20)):
        with sync_session() as session:
            job = session.get(JobRow, name)
            if job is None:
                return HTMLResponse(f"<h1>Job '{name}' not found</h1>", status_code=404)

            runs = session.execute(
                select(JobRunRow)
                .where(JobRunRow.job_name == name)
                .order_by(JobRunRow.start_time.desc())
                .limit(run_limit)
            ).scalars().all()

            children = session.execute(
                select(JobRow).where(JobRow.box_name == name)
            ).scalars().all()

        def run_dict(r: JobRunRow) -> dict:
            dur = None
            if r.start_time and r.end_time:
                dur = int((r.end_time - r.start_time).total_seconds())
            return {
                "run_id":     r.run_id,
                "status":     r.status or "RUNNING",
                "status_cls": _status_cls(r.status),
                "exit_code":  r.exit_code,
                "machine":    r.machine or "—",
                "run_date":   r.run_date or "—",
                "start_time": _fmt_dt(r.start_time),
                "end_time":   _fmt_dt(r.end_time),
                "duration":   f"{dur}s" if dur is not None else "—",
            }

        return templates.TemplateResponse(request, "job_detail.html", {
            "job":      _job_dict(job),
            "runs":     [run_dict(r) for r in runs],
            "children": [_job_dict(c) for c in children],
            "is_box":   job.job_type == "BOX",
        })

    @app.get("/boxes/{name}", response_class=HTMLResponse)
    def box_graph(request: Request, name: str):
        with sync_session() as session:
            box = session.get(JobRow, name)
            if box is None:
                return HTMLResponse(f"<h1>Box '{name}' not found</h1>", status_code=404)
            children = session.execute(
                select(JobRow).where(JobRow.box_name == name)
            ).scalars().all()

        all_jobs = [box] + list(children)
        nodes = [
            {
                "id":     j.job_name,
                "type":   j.job_type,
                "status": j.status or "INACTIVE",
            }
            for j in all_jobs
        ]
        edges = []
        for j in all_jobs:
            for dep in _extract_deps(j.condition):
                if any(n["id"] == dep for n in nodes):
                    edges.append({"source": dep, "target": j.job_name})

        return templates.TemplateResponse(request, "box_graph.html", {
            "box_name":  name,
            "box_status": box.status or "INACTIVE",
            "nodes_json": json.dumps(nodes),
            "edges_json": json.dumps(edges),
        })

    @app.get("/alarms", response_class=HTMLResponse)
    def alarm_console(
        request: Request,
        active:  Optional[str] = Query(None),
    ):
        with sync_session() as session:
            stmt = select(AlarmRow).order_by(AlarmRow.raised_at.desc()).limit(200)
            if active == "true":
                stmt = stmt.where(AlarmRow.cleared_at.is_(None))
            elif active == "false":
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
                "cleared_by": r.cleared_by or "—",
                "active":     r.cleared_at is None,
            }

        alarms = [alarm_dict(r) for r in rows]
        return templates.TemplateResponse(request, "alarms.html", {
            "alarms":       alarms,
            "n_active":     sum(1 for a in alarms if a["active"]),
            "filter_active": active or "",
        })

    # ── JSON data API (used by templates + SSE) ───────────────────────────────

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
        nodes = [{"id": j.job_name, "type": j.job_type, "status": j.status or "INACTIVE"} for j in all_jobs]
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
                    {"job_name": r.job_name, "status": r.status or "INACTIVE"}
                    for r in rows
                ])
                yield f"data: {payload}\n\n"
                await asyncio.sleep(2)

        return StreamingResponse(
            event_generator(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app


# ---------------------------------------------------------------------------
# Direct run (python -m autosys.wcc.app)
# ---------------------------------------------------------------------------

app = create_wcc_app()
