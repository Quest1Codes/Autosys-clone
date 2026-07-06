"""
JIL router — validate and import JIL content via REST.

POST /api/v1/jil/validate   Parse JIL text; return parsed jobs without DB writes.
POST /api/v1/jil/import     Parse JIL text and persist to the database (supports dry_run).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import (
    JILImportRequest, JILImportResponse, JILJobResult,
)
from autosys.db.repository      import jobs as job_repo, machines as machine_repo
from autosys.parser.jil_parser  import parse_jil, JILParseError

router = APIRouter(prefix="/api/v1/jil", tags=["jil"])


def _parse_or_error(content: str) -> tuple[list | None, str | None]:
    """Return (ops, None) on success, (None, error_message) on failure."""
    try:
        return parse_jil(content), None
    except JILParseError as exc:
        return None, str(exc)
    except Exception as exc:
        return None, f"Unexpected error: {exc}"


@router.post("/validate", response_model=JILImportResponse)
def validate_jil(
    body:  JILImportRequest,
    _user: CurrentUser = Depends(get_current_user),
) -> JILImportResponse:
    """
    Parse JIL text and return the list of recognised stanzas.
    Nothing is written to the database.
    """
    ops, err = _parse_or_error(body.content)
    if err:
        return JILImportResponse(success=False, error=err)

    results: list[JILJobResult] = []
    n_machines = 0
    for op in ops:
        if op.op == "insert_machine":
            n_machines += 1
            results.append(JILJobResult(
                action="MACHINE",
                name=op.machine.machine_name,
                type=f"port:{op.machine.port}",
            ))
        else:
            results.append(JILJobResult(
                action="OK",
                name=op.job.job_name,
                type=str(op.job.job_type),
            ))

    return JILImportResponse(
        success=True,
        jobs=results,
        n_machines=n_machines,
    )


@router.post("/import", response_model=JILImportResponse)
def import_jil(
    body:    JILImportRequest,
    session: Session     = Depends(get_session),
    user:    CurrentUser = Depends(get_current_user),
) -> JILImportResponse:
    """
    Parse JIL text and persist stanzas to the database.

    Pass ``dry_run: true`` to parse and validate without writing anything.
    """
    user.require_role("operator", "admin")

    ops, err = _parse_or_error(body.content)
    if err:
        return JILImportResponse(success=False, error=err)

    n_inserted = n_updated = n_deleted = n_machines = 0
    results: list[JILJobResult] = []

    for op in ops:
        if op.op == "insert_machine":
            if not body.dry_run:
                machine_repo.register(
                    session,
                    machine_name=op.machine.machine_name,
                    host=op.machine.host or op.machine.machine_name,
                    port=op.machine.port,
                )
            n_machines += 1
            results.append(JILJobResult(
                action="MACHINE",
                name=op.machine.machine_name,
                type=f"port:{op.machine.port}",
            ))
            continue

        job  = op.job
        name = job.job_name
        jtype = str(job.job_type)

        if op.op == "delete":
            if not body.dry_run:
                job_repo.delete(session, name)
            action = "DELETED"
            n_deleted += 1
        else:
            if body.dry_run:
                action = "OK"
            else:
                result = job_repo.upsert(session, job)
                action = result.upper()
                if action == "INSERTED":
                    n_inserted += 1
                else:
                    n_updated += 1

        results.append(JILJobResult(action=action, name=name, type=jtype))

    return JILImportResponse(
        success=True,
        jobs=results,
        n_inserted=n_inserted,
        n_updated=n_updated,
        n_deleted=n_deleted,
        n_machines=n_machines,
    )
