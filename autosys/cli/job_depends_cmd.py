"""
autosys job_depends — show a job's dependency chain.

Mirrors the real AutoSys ``job_depends`` utility, which reports both
directions of a job's dependency graph:

  - "Depends on"     — jobs referenced in the job's own ``condition`` attribute.
  - "Depended on by" — other jobs whose ``condition`` references this job.

Usage
-----
    autosys job_depends -J job_name
"""

from __future__ import annotations

import sys

import click
from rich.console import Console

from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo
from autosys.scheduler.condition_evaluator import referenced_job_names

_console = Console()
_err     = Console(stderr=True)


@click.command(name="job_depends")
@click.option("-J", "--job", "job_name", required=True, help="Job name to report on.")
def job_depends(job_name: str) -> None:
    """Display a job's dependency chain (upstream and downstream)."""
    with sync_session() as session:
        row = job_repo.get_row(session, job_name)
        if row is None:
            _err.print(f"[yellow]No job found:[/yellow] {job_name!r}")
            sys.exit(0)

        depends_on = sorted(referenced_job_names(row.condition))

        depended_on_by: list[str] = []
        for other in job_repo.list_all(session):
            if other.job_name == job_name:
                continue
            if job_name in referenced_job_names(other.condition):
                depended_on_by.append(other.job_name)
        depended_on_by.sort()

    _console.print(f"\n[bold]{job_name}[/bold]")

    _console.print("\n[bold]Depends on:[/bold]")
    if depends_on:
        for name in depends_on:
            _console.print(f"  {name}")
    else:
        _console.print("  (none)")

    _console.print("\n[bold]Depended on by:[/bold]")
    if depended_on_by:
        for name in depended_on_by:
            _console.print(f"  {name}")
    else:
        _console.print("  (none)")
    _console.print()
