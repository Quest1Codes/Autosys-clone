"""
One definition of "which local jobs does this condition depend on".

Every place that turns a condition into dependency edges -- chain depth,
blast radius, the XL chain rule (dependency_graph), cross-box detection
(migration_signals) and the WCC flow graph -- uses condition_job_refs, so the
numbers in one report can no longer contradict each other (audit SEM-01/02).

autorep writes conditions in the short form (``s(job)``, ``f(job)``,
``e(job) = 0``); the regexes this replaced only recognised ``success(job)``,
so a real export produced almost no dependency edges.

Resolution order:
  1. The real condition grammar (autosys.parser.condition_parser), walked
     iteratively so a very long condition cannot hit the recursion limit.
  2. When a condition does not parse -- names containing ``#``, relational
     exit-code operators the grammar lacks, job names that look like keywords
     -- a tolerant regex over every predicate spelling, so an unusual
     condition still contributes its edges instead of none.

Cross-instance references (``job^PRD``) are another scheduler's job and are
not local edges, so they are excluded. The simulator's runtime evaluation
(scheduler/condition_evaluator.referenced_job_names) is deliberately separate:
it must mirror exactly what the evaluator looks up.
"""
from __future__ import annotations

import re
from functools import lru_cache
from typing import Optional

from autosys.parser.condition_parser import (
    AndNode, ConditionSyntaxError, ExitCodeCondNode, JobCondNode, NotNode, OrNode,
    _CondTokenKind, _NAME_KINDS, _tokenize_condition, parse_condition,
)

# Every predicate spelling, long and short, followed by "(name". Used only
# when the strict grammar rejects a condition.
_FALLBACK_RE = re.compile(
    r"(?<![A-Za-z0-9_.#-])"
    r"(?:success|failure|terminated|done|notrunning|exitcode|s|f|t|d|n|e)"
    r"\s*\(\s*([^\s,()]+)",
    re.IGNORECASE,
)


@lru_cache(maxsize=262_144)
def condition_job_refs(condition: Optional[str]) -> frozenset[str]:
    """Local job names *condition* depends on (empty for None/blank)."""
    if not condition or not condition.strip():
        return frozenset()
    try:
        node = parse_condition(condition)
    except Exception:  # ConditionSyntaxError, or anything a malformed input trips
        names = _FALLBACK_RE.findall(condition)
    else:
        names = []
        stack = [node]
        while stack:
            n = stack.pop()
            if isinstance(n, (AndNode, OrNode)):
                stack.append(n.right)
                stack.append(n.left)
            elif isinstance(n, NotNode):
                stack.append(n.operand)
            elif isinstance(n, JobCondNode):
                if not n.instance:
                    names.append(n.job_name)
            elif isinstance(n, ExitCodeCondNode):
                names.append(n.job_name)
            # ValueCondNode references a global variable, not a job
    return frozenset(name for name in names if "^" not in name)


def rename_job_refs(condition: Optional[str], old: str, new: str) -> Optional[str]:
    """
    *condition* with every reference to job *old* renamed to *new* (audit ING-04).

    Only whole job-name arguments of a predicate are touched -- ``s(old)``,
    ``e(old) = 0``, ``s(old, 01.00)`` -- never a substring of another name
    (``old_b``), a global in ``v(...)``, or another instance's ``old^PRD``.
    A plain ``str.replace`` used to rewrite all of those.
    """
    if not condition or old not in condition:
        return condition
    spans: list[int] = []
    try:
        toks = _tokenize_condition(condition)
    except ConditionSyntaxError:
        toks = None
    if toks is not None:
        for i in range(len(toks) - 2):
            if (toks[i].kind in (_CondTokenKind.FUNC, _CondTokenKind.EXITCODE)
                    and toks[i + 1].kind == _CondTokenKind.LPAREN
                    and toks[i + 2].kind in _NAME_KINDS
                    and toks[i + 2].value == old):
                spans.append(toks[i + 2].pos)
    else:   # unparseable: rename a name that is a predicate's whole argument
        spans = [m.start(1) for m in re.finditer(
            r"\(\s*(" + re.escape(old) + r")(?=\s*[,)])", condition)]
    for pos in reversed(spans):
        condition = condition[:pos] + new + condition[pos + len(old):]
    return condition
