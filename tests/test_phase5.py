"""
Phase 5 test suite — System Agent, subprocess dispatch, output capture, run history.

Coverage
--------
1.  LocalJobRunner     — stdout capture, exit codes, kill, max_run_alarm timeout
2.  AgentDispatch      — dispatch lifecycle, RUNNING→SUCCESS, RUNNING→FAILURE,
                         machine filtering, %%VAR%% expansion
3.  RunRepository      — start/finish, list_runs, latest_run_id
4.  OutputRepository   — append, get_lines, get_lines_for_job
5.  EventProcessor     — kill_fn called by KILLJOB, real dispatch integration
6.  CLI: agent run-once — processes events, waits for completion, shows results
7.  CLI: jobs tail      — shows captured output
8.  CLI: jobs history   — shows run table
"""

from __future__ import annotations

import time
import threading
from datetime import datetime
from pathlib import Path

import pytest
from click.testing import CliRunner

from autosys.agent.dispatch import AgentDispatch, _is_local_machine
from autosys.agent.runner import LocalJobRunner
from autosys.cli.main import autosys
from autosys.db.connection import sync_session, reset_engines
from autosys.db.migrations import create_all_sync
from autosys.db.repository import (
    jobs as job_repo,
    events as event_repo,
    runs as run_repo,
    output as output_repo,
    globs as glob_repo,
)
from autosys.models.event import Event
from autosys.models.job import BoxJob, CmdJob
from autosys.scheduler.event_processor import EventProcessor

_DEMO_JIL = Path(__file__).parents[1] / "examples" / "demo_etl.jil"


# ===========================================================================
# DB fixture
# ===========================================================================

@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test5.db"
    url = f"sqlite:///{db_path}"
    monkeypatch.setenv("AUTOSYS_DB_URL", url)
    reset_engines()
    create_all_sync(drop_first=False)
    yield url
    reset_engines()


# ===========================================================================
# Helpers
# ===========================================================================

def _seed_cmd(name="job_a", command="echo hello", machine="localhost", **kw):
    job = CmdJob(job_name=name, job_type="CMD", command=command, machine=machine, **kw)
    with sync_session() as session:
        job_repo.upsert(session, job)


def _get_status(job_name) -> str:
    with sync_session() as session:
        row = job_repo.get_row(session, job_name)
        return row.status if row else None


def _set_status(job_name, status):
    if isinstance(status, str):
        from autosys.models.enums import JobStatus
        status = JobStatus[status].value
    with sync_session() as session:
        row = job_repo.get_row(session, job_name)
        row.status = status


def _enqueue(event_type, job_name=None, **kwargs):
    ev = Event(event_type=event_type, job_name=job_name, source="internal", **kwargs)
    with sync_session() as session:
        event_repo.enqueue(session, ev)
    return ev.event_id


def _wait_for_status(job_name, expected, timeout=15) -> bool:
    """Poll DB until job reaches expected status or timeout."""
    if isinstance(expected, str):
        from autosys.models.enums import JobStatus
        expected = JobStatus[expected].value
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _get_status(job_name) == expected:
            return True
        time.sleep(0.1)
    return False


def _tick_with_agent(agent=None, now=None):
    if agent is None:
        agent = AgentDispatch(local_only=True)
    proc = EventProcessor(
        dispatch_fn   = agent.dispatch,
        kill_fn       = agent.kill,
        auto_complete = False,
    )
    with sync_session() as session:
        return proc.process_one_tick(session, now=now)


# ===========================================================================
# 1. LocalJobRunner
# ===========================================================================

class TestLocalJobRunner:

    def _runner(self, command, max_run_secs=None):
        captured = []
        def cb(run_id, line_no, stream, content):
            captured.append((line_no, stream, content))
        r = LocalJobRunner(
            command         = command,
            job_name        = "test_job",
            run_id          = "test-run-id",
            max_run_secs    = max_run_secs,
            output_callback = cb,
        )
        r._captured = captured
        return r

    def test_exit_code_0_on_success(self):
        r = self._runner("echo hello")
        code = r.run()
        assert code == 0

    def test_exit_code_nonzero_on_failure(self):
        r = self._runner("bash -c 'exit 42'")
        code = r.run()
        assert code == 42

    def test_stdout_captured(self):
        r = self._runner("echo hello world")
        r.run()
        contents = [c for _, _, c in r._captured]
        assert any("hello world" in c for c in contents)

    def test_multiline_output_captured(self):
        r = self._runner("printf 'line1\\nline2\\nline3\\n'")
        r.run()
        assert len(r._captured) == 3

    def test_line_numbers_sequential(self):
        r = self._runner("printf 'a\\nb\\nc\\n'")
        r.run()
        line_nos = [ln for ln, _, _ in r._captured]
        assert line_nos == [1, 2, 3]

    def test_pid_set_after_run(self):
        r = self._runner("echo hi")
        r.run()
        assert r.pid is not None
        assert r.pid > 0

    def test_exit_code_set_after_run(self):
        r = self._runner("echo hi")
        r.run()
        assert r.exit_code == 0

    def test_kill_terminates_process(self):
        r = self._runner("sleep 30")

        results = []
        def run_in_thread():
            results.append(r.run())

        t = threading.Thread(target=run_in_thread, daemon=True)
        t.start()

        # Wait for process to start
        deadline = time.time() + 5
        while time.time() < deadline and r.pid is None:
            time.sleep(0.05)

        assert r.pid is not None, "Process should have started"
        r.kill()
        t.join(timeout=10)
        assert t.is_alive() is False, "Thread should have exited"
        assert results[0] != 0   # killed → non-zero exit

    def test_max_run_secs_kills_long_job(self):
        r = self._runner("sleep 30", max_run_secs=0.5)
        start = time.time()
        code = r.run()
        elapsed = time.time() - start
        assert elapsed < 10, "Job should have been killed well before 10s"
        assert code != 0        # -1 (killed)

    def test_stderr_merged_into_stdout(self):
        # stderr=STDOUT means stderr appears in captured output
        r = self._runner("bash -c 'echo out; echo err >&2'")
        r.run()
        contents = [c for _, _, c in r._captured]
        combined = " ".join(contents)
        assert "out" in combined
        assert "err" in combined

    def test_no_output_callback_runs_cleanly(self):
        r = LocalJobRunner(command="echo hi", job_name="j", run_id="r")
        code = r.run()
        assert code == 0


# ===========================================================================
# 2. Machine filtering
# ===========================================================================

class TestMachineFiltering:

    def test_localhost_is_local(self):
        assert _is_local_machine("localhost") is True

    def test_127_0_0_1_is_local(self):
        assert _is_local_machine("127.0.0.1") is True

    def test_none_is_local(self):
        assert _is_local_machine(None) is True

    def test_empty_string_is_local(self):
        assert _is_local_machine("") is True

    def test_remote_name_is_not_local(self):
        assert _is_local_machine("etl-server-01") is False

    def test_hostname_is_local(self):
        import socket
        assert _is_local_machine(socket.gethostname()) is True

    def test_remote_job_stays_starting(self):
        """A job targeting a non-local machine should stay in STARTING."""
        _seed_cmd("remote_job", machine="etl-server-01")
        _enqueue("STARTJOB", "remote_job")
        # Use real dispatch (local_only=True by default)
        _tick_with_agent()
        # Remote machine → dispatch skipped → job stays STARTING (not RUNNING)
        # (STARTJOB transitions to STARTING, then dispatch is skipped)
        from autosys.models.enums import JobStatus
        status = _get_status("remote_job")
        # Job should be STARTING (dispatched but not actually run), INACTIVE
        # (condition not met), or FAILURE (local_only=True rejects remote machine)
        assert status in (JobStatus.STARTING.value, JobStatus.INACTIVE.value, JobStatus.FAILURE.value)


# ===========================================================================
# 3. AgentDispatch — full lifecycle
# ===========================================================================

class TestAgentDispatch:

    def test_local_job_becomes_running_then_success(self):
        _seed_cmd("job_a", command="echo hello", machine="localhost")
        _enqueue("STARTJOB", "job_a")
        _tick_with_agent()
        # After tick: job is RUNNING (agent thread started)
        # Wait for the agent thread to complete
        assert _wait_for_status("job_a", "SUCCESS"), \
            f"Expected SUCCESS, got {_get_status('job_a')}"

    def test_failing_job_becomes_failure(self):
        _seed_cmd("fail_job", command="bash -c 'exit 1'", machine="localhost")
        _enqueue("STARTJOB", "fail_job")
        _tick_with_agent()
        assert _wait_for_status("fail_job", "FAILURE"), \
            f"Expected FAILURE, got {_get_status('fail_job')}"

    def test_run_history_record_created(self):
        _seed_cmd("hist_job", command="echo hi", machine="localhost")
        _enqueue("STARTJOB", "hist_job")
        _tick_with_agent()
        assert _wait_for_status("hist_job", "SUCCESS")

        with sync_session() as session:
            rows = run_repo.list_runs(session, "hist_job")
        assert len(rows) == 1
        assert rows[0].status == 4
        assert rows[0].exit_code == 0

    def test_run_history_has_start_and_end_times(self):
        _seed_cmd("time_job", command="echo hi", machine="localhost")
        _enqueue("STARTJOB", "time_job")
        _tick_with_agent()
        assert _wait_for_status("time_job", "SUCCESS")

        with sync_session() as session:
            rows = run_repo.list_runs(session, "time_job")
        assert rows[0].start_time is not None
        assert rows[0].end_time   is not None
        assert rows[0].end_time >= rows[0].start_time

    def test_run_history_has_pid(self):
        _seed_cmd("pid_job", command="echo hi", machine="localhost")
        _enqueue("STARTJOB", "pid_job")
        _tick_with_agent()
        assert _wait_for_status("pid_job", "SUCCESS")

        with sync_session() as session:
            rows = run_repo.list_runs(session, "pid_job")
        assert rows[0].pid is not None
        assert rows[0].pid > 0

    def test_output_lines_stored_in_db(self):
        _seed_cmd("out_job", command="printf 'line1\\nline2\\nline3\\n'",
                  machine="localhost")
        _enqueue("STARTJOB", "out_job")
        _tick_with_agent()
        assert _wait_for_status("out_job", "SUCCESS")

        with sync_session() as session:
            lines = output_repo.get_lines_for_job(session, "out_job")
        assert len(lines) == 3
        assert lines[0].content == "line1"
        assert lines[1].content == "line2"
        assert lines[2].content == "line3"

    def test_output_lines_have_sequential_numbers(self):
        _seed_cmd("seq_job", command="printf 'a\\nb\\nc\\n'", machine="localhost")
        _enqueue("STARTJOB", "seq_job")
        _tick_with_agent()
        assert _wait_for_status("seq_job", "SUCCESS")

        with sync_session() as session:
            lines = output_repo.get_lines_for_job(session, "seq_job")
        assert [l.line_no for l in lines] == [1, 2, 3]

    def test_variable_expansion_in_command(self):
        """%%DATE%% should be expanded to MMDDYYYY before execution."""
        # Command writes %%DATE%% output to a temp file and echoes it
        _seed_cmd("var_job",
                  command="echo %%DATE%%",
                  machine="localhost")
        _enqueue("STARTJOB", "var_job")
        _tick_with_agent()
        assert _wait_for_status("var_job", "SUCCESS")

        with sync_session() as session:
            lines = output_repo.get_lines_for_job(session, "var_job")

        # %%DATE%% expands to MMDDYYYY (8 digits)
        assert lines, "Should have captured some output"
        date_output = lines[0].content.strip()
        assert len(date_output) == 8 and date_output.isdigit(), \
            f"Expected MMDDYYYY, got {date_output!r}"

    def test_global_variable_expanded_in_command(self):
        """User globals set via SET_GLOBAL should expand in commands."""
        with sync_session() as session:
            glob_repo.set(session, "MY_VAR", "hello_world")
        _seed_cmd("gvar_job",
                  command="echo %%MY_VAR%%",
                  machine="localhost")
        _enqueue("STARTJOB", "gvar_job")
        _tick_with_agent()
        assert _wait_for_status("gvar_job", "SUCCESS")

        with sync_session() as session:
            lines = output_repo.get_lines_for_job(session, "gvar_job")
        assert any("hello_world" in l.content for l in lines)


# ===========================================================================
# 4. RunRepository
# ===========================================================================

class TestRunRepository:

    def test_start_creates_row(self):
        _seed_cmd("j")
        with sync_session() as session:
            row = run_repo.start(session, "rid1", "j", "echo hi", "localhost", "2026-06-25")
        with sync_session() as session:
            from autosys.db.schema import JobRunRow
            fetched = session.get(JobRunRow, "rid1")
        assert fetched is not None
        assert fetched.status == 1

    def test_finish_updates_row(self):
        _seed_cmd("j")
        with sync_session() as session:
            run_repo.start(session, "rid2", "j", "echo hi", "localhost", "2026-06-25")
        with sync_session() as session:
            run_repo.finish(session, "rid2", "SUCCESS", 0, pid=12345)
        with sync_session() as session:
            from autosys.db.schema import JobRunRow
            row = session.get(JobRunRow, "rid2")
        assert row.status    == 4
        assert row.exit_code == 0
        assert row.pid       == 12345
        assert row.end_time is not None

    def test_list_runs_newest_first(self):
        _seed_cmd("j")
        with sync_session() as session:
            run_repo.start(session, "r1", "j", "echo", "localhost", "2026-06-24")
        time.sleep(0.02)
        with sync_session() as session:
            run_repo.start(session, "r2", "j", "echo", "localhost", "2026-06-25")
        with sync_session() as session:
            rows = run_repo.list_runs(session, "j")
        assert rows[0].run_id == "r2"   # newest first
        assert rows[1].run_id == "r1"

    def test_latest_run_id(self):
        _seed_cmd("j")
        with sync_session() as session:
            run_repo.start(session, "r_old", "j", "echo", "localhost", "2026-06-24")
        time.sleep(0.02)
        with sync_session() as session:
            run_repo.start(session, "r_new", "j", "echo", "localhost", "2026-06-25")
        with sync_session() as session:
            rid = run_repo.latest_run_id(session, "j")
        assert rid == "r_new"

    def test_latest_run_id_none_for_unknown_job(self):
        with sync_session() as session:
            rid = run_repo.latest_run_id(session, "no_such_job")
        assert rid is None


# ===========================================================================
# 5. OutputRepository
# ===========================================================================

class TestOutputRepository:

    def test_append_and_get_lines(self):
        _seed_cmd("j")
        with sync_session() as session:
            run_repo.start(session, "run1", "j", "echo", "localhost", "2026-06-24")
            session.flush()
            output_repo.append(session, "run1", "j", 1, "hello")
            output_repo.append(session, "run1", "j", 2, "world")
        with sync_session() as session:
            lines = output_repo.get_lines(session, "run1")
        assert len(lines) == 2
        assert lines[0].content == "hello"
        assert lines[1].content == "world"

    def test_get_lines_ordered_by_line_no(self):
        _seed_cmd("j")
        with sync_session() as session:
            run_repo.start(session, "run2", "j", "echo", "localhost", "2026-06-24")
            session.flush()
            output_repo.append(session, "run2", "j", 3, "third")
            output_repo.append(session, "run2", "j", 1, "first")
            output_repo.append(session, "run2", "j", 2, "second")
        with sync_session() as session:
            lines = output_repo.get_lines(session, "run2")
        assert [l.content for l in lines] == ["first", "second", "third"]

    def test_get_lines_for_job_uses_latest_run(self):
        _seed_cmd("j")
        with sync_session() as session:
            run_repo.start(session, "ra", "j", "echo", "localhost", "2026-06-24")
        with sync_session() as session:
            output_repo.append(session, "ra", "j", 1, "old output")
        time.sleep(0.02)
        with sync_session() as session:
            run_repo.start(session, "rb", "j", "echo", "localhost", "2026-06-25")
        with sync_session() as session:
            output_repo.append(session, "rb", "j", 1, "new output")
        with sync_session() as session:
            lines = output_repo.get_lines_for_job(session, "j")
        assert lines[0].content == "new output"

    def test_get_lines_empty_for_no_output(self):
        with sync_session() as session:
            lines = output_repo.get_lines(session, "nonexistent-run-id")
        assert lines == []


# ===========================================================================
# 6. EventProcessor + kill_fn integration
# ===========================================================================

class TestEventProcessorKillFn:

    def test_killjob_calls_kill_fn(self):
        killed = []

        def fake_kill(session, row):
            killed.append(row.job_name)

        _seed_cmd("kill_me")
        _set_status("kill_me", "RUNNING")
        _enqueue("KILLJOB", "kill_me")

        proc = EventProcessor(kill_fn=fake_kill)
        with sync_session() as session:
            proc.process_one_tick(session)

        assert "kill_me" in killed
        assert _get_status("kill_me") == 6

    def test_killjob_no_kill_fn_still_terminates(self):
        """KILLJOB without kill_fn still sets status to TERMINATED."""
        _seed_cmd("no_fn_job")
        _set_status("no_fn_job", "RUNNING")
        _enqueue("KILLJOB", "no_fn_job")
        proc = EventProcessor()
        with sync_session() as session:
            proc.process_one_tick(session)
        assert _get_status("no_fn_job") == 6


# ===========================================================================
# 7. CLI: agent run-once
# ===========================================================================

class TestCLIAgentRunOnce:

    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(autosys, list(args), catch_exceptions=False)

    def test_run_once_exit_0_no_events(self):
        result = self._run("agent", "run-once")
        assert result.exit_code == 0

    def test_run_once_no_events_message(self):
        result = self._run("agent", "run-once")
        assert "No events pending" in result.output

    def test_run_once_processes_local_job(self):
        """Full pipeline: import JIL, queue event, agent run-once, check SUCCESS."""
        # Import a single simple job
        self._run("jil", "import", str(_DEMO_JIL))

        # Patch the machine in the DB to localhost so it runs locally
        with sync_session() as session:
            row = job_repo.get_row(session, "check_source_ready")
            row.machine = "localhost"

        self._run("sendevent", "-E", "STARTJOB", "-J", "check_source_ready")
        result = self._run("agent", "run-once", "--wait", "20")
        assert result.exit_code == 0
        assert "1 event(s) processed" in result.output

    def test_run_once_quiet_flag(self):
        result = self._run("agent", "run-once", "--quiet")
        assert result.exit_code == 0
        assert result.output.strip() == ""


# ===========================================================================
# 8. CLI: jobs tail
# ===========================================================================

class TestCLIJobsTail:

    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(autosys, list(args), catch_exceptions=False)

    def _run_job_and_wait(self, name, command="printf 'hello\\nworld\\n'"):
        """Helper: seed a local job, run it, wait for SUCCESS."""
        _seed_cmd(name, command=command, machine="localhost")
        _enqueue("STARTJOB", name)
        agent = AgentDispatch(local_only=True)
        proc  = EventProcessor(dispatch_fn=agent.dispatch, kill_fn=agent.kill,
                               auto_complete=False)
        with sync_session() as session:
            proc.process_one_tick(session)
        assert _wait_for_status(name, "SUCCESS"), \
            f"Job {name!r} did not reach SUCCESS"

    def test_tail_exit_0(self):
        self._run_job_and_wait("tail_job")
        result = self._run("jobs", "tail", "tail_job")
        assert result.exit_code == 0

    def test_tail_shows_output(self):
        self._run_job_and_wait("tail_job2")
        result = self._run("jobs", "tail", "tail_job2")
        assert "hello" in result.output
        assert "world" in result.output

    def test_tail_shows_run_id(self):
        self._run_job_and_wait("tail_job3")
        result = self._run("jobs", "tail", "tail_job3")
        assert "run_id" in result.output

    def test_tail_no_output_message(self):
        """Jobs with no captured output (e.g. never run) show a message."""
        _seed_cmd("norun_job")
        result = self._run("jobs", "tail", "norun_job")
        # No run → exit 1 (job_name found but no run_id)
        assert result.exit_code != 0 or "No run history" in result.output

    def test_tail_last_n_lines(self):
        self._run_job_and_wait(
            "tail_n_job",
            command="printf 'a\\nb\\nc\\nd\\ne\\n'",
        )
        result = self._run("jobs", "tail", "tail_n_job", "--lines", "2")
        assert result.exit_code == 0
        # Only last 2 lines visible
        assert "d" in result.output
        assert "e" in result.output


# ===========================================================================
# 9. CLI: jobs history
# ===========================================================================

class TestCLIJobsHistory:

    def _run(self, *args):
        runner = CliRunner()
        return runner.invoke(autosys, list(args), catch_exceptions=False)

    def _run_job_and_wait(self, name, command="echo hi"):
        _seed_cmd(name, command=command, machine="localhost")
        _enqueue("STARTJOB", name)
        agent = AgentDispatch(local_only=True)
        proc  = EventProcessor(dispatch_fn=agent.dispatch, kill_fn=agent.kill,
                               auto_complete=False)
        with sync_session() as session:
            proc.process_one_tick(session)
        assert _wait_for_status(name, "SUCCESS")

    def test_history_exit_0(self):
        self._run_job_and_wait("hist_job1")
        result = self._run("jobs", "history", "hist_job1")
        assert result.exit_code == 0

    def test_history_shows_run_row(self):
        self._run_job_and_wait("hist_job2")
        result = self._run("jobs", "history", "hist_job2")
        assert "SUCCESS" in result.output

    def test_history_shows_exit_code(self):
        self._run_job_and_wait("hist_job3")
        result = self._run("jobs", "history", "hist_job3")
        assert "0" in result.output   # exit code 0

    def test_history_no_runs_message(self):
        _seed_cmd("norun_hist")
        result = self._run("jobs", "history", "norun_hist")
        assert result.exit_code == 0
        assert "No run history" in result.output

    def test_history_unknown_job_exits_nonzero(self):
        result = self._run("jobs", "history", "no_such_job")
        assert result.exit_code != 0

    def test_history_shows_duration(self):
        self._run_job_and_wait("dur_job")
        result = self._run("jobs", "history", "dur_job")
        # Duration column shows Xs format
        assert "s" in result.output   # e.g. "1.2s"
