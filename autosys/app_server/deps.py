"""
FastAPI dependency functions.

All routes use these via Depends() for:
- Database sessions
- Current user (auth)
"""
from __future__ import annotations

from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from autosys.db.connection import sync_session

_bearer = HTTPBearer(auto_error=False)


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
    creds: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
) -> CurrentUser:
    """
    Validate the Bearer JWT and return the current user.

    If AUTOSYS_AUTH_ENABLED is false (the default for development and tests),
    returns an anonymous admin user so all endpoints work without a token.
    """
    from autosys.app_server.auth import AUTH_ENABLED, decode_token

    if not AUTH_ENABLED:
        return CurrentUser(username="anon", role="admin")

    if creds is None:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = "Missing Authorization header",
            headers     = {"WWW-Authenticate": "Bearer"},
        )
    try:
        payload = decode_token(creds.credentials)
    except Exception as exc:
        raise HTTPException(
            status_code = status.HTTP_401_UNAUTHORIZED,
            detail      = f"Invalid token: {exc}",
            headers     = {"WWW-Authenticate": "Bearer"},
        )
    return CurrentUser(username=payload["sub"], role=payload.get("role", "viewer"))
