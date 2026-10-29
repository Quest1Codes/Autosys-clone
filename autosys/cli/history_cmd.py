"""
autosys history — load a client's real AutoSys run history.

Commands
--------
autosys history import FILE    Load run history and persist it (source="imported").
autosys history coverage       Show how much of the estate has observed vs
                                simulated operational risk right now.

See autosys/parser/run_history_ingest.py for the file contract and
dev/task9-cost-calculator/05-cost-estimation-questions.md §3a for the
client-facing version of the same contract.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich import box as rich_box

from autosys.db.connection import sync_session
from autosys.parser.run_history_ingest import ingest_run_history_file

_console = Console()
_err     = Console(stderr=True)


@click.group(name="history")
def history_group() -> None:
    """Load and inspect imported run history."""


# ===========================================================================
# history import
# ===========================================================================

@history_group.command("import")
@click.argument("file", type=click.Path(exists=True, readable=True, path_type=Path))
@click.option("--timezone", "declared_timezone", default=None,
              help="IANA timezone name (e.g. America/New_York) or UTC. Required only "
                   "if the file's timestamps carry no UTC offset of their own; a file "
                   "that needs one and doesn't get one is rejected, not guessed at.")
@click.option("--on-conflict", type=click.Choice(["skip", "replace"]), default="skip",
              show_default=True,
              help="skip: re-importing the same run is a no-op (idempotent re-import). "
                   "replace: a later file overwrites a matching earlier row -- use "
                   "deliberately for a corrected re-export.")
def history_import(file: Path, declared_timezone: str | None, on_conflict: str) -> None:
    """
    Load FILE as real AutoSys run history.

    Accepts CSV, TSV or pipe-delimited text (header row required, delimiter
    auto-detected) or JSON Lines (one object per line) -- whichever your
    export tooling already produces. Required columns: job_name, status,
    start_time. See the module docstring of
    autosys/parser/run_history_ingest.py for the full column contract.

    Rows for a job not in the current JIL are counted and reported, never
    silently dropped or imported against a job that doesn't exist.

    Example
    -------
    \\b
        $ autosys history import fidelity_runs_2026q3.csv --timezone America/New_York
        Importing fidelity_runs_2026q3.csv ...

          format: csv  encoding: utf-8
          207,412 rows read, 206,988 loaded, 318 skipped, 106 duplicate
          1,842 of 1,901 jobs covered
          window: 2026-07-01 .. 2026-09-30

        status: OK  (import_id=4)
    """
    _console.print(f"Importing [bold]{file.name}[/bold] ...\n")
    with file.open("rb") as fh:
        report = ingest_run_history_file(
            sync_session, fh, str(file),
            declared_timezone=declared_timezone, on_conflict=on_conflict,
        )

    if report.aborted:
        _err.print(f"[red]Aborted:[/red] {report.abort_reason}")
        _err.print(f"{report.n_rows_loaded} row(s) were durably committed before the "
                   f"connection was lost (import_id={report.import_id}, status left "
                   "IN_PROGRESS). Re-running this command is safe -- already-loaded "
                   "rows will not be duplicated.")
        sys.exit(1)

    status_colour = {"OK": "green", "WARN": "yellow", "REJECTED": "red"}[report.status]
    _console.print(f"  format: {report.format}  encoding: {report.encoding}")
    if report.status == "REJECTED":
        _err.print(f"\n[red]Rejected:[/red] {report.reject_reason}")
        sys.exit(1)

    _console.print(
        f"  {report.n_rows_read:,} rows read, {report.n_rows_loaded:,} loaded, "
        f"{report.n_rows_skipped:,} skipped, {report.n_rows_duplicate:,} duplicate"
    )
    if report.n_rows_unknown_job:
        _console.print(
            f"  {report.n_rows_unknown_job:,} row(s) for a job not in the current JIL"
        )
        for name in report.unknown_job_samples[:10]:
            _console.print(f"    [dim]- {name}[/dim]")
        if len(report.unknown_job_samples) > 10:
            _console.print(f"    [dim]... and {len(report.unknown_job_samples) - 10} more[/dim]")
    if report.window_start and report.window_end:
        _console.print(
            f"  window: {report.window_start.date()} .. {report.window_end.date()}"
        )
    _console.print(f"  {report.n_jobs:,} job(s) covered by this file")

    if report.issue_codes:
        table = Table(box=rich_box.SIMPLE, show_header=True)
        table.add_column("issue", style="dim")
        table.add_column("count", justify="right")
        for code, count in sorted(report.issue_codes.items(), key=lambda kv: -kv[1]):
            table.add_row(code, f"{count:,}")
        _console.print(table)

    _console.print(
        f"\nstatus: [{status_colour}]{report.status}[/{status_colour}]  "
        f"(import_id={report.import_id})"
    )
    if report.status != "OK":
        sys.exit(1 if report.status == "REJECTED" else 0)


# ===========================================================================
# history coverage
# ===========================================================================

@history_group.command("coverage")
def history_coverage() -> None:
    """
    Show the estate's current operational-risk coverage: how many jobs have
    real evidence (observed), how many are below the minimum sample and
    report no rate, how many are filled in from simulation, and how many
    have nothing at all.

    Same numbers the assessment report's `coverage` block exposes over the
    API -- this is the terminal-native view of it.
    """
    from autosys.analysis.operational_risk import (
        MIN_OBSERVED_RUNS, MIN_OBSERVED_WINDOW_DAYS, fetch_run_stats,
    )
    from autosys.db.repository import jobs as job_repo

    with sync_session() as session:
        rows = job_repo.list_all(session)
        job_names = [r.job_name for r in rows]
        imported = fetch_run_stats(session, job_names, sources=("imported",))
        live     = fetch_run_stats(session, job_names, sources=("live",))
        observed = {**live, **imported}

        non_box = [r for r in rows if (r.job_type or "").upper() != "BOX"]
        sufficient = sum(1 for r in non_box if observed.get(r.job_name)
                         and observed[r.job_name].has_sufficient_sample())
        thin = sum(1 for r in non_box if observed.get(r.job_name)
                  and not observed[r.job_name].has_sufficient_sample())
        no_data = len(non_box) - sufficient - thin

    _console.print(f"[bold]{len(rows):,}[/bold] total jobs, [bold]{len(non_box):,}[/bold] non-BOX\n")
    table = Table(box=rich_box.SIMPLE)
    table.add_column("category")
    table.add_column("jobs", justify="right")
    table.add_row("observed (sufficient sample)", f"{sufficient:,}")
    table.add_row("observed (below minimum sample)", f"{thin:,}")
    table.add_row("no real evidence yet", f"{no_data:,}")
    _console.print(table)
    _console.print(
        f"\nminimum sample: {MIN_OBSERVED_RUNS} runs over {MIN_OBSERVED_WINDOW_DAYS} days "
        "(a unanimous-failure job is exempt -- see RunStats.has_sufficient_sample)"
    )
    if len(non_box):
        _console.print(f"coverage: {sufficient / len(non_box):.1%}")
