"""
WCC, its live stream and the API WebSocket require a login (audit SEC-03).

The bundled nginx proxies /api/wcc/* and /api/sse/* to WCC, and those routes
had no authentication: anyone who could reach the Ingress got every job name,
hostname, owner and dependency in the estate. The API's own /api/v1/ws/events
also accepted connections without a token.

These tests run with authentication ON (the rest of the WCC suite turns it off
to test behaviour, not access).
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

SECRET = "test-secret-for-wcc-auth-0123456789abcdef"


@pytest.fixture()
def auth_env(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'wcc.db'}")
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTOSYS_JWT_SECRET", SECRET)
    monkeypatch.setenv("AUTOSYS_USERS", json.dumps({"viewer": {"password": "v", "role": "viewer"}}))
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    connection.reset_engines()
    create_all_sync()
    yield
    connection.reset_engines()


@pytest.fixture()
def token(auth_env) -> str:
    from autosys.app_server.auth import encode_token
    return encode_token("viewer", "viewer")


@pytest.fixture()
def wcc(auth_env):
    from autosys.wcc.app import create_wcc_app
    with TestClient(create_wcc_app()) as c:
        yield c


DATA_ROUTES = ["/api/wcc/jobs", "/api/wcc/jobs/ANY", "/api/wcc/boxes/ANY",
               "/api/wcc/runs", "/api/wcc/alarms", "/api/wcc/runs/r1/output"]


@pytest.mark.parametrize("path", DATA_ROUTES + ["/api/sse/jobs"])
def test_anonymous_requests_are_rejected(wcc, path):
    assert wcc.get(path).status_code == 401


@pytest.mark.parametrize("path", DATA_ROUTES)
def test_bad_token_is_rejected(wcc, path):
    r = wcc.get(path, headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/api/wcc/jobs", "/api/wcc/runs", "/api/wcc/alarms"])
def test_valid_token_is_accepted(wcc, token, path):
    assert wcc.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 200


def test_data_routes_do_not_accept_token_in_url(wcc, token):
    # Only the stream (EventSource cannot send headers) takes ?token=, so the
    # token does not end up in URLs for ordinary requests.
    assert wcc.get(f"/api/wcc/jobs?token={token}").status_code == 401


def test_sse_accepts_token_in_query_string(auth_env, token):
    # The stream route checks the token before it starts streaming, so a
    # rejected request returns at once; an accepted one would stream forever,
    # so its dependency is called directly rather than through the client.
    from starlette.requests import Request
    from autosys.app_server.deps import get_current_user_for_stream
    req = Request({"type": "http", "query_string": f"token={token}".encode(), "headers": []})
    assert get_current_user_for_stream(req, None).username == "viewer"
    bad = Request({"type": "http", "query_string": b"token=nope", "headers": []})
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        get_current_user_for_stream(bad, None)


def test_wcc_api_docs_are_disabled(wcc):
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = wcc.get(path)
        assert r.status_code == 404 or "openapi" not in r.text.lower()


def test_api_websocket_requires_token(auth_env, token):
    from autosys.app_server.main import create_app
    with TestClient(create_app(start_eps=False)) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/v1/ws/events") as ws:
                ws.receive_text()
        with c.websocket_connect(f"/api/v1/ws/events?token={token}"):
            pass  # accepted
