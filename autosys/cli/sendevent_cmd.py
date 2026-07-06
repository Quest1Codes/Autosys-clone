"""
autosys sendevent — enqueue an event into the AutoSys event queue.

This command mirrors the real AutoSys ``sendevent`` utility.  It writes a
row to the ``event_queue`` table; the Event Processor daemon (Phase 4) picks
it up and drives the job state machine.

Supported event types
---------------------
STARTJOB        Start a job immediately (respects its run window).
FORCE_STARTJOB  Start a job even if its conditions aren't met.
KILLJOB         Send SIGTERM to a running job's process.
CHANGE_STATUS   Manually override a job's status in the database.
SET_GLOBAL      Upsert a global variable value.
ON_HOLD         Put a job on hold (won't start until taken off hold).
ON_ICE          Freeze a job (removed from consideration entirely).
OFF_HOLD        Remove a job from hold.
OFF_ICE         Unfreeze a job.
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
    $ sendevent -E ON_HOLD         -J extract_sales
    $ sendevent -E COMMENT         -J extract_sales -c "User authorized"
"""

from __future__ import annotations

import sys

import click
from rich.console import Console

from autosys.db.connection import sync_session
from autosys.db.repository import events as event_repo, jobs as job_repo
from autosys.models.event import Event
from autosys.models.enums import EventType

_console = Console()
_err     = Console(stderr=True)

# Event types that require a -J (job_name) option
_NEED_JOB = frozenset({
    "STARTJOB", "FORCE_STARTJOB", "KILLJOB", "CHANGE_STATUS",
    "HOLD_JOB", "JOB_ON_ICE", "JOB_OFF_HOLD", "JOB_OFF_ICE",
    "COMMENT", "REPLY_RESPONSE", "ALARM",
})

# Event types that require -G (global_name) and -v (value)
_NEED_GLOBAL = frozenset({"SET_GLOBAL"})

# Valid event type names (for the choice validation)
_VALID_EVENTS = [e.value for e in EventType]


@click.command(name="sendevent")
@click.option("-E", "--event", "event_type", required=True,
              type=click.Choice(_VALID_EVENTS, case_sensitive=False),
              help="Event type to send.")
@click.option("-J", "--job",    "job_name",     default=None,
              help="Target job name (required for most event types).")
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
    new_status: str | None,
    comment_text: str | None,
    global_name: str | None,
    global_value: str | None,
    source: str,
) -> None:
    """
    Send an event to the AutoSys event queue.

    The event is written to the database immediately.  The Event Processor
    daemon (Phase 4) will pick it up on its next poll tick and drive the
    job state machine accordingly.

    Example
    -------
    \\b
        $ autosys sendevent -E STARTJOB -J extract_sales
        $ autosys sendevent -E SET_GLOBAL -G RUN_DATE -v 20260625
        $ autosys sendevent -E CHANGE_STATUS -J nightly_cleanup -s INACTIVE
    """
    etype = event_type.upper()

    # --- Validate required arguments per event type ---
    if etype in _NEED_JOB and not job_name:
        _err.print(
            f"[red]Error:[/red] event type {etype!r} requires -J / --job"
        )
        sys.exit(1)

    if etype == "CHANGE_STATUS" and not new_status:
        _err.print(
            "[red]Error:[/red] CHANGE_STATUS requires -s / --status"
        )
        sys.exit(1)

    if etype in _NEED_GLOBAL and (not global_name or global_value is None):
        _err.print(
            "[red]Error:[/red] SET_GLOBAL requires -G / --global-name "
            "and -v / --value"
        )
        sys.exit(1)

    # --- Check the target job exists (warn but don't block) ---
    if job_name:
        with sync_session() as session:
            row = job_repo.get_row(session, job_name)
            if row is None:
                _err.print(
                    f"[yellow]Warning:[/yellow] job {job_name!r} not found "
                    f"in the database — event queued anyway."
                )

    # --- Build and enqueue the Event ---
    event = Event(
        event_type   = etype,
        job_name     = job_name,
        new_status   = new_status,
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
        + (f" → job [cyan]{job_name}[/cyan]" if job_name else "")
        + (f" → {global_name}={global_value!r}" if global_name else "")
    )
    _console.print(f"  event_id: [dim]{eid}[/dim]")
    _console.print()
