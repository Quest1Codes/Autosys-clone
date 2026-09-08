"""
JIL serializer — converts Pydantic Job models back to JIL text.

Used by:
  autosys jil export <job_name>   — dump one job from the DB as JIL
  autosys jil export --all        — dump all jobs (round-trip of import)

Output follows the exact format that AutoSys's own ``jil`` command produces
when you run ``jil < /dev/null`` on a real system, so the output can be
reimported unchanged.

Format rules
-------------
1.  Stanza header:   ``insert_job: NAME   job_type: TYPE``
2.  Each set attribute on its own line:  ``attr_name: value``
3.  Values containing spaces or special characters are double-quoted.
4.  Boolean attributes use ``1`` / ``0``  (not true/false).
5.  List attributes (days_of_week, start_times) are comma-joined.
6.  None values and default-value attributes are omitted to keep the
    output concise.
7.  Stanzas are separated by a blank line.

Attribute emit order
---------------------
Follows the AutoSys documentation order: identity → execution → BOX →
scheduling → dependencies → reliability → notifications → type-specific.
"""

from __future__ import annotations

from typing import Optional

from autosys.models.job import (
    Job, BoxJob, CmdJob, FilewatchJob, FtpJob,
)

# ===========================================================================
# Attribute ordering
# ===========================================================================

# Canonical attribute order for JIL output.
# Attributes not in this list are appended alphabetically at the end.
_ATTR_ORDER: list[str] = [
    # Identity
    "owner", "permission", "run_as_user", "description",
    # BOX containment
    "box_name", "box_success", "box_failure", "box_terminator",
    # Execution
    "command", "machine", "run_window", "profile",
    "std_out_file", "std_err_file", "std_in_file",
    # Scheduling
    "start_times", "start_mins", "days_of_week",
    "run_calendar", "exclude_calendar",
    "date_conditions", "term_run_time",
    "priority", "timezone",
    # Dependencies
    "condition",
    # Reliability
    "n_retrys", "max_run_alarm", "min_run_alarm",
    "alarm_if_fail", "alarm_if_terminated",
    # Resources
    "job_load", "max_load",
    # Extended attributes (Phase 4)
    "auto_delete", "application", "sub_application",
    "command_timeout", "continuous",
    "cpu_usage", "disk_space",
    "auth_string", "connection_retry", "connection_timeout",
    # Notifications
    "notification_msg", "notification_emailaddress",
    "notification_type", "send_report",
    # FTP-specific
    "ftp_server", "ftp_user", "ftp_type", "ftp_src", "ftp_dest",
    # FILEWATCH-specific
    "watch_file", "watch_file_min_size", "watch_interval",
    # Resources string
    "resources",
]

_ATTR_ORDER_IDX: dict[str, int] = {k: i for i, k in enumerate(_ATTR_ORDER)}

# Attributes whose default value means "don't emit" (skip in output)
_BOOL_DEFAULTS: dict[str, bool] = {
    "alarm_if_fail":        False,
    "alarm_if_terminated":  False,
    "box_terminator":       False,
    "date_conditions":      False,
    "send_report":          False,
    "auto_delete":          False,
    "continuous":           False,
}

_INT_DEFAULTS: dict[str, int] = {
    "n_retrys":             0,
    "job_load":             1,
    "watch_file_min_size":  0,
    "watch_interval":       60,
}

# Attributes that are NEVER emitted in the JIL output (runtime state,
# internal metadata managed by the Scheduler ACE).
_SKIP_ALWAYS = frozenset({
    "job_name", "job_type",           # encoded in the header line
    "status", "last_start", "last_end", "last_run_date",
    "created_at", "updated_at",
})


# ===========================================================================
# Value formatters
# ===========================================================================

def _needs_quoting(value: str) -> bool:
    """
    Return True if *value* must be wrapped in double-quotes in JIL output.

    AutoSys quotes values that contain:
    - Spaces or tabs  (common in commands and file paths)
    - Colons          (time values like "06:00")
    - Forward slashes (file paths — actually AutoSys doesn't quote these,
      but quoting is harmless and safer)

    Single-word values like "CMD", "svc_demo", "etl-server-01" are left
    unquoted.
    """
    return " " in value or "\t" in value or ":" in value


def _format_value(value: object) -> Optional[str]:
    """
    Convert a Python value to a JIL-compatible string.

    Returns None if the attribute should be skipped (default / empty).
    """
    if value is None:
        return None

    if isinstance(value, bool):
        return "1" if value else "0"

    if isinstance(value, int):
        return str(value)

    if isinstance(value, list):
        if not value:
            return None
        joined = ",".join(str(v) for v in value)
        # Quote if the joined string contains characters that need quoting
        # (e.g. "06:00,18:00" contains colons → must be quoted).
        if _needs_quoting(joined):
            return f'"{joined}"'
        return joined

    s = str(value)
    if not s:
        return None

    if _needs_quoting(s):
        return f'"{s}"'
    return s


# ===========================================================================
# Core serializer
# ===========================================================================

def job_to_jil(job: Job, op: str = "insert") -> str:
    """
    Serialize a Pydantic Job model to a JIL stanza string.

    Parameters
    ----------
    job:
        Any validated Pydantic Job subclass.
    op:
        The JIL directive: ``"insert"``, ``"update"``, ``"delete"``,
        ``"override"``.  Defaults to ``"insert"``.

    Returns
    -------
    str
        A complete JIL stanza with a trailing newline.
        Example::

            insert_job: demo_etl_box   job_type: BOX
            owner: svc_demo
            start_times: "06:00"
            days_of_week: mo,tu,we,th,fr
            exclude_calendar: us_holidays
            alarm_if_fail: 1
            max_run_alarm: 120

    """
    directive = {
        "insert":   "insert_job",
        "update":   "update_job",
        "delete":   "delete_job",
        "override": "override_job",
    }.get(op, "insert_job")

    lines: list[str] = []

    # --- Header ---
    lines.append(f"{directive}: {job.job_name}   job_type: {job.job_type}")

    # --- Gather all serialisable attributes ---
    raw = job.model_dump()

    attrs: list[tuple[str, str]] = []
    for attr, raw_value in raw.items():
        if attr in _SKIP_ALWAYS:
            continue

        formatted = _format_value(raw_value)
        if formatted is None:
            continue

        # Skip default-value booleans
        if attr in _BOOL_DEFAULTS:
            is_true = raw_value is True or raw_value == 1
            if is_true == _BOOL_DEFAULTS[attr]:
                continue   # default → skip
            formatted = "1" if is_true else "0"

        # Skip default-value integers
        if attr in _INT_DEFAULTS and isinstance(raw_value, int):
            if raw_value == _INT_DEFAULTS[attr]:
                continue   # default → skip

        attrs.append((attr, formatted))

    # --- Sort by canonical order ---
    def _sort_key(pair: tuple[str, str]) -> tuple[int, str]:
        idx = _ATTR_ORDER_IDX.get(pair[0], len(_ATTR_ORDER))
        return (idx, pair[0])

    attrs.sort(key=_sort_key)

    for attr, value in attrs:
        lines.append(f"{attr}: {value}")

    return "\n".join(lines)


def jobs_to_jil(
    jobs: list[Job],
    op: str = "insert",
    header_comment: Optional[str] = None,
) -> str:
    """
    Serialize multiple Job models to a complete JIL file string.

    Parameters
    ----------
    jobs:
        List of Job models (typically all returned by JobRepository.list_all).
    op:
        Directive for all jobs.  Defaults to ``"insert"``.
    header_comment:
        Optional ``/* ... */`` comment prepended to the file.

    Returns
    -------
    str
        Full JIL file text, stanzas separated by blank lines.

    Example
    -------
    >>> text = jobs_to_jil([box_job, cmd_job])
    >>> print(text)
    insert_job: my_box   job_type: BOX
    owner: svc_demo

    insert_job: my_cmd   job_type: CMD
    command: /scripts/run.sh
    machine: etl-server-01
    """
    parts: list[str] = []

    if header_comment:
        parts.append(f"/* {header_comment} */")
        parts.append("")

    for job in jobs:
        parts.append(job_to_jil(job, op=op))
        parts.append("")   # blank line between stanzas

    return "\n".join(parts).rstrip() + "\n"


def machine_to_jil(machine_row) -> str:
    """
    Serialize a ``MachineRow`` (or any object with machine attrs) to JIL.

    Output format::

        insert_machine: etl-server-01
            type: a
            host: 192.168.1.10
            port: 7520
            max_load: 100

    Parameters
    ----------
    machine_row:
        A ``MachineRow`` DB row or a ``MachineDef`` Pydantic model.

    Returns
    -------
    str
        A single ``insert_machine:`` stanza.
    """
    name = getattr(machine_row, "machine_name", "unknown")
    lines: list[str] = [f"insert_machine: {name}"]

    host = getattr(machine_row, "host", None)
    if host and host != name:
        lines.append(f"    host: {host}")

    port = getattr(machine_row, "port", 7520)
    lines.append(f"    type: a")
    lines.append(f"    port: {port}")

    max_load = getattr(machine_row, "max_load", None)
    if max_load:
        lines.append(f"    max_load: {max_load}")

    description = getattr(machine_row, "description", None)
    if description:
        lines.append(f"    description: {description}")

    return "\n".join(lines)
