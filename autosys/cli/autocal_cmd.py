import click
from rich.console import Console
from sqlalchemy import select
from autosys.db.connection import sync_session
from autosys.db.schema import CalendarRow

_console = Console()

@click.group(name="autocal")
def autocal_group():
    """Manage calendars."""
    pass

@autocal_group.command(name="list")
def list_calendars():
    """List all calendars."""
    with sync_session() as session:
        cals = session.scalars(select(CalendarRow)).all()
        if not cals:
            _console.print("No calendars found.")
            return
        for cal in cals:
            _console.print(f"Calendar: {cal.calendar_name}")
