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
from autosys.analysis.operational_risk import RISK_LEVELS as _RISK_LEVELS, fetch_run_stats
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

_RISK_COLOURS = {
    "NO_DATA": "dim",
    "NONE":    "green",
    "LOW":     "cyan",
    "MEDIUM":  "yellow",
    "HIGH":    "bold red",
}
_RISK_ABBREV = {
    "NO_DATA": "--",
    "NONE":    "NO",
    "LOW":     "LO",
    "MEDIUM":  "ME",
    "HIGH":    "HI",
}


# ---------------------------------------------------------------------------
# Report builder — thin wrapper returning plain dicts for this module's own
# Rich table/CSV rendering.  Scoring itself lives in autosys.analysis.complexity
# so the CLI and the REST API can never drift apart.
# ---------------------------------------------------------------------------

def _build_report(rows, box_pattern: Optional[str], run_stats=None) -> list[dict]:
    return [
        {
            "job_name": r.job_name,
            "job_type": r.job_type,
            "box_name": r.box_name,
            "size":     r.size,
            "effort_h": r.effort_h,
            "drivers":  r.drivers,
            "risk":         r.risk,
            "risk_drivers": r.risk_drivers,
            "blast_radius": r.blast_radius,
            "gap_tags":     r.gap_tags,
        }
        for r in build_report(rows, box_pattern, run_stats=run_stats)
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
    table.add_column("Risk",        min_width=4,  no_wrap=True, justify="center")
    table.add_column("Key Drivers", min_width=40)

    for rec in results:
        colour      = _SIZE_COLOURS[rec["size"]]
        risk_colour = _RISK_COLOURS[rec["risk"]]
        indent = "  " if rec["box_name"] else ""
        table.add_row(
            indent + rec["job_name"],
            f"[dim]{rec['job_type']}[/dim]",
            f"[{colour}]{rec['size']}[/{colour}]",
            str(rec["effort_h"]),
            f"[{risk_colour}]{_RISK_ABBREV[rec['risk']]}[/{risk_colour}]",
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
            risk=r["risk"], risk_drivers=r["risk_drivers"],
            blast_radius=r["blast_radius"], gap_tags=r["gap_tags"],
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

    # Risk breakdown (operational — FSM current state + run/alarm history)
    risk_table = Table(
        box=rich_box.SIMPLE_HEAD,
        show_header=True,
        header_style="bold",
        padding=(0, 1),
        title="Risk Breakdown  (operational — separate from complexity/effort)",
    )
    risk_table.add_column("Risk", min_width=8)
    risk_table.add_column("Jobs", min_width=5, justify="right")
    for level in _RISK_LEVELS:
        if summary.risk_counts.get(level, 0) == 0:
            continue
        colour = _RISK_COLOURS[level]
        risk_table.add_row(f"[{colour}]{level}[/{colour}]", str(summary.risk_counts[level]))

    _console.print()
    _console.print(risk_table)

    # Gap severity (AutoSys -> Astronomer heatmap, docs/strategy-and-approach.md)
    gsc = summary.gap_severity_counts
    _console.print()
    _console.print(
        f"[bold]Gap Severity[/bold]  "
        f"[bold red]RED={gsc.get('RED', 0)}[/bold red]  "
        f"[yellow]YELLOW={gsc.get('YELLOW', 0)}[/yellow]  "
        f"[green]GREEN={gsc.get('GREEN', 0)}[/green]"
    )

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
    fields = [
        "job_name", "job_type", "box_name", "size", "effort_h", "drivers",
        "risk", "risk_drivers", "blast_radius", "gap_tags",
    ]
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
        run_stats = fetch_run_stats(session, [r.job_name for r in rows])

    if not rows:
        _err.print(
            "[yellow]No jobs found in the DB.[/yellow]  "
            "Run [bold]autosys jil import <file.jil>[/bold] first."
        )
        sys.exit(0)

    results = _build_report(rows, box_pattern, run_stats)

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


# ---------------------------------------------------------------------------
# Migration report command — JIL-only complexity with simulation
# ---------------------------------------------------------------------------

@click.command(name="migration-report")
@click.option("--cycles", default=20, show_default=True, type=int,
              help="Number of simulation cycles for runtime data generation.")
@click.option("--export", "csv_path", default=None, metavar="FILE",
              help="Write per-job results to a CSV file.")
@click.option("--summary", "summary_only", is_flag=True, default=False,
              help="Print only the summary table (no per-job rows).")
def migration_report(
    cycles:      int,
    csv_path:    Optional[str],
    summary_only: bool,
) -> None:
    """
    JIL-only migration complexity report with simulated runtime data.

    Runs a multi-cycle dry-run simulation to generate JobRunRow/AlarmRow
    history, then combines structural JIL signals (A1-A10) with the
    complexity scoring model to produce a comprehensive assessment.

    Example
    -------
    \\b
        $ autosys jil import jil_files/*.jil
        $ autosys migration-report --cycles 20 --export migration.csv
    """
    from autosys.analysis.migration_signals import run_all_structural_analyses
    from autosys.scheduler.simulation_runner import run_simulation

    with sync_session() as session:
        rows = job_repo.list_all(session)
        if not rows:
            _err.print(
                "[yellow]No jobs found in the DB.[/yellow]  "
                "Run [bold]autosys jil import <file.jil>[/bold] first."
            )
            sys.exit(0)

        # Run simulation to generate runtime data
        _console.print(f"\n[bold cyan]Running {cycles}-cycle dry-run simulation...[/bold cyan]")
        sim_result = run_simulation(session, cycles=cycles, ticks_per_cycle=10, seed=42)
        _console.print(
            f"  [dim]Runs: {sim_result['total_runs']}  "
            f"Failures: {sim_result['total_failures']}  "
            f"Alarms: {sim_result['total_alarms']}  "
            f"Failure rate: {sim_result['failure_rate']:.1%}[/dim]"
        )

        # Fetch run stats from simulated history
        run_stats = fetch_run_stats(session, [r.job_name for r in rows])

        # Run structural analyses
        signals = run_all_structural_analyses(session)

    # Build enriched report
    assessments = build_report(rows, run_stats=run_stats, migration_signals=signals)
    results = [
        {
            "job_name": a.job_name,
            "job_type": a.job_type,
            "box_name": a.box_name,
            "size":     a.size,
            "effort_h": a.effort_h,
            "drivers":  a.drivers,
            "risk":         a.risk,
            "risk_drivers": a.risk_drivers,
            "blast_radius": a.blast_radius,
            "gap_tags":     a.gap_tags,
            "machine_concentration": a.machine_concentration,
            "command_dialect":       a.command_dialect,
            "box_nesting_depth":     a.box_nesting_depth,
            "has_cross_box_dep":     a.has_cross_box_dep,
            "schedule_burst_count":  a.schedule_burst_count,
            "has_notifications":     a.has_notifications,
            "has_hardcoded_logs":    a.has_hardcoded_logs,
            "timezone":              a.timezone,
        }
        for a in assessments
    ]

    _console.print(
        f"\n[bold]AutoSys → Astronomer  JIL-Only Migration Complexity Report[/bold]"
        f"\n[dim]Generated: {datetime.now().strftime('%Y-%m-%d %H:%M')}  |  "
        f"Jobs analysed: {len(results)}  |  "
        f"Simulation cycles: {cycles}[/dim]"
    )

    if not summary_only:
        _print_detail_table(results)

    _print_summary(results)

    # Migration signals summary
    from autosys.analysis.complexity import JobAssessment
    summary = compute_summary([
        JobAssessment(
            job_name=a.job_name, job_type=a.job_type, box_name=a.box_name,
            size=a.size, effort_h=a.effort_h, drivers=a.drivers,
            risk=a.risk, risk_drivers=a.risk_drivers,
            blast_radius=a.blast_radius, gap_tags=a.gap_tags,
            machine_concentration=a.machine_concentration,
            command_dialect=a.command_dialect,
            box_nesting_depth=a.box_nesting_depth,
            has_cross_box_dep=a.has_cross_box_dep,
            schedule_burst_count=a.schedule_burst_count,
            has_notifications=a.has_notifications,
            has_hardcoded_logs=a.has_hardcoded_logs,
            timezone=a.timezone,
        )
        for a in assessments
    ])

    _console.print()
    _console.print("[bold]Migration Signals (JIL structural analysis)[/bold]")
    _console.print(f"  Cross-box dependencies: {summary.cross_box_dep_count}")
    _console.print(f"  Max box nesting depth:  {summary.max_box_nesting}")
    _console.print(f"  Max schedule burst:     {summary.max_schedule_burst} jobs at one time")
    _console.print(f"  Jobs with notifications: {summary.notification_job_count}")
    _console.print(f"  Jobs with hardcoded log paths: {summary.hardcoded_log_job_count}")
    _console.print(f"  Jobs with timezone:    {summary.timezone_count}")
    if summary.dialect_counts:
        _console.print(f"  Script dialects:        {dict(summary.dialect_counts)}")
    _console.print()

    if csv_path:
        from autosys.analysis.complexity import export_csv as _export_csv_new
        with open(csv_path, "w", newline="") as f:
            f.write(_export_csv_new(assessments))
        _console.print(f"  [green]CSV exported to {csv_path}[/green]")

    # Per-box effort breakdown
    from autosys.analysis.complexity import box_effort_breakdown, astronomer_mapping, risk_mitigation
    _console.print()
    _console.print("[bold]Per-Box Effort Breakdown[/bold]")
    box_table = Table(box=rich_box.SIMPLE, show_header=True, header_style="bold")
    box_table.add_column("Box", style="cyan")
    box_table.add_column("Jobs", justify="right")
    box_table.add_column("Effort", justify="right")
    box_table.add_column("Sizes", style="dim")
    box_table.add_column("HIGH Risk", style="red")
    for b in box_effort_breakdown(assessments):
        box_table.add_row(
            b["box_name"],
            str(b["job_count"]),
            f"{b['total_effort_h']}h",
            ", ".join(f"{k}={v}" for k, v in b["sizes"].items() if v),
            ", ".join(b["high_risk_jobs"]) or "-",
        )
    _console.print(box_table)

    # Top 5 Astronomer mapping recommendations
    _console.print()
    _console.print("[bold]Astronomer Mapping Recommendations (top 5 XL/L jobs)[/bold]")
    for a in sorted(assessments, key=lambda x: x.effort_h, reverse=True)[:5]:
        _console.print(f"  [bold]{a.job_name}[/bold] [{a.size}]")
        _console.print(f"    -> {astronomer_mapping(a)}")
        _console.print(f"    !  {risk_mitigation(a)}")
    _console.print()
