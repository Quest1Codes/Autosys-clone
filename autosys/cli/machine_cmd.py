"""
autosys machine — System Agent registry management.

Commands
--------
autosys machine register <name>   Register a remote agent in the DB.
autosys machine list               Show all registered agents and status.
autosys machine check <name>       Ping an agent, update its health status.
autosys machine unregister <name>  Remove a machine from the registry.

Background
----------
Before the Scheduler can dispatch a job to a remote machine, that machine
must be registered.  Registration stores the agent's ``host:port`` in the
``machines`` table so the Scheduler can look it up at dispatch time.

In real AutoSys, machines are defined via JIL using the ``insert_machine:``
syntax:

    insert_machine: etl-server-01
        type: a
        port: 7520

We expose the same operation as a CLI command.  The JIL ``insert_machine``
syntax is parsed in Phase 7 (when we extend the JIL parser for machine
definitions).

Typical workflow
----------------
\\b
    # On the scheduler host — register the agent
    $ autosys machine register etl-server-01 \\
        --host 192.168.1.10 --port 7520

    # On etl-server-01 — start the agent
    $ AUTOSYS_DB_URL=... autosys agent serve \\
        --machine etl-server-01 --port 7520

    # On the scheduler host — verify it's alive
    $ autosys machine check etl-server-01
    etl-server-01  UP   192.168.1.10:7520   active_jobs=0   uptime=12.3s

    # Import a JIL with machine: etl-server-01
    $ autosys jil import my_jobs.jil

    # Start the job — it now dispatches remotely!
    $ autosys sendevent -E STARTJOB -J my_cmd_job
    $ autosys agent run-once --wait 60
"""

from __future__ import annotations

import sys
from datetime import datetime
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich import box as rich_box

from autosys.db.connection import sync_session
from autosys.db.repository import machines as machine_repo

_console = Console()
_err     = Console(stderr=True)


@click.group(name="machine")
def machine_group() -> None:
    """System Agent registry commands."""


# ---------------------------------------------------------------------------
# machine register
# ---------------------------------------------------------------------------

@machine_group.command("register")
@click.argument("machine_name")
@click.option("--host", "-H", required=True,
              help="Hostname or IP of the agent server.")
@click.option("--port", "-p", default=7520, show_default=True,
              help="TCP port the agent listens on.")
@click.option("--description", "-d", default=None,
              help="Optional human-readable description.")
def machine_register(
    machine_name: str,
    host: str,
    port: int,
    description: Optional[str],
) -> None:
    """
    Register or update a System Agent in the machines table.

    If MACHINE_NAME already exists, updates host, port, and description.

    Example
    -------
    \\b
        $ autosys machine register etl-server-01 --host 10.0.1.10 --port 7520
        Registered: etl-server-01  →  10.0.1.10:7520
    """
    with sync_session() as session:
        row = machine_repo.register(
            session,
            machine_name = machine_name,
            host         = host,
            port         = port,
            description  = description,
        )

    _console.print(
        f"Registered: [cyan]{machine_name}[/cyan]  →  "
        f"[dim]{host}:{port}[/dim]"
    )


# ---------------------------------------------------------------------------
# machine list
# ---------------------------------------------------------------------------

@machine_group.command("list")
def machine_list() -> None:
    """
    List all registered System Agents with their health status.

    Example
    -------
    \\b
        $ autosys machine list
        Machine          Status    Host               Port   Last Heartbeat
        etl-server-01    UP        10.0.1.10          7520   2026-06-29 06:01
        etl-server-02    UNKNOWN   10.0.1.11          7520   —
    """
    with sync_session() as session:
        rows = machine_repo.list_all(session)

    if not rows:
        _console.print("[dim]No machines registered. Use 'autosys machine register'.[/dim]")
        return

    table = Table(
        box         = rich_box.SIMPLE_HEAD,
        show_header = True,
        header_style = "bold",
        padding     = (0, 1),
    )
    table.add_column("Machine",        min_width=20)
    table.add_column("Status",         min_width=8)
    table.add_column("Host",           min_width=16)
    table.add_column("Port",           justify="right")
    table.add_column("Last Heartbeat", min_width=18)

    status_colour = {"UP": "green", "DOWN": "red", "UNKNOWN": "dim"}

    for r in rows:
        col    = status_colour.get(r.status, "white")
        hb     = r.last_heartbeat.strftime("%Y-%m-%d %H:%M") if r.last_heartbeat else "—"
        table.add_row(
            r.machine_name,
            f"[{col}]{r.status}[/{col}]",
            r.host,
            str(r.port),
            hb,
        )

    _console.print()
    _console.print(table)


# ---------------------------------------------------------------------------
# machine check
# ---------------------------------------------------------------------------

@machine_group.command("check")
@click.argument("machine_name")
@click.option("--timeout", "-t", default=5.0, show_default=True,
              help="Heartbeat timeout in seconds.")
def machine_check(machine_name: str, timeout: float) -> None:
    """
    Send a heartbeat ping to MACHINE_NAME and display its status.

    Updates the machine's ``status`` and ``last_heartbeat`` in the DB.
    Exits with code 1 if the machine is DOWN or not registered.

    Example
    -------
    \\b
        $ autosys machine check etl-server-01
        etl-server-01  UP   10.0.1.10:7520   active_jobs=[]   uptime=42.3s
    """
    from autosys.agent.remote import RemoteDispatch

    with sync_session() as session:
        machine_row = machine_repo.get(session, machine_name)

    if machine_row is None:
        _err.print(
            f"[red]Machine {machine_name!r} is not registered.[/red]\n"
            f"Register it with: autosys machine register {machine_name} --host <host>"
        )
        sys.exit(1)

    rd    = RemoteDispatch(timeout=timeout)
    alive = rd.heartbeat(machine_row)

    with sync_session() as session:
        machine_repo.update_heartbeat(
            session, machine_name, status="UP" if alive else "DOWN"
        )

    if alive:
        # Also fetch status details
        status_resp = rd.status(machine_row)
        active = status_resp.get("active_jobs", [])
        uptime = status_resp.get("uptime_secs", 0)
        _console.print(
            f"[green]UP[/green]  [cyan]{machine_name}[/cyan]  "
            f"{machine_row.host}:{machine_row.port}  "
            f"active_jobs={active}  uptime={uptime:.1f}s"
        )
    else:
        _console.print(
            f"[red]DOWN[/red]  [cyan]{machine_name}[/cyan]  "
            f"{machine_row.host}:{machine_row.port}"
        )
        sys.exit(1)


# ---------------------------------------------------------------------------
# machine unregister
# ---------------------------------------------------------------------------

@machine_group.command("unregister")
@click.argument("machine_name")
@click.confirmation_option(
    prompt="Remove this machine from the registry? Jobs targeting it will not be dispatched."
)
def machine_unregister(machine_name: str) -> None:
    """
    Remove MACHINE_NAME from the registry.

    Jobs whose ``machine:`` attribute matches this name will no longer be
    dispatched (they will stay in STARTING state).
    """
    with sync_session() as session:
        row = machine_repo.get(session, machine_name)
        if row is None:
            _err.print(f"[red]Machine {machine_name!r} not found.[/red]")
            sys.exit(1)
        session.delete(row)

    _console.print(f"Unregistered: [cyan]{machine_name}[/cyan]")
