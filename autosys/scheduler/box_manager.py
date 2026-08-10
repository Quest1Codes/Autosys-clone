"""
BoxManager — orchestrates BOX job children.

Background: What is a BOX in AutoSys?
--------------------------------------
A BOX is a container job.  Its children are the jobs that have
``box_name: <this_box>`` set in their JIL definition.

Real AutoSys BOX lifecycle:

  1. ``STARTJOB box_etl`` event fires.
  2. BOX transitions: INACTIVE → STARTING → RUNNING.
  3. All children become "active" — the Scheduler ACE begins evaluating
     their start conditions on every tick.
  4. Children without conditions (or whose conditions are already met)
     start immediately.  Children with ``condition: s(sibling)`` wait.
  5. When ALL children reach a terminal state:
       - All SUCCESS            → BOX = SUCCESS
       - Any FAILURE            → BOX = FAILURE  (unless restart logic applies)
       - Any TERMINATED         → BOX = TERMINATED
  6. The BOX's own terminal status propagates to any job that has
     ``condition: s(box_etl)`` — e.g. a downstream report job.

What BoxManager does per tick
------------------------------
Called once per ``process_one_tick`` cycle after events are processed:

1. Scan all RUNNING BOX jobs.
2. For each INACTIVE child:
   a. Evaluate start conditions (using the condition AST in the job's
      ``condition`` field).
   b. If conditions are satisfied (or the child has no condition):
      transition to STARTING and invoke the dispatch function.
3. For each box where ALL children are terminal:
   determine and set the BOX's terminal status.

Key design choices
------------------
- BoxManager is stateless.  All state lives in the DB.
- It re-evaluates children every tick — idempotent.
- It calls the same ``dispatch_fn`` as the event processor so local and
  remote dispatch both work transparently.
- Children reset to INACTIVE when ``FORCE_STARTJOB`` is sent to the BOX.
  (Handled by the event processor's FORCE_STARTJOB handler which resets
  child statuses before calling this module.)

Phase 8 extensions
------------------
- ``max_load`` enforcement per machine
- Restart-on-failure policy (``alarm_if_fail`` + ``n_retrys``)
- Priority-based child ordering (``priority`` attribute)
- Box abort: if a child fails and ``abort_on_failure`` is set, kill all
  siblings
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable, Optional

from loguru import logger
from sqlalchemy.orm import Session

from autosys.db.schema import JobRow
from autosys.models.enums import JobStatus
from autosys.scheduler.condition_evaluator import is_satisfied, referenced_job_names
from autosys.scheduler.state_machine import _norm_status

# Reuse the same type aliases as event_processor.py
DispatchFn = Callable[[Session, JobRow], None]
KillFn     = Callable[[Session, JobRow], None]

# Jobs in one of these states are considered "terminal" for box completion
_TERMINAL = frozenset({"SUCCESS", "FAILURE", "TERMINATED"})

# Jobs that are still executing (prevent premature box completion)
_ACTIVE = frozenset({"STARTING", "RUNNING"})


def _stub_dispatch(session: Session, row: JobRow) -> None:
    """Stub dispatcher — immediately marks CMD children SUCCESS (no subprocess)."""
    row.status     = JobStatus.SUCCESS.value
    row.last_start = datetime.now()
    row.last_end   = datetime.now()


class BoxManager:
    """
    Per-tick box orchestrator.

    Parameters
    ----------
    dispatch_fn:
        Called when a child job is ready to start.  Same contract as
        ``EventProcessor.dispatch_fn``.
    kill_fn:
        Called when a box is killed and running children must be killed.
        Optional — if not provided, running children are just marked
        TERMINATED without signalling their processes.
    auto_complete:
        If True (default False) children are immediately marked SUCCESS
        rather than dispatched.  Used by the stub dispatcher.
    """

    def __init__(
        self,
        dispatch_fn:  Optional[DispatchFn] = None,
        kill_fn:      Optional[KillFn]     = None,
        auto_complete: bool = False,
    ) -> None:
        self._dispatch_fn  = dispatch_fn or _stub_dispatch
        self._kill_fn      = kill_fn
        self._auto_complete = auto_complete

    # ------------------------------------------------------------------
    # Main entry point — called each tick
    # ------------------------------------------------------------------

    def tick(
        self,
        session:  Session,
        snapshot: dict[str, str],
        now:      datetime,
    ) -> int:
        """
        Process all RUNNING boxes: activate eligible children, complete done boxes.

        Parameters
        ----------
        session:
            Open SQLAlchemy session.
        snapshot:
            ``{job_name: status}`` dict used by the condition evaluator.
            Built once per tick so all condition checks use a consistent view.
        now:
            Current timestamp (passed in from the EPS tick for consistency).

        Returns
        -------
        int
            Number of state changes made (child activations + box completions).
        """
        from autosys.db.repository import jobs as job_repo

        running_boxes = job_repo.get_running_boxes(session)
        changes = 0

        for box in running_boxes:
            changes += self._process_box(session, box, snapshot, now)

        return changes

    # ------------------------------------------------------------------
    # Per-box processing
    # ------------------------------------------------------------------

    def _process_box(
        self,
        session:  Session,
        box:      JobRow,
        snapshot: dict[str, str],
        now:      datetime,
    ) -> int:
        """Process one RUNNING box — activate children, check for completion."""
        from autosys.db.repository import jobs as job_repo

        children = job_repo.get_children(session, box.job_name)
        if not children:
            # Empty BOX: no children, complete immediately
            logger.info(
                "BOX %r has no children — auto-completing as SUCCESS",
                box.job_name,
            )
            box.status  = JobStatus.SUCCESS.value
            box.last_end = now
            return 1

        changes = 0

        # Step 1: activate INACTIVE children whose conditions are met
        for child in children:
            if _norm_status(child.status) == "INACTIVE":
                if self._conditions_met(child, snapshot):
                    logger.info(
                        "BOX %r: activating child %r → STARTING",
                        box.job_name, child.job_name,
                    )
                    child.status = JobStatus.STARTING.value
                    # Update snapshot so later siblings see this child's new status
                    snapshot[child.job_name] = "STARTING"

                    if self._auto_complete:
                        child.status     = JobStatus.SUCCESS.value
                        child.last_start = now
                        child.last_end   = now
                    else:
                        self._dispatch_fn(session, child)

                    # NOTE: do NOT update snapshot here — subsequent siblings are
                    # evaluated against the snapshot built at the START of the tick,
                    # matching real AutoSys behaviour.  Sequential chains need
                    # multiple ticks (one per wave of the dependency chain).
                    changes += 1

        # Step 2: check box_failure and box_success conditions first
        if box.box_failure and self._conditions_met(box, snapshot, box.box_failure):
            box.status = JobStatus.FAILURE.value
            box.last_end = now
            logger.info("BOX %r completed → FAILURE (box_failure condition met)", box.job_name)
            return changes + 1

        if box.box_success and self._conditions_met(box, snapshot, box.box_success):
            box.status = JobStatus.SUCCESS.value
            box.last_end = now
            logger.info("BOX %r completed → SUCCESS (box_success condition met)", box.job_name)
            return changes + 1

        # Step 3: default fail-fast — if any child FAILED and there is no custom
        # box_failure condition, immediately fail the box.  This mirrors real
        # AutoSys default behaviour: a single child failure aborts the box.
        if not box.box_failure:
            if any(_norm_status(c.status) == "FAILURE" for c in children):
                box.status   = JobStatus.FAILURE.value
                box.last_end = now
                logger.info(
                    "BOX %r → FAILURE (child failed, default fail-fast)",
                    box.job_name,
                )
                return changes + 1

        # All-terminal check.  A child that's still INACTIVE but whose
        # condition can never be satisfied anymore — e.g. an alert job with
        # `condition: failure(x)` once `x` has already reached SUCCESS —
        # would otherwise block the box forever.  Exclude those: nothing
        # left in the system can change their outcome, so they're
        # effectively done (still INACTIVE, correctly, just not "pending").
        non_terminal = [
            c for c in children
            if _norm_status(c.status) not in _TERMINAL
            and not self._is_unreachable(c, snapshot)
        ]
        if non_terminal:
            return changes   # box is still in progress

        # All children are terminal (or permanently unreachable) — determine
        # box outcome from whichever children actually ran.
        ran_children = [c for c in children if _norm_status(c.status) in _TERMINAL]
        terminal_statuses = {_norm_status(c.status) for c in ran_children}

        if "TERMINATED" in terminal_statuses:
            box.status = JobStatus.TERMINATED.value
        elif "FAILURE" in terminal_statuses:
            box.status = JobStatus.FAILURE.value
        else:
            box.status = JobStatus.SUCCESS.value

        box.last_end = now
        logger.info(
            "BOX %r completed → %s  (children: %s)",
            box.job_name,
            box.status,
            {c.job_name: c.status for c in children},
        )
        changes += 1
        return changes

    # ------------------------------------------------------------------
    # Child kill  (called by KILLJOB on the box itself)
    # ------------------------------------------------------------------

    def kill_children(
        self,
        session: Session,
        box_name: str,
        now:     datetime,
    ) -> None:
        """
        Terminate all active children of a box being killed.

        Called by the event processor's KILLJOB handler when the target
        is a BOX job.  In real AutoSys, killing a box kills all running
        children (the same as sending KILLJOB to each running child).

        Children in terminal states are left as-is.
        INACTIVE children are simply set to TERMINATED (they never ran).
        """
        from autosys.db.repository import jobs as job_repo

        children = job_repo.get_children(session, box_name)
        for child in children:
            if _norm_status(child.status) in _TERMINAL:
                continue
            if _norm_status(child.status) in _ACTIVE and self._kill_fn is not None:
                try:
                    self._kill_fn(session, child)
                except Exception as exc:
                    logger.warning(
                        "BOX kill: kill_fn raised for child %r: %s",
                        child.job_name, exc,
                    )
            child.status  = JobStatus.TERMINATED.value
            child.last_end = now
            logger.info(
                "BOX %r: child %r killed → TERMINATED",
                box_name, child.job_name,
            )

    # ------------------------------------------------------------------
    # Reset children for FORCE_STARTJOB
    # ------------------------------------------------------------------

    def reset_children(self, session: Session, box_name: str) -> None:
        """
        Reset all children to INACTIVE for a box restart.

        In real AutoSys, ``FORCE_STARTJOB`` on a BOX resets all children
        to INACTIVE so they re-run from scratch.  Running children are
        killed first.

        Called by the event processor before re-running the box.
        """
        from autosys.db.repository import jobs as job_repo

        children = job_repo.get_children(session, box_name)
        now      = datetime.now()
        for child in children:
            if _norm_status(child.status) in _ACTIVE and self._kill_fn is not None:
                try:
                    self._kill_fn(session, child)
                except Exception as exc:
                    logger.warning(
                        "BOX reset: kill_fn raised for %r: %s",
                        child.job_name, exc,
                    )
            child.status  = JobStatus.INACTIVE.value
            child.last_end = None
            logger.debug("BOX %r: reset child %r → INACTIVE", box_name, child.job_name)

    # ------------------------------------------------------------------
    # Condition evaluation helper
    # ------------------------------------------------------------------

    def _conditions_met(self, child: JobRow, snapshot: dict[str, str], condition_override: Optional[str] = None) -> bool:
        """
        Return True if the child's start conditions (or overridden condition) are satisfied.

        Uses the module-level ``is_satisfied`` function from condition_evaluator.
        Jobs with no condition are always eligible.
        """
        condition = condition_override if condition_override is not None else child.condition
        if not condition or condition.strip() == "":
            return True

        try:
            return is_satisfied(condition, snapshot)
        except Exception as exc:
            logger.warning(
                "BoxManager: condition eval error for %r (%r): %s",
                child.job_name, condition, exc,
            )
            return False

    def _is_unreachable(self, child: JobRow, snapshot: dict[str, str]) -> bool:
        """
        Return True if *child* is INACTIVE and its condition can never be
        satisfied anymore given the current snapshot.

        Every job it references is either terminal (won't change again) or
        doesn't exist in the system (never will), and its condition still
        evaluates to False — e.g. `condition: failure(x)` once `x` has
        already reached SUCCESS.  Real AutoSys doesn't wait forever for a
        child that's structurally incapable of triggering.
        """
        if _norm_status(child.status) != "INACTIVE" or not child.condition:
            return False

        refs = referenced_job_names(child.condition)
        if not refs:
            return False

        if any(
            name in snapshot and _norm_status(snapshot[name]) not in _TERMINAL
            for name in refs
        ):
            return False  # a referenced job can still change state

        return not self._conditions_met(child, snapshot)
