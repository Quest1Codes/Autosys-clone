"""
autosys dbstatistics — report row counts per database table.

Mirrors the real AutoSys ``dbstatistics`` command, which prints basic
statistics (row counts) for every table in the AutoSys database so an
operator can eyeball growth over time (event history, run history, etc.).

Usage
-----
    autosys dbstatistics

We iterate ``Base.metadata.tables`` generically — rather than hardcoding a
table list — so the report stays correct as tables are added to the schema.
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table
from rich import box as rich_box
from sqlalchemy import func, select

from autosys.db.connection import sync_session
from autosys.db.schema import Base

_console = Console()


@click.command(name="dbstatistics")
def dbstatistics() -> None:
    """Display row counts for every table in the AutoSys database."""
    counts: dict[str, int] = {}

    with sync_session() as session:
        for table_name, table in Base.metadata.tables.items():
            count = session.scalar(select(func.count()).select_from(table)) or 0
            counts[table_name] = count

    table = Table(box=rich_box.SIMPLE_HEAD, show_header=True, header_style="bold", padding=(0, 1))
    table.add_column("Table", min_width=24, no_wrap=True)
    table.add_column("Rows", min_width=8, no_wrap=True, justify="right")

    for table_name in sorted(counts):
        table.add_row(table_name, str(counts[table_name]))

    _console.print()
    _console.print(table)
    _console.print(f"[dim]{len(counts)} table{'s' if len(counts) != 1 else ''} reported.[/dim]\n")
