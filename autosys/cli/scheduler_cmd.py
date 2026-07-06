"""
autosys scheduler — run the Event Processor daemon.

Commands
--------
autosys scheduler start      Run the EPS in the foreground (Ctrl+C to stop).
autosys scheduler serve      Run the EPS + REST API server together.
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
@click.option("--dry-run", is_flag=True, default=False,
              help=(
                  "Use the stub dispatcher: jobs transition STARTING → RUNNING → SUCCESS "
                  "instantly without executing any scripts or contacting remote agents. "
                  "Ideal for local development and migration complexity analysis."
              ))
@click.option("--auto-complete/--no-auto-complete", default=False,
              help="(Non-dry-run) Auto-complete jobs immediately after RUNNING.")
def scheduler_start(poll_interval: float, dry_run: bool, auto_complete: bool) -> None:
    """
    Run the Event Processor in the foreground.

    Polls the event queue every POLL_INTERVAL seconds and drives the job
    state machine.  Press Ctrl+C to stop.

    Use --dry-run to exercise the full state machine (BOX cascading,
    condition evaluation, alarms) without needing any System Agents running.
    This is the recommended mode for migration analysis.

    Example
    -------
    \\b
        $ autosys scheduler start --dry-run
        Event Processor starting [DRY-RUN] (poll interval: 1.0s)
        ^C  Stopped.
    """
    from autosys.scheduler.event_processor import _stub_dispatch
    if dry_run:
        processor = EventProcessor(
            poll_interval = poll_interval,
            dispatch_fn   = _stub_dispatch,
            auto_complete = True,
        )
        mode_label = "[bold yellow][DRY-RUN][/bold yellow] "
    else:
        from autosys.agent.dispatch import AgentDispatch
        agent = AgentDispatch(local_only=False)
        processor = EventProcessor(
            poll_interval = poll_interval,
            auto_complete = auto_complete,
            dispatch_fn   = agent.dispatch,
            kill_fn       = agent.kill,
        )
        mode_label = ""

    _console.print(
        f"\n[bold green]Event Processor starting[/bold green] {mode_label}"
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


# ---------------------------------------------------------------------------
# scheduler serve  (EPS + REST API together)
# ---------------------------------------------------------------------------

@scheduler_group.command("serve")
@click.option("--port", "-p", default=9000, show_default=True,
              help="Port for the REST API server.")
@click.option("--host", "-H", default="0.0.0.0", show_default=True,
              help="Host to bind the REST API server to.")
@click.option("--poll-interval", default=1.0, show_default=True,
              help="EPS tick interval in seconds.")
@click.option("--dry-run", is_flag=True, default=False,
              help=(
                  "Use the stub dispatcher: jobs transition STARTING → RUNNING → SUCCESS "
                  "instantly without executing any scripts or contacting remote agents. "
                  "Ideal for local development and migration complexity analysis."
              ))
def scheduler_serve(port: int, host: str, poll_interval: float, dry_run: bool) -> None:
    """
    Run the Event Processor and REST API server together.

    This starts uvicorn with the FastAPI app.  The EPS runs as a background
    asyncio task inside the same event loop as the API server.  All REST
    endpoints and the WebSocket live feed are available immediately.

    Pass --dry-run to exercise the full state machine without needing any
    System Agents.  Jobs complete instantly via the stub dispatcher, making
    it easy to walk through every BOX in the WCC dashboard during migration
    analysis.

    Example
    -------
    \\b
        $ autosys scheduler serve --port 9000 --dry-run
        AutoSys App Server starting on http://0.0.0.0:9000  [DRY-RUN]
        ^C  Stopped.
    """
    import uvicorn
    from autosys.app_server.main import create_app

    dry_label = "  [bold yellow][DRY-RUN — stub dispatcher][/bold yellow]" if dry_run else ""
    _console.print(
        f"\n[bold green]AutoSys App Server[/bold green] starting on "
        f"[cyan]http://{host}:{port}[/cyan]{dry_label}\n"
        f"  API docs:  [bold]http://localhost:{port}/docs[/bold]\n"
        f"  Live feed: [bold]ws://localhost:{port}/api/v1/ws/events[/bold]\n"
        f"  EPS poll:  {poll_interval:.1f}s\n"
        f"[dim]Ctrl+C to stop[/dim]\n"
    )

    app = create_app(start_eps=True, eps_poll_interval=poll_interval, dry_run=dry_run)

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        _console.print("\n[yellow]Stopped.[/yellow]\n")


# ---------------------------------------------------------------------------
# scheduler wcc
# ---------------------------------------------------------------------------

@scheduler_group.command("wcc")
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host.")
@click.option("--port", default=8080, show_default=True, help="WCC HTTP port.")
def scheduler_wcc(host: str, port: int) -> None:
    """
    Start the WCC (Workload Control Centre) web dashboard on port 8080.

    The WCC reads directly from the same SQLite database as the scheduler.
    It provides a live job grid, D3 dependency graphs, alarm console, and
    run history viewer.

    Example
    -------
    \\b
        $ autosys scheduler wcc --port 8080
        AutoSys WCC Dashboard starting on http://0.0.0.0:8080
        Open: http://localhost:8080
        ^C  Stopped.
    """
    import uvicorn
    from autosys.wcc.app import create_wcc_app

    _console.print(
        f"\n[bold cyan]AutoSys WCC Dashboard[/bold cyan] starting on "
        f"[cyan]http://{host}:{port}[/cyan]\n"
        f"  Job grid:   [bold]http://localhost:{port}/[/bold]\n"
        f"  Alarms:     [bold]http://localhost:{port}/alarms[/bold]\n"
        f"[dim]Ctrl+C to stop[/dim]\n"
    )

    app = create_wcc_app()

    try:
        uvicorn.run(app, host=host, port=port, log_level="warning")
    except KeyboardInterrupt:
        _console.print("\n[yellow]Stopped.[/yellow]\n")
