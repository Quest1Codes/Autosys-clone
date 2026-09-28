"""
autosys autorep — display job, machine, and global-variable reports.

Mirrors the real AutoSys ``autorep`` command.  Reads the current status
snapshot from the database and formats it as a table, an event-detail
report, or a JIL dump, depending on which flags are given.

Usage
-----
    autosys autorep -J job_name           Single job (exact name).
    autosys autorep -J %                  All jobs  (% = SQL wildcard).
    autosys autorep -J etl%               Jobs whose name starts with 'etl'.
    autosys autorep -J job_name -q        JIL definition (real -q semantics).
    autosys autorep -J job_name -q -w     JIL definition, long-form conditions.
    autosys autorep -J job_name -d        Event-detail report (most recent run).
    autosys autorep -J job_name -R -2     Detail/summary for two runs back.
    autosys autorep -J box_name -L 1      Box report, one level of children deep.
    autosys autorep -M ALL                Machine report.
    autosys autorep -G ALL                Global-variable report.
    autosys autorep -J % --tsv            Machine-parseable TSV (this
                                           simulator's own addition — NOT a
                                           real AutoSys flag; real -q means
                                           "dump the JIL", not TSV).

Output
------
Real AutoSys default (summary) output looks like::

    Job Name                      Last Start         Last End           ST  Run
    ----------------------------  -----------------  -----------------  --  ---
    demo_etl_box                  ----------         ----------         IN    0
      check_source_ready          ----------         ----------         IN    0
      extract_sales               ----------         ----------         IN    0

Where:
  Last Start / Last End  — timestamp of the last run, or ``----------``
  ST                     — two-letter status code (see state_machine.STATUS_ABBREV)
  Run                    — total number of runs recorded for the job (its runnum)

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
from autosys.db.repository import (
    jobs as job_repo, runs as run_repo, globs as glob_repo, machines as machine_repo,
)
from autosys.db.schema import JobRow, JobRunRow, EventHistoryRow
from autosys.scheduler.state_machine import STATUS_ABBREV

_console = Console()
_err     = Console(stderr=True)

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


from autosys.models.enums import JobStatus

def _get_status_name(status_int: int | str) -> str:
    if isinstance(status_int, str):
        return status_int.upper()
    try:
        return JobStatus(status_int).name
    except ValueError:
        return str(status_int)

def _abbrev(status: int | str) -> str:
    name = _get_status_name(status)
    return STATUS_ABBREV.get(name, name[:2].upper())


def _status_colour(status: int | str) -> str:
    """Return a rich colour tag for the status abbreviation."""
    name = _get_status_name(status)
    return {
        "INACTIVE":   "dim",
        "ACTIVATED":  "cyan",
        "STARTING":   "yellow",
        "RUNNING":    "blue",
        "SUCCESS":    "green",
        "FAILURE":    "red",
        "TERMINATED": "red",
        "RESTART":    "yellow",
        "WAIT_REPLY": "magenta",
        "QUE_WAIT":   "yellow",
        "PEND_MACH":  "yellow",
        "RESWAIT":    "yellow",
        "ON_NOEXEC":  "dim",
        "SUSPENDED":  "magenta",
        "ON_HOLD":    "magenta",
        "ON_ICE":     "magenta",
    }.get(name, "white")


def _like_to_fnmatch(pattern: str) -> str:
    """Translate a SQL-LIKE-style AutoSys pattern (%, _) to fnmatch (*, ?)."""
    return pattern.replace("%", "*").replace("_", "?")


# ---------------------------------------------------------------------------
# autorep command
# ---------------------------------------------------------------------------

@click.command(name="autorep")
@click.option("-J", "--job",  "job_pattern", default=None,
              help="Job name or SQL LIKE pattern (use % for all jobs).")
@click.option("-M", "--machine", "machine_pattern", default=None,
              help="Machine name or pattern (use ALL for all machines). "
                   "Generates a machine report instead of a job report.")
@click.option("-G", "--global", "global_pattern", default=None,
              help="Global variable name or pattern (use ALL for all globals). "
                   "Generates a global-variable report instead of a job report.")
@click.option("-s", "--summary", "summary", is_flag=True, default=False,
              help="Summary report (the default job-report format).")
@click.option("-d", "--detail", "detail", is_flag=True, default=False,
              help="Detail report: events from the job's most recent run "
                   "(or the run selected by -R).")
@click.option("-q", "--query", "query", is_flag=True, default=False,
              help="Query report: print the job's current JIL definition "
                   "(matches real AutoSys -q; ignored for -M/-G reports).")
@click.option("-w", "--wide", "wide", is_flag=True, default=False,
              help="With -q: render conditions in long form (success(job)) "
                   "instead of the default short form (s(job)).")
@click.option("-R", "--run", "run_num", type=int, default=None,
              help="Report on a specific run number. Positive counts from "
                   "the first run (1 = first); negative counts back from "
                   "the most recent (-1 = most recent). Valid with -s/-d.")
@click.option("-L", "--level", "level", type=int, default=None,
              help="Box hierarchy depth to report (0 = box only, no "
                   "children). Default: all levels.")
@click.option("--no-children", is_flag=True, default=False,
              help="Shorthand for -L 0: do not indent child jobs under "
                   "their BOX parents.")
@click.option("--tsv", "tsv", is_flag=True, default=False,
              help="Machine-parseable TSV output (this simulator's own "
                   "addition, not a real AutoSys flag — see -q).")
def autorep(
    job_pattern: Optional[str],
    machine_pattern: Optional[str],
    global_pattern: Optional[str],
    summary: bool,
    detail: bool,
    query: bool,
    wide: bool,
    run_num: Optional[int],
    level: Optional[int],
    no_children: bool,
    tsv: bool,
) -> None:
    """
    Display a job, machine, or global-variable status report.

    Example
    -------
    \\b
        $ autosys autorep -J %
        $ autosys autorep -J demo_etl_box -L 0
        $ autosys autorep -J extract_sales -q
        $ autosys autorep -M ALL
        $ autosys autorep -G ALL
    """
    selectors = [x for x in (job_pattern, machine_pattern, global_pattern) if x is not None]
    if not selectors:
        _err.print("[red]Error:[/red] one of -J, -M, or -G is required")
        sys.exit(1)
    if len(selectors) > 1:
        _err.print("[red]Error:[/red] -J, -M, and -G are mutually exclusive")
        sys.exit(1)

    effective_level = 0 if no_children else level

    if machine_pattern is not None:
        _run_machine_report(machine_pattern, tsv=tsv)
        return

    if global_pattern is not None:
        _run_global_report(global_pattern, tsv=tsv)
        return

    _run_job_report(
        job_pattern, detail=detail, query=query, wide=wide,
        run_num=run_num, level=effective_level, tsv=tsv,
    )


# ---------------------------------------------------------------------------
# Job report
# ---------------------------------------------------------------------------

def _run_job_report(
    job_pattern: str, *, detail: bool, query: bool, wide: bool,
    run_num: Optional[int], level: Optional[int], tsv: bool,
) -> None:
    with sync_session() as session:
        if job_pattern == "%" or job_pattern.upper() == "ALL" or "%" in job_pattern or "_" in job_pattern:
            pattern = "%" if job_pattern.upper() == "ALL" else job_pattern
            rows = job_repo.list_by_pattern(session, pattern)
        else:
            row = job_repo.get_row(session, job_pattern)
            rows = [row] if row else []

        if not rows:
            _err.print(f"[yellow]No jobs matched pattern:[/yellow] {job_pattern!r}")
            sys.exit(0)

        if query:
            _print_jil(session, rows, wide=wide)
            return

        if detail:
            _print_detail(session, rows, run_num=run_num)
            return

        # For the summary table, an exact -J box_name lookup should still
        # report the box's children (real AutoSys always does) — -L then
        # controls how many levels deep. A wildcard pattern already pulls
        # in whatever box + child rows it happens to match.
        if len(rows) == 1 and rows[0].job_type == "BOX":
            rows = rows + _fetch_box_descendants(session, rows[0].job_name, level)

        run_counts = {r.job_name: run_repo.count_runs(session, r.job_name) for r in rows}

    if tsv:
        _print_tsv(rows, run_counts)
        return

    _print_table(rows, run_counts, level=level)


def _fetch_box_descendants(session, box_name: str, level: Optional[int]) -> list[JobRow]:
    """
    Return every descendant of *box_name*, down to *level* levels deep
    (None = unlimited, matching real AutoSys -L's default; 0 = none).

    Only direct child rows are fetched per level (BFS), so nested boxes are
    expanded one layer at a time rather than assumed pre-loaded.
    """
    if level == 0:
        return []
    out: list[JobRow] = []
    frontier = [box_name]
    depth = 1
    seen: set[str] = {box_name}
    while frontier and (level is None or depth <= level):
        next_frontier: list[str] = []
        for parent in frontier:
            for child in job_repo.get_children(session, parent):
                if child.job_name in seen:
                    continue
                seen.add(child.job_name)
                out.append(child)
                if child.job_type == "BOX":
                    next_frontier.append(child.job_name)
        frontier = next_frontier
        depth += 1
    return out


def _print_jil(session, rows: list[JobRow], *, wide: bool) -> None:
    """
    -q: dump the current JIL definition for each matched job.

    Matches real AutoSys: ``autorep -J ALL -q >> autosys.jil`` round-trips
    a JIL backup.  -w renders conditions in long form instead of short form.
    """
    from autosys.parser.jil_writer import job_to_jil
    from autosys.parser.condition_parser import parse_condition, condition_to_str, ConditionSyntaxError

    for row in rows:
        job = job_repo.get(session, row.job_name)
        if job is None:
            continue
        if job.condition:
            try:
                node = parse_condition(job.condition)
                job = job.model_copy(update={
                    "condition": condition_to_str(node, form="long" if wide else "short"),
                })
            except ConditionSyntaxError:
                pass  # leave condition exactly as stored if it doesn't parse
        _console.print(job_to_jil(job), end="")


def _print_detail(session, rows: list[JobRow], *, run_num: Optional[int]) -> None:
    """
    -d: event-detail report — every event recorded for the job's most
    recent run (or the run selected by -R).
    """
    from sqlalchemy import select

    for row in rows:
        target_run: Optional[JobRunRow] = None
        if run_num is not None:
            target_run = run_repo.get_run_by_number(session, row.job_name, run_num)
        else:
            latest_id = run_repo.latest_run_id(session, row.job_name)
            if latest_id:
                target_run = session.get(JobRunRow, latest_id)

        _console.print(f"\n[bold]{row.job_name}[/bold]  (status: {_abbrev(row.status)})")

        if target_run is None:
            _console.print("[dim]  No run history — nothing to detail.[/dim]")
            continue

        from datetime import datetime as _dt
        window_start = target_run.start_time
        window_end   = target_run.end_time or _dt.now()

        events = list(session.scalars(
            select(EventHistoryRow)
            .where(EventHistoryRow.job_name == row.job_name)
            .where(EventHistoryRow.created_at >= window_start)
            .where(EventHistoryRow.created_at <= window_end)
            .order_by(EventHistoryRow.created_at)
        ))

        table = Table(box=rich_box.SIMPLE_HEAD, show_header=True, header_style="bold", padding=(0, 1))
        table.add_column("Event", no_wrap=True)
        table.add_column("Date/Time", style="dim", no_wrap=True)
        table.add_column("Machine", style="dim", no_wrap=True)
        table.add_column("Source", style="dim", no_wrap=True)

        if not events:
            _console.print("[dim]  No events recorded within this run's window.[/dim]")
            continue

        for ev in events:
            table.add_row(
                ev.event_type,
                _fmt_ts(ev.created_at),
                str(row.machine or ""),
                str(ev.source or ""),
            )
        _console.print(table)


# ---------------------------------------------------------------------------
# Machine report  (-M)
# ---------------------------------------------------------------------------

def _run_machine_report(pattern: str, *, tsv: bool) -> None:
    with sync_session() as session:
        if pattern.upper() == "ALL":
            machines = machine_repo.list_all(session)
        else:
            import fnmatch
            fn = _like_to_fnmatch(pattern)
            machines = [m for m in machine_repo.list_all(session) if fnmatch.fnmatch(m.machine_name, fn)]

    if not machines:
        _err.print(f"[yellow]No machines matched pattern:[/yellow] {pattern!r}")
        sys.exit(0)

    if tsv:
        for m in machines:
            print(m.machine_name, m.host, m.port, m.status, sep="\t")
        return

    table = Table(box=rich_box.SIMPLE_HEAD, show_header=True, header_style="bold", padding=(0, 1))
    table.add_column("Machine",  min_width=20, no_wrap=True)
    table.add_column("Host",     min_width=16, no_wrap=True)
    table.add_column("Port",     min_width=6,  no_wrap=True, justify="right")
    table.add_column("Status",   min_width=8,  no_wrap=True)

    for m in machines:
        colour = {"UP": "green", "DOWN": "red"}.get(m.status, "yellow")
        table.add_row(m.machine_name, m.host, str(m.port), f"[{colour}]{m.status}[/{colour}]")

    _console.print()
    _console.print(table)
    _console.print(f"[dim]{len(machines)} machine{'s' if len(machines) != 1 else ''} found.[/dim]\n")


# ---------------------------------------------------------------------------
# Global-variable report  (-G)
# ---------------------------------------------------------------------------

def _run_global_report(pattern: str, *, tsv: bool) -> None:
    with sync_session() as session:
        if pattern.upper() == "ALL":
            globals_ = glob_repo.list_all(session)
        else:
            import fnmatch
            fn = _like_to_fnmatch(pattern)
            globals_ = [g for g in glob_repo.list_all(session) if fnmatch.fnmatch(g.global_name, fn)]

    if not globals_:
        _err.print(f"[yellow]No global variables matched pattern:[/yellow] {pattern!r}")
        sys.exit(0)

    if tsv:
        for g in globals_:
            print(g.global_name, g.value, sep="\t")
        return

    table = Table(box=rich_box.SIMPLE_HEAD, show_header=True, header_style="bold", padding=(0, 1))
    table.add_column("Global Name", min_width=20, no_wrap=True)
    table.add_column("Value",       min_width=20)

    for g in globals_:
        table.add_row(g.global_name, g.value)

    _console.print()
    _console.print(table)
    _console.print(f"[dim]{len(globals_)} global{'s' if len(globals_) != 1 else ''} found.[/dim]\n")


# ---------------------------------------------------------------------------
# Job summary table  (default, and -s)
# ---------------------------------------------------------------------------

def _print_table(rows: list[JobRow], run_counts: dict[str, int], *, level: Optional[int]) -> None:
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
    table.add_column("Type",       style="dim",     min_width=6,  no_wrap=True)
    table.add_column("Application", style="dim",    min_width=12, no_wrap=True)

    def _add_row(r: JobRow, indent: int = 0) -> None:
        status = r.status if r.status is not None else JobStatus.INACTIVE.value
        abbrev = _abbrev(status)
        colour = _status_colour(status)
        prefix = "  " * indent
        table.add_row(
            prefix + r.job_name,
            _fmt_ts(r.last_start),
            _fmt_ts(r.last_end),
            f"[{colour}]{abbrev}[/{colour}]",
            str(run_counts.get(r.job_name, 0)),
            str(r.job_type or ""),
            str(r.application or ""),
        )

    if level == 0:
        for row in rows:
            _add_row(row, indent=0)
    else:
        # Render BOX jobs with their children indented beneath them, down to
        # `level` levels deep (None = unlimited, matching real -L's default).
        rendered: set[str] = set()

        def _add_children(box_name: str, depth: int) -> None:
            if level is not None and depth > level:
                return
            for child in box_children.get(box_name, []):
                if child.job_name in rendered:
                    continue
                _add_row(child, indent=depth)
                rendered.add(child.job_name)
                if child.job_type == "BOX":
                    _add_children(child.job_name, depth + 1)

        for row in rows:
            if row.job_name in rendered:
                continue
            _add_row(row, indent=0)
            rendered.add(row.job_name)
            if row.job_type == "BOX":
                _add_children(row.job_name, 1)
        # Any remaining rows not rendered yet (e.g. whose parent wasn't in the query)
        for row in rows:
            if row.job_name not in rendered:
                _add_row(row, indent=0)
                rendered.add(row.job_name)

    _console.print()
    _console.print(table)
    _console.print(f"[dim]{len(rows)} job{'s' if len(rows) != 1 else ''} found.[/dim]\n")


def _print_tsv(rows: list[JobRow], run_counts: dict[str, int]) -> None:
    """Print tab-separated values for scripting (this simulator's own addition)."""
    for r in rows:
        status = r.status if r.status is not None else JobStatus.INACTIVE.value
        print(
            r.job_name,
            _fmt_ts(r.last_start),
            _fmt_ts(r.last_end),
            _abbrev(status),
            str(run_counts.get(r.job_name, 0)),
            str(r.job_type or ""),
            str(r.application or ""),
            sep="\t",
        )
