"""
Airflow gap tagging — Phase 1 migration assessment.

Tags each job against the capability gaps already catalogued in
docs/strategy-and-approach.md's "Gap Analysis & Heatmap: AutoSys vs.
Astronomer (Airflow)" table, so tool output is directly traceable to that
severity model instead of inventing a parallel taxonomy.

Severity (matches the doc's heatmap key)
-----------------------------------------
  GREEN   Direct equivalent exists. Low migration effort.
  YELLOW  Partial equivalent exists. Requires re-design or custom code.
  RED     No direct equivalent. Requires a paradigm shift / external tooling.

This is a pure, DB-free function — no session, no scheduler imports —
mirroring complexity.py's design so it stays cheap to call for every job in
a bulk `analyze` run.
"""

from __future__ import annotations

import re
from typing import Optional

from autosys.db.schema import JobRow

# Standard AutoSys date/time built-ins — not counted as "business" global
# vars. Kept in sync with complexity._DT_BUILTINS (duplicated rather than
# imported to keep this module dependency-free from complexity.py).
_DT_BUILTINS: frozenset[str] = frozenset({
    "DATE", "YEAR", "MM", "DD", "TIME", "HH", "MIN",
    "ODATE", "OYEAR", "OMM", "ODD", "OTIME",
    "TIMESTAMP", "UNIX_TIMESTAMP",
})

# tag -> (severity, description, doc reference)
GAP_CATALOGUE: dict[str, tuple[str, str]] = {
    "cross-instance-event": (
        "RED",
        "sendevent-style cross-instance trigger (^) — no native Airflow "
        "equivalent; needs an external event bus (Kafka/SQS/RabbitMQ).",
    ),
    "ftp-db-jobtype": (
        "RED",
        "FTP/DB/i5 job type — needs a Provider operator equivalent "
        "(SFTPOperator, PostgresOperator, etc.).",
    ),
    "complex-calendar": (
        "YELLOW",
        "run_calendar/exclude_calendar/date_conditions — needs a custom "
        "Airflow Timetable class (2.2+).",
    ),
    "sla-management": (
        "YELLOW",
        "max_run_alarm/min_run_alarm — maps to Airflow's sla parameter / "
        "sla_miss_callback, less robust out-of-the-box.",
    ),
    "file-watcher": (
        "GREEN",
        "FILEWATCH — direct 1:1 mapping to FileSensor/SFTPSensor.",
    ),
    "global-variables": (
        "GREEN",
        "%%VAR%% business globals — direct equivalent in Airflow "
        "Variables/Connections.",
    ),
}

def _business_globals_present(command: Optional[str]) -> bool:
    """True if command references a %%VAR%% that isn't an AutoSys date/time builtin."""
    if not command:
        return False
    all_vars = re.findall(r"%%([A-Z_][A-Z_0-9]*)%%", command)
    return any(v not in _DT_BUILTINS for v in all_vars)


def compute_gap_tags(row: JobRow) -> list[str]:
    """Return the GAP_CATALOGUE tag names that apply to *row*."""
    tags: list[str] = []
    jt = (row.job_type or "CMD").upper()

    if row.condition and "^" in row.condition:
        tags.append("cross-instance-event")

    if jt in ("FTP", "DB", "I5"):
        tags.append("ftp-db-jobtype")

    if row.run_calendar or row.exclude_calendar or row.date_conditions:
        tags.append("complex-calendar")

    if row.max_run_alarm is not None or row.min_run_alarm is not None:
        tags.append("sla-management")

    if jt == "FILEWATCH":
        tags.append("file-watcher")

    if _business_globals_present(row.command):
        tags.append("global-variables")

    return tags
