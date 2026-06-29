"""
autosys autorep — display job status report.

Mirrors the real AutoSys ``autorep -J`` command.  Reads the current job
status snapshot from the ``jobs`` table and formats it as a table.

Usage
-----
    autosys autorep -J job_name       Single job (exact name).
    autosys autorep -J %              All jobs  (% = SQL wildcard).
    autosys autorep -J etl%           Jobs whose name starts with 'etl'.
    autosys autorep -J % -q           Quiet: machine-parseable TSV format.

Output
------
Real AutoSys output looks like::

    Job Name                      Last Start         Last End           ST  Run
    ----------------------------  -----------------  -----------------  --  ---
    demo_etl_box                  ----------         ----------         IN    0
      check_source_ready          ----------         ----------         IN    0
      extract_sales               ----------         ----------         IN    0

Where:
  Last Start / Last End  — timestamp of the last run, or ``----------``
  ST                     — two-letter status code (see _STATUS_ABBREV)
  Run                    — number of completed runs

We reproduce this format faithfully using rich.Table with a monospace style.
"""

from __future__ import annotations

import sys
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich import box as rich_box

from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo
from autosys.db.schema import JobRow

_console = Console()
_err     = Console(stderr=True)

# Two-letter status abbreviations (matches real AutoSys autorep output)
_STATUS_ABBREV: dict[str, str] = {
    "INACTIVE":    "IN",
    "ACTIVATED":   "AC",
    "STARTING":    "ST",
    "RUNNING":     "RU",
    "SUCCESS":     "SU",
    "FAILURE":     "FA",
    "TERMINATED":  "TE",
    "RESTART":     "RE",
    "QUE_WAIT":    "QW",
    "ON_HOLD":     "OH",
    "ON_ICE":      "OI",
}

_TS_FMT = "%m/%d/%Y %H:%M:%S"
_BLANK  = "----------"


def _fmt_ts(dt) -> str:
    """Format a datetime or return the blank placeholder."""
    if dt is None:
        return _BLANK
    try:
        return dt.strftime(_TS_FMT)
    except Exception:
        return _BLANK


def _abbrev(status: str) -> str:
    return _STATUS_ABBREV.get(status.upper(), status[:2].upper())


def _status_colour(status: str) -> str:
    """Return a rich colour tag for the status abbreviation."""
    return {
        "INACTIVE":   "dim",
        "ACTIVATED":  "cyan",
        "STARTING":   "yellow",
        "RUNNING":    "blue",
        "SUCCESS":    "green",
        "FAILURE":    "red",
        "TERMINATED": "red",
        "RESTART":    "yellow",
        "QUE_WAIT":   "yellow",
        "ON_HOLD":    "magenta",
        "ON_ICE":     "magenta",
    }.get(status.upper(), "white")


# ---------------------------------------------------------------------------
# autorep command
# ---------------------------------------------------------------------------

@click.command(name="autorep")
@click.option("-J", "--job",  "job_pattern", required=True,
              help="Job name or SQL LIKE pattern (use % for all jobs).")
@click.option("-q", "--quiet", is_flag=True, default=False,
              help="Machine-parseable TSV output (no colour, no header).")
@click.option("--no-children", is_flag=True, default=False,
              help="Do not indent child jobs under their BOX parents.")
def autorep(job_pattern: str, quiet: bool, no_children: bool) -> None:
    """
    Display a job status report.

    JOB_PATTERN is an exact job name or a SQL LIKE pattern
    (``%`` for all jobs, ``etl%`` for jobs starting with 'etl').

    Example
    -------
    \\b
        $ autosys autorep -J %
        $ autosys autorep -J demo_etl_box
        $ autosys autorep -J etl% --no-children
    """
    with sync_session() as session:
        if job_pattern == "%" or "%" in job_pattern or "_" in job_pattern:
            rows = job_repo.list_by_pattern(session, job_pattern)
        else:
            row = job_repo.get_row(session, job_pattern)
            rows = [row] if row else []

    if not rows:
        _err.print(
            f"[yellow]No jobs matched pattern:[/yellow] {job_pattern!r}"
        )
        sys.exit(0)

    if quiet:
        _print_tsv(rows)
        return

    _print_table(rows, no_children=no_children)


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _print_table(rows: list[JobRow], *, no_children: bool) -> None:
    """Render jobs as a rich-formatted table mimicking real autorep output."""

    # Build an index for quick BOX-member lookup
    box_children: dict[str, list[JobRow]] = {}
    top_level: list[JobRow] = []

    for row in rows:
        if row.box_name:
            box_children.setdefault(row.box_name, []).append(row)
        else:
            top_level.append(row)

    table = Table(
        box=rich_box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold",
        padding=(0, 1),
    )
    table.add_column("Job Name",   style="",        min_width=28, no_wrap=True)
    table.add_column("Last Start", style="dim",     min_width=19, no_wrap=True)
    table.add_column("Last End",   style="dim",     min_width=19, no_wrap=True)
    table.add_column("ST",         style="",        min_width=2,  no_wrap=True)
    table.add_column("Run",        style="",        min_width=3,  no_wrap=True, justify="right")

    def _add_row(r: JobRow, indent: int = 0) -> None:
        status = r.status or "INACTIVE"
        abbrev = _abbrev(status)
        colour = _status_colour(status)
        prefix = "  " * indent
        table.add_row(
            prefix + r.job_name,
            _fmt_ts(r.last_start),
            _fmt_ts(r.last_end),
            f"[{colour}]{abbrev}[/{colour}]",
            str(_run_count(r)),
        )

    if no_children:
        for row in rows:
            _add_row(row, indent=0)
    else:
        # Render BOX jobs with their children indented beneath them
        rendered: set[str] = set()
        for row in rows:
            if row.job_name in rendered:
                continue
            _add_row(row, indent=0)
            rendered.add(row.job_name)
            # Render children of this BOX (if any)
            for child in box_children.get(row.job_name, []):
                if child.job_name not in rendered:
                    _add_row(child, indent=1)
                    rendered.add(child.job_name)
        # Any remaining rows not rendered yet (e.g. whose parent wasn't in the query)
        for row in rows:
            if row.job_name not in rendered:
                _add_row(row, indent=0)
                rendered.add(row.job_name)

    _console.print()
    _console.print(table)
    _console.print(f"[dim]{len(rows)} job{'s' if len(rows) != 1 else ''} found.[/dim]\n")


def _print_tsv(rows: list[JobRow]) -> None:
    """Print tab-separated values for scripting (no rich markup)."""
    for r in rows:
        status = r.status or "INACTIVE"
        print(
            r.job_name,
            _fmt_ts(r.last_start),
            _fmt_ts(r.last_end),
            _abbrev(status),
            str(_run_count(r)),
            sep="\t",
        )


def _run_count(row: JobRow) -> int:
    """Return the number of completed runs for a job row."""
    # In Phase 3 we don't query job_runs here to keep it fast.
    # Phase 4 will add a run_count denormalised column or a join.
    return 0
