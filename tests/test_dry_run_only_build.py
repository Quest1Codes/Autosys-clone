"""A DRY_RUN_ONLY image deletes the real-execution modules; everything else
(CLI, API app, assessment endpoints) must still import and refuse real runs."""
import importlib
import subprocess
import sys
import textwrap

import pytest

STRIPPED = [
    "autosys/agent/dispatch.py", "autosys/agent/runner.py",
    "autosys/agent/runners.py", "autosys/agent/server.py",
    "autosys/engine/xinst_client.py", "autosys/engine/cloud_integration.py",
    "autosys/engine/agent_monitor.py",
]


@pytest.fixture(scope="module")
def stripped_tree(tmp_path_factory):
    """Copy of the package with the DRY_RUN_ONLY deletions applied."""
    import shutil
    import autosys
    from pathlib import Path
    root = tmp_path_factory.mktemp("dryonly")
    shutil.copytree(Path(autosys.__file__).parent, root / "autosys",
                    ignore=shutil.ignore_patterns("__pycache__"))
    for rel in STRIPPED:
        (root / rel).unlink()
    return root


def _run(root, code, env_extra=None):
    import os
    env = {**os.environ, "PYTHONPATH": str(root), "AUTOSYS_ALLOW_REAL_EXECUTION": "true",
           "AUTOSYS_DB_URL": "sqlite://", **(env_extra or {})}
    return subprocess.run([sys.executable, "-c", textwrap.dedent(code)], env=env,
                          capture_output=True, text=True, cwd=str(root))


def test_real_execution_refused_even_when_opted_in(stripped_tree):
    r = _run(stripped_tree, """
        from autosys import safety
        assert safety.DRY_RUN_ONLY_BUILD
        assert not safety.real_execution_allowed()
        assert "dry-run-only" in safety.real_execution_error()
    """)
    assert r.returncode == 0, r.stderr


def test_cli_and_app_import_without_real_run_code(stripped_tree):
    r = _run(stripped_tree, """
        import autosys.cli.main
        import autosys.agent
        from autosys.app_server.main import create_app
        from autosys.app_server.routers import assessment, machines
        create_app(dry_run=True)
    """)
    assert r.returncode == 0, r.stderr


def test_agent_commands_refuse(stripped_tree):
    r = _run(stripped_tree, """
        from click.testing import CliRunner
        from autosys.cli.agent_cmd import agent_group
        res = CliRunner().invoke(agent_group, ["start"])
        assert res.exit_code == 1, res.output
        assert "dry-run-only" in res.output
    """)
    assert r.returncode == 0, r.stderr


def test_history_import_works_in_stripped_build(stripped_tree):
    """Run-history import needs none of the real-execution modules -- by
    design it only touches db/schema, db/repository, db/connection and
    models/enums, none of which are stripped -- so it must work unchanged
    in the client-shipped DRY_RUN_ONLY image."""
    r = _run(stripped_tree, """
        import autosys.cli.history_cmd
        import autosys.app_server.routers.history
        from autosys.parser.run_history_ingest import ingest_run_history_stream
        assert callable(ingest_run_history_stream)
    """)
    assert r.returncode == 0, r.stderr


def test_estate_clear_works_in_stripped_build(stripped_tree):
    """Estate-clear needs none of the real-execution modules -- it only
    touches db/schema, db/repository, db/connection and parser/jil_ingest,
    none of which are stripped -- so it must work unchanged in the
    client-shipped DRY_RUN_ONLY image."""
    r = _run(stripped_tree, """
        import autosys.cli.estate_cmd
        import autosys.app_server.routers.estate
        from autosys.parser.jil_ingest import remove_jobs_not_in
        assert callable(remove_jobs_not_in)
    """)
    assert r.returncode == 0, r.stderr


def test_full_tree_unchanged():
    from autosys import safety
    assert not safety.DRY_RUN_ONLY_BUILD
