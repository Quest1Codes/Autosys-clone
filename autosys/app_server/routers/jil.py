"""
JIL router — validate and import JIL content via REST.

POST /api/v1/jil/validate   Parse JIL text; return parsed jobs without DB writes.
POST /api/v1/jil/import     Parse JIL text and persist to the database (supports dry_run).

Both routes go through the same tolerant, lossless ingester `autosys jil
import` / `jil import-dir` use (``autosys.parser.jil_ingest.ingest_text``):
a malformed stanza is quarantined and reported, not a whole-request failure,
and everything /import actually writes is archived verbatim in
``ujo_jil_file`` / ``ujo_jil_stanza`` — the same guarantee the CLI path has.
This also means the API now persists every sub-command the CLI does
(resources, monitors, blobs, job types, xinsts, connection profiles), not
just jobs/machines/globs/calendars as it did before: that restriction was
an artifact of this router's own older, hand-rolled logic, not a deliberate
scope limit, and kept it inconsistent with `jil import`.
"""
from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from autosys.analysis import simulated_risk
from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import (
    JILImportRequest, JILImportResponse, JILJobResult,
)
from autosys.parser.jil_parser  import JILParser
from autosys.parser.jil_ingest  import ingest_text

router = APIRouter(prefix="/api/v1/jil", tags=["jil"])


def _parse_or_error(content: str) -> str | None:
    """
    ``None`` normally; an error message when NOTHING in ``content`` reads as
    JIL at all.

    ``ingest_text`` itself never fails a request over content — a malformed
    stanza is just quarantined and reported. This pre-check exists only to
    keep this endpoint's older contract for a caller that posts pure garbage:
    a clean success=False rather than a response whose only content is one
    QUARANTINED row.
    """
    try:
        ops = JILParser().parse_text(content, tolerant=True)
    except Exception as exc:                 # pragma: no cover — tolerant mode does not raise
        return f"Unexpected error: {exc}"
    if ops and all(op.op == "raw" for op in ops):
        msgs = "; ".join(i["message"] for op in ops for i in op.issues)
        return msgs or "No JIL statements recognised"
    return None


def _synthetic_path(kind: str, user: CurrentUser) -> str:
    """A unique, informative path for this call's archive rows (there is no
    real file — the caller posted a string)."""
    who = getattr(user, "username", None) or "unknown"
    return f"api:{kind}:{who}:{uuid.uuid4().hex}"


def _to_response(report, **counters) -> JILImportResponse:
    jobs = [JILJobResult(action=action, name=name or "?", type=detail or "")
            for action, name, detail in report.results]
    return JILImportResponse(
        success=True, jobs=jobs,
        n_quarantined=report.dispositions.get("QUARANTINED", 0),
        n_warnings=report.dispositions.get("LOADED_WITH_WARNINGS", 0),
        n_failed=report.counters.get("failed", 0),
        **counters,
    )


@router.post("/validate", response_model=JILImportResponse)
def validate_jil(
    body:    JILImportRequest,
    session: Session     = Depends(get_session),
    user:    CurrentUser = Depends(get_current_user),
) -> JILImportResponse:
    """
    Parse JIL text and return the list of recognised stanzas.
    Nothing is written to the database.
    """
    err = _parse_or_error(body.content)
    if err:
        return JILImportResponse(success=False, error=err)

    # read_files=False: a blob_file path names a file on THIS server, never
    # the caller's (audit SEC-05).
    report = ingest_text(session, body.content, _synthetic_path("validate", user),
                         dry_run=True, read_files=False)
    return _to_response(report, n_machines=report.counters.get("machines", 0))


@router.post("/import", response_model=JILImportResponse)
def import_jil(
    body:    JILImportRequest,
    request: Request,
    session: Session     = Depends(get_session),
    user:    CurrentUser = Depends(get_current_user),
) -> JILImportResponse:
    """
    Parse JIL text and persist stanzas to the database.

    Pass ``dry_run: true`` to parse and validate without writing (or
    archiving) anything.
    """
    user.require_role("operator", "admin")

    err = _parse_or_error(body.content)
    if err:
        return JILImportResponse(success=False, error=err)

    report = ingest_text(session, body.content, _synthetic_path("import", user),
                         dry_run=body.dry_run, read_files=False)
    n_inserted = report.counters.get("inserted", 0)
    n_updated  = report.counters.get("updated", 0)
    n_deleted  = report.counters.get("deleted", 0)
    n_machines = report.counters.get("machines", 0)

    # New/changed job definitions have no run history yet — start simulating it in
    # the background so the assessment report has risk data by the time it is asked
    # for (see analysis/simulated_risk.py). Debounced: a bulk import is one call
    # per file. Only in dry-run mode; real mode never mixes in simulated history.
    if (
        not body.dry_run
        and (n_inserted or n_updated or n_deleted)
        and getattr(request.app.state, "dry_run", True)
        and simulated_risk.simulation_enabled()
    ):
        simulated_risk.schedule_warm()

    return _to_response(report, n_inserted=n_inserted, n_updated=n_updated,
                        n_deleted=n_deleted, n_machines=n_machines)
