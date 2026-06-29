"""
AutoSys Event Processor (EPS) — the heart of the scheduler.

This is the most important file in the codebase.  Everything else exists to
support this loop.

Architecture
------------
The Event Processor is a polling daemon that runs one "tick" every
``poll_interval`` seconds (default: 1).  Each tick does two things:

    1. **Process queued events** — read all unprocessed rows from
       ``event_queue`` ordered by ``created_at ASC`` and run the
       appropriate handler.

    2. **Check time triggers** — for every INACTIVE job with a
       ``start_times`` attribute, check if the current HH:MM matches and
       the job hasn't already run today.  If so, enqueue a STARTJOB event
       (processed in the next tick, preserving FIFO order).

Event handlers
--------------
STARTJOB        → evaluate condition → if met, activate job (STARTING for CMD,
                  ACTIVATED for BOX; then cascade children)
FORCE_STARTJOB  → same, but skip condition check
KILLJOB         → RUNNING/STARTING → TERMINATED
HOLD_JOB        → INACTIVE/ACTIVATED → ON_HOLD
JOB_OFF_HOLD    → ON_HOLD → INACTIVE
JOB_ON_ICE      → INACTIVE → ON_ICE
JOB_OFF_ICE     → ON_ICE → INACTIVE
CHANGE_STATUS   → force-override to any status (operator escape hatch)
SET_GLOBAL      → upsert global variable; then re-evaluate all waiting jobs

Dispatcher (Phase 4 stub)
--------------------------
In Phase 4 the ``dispatch`` function is a stub that immediately transitions
STARTING → RUNNING (simulating an instant agent start) and optionally
auto-completes the run.  Phase 5 will replace this with the real System
Agent dispatch over a socket/REST call.

BOX cascading
-------------
When a BOX job is activated:
  1. BOX transitions INACTIVE → ACTIVATED.
  2. Each child of the BOX that is INACTIVE and has satisfied conditions
     is immediately started (STARTING).

When a child of a BOX completes:
  1. If ALL children are SUCCESS → BOX transitions to SUCCESS.
  2. If ANY child is FAILURE (and no box_failure override) → BOX FAILURE.

Testability
-----------
``EventProcessor.process_one_tick(session, now=...)`` is a pure synchronous
function — it takes an open SQLAlchemy ``Session`` and an injectable
``datetime`` (for time trigger tests).  No ``asyncio`` needed for unit tests.

The ``run_forever()`` coroutine wraps ``process_one_tick`` in an asyncio loop
for the production daemon.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import Callable, Optional

from loguru import logger
from sqlalchemy.orm import Session

from autosys.db.connection import sync_session
from autosys.db.repository import (
    jobs as job_repo,
    events as event_repo,
    globs as glob_repo,
)
from autosys.db.schema import EventQueueRow, JobRow
from autosys.models.event import Event
from autosys.scheduler.condition_evaluator import is_satisfied, build_status_snapshot
from autosys.scheduler.state_machine import (
    validate_transition,
    is_startable,
    InvalidTransitionError,
)
from autosys.scheduler.time_trigger import get_triggered_jobs


# ===========================================================================
# Dispatcher protocol
# ===========================================================================

# A dispatcher receives (session, row) and transitions the job from STARTING
# to RUNNING (and optionally further).  In Phase 4 it's a stub; Phase 5
# plugs in the real System Agent call here.
DispatchFn = Callable[[Session, JobRow], None]
KillFn     = Callable[[Session, JobRow], None]


def _stub_dispatch(session: Session, row: JobRow) -> None:
    """
    Phase 4 stub: immediately transitions STARTING → RUNNING.

    In the real system the EPS sends a dispatch request to the System Agent,
    which starts the OS process and sends back a JOB_START acknowledgement.
    That ACK triggers the STARTING → RUNNING transition.  Here we simulate
    that ACK happening instantaneously for testing purposes.
    """
    logger.info("[stub] Dispatching %r — simulating RUNNING", row.job_name)
    row.status     = "RUNNING"
    row.last_start = datetime.now()


# ===========================================================================
# EventProcessor
# ===========================================================================

class EventProcessor:
    """
    The AutoSys Event Processor Service (EPS).

    Parameters
    ----------
    dispatch_fn:
        Called when a CMD job transitions to STARTING.  Defaults to the
        Phase 4 stub that immediately makes the job RUNNING.
    poll_interval:
        Seconds to sleep between ticks in the async daemon loop.
    auto_complete:
        If True (default), the stub dispatcher immediately marks jobs as
        SUCCESS after setting them to RUNNING.  Set to False to leave jobs
        in RUNNING state so tests can inspect intermediate state.
    """

    def __init__(
        self,
        dispatch_fn:    Optional[DispatchFn] = None,
        kill_fn:        Optional[KillFn]     = None,
        poll_interval:  float = 1.0,
        auto_complete:  bool  = True,
    ) -> None:
        from autosys.scheduler.box_manager import BoxManager
        self._dispatch_fn   = dispatch_fn or _stub_dispatch
        self._kill_fn       = kill_fn          # None → no signal to real process
        self.poll_interval  = poll_interval
        self.auto_complete  = auto_complete
        self._running       = False
        self._box_manager   = BoxManager(
            dispatch_fn   = dispatch_fn or _stub_dispatch,
            kill_fn       = kill_fn,
            auto_complete = auto_complete,
        )

    # ------------------------------------------------------------------
    # Main synchronous tick (used by tests and the CLI one-shot mode)
    # ------------------------------------------------------------------

    def process_one_tick(
        self,
        session: Session,
        now: Optional[datetime] = None,
    ) -> int:
        """
        Process all pending events and check time triggers.

        This is the core unit of work — the async daemon calls it in a loop;
        tests call it directly for deterministic verification.

        Parameters
        ----------
        session:
            Open SQLAlchemy session.  The caller manages commit/rollback.
        now:
            Current datetime.  Injectable so tests can simulate specific
            times without waiting for real clock time.

        Returns
        -------
        int
            Number of events processed (excluding time-triggered events,
            which are enqueued for the *next* tick to preserve FIFO order).
        """
        if now is None:
            now = datetime.now()

        # Build a status snapshot once for the entire tick so all condition
        # evaluations see a consistent view of the world.
        snapshot = build_status_snapshot(session)

        # 1. Process queued events (FIFO)
        pending = event_repo.dequeue_pending(session)
        n = 0
        for ev in pending:
            try:
                self._handle_event(session, ev, snapshot, now)
            except Exception as exc:
                logger.error(
                    "Error processing event %s (%s): %s",
                    ev.event_id[:8], ev.event_type, exc,
                )
            event_repo.mark_processed(session, ev.event_id)
            n += 1

        # Refresh snapshot after processing events so time triggers and box
        # evaluation see the updated statuses from this tick.
        snapshot = build_status_snapshot(session)

        # 2. BOX tick — activate eligible children of running boxes and
        #    complete boxes whose children are all terminal.
        try:
            self._box_manager.tick(session, snapshot, now)
        except Exception as exc:
            logger.error("BoxManager.tick raised: %s", exc)

        # Refresh again after box changes (children may have been activated)
        snapshot = build_status_snapshot(session)

        # 3. Check time triggers (enqueue STARTJOB events for next tick)
        rows = job_repo.list_all(session)
        for row in get_triggered_jobs(rows, now):
            ev = Event(
                event_type = "STARTJOB",
                job_name   = row.job_name,
                source     = "scheduler",
            )
            event_repo.enqueue(session, ev)
            logger.info(
                "Time trigger: queued STARTJOB for %r at %s",
                row.job_name, now.strftime("%H:%M"),
            )

        return n

    # ------------------------------------------------------------------
    # Async daemon loop
    # ------------------------------------------------------------------

    async def run_forever(self) -> None:
        """
        Run the event processor as an async daemon.

        Each iteration:
          1. Opens a sync session (SQLite is sync-only in Phase 4).
          2. Calls ``process_one_tick``.
          3. Commits the session.
          4. Sleeps for ``poll_interval`` seconds.

        The loop runs until ``stop()`` is called (or the process is killed).
        """
        self._running = True
        logger.info(
            "Event Processor started (poll interval: %.1fs)", self.poll_interval
        )
        while self._running:
            try:
                with sync_session() as session:
                    n = self.process_one_tick(session)
                    if n:
                        logger.debug("Tick processed %d event(s)", n)
            except Exception as exc:
                logger.error("Tick error: %s", exc)
            await asyncio.sleep(self.poll_interval)

    def stop(self) -> None:
        """Signal the daemon loop to exit after the current tick."""
        self._running = False

    # ------------------------------------------------------------------
    # Event handlers (one per EventType)
    # ------------------------------------------------------------------

    def _handle_event(
        self,
        session: Session,
        ev: EventQueueRow,
        snapshot: dict[str, str],
        now: datetime,
    ) -> None:
        """Dispatch to the correct per-event-type handler."""
        handler = {
            "STARTJOB":        self._handle_startjob,
            "FORCE_STARTJOB":  self._handle_force_startjob,
            "KILLJOB":         self._handle_killjob,
            "HOLD_JOB":        self._handle_hold,
            "JOB_OFF_HOLD":    self._handle_off_hold,
            "JOB_ON_ICE":      self._handle_on_ice,
            "JOB_OFF_ICE":     self._handle_off_ice,
            "CHANGE_STATUS":   self._handle_change_status,
            "SET_GLOBAL":      self._handle_set_global,
            "CHECK_HEARTBEAT": self._handle_check_heartbeat,
        }.get(ev.event_type)

        if handler is None:
            logger.debug("No handler for event type %r — skipping", ev.event_type)
            return

        handler(session, ev, snapshot, now)

    # -- STARTJOB --------------------------------------------------------

    def _handle_startjob(
        self,
        session: Session,
        ev: EventQueueRow,
        snapshot: dict[str, str],
        now: datetime,
    ) -> None:
        """
        STARTJOB: evaluate condition, activate if satisfied.

        Real AutoSys behaviour:
        - If the job's condition is not met → log and drop (job stays INACTIVE).
        - If met → transition CMD to STARTING, BOX to ACTIVATED.
        """
        row = job_repo.get_row(session, ev.job_name)
        if row is None:
            logger.warning("STARTJOB: job %r not found", ev.job_name)
            return

        if not is_startable(row.status or "INACTIVE"):
            logger.info(
                "STARTJOB: %r is %s — not startable, skipping",
                ev.job_name, row.status,
            )
            return

        # Evaluate condition using the snapshot from the START of this tick
        if not is_satisfied(row.condition, snapshot):
            logger.info(
                "STARTJOB: %r condition not satisfied — staying %s",
                ev.job_name, row.status,
            )
            return

        self._activate_job(session, row, now)

    # -- FORCE_STARTJOB --------------------------------------------------

    def _handle_force_startjob(
        self,
        session: Session,
        ev: EventQueueRow,
        snapshot: dict[str, str],
        now: datetime,
    ) -> None:
        """
        FORCE_STARTJOB: bypass condition check, start immediately.

        Used by operators to manually kick off a job regardless of its
        dependency state.  Equivalent to the real AutoSys
        ``sendevent -E FORCE_STARTJOB``.
        """
        row = job_repo.get_row(session, ev.job_name)
        if row is None:
            logger.warning("FORCE_STARTJOB: job %r not found", ev.job_name)
            return

        logger.info("FORCE_STARTJOB: activating %r (bypassing conditions)", ev.job_name)
        # Reset children if this is a BOX restart
        if row.job_type == "BOX":
            self._box_manager.reset_children(session, ev.job_name)
        self._activate_job(session, row, now)

    # -- KILLJOB ---------------------------------------------------------

    def _handle_killjob(
        self,
        session: Session,
        ev: EventQueueRow,
        snapshot: dict[str, str],
        now: datetime,
    ) -> None:
        """
        KILLJOB: terminate a running or starting job.

        Real AutoSys sends SIGTERM to the OS process via the System Agent,
        then transitions to TERMINATED.  We skip the signal in Phase 4.
        """
        row = job_repo.get_row(session, ev.job_name)
        if row is None:
            logger.warning("KILLJOB: job %r not found", ev.job_name)
            return

        if row.status not in ("RUNNING", "STARTING", "ACTIVATED"):
            logger.info(
                "KILLJOB: %r is %s (not killable) — skipping",
                ev.job_name, row.status,
            )
            return

        try:
            validate_transition(ev.job_name, row.status, "TERMINATED")
        except InvalidTransitionError as exc:
            logger.warning("KILLJOB: %s", exc)
            return

        # If target is a BOX, kill all active children first
        if row.job_type == "BOX":
            self._box_manager.kill_children(session, ev.job_name, now)

        # Signal the real process first (Phase 5+), then update state.
        if self._kill_fn is not None:
            try:
                self._kill_fn(session, row)
            except Exception as exc:
                logger.warning("KILLJOB: kill_fn raised %s", exc)

        row.status  = "TERMINATED"
        row.last_end = now
        logger.info("KILLJOB: %r → TERMINATED", ev.job_name)

    # -- HOLD_JOB / JOB_OFF_HOLD -----------------------------------------

    def _handle_hold(
        self,
        session: Session,
        ev: EventQueueRow,
        snapshot: dict[str, str],
        now: datetime,
    ) -> None:
        """HOLD_JOB: freeze a job in ON_HOLD state."""
        row = job_repo.get_row(session, ev.job_name)
        if row is None:
            logger.warning("HOLD_JOB: job %r not found", ev.job_name)
            return
        try:
            validate_transition(ev.job_name, row.status or "INACTIVE", "ON_HOLD")
        except InvalidTransitionError as exc:
            logger.warning("HOLD_JOB: %s", exc)
            return
        row.status = "ON_HOLD"
        logger.info("HOLD_JOB: %r → ON_HOLD", ev.job_name)

    def _handle_off_hold(
        self,
        session: Session,
        ev: EventQueueRow,
        snapshot: dict[str, str],
        now: datetime,
    ) -> None:
        """JOB_OFF_HOLD: release a job from ON_HOLD back to INACTIVE."""
        row = job_repo.get_row(session, ev.job_name)
        if row is None:
            logger.warning("JOB_OFF_HOLD: job %r not found", ev.job_name)
            return
        if row.status != "ON_HOLD":
            logger.info(
                "JOB_OFF_HOLD: %r is %s (not ON_HOLD) — skipping",
                ev.job_name, row.status,
            )
            return
        row.status = "INACTIVE"
        logger.info("JOB_OFF_HOLD: %r → INACTIVE", ev.job_name)

    # -- JOB_ON_ICE / JOB_OFF_ICE ----------------------------------------

    def _handle_on_ice(
        self,
        session: Session,
        ev: EventQueueRow,
        snapshot: dict[str, str],
        now: datetime,
    ) -> None:
        """JOB_ON_ICE: freeze a job for the current cycle."""
        row = job_repo.get_row(session, ev.job_name)
        if row is None:
            logger.warning("JOB_ON_ICE: job %r not found", ev.job_name)
            return
        try:
            validate_transition(ev.job_name, row.status or "INACTIVE", "ON_ICE")
        except InvalidTransitionError as exc:
            logger.warning("JOB_ON_ICE: %s", exc)
            return
        row.status = "ON_ICE"
        logger.info("JOB_ON_ICE: %r → ON_ICE", ev.job_name)

    def _handle_off_ice(
        self,
        session: Session,
        ev: EventQueueRow,
        snapshot: dict[str, str],
        now: datetime,
    ) -> None:
        """JOB_OFF_ICE: release a job from ON_ICE."""
        row = job_repo.get_row(session, ev.job_name)
        if row is None:
            logger.warning("JOB_OFF_ICE: job %r not found", ev.job_name)
            return
        if row.status != "ON_ICE":
            logger.info(
                "JOB_OFF_ICE: %r is %s (not ON_ICE) — skipping",
                ev.job_name, row.status,
            )
            return
        row.status = "INACTIVE"
        logger.info("JOB_OFF_ICE: %r → INACTIVE", ev.job_name)

    # -- CHANGE_STATUS ---------------------------------------------------

    def _handle_change_status(
        self,
        session: Session,
        ev: EventQueueRow,
        snapshot: dict[str, str],
        now: datetime,
    ) -> None:
        """
        CHANGE_STATUS: force-override a job's status.

        This is the operator's escape hatch — it bypasses all state machine
        validation.  Used to manually mark a stuck FAILURE as SUCCESS so
        downstream jobs can proceed.
        """
        row = job_repo.get_row(session, ev.job_name)
        if row is None:
            logger.warning("CHANGE_STATUS: job %r not found", ev.job_name)
            return
        if not ev.new_status:
            logger.warning(
                "CHANGE_STATUS: event for %r has no new_status — skipping",
                ev.job_name,
            )
            return

        old_status  = row.status
        row.status  = ev.new_status.upper()
        logger.info(
            "CHANGE_STATUS: %r  %s → %s  (forced by operator)",
            ev.job_name, old_status, row.status,
        )

    # -- SET_GLOBAL ------------------------------------------------------

    def _handle_set_global(
        self,
        session: Session,
        ev: EventQueueRow,
        snapshot: dict[str, str],
        now: datetime,
    ) -> None:
        """
        SET_GLOBAL: upsert a global variable.

        After setting the variable, re-evaluate all INACTIVE jobs whose
        condition references ``value(VARNAME)`` — one of them might now be
        unblocked.
        """
        if not ev.global_name or ev.global_value is None:
            logger.warning("SET_GLOBAL: missing global_name or global_value")
            return

        glob_repo.set(session, ev.global_name, ev.global_value)
        logger.info(
            "SET_GLOBAL: %s = %r",
            ev.global_name.upper(), ev.global_value,
        )

        # Re-evaluate waiting jobs in case this variable change unblocked them.
        # Rebuild snapshot to include updated globals in condition evaluation.
        updated_snapshot = build_status_snapshot(session)
        rows = job_repo.list_all(session)
        for row in rows:
            if (row.status or "INACTIVE") == "INACTIVE" and row.condition:
                if "value(" in row.condition.lower():
                    if is_satisfied(row.condition, updated_snapshot):
                        logger.info(
                            "SET_GLOBAL: %r unblocked by %s=%r",
                            row.job_name, ev.global_name, ev.global_value,
                        )

    # -- CHECK_HEARTBEAT -------------------------------------------------

    def _handle_check_heartbeat(
        self,
        session: Session,
        ev: "EventQueueRow",
        snapshot: dict[str, str],
        now: datetime,
    ) -> None:
        """
        CHECK_HEARTBEAT: ping a registered System Agent and update its status.

        Real AutoSys sends CHECK_HEARTBEAT events on a schedule (default every
        5 minutes).  If the agent does not respond, the machine is marked DOWN
        and an alarm is raised for each job assigned to it.

        The ``job_name`` field in the event is repurposed here to carry the
        ``machine_name`` (same convention as the real AutoSys event table).
        """
        from autosys.db.repository import machines as machine_repo
        from autosys.agent.remote import RemoteDispatch

        machine_name = ev.job_name   # repurposed field
        if not machine_name:
            logger.warning("CHECK_HEARTBEAT: no machine_name in event")
            return

        machine_row = machine_repo.get(session, machine_name)
        if machine_row is None:
            logger.warning(
                "CHECK_HEARTBEAT: machine %r not registered", machine_name
            )
            return

        rd    = RemoteDispatch()
        alive = rd.heartbeat(machine_row)

        new_status = "UP" if alive else "DOWN"
        machine_repo.update_heartbeat(session, machine_name, status=new_status)

        if alive:
            logger.info(
                "CHECK_HEARTBEAT: %r responded — status=UP", machine_name
            )
        else:
            logger.warning(
                "CHECK_HEARTBEAT: %r did not respond — status=DOWN", machine_name
            )

    # ------------------------------------------------------------------
    # Job activation helpers
    # ------------------------------------------------------------------

    def _activate_job(
        self,
        session: Session,
        row: JobRow,
        now: datetime,
    ) -> None:
        """
        Activate a job: BOX → ACTIVATED, CMD → STARTING → dispatch.

        For BOX jobs, also cascade-start any children whose conditions
        are satisfied within the same tick.

        Parameters
        ----------
        session:
            Open session (shared with caller's tick transaction).
        row:
            The JobRow to activate.
        now:
            Current datetime, used for last_start timestamp.
        """
        if row.job_type == "BOX":
            self._activate_box(session, row, now)
        else:
            self._activate_cmd(session, row, now)

    def _activate_cmd(
        self,
        session: Session,
        row: JobRow,
        now: datetime,
    ) -> None:
        """Transition a CMD/FILEWATCH/FTP job to STARTING and dispatch."""
        try:
            validate_transition(row.job_name, row.status or "INACTIVE", "STARTING")
        except InvalidTransitionError as exc:
            logger.warning("activate_cmd: %s", exc)
            return

        row.status     = "STARTING"
        row.last_start = now
        row.last_run_date = now.strftime("%Y-%m-%d")
        logger.info("STARTJOB: %r → STARTING", row.job_name)

        # Call the dispatcher (stub in Phase 4, real in Phase 5)
        self._dispatch_fn(session, row)

        if self.auto_complete and row.status == "RUNNING":
            row.status  = "SUCCESS"
            row.last_end = now
            logger.info("STARTJOB: %r → SUCCESS (auto-complete stub)", row.job_name)

    def _activate_box(
        self,
        session: Session,
        row: JobRow,
        now: datetime,
    ) -> None:
        """
        Transition a BOX job to ACTIVATED and cascade-start eligible children.

        AutoSys BOX behaviour:
        1. BOX: INACTIVE → ACTIVATED (the "window" is open)
        2. Children with no condition (or satisfied conditions) start immediately.
        3. BOX transitions ACTIVATED → RUNNING once any child is RUNNING.
        4. BOX transitions RUNNING → SUCCESS when ALL children are SUCCESS.
        5. BOX transitions RUNNING → FAILURE when ANY child is FAILURE.
        """
        try:
            validate_transition(row.job_name, row.status or "INACTIVE", "ACTIVATED")
        except InvalidTransitionError as exc:
            logger.warning("activate_box: %s", exc)
            return

        row.status     = "ACTIVATED"
        row.last_start = now
        row.last_run_date = now.strftime("%Y-%m-%d")
        logger.info("STARTJOB: BOX %r → ACTIVATED", row.job_name)

        # Cascade: start children whose conditions are satisfied
        self._cascade_box_children(session, row.job_name, now)

        # Update BOX status based on children outcomes
        self._update_box_status(session, row, now)

    def _cascade_box_children(
        self,
        session: Session,
        box_name: str,
        now: datetime,
    ) -> None:
        """Start all INACTIVE children of *box_name* whose conditions are met."""
        from sqlalchemy import select
        from autosys.db.schema import JobRow as JR

        children = list(session.scalars(
            select(JR).where(JR.box_name == box_name)
        ))

        if not children:
            return

        # Build snapshot including the now-ACTIVATED box
        snapshot = build_status_snapshot(session)

        for child in children:
            if not is_startable(child.status or "INACTIVE"):
                continue
            if is_satisfied(child.condition, snapshot):
                logger.info(
                    "BOX cascade: starting child %r of %r",
                    child.job_name, box_name,
                )
                self._activate_cmd(session, child, now)
                # Update snapshot so subsequent siblings see this child's new status
                snapshot[child.job_name] = child.status or "INACTIVE"

    def _update_box_status(
        self,
        session: Session,
        box_row: JobRow,
        now: datetime,
    ) -> None:
        """
        Recompute and update the BOX's status from the current status of its children.

        Called after cascade-starting children.  If children ran to completion
        (auto_complete=True in Phase 4), the BOX should be updated accordingly.
        """
        from sqlalchemy import select
        from autosys.db.schema import JobRow as JR

        children = list(session.scalars(
            select(JR).where(JR.box_name == box_row.job_name)
        ))

        if not children:
            # Empty BOX → SUCCESS immediately
            box_row.status  = "SUCCESS"
            box_row.last_end = now
            return

        statuses = {c.job_name: (c.status or "INACTIVE") for c in children}
        vals     = list(statuses.values())

        if any(s == "RUNNING" or s == "STARTING" for s in vals):
            if box_row.status == "ACTIVATED":
                box_row.status = "RUNNING"
        elif any(s == "FAILURE" for s in vals):
            box_row.status  = "FAILURE"
            box_row.last_end = now
            logger.info("BOX %r → FAILURE (child failed)", box_row.job_name)
        elif all(s == "SUCCESS" for s in vals):
            box_row.status  = "SUCCESS"
            box_row.last_end = now
            logger.info("BOX %r → SUCCESS (all children succeeded)", box_row.job_name)
        elif any(s == "INACTIVE" for s in vals):
            # Some children haven't run yet — BOX stays ACTIVATED
            pass


# ===========================================================================
# Module-level default processor instance
# ===========================================================================

#: Default processor singleton — import and use in the CLI scheduler command.
default_processor = EventProcessor()
