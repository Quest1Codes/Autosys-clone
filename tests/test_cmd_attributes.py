"""
Tests for the four CMD-job-type-only JIL attributes that were modelled
(Pydantic + DB schema) but never actually applied at dispatch time:

    envvars, ulimit, std_in_file, chk_files

Coverage
--------
- envvars      — merged into the subprocess environment.
- ulimit       — applied via the ``resource`` module (POSIX); smoke test
                 plus an ``ulimit -n`` assertion of the actual applied limit.
- std_in_file  — redirected as the subprocess's stdin.
- chk_files    — preflight disk-space check; job dispatches normally when
                 space is sufficient, and fails WITHOUT ever running its
                 command when it isn't.

These exercise the real dispatch pipeline (``AgentDispatch`` +
``EventProcessor``), following the same pattern as ``tests/test_phase6.py``.
"""

from __future__ import annotations

import os
import sys
import time

import pytest

from autosys.agent.dispatch import AgentDispatch, _check_chk_files, _parse_chk_files_size
from autosys.agent.runner import parse_envvars, parse_ulimit, build_ulimit_preexec_fn
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.repository import jobs as job_repo, events as event_repo, output as output_repo
from autosys.models.enums import JobStatus
from autosys.models.event import Event
from autosys.models.job import CmdJob
from autosys.scheduler.event_processor import EventProcessor


# ===========================================================================
# Helpers (mirrors tests/test_phase6.py)
# ===========================================================================

def _seed_cmd(name="job_a", command="echo hello", machine="localhost", **kw):
    job = CmdJob(job_name=name, job_type="CMD", command=command, machine=machine, **kw)
    with sync_session() as session:
        job_repo.upsert(session, job)


def _get_status(job_name) -> str:
    with sync_session() as session:
        row = job_repo.get_row(session, job_name)
        return row.status if row else None


def _wait_for_status(job_name, expected, timeout=15) -> bool:
    if isinstance(expected, str):
        expected = JobStatus[expected].value
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _get_status(job_name) == expected:
            return True
        time.sleep(0.1)
    return False


def _enqueue(event_type, job_name=None, **kwargs):
    ev = Event(event_type=event_type, job_name=job_name, source="internal", **kwargs)
    with sync_session() as session:
        event_repo.enqueue(session, ev)
    return ev.event_id


def _dispatch_and_tick(job_name):
    agent = AgentDispatch(local_only=True)
    _enqueue("STARTJOB", job_name)
    proc = EventProcessor(dispatch_fn=agent.dispatch, kill_fn=agent.kill, auto_complete=False)
    with sync_session() as session:
        proc.process_one_tick(session)
    return agent


def _output_lines(job_name):
    with sync_session() as session:
        return output_repo.get_lines_for_job(session, job_name)


# ===========================================================================
# DB fixture
# ===========================================================================

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_cmd_attrs.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("AUTOSYS_DB_URL", url)
    reset_engines()
    create_all_sync(drop_first=False)
    yield url
    reset_engines()


posix_only = pytest.mark.skipif(os.name != "posix", reason="ulimit/resource is POSIX-only")


# ===========================================================================
# envvars
# ===========================================================================

class TestEnvvars:

    def test_parse_envvars_basic(self):
        assert parse_envvars("A=1, B=2") == {"A": "1", "B": "2"}

    def test_parse_envvars_quoted_value(self):
        assert parse_envvars('GREETING="hello there"') == {"GREETING": "hello there"}

    def test_parse_envvars_empty(self):
        assert parse_envvars("") == {}
        assert parse_envvars(None) == {}

    def test_envvars_applied_to_subprocess(self):
        _seed_cmd(
            "envvars_job",
            command="echo MYVAL=$MY_TEST_VAR",
            envvars="MY_TEST_VAR=hello123",
        )
        _dispatch_and_tick("envvars_job")
        assert _wait_for_status("envvars_job", "SUCCESS"), _get_status("envvars_job")

        lines = _output_lines("envvars_job")
        assert any("MYVAL=hello123" in l.content for l in lines), lines

    def test_envvars_overrides_existing_var(self, monkeypatch):
        monkeypatch.setenv("MY_TEST_VAR", "original")
        _seed_cmd(
            "envvars_override_job",
            command="echo MYVAL=$MY_TEST_VAR",
            envvars="MY_TEST_VAR=overridden",
        )
        _dispatch_and_tick("envvars_override_job")
        assert _wait_for_status("envvars_override_job", "SUCCESS")

        lines = _output_lines("envvars_override_job")
        assert any("MYVAL=overridden" in l.content for l in lines), lines

    def test_no_envvars_still_inherits_parent_env(self, monkeypatch):
        monkeypatch.setenv("MY_INHERITED_VAR", "inherited_val")
        _seed_cmd("no_envvars_job", command="echo MYVAL=$MY_INHERITED_VAR")
        _dispatch_and_tick("no_envvars_job")
        assert _wait_for_status("no_envvars_job", "SUCCESS")

        lines = _output_lines("no_envvars_job")
        assert any("MYVAL=inherited_val" in l.content for l in lines), lines


# ===========================================================================
# ulimit
# ===========================================================================

class TestUlimit:

    def test_parse_ulimit_basic(self):
        assert parse_ulimit('n="256,512", t="60,120"') == {
            "n": ("256", "512"),
            "t": ("60", "120"),
        }

    def test_parse_ulimit_single_value_used_for_both(self):
        assert parse_ulimit('n="256"') == {"n": ("256", "256")}

    def test_parse_ulimit_unlimited(self):
        assert parse_ulimit('c="unlimited,unlimited"') == {"c": ("unlimited", "unlimited")}

    def test_parse_ulimit_empty(self):
        assert parse_ulimit("") == {}

    @posix_only
    def test_build_ulimit_preexec_fn_none_on_empty(self):
        assert build_ulimit_preexec_fn("") is None

    @posix_only
    def test_ulimit_job_runs_successfully(self):
        """Smoke test: a job with a ulimit spec still dispatches and succeeds."""
        _seed_cmd("ulimit_smoke_job", command="echo ok", ulimit='n="256,512"')
        _dispatch_and_tick("ulimit_smoke_job")
        assert _wait_for_status("ulimit_smoke_job", "SUCCESS"), _get_status("ulimit_smoke_job")

    @posix_only
    def test_ulimit_nofile_actually_applied(self):
        """Stronger check: the shell's own 'ulimit -n' reports the value we set."""
        _seed_cmd("ulimit_nofile_job", command="ulimit -n", ulimit='n="225,512"')
        _dispatch_and_tick("ulimit_nofile_job")
        assert _wait_for_status("ulimit_nofile_job", "SUCCESS"), _get_status("ulimit_nofile_job")

        lines = _output_lines("ulimit_nofile_job")
        contents = [l.content.strip() for l in lines]
        assert "225" in contents, contents


# ===========================================================================
# std_in_file
# ===========================================================================

class TestStdInFile:

    def test_std_in_file_redirected(self, tmp_path):
        stdin_path = tmp_path / "input.txt"
        stdin_path.write_text("hello from stdin\n")

        _seed_cmd("stdin_job", command="cat", std_in_file=str(stdin_path))
        _dispatch_and_tick("stdin_job")
        assert _wait_for_status("stdin_job", "SUCCESS"), _get_status("stdin_job")

        lines = _output_lines("stdin_job")
        assert any("hello from stdin" in l.content for l in lines), lines

    def test_missing_std_in_file_falls_back_without_crashing(self):
        _seed_cmd(
            "stdin_missing_job",
            command="echo survived",
            std_in_file="/no/such/path/does_not_exist.txt",
        )
        _dispatch_and_tick("stdin_missing_job")
        assert _wait_for_status("stdin_missing_job", "SUCCESS"), _get_status("stdin_missing_job")

        lines = _output_lines("stdin_missing_job")
        assert any("survived" in l.content for l in lines), lines


# ===========================================================================
# chk_files
# ===========================================================================

class TestChkFiles:

    def test_parse_chk_files_size_units(self):
        assert _parse_chk_files_size("100") == 100 * 1024        # default KB
        assert _parse_chk_files_size("1KB") == 1024
        assert _parse_chk_files_size("500M") == 500 * 1024 ** 2
        assert _parse_chk_files_size("2G") == 2 * 1024 ** 3

    def test_check_chk_files_passes_with_small_requirement(self, tmp_path):
        assert _check_chk_files(f"{tmp_path} 1KB") is None

    def test_check_chk_files_fails_with_absurd_requirement(self, tmp_path):
        error = _check_chk_files(f"{tmp_path} 999999999999999KB")
        assert error is not None

    def test_chk_files_sufficient_space_dispatches_normally(self, tmp_path):
        _seed_cmd(
            "chkfiles_ok_job",
            command="echo chk_ok",
            chk_files=f"{tmp_path} 1KB",
        )
        _dispatch_and_tick("chkfiles_ok_job")
        assert _wait_for_status("chkfiles_ok_job", "SUCCESS"), _get_status("chkfiles_ok_job")

        lines = _output_lines("chkfiles_ok_job")
        assert any("chk_ok" in l.content for l in lines), lines

    def test_chk_files_insufficient_space_fails_without_running(self, tmp_path):
        marker = tmp_path / "should_not_be_created.txt"
        _seed_cmd(
            "chkfiles_fail_job",
            command=f"touch {marker}",
            chk_files=f"{tmp_path} 999999999999999KB",
        )
        _dispatch_and_tick("chkfiles_fail_job")
        assert _wait_for_status("chkfiles_fail_job", "FAILURE"), _get_status("chkfiles_fail_job")

        # The command must never have actually run.
        assert not marker.exists()
        assert _output_lines("chkfiles_fail_job") == []
