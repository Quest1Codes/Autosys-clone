"""
autosys autocal — calendar management CLI.

Manages named calendars used for job scheduling (run_calendar / exclude_calendar).

Usage
-----
    autosys autocal list
    autosys autocal create my_cal --description "My calendar"
    autosys autocal add my_cal --date 2025-01-01
    autosys autocal add my_cal --range 2025-01-01 2025-01-31
    autosys autocal add my_cal --day-of-week mo,tu,we,th,fr
    autosys autocal remove my_cal --date 2025-01-01
    autosys autocal show my_cal
    autosys autocal delete my_cal
    autosys autocal import /path/to/calendar.cal
    autosys autocal export my_cal --output /path/to/export.cal
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta

import click
from rich.console import Console
from rich.table import Table
from sqlalchemy import select

from autosys.db.connection import sync_session
from autosys.db.schema import CalendarRow
from autosys.db.repository import CalendarRepository

_console = Console()
_err = Console(stderr=True)
_cal_repo = CalendarRepository()

_DAY_OF_WEEK_MAP: dict[str, int] = {
    "mo": 0, "mon": 0, "monday": 0,
    "tu": 1, "tue": 1, "tuesday": 1,
    "we": 2, "wed": 2, "wednesday": 2,
    "th": 3, "thu": 3, "thursday": 3,
    "fr": 4, "fri": 4, "friday": 4,
    "sa": 5, "sat": 5, "saturday": 5,
    "su": 6, "sun": 6, "sunday": 6,
}


@click.group(name="autocal")
def autocal_group():
    """Manage calendars."""
    pass


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@autocal_group.command(name="list")
def list_calendars():
    """List all calendars."""
    with sync_session() as session:
        cals = session.scalars(select(CalendarRow)).all()
        if not cals:
            _console.print("No calendars found.")
            return

        table = Table(title="Calendars", show_header=True, header_style="bold")
        table.add_column("Name", style="", min_width=20)
        table.add_column("Dates", justify="right", min_width=6)
        table.add_column("Description", style="dim")

        for cal in cals:
            dates = json.loads(cal.dates_json) if cal.dates_json else []
            table.add_row(
                cal.calendar_name,
                str(len(dates)),
                cal.description or "",
            )
        _console.print(table)


# ---------------------------------------------------------------------------
# create
# ---------------------------------------------------------------------------

@autocal_group.command(name="create")
@click.argument("name")
@click.option("--description", "-d", default=None, help="Calendar description.")
def create_calendar(name: str, description: str | None):
    """Create a new empty calendar."""
    with sync_session() as session:
        existing = _cal_repo.get(session, name)
        if existing:
            _err.print(f"[red]Error:[/red] Calendar {name!r} already exists.")
            sys.exit(1)
        row = CalendarRow(
            calendar_name=name,
            dates_json="[]",
            description=description,
        )
        session.add(row)
        session.commit()
        _console.print(f"[green]Created[/green] calendar {name!r}.")


# ---------------------------------------------------------------------------
# add
# ---------------------------------------------------------------------------

@autocal_group.command(name="add")
@click.argument("name")
@click.option("--date", "date_str", default=None, help="Single date (YYYY-MM-DD).")
@click.option("--range", "date_range", nargs=2, default=None, help="Date range (START END, inclusive).")
@click.option("--day-of-week", "dow_str", default=None, help="Comma-separated days (mo,tu,we,th,fr,sa,su).")
def add_dates(name: str, date_str: str | None, date_range: tuple[str, str] | None, dow_str: str | None):
    """Add dates to a calendar."""
    if not date_str and not date_range and not dow_str:
        _err.print("[red]Error:[/red] Must specify --date, --range, or --day-of-week.")
        sys.exit(1)

    dates_to_add: list[str] = []

    if date_str:
        dates_to_add.append(date_str)

    if date_range:
        start = date.fromisoformat(date_range[0])
        end = date.fromisoformat(date_range[1])
        current = start
        while current <= end:
            dates_to_add.append(current.isoformat())
            current += timedelta(days=1)

    if dow_str:
        target_dows = set()
        for token in dow_str.split(","):
            token = token.strip().lower()
            if token not in _DAY_OF_WEEK_MAP:
                _err.print(f"[red]Error:[/red] Invalid day-of-week {token!r}. Use mo,tu,we,th,fr,sa,su.")
                sys.exit(1)
            target_dows.add(_DAY_OF_WEEK_MAP[token])
        year = date.today().year
        current = date(year, 1, 1)
        end = date(year, 12, 31)
        while current <= end:
            if current.weekday() in target_dows:
                dates_to_add.append(current.isoformat())
            current += timedelta(days=1)

    with sync_session() as session:
        row = _cal_repo.get(session, name)
        if row is None:
            _err.print(f"[red]Error:[/red] Calendar {name!r} not found.")
            sys.exit(1)
        added = _cal_repo.add_dates(session, name, dates_to_add)
        session.commit()
        _console.print(f"[green]Added[/green] {added} date(s) to calendar {name!r}.")


# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------

@autocal_group.command(name="remove")
@click.argument("name")
@click.option("--date", "date_str", required=True, help="Date to remove (YYYY-MM-DD).")
def remove_date(name: str, date_str: str):
    """Remove a date from a calendar."""
    with sync_session() as session:
        row = _cal_repo.get(session, name)
        if row is None:
            _err.print(f"[red]Error:[/red] Calendar {name!r} not found.")
            sys.exit(1)
        removed = _cal_repo.remove_date(session, name, date_str)
        session.commit()
        if removed:
            _console.print(f"[green]Removed[/green] {date_str} from calendar {name!r}.")
        else:
            _console.print(f"[yellow]Date {date_str} not found in calendar {name!r}.")


# ---------------------------------------------------------------------------
# show
# ---------------------------------------------------------------------------

@autocal_group.command(name="show")
@click.argument("name")
def show_calendar(name: str):
    """Show all dates in a calendar."""
    with sync_session() as session:
        row = _cal_repo.get(session, name)
        if row is None:
            _err.print(f"[red]Error:[/red] Calendar {name!r} not found.")
            sys.exit(1)
        dates = json.loads(row.dates_json) if row.dates_json else []
        _console.print(f"[bold]{name}[/bold]  ({len(dates)} date{'s' if len(dates) != 1 else ''})")
        if row.description:
            _console.print(f"[dim]{row.description}[/dim]")
        _console.print()
        if not dates:
            _console.print("[dim]No dates defined.[/dim]")
        else:
            for d in dates:
                _console.print(f"  {d}")


# ---------------------------------------------------------------------------
# delete
# ---------------------------------------------------------------------------

@autocal_group.command(name="delete")
@click.argument("name")
@click.confirmation_option(prompt="Are you sure you want to delete this calendar?")
def delete_calendar(name: str):
    """Delete a calendar entirely."""
    with sync_session() as session:
        deleted = _cal_repo.delete(session, name)
        if not deleted:
            _err.print(f"[red]Error:[/red] Calendar {name!r} not found.")
            sys.exit(1)
        session.commit()
        _console.print(f"[green]Deleted[/green] calendar {name!r}.")


# ---------------------------------------------------------------------------
# import
# ---------------------------------------------------------------------------

@autocal_group.command(name="import")
@click.argument("file_path", type=click.Path(exists=True))
@click.option("--name", "-n", default=None, help="Calendar name (defaults to filename stem).")
def import_calendar(file_path: str, name: str | None):
    """Import calendar dates from a .cal file."""
    from pathlib import Path
    from autosys.models.calendar import Calendar

    p = Path(file_path)
    cal_name = name or p.stem
    cal = Calendar.load_from_file(file_path)
    cal.calendar_name = cal_name

    dates_str = [d.isoformat() for d in cal.dates]

    with sync_session() as session:
        existing = _cal_repo.get(session, cal_name)
        if existing:
            _cal_repo.add_dates(session, cal_name, dates_str)
            if cal.description and not existing.description:
                existing.description = cal.description
        else:
            row = CalendarRow(
                calendar_name=cal_name,
                dates_json=json.dumps(dates_str),
                description=cal.description,
            )
            session.add(row)
        session.commit()
        _console.print(
            f"[green]Imported[/green] {len(dates_str)} date(s) into calendar {cal_name!r}."
        )


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------

@autocal_group.command(name="export")
@click.argument("name")
@click.option("--output", "-o", required=True, type=click.Path(), help="Output file path.")
def export_calendar(name: str, output: str):
    """Export a calendar to a .cal file."""
    with sync_session() as session:
        row = _cal_repo.get(session, name)
        if row is None:
            _err.print(f"[red]Error:[/red] Calendar {name!r} not found.")
            sys.exit(1)
        dates = json.loads(row.dates_json) if row.dates_json else []
        lines = [
            f"# Calendar: {name}",
            f"# Exported from AutoSys clone",
            f"# Total dates: {len(dates)}",
            "",
        ]
        if row.description:
            lines.append(f"# {row.description}")
            lines.append("")
        for d in dates:
            lines.append(d)
        with open(output, "w") as f:
            f.write("\n".join(lines) + "\n")
        _console.print(f"[green]Exported[/green] {len(dates)} date(s) to {output!r}.")
