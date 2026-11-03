"""
WCC, its live stream and the API WebSocket require a sign-in (audit SEC-03).

The bundled nginx proxies /api/wcc/* and /api/sse/* to WCC, and those routes
had no authentication: anyone who could reach the Ingress got every job name,
hostname, owner and dependency in the estate. The API's own /api/v1/ws/events
also accepted connections without a token.

These tests run with authentication ON (the rest of the WCC suite turns it off
to test behaviour, not access).
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect


@pytest.fixture()
def auth_env(tmp_path, oidc_env, monkeypatch):
    monkeypatch.setenv("AUTOSYS_DB_URL", f"sqlite:///{tmp_path / 'wcc.db'}")
    from autosys.db import connection
    from autosys.db.migrations import create_all_sync
    connection.reset_engines()
    create_all_sync()
    yield
    connection.reset_engines()


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
def test_bad_cookie_is_rejected(wcc, path):
    r = wcc.get(path, cookies={"wcc_session": "not-a-real-token"})
    assert r.status_code == 401


@pytest.mark.parametrize("path", DATA_ROUTES)
def test_bad_bearer_is_rejected(wcc, path):
    r = wcc.get(path, headers={"Authorization": "Bearer not-a-real-token"})
    assert r.status_code == 401


@pytest.mark.parametrize("path", ["/api/wcc/jobs", "/api/wcc/runs", "/api/wcc/alarms"])
def test_valid_session_cookie_is_accepted(wcc, session_cookie, path):
    r = wcc.get(path, cookies={"wcc_session": session_cookie("viewer")})
    assert r.status_code == 200


def test_valid_m2m_bearer_is_accepted(wcc, m2m_token):
    r = wcc.get("/api/wcc/jobs", headers={"Authorization": f"Bearer {m2m_token()}"})
    assert r.status_code == 200


def test_m2m_token_from_an_untrusted_client_is_rejected(wcc, m2m_token):
    r = wcc.get("/api/wcc/jobs", headers={"Authorization": f"Bearer {m2m_token(client_id='someone-else')}"})
    assert r.status_code == 401


def test_m2m_token_missing_the_required_scope_is_rejected(wcc, m2m_token):
    r = wcc.get("/api/wcc/jobs", headers={"Authorization": f"Bearer {m2m_token(scope='something-else')}"})
    assert r.status_code == 401


def test_data_routes_do_not_accept_token_in_url(wcc, session_cookie):
    # Query-string tokens are gone entirely now -- a same-origin EventSource
    # sends cookies like any other request, so nothing needs one in the URL.
    assert wcc.get(f"/api/wcc/jobs?token={session_cookie('viewer')}").status_code == 401


def test_sse_rejects_a_bad_session_cookie(wcc):
    bad = wcc.get("/api/sse/jobs", cookies={"wcc_session": "nope"})
    assert bad.status_code == 401


def test_sse_dependency_accepts_a_valid_session_cookie(session_cookie):
    # The stream route checks the cookie before it starts streaming, so a
    # rejected request returns at once; an accepted one would stream
    # forever, so its dependency is called directly rather than through
    # the client (same precaution the old ?token= version of this test
    # took).
    from starlette.requests import Request
    from autosys.app_server.deps import get_current_user

    def _request(cookie_header: str = "") -> Request:
        headers = [(b"cookie", cookie_header.encode())] if cookie_header else []
        return Request({"type": "http", "method": "GET", "headers": headers, "query_string": b""})

    user = get_current_user(_request(f"wcc_session={session_cookie('viewer')}"), None)
    assert user.username == "user@example.com"

    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        get_current_user(_request("wcc_session=nope"), None)


def test_wcc_api_docs_are_disabled(wcc):
    for path in ("/docs", "/redoc", "/openapi.json"):
        r = wcc.get(path)
        assert r.status_code == 404 or "openapi" not in r.text.lower()


def test_api_websocket_requires_a_session(auth_env, session_cookie):
    from autosys.app_server.main import create_app
    with TestClient(create_app(start_eps=False)) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/api/v1/ws/events") as ws:
                ws.receive_text()
        with c.websocket_connect(
            "/api/v1/ws/events", headers={"cookie": f"wcc_session={session_cookie('viewer')}"},
        ):
            pass  # accepted
