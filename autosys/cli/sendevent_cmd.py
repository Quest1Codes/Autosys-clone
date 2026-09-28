"""
autosys sendevent — enqueue an event into the AutoSys event queue.

This command mirrors the real AutoSys ``sendevent`` utility.  It writes a
row to the ``event_queue`` table; the Event Processor daemon picks it up
and drives the job state machine.

Supported event types
---------------------
STARTJOB        Start a job immediately (respects its run window).
FORCE_STARTJOB  Start a job even if its conditions aren't met.
KILLJOB         Send SIGTERM to a running job's process.
CHANGE_STATUS   Manually override a job's status in the database.
SET_GLOBAL      Upsert a global variable value.
JOB_ON_HOLD     Put a job on hold (won't start until taken off hold).
                (HOLD_JOB is accepted as a legacy alias.)
JOB_ON_ICE      Freeze a job (removed from consideration entirely).
JOB_OFF_HOLD    Remove a job from hold.
JOB_OFF_ICE     Unfreeze a job.
JOB_ON_NOEXEC   Bypass job execution (evaluated as SUCCESS once its
                conditions are met, without actually running).
JOB_OFF_NOEXEC  Release a job from ON_NOEXEC.
MACH_ONLINE     Mark a machine online.
MACH_OFFLINE    Mark a machine offline.
DELETEJOB       Delete a job definition.
STOP_DEMON      Gracefully stop the Event Processor daemon.
COMMENT         Add an audit comment to the event history.
REPLY_RESPONSE  Answer a manual intervention prompt (for WAIT_REPLY jobs).
ALARM           Raise an alarm on a job.
RELEASE_RESOURCE Manually free up stuck virtual load-balancing resources.

Real AutoSys usage
------------------
    $ sendevent -E STARTJOB        -J extract_sales
    $ sendevent -E FORCE_STARTJOB  -J extract_sales
    $ sendevent -E KILLJOB         -J extract_sales
    $ sendevent -E CHANGE_STATUS   -J extract_sales -s INACTIVE
    $ sendevent -E SET_GLOBAL      -G MY_DATE       -v 20260625
    $ sendevent -E JOB_ON_HOLD     -J extract_sales
    $ sendevent -E JOB_ON_NOEXEC   -J extract_sales
    $ sendevent -E MACH_OFFLINE    -N etl-server-01
    $ sendevent -E DELETEJOB       -J old_job
    $ sendevent -E STOP_DEMON
    $ sendevent -E COMMENT         -J extract_sales -c "User authorized"
"""

from __future__ import annotations

import sys

import click
from rich.console import Console

from autosys.db.connection import sync_session
from autosys.db.repository import events as event_repo, jobs as job_repo, machines as machine_repo
from autosys.models.event import Event
from autosys.models.enums import EventType

_console = Console()
_err     = Console(stderr=True)

# Event types that require a -J (job_name) option
_NEED_JOB = frozenset({
    "STARTJOB", "FORCE_STARTJOB", "KILLJOB", "CHANGE_STATUS",
    "JOB_ON_HOLD", "HOLD_JOB", "JOB_ON_ICE", "JOB_OFF_HOLD", "JOB_OFF_ICE",
    "JOB_ON_NOEXEC", "JOB_OFF_NOEXEC", "DELETEJOB",
    "COMMENT", "REPLY_RESPONSE", "ALARM",
})

# Event types that require -G (global_name) and -v (value)
_NEED_GLOBAL = frozenset({"SET_GLOBAL"})

# Event types that require -N (machine_name) instead of -J
_NEED_MACHINE = frozenset({"MACH_ONLINE", "MACH_OFFLINE"})

# Valid event type names (for the choice validation)
_VALID_EVENTS = [e.value for e in EventType]


@click.command(name="sendevent")
@click.option("-E", "--event", "event_type", required=True,
              type=click.Choice(_VALID_EVENTS, case_sensitive=False),
              help="Event type to send.")
@click.option("-J", "--job",    "job_name",     default=None,
              help="Target job name (required for most event types).")
@click.option("-N", "--machine", "machine_name", default=None,
              help="Target machine name (for MACH_ONLINE/MACH_OFFLINE).")
@click.option("-s", "--status", "new_status",   default=None,
              help="New status for CHANGE_STATUS events.")
@click.option("-c", "--comment", "comment_text", default=None,
              help="Comment text for COMMENT events.")
@click.option("-G", "--global-name", "global_name", default=None,
              help="Global variable name for SET_GLOBAL events.")
@click.option("-v", "--value",  "global_value", default=None,
              help="Global variable value for SET_GLOBAL events.")
@click.option("--source", default="cli",
              type=click.Choice(["cli", "api", "scheduler", "agent", "internal"],
                                case_sensitive=False),
              help="Event source tag (default: cli).")
def sendevent(
    event_type: str,
    job_name:   str | None,
    machine_name: str | None,
    new_status: str | None,
    comment_text: str | None,
    global_name: str | None,
    global_value: str | None,
    source: str,
) -> None:
    """
    Send an event to the AutoSys event queue.

    The event is written to the database immediately.  The Event Processor
    daemon will pick it up on its next poll tick and drive the job state
    machine accordingly.

    Example
    -------
    \\b
        $ autosys sendevent -E STARTJOB -J extract_sales
        $ autosys sendevent -E SET_GLOBAL -G RUN_DATE -v 20260625
        $ autosys sendevent -E CHANGE_STATUS -J nightly_cleanup -s INACTIVE
    """
    from autosys.models.enums import JobStatus

    etype = event_type.upper()

    # --- Validate required arguments per event type ---
    if etype in _NEED_JOB and not job_name:
        _err.print(
            f"[red]Error:[/red] event type {etype!r} requires -J / --job"
        )
        sys.exit(1)

    if etype in _NEED_MACHINE and not machine_name:
        _err.print(
            f"[red]Error:[/red] event type {etype!r} requires -N / --machine"
        )
        sys.exit(1)

    new_status_int: int | None = None
    if etype == "CHANGE_STATUS":
        if not new_status:
            _err.print(
                "[red]Error:[/red] CHANGE_STATUS requires -s / --status"
            )
            sys.exit(1)
        try:
            new_status_int = JobStatus[new_status.upper()].value
        except KeyError:
            _err.print(
                f"[red]Error:[/red] unknown status {new_status!r}. "
                f"Valid: {[s.name for s in JobStatus]}"
            )
            sys.exit(1)

    if etype in _NEED_GLOBAL and (not global_name or global_value is None):
        _err.print(
            "[red]Error:[/red] SET_GLOBAL requires -G / --global-name "
            "and -v / --value"
        )
        sys.exit(1)

    # --- Reject events for jobs that don't exist (as real AutoSys does) ---
    if job_name and etype not in _NEED_MACHINE:
        with sync_session() as session:
            row = job_repo.get_row(session, job_name)
            if row is None:
                _err.print(
                    f"[red]Error:[/red] job {job_name!r} not found "
                    f"in the database — event not queued."
                )
                sys.exit(1)

    if machine_name:
        with sync_session() as session:
            if machine_repo.get(session, machine_name) is None:
                _err.print(
                    f"[red]Error:[/red] machine {machine_name!r} not registered "
                    f"in the database — event not queued."
                )
                sys.exit(1)

    # MACH_ONLINE/MACH_OFFLINE carry their target machine name in job_name
    # (EventQueueRow has no separate "target machine" column).
    target_name = machine_name if etype in _NEED_MACHINE else job_name

    # --- Build and enqueue the Event ---
    event = Event(
        event_type   = etype,
        job_name     = target_name,
        new_status   = new_status_int,
        global_name  = global_name,
        global_value = global_value,
        comment      = comment_text,
        source       = source.lower(),
    )

    with sync_session() as session:
        eid = event_repo.enqueue(session, event)

    # --- Success output ---
    _console.print()
    _console.print(
        f"[green]Event queued:[/green] [bold]{etype}[/bold]"
        + (f" → machine [cyan]{machine_name}[/cyan]" if machine_name else "")
        + (f" → job [cyan]{job_name}[/cyan]" if job_name and etype not in _NEED_MACHINE else "")
        + (f" → {global_name}={global_value!r}" if global_name else "")
    )
    _console.print(f"  event_id: [dim]{eid}[/dim]")
    _console.print()
