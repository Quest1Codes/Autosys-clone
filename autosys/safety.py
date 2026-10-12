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
from pathlib import Path

#: Env var that must be explicitly true before anything executes. Deliberately
#: has no safe-looking default.
REAL_EXECUTION_ENV_VAR = "AUTOSYS_ALLOW_REAL_EXECUTION"


class RealExecutionRefused(RuntimeError):
    """Raised when something tries to execute without the opt-in."""


#: True in a DRY_RUN_ONLY image build, which deletes the real-execution
#: modules (see the Dockerfile). Nothing can execute there, whatever the env.
# A file check, not importlib.util.find_spec: find_spec imports the parent
# package (autosys.agent), which imports modules that import this one.
DRY_RUN_ONLY_BUILD = not (Path(__file__).parent / "agent" / "runner.py").exists()


def real_execution_allowed() -> bool:
    """True only when the operator has explicitly opted in."""
    if DRY_RUN_ONLY_BUILD:
        return False
    return os.environ.get(REAL_EXECUTION_ENV_VAR, "").strip().lower() == "true"


def real_execution_error() -> str | None:
    """Problem string if real execution is not permitted, else None."""
    if real_execution_allowed():
        return None
    if DRY_RUN_ONLY_BUILD:
        return (
            "this is a dry-run-only build: the real-execution code is not "
            "installed, so only --dry-run is available."
        )
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
