"""
AutoSys job state machine.

In real AutoSys, the Scheduler ACE enforces a strict finite-state machine (FSM)
for every job.  No component is allowed to write directly to the status column;
all transitions must go through the FSM.  This design guarantees audit-ability
and prevents impossible states like STARTING → SUCCESS (skipping RUNNING).

State diagram
-------------

                         ┌─────────────────────────────┐
                         │  INACTIVE (default / reset)  │
                         └──────────────┬───────────────┘
                 STARTJOB/time trigger  │
                 conditions met         │
                                        ▼
                              ┌─────────────────┐
               BOX jobs only  │   ACTIVATED     │◄──── BOX open, children eligible
                              └────────┬────────┘
                                       │  CMD / dispatched
                                       ▼
                              ┌─────────────────┐
                              │    STARTING     │  Agent notified, waiting for PID
                              └────────┬────────┘
                     agent confirms    │
                                       ▼
                              ┌─────────────────┐
                              │    RUNNING      │
                              └────────┬────────┘
                  exit code 0 │        │ non-zero / timeout
                              ▼        ▼
                          SUCCESS   FAILURE ──► RESTART (if n_retrys > 0)
                              │        │              │
                              │        │              ▼
                              └──►INACTIVE◄──── re-dispatched


Hold / ice states (orthogonal to the main flow):
  INACTIVE  ──► ON_HOLD  ──► INACTIVE
  INACTIVE  ──► ON_ICE   ──► INACTIVE   (also reset automatically at cycle boundary)

KILLJOB always sends RUNNING → TERMINATED → INACTIVE.
"""

from __future__ import annotations

from typing import Optional
from autosys.models.enums import JobStatus

def _norm_status(s: int | str | JobStatus | None) -> str:
    if s is None:
        return "INACTIVE"
    if isinstance(s, str):
        return s.upper()
    try:
        return JobStatus(s).name
    except ValueError:
        return str(s)


# ===========================================================================
# Transition table
# ===========================================================================

# Map each from-status to the set of statuses it can legally transition to.
# CHANGE_STATUS (force=True) bypasses this table entirely.
VALID_TRANSITIONS: dict[str, frozenset[str]] = {
    "INACTIVE":            frozenset({"ACTIVATED", "STARTING", "ON_HOLD", "ON_ICE"}),
    "ACTIVATED":           frozenset({"RUNNING", "STARTING", "INACTIVE", "ON_HOLD", "FAILURE"}),
    "STARTING":            frozenset({"RUNNING", "FAILURE", "INACTIVE"}),
    "RUNNING":             frozenset({"SUCCESS", "FAILURE", "TERMINATED"}),
    "SUCCESS":             frozenset({"INACTIVE", "ACTIVATED", "STARTING"}),
    "FAILURE":             frozenset({"INACTIVE", "RESTART", "ACTIVATED", "STARTING"}),
    "TERMINATED":          frozenset({"INACTIVE", "STARTING"}),
    "RESTART":             frozenset({"STARTING", "INACTIVE", "FAILURE"}),
    "ON_HOLD":             frozenset({"INACTIVE", "ACTIVATED"}),
    "ON_ICE":              frozenset({"INACTIVE"}),
    "WAIT_REPLY":          frozenset({"RUNNING", "FAILURE", "INACTIVE", "STARTING"}),
    "QUE_WAIT":            frozenset({"STARTING", "INACTIVE"}),
    "REFRESH_DEPENDENCIES": frozenset({"INACTIVE", "STARTING", "ACTIVATED"}),
    "PEND_MACH":           frozenset({"STARTING", "INACTIVE"}),
    "RESWAIT":             frozenset({"STARTING", "INACTIVE"}),
    "ON_NOEXEC":           frozenset({"INACTIVE"}),
    "SUSPENDED":           frozenset({"INACTIVE", "STARTING", "RUNNING"}),
}

# Two-letter status codes for autorep output (mirrors Phase 3 autorep_cmd.py)
STATUS_ABBREV: dict[str, str] = {
    "INACTIVE":    "IN",
    "ACTIVATED":   "AC",
    "STARTING":    "ST",
    "RUNNING":     "RU",
    "SUCCESS":     "SU",
    "FAILURE":     "FA",
    "TERMINATED":  "TE",
    "RESTART":     "RE",
    "WAIT_REPLY":  "WR",
    "ON_HOLD":     "OH",
    "ON_ICE":      "OI",
    "QUE_WAIT":    "QW",
    "PEND_MACH":   "PM",
    "RESWAIT":     "RW",
    "ON_NOEXEC":   "NE",
    "SUSPENDED":   "SS",
}

# Terminal states — jobs in these states have finished their current run.
TERMINAL_STATES: frozenset[str] = frozenset({"SUCCESS", "FAILURE", "TERMINATED"})

# States from which a job can be re-started by a STARTJOB event.
STARTABLE_STATES: frozenset[str] = frozenset({
    "INACTIVE", "SUCCESS", "FAILURE", "TERMINATED",
})


# ===========================================================================
# Exceptions
# ===========================================================================

class InvalidTransitionError(Exception):
    """
    Raised when an event processor handler attempts an illegal state transition.

    This mirrors the error AutoSys logs as::

        CAUAJM_W_50050  Cannot transition job 'extract_sales'
                        from RUNNING to INACTIVE  (reason: direct reset disallowed)

    Parameters
    ----------
    job_name:
        The job that was being transitioned.
    from_status:
        Current (illegal source) status.
    to_status:
        Target (rejected) status.
    """
    def __init__(self, job_name: str, from_status: str, to_status: str) -> None:
        self.job_name    = job_name
        self.from_status = from_status
        self.to_status   = to_status
        super().__init__(
            f"Job {job_name!r}: cannot transition "
            f"{from_status!r} → {to_status!r}"
        )


# ===========================================================================
# Public API
# ===========================================================================

def validate_transition(
    job_name: str,
    from_status: str,
    to_status: str,
    *,
    force: bool = False,
) -> None:
    """
    Raise ``InvalidTransitionError`` if the transition is illegal.

    If ``force=True`` (used by CHANGE_STATUS events), the transition
    is allowed regardless of the FSM rules.
    """
    if force:
        return

    from_status_str = _norm_status(from_status)
    to_status_str = _norm_status(to_status)

    allowed = VALID_TRANSITIONS.get(from_status_str, frozenset())
    if to_status_str not in allowed:
        raise InvalidTransitionError(job_name, from_status_str, to_status_str)


def can_transition(from_status: int | str | None, to_status: int | str | None) -> bool:
    """
    Return True if the transition is legal (does not raise).

    Useful in condition checks before attempting a transition.
    """
    from_status_str = _norm_status(from_status)
    to_status_str = _norm_status(to_status)
    return to_status_str in VALID_TRANSITIONS.get(from_status_str, frozenset())


def is_terminal(status: int | str | None) -> bool:
    """Return True if the status represents a completed run (SUCCESS/FAILURE/TERMINATED)."""
    return _norm_status(status) in TERMINAL_STATES


def is_startable(status: int | str | None) -> bool:
    """Return True if a STARTJOB event is allowed against this status."""
    return _norm_status(status) in STARTABLE_STATES
