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
    n_resources = n_job_types = n_monitors = n_blobs = n_globs = 0
    n_xinsts = n_profiles = n_calendars = 0
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

            if op.op == "update_machine":
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
                results.append(("MACHINE", m.machine_name, "updated"))
                continue

            if op.op == "delete_machine":
                name = op.raw_attrs.get("machine_name", "")
                if not dry_run:
                    from autosys.db.repository import machines as machine_repo
                    machine_repo.delete(session, name)
                n_machines += 1
                results.append(("DELETED", name, "machine"))
                continue

            # ---- rename_job ----
            if op.op == "rename":
                old_name = op.raw_attrs.get("job_name", "")
                new_name = op.raw_attrs.get("new_name", "")
                if not dry_run:
                    job_repo.rename(session, old_name, new_name)
                results.append(("RENAMED", old_name, f"→ {new_name}"))
                continue

            # ---- delete_box ----
            if op.op == "delete" and op.job is not None and str(op.job.job_type) == "BOX":
                name = op.job.job_name
                if not dry_run:
                    count = job_repo.delete_box(session, name)
                else:
                    count = 0
                n_deleted += count
                results.append(("DELETED", name, f"box ({count} jobs)"))
                continue

            # ---- override_job (apply as update) ----
            if op.op == "override":
                job = op.job
                name = job.job_name
                if dry_run:
                    action = "OK"
                else:
                    result = job_repo.upsert(session, job)
                    action = result.upper()
                results.append((action, name, "override"))
                continue

            # ---- resources ----
            if op.op in ("insert_resource", "update_resource"):
                name = op.raw_attrs.get("resource_name", "")
                max_load = int(op.raw_attrs.get("max_load", "1"))
                desc = op.raw_attrs.get("description")
                if not dry_run:
                    from autosys.db.repository import resources as resource_repo
                    resource_repo.upsert(session, name, max_load=max_load, description=desc)
                n_resources += 1
                results.append(("RESOURCE", name, f"max_load:{max_load}"))
                continue

            if op.op == "delete_resource":
                name = op.raw_attrs.get("resource_name", "")
                if not dry_run:
                    from autosys.db.repository import resources as resource_repo
                    resource_repo.delete(session, name)
                n_resources += 1
                results.append(("DELETED", name, "resource"))
                continue

            # ---- job types ----
            if op.op in ("insert_job_type", "update_job_type"):
                name = op.raw_attrs.get("job_type_name", "")
                cmd = op.raw_attrs.get("command")
                desc = op.raw_attrs.get("description")
                if not dry_run:
                    from autosys.db.repository import job_types as job_type_repo
                    job_type_repo.upsert(session, name, command_template=cmd, description=desc)
                n_job_types += 1
                results.append(("JOB_TYPE", name, desc or ""))
                continue

            if op.op == "delete_job_type":
                name = op.raw_attrs.get("job_type_name", "")
                if not dry_run:
                    from autosys.db.repository import job_types as job_type_repo
                    job_type_repo.delete(session, name)
                n_job_types += 1
                results.append(("DELETED", name, "job_type"))
                continue

            # ---- monbro ----
            if op.op in ("insert_monbro", "update_monbro"):
                name = op.raw_attrs.get("monbro_name", "")
                mtype = op.raw_attrs.get("monbro_type", "FILE_MONITOR")
                jname = op.raw_attrs.get("job_name")
                import json as _json
                attrs = {k: v for k, v in op.raw_attrs.items()
                         if k not in ("monbro_name", "monbro_type", "job_name")}
                attrs_json = _json.dumps(attrs) if attrs else None
                if not dry_run:
                    from autosys.db.repository import monitors as monitor_repo
                    monitor_repo.upsert(session, name, mtype, job_name=jname,
                                       attributes_json=attrs_json)
                n_monitors += 1
                results.append(("MONBRO", name, mtype))
                continue

            if op.op == "delete_monbro":
                name = op.raw_attrs.get("monbro_name", "")
                if not dry_run:
                    from autosys.db.repository import monitors as monitor_repo
                    monitor_repo.delete(session, name)
                n_monitors += 1
                results.append(("DELETED", name, "monbro"))
                continue

            # ---- blobs ----
            if op.op == "insert_blob":
                name = op.raw_attrs.get("blob_name", "")
                jname = op.raw_attrs.get("job_name")
                bfile = op.raw_attrs.get("blob_file", "")
                content = ""
                if bfile:
                    try:
                        with open(bfile) as bf:
                            content = bf.read()
                    except OSError:
                        content = ""
                if not dry_run:
                    from autosys.db.repository import blobs as blob_repo
                    blob_repo.insert(session, name, content, job_name=jname)
                n_blobs += 1
                results.append(("BLOB", name, jname or ""))
                continue

            if op.op == "delete_blob":
                name = op.raw_attrs.get("blob_name", "")
                if not dry_run:
                    from autosys.db.repository import blobs as blob_repo
                    blob_repo.delete(session, name)
                n_blobs += 1
                results.append(("DELETED", name, "blob"))
                continue

            # ---- globs ----
            if op.op == "insert_glob":
                name = op.raw_attrs.get("global_name", "")
                gfile = op.raw_attrs.get("blob_file", "")
                content = ""
                if gfile:
                    try:
                        with open(gfile) as gf:
                            content = gf.read()
                    except OSError:
                        content = ""
                if not dry_run:
                    from autosys.db.repository import globs2 as glob_repo
                    glob_repo.upsert(session, name, content)
                n_globs += 1
                results.append(("GLOB", name, ""))
                continue

            if op.op == "delete_glob":
                name = op.raw_attrs.get("global_name", "")
                if not dry_run:
                    from autosys.db.repository import globs2 as glob_repo
                    glob_repo.delete(session, name)
                n_globs += 1
                results.append(("DELETED", name, "glob"))
                continue

            # ---- xinst ----
            if op.op in ("insert_xinst", "update_xinst"):
                name = op.raw_attrs.get("xinst_name", "")
                inst = op.raw_attrs.get("instance_name", name)
                host = op.raw_attrs.get("host", "localhost")
                port = int(op.raw_attrs.get("port", "9000"))
                desc = op.raw_attrs.get("description")
                if not dry_run:
                    from autosys.db.repository import xinsts as xinst_repo
                    xinst_repo.upsert(session, name, inst, host, port=port, description=desc)
                n_xinsts += 1
                results.append(("XINST", name, f"{host}:{port}"))
                continue

            if op.op == "delete_xinst":
                name = op.raw_attrs.get("xinst_name", "")
                if not dry_run:
                    from autosys.db.repository import xinsts as xinst_repo
                    xinst_repo.delete(session, name)
                n_xinsts += 1
                results.append(("DELETED", name, "xinst"))
                continue

            # ---- connection profiles ----
            if op.op == "insert_connectionprofile":
                name = op.raw_attrs.get("profile_name", "")
                ptype = op.raw_attrs.get("profile_type", "HADOOP")
                import json as _json
                attrs = {k: v for k, v in op.raw_attrs.items()
                         if k not in ("profile_name", "profile_type")}
                attrs_json = _json.dumps(attrs) if attrs else None
                if not dry_run:
                    from autosys.db.repository import profiles as profile_repo
                    profile_repo.upsert(session, name, ptype, attributes_json=attrs_json)
                n_profiles += 1
                results.append(("PROFILE", name, ptype))
                continue

            if op.op == "delete_connectionprofile":
                name = op.raw_attrs.get("profile_name", "")
                if not dry_run:
                    from autosys.db.repository import profiles as profile_repo
                    profile_repo.delete(session, name)
                n_profiles += 1
                results.append(("DELETED", name, "profile"))
                continue

            # ---- calendars ----
            if op.op in ("insert_calendar", "update_calendar"):
                name = op.raw_attrs.get("calendar_name", "")
                if not dry_run:
                    from autosys.db.repository import calendars as cal_repo
                    from autosys.db.schema import CalendarRow
                    cal_repo.upsert(session, CalendarRow(
                        calendar_name=name,
                        dates_json=op.raw_attrs.get("dates_json", "[]"),
                        description=op.raw_attrs.get("description"),
                    ))
                n_calendars += 1
                results.append(("CALENDAR", name, ""))
                continue

            if op.op == "delete_calendar":
                name = op.raw_attrs.get("calendar_name", "")
                if not dry_run:
                    from autosys.db.repository import calendars as cal_repo
                    cal_repo.delete(session, name)
                n_calendars += 1
                results.append(("DELETED", name, "calendar"))
                continue

            # ---- job definition (insert/update/delete) ----
            if op.job is None:
                results.append(("SKIPPED", op.op, "unsupported"))
                continue

            job   = op.job
            name  = job.job_name
            jtype = str(job.job_type)

            if op.op == "delete":
                if not dry_run:
                    job_repo.delete(session, name)
                action = "DELETED"
                n_deleted += 1
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
                "RENAMED":  "cyan",
                "RESOURCE": "blue",
                "JOB_TYPE": "blue",
                "MONBRO":   "blue",
                "BLOB":     "blue",
                "GLOB":     "blue",
                "XINST":    "blue",
                "PROFILE":  "blue",
                "CALENDAR": "blue",
            }.get(action, "white")
            _console.print(
                f"  [{colour}]{action:<8}[/{colour}]  "
                f"{name:<30}  {jtype}"
            )
        _console.print()

    n_jobs = n_inserted + n_updated + n_deleted
    total  = (n_jobs + n_machines + n_resources + n_job_types + n_monitors
              + n_blobs + n_globs + n_xinsts + n_profiles + n_calendars)
    if dry_run:
        _console.print(
            f"[cyan]Validation OK[/cyan] — "
            f"{total} stanza{'s' if total != 1 else ''} parsed successfully."
        )
    else:
        parts = [f"{n_jobs} job{'s' if n_jobs != 1 else ''} imported"]
        if n_machines:
            parts.append(f"{n_machines} machine{'s' if n_machines != 1 else ''}")
        if n_resources:
            parts.append(f"{n_resources} resource{'s' if n_resources != 1 else ''}")
        if n_job_types:
            parts.append(f"{n_job_types} job type{'s' if n_job_types != 1 else ''}")
        if n_monitors:
            parts.append(f"{n_monitors} monitor{'s' if n_monitors != 1 else ''}")
        if n_blobs:
            parts.append(f"{n_blobs} blob{'s' if n_blobs != 1 else ''}")
        if n_globs:
            parts.append(f"{n_globs} glob{'s' if n_globs != 1 else ''}")
        if n_xinsts:
            parts.append(f"{n_xinsts} xinst{'s' if n_xinsts != 1 else ''}")
        if n_profiles:
            parts.append(f"{n_profiles} profile{'s' if n_profiles != 1 else ''}")
        if n_calendars:
            parts.append(f"{n_calendars} calendar{'s' if n_calendars != 1 else ''}")
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
            if op.job is not None:
                _console.print(
                    f"  [green]OK[/green]  "
                    f"{op.job.job_name:<30}  {op.job.job_type}"
                )
            elif op.machine is not None:
                _console.print(
                    f"  [green]OK[/green]  "
                    f"{op.machine.machine_name:<30}  machine"
                )
            else:
                name = (
                    op.raw_attrs.get("resource_name")
                    or op.raw_attrs.get("job_type_name")
                    or op.raw_attrs.get("monbro_name")
                    or op.raw_attrs.get("blob_name")
                    or op.raw_attrs.get("global_name")
                    or op.raw_attrs.get("xinst_name")
                    or op.raw_attrs.get("profile_name")
                    or op.raw_attrs.get("calendar_name")
                    or op.raw_attrs.get("machine_name")
                    or op.raw_attrs.get("job_name")
                    or op.op
                )
                _console.print(
                    f"  [green]OK[/green]  "
                    f"{name:<30}  {op.op}"
                )
        _console.print()

    n = len(ops)
    _console.print(
        f"[green]JIL file is valid[/green]: "
        f"{n} stanza{'s' if n != 1 else ''} parsed.\n"
    )
