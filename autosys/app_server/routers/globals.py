"""
Global variables router — CRUD for %%VARIABLE%% definitions.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import GlobalVarResponse, SetGlobalRequest
from autosys.db.repository      import globs as glob_repo
from autosys.db.schema          import GlobalVariableRow as GlobalVarRow

router = APIRouter(prefix="/api/v1/globals", tags=["globals"])


def _row_to_resp(row: GlobalVarRow) -> GlobalVarResponse:
    return GlobalVarResponse(
        name  = row.name,
        value = row.value or "",
    )


@router.get("", response_model=list[GlobalVarResponse])
def list_globals(
    session: Session     = Depends(get_session),
    _user:   CurrentUser = Depends(get_current_user),
):
    """List all global variables."""
    from sqlalchemy import select
    rows = session.scalars(select(GlobalVarRow).order_by(GlobalVarRow.name)).all()
    return [_row_to_resp(r) for r in rows]


@router.get("/{name}", response_model=GlobalVarResponse)
def get_global(
    name:    str,
    session: Session     = Depends(get_session),
    _user:   CurrentUser = Depends(get_current_user),
):
    """Get a single global variable."""
    row = session.get(GlobalVarRow, name.upper())
    if row is None:
        raise HTTPException(status_code=404, detail=f"Global variable '{name}' not found")
    return _row_to_resp(row)


@router.put("/{name}", response_model=GlobalVarResponse)
def set_global(
    name:    str,
    body:    SetGlobalRequest,
    session: Session     = Depends(get_session),
    user:    CurrentUser = Depends(get_current_user),
):
    """Create or update a global variable."""
    user.require_role("operator", "admin")
    upper = name.upper()
    glob_repo.set(session, upper, body.value)
    session.flush()   # ensure the new/updated row is visible in this session
    row = session.get(GlobalVarRow, upper)
    return _row_to_resp(row)


@router.delete("/{name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_global(
    name:    str,
    session: Session     = Depends(get_session),
    user:    CurrentUser = Depends(get_current_user),
):
    """Delete a global variable."""
    user.require_role("admin")
    row = session.get(GlobalVarRow, name.upper())
    if row is None:
        raise HTTPException(status_code=404, detail=f"Global variable '{name}' not found")
    session.delete(row)
