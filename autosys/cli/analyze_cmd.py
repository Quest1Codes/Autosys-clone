"""
autosys analyze — Phase 1 migration assessment: complexity scoring & effort estimation.

Reads job definitions from the DB and applies the T-shirt sizing model from
the migration strategy document to produce a structured assessment report.

Complexity sizes (from docs/strategy-and-approach.md)
------------------------------------------------------
  XS  Simple command job, no dependencies.                      1-2 h per DAG
  S   Box with linear deps OR cmd with one simple condition.    2-4 h per DAG
  M   Calendar, time triggers, date_conditions, file-watchers.  4-8 h per DAG
  L   look_back, virtual resources, complex conditions.         1-3 days per DAG
  XL  FTP jobs, cross-instance deps, deep BOX trees.            1+ week per DAG

Usage
-----
    autosys analyze                         # Full report, all jobs
    autosys analyze --box %risk%            # Filter by BOX name pattern
    autosys analyze --export report.csv     # Export per-job CSV
    autosys analyze --summary               # Summary table + effort estimate only
    autosys analyze --export out.csv --summary  # Both
"""

from __future__ import annotations

import csv
import sys
from datetime import datetime
from typing import Optional

import click
from rich.console import Console
from rich.table import Table
from rich import box as rich_box

from autosys.analysis.complexity import (
    SIZES as _SIZES,
    SIZE_DESCRIPTIONS as _SIZE_DESCRIPTIONS,
    EFFORT_HOURS as _EFFORT_HOURS,
    build_report,
    compute_summary,
)
from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo

_console = Console()
_err     = Console(stderr=True)

_SIZE_COLOURS = {
    "XS": "green",
    "S":  "cyan",
    "M":  "yellow",
    "L":  "red",
    "XL": "bold red",
}


# ---------------------------------------------------------------------------
# Report builder — thin wrapper returning plain dicts for this module's own
# Rich table/CSV rendering.  Scoring itself lives in autosys.analysis.complexity
# so the CLI and the REST API can never drift apart.
# ---------------------------------------------------------------------------

def _build_report(rows, box_pattern: Optional[str]) -> list[dict]:
    return [
        {
            "job_name": r.job_name,
            "job_type": r.job_type,
            "box_name": r.box_name,
            "size":     r.size,
            "effort_h": r.effort_h,
            "drivers":  r.drivers,
        }
        for r in build_report(rows, box_pattern)
    ]


def _print_detail_table(results: list[dict]) -> None:
    """Print the per-job complexity table to the console."""
    table = Table(
        box=rich_box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold",
        padding=(0, 1),
    )
    table.add_column("Job Name",    min_width=32, no_wrap=True)
    table.add_column("Type",        min_width=12, no_wrap=True)
    table.add_column("Size",        min_width=4,  no_wrap=True, justify="center")
    table.add_column("Est. Hours",  min_width=9,  no_wrap=True, justify="right")
    table.add_column("Key Drivers", min_width=40)

    for rec in results:
        colour = _SIZE_COLOURS[rec["size"]]
        indent = "  " if rec["box_name"] else ""
        table.add_row(
            indent + rec["job_name"],
            f"[dim]{rec['job_type']}[/dim]",
            f"[{colour}]{rec['size']}[/{colour}]",
            str(rec["effort_h"]),
            f"[dim]{rec['drivers'][:80]}[/dim]",
        )

    _console.print()
    _console.print(table)


def _print_summary(results: list[dict]) -> None:
    """Print the T-shirt size breakdown + effort estimate."""
    from autosys.analysis.complexity import JobAssessment

    summary = compute_summary([
        JobAssessment(
            job_name=r["job_name"], job_type=r["job_type"], box_name=r["box_name"],
            size=r["size"], effort_h=r["effort_h"], drivers=r["drivers"],
        )
        for r in results
    ])

    # Complexity breakdown table
    table = Table(
        box=rich_box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold",
        padding=(0, 1),
        title="Complexity Breakdown",
    )
    table.add_column("Size", min_width=4)
    table.add_column("Description",       min_width=38)
    table.add_column("Jobs", min_width=5, justify="right")
    table.add_column("h/job", min_width=5, justify="right")
    table.add_column("Total h", min_width=7, justify="right")

    for size in _SIZES:
        if summary.counts[size] == 0:
            continue
        colour = _SIZE_COLOURS[size]
        table.add_row(
            f"[{colour}]{size}[/{colour}]",
            f"[dim]{_SIZE_DESCRIPTIONS[size]}[/dim]",
            str(summary.counts[size]),
            str(_EFFORT_HOURS[size]),
            str(summary.hours[size]),
        )
    table.add_section()
    table.add_row("", "[bold]Total[/bold]", f"[bold]{summary.total_jobs}[/bold]", "",
                  f"[bold]{summary.raw_hours}[/bold]")

    _console.print()
    _console.print(table)

    # Effort breakdown
    _console.print()
    _console.print("[bold]Effort Estimate  (strategy-and-approach.md overhead model)[/bold]")
    _console.print()
    _console.print(f"  Raw job effort      {summary.raw_hours:>6} h")
    _console.print(f"  + Platform setup  (20%)  {summary.platform_h:>5} h")
    _console.print(f"  + Testing         (30%)  {summary.testing_h:>5} h")
    _console.print(f"  + Project Mgmt    (15%)  {summary.pm_h:>5} h")
    _console.print(f"  + Training / docs (10%)  {summary.training_h:>5} h")
    _console.print(f"  {'─' * 36}")
    _console.print(f"  [bold green]TOTAL ESTIMATED EFFORT  {summary.total_h:>5} h  ({summary.total_days} person-days)[/bold green]")
    _console.print()


def _export_csv(results: list[dict], path: str) -> None:
    """Write results to a CSV file."""
    fields = ["job_name", "job_type", "box_name", "size", "effort_h", "drivers"]
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)
    _console.print(f"\n[green]Exported {len(results)} rows → {path}[/green]\n")


# ---------------------------------------------------------------------------
# CLI command
# ---------------------------------------------------------------------------

@click.command(name="analyze")
@click.option("--box", "box_pattern", default=None, metavar="PATTERN",
              help=(
                  "Restrict analysis to BOX jobs matching this SQL LIKE pattern "
                  "(e.g. '%risk%') and their children.  Default: all jobs."
              ))
@click.option("--export", "csv_path", default=None, metavar="FILE",
              help="Write per-job results to a CSV file.")
@click.option("--summary", "summary_only", is_flag=True, default=False,
              help="Print only the summary table and effort estimate (no per-job rows).")
def analyze(
    box_pattern:  Optional[str],
    csv_path:     Optional[str],
    summary_only: bool,
) -> None:
    """
    Phase 1 migration assessment: T-shirt size every job and estimate effort.

    Reads job definitions from the DB (loaded via 'autosys jil import') and
    applies the complexity model from docs/strategy-and-approach.md.

    Output includes:
      - Per-job complexity score (XS / S / M / L / XL) with key drivers
      - Summary breakdown with job counts per tier
      - Total effort estimate including platform, testing, PM, and training
        overheads (20% / 30% / 15% / 10%)

    Example
    -------
    \\b
        # Load the financial JIL inventory first
        $ autosys jil import jil_files/00_global_variables.jil
        $ for f in jil_files/*.jil; do autosys jil import "$f"; done

        # Run the full assessment
        $ autosys analyze --export assessment.csv
        $ autosys analyze --summary
        $ autosys analyze --box '%aml%'
    """
    with sync_session() as session:
        rows = job_repo.list_all(session)

    if not rows:
        _err.print(
            "[yellow]No jobs found in the DB.[/yellow]  "
            "Run [bold]autosys jil import <file.jil>[/bold] first."
        )
        sys.exit(0)

    results = _build_report(rows, box_pattern)

    _console.print(
        f"\n[bold]AutoSys → Astronomer  Migration Complexity Assessment[/bold]"
        f"\n[dim]Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  "
        f"Jobs analysed: {len(results)}[/dim]"
    )

    if not summary_only:
        _print_detail_table(results)

    _print_summary(results)

    if csv_path:
        _export_csv(results, csv_path)
