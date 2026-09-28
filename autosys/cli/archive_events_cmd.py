"""
autosys archive_events — purge old processed events from event history.

Mirrors the real AutoSys ``archive_events`` utility, which trims the
event-history audit table so it doesn't grow unbounded.

Usage
-----
    autosys archive_events --older-than-days 30
    autosys archive_events --older-than-days 7 --dry-run
"""

from __future__ import annotations

from datetime import timedelta

import click
from rich.console import Console

from autosys.db.connection import sync_session
from autosys.db.repository import events as event_repo
from autosys.db.schema import EventHistoryRow
from autosys.timeutil import utcnow
from sqlalchemy import func, select

_console = Console()


@click.command(name="archive_events")
@click.option(
    "--older-than-days", "older_than_days", type=int, default=30, show_default=True,
    help="Delete processed events older than this many days.",
)
@click.option(
    "--dry-run", "dry_run", is_flag=True, default=False,
    help="Report how many rows would be deleted, without deleting them.",
)
def archive_events(older_than_days: int, dry_run: bool) -> None:
    """Purge old processed events from the event history audit table."""
    cutoff = utcnow() - timedelta(days=older_than_days)

    with sync_session() as session:
        if dry_run:
            count = session.scalar(
                select(func.count()).select_from(EventHistoryRow)
                .where(EventHistoryRow.created_at < cutoff)
            ) or 0
        else:
            count = event_repo.archive_older_than(session, cutoff)

    if dry_run:
        _console.print(
            f"[yellow]Dry run:[/yellow] {count} event{'s' if count != 1 else ''} "
            f"older than {older_than_days} day{'s' if older_than_days != 1 else ''} "
            f"would be deleted."
        )
    else:
        _console.print(
            f"[green]Archived:[/green] {count} event{'s' if count != 1 else ''} "
            f"older than {older_than_days} day{'s' if older_than_days != 1 else ''} deleted."
        )
