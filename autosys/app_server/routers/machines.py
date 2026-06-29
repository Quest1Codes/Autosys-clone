"""
Machines router — CRUD for registered System Agent machines.
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from autosys.app_server.deps    import get_session, get_current_user, CurrentUser
from autosys.app_server.schemas import MachineResponse, RegisterMachineRequest
from autosys.db.repository      import machines as machine_repo

router = APIRouter(prefix="/api/v1/machines", tags=["machines"])


def _row_to_resp(row) -> MachineResponse:
    return MachineResponse(
        machine_name   = row.machine_name,
        host           = row.host,
        port           = row.port,
        status         = row.status or "UNKNOWN",
        last_heartbeat = row.last_heartbeat,
        description    = row.description,
    )


@router.get("", response_model=list[MachineResponse])
def list_machines(
    session: Session     = Depends(get_session),
    _user:   CurrentUser = Depends(get_current_user),
):
    """List all registered machines."""
    rows = machine_repo.list_all(session)
    return [_row_to_resp(r) for r in rows]


@router.get("/{machine_name}", response_model=MachineResponse)
def get_machine(
    machine_name: str,
    session:      Session     = Depends(get_session),
    _user:        CurrentUser = Depends(get_current_user),
):
    """Get a single machine by name."""
    row = machine_repo.get(session, machine_name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_name}' not found")
    return _row_to_resp(row)


@router.post("", response_model=MachineResponse, status_code=status.HTTP_201_CREATED)
def register_machine(
    body:    RegisterMachineRequest,
    session: Session     = Depends(get_session),
    user:    CurrentUser = Depends(get_current_user),
):
    """Register or update a machine."""
    user.require_role("operator", "admin")
    row = machine_repo.register(
        session,
        machine_name = body.machine_name,
        host         = body.host,
        port         = body.port,
        description  = body.description,
    )
    return _row_to_resp(row)


@router.delete("/{machine_name}", status_code=status.HTTP_204_NO_CONTENT)
def delete_machine(
    machine_name: str,
    session:      Session     = Depends(get_session),
    user:         CurrentUser = Depends(get_current_user),
):
    """Deregister a machine."""
    user.require_role("admin")
    from autosys.db.schema import MachineRow
    row = machine_repo.get(session, machine_name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_name}' not found")
    session.delete(row)


@router.post("/{machine_name}/heartbeat", response_model=MachineResponse)
def check_heartbeat(
    machine_name: str,
    session:      Session     = Depends(get_session),
    user:         CurrentUser = Depends(get_current_user),
):
    """
    Trigger an immediate heartbeat check for a machine.

    Attempts to connect to the agent's TCP server and returns the updated
    machine status (UP or DOWN).
    """
    user.require_role("operator", "admin")
    row = machine_repo.get(session, machine_name)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Machine '{machine_name}' not found")

    from autosys.agent.remote import RemoteDispatch
    rd    = RemoteDispatch()
    alive = rd.heartbeat(row)
    machine_repo.update_heartbeat(session, machine_name, status="UP" if alive else "DOWN")
    # Re-fetch after update
    row = machine_repo.get(session, machine_name)
    return _row_to_resp(row)
