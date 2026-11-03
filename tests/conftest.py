"""
Suite-wide test setup.

Many tests exercise real subprocess dispatch on purpose (LocalJobRunner,
AgentDispatch, agent serve, FTP/WOL runners against local fakes), so the suite
opts in to real execution by default. tests/test_execution_gate.py removes the
opt-in and proves every execution path refuses without it.
"""
from __future__ import annotations

import time
from types import SimpleNamespace

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from autosys.safety import REAL_EXECUTION_ENV_VAR


@pytest.fixture(autouse=True)
def _allow_real_execution_in_tests(monkeypatch):
    monkeypatch.setenv(REAL_EXECUTION_ENV_VAR, "true")


# ---------------------------------------------------------------------------
# OIDC auth fixtures
#
# The simulator no longer keeps its own users; it trusts whatever Cognito
# says. For tests, that means minting RS256 tokens with our own throwaway
# key and making auth.py's JWKS lookup return that key's public half
# instead of fetching anything over the network -- the same shape as the
# real JWKS response, without needing a real Cognito pool for every test.
# ---------------------------------------------------------------------------

@pytest.fixture()
def oidc_env(monkeypatch):
    """The OIDC settings auth.oidc_settings() reads live from the environment."""
    monkeypatch.setenv("AUTOSYS_AUTH_ENABLED", "true")
    monkeypatch.setenv("AUTOSYS_JWT_SECRET", "test-secret-for-oidc-tests-0123456789abcdef")
    monkeypatch.setenv("AUTOSYS_OIDC_REGION", "us-east-1")
    monkeypatch.setenv("AUTOSYS_OIDC_USER_POOL_ID", "us-east-1_TESTPOOL")
    monkeypatch.setenv("AUTOSYS_OIDC_CLIENT_ID", "wcc-test-client")
    monkeypatch.setenv("AUTOSYS_OIDC_DOMAIN", "https://test-pool.auth.us-east-1.amazoncognito.com")
    monkeypatch.setenv("AUTOSYS_PUBLIC_URL", "http://localhost:8080")
    monkeypatch.setenv("AUTOSYS_M2M_CLIENT_IDS", "shinro-worker-test")
    monkeypatch.setenv("AUTOSYS_M2M_SCOPE", "autosys-simulator/read")


@pytest.fixture()
def oidc_keypair(oidc_env, monkeypatch):
    """
    A throwaway RSA key standing in for Cognito's own signing key. Patches
    auth._jwks_client() so the app's token verification resolves to this
    key's public half instead of making a real HTTPS call.
    """
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key()

    class _FakeJWKSClient:
        def get_signing_key_from_jwt(self, token):
            return SimpleNamespace(key=public_key)

    monkeypatch.setattr("autosys.app_server.auth._jwks_client", lambda uri: _FakeJWKSClient())
    return private_key


def _mint(private_key, claims: dict, ttl: int = 3600) -> str:
    now = int(time.time())
    return jwt.encode({"iat": now, "exp": now + ttl, **claims}, private_key, algorithm="RS256")


@pytest.fixture()
def id_token(oidc_keypair):
    """id_token(sub=..., email=..., groups=[...], nonce=..., ttl=...) -> a Cognito-shaped ID token."""
    from autosys.app_server.auth import oidc_settings

    def _make(*, sub="user-1", email="user@example.com", groups=None, nonce="test-nonce", ttl=3600, **overrides):
        cfg = oidc_settings()
        claims = {
            "sub": sub, "email": email, "email_verified": True,
            "token_use": "id", "aud": cfg.client_id, "iss": cfg.issuer,
            "nonce": nonce, "cognito:groups": groups or [],
            **overrides,
        }
        return _mint(oidc_keypair, claims, ttl)

    return _make


@pytest.fixture()
def m2m_token(oidc_keypair):
    """m2m_token(client_id=..., scope=..., ttl=...) -> a Cognito-shaped client-credentials access token."""
    from autosys.app_server.auth import oidc_settings

    def _make(*, client_id="shinro-worker-test", scope="autosys-simulator/read", ttl=3600, **overrides):
        cfg = oidc_settings()
        claims = {
            "token_use": "access", "client_id": client_id, "scope": scope, "iss": cfg.issuer,
            **overrides,
        }
        return _mint(oidc_keypair, claims, ttl)

    return _make


@pytest.fixture()
def session_cookie(oidc_env):
    """session_cookie(role) -> this app's own signed session token, as if a user had just signed in."""
    from autosys.app_server.auth import encode_session_token

    def _make(role: str = "viewer", username: str = "user@example.com") -> str:
        return encode_session_token(username, role)

    return _make
