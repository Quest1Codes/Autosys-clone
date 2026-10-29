"""
Estate router — delete the entire imported job estate.

DELETE /api/v1/estate   Remove every job definition, and everything that
                        depends on one (run history, alarms, output).

The jobs router (routers/jobs.py) deliberately has no DELETE route at all --
see its module docstring: "deleting an already-imported job definition is a
different class of action from running it." This is that different class of
action, made its own obvious, separately-gated endpoint rather than folded
quietly into jobs.py against that router's own documented decision.

Deliberately stricter than JIL import (which only needs "operator"): wiping
every job definition in one call is the single most destructive action in
this API, so it requires "admin" specifically, and the caller must echo back
the exact current job count as `confirm_job_count` -- not just a boolean
flag, which a stale UI state or a replayed request could set without the
caller actually knowing what they are about to destroy. A mismatch is
refused with the real count, so the caller is always working from a number
they just confirmed, not one they assumed.

Recoverable, not just destructive: implemented via jil_ingest.
remove_jobs_not_in(session, present=set()) -- the exact same mechanism
`jil import --replace-estate` already uses to remove jobs missing from a
new export, just with an empty "present" set so every job qualifies. That
function archives each job's last definition as JIL to a
`reconcile:<timestamp>` file before removing it, so the estate can be
restored by re-importing that archive -- the same safety property a
replace-estate import already has, extended to this action for free.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import EstateClearResponse
from autosys.db.repository      import jobs as job_repo
from autosys.parser.jil_ingest  import remove_jobs_not_in

router = APIRouter(prefix="/api/v1/estate", tags=["estate"])


@router.delete("", response_model=EstateClearResponse)
def clear_estate(
    confirm_job_count: int = Query(..., description=(
        "Must exactly equal the estate's current job count -- GET /api/v1/jobs "
        "to read it first. A mismatch is refused with the real count, so this "
        "can't be fired against a stale assumption of estate size."
    )),
    session: Session     = Depends(get_session),
    user:    CurrentUser = Depends(get_current_user),
) -> EstateClearResponse:
    """
    Delete every job definition and everything that depends on one (run
    history, alarms, captured output) -- see the module docstring for why
    this is its own endpoint, its own role requirement, and how it stays
    recoverable.
    """
    user.require_role("admin")

    actual = len(job_repo.list_all(session))
    if confirm_job_count != actual:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(f"confirm_job_count={confirm_job_count} does not match the estate's "
                    f"actual job count ({actual}) -- re-check and resend with the real count."),
        )
    if actual == 0:
        return EstateClearResponse(jobs_removed=0, jobs_failed=0, archive_path="")

    removed = remove_jobs_not_in(session, present=set())
    session.commit()

    return EstateClearResponse(
        jobs_removed=len(removed),
        jobs_failed=actual - len(removed),
        archive_path="",  # the reconcile:<timestamp> JilFileRow.path is logged server-side;
                          # not round-tripped here to keep this response minimal.
    )
