"""
Nothing executes without AUTOSYS_ALLOW_REAL_EXECUTION=true (audit SEC-01).

The original gate covered only `scheduler serve`; `scheduler start`,
`agent start`, `agent run-once` and `agent serve` still ran imported commands
with the variable unset. These tests unset it and try every path. The rest of
the suite runs with it set (tests/conftest.py), because many tests exercise
real subprocess dispatch on purpose.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from autosys.safety import REAL_EXECUTION_ENV_VAR, RealExecutionRefused


@pytest.fixture()
def no_opt_in(monkeypatch, tmp_path):
    monkeypatch.delenv(REAL_EXECUTION_ENV_VAR, raising=False)
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'gate.db'}")
    return tmp_path


def test_local_runner_refuses(no_opt_in):
    from autosys.agent.runner import LocalJobRunner
    marker = no_opt_in / "PWNED"
    runner = LocalJobRunner(command=f"touch {marker}", job_name="j", run_id="r")
    with pytest.raises(RealExecutionRefused):
        runner.run()
    assert not marker.exists()


@pytest.mark.parametrize("job_type", ["CMD", "FILEWATCH", "FTP", "CONNECT", "REMOTECMD", "WOL", "WEBSERVICE",
                                      "USERDEFINED", "SOMETHING_UNKNOWN"])
def test_runner_factory_refuses_every_real_type(no_opt_in, job_type):
    from autosys.agent.runners import create_runner
    row = SimpleNamespace(job_type=job_type, job_name="j", command="true", machine="m",
                          watch_file="/tmp/x", watch_file_min_size=0, watch_interval=60,
                          envvars=None, std_in_file=None, ulimit=None, extra_attrs_json=None)
    with pytest.raises(RealExecutionRefused):
        create_runner(row, run_id="r", command="true")


def test_dispatchers_and_listener_refuse(no_opt_in):
    from autosys.agent.dispatch import AgentDispatch
    from autosys.agent.remote import RemoteDispatch
    from autosys.agent.server import AgentServer
    with pytest.raises(RealExecutionRefused):
        AgentDispatch(local_only=True)
    with pytest.raises(RealExecutionRefused):
        AgentServer(machine_name="m", host="127.0.0.1", port=0)
    with pytest.raises(RealExecutionRefused):
        RemoteDispatch().dispatch(None, None, None)


@pytest.mark.parametrize("args", [
    ["scheduler", "start"],
    ["agent", "start"],
    ["agent", "run-once"],
    ["agent", "serve", "--machine", "m", "--port", "0"],
])
def test_cli_commands_refuse(no_opt_in, args):
    from autosys.cli.main import autosys
    result = CliRunner().invoke(autosys, args)
    assert result.exit_code != 0
    assert REAL_EXECUTION_ENV_VAR in (result.output + str(result.exception or ""))


def test_stub_types_do_not_need_opt_in(no_opt_in):
    from autosys.agent.runners import StubJobRunner, create_runner
    row = SimpleNamespace(job_type="SAP", job_name="s", command=None, machine=None)
    assert isinstance(create_runner(row, run_id="r", command=None), StubJobRunner)


def test_dry_run_dispatch_needs_no_opt_in(no_opt_in):
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    from autosys.db.schema import JobRow
    from autosys.scheduler.event_processor import _stub_dispatch
    connection.reset_engines()
    create_all_sync()
    marker = no_opt_in / "PWNED"
    with connection.sync_session() as s:
        row = JobRow(job_name="j", job_type="CMD", command=f"touch {marker}", status=8)
        s.add(row)
        s.flush()
        _stub_dispatch(s, row)
    connection.reset_engines()
    assert not marker.exists()


def test_opt_in_allows(monkeypatch, tmp_path):
    monkeypatch.setenv(REAL_EXECUTION_ENV_VAR, "true")
    from autosys.agent.runner import LocalJobRunner
    marker = tmp_path / "ran"
    assert LocalJobRunner(command=f"touch {marker}", job_name="j", run_id="r").run() == 0
    assert marker.exists()
