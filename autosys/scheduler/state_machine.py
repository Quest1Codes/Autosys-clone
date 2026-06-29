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

    Parameters
    ----------
    job_name:
        Used only for the error message.
    from_status:
        Current status of the job (must be a key in VALID_TRANSITIONS).
    to_status:
        Desired next status.
    force:
        If True, skip the table check (used by CHANGE_STATUS events).
        Operators know what they are doing when they force a status.
    """
    if force:
        return
    allowed = VALID_TRANSITIONS.get(from_status, frozenset())
    if to_status not in allowed:
        raise InvalidTransitionError(job_name, from_status, to_status)


def can_transition(from_status: str, to_status: str) -> bool:
    """
    Return True if the transition is legal (does not raise).

    Useful in condition checks before attempting a transition.
    """
    return to_status in VALID_TRANSITIONS.get(from_status, frozenset())


def is_terminal(status: str) -> bool:
    """Return True if the job has finished its current run (SUCCESS/FAILURE/TERMINATED)."""
    return status in TERMINAL_STATES


def is_startable(status: str) -> bool:
    """Return True if a STARTJOB event can initiate a new run for this job."""
    return status in STARTABLE_STATES
