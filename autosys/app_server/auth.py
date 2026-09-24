"""
JWT authentication for the AutoSys App Server.

Real AutoSys uses CA EEM (Embedded Entitlements Manager) — an LDAP-backed
RBAC system with its own token format.  We implement a simplified JWT scheme
that maps to the same three roles:

  viewer    — read-only (GET endpoints only)
  operator  — can enqueue events (STARTJOB, KILLJOB, …)
  admin     — full access including DELETE and machine management

Auth is controlled by the AUTOSYS_AUTH_ENABLED environment variable, which
now defaults to "true" -- this used to default to "false" ("all requests
are admin, no token required"), which combined with a well-known default
JWT secret and default admin/admin/operator/viewer accounts meant anyone
who could reach this server at all was an anonymous admin for free. There
is no baked-in fallback for the JWT secret or the user list any more
either: both must be supplied, and `autosys scheduler serve` (the actual
network-facing entrypoint -- see cli/scheduler_cmd.py's `_require_real_auth`)
refuses to start if they aren't, or if auth is off, or if the secret is
still the old well-known default. `create_app()` itself does not enforce
this -- it stays a plain factory so tests can construct an app without
standing up real credentials; the enforcement lives at the operational
boundary, where a real, network-reachable instance actually starts.

Users are defined in AUTOSYS_USERS as a JSON mapping:
  {"admin": {"password": "secret", "role": "admin"}}

For production, replace this with an LDAP lookup or EEM integration.

Read live from os.environ on every call (not cached at module-import time):
tests set AUTOSYS_AUTH_ENABLED/AUTOSYS_USERS/AUTOSYS_JWT_SECRET per-test via
monkeypatch, and a module-level constant computed once at first import
would freeze on whatever value happened to be set the first time any test
in the whole run touched this module -- every later test's monkeypatch
would then silently have no effect. Confirmed this was already happening:
running TestAuthRouter's tests together made the auth-disabled test fail
depending on run order, even before this rewrite.
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

_INSECURE_DEFAULT_SECRET = "dev-secret-change-in-production"


def is_auth_enabled() -> bool:
    return os.environ.get("AUTOSYS_AUTH_ENABLED", "true").lower() == "true"


def _jwt_secret() -> str:
    return os.environ.get("AUTOSYS_JWT_SECRET", "")


_JWT_TTL: int = int(os.environ.get("AUTOSYS_JWT_TTL", "3600"))


def _users() -> dict[str, dict]:
    raw = os.environ.get("AUTOSYS_USERS")
    if not raw:
        return {}
    return json.loads(raw)


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
    sig = _sign(f"{header}.{payload}", _jwt_secret())
    return f"{header}.{payload}.{sig}"


def decode_token(token: str) -> dict:
    """Verify and decode a JWT.  Raises ValueError on invalid/expired tokens."""
    parts = token.split(".")
    if len(parts) != 3:
        raise ValueError("Malformed token")
    header, payload, sig = parts
    expected = _sign(f"{header}.{payload}", _jwt_secret())
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
    user = _users().get(username)
    if user and user["password"] == password:
        return user
    return None


# ---------------------------------------------------------------------------
# Startup validation for the real, network-facing entrypoint
# ---------------------------------------------------------------------------

def startup_check_errors() -> list[str]:
    """
    Checks that must all pass before this server is allowed to actually
    start serving real network traffic. Returns a human-readable problem
    per failed check; empty list means OK to start.

    Deliberately not called from create_app() -- that stays a plain
    factory so tests can build an app without standing up real
    credentials. Called instead from the CLI's `serve` command
    (cli/scheduler_cmd.py), which is the actual network-facing entrypoint;
    that is where "no client-facing deployment can run with auth off"
    actually gets enforced.
    """
    errors: list[str] = []

    if not is_auth_enabled():
        errors.append(
            "AUTOSYS_AUTH_ENABLED must be 'true' -- this server refuses to "
            "start with auth disabled."
        )

    secret = _jwt_secret()
    if not secret:
        errors.append("AUTOSYS_JWT_SECRET must be set.")
    elif secret == _INSECURE_DEFAULT_SECRET:
        errors.append(
            f"AUTOSYS_JWT_SECRET must not be the well-known default "
            f"({_INSECURE_DEFAULT_SECRET!r})."
        )

    if not _users():
        errors.append(
            "AUTOSYS_USERS must be set to a JSON mapping of at least one "
            'user, e.g. {"admin": {"password": "...", "role": "admin"}}.'
        )

    return errors
