"""
autosys jil — JIL file operations.

Commands
--------
autosys jil import FILE      Parse a .jil file and persist jobs to the DB.
autosys jil export JOB_NAME  Read a job from the DB and print JIL text.
autosys jil validate FILE    Parse and validate a .jil file without persisting.
"""

from __future__ import annotations

import sys
from pathlib import Path

import click
from rich.console import Console
from rich.table import Table
from rich import box as rich_box

from autosys.db.connection import sync_session
from autosys.db.repository import jobs as job_repo
from autosys.parser.jil_parser import parse_jil_file, JILParseError
from autosys.parser.jil_apply import apply_operation
from autosys.parser.lexer import LexError
from autosys.parser.jil_writer import jobs_to_jil, job_to_jil

_console = Console()
_err     = Console(stderr=True)

# Printed action -> colour, for the per-stanza lines `jil import` prints.
_ACTION_COLOURS = {
    "INSERTED": "green", "UPDATED": "yellow", "DELETED": "red", "MACHINE": "blue",
    "SKIPPED": "magenta", "OK": "cyan", "RENAMED": "cyan", "RESOURCE": "blue",
    "JOB_TYPE": "blue", "MONBRO": "blue", "BLOB": "blue", "GLOB": "blue",
    "XINST": "blue", "PROFILE": "blue", "CALENDAR": "blue",
}
# ApplyResult.counter key -> the plural noun used in the summary line.
_COUNTER_LABELS = [
    ("machines", "machine"), ("resources", "resource"), ("job_types", "job type"),
    ("monitors", "monitor"), ("blobs", "blob"), ("globs", "glob"), ("xinsts", "xinst"),
    ("profiles", "profile"), ("calendars", "calendar"),
]


def _print_import_results(
    results: list[tuple[str, str, str]], counters: dict[str, int], dry_run: bool, quiet: bool,
) -> None:
    """Print `jil import`'s per-stanza lines and summary — shared by --strict and
    the tolerant default, so the two report their (differently-derived) results
    identically."""
    if not quiet:
        for action, name, jtype in results:
            colour = _ACTION_COLOURS.get(action, "white")
            _console.print(f"  [{colour}]{action:<8}[/{colour}]  {name:<30}  {jtype}")
        _console.print()

    n_inserted = counters.get("inserted", 0)
    n_updated  = counters.get("updated", 0)
    n_deleted  = counters.get("deleted", 0)
    n_jobs = n_inserted + n_updated + n_deleted
    total  = n_jobs + sum(counters.get(k, 0) for k, _ in _COUNTER_LABELS)

    if dry_run:
        _console.print(
            f"[cyan]Validation OK[/cyan] — "
            f"{total} stanza{'s' if total != 1 else ''} parsed successfully."
        )
    else:
        parts = [f"{n_jobs} job{'s' if n_jobs != 1 else ''} imported"]
        for key, label in _COUNTER_LABELS:
            n = counters.get(key, 0)
            if n:
                parts.append(f"{n} {label}{'s' if n != 1 else ''}")
        _console.print(
            "[green]" + ", ".join(parts) + "[/green] "
            f"({n_inserted} inserted, {n_updated} updated, {n_deleted} deleted)."
        )
    _console.print()


# ===========================================================================
# jil group
# ===========================================================================

@click.group(name="jil")
def jil_group() -> None:
    """JIL file operations: import, export, validate."""


# ===========================================================================
# jil import
# ===========================================================================

@jil_group.command("import")
@click.argument("file", type=click.Path(exists=True, readable=True, path_type=Path))
@click.option("--dry-run", is_flag=True, default=False,
              help="Parse and validate only — do NOT write to the database.")
@click.option("--quiet", "-q", is_flag=True, default=False,
              help="Suppress per-job lines; only print the summary.")
@click.option("--strict", is_flag=True, default=False,
              help="Fail the whole import on the first malformed stanza instead of "
                   "quarantining it and importing everything else (the pre-tolerant-"
                   "ingest behaviour).")
def jil_import(file: Path, dry_run: bool, quiet: bool, strict: bool) -> None:
    """
    Parse FILE and persist all insert_job / update_job stanzas to the DB.

    Mirrors the real AutoSys ``jil < file.jil`` command.  Each stanza
    produces one DB upsert; the operation that was performed (inserted /
    updated) is shown for each job.

    By default a malformed stanza is quarantined — archived verbatim and
    reported as SKIPPED — rather than failing the whole file; this is the
    same lossless ingestion ``jil import-dir`` uses, including the raw-text
    archive in ``ujo_jil_file`` / ``ujo_jil_stanza``.  Pass --strict for the
    old behaviour: stop and exit 1 on the first stanza that fails to parse
    or validate.

    With --dry-run the file is parsed and validated but nothing is written
    to the database.

    Example
    -------
    \\b
        $ autosys jil import examples/demo_etl.jil
        Importing demo_etl.jil ...

          INSERTED  demo_etl_box          BOX
          INSERTED  check_source_ready    CMD
          ...

        7 jobs imported (7 inserted, 0 updated, 0 deleted).
    """
    label = "[dim]DRY-RUN[/dim] " if dry_run else ""
    _console.print(f"\n{label}Importing [bold]{file.name}[/bold] …\n")

    if strict:
        try:
            ops = parse_jil_file(str(file))
        except (JILParseError, LexError) as exc:
            _err.print(f"[red]Parse error:[/red] {exc}")
            sys.exit(1)
        except Exception as exc:
            _err.print(f"[red]Error reading {file}:[/red] {exc}")
            sys.exit(1)

        results: list[tuple[str, str, str]] = []
        counters: dict[str, int] = {}
        with sync_session() as session:
            for op in ops:
                # The session runs with autoflush off: flush so a later
                # stanza about the same object (insert_x then update_x)
                # sees this one.
                session.flush()
                result = apply_operation(session, op, dry_run=dry_run)
                results.append((result.action, result.name, result.detail))
                if result.counter:
                    counters[result.counter] = counters.get(result.counter, 0) + result.count
        _print_import_results(results, counters, dry_run, quiet)
        return

    # Tolerant default: the same lossless ingester `jil import-dir` uses for
    # a whole directory. Nothing here raises for content reasons — a bad
    # stanza is archived and reported SKIPPED, not a crash.
    from autosys.parser.jil_ingest import ingest_file

    with sync_session() as session:
        report = ingest_file(session, file, dry_run=dry_run)

    for issue in report.issues:            # file-level (unreadable, odd encoding, ...)
        _console.print(f"  [yellow]{issue['code']}[/yellow]: {issue['message']}")
    _print_import_results(report.results, report.counters, dry_run, quiet)

    n_quarantined = report.dispositions.get("QUARANTINED", 0)
    if n_quarantined:
        _console.print(
            f"[yellow]{n_quarantined} stanza{'s' if n_quarantined != 1 else ''} could not be "
            "read as JIL and were archived, not imported — see the SKIPPED line(s) above, "
            "or re-run with --strict for a hard failure.[/yellow]\n"
        )


# ===========================================================================
# jil export
# ===========================================================================

@jil_group.command("export")
@click.argument("job_name")
@click.option("--all", "export_all", is_flag=True, default=False,
              help="Export every job in the database.")
@click.option("--op", default="insert",
              type=click.Choice(["insert", "update", "override"]),
              help="JIL directive to use (default: insert).")
def jil_export(job_name: str, export_all: bool, op: str) -> None:
    """
    Read a job from the database and print it as JIL text.

    JOB_NAME is the exact name of the job to export.  Use --all to export
    every job in the database at once (JOB_NAME is then ignored).

    The output is valid JIL that can be piped back into ``jil import``.

    Example
    -------
    \\b
        $ autosys jil export demo_etl_box
        insert_job: demo_etl_box   job_type: BOX
        owner: svc_demo
        start_times: "06:00"
        ...

        $ autosys jil export --all > all_jobs.jil
    """
    with sync_session() as session:
        if export_all:
            rows = job_repo.list_all(session)
            if not rows:
                _err.print("[yellow]No jobs found in the database.[/yellow]")
                sys.exit(0)
            from autosys.db.repository import _row_to_job
            job_models = [_row_to_job(r) for r in rows]
            click.echo(jobs_to_jil(job_models, op=op))
        else:
            job = job_repo.get(session, job_name)
            if job is None:
                _err.print(f"[red]Job not found:[/red] {job_name!r}")
                sys.exit(1)
            click.echo(job_to_jil(job, op=op))


# ===========================================================================
# jil validate
# ===========================================================================

@jil_group.command("validate")
@click.argument("file", type=click.Path(exists=True, readable=True, path_type=Path))
@click.option("--quiet", "-q", is_flag=True, default=False,
              help="Suppress per-job lines; only print the summary.")
@click.option("--strict", is_flag=True, default=False,
              help="Fail on the first malformed stanza instead of quarantining it "
                   "and reporting the rest (the pre-tolerant-ingest behaviour).")
def jil_validate(file: Path, quiet: bool, strict: bool) -> None:
    """
    Parse and validate FILE without writing to the database.

    Exactly ``jil import --dry-run`` — tolerant by default (a malformed
    stanza is quarantined and reported, not a hard failure), --strict for
    the old all-or-nothing contract. Use this as a quick sanity-check before
    importing a JIL file for real.

    Example
    -------
    \\b
        $ autosys jil validate examples/demo_etl.jil
          OK        demo_etl_box                    BOX
          OK        check_source_ready              CMD
          ...
        JIL file is valid: 7 stanzas parsed.
    """
    _console.print(f"\nValidating [bold]{file.name}[/bold] …\n")

    if strict:
        try:
            ops = parse_jil_file(str(file))
        except (JILParseError, LexError) as exc:
            _err.print(f"[red]Parse error:[/red] {exc}")
            sys.exit(1)
        except Exception as exc:
            _err.print(f"[red]Error reading {file}:[/red] {exc}")
            sys.exit(1)
        if not quiet:
            for op in ops:
                if op.job is not None:
                    name, jtype = op.job.job_name, str(op.job.job_type)
                elif op.machine is not None:
                    name, jtype = op.machine.machine_name, "machine"
                else:
                    name = next((v for k, v in op.raw_attrs.items() if k.endswith("_name")), op.op)
                    jtype = op.op
                _console.print(f"  [green]OK[/green]  {name:<30}  {jtype}")
            _console.print()
        n = len(ops)
        _console.print(f"[green]JIL file is valid[/green]: {n} stanza{'s' if n != 1 else ''} parsed.\n")
        return

    from autosys.parser.jil_ingest import ingest_file
    with sync_session() as session:
        report = ingest_file(session, file, dry_run=True)

    for issue in report.issues:
        _console.print(f"  [yellow]{issue['code']}[/yellow]: {issue['message']}")
    if not quiet:
        for action, name, jtype in report.results:
            colour = _ACTION_COLOURS.get(action, "white")
            _console.print(f"  [{colour}]{action:<8}[/{colour}]  {name:<30}  {jtype}")
        _console.print()

    n = len(report.results)
    n_quarantined = report.dispositions.get("QUARANTINED", 0)
    if n_quarantined:
        _console.print(
            f"[yellow]{n_quarantined} of {n} stanzas could not be read as JIL and were "
            "quarantined — see the SKIPPED line(s) above, or re-run with --strict for a hard "
            "failure.[/yellow]\n"
        )
    else:
        _console.print(f"[green]JIL file is valid[/green]: {n} stanza{'s' if n != 1 else ''} parsed.\n")


# ===========================================================================
# jil import-dir  — lossless bulk ingestion
# ===========================================================================

@jil_group.command("import-dir")
@click.argument("path", type=click.Path(exists=True, readable=True, path_type=Path))
@click.option("--pattern", default="*.jil", show_default=True, help="Glob for files under PATH.")
@click.option("--exclude", "excludes", multiple=True,
              help="Glob(s) of file names to skip (repeatable).")
@click.option("--duplicates", type=click.Choice(["first", "last"]), default="first",
              show_default=True,
              help="When a job is defined twice: keep the first (later archived as "
                   "DUPLICATE) or let the last overwrite. Both stay in the archive.")
@click.option("--commit-every", default=200, show_default=True, help="Files per commit.")
@click.option("--report", "report_path", type=click.Path(path_type=Path), default=None,
              help="Write the JSON summary here.")
@click.option("--quiet", "-q", is_flag=True, default=False)
def jil_import_dir(path: Path, pattern: str, excludes: tuple, duplicates: str, commit_every: int,
                   report_path: Path, quiet: bool) -> None:
    """
    Ingest every JIL file under PATH without losing anything.

    Never aborts on bad content: every stanza is archived verbatim and ends as
    LOADED, LOADED_WITH_WARNINGS, ARCHIVE_ONLY, QUARANTINED or DUPLICATE.
    Files in any common encoding are decoded (UTF-8/16, cp1252, latin-1).

    A dead database connection is different — every remaining file would
    fail the same way, so the run stops there instead of logging one error
    per file. Re-run this command on the files after the one it stopped at
    (sorted order is deterministic) to resume; everything up to that point
    is already durably committed.
    """
    import json
    from autosys.parser.jil_ingest import ingest_paths, iter_files

    import fnmatch
    files = [f for f in iter_files(path, pattern)
             if not any(fnmatch.fnmatch(f.name, x) for x in excludes)]

    def _tick(n, rep):
        if not quiet and (n % 1000 == 0 or n == len(files)):
            _console.print(f"  {n}/{len(files)} files")

    summary = ingest_paths(sync_session, files, commit_every=commit_every,
                           duplicates=duplicates, progress=_tick)
    data = summary.as_dict()
    if report_path:
        report_path.write_text(json.dumps(data, indent=2))
    _console.print(f"[bold]{data['files']}[/bold] files, [bold]{data['stanzas']}[/bold] stanzas")
    for k, v in sorted(data["dispositions"].items()):
        _console.print(f"  {k:<22}{v}")

    if summary.aborted:
        _err.print(
            f"\n[red]Stopped[/red]: the database connection was lost while processing "
            f"[bold]{summary.last_path}[/bold].\n"
            f"[green]{summary.files_committed}[/green] of {len(files)} files are durably "
            f"committed. Re-run this command once the database is back up — files already "
            f"committed are safely re-imported as no-ops (or use --duplicates to control "
            f"that), so pointing it at the same PATH again is enough.\n"
            f"[dim]{summary.abort_reason}[/dim]"
        )
        sys.exit(1)
