"""
Suite-wide test setup.

Many tests exercise real subprocess dispatch on purpose (LocalJobRunner,
AgentDispatch, agent serve, FTP/WOL runners against local fakes), so the suite
opts in to real execution by default. tests/test_execution_gate.py removes the
opt-in and proves every execution path refuses without it.
"""
from __future__ import annotations

import pytest

from autosys.safety import REAL_EXECUTION_ENV_VAR


@pytest.fixture(autouse=True)
def _allow_real_execution_in_tests(monkeypatch):
    monkeypatch.setenv(REAL_EXECUTION_ENV_VAR, "true")
