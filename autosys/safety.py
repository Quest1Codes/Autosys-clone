"""
Real-execution gate.

Real execution means the agent forks a job's ``command:`` through a shell,
opens FTP/SSH/HTTP connections, sends wake-on-LAN packets or watches real
paths -- against whatever hosts and commands the imported JIL names. On an
estate imported from a client's production export those are their real
machines, so nothing executes unless AUTOSYS_ALLOW_REAL_EXECUTION=true.

The check lives at the points where execution actually happens (the runners,
the runner factory, the dispatchers and the agent listener), not only at one
entry point: audit SEC-01 found `scheduler start`, `agent start`,
`agent run-once` and `agent serve` all executing imported commands with the
variable unset, because the original check was only in `scheduler serve`.

Kept free of heavy imports so the agent package can use it.
"""
from __future__ import annotations

import os

#: Env var that must be explicitly true before anything executes. Deliberately
#: has no safe-looking default.
REAL_EXECUTION_ENV_VAR = "AUTOSYS_ALLOW_REAL_EXECUTION"


class RealExecutionRefused(RuntimeError):
    """Raised when something tries to execute without the opt-in."""


def real_execution_allowed() -> bool:
    """True only when the operator has explicitly opted in."""
    return os.environ.get(REAL_EXECUTION_ENV_VAR, "").strip().lower() == "true"


def real_execution_error() -> str | None:
    """Problem string if real execution is not permitted, else None."""
    if real_execution_allowed():
        return None
    return (
        f"{REAL_EXECUTION_ENV_VAR} must be 'true' to run without --dry-run -- "
        "this process refuses to dispatch real commands to real machines "
        "unless that is opted into explicitly."
    )


def require_real_execution(what: str) -> None:
    """Raise RealExecutionRefused unless real execution is opted into."""
    problem = real_execution_error()
    if problem:
        raise RealExecutionRefused(f"{what}: {problem}")
