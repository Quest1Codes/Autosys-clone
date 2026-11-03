"""
Authentication for the AutoSys App Server.

Real AutoSys uses CA EEM (Embedded Entitlements Manager) -- an LDAP-backed
RBAC system with its own token format. This clone signs people and the
Shinro worker in through the same identity provider Shinro itself uses
(Amazon Cognito, over OIDC), instead of keeping a second, local account
list. That used to be AUTOSYS_USERS -- a JSON map of plaintext passwords,
checked with a hand-rolled JWT -- which was a weaker, inconsistent sign-in
next to Shinro's own Cognito login, sitting on the same client Ingress.

Two kinds of caller now exist:

  Humans (the WCC console)
    Sign in through Cognito's hosted UI (authorization code + PKCE, run
    server-side by this app -- see main.py's /api/v1/auth/* routes).
    verify_id_token() checks the returned ID token against the pool's
    JWKS. Their role comes from Cognito groups (role_from_groups()), not
    from anything stored here. On success this app mints its own short
    session -- encode_session_token()/decode_session_token() -- and hands
    it back as an HttpOnly cookie. The pool and its JWKS are the only
    source of truth; this app stores no password of its own for a human.

  Shinro's worker (service-to-service)
    Presents a Cognito client-credentials access token as a Bearer
    header. verify_m2m_access_token() checks it against the same JWKS,
    and requires the token's own client_id to be one this app was told to
    trust (AUTOSYS_M2M_CLIENT_IDS) and carry the expected scope. There is
    no password shared between Shinro and this app any more -- Shinro
    gets its token directly from Cognito.

AUTOSYS_JWT_SECRET survives, but its job shrank to one thing: HS256-signing
this app's own session cookie and the short-lived OIDC state cookie used
mid-login. It is never shared with another service, so rotating it is a
one-place change.

Auth is controlled by the AUTOSYS_AUTH_ENABLED environment variable, which
defaults to "true". There is no baked-in fallback for the JWT secret or
the OIDC settings: all must be supplied, and `autosys scheduler serve` /
`autosys scheduler wcc` (the actual network-facing entrypoints -- see
cli/scheduler_cmd.py) refuse to start if they aren't, or if auth is off, or
if the secret is still the old well-known default. `create_app()` /
`create_wcc_app()` themselves do not enforce this -- they stay plain
factories so tests can construct an app without standing up real
credentials; the enforcement lives at the operational boundary, where a
real, network-reachable instance actually starts.

Read live from os.environ on every call (not cached at module-import time):
tests set these per-test via monkeypatch, and a module-level constant
computed once at first import would freeze on whatever value happened to
be set the first time any test in the whole run touched this module --
every later test's monkeypatch would then silently have no effect.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional
from urllib.parse import urlencode

import httpx
import jwt
from jwt import PyJWKClient

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_INSECURE_DEFAULT_SECRET = "dev-secret-change-in-production"  # nosec B105  # sentinel the server REFUSES to run with

# Cognito groups a simulator user can be in, and the role each one grants.
# Separate from Shinro's own Admin/Developer/Viewer groups on purpose --
# simulator access is granted independently, group by group, never implied
# by Shinro access. A user in none of these groups gets no simulator role.
_GROUP_ROLES = {
    "simulator-admin": "admin",
    "simulator-operator": "operator",
    "simulator-viewer": "viewer",
}
_ROLE_PRECEDENCE = ("admin", "operator", "viewer")


def is_auth_enabled() -> bool:
    return os.environ.get("AUTOSYS_AUTH_ENABLED", "true").lower() == "true"


def _jwt_secret() -> str:
    return os.environ.get("AUTOSYS_JWT_SECRET", "")


_JWT_TTL: int = int(os.environ.get("AUTOSYS_JWT_TTL", "3600"))


@dataclass(frozen=True)
class OIDCSettings:
    region: str
    user_pool_id: str
    client_id: str
    domain: str
    public_url: str
    m2m_client_ids: frozenset[str] = field(default_factory=frozenset)
    m2m_scope: str = "autosys-simulator/read"

    @property
    def domain_url(self) -> str:
        if self.domain:
            return self.domain
        return f"https://{self.user_pool_id}.auth.{self.region}.amazoncognito.com"

    @property
    def authorization_endpoint(self) -> str:
        return f"{self.domain_url}/oauth2/authorize"

    @property
    def token_endpoint(self) -> str:
        return f"{self.domain_url}/oauth2/token"

    @property
    def logout_endpoint(self) -> str:
        return f"{self.domain_url}/logout"

    @property
    def jwks_uri(self) -> str:
        return (
            f"https://cognito-idp.{self.region}.amazonaws.com/"
            f"{self.user_pool_id}/.well-known/jwks.json"
        )

    @property
    def issuer(self) -> str:
        return f"https://cognito-idp.{self.region}.amazonaws.com/{self.user_pool_id}"


def oidc_settings() -> OIDCSettings:
    """Read live from os.environ -- same reason as is_auth_enabled() above."""
    m2m_ids = os.environ.get("AUTOSYS_M2M_CLIENT_IDS", "")
    return OIDCSettings(
        region=os.environ.get("AUTOSYS_OIDC_REGION", ""),
        user_pool_id=os.environ.get("AUTOSYS_OIDC_USER_POOL_ID", ""),
        client_id=os.environ.get("AUTOSYS_OIDC_CLIENT_ID", ""),
        domain=os.environ.get("AUTOSYS_OIDC_DOMAIN", ""),
        public_url=os.environ.get("AUTOSYS_PUBLIC_URL", "").rstrip("/"),
        m2m_client_ids=frozenset(c.strip() for c in m2m_ids.split(",") if c.strip()),
        m2m_scope=os.environ.get("AUTOSYS_M2M_SCOPE", "autosys-simulator/read"),
    )


@lru_cache(maxsize=4)
def _jwks_client(jwks_uri: str) -> PyJWKClient:
    """
    One PyJWKClient per JWKS URL for the life of the process. PyJWKClient
    caches the key set itself (refetching on an unknown kid), so this just
    avoids rebuilding the client -- and its own small cache -- on every
    request. Keyed by URL rather than unbounded so a changed region/pool
    (tests) gets a fresh client instead of reusing a stale one forever.
    """
    return PyJWKClient(jwks_uri)


# ---------------------------------------------------------------------------
# Pure-Python JWT  (avoids depending on PyJWT for the session/state cookies
# too -- those are HS256, self-issued, and never need to interop with
# anything outside this process)
# ---------------------------------------------------------------------------

def _b64url_encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    pad = 4 - len(s) % 4
    return base64.urlsafe_b64decode(s + "=" * (pad % 4))


def _sign(header_payload: str, secret: str) -> str:
    sig = hmac.new(secret.encode(), header_payload.encode(), hashlib.sha256).digest()
    return _b64url_encode(sig)


def _encode_jwt(claims: dict, ttl: int) -> str:
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    now = int(time.time())
    payload = _b64url_encode(json.dumps({**claims, "iat": now, "exp": now + ttl}).encode())
    sig = _sign(f"{header}.{payload}", _jwt_secret())
    return f"{header}.{payload}.{sig}"


def _decode_jwt(token: str) -> dict:
    """Verify and decode a self-issued JWT. Raises ValueError on invalid/expired tokens."""
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
# Session cookie (human sign-in, after the Cognito round trip)
# ---------------------------------------------------------------------------

_SESSION_AUD = "wcc-session"
_SESSION_ISS = "autosys-simulator"


def encode_session_token(username: str, role: str, ttl: int = _JWT_TTL) -> str:
    return _encode_jwt(
        {"sub": username, "role": role, "iss": _SESSION_ISS, "aud": _SESSION_AUD},
        ttl,
    )


def decode_session_token(token: str) -> dict:
    claims = _decode_jwt(token)
    if claims.get("iss") != _SESSION_ISS or claims.get("aud") != _SESSION_AUD:
        raise ValueError("Not a session token")
    return claims


# ---------------------------------------------------------------------------
# OIDC state cookie (holds the PKCE verifier/state/nonce between the
# /auth/login redirect and the /auth/callback it leads to). A distinct
# audience from the session cookie means one can never be replayed as the
# other even though both are signed with the same secret.
# ---------------------------------------------------------------------------

_STATE_AUD = "wcc-oidc-state"
_STATE_TTL = 600  # the login round trip has 10 minutes to complete


def encode_state_token(*, state: str, nonce: str, code_verifier: str, next_path: str) -> str:
    return _encode_jwt(
        {
            "aud": _STATE_AUD,
            "state": state,
            "nonce": nonce,
            "code_verifier": code_verifier,
            "next": next_path,
        },
        _STATE_TTL,
    )


def decode_state_token(token: str) -> dict:
    claims = _decode_jwt(token)
    if claims.get("aud") != _STATE_AUD:
        raise ValueError("Not an OIDC state token")
    return claims


def generate_pkce_pair() -> tuple[str, str]:
    """Return (code_verifier, code_challenge) for the S256 PKCE method."""
    verifier = _b64url_encode(secrets.token_bytes(32))
    challenge = _b64url_encode(hashlib.sha256(verifier.encode()).digest())
    return verifier, challenge


# ---------------------------------------------------------------------------
# Cognito: human sign-in (authorization code + PKCE)
# ---------------------------------------------------------------------------

def authorization_url(cfg: OIDCSettings, *, redirect_uri: str, state: str, nonce: str, code_challenge: str) -> str:
    params = {
        "response_type": "code",
        "client_id": cfg.client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "scope": "openid email profile",
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
        "nonce": nonce,
    }
    return f"{cfg.authorization_endpoint}?{urlencode(params)}"


async def exchange_code_for_tokens(cfg: OIDCSettings, *, code: str, redirect_uri: str, code_verifier: str) -> dict:
    """
    Trade an authorization code for Cognito's tokens. No client secret is
    sent -- the WCC app client is public (PKCE is its only proof), the
    same shape as Shinro's own Cognito client.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            cfg.token_endpoint,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "grant_type": "authorization_code",
                "client_id": cfg.client_id,
                "code": code,
                "redirect_uri": redirect_uri,
                "code_verifier": code_verifier,
            },
        )
        response.raise_for_status()
        return response.json()


def verify_id_token(cfg: OIDCSettings, id_token: str, *, expected_nonce: str) -> dict:
    """
    Verify a Cognito ID token's signature, issuer, audience and expiry, and
    that it is actually an ID token carrying the nonce this app minted for
    this login attempt. Raises on any failure.
    """
    client = _jwks_client(cfg.jwks_uri)
    signing_key = client.get_signing_key_from_jwt(id_token)
    payload = jwt.decode(
        id_token,
        signing_key.key,
        algorithms=["RS256"],
        audience=cfg.client_id,
        issuer=cfg.issuer,
        leeway=120,
    )
    if payload.get("token_use") != "id":
        raise ValueError("Not an ID token")
    if not hmac.compare_digest(str(payload.get("nonce", "")), expected_nonce):
        raise ValueError("Nonce mismatch")
    return payload


def role_from_groups(groups: list[str]) -> Optional[str]:
    """
    Map Cognito groups to a simulator role. A user in more than one
    simulator group gets the most privileged role they hold. A user in
    none of them gets None -- simulator access is opt-in per group, never
    a default.
    """
    mapped = {_GROUP_ROLES[g] for g in groups if g in _GROUP_ROLES}
    for role in _ROLE_PRECEDENCE:
        if role in mapped:
            return role
    return None


def logout_url(cfg: OIDCSettings, *, redirect_uri: str) -> str:
    params = {"client_id": cfg.client_id, "logout_uri": redirect_uri}
    return f"{cfg.logout_endpoint}?{urlencode(params)}"


# ---------------------------------------------------------------------------
# Cognito: machine-to-machine (client-credentials access token)
# ---------------------------------------------------------------------------

def verify_m2m_access_token(cfg: OIDCSettings, token: str) -> dict:
    """
    Verify a Cognito client-credentials access token: signature, issuer and
    expiry against the pool's JWKS, that it really is an access token
    (never a session cookie or ID token), that its client_id is one this
    app was told to trust, and that it carries the required scope.

    Cognito access tokens carry no "aud" claim, so audience verification is
    off here -- the client_id allowlist is what stands in its place.
    """
    client = _jwks_client(cfg.jwks_uri)
    signing_key = client.get_signing_key_from_jwt(token)
    payload = jwt.decode(
        token,
        signing_key.key,
        algorithms=["RS256"],
        issuer=cfg.issuer,
        leeway=120,
        options={"verify_aud": False},
    )
    if payload.get("token_use") != "access":
        raise ValueError("Not an access token")
    client_id = payload.get("client_id")
    if client_id not in cfg.m2m_client_ids:
        raise ValueError("Client is not trusted for machine-to-machine access")
    scopes = str(payload.get("scope", "")).split()
    if cfg.m2m_scope not in scopes:
        raise ValueError(f"Token missing required scope {cfg.m2m_scope!r}")
    return payload


# ---------------------------------------------------------------------------
# Startup validation for the real, network-facing entrypoints
# ---------------------------------------------------------------------------

def startup_check_errors() -> list[str]:
    """
    Checks that must all pass before this server is allowed to actually
    start serving real network traffic. Returns a human-readable problem
    per failed check; empty list means OK to start.

    Deliberately not called from create_app()/create_wcc_app() -- those
    stay plain factories so tests can build an app without standing up
    real credentials. Called instead from the CLI's `serve` and `wcc`
    commands (cli/scheduler_cmd.py), which are the actual network-facing
    entrypoints; that is where "no client-facing deployment can run with
    auth off, or without real OIDC settings" actually gets enforced.
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

    cfg = oidc_settings()
    if not cfg.region:
        errors.append("AUTOSYS_OIDC_REGION must be set.")
    if not cfg.user_pool_id:
        errors.append("AUTOSYS_OIDC_USER_POOL_ID must be set.")
    if not cfg.client_id:
        errors.append("AUTOSYS_OIDC_CLIENT_ID must be set.")
    if not cfg.public_url:
        errors.append(
            "AUTOSYS_PUBLIC_URL must be set -- it is this app's own origin, "
            "used to build the Cognito redirect/logout URLs and to reject "
            "cross-origin requests."
        )

    return errors
