"""
`scheduler wcc` must refuse to start under the same conditions `scheduler
serve` already does -- see cli/scheduler_cmd.py's scheduler_wcc.

Before this gate existed, `wcc` booted successfully with no
AUTOSYS_JWT_SECRET configured at all, silently diverging from whatever
secret `api` was actually using to mint tokens. Every token `api` minted
then failed verification on every /api/wcc/* and /api/sse/* route -- 401,
with nothing in wcc's own logs to explain why (it never crashed; it just
silently never worked). Reproduced live: docker-compose.yml's `wcc` service
had no AUTOSYS_JWT_SECRET entry while `api`'s did, and the two containers'
secrets provably differed (wcc's hashed to sha256('') -- i.e. unset).
"""
from __future__ import annotations

import pytest
from click.testing import CliRunner


@pytest.fixture(autouse=True)
def isolated_db(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'test.db'}")
    yield


def test_wcc_refuses_with_no_secret_configured(monkeypatch):
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "true")
    monkeypatch.delenv("AUTOSYS_JWT_SECRET", raising=False)
    monkeypatch.delenv("AUTOSYS_USERS", raising=False)
    from autosys.cli.scheduler_cmd import scheduler_group
    res = CliRunner().invoke(scheduler_group, ["wcc"])
    assert res.exit_code == 1, res.output
    assert "Refusing to start" in res.output


def test_wcc_refuses_with_auth_disabled(monkeypatch):
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "false")
    from autosys.cli.scheduler_cmd import scheduler_group
    res = CliRunner().invoke(scheduler_group, ["wcc"])
    assert res.exit_code == 1, res.output
    assert "auth disabled" in res.output or "AUTOSYS_AUTH_ENABLED" in res.output


def test_wcc_refuses_with_insecure_default_secret(monkeypatch):
    from autosys.app_server.auth import _INSECURE_DEFAULT_SECRET
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTOSYS_JWT_SECRET", _INSECURE_DEFAULT_SECRET)
    monkeypatch.setenv("AUTOSYS_USERS", '{"admin": {"password": "x", "role": "admin"}}')
    from autosys.cli.scheduler_cmd import scheduler_group
    res = CliRunner().invoke(scheduler_group, ["wcc"])
    assert res.exit_code == 1, res.output
    assert "Refusing to start" in res.output
