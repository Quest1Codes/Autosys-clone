"""
autosys monbro — run a monitor or report (browser) and print its result.

Mirrors the real AutoSys ``monbro`` command: it runs a pre-defined
monitor/report by name (defined via ``insert_monbro`` in JIL — see
``jil_cmd.py``) and prints the outcome to standard output.

Monitor vs. report
-------------------
A ``monbro`` definition is either:
  - A **monitor** (``monbro_type`` one of FILE_MONITOR, CPU_MONITOR,
    DISK_MONITOR, PROCESS_MONITOR, LOG_MONITOR, TEXT_MONITOR) — evaluated
    once via ``MonitorEvaluator``, same handlers the EPS tick loop uses.
  - A **report** (``monbro_type`` JOB_REPORT or ALARM_REPORT) — runs
    ``generate_job_report``/``generate_alarm_report`` over a date window.
    The window comes from the definition's attributes: ``hours`` (an int,
    default 24) or explicit ``date_from``/``date_to`` (ISO timestamps).

Usage
-----
    autosys monbro -N mon1            Run monitor/report "mon1" once.
    autosys monbro -N ALL             Run every defined monbro.
    autosys monbro -N "mon%"          Run all monbros matching a % pattern.
    autosys monbro -N mon1 -q         Print mon1's JIL definition instead
                                       of running it.
    autosys monbro -N mon1 -P 30      Poll every 30s until Ctrl-C
                                       (monitors only; -P is ignored for
                                       reports, which have no polling
                                       concept in real AutoSys either).
"""

from __future__ import annotations

import fnmatch
import sys
import time

import click
from rich.console import Console

from autosys.db.connection import sync_session
from autosys.db.repository import monitors as monitor_repo
from autosys.db.schema import MonitorRow

_console = Console()
_err     = Console(stderr=True)

_REPORT_TYPES = frozenset({"JOB_REPORT", "ALARM_REPORT"})


def _like_to_fnmatch(pattern: str) -> str:
    return pattern.replace("%", "*").replace("_", "?")


def _matching_monitors(session, name_pattern: str) -> list[MonitorRow]:
    if name_pattern.upper() == "ALL":
        return monitor_repo.list_all(session)
    fn = _like_to_fnmatch(name_pattern)
    return [m for m in monitor_repo.list_all(session) if fnmatch.fnmatch(m.monbro_name, fn)]


@click.command(name="monbro")
@click.option("-N", "--name", "name_pattern", required=True,
              help="Monitor/report name, % pattern, or ALL.")
@click.option("-P", "--poll", "poll_frequency", type=int, default=None,
              help="Poll interval in seconds (monitors only). "
                   "Without this, runs once and exits.")
@click.option("-q", "--query", "query", is_flag=True, default=False,
              help="Print the monbro definition in JIL format instead of running it.")
def monbro(name_pattern: str, poll_frequency: int | None, query: bool) -> None:
    """
    Run a monitor or report (browser) by name, or print its JIL definition.

    Example
    -------
    \\b
        $ autosys monbro -N file_watch_1
        $ autosys monbro -N ALL -q
        $ autosys monbro -N cpu_check -P 10
    """
    with sync_session() as session:
        mons = _matching_monitors(session, name_pattern)

        if not mons:
            _err.print(f"[yellow]No monbro definitions matched:[/yellow] {name_pattern!r}")
            sys.exit(0)

        if query:
            for mon in mons:
                _console.print(_definition_to_jil(mon))
            return

        if poll_frequency:
            _console.print(
                f"[dim]Polling every {poll_frequency}s — Ctrl-C to stop.[/dim]\n"
            )
            try:
                while True:
                    for mon in mons:
                        _run_one(session, mon)
                    session.commit()
                    time.sleep(poll_frequency)
            except KeyboardInterrupt:
                _console.print("\n[dim]Stopped.[/dim]")
                return

        for mon in mons:
            _run_one(session, mon)
        session.commit()


def _run_one(session, mon: MonitorRow) -> None:
    if mon.monbro_type in _REPORT_TYPES:
        _run_report(session, mon)
    else:
        _run_monitor(session, mon)


def _run_monitor(session, mon: MonitorRow) -> None:
    from autosys.engine.monitor_evaluator import MonitorEvaluator

    evaluator = MonitorEvaluator()
    result = evaluator.evaluate_one(session, mon)

    _console.print(f"[bold]{mon.monbro_name}[/bold]  ({mon.monbro_type})")
    if result is None:
        _console.print("  [dim]No condition met — nothing to report.[/dim]")
        return

    if result.get("alarm"):
        evaluator.raise_alarm(session, mon, result["alarm"])
        _console.print(f"  [red]ALARM:[/red] {result['alarm']}")
    if result.get("event") and mon.job_name:
        evaluator.enqueue_event(session, mon, result["event"])
        _console.print(f"  [green]EVENT:[/green] {result['event']} → {mon.job_name}")


def _run_report(session, mon: MonitorRow) -> None:
    import json
    from datetime import datetime, timedelta
    from autosys.timeutil import utcnow
    from autosys.engine.monitor_evaluator import generate_job_report, generate_alarm_report

    attrs = {}
    if mon.attributes_json:
        try:
            attrs = json.loads(mon.attributes_json)
        except json.JSONDecodeError:
            _err.print(f"[red]Error:[/red] monbro {mon.monbro_name!r} has invalid JSON attributes")
            return

    if "date_from" in attrs and "date_to" in attrs:
        date_from = datetime.fromisoformat(attrs["date_from"])
        date_to   = datetime.fromisoformat(attrs["date_to"])
    else:
        hours = attrs.get("hours", 24)
        date_to   = utcnow()
        date_from = date_to - timedelta(hours=hours)

    if mon.monbro_type == "JOB_REPORT":
        report = generate_job_report(session, date_from, date_to)
    else:
        report = generate_alarm_report(session, date_from, date_to)

    _console.print(f"[bold]{mon.monbro_name}[/bold]  ({mon.monbro_type})")
    for key, value in report.items():
        _console.print(f"  {key}: {value}")


def _definition_to_jil(mon: MonitorRow) -> str:
    """Render a MonitorRow back to JIL, matching real ``monbro -N <name> -q``."""
    import json
    lines = [f"insert_monbro: {mon.monbro_name}", f"monbro_type: {mon.monbro_type}"]
    if mon.job_name:
        lines.append(f"job_name: {mon.job_name}")
    if mon.attributes_json:
        try:
            attrs = json.loads(mon.attributes_json)
            for key, value in attrs.items():
                lines.append(f"{key}: {value}")
        except json.JSONDecodeError:
            pass
    return "\n".join(lines) + "\n"
