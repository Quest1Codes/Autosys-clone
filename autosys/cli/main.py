"""
autosys CLI — root Click group and entry point.

This module is registered as the ``autosys`` console script in pyproject.toml::

    [project.scripts]
    autosys = "autosys.cli.main:autosys"

Running ``autosys --help`` shows all top-level commands.

DB initialisation
-----------------
The root group's ``@click.pass_context`` callback runs before every
subcommand.  It:

1.  Reads ``--db`` (or ``$AUTOSYS_DB_URL``) to get the database URL.
2.  Sets ``AUTOSYS_DB_URL`` in the environment so that connection.py picks
    it up via its env-var default (no global state needed).
3.  Calls ``create_all_sync(drop_first=False)`` to ensure the schema exists
    (idempotent — safe to call on every invocation).

This means operators can use any SQLite file they want simply by passing
``--db sqlite:///path/to/custom.db`` or setting the env var, without
touching any config file.
"""

from __future__ import annotations

import os
import sys

import click
from rich.console import Console

from autosys.cli.jil_cmd        import jil_group
from autosys.cli.sendevent_cmd  import sendevent
from autosys.cli.autorep_cmd    import autorep
from autosys.cli.scheduler_cmd  import scheduler_group
from autosys.cli.agent_cmd      import agent_group, jobs_group
from autosys.cli.machine_cmd    import machine_group
from autosys.cli.box_cmd        import box_group
from autosys.cli.chase_cmd      import chase
from autosys.cli.autoping_cmd   import autoping
from autosys.cli.autocal_cmd    import autocal_group
from autosys.cli.analyze_cmd    import analyze

_console = Console()
_err     = Console(stderr=True)


# ---------------------------------------------------------------------------
# Root group
# ---------------------------------------------------------------------------

@click.group()
@click.version_option(package_name="autosys-clone", prog_name="autosys")
@click.option(
    "--db",
    envvar="AUTOSYS_DB_URL",
    default=None,
    metavar="URL",
    help=(
        "SQLAlchemy database URL.  "
        "Defaults to $AUTOSYS_DB_URL or a local SQLite file.  "
        "Example: sqlite:///./data/autosys.db"
    ),
)
@click.pass_context
def autosys(ctx: click.Context, db: str | None) -> None:
    """
    AutoSys-clone: a learning implementation of CA Workload Automation AE.

    \b
    Quick start:
        autosys jil import examples/demo_etl.jil
        autosys autorep -J %
        autosys sendevent -E STARTJOB -J check_source_ready
    """
    ctx.ensure_object(dict)

    # Override the database URL if supplied via --db flag
    if db:
        os.environ["AUTOSYS_DB_URL"] = db

    # Ensure the DB schema exists (idempotent)
    try:
        from autosys.db.migrations import create_all_sync
        create_all_sync(drop_first=False)
    except Exception as exc:
        _err.print(f"[red]DB init error:[/red] {exc}")
        sys.exit(1)

    ctx.obj["db_url"] = os.environ.get("AUTOSYS_DB_URL", "")


# ---------------------------------------------------------------------------
# Register subcommands
# ---------------------------------------------------------------------------

autosys.add_command(jil_group)
autosys.add_command(sendevent)
autosys.add_command(autorep)
autosys.add_command(scheduler_group)
autosys.add_command(agent_group)
autosys.add_command(jobs_group)
autosys.add_command(machine_group)
autosys.add_command(box_group)
autosys.add_command(chase)
autosys.add_command(autoping)
autosys.add_command(autocal_group)
autosys.add_command(analyze)
