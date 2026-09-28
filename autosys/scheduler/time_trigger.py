"""
Time trigger — computes which INACTIVE jobs should fire at a given moment.

In real AutoSys, the Scheduler ACE evaluates schedule expressions on every
timer tick (default: every second).  A job fires when:

    0. ``date_conditions`` is set (1/True) — real AutoSys treats this as
       *mandatory* for any of the date/time scheduling attributes below to
       take effect.  A job with ``start_times`` but ``date_conditions: 0``
       (the default) never self-triggers on a schedule; it can only be
       started by condition or a manual/API event.
    1. Its ``start_times`` list includes the current HH:MM, OR its
       ``start_mins`` list includes the current minute-of-hour (every hour).
    2. Its ``days_of_week`` list includes today's weekday (or is empty = every day).
    3. It has NOT already been started today (``last_run_date != today``) —
       for ``start_times`` jobs.  ``start_mins`` jobs instead only guard
       against re-firing within the same clock minute, since they are
       meant to fire repeatedly through the day.
    4. The job is in a state that allows starting (INACTIVE, SUCCESS, FAILURE).
    5. The ``exclude_calendar`` does not include today's date.
       (Phase 4: calendar exclusion is a stub — Phase 5 adds full calendar support.)

The trigger fires at most once per minute — the granularity of AutoSys's
``start_times``/``start_mins`` attributes.  If the daemon misses a tick
(e.g. due to restart), it will still catch up within one minute window
after the scheduled time.

Usage
-----
    from autosys.scheduler.time_trigger import get_triggered_jobs

    now = datetime.now()
    with sync_session() as session:
        rows = job_repo.list_all(session)
        for row in get_triggered_jobs(rows, now):
            # enqueue a STARTJOB event for this job
            ...
"""

from __future__ import annotations

from datetime import datetime, date
from typing import Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from autosys.models.calendar import Calendar
    from autosys.db.schema import JobRow

from loguru import logger

# AutoSys uses a 5-character "HH:MM" time format.
_HHMM_FMT = "%H:%M"

# Maps JIL day abbreviation → Python weekday() number (0 = Monday)
_DAY_ABBREV: dict[str, int] = {
    "mo": 0, "tu": 1, "we": 2, "th": 3,
    "fr": 4, "sa": 5, "su": 6,
    # "all" means every day — handled specially
}


def _should_run_today(row, today: date) -> bool:
    """
    Return True if this job is allowed to run on *today* based on
    ``days_of_week``.

    Rules:
    - No ``days_of_week`` → job runs every day.
    - ``all`` in the list → every day.
    - Otherwise → only the listed weekday abbreviations.

    Parameters
    ----------
    row:
        A ``JobRow`` (or any object with a ``days_of_week`` string attr).
    today:
        The date to evaluate against.
    """
    raw = row.days_of_week
    if not raw:
        return True   # no constraint → every day

    days_list = [d.strip().lower() for d in raw.split(",") if d.strip()]

    if "all" in days_list:
        return True

    allowed_weekdays: set[int] = set()
    for abbrev in days_list:
        wd = _DAY_ABBREV.get(abbrev)
        if wd is not None:
            allowed_weekdays.add(wd)

    return today.weekday() in allowed_weekdays


def _already_ran_today(row, today: date) -> bool:
    """
    Return True if this job has already been started today.

    Checks the ``last_run_date`` column (stored as "YYYY-MM-DD").
    This prevents a job from firing twice in the same day when the
    daemon restarts mid-day.
    """
    if not row.last_run_date:
        return False
    return row.last_run_date == today.strftime("%Y-%m-%d")


def _already_fired_this_minute(row, now: datetime) -> bool:
    """
    Return True if ``last_start`` already falls within *now*'s HH:MM minute.

    Used to guard ``start_mins`` jobs against re-firing on every poll tick
    within the same minute, without blocking them for the rest of the day
    the way ``_already_ran_today`` would (``start_mins`` jobs are meant to
    fire repeatedly, e.g. every 15 minutes).
    """
    last_start = getattr(row, "last_start", None)
    if not last_start:
        return False
    return (
        last_start.year == now.year and last_start.month == now.month
        and last_start.day == now.day and last_start.hour == now.hour
        and last_start.minute == now.minute
    )


def _get_matching_start_mins(row, now: datetime) -> list[str]:
    """
    Return the entries from ``row.start_mins`` that match the current
    minute-of-hour.

    AutoSys ``start_mins`` fires the job every hour at the listed minute
    offsets, e.g. ``start_mins: "0,15,30,45"`` runs 4 times an hour.  This
    is the sub-hourly alternative to ``start_times``.
    """
    raw = getattr(row, "start_mins", None)
    if not raw:
        return []

    matched = []
    for m in raw.split(","):
        m = m.strip()
        if not m:
            continue
        try:
            if int(m) == now.minute:
                matched.append(m)
        except ValueError:
            continue
    return matched


def _get_matching_start_times(row, now: datetime) -> list[str]:
    """
    Return the start times from ``row.start_times`` that match the
    current minute (*now*).

    AutoSys evaluates at 1-second granularity but ``start_times`` has
    1-minute resolution.  Any second within the HH:MM minute is a match.
    We return the list of matching time strings for logging.
    """
    raw = row.start_times
    if not raw:
        return []

    current_hhmm = now.strftime(_HHMM_FMT)
    times = [t.strip().strip('"') for t in raw.split(",") if t.strip()]
    return [t for t in times if t == current_hhmm]


def is_triggered(row: 'JobRow', now: datetime, calendars: Optional[dict[str, 'Calendar']] = None) -> bool:
    """
    Return True if this job's schedule should fire right now.

    This is the single predicate the Event Processor calls for every
    INACTIVE job on every tick.  If it returns True, the processor
    enqueues a STARTJOB event for the job.

    Parameters
    ----------
    row:
        A ``JobRow`` (or stub with the same attrs).
    now:
        The current datetime.  Injectable for testing.
    calendars:
        A dictionary of calendar names to Calendar models.

    Returns
    -------
    bool
        True → create a STARTJOB event for this job.
    """
    from autosys.scheduler.state_machine import is_startable

    # Only trigger jobs that are in a startable state
    status = row.status or "INACTIVE"
    if not is_startable(status):
        return False

    # Real AutoSys requires date_conditions: 1 for any date/time scheduling
    # attribute (start_times, start_mins, run_calendar, days_of_week, …) to
    # take effect.  Without it, the job is condition-driven or manual only.
    if not getattr(row, "date_conditions", False):
        return False

    # Must have start_times or start_mins defined
    has_start_times = bool(row.start_times)
    has_start_mins  = bool(getattr(row, "start_mins", None))
    if not has_start_times and not has_start_mins:
        return False

    today = now.date()

    calendars = calendars or {}

    if hasattr(row, "run_calendar") and row.run_calendar:
        run_cal = calendars.get(row.run_calendar)
        if not run_cal or not run_cal.contains(today):
            return False

    if hasattr(row, "exclude_calendar") and row.exclude_calendar:
        ex_cal = calendars.get(row.exclude_calendar)
        if ex_cal and ex_cal.contains(today):
            return False

    if not _should_run_today(row, today):
        return False

    matching_times = _get_matching_start_times(row, now) if has_start_times else []
    matching_mins  = _get_matching_start_mins(row, now) if has_start_mins else []

    if matching_times:
        # Once-per-day guard — a start_times job is expected to fire once
        # per listed time, tracked at day granularity via last_run_date.
        if _already_ran_today(row, today):
            return False
    elif matching_mins:
        # start_mins jobs fire repeatedly through the day; only guard
        # against re-firing within the same minute.
        if _already_fired_this_minute(row, now):
            return False
    else:
        return False

    logger.debug(
        "Time trigger: %r fires at %s (matched times=%s mins=%s)",
        row.job_name, now.strftime(_HHMM_FMT), matching_times, matching_mins,
    )
    return True


def get_triggered_jobs(rows: list, now: datetime, calendars: Optional[dict[str, 'Calendar']] = None) -> list:
    """
    Filter *rows* to those whose schedule fires at *now*.

    Parameters
    ----------
    rows:
        A list of ``JobRow`` objects (from ``job_repo.list_all``).
    now:
        Current datetime (injectable for testing).
    calendars:
        A dictionary of calendar names to Calendar models.

    Returns
    -------
    list[JobRow]
        The subset of *rows* that should be started right now.

    Example
    -------
    >>> import datetime
    >>> rows = job_repo.list_all(session)
    >>> to_start = get_triggered_jobs(rows, datetime.datetime(2026, 6, 25, 6, 0))
    >>> [r.job_name for r in to_start]
    ['demo_etl_box']
    """
    return [row for row in rows if is_triggered(row, now, calendars)]
