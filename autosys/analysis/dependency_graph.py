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

from typing import Optional

from autosys.analysis.condition_refs import condition_job_refs
from autosys.db.schema import JobRow

def referenced_jobs(condition: Optional[str], scope: set[str]) -> set[str]:
    """Return the in-scope job names referenced by a condition expression.

    Delegates to condition_refs.condition_job_refs, the single definition of
    a dependency (audit SEM-01: the regex this replaced only matched the long
    form ``success(job)``, but autorep writes ``s(job)``).
    """
    return set(condition_job_refs(condition) & scope)


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

    Iterative on purpose (task E1). The recursive form cost two stack frames
    per chain level — the call plus the generator inside ``max()`` — so it
    raised ``RecursionError`` on a chain deeper than ~450 against CPython's
    default limit of 1000. That is a crash, not a slowdown, and a real estate
    of 85,000 jobs can easily hold a chain that long. This version is bounded
    only by memory; it is verified against the recursive implementation in
    ``tests/test_dependency_wave.py``, including on cyclic graphs, because the
    numbers it produces feed the complexity score and must not move.

    *memo* is shared across calls by design — callers build one dict per
    report so a chain is costed once. *_visiting* tracks the nodes on the
    current path for the cycle guard; it is accepted (rather than always
    created here) so a caller mid-traversal can pass its own.
    """
    if job_name in memo:
        return memo[job_name]
    visiting = _visiting if _visiting is not None else set()
    if job_name in visiting:
        return 1  # dependency cycle guard — shouldn't happen, fail safe

    # Explicit-stack DFS. Each node is pushed twice: once to expand its
    # dependencies (expanded=False) and once to fold their results into its
    # own wave (expanded=True), which is what the recursive version got for
    # free from the call stack.
    stack: list[tuple[str, bool]] = [(job_name, False)]
    while stack:
        node, expanded = stack.pop()
        if node in memo:
            # Reached twice via two parents; the first visit already scored it.
            if expanded:
                visiting.discard(node)
            continue
        deps = referenced_jobs(condition_by_name.get(node), scope) - {node}
        if not expanded:
            visiting.add(node)
            stack.append((node, True))
            # reversed() so the LIFO stack pops dependencies in the same order
            # the recursive generator consumed them. Order is irrelevant to
            # max() on a DAG but decides, on a cyclic graph, which branch
            # memoises a node and which trips the cycle guard above — so it
            # has to match or the scores would move.
            pending = [d for d in deps if d not in memo and d not in visiting]
            for d in reversed(pending):
                stack.append((d, False))
            continue
        if not deps:
            memo[node] = 1
        else:
            # A dep missing from memo is one the cycle guard rejected; it
            # contributes 1, exactly as the recursive version's early return.
            memo[node] = 1 + max(memo.get(d, 1) for d in deps)
        visiting.discard(node)
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
