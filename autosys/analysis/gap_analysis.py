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

# tag -> (severity, description). Target is Airflow 3 (Astronomer Runtime 3).
GAP_CATALOGUE: dict[str, tuple[str, str]] = {
    "cross-instance-event": (
        "RED",
        "condition on a job in another AutoSys instance (job^INS). Airflow 3 "
        "can be triggered by events (Asset events posted through the REST "
        "API, or a message queue an AssetWatcher listens to), but the other "
        "scheduler must be changed to send them: cross-system architecture "
        "work.",
    ),
    "ftp-db-jobtype": (
        "RED",
        "FTP/DB/i5 job type — needs a Provider operator equivalent "
        "(SFTPOperator, SQLExecuteQueryOperator, etc.) and its connection.",
    ),
    "complex-calendar": (
        "YELLOW",
        "run_calendar/exclude_calendar — a named calendar needs a custom "
        "Timetable, or a cron schedule plus a check task that skips "
        "excluded dates.",
    ),
    "sla-management": (
        "YELLOW",
        "max_run_alarm/min_run_alarm — Airflow 3.0 removed the sla / "
        "sla_miss_callback parameters. Use Deadline Alerts (Airflow 3.1+) "
        "for max_run_alarm, or execution_timeout when the job should be "
        "stopped; min_run_alarm (finished too fast) has no equivalent and "
        "needs a duration check in on_success_callback.",
    ),
    "file-watcher": (
        "GREEN",
        "FILEWATCH — direct mapping to FileSensor/SFTPSensor (deferrable).",
    ),
    "global-variables": (
        "GREEN",
        "%%VAR%% business globals — direct equivalent in Airflow "
        "Variables/Connections.",
    ),
    # Condition constructs with no direct Airflow equivalent (audit SEM-15),
    # found from the parsed condition, not by text search.
    "look-back-condition": (
        "YELLOW",
        "s(job, hh.mm) look-back — Airflow has no 'succeeded within the last "
        "N hours'; needs a custom sensor or check task on the upstream's last "
        "run time.",
    ),
    "exit-code-condition": (
        "YELLOW",
        "e(job) / exitcode() condition — Airflow tasks only succeed or fail; "
        "branching on exit codes needs skip_on_exit_code, XCom and a "
        "branch task or trigger rules.",
    ),
    "global-value-condition": (
        "YELLOW",
        "v(GLOBAL) condition — Airflow cannot wait on a Variable's value; "
        "needs a sensor or short-circuit task, or an Asset event.",
    ),
    "notrunning-condition": (
        "YELLOW",
        "n(job) mutual exclusion — re-design with a pool of size 1 or "
        "max_active_tasks / max_active_runs.",
    ),
    "box-success-failure": (
        "YELLOW",
        "box_success/box_failure custom box verdict — needs trigger rules "
        "and a final task that sets the DAG run's outcome.",
    ),
    "terminator": (
        "YELLOW",
        "box_terminator/job_terminator — no direct equivalent; needs an "
        "on_failure_callback that fails the rest of the DAG run.",
    ),
}


def _condition_constructs(condition: Optional[str]) -> set[str]:
    """Gap tags for the constructs in a condition, from its parse tree.
    Unparseable conditions fall back to the predicate spelling."""
    from autosys.parser.condition_parser import (
        AndNode, ExitCodeCondNode, JobCondNode, NotNode, OrNode, ValueCondNode,
        parse_condition,
    )
    found: set[str] = set()
    if not condition or not condition.strip():
        return found
    try:
        stack = [parse_condition(condition)]
    except Exception:
        text = condition.lower()
        if "^" in text:
            found.add("cross-instance-event")
        if re.search(r"(?<![\w#.-])(e|exitcode)\s*\(", text):
            found.add("exit-code-condition")
        if re.search(r"(?<![\w#.-])(v|value)\s*\(", text):
            found.add("global-value-condition")
        if re.search(r"(?<![\w#.-])(n|notrunning)\s*\(", text):
            found.add("notrunning-condition")
        return found
    while stack:
        n = stack.pop()
        if isinstance(n, (AndNode, OrNode)):
            stack += [n.left, n.right]
        elif isinstance(n, NotNode):
            stack.append(n.operand)
        elif isinstance(n, JobCondNode):
            if n.instance:
                found.add("cross-instance-event")
            if n.look_back:
                found.add("look-back-condition")
            if n.func in ("n", "notrunning"):
                found.add("notrunning-condition")
        elif isinstance(n, ExitCodeCondNode):
            if "^" in n.job_name:
                found.add("cross-instance-event")
            found.add("exit-code-condition")
        elif isinstance(n, ValueCondNode):
            found.add("global-value-condition")
    return found


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

    cond_tags = _condition_constructs(row.condition)
    if "cross-instance-event" in cond_tags:
        tags.append("cross-instance-event")

    if jt in ("FTP", "DB", "I5"):
        tags.append("ftp-db-jobtype")

    # date_conditions alone only switches days_of_week/start_times on -- a
    # plain cron schedule, not a calendar.
    if row.run_calendar or row.exclude_calendar:
        tags.append("complex-calendar")

    if row.max_run_alarm is not None or row.min_run_alarm is not None:
        tags.append("sla-management")

    if jt == "FILEWATCH":
        tags.append("file-watcher")

    if _business_globals_present(row.command):
        tags.append("global-variables")

    for tag in ("look-back-condition", "exit-code-condition", "global-value-condition",
                "notrunning-condition"):
        if tag in cond_tags:
            tags.append(tag)
    if jt == "BOX" and (getattr(row, "box_success", None) or getattr(row, "box_failure", None)):
        tags.append("box-success-failure")
    if getattr(row, "box_terminator", None) or getattr(row, "job_terminator", None):
        tags.append("terminator")

    return tags
