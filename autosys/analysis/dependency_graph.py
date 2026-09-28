"""
Shared dependency-graph helpers — Phase 1 migration assessment.

Pure, DB-free graph analysis over `condition` expressions.  Extracted out of
`box_trace.py` so `complexity.py` (which must stay import-light: no DB
session, no scheduler/event-processor machinery) can reuse the same
dependency-depth logic that box_trace.py uses for its live FSM dry-run,
without pulling in the scheduler stack.

Two distinct signals live here:
  referenced_jobs / dependency_wave  Depth of a job within a *bounded* scope
                                      (e.g. one BOX's descendants) — how long
                                      is the sequential chain this job sits in.
  fan_in_counts                      Global reference count across the whole
                                      job estate — how many *other* jobs
                                      depend on this one ("blast radius").
"""

from __future__ import annotations

import re
from typing import Optional

from autosys.db.schema import JobRow

# Predicates recognised by autosys.scheduler.condition_evaluator — used here
# only to extract *which job names* a condition string references, not to
# evaluate it.
_CONDITION_REF_RE = re.compile(
    r"\b(?:success|failure|terminated|done|running|notrunning)\(\s*"
    r"([A-Za-z0-9_.:-]+)"
)


def referenced_jobs(condition: Optional[str], scope: set[str]) -> set[str]:
    """Return the in-scope job names referenced by a condition expression."""
    if not condition:
        return set()
    return {m for m in _CONDITION_REF_RE.findall(condition) if m in scope}


def dependency_wave(
    job_name: str,
    condition_by_name: dict[str, Optional[str]],
    scope: set[str],
    memo: dict[str, int],
    _visiting: Optional[set[str]] = None,
) -> int:
    """
    Depth of *job_name* in its dependency graph (1 = no in-scope deps).

    A job where success(a) -> success(b) -> success(c) chains 5 deep is
    harder to migrate as parallel Airflow tasks than one where all children
    are independent, even though a stub-dispatcher tick simulation may
    resolve both in the same tick (ticks alone can't distinguish them).
    """
    if job_name in memo:
        return memo[job_name]
    _visiting = _visiting or set()
    if job_name in _visiting:
        return 1  # dependency cycle guard — shouldn't happen, fail safe
    _visiting.add(job_name)

    deps = referenced_jobs(condition_by_name.get(job_name), scope) - {job_name}
    if not deps:
        memo[job_name] = 1
    else:
        memo[job_name] = 1 + max(
            dependency_wave(d, condition_by_name, scope, memo, _visiting)
            for d in deps
        )
    _visiting.discard(job_name)
    return memo[job_name]


def upstream_closure(
    seeds: set[str],
    condition_by_name: dict[str, Optional[str]],
    scope: set[str],
) -> set[str]:
    """
    Every in-scope node reachable *upstream* from *seeds* by following
    condition references (i.e. the union of the seeds' transitive
    dependencies).

    A seed is in the result only when it is itself a dependency of another
    seed — so for one linear chain a->b->c seeded on {c}, the result is
    {a, b} and c is excluded.

    Used by complexity.score_job to avoid charging the same dependency chain
    once per box: the chain's design cost lands on the node the chain ends
    at, and everything upstream of it is already covered by that decision.
    """
    out: set[str] = set()
    stack = [s for s in seeds if s in scope]
    while stack:
        node = stack.pop()
        for dep in referenced_jobs(condition_by_name.get(node), scope) - {node}:
            if dep not in out:
                out.add(dep)
                stack.append(dep)
    return out


def fan_in_counts(all_rows: dict[str, JobRow]) -> dict[str, int]:
    """
    Return {job_name: N} where N is the number of *other* jobs across the
    whole estate whose `condition` references job_name.

    Unlike dependency_wave, this is not scoped to a single BOX — a job
    referenced by jobs in other boxes (or top-level) is still a migration
    hub: breaking it has blast radius beyond its own dependency chain.
    """
    scope = set(all_rows)
    counts: dict[str, int] = {name: 0 for name in all_rows}
    for row in all_rows.values():
        for dep in referenced_jobs(row.condition, scope) - {row.job_name}:
            counts[dep] = counts.get(dep, 0) + 1
    return counts
