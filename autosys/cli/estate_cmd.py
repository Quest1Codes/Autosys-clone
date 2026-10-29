"""
autosys estate — operations on the whole imported job estate, not one job.

Commands
--------
autosys estate clear   Delete every job definition (and everything that
                        depends on one: run history, alarms, output).
"""

from __future__ import annotations

import sys

import click
from rich.console import Console

from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo
from autosys.parser.jil_ingest import remove_jobs_not_in

_console = Console()
_err     = Console(stderr=True)


@click.group(name="estate")
def estate_group() -> None:
    """Operations on the whole job estate."""


@estate_group.command("clear")
@click.option("--yes", is_flag=True, default=False,
              help="Required. Without it, this only reports what would be removed.")
def estate_clear(yes: bool) -> None:
    """
    Delete every job definition currently imported, and everything that
    depends on one (run history, alarms, captured output).

    Recoverable: each job's last definition is archived as JIL to a
    reconcile:<timestamp> file before removal (the same mechanism
    `jil import --replace-estate` already uses) -- re-import that archive
    to restore the estate. See routers/estate.py's module docstring for
    the full safety model this mirrors.

    Repopulating from the ORIGINAL JIL source afterward (rather than that
    archive) needs `jil import-dir --replace-estate`, not a plain
    `jil import-dir`: a plain reimport sees every job as already defined
    (from the original import, which this command never erases from the
    audit trail) and skips reloading them -- silently, with no error. See
    `jil import-dir --help`.

    Without --yes, reports the current job count and does nothing.

    Example
    -------
    \\b
        $ autosys estate clear
        297 job(s) would be removed. Pass --yes to actually remove them.

        $ autosys estate clear --yes
        Removed 297 job(s).
    """
    with sync_session() as session:
        count = len(job_repo.list_all(session))
        if count == 0:
            _console.print("Estate is already empty.")
            return
        if not yes:
            _console.print(
                f"[yellow]{count} job(s) would be removed.[/yellow] Pass --yes to actually remove them."
            )
            return

        removed = remove_jobs_not_in(session, present=set())
        session.commit()

    failed = count - len(removed)
    _console.print(f"[green]Removed {len(removed)} job(s).[/green]")
    if failed:
        _err.print(f"[red]{failed} job(s) could not be removed[/red] -- see the server log.")
        sys.exit(1)
