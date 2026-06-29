"""
autosys box — Box job status and dependency tree commands.

Commands
--------
autosys box status <box>   Show the box and all its children with status.
autosys box tree   <box>   Print a dependency tree of children.

Background
----------
A BOX job is AutoSys's unit of workflow orchestration.  It groups related
CMD jobs that share a common start trigger and have dependencies on each
other.  The BOX itself starts when a ``STARTJOB`` event fires; children
start automatically when the box is RUNNING and their conditions are met.

Example box hierarchy::

    demo_etl_box            [BOX]   RUNNING
    ├── check_source_ready  [CMD]   SUCCESS
    ├── extract_sales       [CMD]   RUNNING  (condition: s(check_source_ready))
    └── load_to_warehouse   [CMD]   INACTIVE (condition: s(extract_sales))

``autosys box status demo_etl_box`` renders this as a Rich table.
``autosys box tree   demo_etl_box`` renders the tree view above.
"""

from __future__ import annotations

import sys
from typing import Optional

import click
from rich.console import Console
from rich.table   import Table
from rich.tree    import Tree
from rich         import box as rich_box

from autosys.db.connection  import sync_session
from autosys.db.repository  import jobs as job_repo

_console = Console()
_err     = Console(stderr=True)

_STATUS_COLOUR = {
    "INACTIVE":   "dim",
    "STARTING":   "yellow",
    "RUNNING":    "blue",
    "SUCCESS":    "green",
    "FAILURE":    "red",
    "TERMINATED": "red",
    "ON_HOLD":    "yellow",
    "ON_ICE":     "dim",
    "UNKNOWN":    "dim",
}


@click.group(name="box")
def box_group() -> None:
    """Box job inspection commands."""


# ---------------------------------------------------------------------------
# box status
# ---------------------------------------------------------------------------

@box_group.command("status")
@click.argument("box_name")
def box_status(box_name: str) -> None:
    """
    Show the status of BOX_NAME and all its direct children.

    Displays job name, type, status, condition, and machine in a table.
    Useful for quickly seeing which step of a workflow is blocked.

    Example
    -------
    \\b
        $ autosys box status demo_etl_box

        Box: demo_etl_box  [RUNNING]
        ┌─ Job Name ──────────────────┬─ Type ─┬─ Status ──┬─ Condition ─────────────────┐
        │ check_source_ready          │ CMD    │ SUCCESS   │                             │
        │ extract_sales               │ CMD    │ RUNNING   │ s(check_source_ready)       │
        │ load_to_warehouse           │ CMD    │ INACTIVE  │ s(extract_sales)            │
        └─────────────────────────────┴────────┴───────────┴─────────────────────────────┘
    """
    with sync_session() as session:
        box_row = job_repo.get_row(session, box_name)
        if box_row is None:
            _err.print(f"[red]Job {box_name!r} not found.[/red]")
            sys.exit(1)
        if box_row.job_type != "BOX":
            _err.print(
                f"[yellow]{box_name!r} is a {box_row.job_type} job, not a BOX.[/yellow]\n"
                f"Use 'autosys autorep -j {box_name}' for non-box jobs."
            )
            sys.exit(1)

        children = job_repo.get_children(session, box_name)

    # Box header
    box_col = _STATUS_COLOUR.get(box_row.status, "white")
    _console.print(
        f"\nBox: [cyan]{box_name}[/cyan]  "
        f"[[{box_col}]{box_row.status}[/{box_col}]]\n"
    )

    if not children:
        _console.print("[dim]  (no children defined)[/dim]\n")
        return

    table = Table(
        box         = rich_box.SIMPLE_HEAD,
        show_header = True,
        header_style = "bold",
        padding     = (0, 1),
    )
    table.add_column("Job Name",   min_width=28)
    table.add_column("Type",       min_width=5)
    table.add_column("Status",     min_width=10)
    table.add_column("Machine",    min_width=12)
    table.add_column("Condition")

    for child in sorted(children, key=lambda r: r.job_name):
        col = _STATUS_COLOUR.get(child.status, "white")
        table.add_row(
            child.job_name,
            child.job_type or "CMD",
            f"[{col}]{child.status}[/{col}]",
            child.machine or "—",
            child.condition or "",
        )

    _console.print(table)
    _console.print()


# ---------------------------------------------------------------------------
# box tree
# ---------------------------------------------------------------------------

@box_group.command("tree")
@click.argument("box_name")
@click.option("--depth", "-d", default=3, show_default=True,
              help="Maximum nesting depth (for nested boxes).")
def box_tree(box_name: str, depth: int) -> None:
    """
    Print a dependency tree for BOX_NAME showing execution order.

    Children are displayed in topological order: jobs with no dependencies
    first, then jobs whose conditions reference those jobs.  Each node
    shows status with colour.

    Example
    -------
    \\b
        $ autosys box tree demo_etl_box
        demo_etl_box [RUNNING]
        ├── check_source_ready [SUCCESS]
        ├── extract_sales [RUNNING]    (condition: s(check_source_ready))
        └── load_to_warehouse [INACTIVE]  (condition: s(extract_sales))
    """
    with sync_session() as session:
        box_row = job_repo.get_row(session, box_name)
        if box_row is None:
            _err.print(f"[red]Job {box_name!r} not found.[/red]")
            sys.exit(1)

        tree_data = _build_tree_data(session, box_row, depth)

    tree = Tree(_format_node(box_row))
    _populate_tree(tree, tree_data)
    _console.print()
    _console.print(tree)
    _console.print()


def _format_node(row) -> str:
    col    = _STATUS_COLOUR.get(row.status, "white")
    label  = f"[cyan]{row.job_name}[/cyan]  [[{col}]{row.status}[/{col}]]"
    if row.condition:
        label += f"  [dim]({row.condition})[/dim]"
    return label


def _build_tree_data(session, box_row, depth: int, current_depth: int = 0):
    """Recursively build (row, children_data) for tree rendering."""
    if current_depth >= depth:
        return (box_row, [])
    children = job_repo.get_children(session, box_row.job_name)
    # Sort topologically: jobs without conditions first
    children_sorted = sorted(children, key=lambda r: (1 if r.condition else 0, r.job_name))
    subtrees = []
    for child in children_sorted:
        if child.job_type == "BOX":
            subtrees.append(_build_tree_data(session, child, depth, current_depth + 1))
        else:
            subtrees.append((child, []))
    return (box_row, subtrees)


def _populate_tree(tree_node, node_data):
    _, children_data = node_data
    for child_data in children_data:
        child_row, grandchildren = child_data
        branch = tree_node.add(_format_node(child_row))
        for gc_data in grandchildren:
            _populate_tree(branch, (None, [gc_data]))
