"""Deterministic failure injection for migration simulation.

Uses JIL attributes (n_retrys, max_exit_success, fail_codes) to decide
whether a job should fail in a given simulation cycle. Uses a seeded RNG
so that repeated runs produce identical results for reproducibility.
"""
from __future__ import annotations

import random
from typing import Optional

from autosys.db.schema import JobRow


class FailureInjector:
    """
    Decides whether a job should fail during a simulated run.

    Heuristics (derived from JIL attributes):
    - n_retrys >= 3  → 25% failure rate (frequently retried = flaky)
    - n_retrys 1-2   → 10% failure rate
    - n_retrys 0     →  3% failure rate (baseline)
    - max_exit_success set → +5% failure rate (non-standard success codes)
    - fail_codes set → +5% failure rate (known failure modes)
    """

    def __init__(self, seed: int = 42) -> None:
        self._rng = random.Random(seed)

    def should_fail(self, row: JobRow) -> bool:
        """Return True if this job should fail in the current cycle."""
        n_retrys = row.n_retrys or 0
        if n_retrys >= 3:
            base_rate = 0.25
        elif n_retrys >= 1:
            base_rate = 0.10
        else:
            base_rate = 0.03

        if row.max_exit_success is not None:
            base_rate += 0.05
        if row.fail_codes:
            base_rate += 0.05

        return self._rng.random() < base_rate

    def estimated_run_secs(self, row: JobRow) -> float:
        """Estimate run duration in seconds from JIL attributes."""
        if row.avg_runtime and row.avg_runtime > 0:
            return float(row.avg_runtime * 60)
        if row.max_run_alarm and row.max_run_alarm > 0:
            return float(row.max_run_alarm * 30)
        return 60.0
