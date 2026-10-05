"""
dependency_wave is iterative (task E1). These tests exist to pin two things:

  1. It no longer raises RecursionError on a deep chain. The recursive form
     cost two stack frames per level (the call, plus the generator inside
     ``max()``) and died somewhere between depth 400 and 500 against
     CPython's default limit of 1000.

  2. It produces *exactly* the numbers the recursive form produced. Those
     numbers feed complexity.score_job's dependency-chain-depth factor and
     the report's wave findings, so a behaviour change here silently moves
     every estate's complexity score. The reference implementation below is
     the pre-E1 recursive version, kept verbatim, and the differential test
     fuzzes both over random graphs -- cyclic ones included, because the
     cycle guard's result depends on traversal order.
"""
from __future__ import annotations

import random
from typing import Optional

import pytest

from autosys.analysis.dependency_graph import dependency_wave, referenced_jobs


# ---------------------------------------------------------------------------
# Reference implementation — the recursive pre-E1 version, verbatim.
# Do not "fix" or tidy this. Its whole job is to be the old behaviour.
# ---------------------------------------------------------------------------

def _recursive_wave(
    job_name: str,
    condition_by_name: dict[str, Optional[str]],
    scope: set[str],
    memo: dict[str, int],
    _visiting: Optional[set[str]] = None,
) -> int:
    if job_name in memo:
        return memo[job_name]
    _visiting = _visiting or set()
    if job_name in _visiting:
        return 1
    _visiting.add(job_name)
    deps = referenced_jobs(condition_by_name.get(job_name), scope) - {job_name}
    if not deps:
        memo[job_name] = 1
    else:
        memo[job_name] = 1 + max(
            _recursive_wave(d, condition_by_name, scope, memo, _visiting)
            for d in deps
        )
    _visiting.discard(job_name)
    return memo[job_name]


def _chain(n: int) -> tuple[dict[str, Optional[str]], set[str]]:
    """j000000 <- j000001 <- ... a single linear chain n deep."""
    cond = {
        f"j{i:06d}": (f"success(j{i - 1:06d})" if i else None)
        for i in range(n)
    }
    return cond, set(cond)


def _random_graph(n, max_deps, allow_cycles, rng):
    names = [f"j{i:04d}" for i in range(n)]
    cond: dict[str, Optional[str]] = {}
    for i, nm in enumerate(names):
        # Referencing only earlier names keeps it acyclic; drawing from the
        # whole list lets forward references -- i.e. cycles -- through.
        pool = names if allow_cycles else names[:i]
        k = min(rng.randint(0, max_deps), len(pool))
        picks = rng.sample(pool, k) if pool else []
        cond[nm] = " and ".join(f"success({p})" for p in picks) if picks else None
    return cond, set(names)


# ---------------------------------------------------------------------------
# 1. The crash is gone
# ---------------------------------------------------------------------------

class TestDeepChains:
    def test_recursive_reference_still_blows_up(self):
        """Guards the premise: if this ever stops raising, the test below is
        no longer proving anything and the reference can be retired."""
        cond, scope = _chain(2000)
        with pytest.raises(RecursionError):
            _recursive_wave("j001999", cond, scope, {})

    @pytest.mark.parametrize("depth", [500, 2_000, 50_000])
    def test_deep_chain_does_not_recurse(self, depth):
        cond, scope = _chain(depth)
        assert dependency_wave(f"j{depth - 1:06d}", cond, scope, {}) == depth

    def test_depth_just_below_old_limit_unchanged(self):
        """400 worked before E1 and must still give the same answer."""
        cond, scope = _chain(400)
        assert dependency_wave("j000399", cond, scope, {}) == 400
        assert _recursive_wave("j000399", cond, scope, {}) == 400


# ---------------------------------------------------------------------------
# 2. The numbers did not move
# ---------------------------------------------------------------------------

class TestMatchesRecursiveBehaviour:
    @pytest.mark.parametrize("allow_cycles", [False, True])
    def test_differential_over_random_graphs(self, allow_cycles):
        rng = random.Random(20261004)
        evaluated = 0
        for _ in range(300):
            n = rng.randint(1, 25)
            cond, scope = _random_graph(n, rng.randint(0, 4), allow_cycles, rng)

            # Evaluation order matters: memo is shared and the cycle guard is
            # path-sensitive, so score every node in a shuffled order with one
            # shared memo per implementation, exactly as a report would.
            order = sorted(scope)
            rng.shuffle(order)
            memo_new: dict[str, int] = {}
            memo_ref: dict[str, int] = {}
            for name in order:
                got = dependency_wave(name, cond, scope, memo_new)
                want = _recursive_wave(name, cond, scope, memo_ref)
                assert got == want, (
                    f"wave for {name} changed: {want} -> {got}\n"
                    f"conditions: { {k: v for k, v in cond.items() if v} }"
                )
                evaluated += 1

            # The shared memo must also end up identical -- callers read it.
            assert memo_new == memo_ref

        assert evaluated > 1000, "fuzz did not actually exercise much"


class TestKnownShapes:
    def test_no_dependencies_is_wave_1(self):
        cond = {"a": None, "b": ""}
        assert dependency_wave("a", cond, {"a", "b"}, {}) == 1
        assert dependency_wave("b", cond, {"a", "b"}, {}) == 1

    def test_out_of_scope_dependency_is_ignored(self):
        cond = {"b": "success(a)"}
        # 'a' is not in scope, so b has no in-scope deps.
        assert dependency_wave("b", cond, {"b"}, {}) == 1

    def test_self_reference_is_ignored(self):
        cond = {"a": "success(a)"}
        assert dependency_wave("a", cond, {"a"}, {}) == 1

    def test_diamond_takes_the_longest_path(self):
        #   a -> b -> d
        #   a -> c -------> d   (c also depends on b, making the left leg 4)
        cond = {
            "a": None,
            "b": "success(a)",
            "c": "success(b)",
            "d": "success(b) and success(c)",
        }
        scope = set(cond)
        assert dependency_wave("d", cond, scope, {}) == 4

    def test_two_node_cycle_does_not_hang(self):
        """a <-> b. Terminates, and gives what the recursive form gave: 3.

        Not an obvious number, so here is the derivation. Entering at 'a':
        a is marked on-path, its dep b is entered, b's dep a is already
        on-path so the cycle guard returns 1 without memoising, b scores
        1 + 1 = 2, and a scores 1 + 2 = 3. The value therefore depends on
        which node you enter from, which is why this is pinned rather than
        reasoned about at the call site -- and why cyclic JIL is worth
        reporting to the client rather than silently scoring.
        """
        cond = {"a": "success(b)", "b": "success(a)"}
        scope = set(cond)
        assert dependency_wave("a", cond, scope, {}) == 3
        assert _recursive_wave("a", cond, scope, {}) == 3
        assert dependency_wave("b", cond, scope, {}) == 3

    def test_memo_is_reused_across_calls(self):
        cond, scope = _chain(50)
        memo: dict[str, int] = {}
        dependency_wave("j000049", cond, scope, memo)
        # One traversal should have scored the whole chain, not just the tail.
        assert len(memo) == 50
        assert memo["j000000"] == 1
        assert memo["j000049"] == 50
