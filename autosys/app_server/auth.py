"""
JWT authentication for the AutoSys App Server.

Real AutoSys uses CA EEM (Embedded Entitlements Manager) — an LDAP-backed
RBAC system with its own token format.  We implement a simplified JWT scheme
that maps to the same three roles:

  viewer    — read-only (GET endpoints only)
  operator  — can enqueue events (STARTJOB, KILLJOB, …)
  admin     — full access including DELETE and machine management

Auth is controlled by the AUTOSYS_AUTH_ENABLED environment variable.
When set to "false" (the default for development), all requests are treated
as role "admin" without requiring a token.  Set to "true" for production.

Users are defined in AUTOSYS_USERS as a JSON mapping:
  {"admin": {"password": "secret", "role": "admin"}}

For production, replace this with an LDAP lookup or EEM integration.
"""
from __future__ import annotations

import json
import os
import time
from typing import Optional

from loguru import logger

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

AUTH_ENABLED: bool = os.environ.get("AUTOSYS_AUTH_ENABLED", "false").lower() == "true"

# Sentinel used by deps.py to short-circuit token validation
AuthDisabled = not AUTH_ENABLED

_JWT_SECRET: str = os.environ.get("AUTOSYS_JWT_SECRET", "dev-secret-change-in-production")
_JWT_TTL:    int = int(os.environ.get("AUTOSYS_JWT_TTL", "3600"))

# Default users when no AUTOSYS_USERS env var is set.
_DEFAULT_USERS: dict[str, dict] = {
    "admin":    {"password": "admin",    "role": "admin"},
    "operator": {"password": "operator", "role": "operator"},
    "viewer":   {"password": "viewer",   "role": "viewer"},
}

_USERS: dict[str, dict] = json.loads(
    os.environ.get("AUTOSYS_USERS", json.dumps(_DEFAULT_USERS))
)


# ---------------------------------------------------------------------------
# Pure-Python JWT  (avoids the python-jose / PyJWT dependency)
# ---------------------------------------------------------------------------

import base64
import hashlib
import hmac


def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))


def _sign(header_payload: str, secret: str) -> str:
    sig = hmac.new(secret.encode(), header_payload.encode(), hashlib.sha256).digest()
    return _b64url_encode(sig)


def encode_token(username: str, role: str, ttl: int = _JWT_TTL) -> str:
    """Create a signed HS256 JWT."""
    header  = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload = _b64url_encode(json.dumps({
        "sub":  username,
        "role": role,
        "iat":  int(time.time()),
        "exp":  int(time.time()) + ttl,
    }).encode())
    sig = _sign(f"{header}.{payload}", _JWT_SECRET)
    return f"{header}.{payload}.{sig}"


def decode_token(token: str) -> dict:
    """Verify and decode a JWT.  Raises ValueError on invalid/expired tokens."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed token")
    header, payload, sig = parts
    expected = _sign(f"{header}.{payload}", _JWT_SECRET)
    if not hmac.compare_digest(sig, expected):
        raise ValueError("Invalid signature")
    claims = json.loads(_b64url_decode(payload))
    if claims.get("exp", 0) < time.time():
        raise ValueError("Token expired")
    return claims


# ---------------------------------------------------------------------------
# Login helper
# ---------------------------------------------------------------------------

def authenticate(username: str, password: str) -> Optional[dict]:
    """Return user dict if credentials are valid, else None."""
    user = _USERS.get(username)
    if user and user["password"] == password:
        return user
    return None
