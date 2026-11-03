"""
FastAPI dependency functions.

All routes use these via Depends() for:
- Database sessions
- Current user (auth)
"""
from __future__ import annotations

from typing import Optional
from urllib.parse import urlsplit

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from autosys.db.connection import sync_session

_bearer = HTTPBearer(auto_error=False)

# Methods a forged cross-site form/fetch could use to change state. GET/HEAD
# never need the check: SameSite=Lax already blocks the cookie on a
# cross-site *subrequest*, but still attaches it on a top-level navigation,
# so a belt-and-suspenders Origin check covers the unsafe verbs explicitly.
_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


# ---------------------------------------------------------------------------
# DB session
# ---------------------------------------------------------------------------

def get_session():
    """
    Yield a SQLAlchemy sync Session.  FastAPI runs sync endpoints in a
    threadpool automatically, so blocking DB calls are safe here.
    """
    with sync_session() as session:
        yield session


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

class CurrentUser:
    def __init__(self, username: str, role: str) -> None:
        self.username = username
        self.role     = role

    def require_role(self, *roles: str) -> None:
        if self.role not in roles:
            raise HTTPException(
                status_code = status.HTTP_403_FORBIDDEN,
                detail      = f"Role '{self.role}' cannot perform this action. Required: {list(roles)}",
            )


def get_current_user(
    request: Request,
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> CurrentUser:
    """
    Resolve the current caller, human or machine, and return it.

    A Bearer header is always Shinro's worker: a Cognito client-credentials
    access token, checked against the pool's JWKS (auth.verify_m2m_access_token).
    No Bearer header falls back to the ``wcc_session`` cookie set by the
    OIDC login round trip (main.py's /api/v1/auth/* routes). Cookie-backed
    requests that use an unsafe HTTP method are also checked for same-origin
    (SameSite=Lax already blocks cross-site subrequests; this is the
    belt-and-suspenders check for a top-level cross-site navigation). A
    Bearer caller skips that check -- there is no cookie for a forged
    cross-site request to replay.

    If AUTOSYS_AUTH_ENABLED is false (only ever true in tests now -- the
    real network-facing entrypoint refuses to start that way, see
    auth.startup_check_errors()), returns an anonymous admin user so tests
    can hit endpoints without a token.
    """
    from autosys.app_server.auth import is_auth_enabled

    if not is_auth_enabled():
        return CurrentUser(username="anon", role="admin")

    if creds:
        return user_from_m2m_token(creds.credentials)

    token = request.cookies.get("wcc_session")
    if not token:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = "Not signed in",
            headers     = {"WWW-Authenticate": "Bearer"},
        )
    user = user_from_session_cookie(token)
    if request.method in _UNSAFE_METHODS:
        _require_same_origin(request)
    return user


def user_from_m2m_token(token: str) -> CurrentUser:
    from autosys.app_server.auth import oidc_settings, verify_m2m_access_token

    try:
        claims = verify_m2m_access_token(oidc_settings(), token)
    except Exception as exc:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = f"Invalid service token: {exc}",
            headers     = {"WWW-Authenticate": "Bearer"},
        )
    # Machine callers are read-only by design -- the client-credentials
    # scope Shinro's worker is issued (autosys-simulator/read) never grants
    # more than this role, so there is nothing to escalate to even if the
    # token is somehow coaxed into asking.
    return CurrentUser(username=f"svc:{claims['client_id']}", role="viewer")


def user_from_session_cookie(token: str) -> CurrentUser:
    from autosys.app_server.auth import decode_session_token

    try:
        claims = decode_session_token(token)
    except Exception as exc:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = f"Invalid session: {exc}",
            headers     = {"WWW-Authenticate": "Bearer"},
        )
    return CurrentUser(username=claims["sub"], role=claims.get("role", "viewer"))


def _require_same_origin(request: Request) -> None:
    from autosys.app_server.auth import oidc_settings

    public_url = oidc_settings().public_url
    sent = request.headers.get("origin") or request.headers.get("referer")
    if not sent or not public_url:
        return
    if urlsplit(sent)[:2] != urlsplit(public_url)[:2]:  # (scheme, host) only
        raise HTTPException(
            status_code = status.HTTP_403_FORBIDDEN,
            detail      = "Cross-origin request rejected",
        )
