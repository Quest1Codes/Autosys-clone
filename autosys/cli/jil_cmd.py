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
from autosys.parser.jil_parser import parse_jil_file, parse_jil, JILParseError
from autosys.parser.jil_writer import jobs_to_jil, job_to_jil

_console = Console()
_err     = Console(stderr=True)


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
def jil_import(file: Path, dry_run: bool, quiet: bool) -> None:
    """
    Parse FILE and persist all insert_job / update_job stanzas to the DB.

    Mirrors the real AutoSys ``jil < file.jil`` command.  Each stanza
    produces one DB upsert; the operation that was performed (inserted /
    updated) is shown for each job.

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

    try:
        ops = parse_jil_file(str(file))
    except JILParseError as exc:
        _err.print(f"[red]Parse error:[/red] {exc}")
        sys.exit(1)
    except Exception as exc:
        _err.print(f"[red]Error reading {file}:[/red] {exc}")
        sys.exit(1)

    # Tally by directive
    n_inserted = n_updated = n_deleted = n_machines = 0
    results: list[tuple[str, str, str]] = []   # (action, name, type)

    with sync_session() as session:
        for op in ops:

            # ---- machine definition ----
            if op.op == "insert_machine":
                m = op.machine
                if not dry_run:
                    from autosys.db.repository import machines as machine_repo
                    machine_repo.register(
                        session,
                        machine_name = m.machine_name,
                        host         = m.host or m.machine_name,
                        port         = m.port,
                    )
                n_machines += 1
                results.append(("MACHINE", m.machine_name, f"port:{m.port}"))
                continue

            # ---- unsupported/mocked definitions ----
            if op.job is None:
                # E.g. insert_job_type, insert_blob, insert_xinst
                # Parse was successful, but we don't store them in this clone yet.
                results.append(("SKIPPED", op.op, "unsupported"))
                continue
                
            # ---- job definition ----
            job   = op.job
            name  = job.job_name
            jtype = str(job.job_type)

            if op.op == "delete":
                if not dry_run:
                    job_repo.delete(session, name)
                action = "DELETED"
                n_deleted += 1
            elif op.op == "override":
                # AutoSys one-time next-run override.
                # Since we don't have the override tables, we skip it with a warning
                # rather than permanently mutating the job definition.
                action = "SKIPPED"
                jtype  = "override (unsupported)"
            else:
                if dry_run:
                    action = "OK"
                else:
                    result = job_repo.upsert(session, job)
                    action = result.upper()   # "INSERTED" or "UPDATED"
                    if action == "INSERTED":
                        n_inserted += 1
                    else:
                        n_updated += 1

            results.append((action, name, jtype))

    if not quiet:
        for action, name, jtype in results:
            colour = {
                "INSERTED": "green",
                "UPDATED":  "yellow",
                "DELETED":  "red",
                "MACHINE":  "blue",
                "SKIPPED":  "magenta",
                "OK":       "cyan",
            }.get(action, "white")
            _console.print(
                f"  [{colour}]{action:<8}[/{colour}]  "
                f"{name:<30}  {jtype}"
            )
        _console.print()

    n_jobs = n_inserted + n_updated + n_deleted
    total  = n_jobs + n_machines
    if dry_run:
        _console.print(
            f"[cyan]Validation OK[/cyan] — "
            f"{total} stanza{'s' if total != 1 else ''} parsed successfully."
        )
    else:
        parts = [f"{n_jobs} job{'s' if n_jobs != 1 else ''} imported"]
        if n_machines:
            parts.append(f"{n_machines} machine{'s' if n_machines != 1 else ''} registered")
        _console.print(
            "[green]" + ", ".join(parts) + "[/green] "
            f"({n_inserted} inserted, {n_updated} updated, {n_deleted} deleted)."
        )
    _console.print()


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
def jil_validate(file: Path, quiet: bool) -> None:
    """
    Parse and validate FILE without writing to the database.

    Identical to ``jil import --dry-run``.  Use this as a quick sanity-
    check before deploying a JIL file to production.

    Example
    -------
    \\b
        $ autosys jil validate examples/demo_etl.jil
          OK  demo_etl_box
          OK  check_source_ready
          ...
        JIL file is valid: 7 jobs defined.
    """
    _console.print(f"\nValidating [bold]{file.name}[/bold] …\n")

    try:
        ops = parse_jil_file(str(file))
    except JILParseError as exc:
        _err.print(f"[red]Parse error:[/red] {exc}")
        sys.exit(1)
    except Exception as exc:
        _err.print(f"[red]Error reading {file}:[/red] {exc}")
        sys.exit(1)

    if not quiet:
        for op in ops:
            _console.print(
                f"  [green]OK[/green]  "
                f"{op.job.job_name:<30}  {op.job.job_type}"
            )
        _console.print()

    n = len(ops)
    _console.print(
        f"[green]JIL file is valid[/green]: "
        f"{n} job{'s' if n != 1 else ''} defined.\n"
    )
