"""
autosys agent — System Agent commands and job output inspection.

Commands
--------
autosys agent start          Run the EPS with real subprocess dispatch.
autosys agent status         Show the current agent state (active jobs).

autosys jobs tail <job>      Print captured stdout/stderr of the last run.
autosys jobs history <job>   Show run history with exit codes and durations.

The ``agent start`` command is the Phase 5 replacement for
``scheduler start``.  It uses the real ``AgentDispatch`` dispatcher so jobs
are launched as actual OS subprocesses.

Machine filtering
-----------------
Phase 5 only dispatches jobs whose ``machine`` attribute resolves to the
local hostname (or is empty/localhost).  Jobs targeting other machines are
left in STARTING state.  Phase 6 adds SSH-based remote dispatch.

Example
-------
\\b
    # Terminal 1 — import jobs and queue a STARTJOB event
    $ autosys jil import examples/demo_etl.jil
    $ autosys sendevent -E STARTJOB -J check_source_ready

    # Terminal 2 — run the agent (processes the queued event)
    $ autosys agent start
    [agent] Running 'check_source_ready'  cmd='...'
    ^C

    # View output
    $ autosys jobs tail check_source_ready
    [1]  Source check passed.

    # View history
    $ autosys jobs history check_source_ready
    Run ID    Start                End                  Duration  Status  Exit
    abc123…   2026-06-25 06:00:00  2026-06-25 06:00:02  2.1 s     SUCCESS   0
"""

from __future__ import annotations

import asyncio
import sys
from datetime import datetime
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich import box as rich_box

from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo, runs as run_repo, output as output_repo
from autosys.agent.dispatch import AgentDispatch
from autosys.agent.server import AgentServer
from autosys.scheduler.event_processor import EventProcessor

_console = Console()
_err     = Console(stderr=True)


# ===========================================================================
# autosys agent <subcommands>
# ===========================================================================

@click.group(name="agent")
def agent_group() -> None:
    """System Agent commands (real subprocess dispatch)."""


# ---------------------------------------------------------------------------
# agent start
# ---------------------------------------------------------------------------

@agent_group.command("start")
@click.option("--poll-interval", "-p", default=1.0, show_default=True,
              help="Seconds between event queue polls.")
@click.option("--machine", "-m", default=None,
              help="Override the local machine name filter. "
                   "Jobs must set machine: <this value> to be dispatched.")
def agent_start(poll_interval: float, machine: Optional[str]) -> None:
    """
    Run the Event Processor with real subprocess dispatch.

    Jobs whose machine attribute resolves to the current host will be
    executed as OS subprocesses.  stdout/stderr is captured and stored in
    the ``job_output`` table.

    Unlike ``scheduler start`` (which uses a stub dispatcher), this command
    runs jobs for real.  Use it on the machine that will execute the jobs.

    Example
    -------
    \\b
        $ autosys agent start
        Agent started on localhost (poll: 1.0s)  Ctrl+C to stop
    """
    import socket
    local_name = machine or socket.gethostname()

    agent     = AgentDispatch(local_only=True)
    processor = EventProcessor(
        dispatch_fn   = agent.dispatch,
        kill_fn       = agent.kill,
        poll_interval = poll_interval,
        auto_complete = False,   # real dispatch — don't auto-flip to SUCCESS
    )

    _console.print(
        f"\n[bold green]Agent started[/bold green] on "
        f"[cyan]{local_name}[/cyan] "
        f"(poll: {poll_interval:.1f}s)  "
        f"[dim]Ctrl+C to stop[/dim]\n"
    )

    try:
        asyncio.run(processor.run_forever())
    except KeyboardInterrupt:
        _console.print("\n[yellow]Agent stopped.[/yellow]\n")
        sys.exit(0)


# ---------------------------------------------------------------------------
# agent run-once  (one-shot: process all pending + exit)
# ---------------------------------------------------------------------------

@agent_group.command("serve")
@click.option("--machine", "-m", required=True,
              help="Logical machine name (must match job machine: attributes).")
@click.option("--host", "-H", default="0.0.0.0", show_default=True,
              help="IP address to bind.")
@click.option("--port", "-p", default=7520, show_default=True,
              help="TCP port to listen on.")
def agent_serve(machine: str, host: str, port: int) -> None:
    """
    Start the System Agent TCP server (Phase 6 remote dispatch).

    Listens on HOST:PORT for dispatch requests from the Scheduler ACE.
    Registers this machine in the shared DB so the Scheduler can route
    jobs targeting this machine name to this server.

    Run this on each worker machine that will execute jobs.  The Scheduler
    and the agent must share the same AUTOSYS_DB_URL (e.g. PostgreSQL).

    Example
    -------
    \\b
        # On etl-server-01:
        $ AUTOSYS_DB_URL=postgresql://... autosys agent serve \\
            --machine etl-server-01 --host 0.0.0.0 --port 7520

        # On the scheduler host:
        $ autosys machine check etl-server-01
        UP  etl-server-01  0.0.0.0:7520  active_jobs=[]  uptime=1.2s
    """
    server = AgentServer(machine_name=machine, host=host, port=port)

    _console.print(
        f"\n[bold green]Agent serving[/bold green]  "
        f"machine=[cyan]{machine}[/cyan]  "
        f"addr=[dim]{host}:{port}[/dim]  "
        f"[dim]Ctrl+C to stop[/dim]\n"
    )

    try:
        asyncio.run(server.serve_forever())
    except KeyboardInterrupt:
        _console.print("\n[yellow]Agent stopped.[/yellow]\n")
        sys.exit(0)


@agent_group.command("run-once")
@click.option("--quiet", "-q", is_flag=True, default=False)
@click.option("--wait", "-w", default=30, show_default=True,
              help="Seconds to wait for running jobs to complete.")
def agent_run_once(quiet: bool, wait: int) -> None:
    """
    Process all pending events once with real subprocess dispatch, then exit.

    Waits up to WAIT seconds for all dispatched jobs to finish.
    Useful for integration tests and one-shot batch runs.

    Example
    -------
    \\b
        $ autosys agent run-once
        Processed 1 event(s).
        Waiting for jobs to complete (max 30s)...
        check_source_ready: SUCCESS (exit 0, 1.2s)
    """
    import time

    agent     = AgentDispatch(local_only=True)
    processor = EventProcessor(
        dispatch_fn   = agent.dispatch,
        kill_fn       = agent.kill,
        auto_complete = False,
    )

    with sync_session() as session:
        before = {r.job_name: r.status for r in job_repo.list_all(session)}
    with sync_session() as session:
        n = processor.process_one_tick(session)

    if not quiet:
        _console.print(f"\n[bold]{n} event(s) processed.[/bold]")

    if n == 0:
        if not quiet:
            _console.print("[dim]No events pending.[/dim]\n")
        return

    # Wait for dispatched jobs to complete
    if not quiet:
        _console.print(f"Waiting for jobs to complete (max {wait}s)…\n")

    deadline = time.time() + wait
    while time.time() < deadline:
        with sync_session() as session:
            after = {r.job_name: r.status for r in job_repo.list_all(session)}

        still_running = [
            name for name, st in after.items()
            if st in ("RUNNING", "STARTING")
        ]
        if not still_running:
            break
        time.sleep(0.2)

    # Print result
    with sync_session() as session:
        after = {r.job_name: r.status for r in job_repo.list_all(session)}
        changed = [
            (name, before.get(name, "N/A"), after.get(name, "N/A"))
            for name in sorted(set(before) | set(after))
            if before.get(name) != after.get(name)
        ]

        for name, old_s, new_s in changed:
            if not quiet:
                run_id = run_repo.latest_run_id(session, name)
                dur = ""
                if run_id:
                    rows = run_repo.list_runs(session, name, limit=1)
                    if rows and rows[0].end_time and rows[0].start_time:
                        secs = (rows[0].end_time - rows[0].start_time).total_seconds()
                        dur  = f"  [{secs:.1f}s]"
                _console.print(
                    f"  [cyan]{name:<30}[/cyan]  "
                    f"[dim]{old_s}[/dim] → "
                    f"[{'green' if new_s == 'SUCCESS' else 'red' if new_s == 'FAILURE' else 'yellow'}]{new_s}[/]"
                    f"{dur}"
                )
    if not quiet:
        _console.print()


# ===========================================================================
# autosys jobs <subcommands>
# ===========================================================================

@click.group(name="jobs")
def jobs_group() -> None:
    """Job output and run history commands."""


# ---------------------------------------------------------------------------
# jobs tail
# ---------------------------------------------------------------------------

@jobs_group.command("tail")
@click.argument("job_name")
@click.option("--run-id", "-r", default=None,
              help="Specific run_id to tail (defaults to most recent run).")
@click.option("--lines", "-n", default=None, type=int,
              help="Show only the last N lines.")
def jobs_tail(job_name: str, run_id: Optional[str], lines: Optional[int]) -> None:
    """
    Print captured stdout/stderr output from the most recent run of JOB_NAME.

    By default shows all output from the most recent run.  Use --run-id
    to view a specific historical run, or --lines to limit output.

    Example
    -------
    \\b
        $ autosys jobs tail check_source_ready
        Job: check_source_ready  run_id: abc123de...
        [1]  Starting source check…
        [2]  /data/sales.csv  OK  (1.24 GB)
        [3]  Check complete.
    """
    with sync_session() as session:
        if run_id is None:
            run_id = run_repo.latest_run_id(session, job_name)

        if run_id is None:
            _err.print(
                f"[red]No run history found for job {job_name!r}.[/red]"
            )
            sys.exit(1)

        output_lines = output_repo.get_lines(session, run_id)

    if not output_lines:
        _console.print(
            f"[dim]No output captured for job [cyan]{job_name}[/cyan]  "
            f"run_id: [dim]{run_id[:8]}…[/dim][/dim]"
        )
        return

    if lines is not None:
        output_lines = output_lines[-lines:]

    _console.print(
        f"\nJob: [cyan]{job_name}[/cyan]  "
        f"run_id: [dim]{run_id[:8]}…[/dim]\n"
    )
    for row in output_lines:
        _console.print(f"[dim]{row.line_no:>4}[/dim]  {row.content}")
    _console.print()


# ---------------------------------------------------------------------------
# jobs history
# ---------------------------------------------------------------------------

@jobs_group.command("history")
@click.argument("job_name")
@click.option("--limit", "-n", default=10, show_default=True,
              help="Maximum number of runs to show.")
def jobs_history(job_name: str, limit: int) -> None:
    """
    Show run history for JOB_NAME (newest first).

    Displays run_id, start/end times, duration, final status, and exit code.

    Example
    -------
    \\b
        $ autosys jobs history check_source_ready
        Run ID    Started              Ended                Dur    Status   Exit
        abc123…   2026-06-25 06:00:00  2026-06-25 06:00:02  2.1s   SUCCESS     0
        def456…   2026-06-24 06:00:00  2026-06-24 06:00:01  1.4s   SUCCESS     0
    """
    with sync_session() as session:
        job_row = job_repo.get_row(session, job_name)
        if job_row is None:
            _err.print(f"[red]Job {job_name!r} not found.[/red]")
            sys.exit(1)

        run_rows = run_repo.list_runs(session, job_name, limit=limit)

    if not run_rows:
        _console.print(f"[dim]No run history for job [cyan]{job_name}[/cyan].[/dim]")
        return

    table = Table(
        box         = rich_box.SIMPLE_HEAD,
        show_header = True,
        header_style = "bold",
        padding     = (0, 1),
    )
    table.add_column("Run ID",  min_width=10)
    table.add_column("Started",  min_width=19)
    table.add_column("Ended",    min_width=19)
    table.add_column("Dur",      justify="right", min_width=6)
    table.add_column("Status",   min_width=10)
    table.add_column("Exit",     justify="right")

    status_colour = {
        "SUCCESS":    "green",
        "FAILURE":    "red",
        "TERMINATED": "red",
        "RUNNING":    "blue",
        "STARTING":   "yellow",
    }

    from autosys.scheduler.state_machine import _norm_status
    for r in run_rows:
        dur = "—"
        if r.start_time and r.end_time:
            secs = (r.end_time - r.start_time).total_seconds()
            dur = f"{secs:.1f}s"

        sname = _norm_status(r.status)
        col = status_colour.get(sname, "white")
        table.add_row(
            r.run_id[:8] + "…",
            r.start_time.strftime("%Y-%m-%d %H:%M:%S") if r.start_time else "—",
            r.end_time.strftime("%Y-%m-%d %H:%M:%S")   if r.end_time   else "—",
            dur,
            f"[{col}]{sname}[/{col}]",
            str(r.exit_code) if r.exit_code is not None else "—",
        )

    _console.print(f"\nRun history for [cyan]{job_name}[/cyan]:\n")
    _console.print(table)
