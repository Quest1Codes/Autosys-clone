"""
AlarmManager — evaluates alarm conditions and writes AlarmRow records.

Called at the end of each EPS tick via ``EventProcessor.process_one_tick``.
It checks the current DB state and:

1. ALARM_IF_FAIL       — job is FAILURE + alarm_if_fail=True → raise alarm
2. ALARM_IF_TERMINATED — job is TERMINATED + alarm_if_terminated=True → raise alarm
3. MAX_RUN_ALARM       — job is RUNNING > max_run_alarm minutes → raise alarm
4. MIN_RUN_ALARM       — job just finished < min_run_alarm minutes → raise alarm
5. HEARTBEAT_FAIL      — machine is DOWN (checked via MachineRow) → raise alarm
6. MUST_START_ALARM    — job hasn't STARTED by its must_start_times deadline
                         → raise alarm
7. MUST_COMPLETE_ALARM — job hasn't COMPLETED by its must_complete_times
                         deadline → raise alarm
8. Deduplication       — if an identical active alarm exists, skip it
9. Auto-resolve        — if job goes SUCCESS and has an active ALARM_IF_FAIL alarm,
                         clear it automatically
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from autosys.timeutil import utcnow
from typing import Optional

from loguru import logger
from sqlalchemy import select
from sqlalchemy.orm import Session

from autosys.db.schema import AlarmRow, JobRow, JobRunRow, MachineRow
from autosys.models.enums import JobStatus


def _parse_must_times(raw: str) -> tuple[str, list]:
    """
    Parse a ``must_start_times`` / ``must_complete_times`` JIL attribute
    value into a normalised (kind, values) pair.

    Real AutoSys accepts either:
      - a comma-separated list of absolute "hh:mm" deadlines, e.g.
        ``"06:00, 07:00"`` (real AutoSys additionally requires
        ``date_conditions`` + ``start_times`` for these to take effect —
        we don't re-enforce that gate here, we just use the times as
        wall-clock deadlines for "today"), or
      - a single "+minutes" value, relative to the job's nearest
        preceding ``start_times`` entry.

    Returns
    -------
    ("absolute", ["06:00", "07:00"])  or  ("relative", [30])
    """
    raw = (raw or "").strip()
    if not raw:
        return ("absolute", [])
    if raw.startswith("+"):
        try:
            return ("relative", [int(raw[1:].strip())])
        except ValueError:
            return ("absolute", [])
    times = [t.strip().strip('"') for t in raw.split(",") if t.strip()]
    return ("absolute", times)


class AlarmManager:
    """
    Evaluates alarm conditions for all jobs after an EPS tick.

    Parameters
    ----------
    now_fn:
        Callable that returns the current datetime.  Injectable for tests.
    """

    def __init__(self, now_fn=None) -> None:
        self._now_fn = now_fn or utcnow

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def evaluate(self, session: Session, now: Optional[datetime] = None) -> list[AlarmRow]:
        """
        Evaluate all alarm conditions.

        Returns the list of newly created AlarmRow objects (not yet committed —
        they are added to the session by this method; the caller's transaction
        will commit them).

        Parameters
        ----------
        session:
            Open SQLAlchemy session (shared with the EPS tick transaction).
        now:
            Current time.  Defaults to ``utcnow()``.
        """
        if now is None:
            now = self._now_fn()

        new_alarms: list[AlarmRow] = []
        jobs = session.execute(select(JobRow)).scalars().all()

        for job in jobs:
            new_alarms.extend(self._check_job(session, job, now))

        new_alarms.extend(self._check_machines(session, now))
        new_alarms.extend(self._check_min_run(session, now))
        new_alarms.extend(self._check_must_start(session, now))
        new_alarms.extend(self._check_must_complete(session, now))

        self._auto_resolve(session, jobs, now)

        for alarm in new_alarms:
            logger.info(
                "AlarmManager: raised %s alarm for %r",
                alarm.alarm_type, alarm.job_name,
            )

        return new_alarms

    # ------------------------------------------------------------------
    # Per-job checks
    # ------------------------------------------------------------------

    def _check_job(
        self,
        session: Session,
        job: JobRow,
        now: datetime,
    ) -> list[AlarmRow]:
        new: list[AlarmRow] = []
        status = job.status if job.status is not None else JobStatus.INACTIVE.value

        # 1. FAILURE alarm
        if status == JobStatus.FAILURE.value and job.alarm_if_fail:
            if not self._has_active_alarm(session, job.job_name, "ALARM_IF_FAIL"):
                msg = (
                    f"Job {job.job_name!r} reached FAILURE"
                    f" at {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                )
                new.append(self._raise_alarm(
                    session, job.job_name, "ALARM_IF_FAIL", msg,
                    job_status=JobStatus.FAILURE.value,
                ))

        # 2. TERMINATED alarm
        if status == JobStatus.TERMINATED.value and job.alarm_if_terminated:
            if not self._has_active_alarm(session, job.job_name, "ALARM_IF_TERMINATED"):
                msg = (
                    f"Job {job.job_name!r} was TERMINATED"
                    f" at {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                )
                new.append(self._raise_alarm(
                    session, job.job_name, "ALARM_IF_TERMINATED", msg,
                    job_status=JobStatus.TERMINATED.value,
                ))

        # 3. MAX_RUN alarm
        if status == JobStatus.RUNNING.value and job.max_run_alarm and job.last_start:
            elapsed_mins = (now - job.last_start).total_seconds() / 60.0
            if elapsed_mins > job.max_run_alarm:
                if not self._has_active_alarm(session, job.job_name, "MAX_RUN_ALARM"):
                    msg = (
                        f"Job {job.job_name!r} has been RUNNING for"
                        f" {int(elapsed_mins)} minutes,"
                        f" exceeding max_run_alarm threshold of {job.max_run_alarm} minutes"
                    )
                    new.append(self._raise_alarm(
                        session, job.job_name, "MAX_RUN_ALARM", msg,
                        job_status=JobStatus.RUNNING.value,
                    ))

        return new

    def _check_min_run(
        self,
        session: Session,
        now: datetime,
    ) -> list[AlarmRow]:
        """
        Check for suspiciously short runs (MIN_RUN_ALARM).

        Looks for JobRunRow records that:
        - Have both start_time and end_time set (completed run)
        - Finished within the last 5 minutes (to avoid re-raising on old runs)
        - Ran for less than job.min_run_alarm minutes
        - Do not already have a MIN_RUN_ALARM for this run_id
        """
        new: list[AlarmRow] = []
        cutoff = now - timedelta(minutes=5)

        recent_runs = session.execute(
            select(JobRunRow)
            .where(JobRunRow.end_time.is_not(None))
            .where(JobRunRow.end_time >= cutoff)
        ).scalars().all()

        for run in recent_runs:
            job = session.get(JobRow, run.job_name)
            if job is None or not job.min_run_alarm:
                continue

            if run.start_time is None or run.end_time is None:
                continue

            duration_mins = (run.end_time - run.start_time).total_seconds() / 60.0
            if duration_mins < job.min_run_alarm:
                if not self._has_active_alarm(
                    session, job.job_name, "MIN_RUN_ALARM", run_id=run.run_id
                ):
                    msg = (
                        f"Job {job.job_name!r} finished in"
                        f" {duration_mins:.1f} minutes,"
                        f" below min_run_alarm threshold of {job.min_run_alarm} minutes"
                        f" (possible empty feed)"
                    )
                    new.append(self._raise_alarm(
                        session, job.job_name, "MIN_RUN_ALARM", msg,
                        run_id=run.run_id,
                        job_status=run.status if run.status is not None else JobStatus.SUCCESS.value,
                    ))
        return new

    @staticmethod
    def _nearest_preceding_start_minutes(
        start_times_raw: Optional[str],
        now_minutes: int,
    ) -> Optional[int]:
        """
        Parse ``start_times`` ("06:00,18:00") and return the largest entry
        (in minutes-since-midnight) that is <= *now_minutes*, or None if
        there isn't one (or the job has no start_times at all).

        Used to anchor the "+minutes" relative form of must_start_times /
        must_complete_times to the schedule slot that most recently fired.
        """
        if not start_times_raw:
            return None
        candidates: list[int] = []
        for t in start_times_raw.split(","):
            t = t.strip().strip('"')
            if not t:
                continue
            try:
                h, m = t.split(":")
                candidates.append(int(h) * 60 + int(m))
            except (ValueError, IndexError):
                continue
        preceding = [c for c in candidates if c <= now_minutes]
        return max(preceding) if preceding else None

    def _must_time_deadline_passed(
        self,
        job: JobRow,
        raw: str,
        now: datetime,
    ) -> bool:
        """
        Return True if *now* is past the must_start_times/must_complete_times
        deadline encoded in *raw* (see ``_parse_must_times``).
        """
        kind, values = _parse_must_times(raw)
        now_minutes = now.hour * 60 + now.minute

        if kind == "absolute":
            now_hhmm = now.strftime("%H:%M")
            return any(now_hhmm >= t for t in values if t)

        # Relative form ("+N minutes"), anchored to the nearest preceding
        # start_times entry.  NOTE (simplification): only start_times-based
        # jobs are supported here — for start_mins (sub-hourly, repeating)
        # jobs "nearest preceding start" is ambiguous/less meaningful, so
        # those are simply skipped (treated as "deadline not reached").
        if not values:
            return False
        n = values[0]
        preceding = self._nearest_preceding_start_minutes(job.start_times, now_minutes)
        if preceding is None:
            return False
        return now_minutes >= preceding + n

    def _check_must_start(
        self,
        session: Session,
        now: datetime,
    ) -> list[AlarmRow]:
        """
        MUST_START_ALARM — job hasn't STARTED by its must_start_times deadline.

        Only considers jobs that have not yet started *today* (tracked via
        ``last_run_date``, which is stamped the moment a job transitions to
        STARTING — see ``EventProcessor._activate_cmd``).
        """
        new: list[AlarmRow] = []
        today_str = now.strftime("%Y-%m-%d")

        jobs = session.execute(
            select(JobRow).where(JobRow.must_start_times.is_not(None))
        ).scalars().all()

        for job in jobs:
            if not job.must_start_times:
                continue
            if job.last_run_date == today_str:
                continue  # already started today
            if not self._must_time_deadline_passed(job, job.must_start_times, now):
                continue
            if self._has_active_alarm(session, job.job_name, "MUST_START_ALARM"):
                continue
            msg = (
                f"Job {job.job_name!r} has not STARTED by its must_start_times"
                f" deadline ({job.must_start_times!r}) as of"
                f" {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
            )
            new.append(self._raise_alarm(
                session, job.job_name, "MUST_START_ALARM", msg,
                job_status=job.status,
            ))
        return new

    def _check_must_complete(
        self,
        session: Session,
        now: datetime,
    ) -> list[AlarmRow]:
        """
        MUST_COMPLETE_ALARM — job hasn't COMPLETED by its must_complete_times
        deadline.

        Only considers jobs that DID start today (``last_run_date == today``)
        but have not yet reached a terminal state (SUCCESS/FAILURE/TERMINATED).
        """
        new: list[AlarmRow] = []
        today_str = now.strftime("%Y-%m-%d")
        terminal = {
            JobStatus.SUCCESS.value,
            JobStatus.FAILURE.value,
            JobStatus.TERMINATED.value,
        }

        jobs = session.execute(
            select(JobRow).where(JobRow.must_complete_times.is_not(None))
        ).scalars().all()

        for job in jobs:
            if not job.must_complete_times:
                continue
            if job.last_run_date != today_str:
                continue  # hasn't started today — nothing to complete yet
            status = job.status if job.status is not None else JobStatus.INACTIVE.value
            if status in terminal:
                continue
            if not self._must_time_deadline_passed(job, job.must_complete_times, now):
                continue
            if self._has_active_alarm(session, job.job_name, "MUST_COMPLETE_ALARM"):
                continue
            msg = (
                f"Job {job.job_name!r} has not COMPLETED by its must_complete_times"
                f" deadline ({job.must_complete_times!r}) as of"
                f" {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
            )
            new.append(self._raise_alarm(
                session, job.job_name, "MUST_COMPLETE_ALARM", msg,
                job_status=job.status,
            ))
        return new

    def _check_machines(
        self,
        session: Session,
        now: datetime,
    ) -> list[AlarmRow]:
        """
        Raise HEARTBEAT_FAIL for each job currently RUNNING/STARTING on a DOWN machine.

        Machine names are not job_names (FK constraint), so we attach the alarm
        to each affected job.  If no jobs are stuck on the machine we skip it
        (the machine status itself is visible in autorep output).
        """
        new: list[AlarmRow] = []
        machines = session.execute(
            select(MachineRow).where(MachineRow.status == "DOWN")
        ).scalars().all()

        for m in machines:
            affected_jobs = session.execute(
                select(JobRow)
                .where(JobRow.machine == m.machine_name)
                .where(JobRow.status.in_([JobStatus.RUNNING.value, JobStatus.STARTING.value]))
            ).scalars().all()

            for job in affected_jobs:
                if not self._has_active_alarm(session, job.job_name, "HEARTBEAT_FAIL"):
                    msg = (
                        f"System Agent on {m.machine_name!r} is DOWN —"
                        f" job {job.job_name!r} cannot be reached"
                        f" at {now.strftime('%Y-%m-%d %H:%M:%S')} UTC"
                    )
                    new.append(self._raise_alarm(
                        session, job.job_name, "HEARTBEAT_FAIL", msg,
                        job_status=job.status,
                    ))
        return new

    # ------------------------------------------------------------------
    # Auto-resolve
    # ------------------------------------------------------------------

    def _auto_resolve(
        self,
        session: Session,
        jobs: list[JobRow],
        now: datetime,
    ) -> None:
        """
        Automatically clear ALARM_IF_FAIL alarms when a job recovers to SUCCESS.

        This mirrors real AutoSys behaviour where a job that fails and is later
        retried successfully will auto-clear its own alarm.

        One query for the open ALARM_IF_FAIL alarms, matched against the jobs
        now in SUCCESS (task E6). It used to issue one query per SUCCESS job
        on every tick -- tens of thousands of queries per tick on a large
        estate, almost all of them returning nothing.
        """
        succeeded = {
            job.job_name for job in jobs
            if (job.status if job.status is not None else JobStatus.INACTIVE.value)
            == JobStatus.SUCCESS.value
        }
        if not succeeded:
            return

        stmt = (
            select(AlarmRow)
            .where(AlarmRow.alarm_type == "ALARM_IF_FAIL")
            .where(AlarmRow.cleared_at.is_(None))
        )
        for alarm in session.execute(stmt).scalars().all():
            if alarm.job_name not in succeeded:
                continue
            alarm.cleared_at = now
            alarm.cleared_by = "auto"
            logger.info(
                "AlarmManager: auto-resolved ALARM_IF_FAIL for %r (job now SUCCESS)",
                alarm.job_name,
            )

    # ------------------------------------------------------------------
    # Low-level helpers
    # ------------------------------------------------------------------

    def _has_active_alarm(
        self,
        session: Session,
        job_name: str,
        alarm_type: str,
        run_id: Optional[str] = None,
    ) -> bool:
        """Return True if an identical unresolved alarm already exists."""
        stmt = (
            select(AlarmRow)
            .where(AlarmRow.job_name == job_name)
            .where(AlarmRow.alarm_type == alarm_type)
            .where(AlarmRow.cleared_at.is_(None))
        )
        if run_id is not None:
            stmt = stmt.where(AlarmRow.run_id == run_id)
        return session.execute(stmt).first() is not None

    def _raise_alarm(
        self,
        session: Session,
        job_name: str,
        alarm_type: str,
        message: str,
        run_id: Optional[str] = None,
        job_status: Optional[str] = None,
    ) -> AlarmRow:
        """Create an AlarmRow, add it to the session, and return it."""
        row = AlarmRow(
            alarm_id            = str(uuid.uuid4()),
            job_name            = job_name,
            run_id              = run_id,
            alarm_type          = alarm_type,
            message             = message,
            job_status_at_raise = job_status,
            raised_at           = self._now_fn(),
            notified            = False,
        )
        session.add(row)
        return row
