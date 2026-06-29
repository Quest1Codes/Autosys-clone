"""
autosys scheduler — run the Event Processor daemon.

Commands
--------
autosys scheduler start      Run the EPS in the foreground (Ctrl+C to stop).
autosys scheduler run-once   Process all pending events exactly once and exit.
autosys scheduler status     Show pending event count and INACTIVE job counts.

The ``start`` command runs ``EventProcessor.run_forever()`` in an asyncio
event loop.  For production use, wrap it in a systemd unit or supervisor.

The ``run-once`` command is useful for:
  - Manual debugging ("what events are pending right now?")
  - Cron-based scheduling (fire every minute, process queue, exit)
  - Integration tests that want deterministic event processing

Example
-------
\\b
    $ autosys jil import examples/demo_etl.jil
    $ autosys sendevent -E STARTJOB -J check_source_ready
    $ autosys scheduler run-once
      Processed 1 event(s).
      check_source_ready: INACTIVE → STARTING → RUNNING → SUCCESS

    $ autosys autorep -J %
"""

from __future__ import annotations

import asyncio
import sys

import click
from rich.console import Console
from rich.table import Table
from rich import box as rich_box

from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo, events as event_repo
from autosys.scheduler.event_processor import EventProcessor

_console = Console()
_err     = Console(stderr=True)


@click.group(name="scheduler")
def scheduler_group() -> None:
    """Event Processor daemon commands."""


# ---------------------------------------------------------------------------
# scheduler start
# ---------------------------------------------------------------------------

@scheduler_group.command("start")
@click.option("--poll-interval", "-p", default=1.0, show_default=True,
              help="Seconds between event queue polls.")
@click.option("--auto-complete/--no-auto-complete", default=True,
              help="Phase 4 stub: auto-complete jobs immediately after RUNNING.")
def scheduler_start(poll_interval: float, auto_complete: bool) -> None:
    """
    Run the Event Processor in the foreground.

    Polls the event queue every POLL_INTERVAL seconds and drives the job
    state machine.  Press Ctrl+C to stop.

    In Phase 4 the dispatcher is a stub that transitions RUNNING → SUCCESS
    immediately (no real subprocess is launched).  Phase 5 adds real System
    Agent dispatch.

    Example
    -------
    \\b
        $ autosys scheduler start
        Event Processor started (poll interval: 1.0s)
        ^C  Stopped.
    """
    processor = EventProcessor(
        poll_interval = poll_interval,
        auto_complete = auto_complete,
    )

    _console.print(
        f"\n[bold green]Event Processor starting[/bold green] "
        f"(poll interval: {poll_interval:.1f}s)  "
        f"[dim]Ctrl+C to stop[/dim]\n"
    )

    try:
        asyncio.run(processor.run_forever())
    except KeyboardInterrupt:
        _console.print("\n[yellow]Stopped.[/yellow]\n")
        sys.exit(0)


# ---------------------------------------------------------------------------
# scheduler run-once
# ---------------------------------------------------------------------------

@scheduler_group.command("run-once")
@click.option("--auto-complete/--no-auto-complete", default=True,
              help="Phase 4 stub: auto-complete jobs immediately after RUNNING.")
@click.option("--quiet", "-q", is_flag=True, default=False)
def scheduler_run_once(auto_complete: bool, quiet: bool) -> None:
    """
    Process all currently pending events exactly once, then exit.

    Useful for debugging, testing, or cron-based scheduling.
    The jobs' final statuses are shown after processing.

    Example
    -------
    \\b
        $ autosys scheduler run-once
        Processing pending events...

          STARTJOB  →  check_source_ready     INACTIVE → SUCCESS
          SET_GLOBAL → RUN_DATE = 20260625

        1 event(s) processed.
    """
    processor = EventProcessor(auto_complete=auto_complete)

    if not quiet:
        _console.print("\n[bold]Processing pending events...[/bold]\n")

    # Capture before/after statuses for display
    with sync_session() as session:
        before = {r.job_name: r.status for r in job_repo.list_all(session)}
        pending_before = len(event_repo.dequeue_pending(session))

    with sync_session() as session:
        n = processor.process_one_tick(session)

    with sync_session() as session:
        after = {r.job_name: r.status for r in job_repo.list_all(session)}

    if not quiet:
        # Show jobs whose status changed
        changed = [
            (name, before.get(name, "N/A"), after.get(name, "N/A"))
            for name in sorted(set(before) | set(after))
            if before.get(name) != after.get(name)
        ]
        if changed:
            for name, old_s, new_s in changed:
                _console.print(
                    f"  [cyan]{name:<30}[/cyan]  "
                    f"[dim]{old_s}[/dim] → [green]{new_s}[/green]"
                )
            _console.print()

        _console.print(
            f"[green]{n} event(s) processed.[/green]\n"
            if n else "[dim]No events pending.[/dim]\n"
        )


# ---------------------------------------------------------------------------
# scheduler status
# ---------------------------------------------------------------------------

@scheduler_group.command("status")
def scheduler_status() -> None:
    """
    Show a summary of the scheduler's current state.

    Displays the count of pending events and the distribution of job statuses.

    Example
    -------
    \\b
        $ autosys scheduler status
        Pending events:    2
        Jobs by status:
          INACTIVE    5
          RUNNING     1
          SUCCESS     1
    """
    with sync_session() as session:
        pending = len(event_repo.dequeue_pending(session))
        rows = job_repo.list_all(session)

    status_counts: dict[str, int] = {}
    for row in rows:
        s = row.status or "INACTIVE"
        status_counts[s] = status_counts.get(s, 0) + 1

    _console.print()
    _console.print(f"[bold]Pending events:[/bold]  {pending}")
    _console.print(f"[bold]Total jobs:    [/bold]  {len(rows)}")

    if status_counts:
        _console.print()
        table = Table(
            box=rich_box.SIMPLE_HEAD,
            show_header=True,
            header_style="bold",
            padding=(0, 1),
        )
        table.add_column("Status", min_width=14)
        table.add_column("Count", justify="right")

        colour_map = {
            "INACTIVE":  "dim",
            "ACTIVATED": "cyan",
            "STARTING":  "yellow",
            "RUNNING":   "blue",
            "SUCCESS":   "green",
            "FAILURE":   "red",
            "TERMINATED":"red",
            "ON_HOLD":   "magenta",
            "ON_ICE":    "magenta",
        }
        for status, count in sorted(status_counts.items()):
            col = colour_map.get(status, "white")
            table.add_row(f"[{col}]{status}[/{col}]", str(count))

        _console.print(table)

    _console.print()
