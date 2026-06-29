"""
Condition evaluator — the dependency engine of AutoSys.

This module is the bridge between the JIL condition language and the
Scheduler ACE's job activation logic.  It answers one question:

    "Given the current status of all jobs, is this job's condition satisfied?"

If yes, the Event Processor transitions the job to STARTING.
If no, the event is dropped and the job stays in its current state until a
future status-change event re-triggers the check.

Condition language recap
------------------------
Conditions are JIL expressions evaluated by the recursive-descent parser in
``autosys/parser/condition_parser.py``.  This module is a thin wrapper:

    condition_str = "success(extract_sales) & success(generate_report)"
    statuses = {"extract_sales": "SUCCESS", "generate_report": "FAILURE"}
    is_satisfied(condition_str, statuses)  →  False

Built-in status predicates:
    success(job)    →  job.status == SUCCESS
    failure(job)    →  job.status == FAILURE
    terminated(job) →  job.status == TERMINATED
    done(job)       →  job.status in {SUCCESS, FAILURE, TERMINATED}
    running(job)    →  job.status == RUNNING
    notrunning(job) →  job.status not in {STARTING, RUNNING}

Boolean operators:
    &   AND  (higher precedence — binds tighter than |)
    |   OR

Examples
--------
    "success(a)"                                    →  a finished OK
    "success(a) & success(b)"                       →  both finished OK
    "success(a) | success(b)"                       →  at least one OK
    "success(a) & (success(b) | failure(b))"        →  a OK and b is done
    "done(extract_sales)"                           →  extract finished (OK or failed)
    "success(box) & value(RUN_DATE) = \"20260625\"" →  box OK and global var set
"""

from __future__ import annotations

from typing import Optional

from loguru import logger

from autosys.parser.condition_parser import (
    parse_condition,
    evaluate,
    ConditionSyntaxError as ConditionParseError,
)


def is_satisfied(
    condition_str: Optional[str],
    job_statuses: dict[str, str],
) -> bool:
    """
    Return True if *condition_str* is satisfied given the current *job_statuses*.

    Parameters
    ----------
    condition_str:
        The raw JIL condition string from the job definition, e.g.
        ``"success(extract_sales) & success(generate_report)"``.
        ``None`` or empty string means "no condition" → always satisfied.
    job_statuses:
        A mapping of ``{job_name: status_string}`` for every job currently
        in the system.  The evaluator looks up job names from this dict.

    Returns
    -------
    bool
        True  → condition is met, the job can be activated.
        False → condition is not met, the job must keep waiting.

    Notes
    -----
    Undefined job names in the condition expression evaluate to False for
    all predicates (a job that doesn't exist is never SUCCESS, FAILURE, etc.).
    This mirrors real AutoSys behaviour — a typo in a condition expression
    will silently prevent the job from ever starting.
    """
    if not condition_str:
        return True

    try:
        node = parse_condition(condition_str)
        result = evaluate(node, job_statuses)
        logger.debug(
            "Condition %r → %s  (given %d job statuses)",
            condition_str, result, len(job_statuses),
        )
        return result
    except ConditionParseError as exc:
        # Malformed condition → treat as unsatisfied and warn.
        # Real AutoSys raises an alarm in this case.
        logger.warning(
            "Condition parse error for %r: %s — treating as unsatisfied",
            condition_str, exc,
        )
        return False
    except Exception as exc:
        logger.error(
            "Unexpected error evaluating condition %r: %s",
            condition_str, exc,
        )
        return False


def build_status_snapshot(session) -> dict[str, str]:
    """
    Build a {job_name: status} snapshot of every job in the DB.

    The Event Processor calls this once per tick so that all condition
    evaluations within a single tick see a consistent snapshot.  This
    matches how the real AutoSys EPS batches events: it reads the world
    state at the START of a tick, processes all queued events against that
    snapshot, then writes back the resulting state changes in one pass.

    Parameters
    ----------
    session:
        An open SQLAlchemy Session.

    Returns
    -------
    dict[str, str]
        Maps ``job_name → status`` for every row in the ``jobs`` table.
    """
    from autosys.db.repository import jobs as job_repo
    rows = job_repo.list_all(session)
    return {row.job_name: (row.status or "INACTIVE") for row in rows}
