"""Single-credential authentication for the exposed services.

Scope, stated plainly: this is one shared operator login, not a user system.
There is one username and one password, taken from the environment, and a signed
token proving the holder presented them. There are no accounts, no roles and no
revocation, because there are no users yet.

What it does buy is the thing that was missing: `/api/mcp/`, `/api/gateway/` and
the rest are published to the internet by nginx, and before this anyone who
could reach the page could execute a remediation through
`POST /runs/{id}/approve` or write into Grafana through `POST /write`. The
approval gate is the system's core safety control and it was reachable by
anyone.

Tokens are HMAC-signed rather than stored, so the services verify them
independently. They run as separate uvicorn processes in one container and share
only the environment, so a token issued by api-gateway has to verify in
mcp-gateway without a shared session store.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import time
from typing import Dict, Optional

import httpx

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

from services.common.telemetry import setup_logging

logger = setup_logging("common-auth")

# Paths every service leaves open. The health checks are what Cloud Run and the
# deploy smoke test poll, so closing them would fail the deploy rather than
# secure it; the login route has to be reachable to obtain a token at all.
PUBLIC_PATHS = frozenset({"/healthz", "/readyz", "/auth/login"})

TOKEN_TTL_SECONDS = 12 * 60 * 60


def _username() -> str:
    return os.environ.get("APP_USERNAME", "supervisor")


def _password() -> str:
    return os.environ.get("APP_PASSWORD", "shadow-protocol")


def _secret() -> bytes:
    """Returns the signing key, shared by every service in the container.

    Derived from the password when APP_AUTH_SECRET is unset, rather than falling
    back to a constant baked into the repository. A constant would be public and
    would let anyone mint a valid token; deriving keeps the key exactly as secret
    as the password already is, and keeps it identical across the separate
    uvicorn processes, which is what lets one service verify another's token.
    """
    explicit = os.environ.get("APP_AUTH_SECRET")
    if explicit:
        return explicit.encode("utf-8")
    return hashlib.sha256(f"spc-derived-key:{_password()}".encode("utf-8")).digest()


def _sign(payload: bytes) -> str:
    return hmac.new(_secret(), payload, hashlib.sha256).hexdigest()


def _b64encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64decode(text: str) -> bytes:
    padding = "=" * (-len(text) % 4)
    return base64.urlsafe_b64decode(text + padding)


def check_credentials(username: str, password: str) -> bool:
    """Verifies the single operator credential in constant time."""
    user_ok = hmac.compare_digest(username or "", _username())
    pass_ok = hmac.compare_digest(password or "", _password())
    # Both comparisons always run, so a wrong username and a wrong password take
    # the same time and neither can be probed independently.
    return user_ok and pass_ok


def issue_token(username: str) -> tuple[str, int]:
    """Mints a signed token for a verified login. Returns (token, expiry epoch)."""
    expires_at = int(time.time()) + TOKEN_TTL_SECONDS
    payload = json.dumps({"u": username, "exp": expires_at}, separators=(",", ":"))
    encoded = _b64encode(payload.encode("utf-8"))
    return f"{encoded}.{_sign(encoded.encode('ascii'))}", expires_at


def verify_token(token: Optional[str]) -> Optional[str]:
    """Returns the username a token proves, or None if it proves nothing."""
    if not token or "." not in token:
        return None

    encoded, _, signature = token.rpartition(".")
    expected = _sign(encoded.encode("ascii"))
    if not hmac.compare_digest(signature, expected):
        return None

    try:
        payload = json.loads(_b64decode(encoded))
    except (ValueError, json.JSONDecodeError):
        return None

    if not isinstance(payload, dict) or payload.get("exp", 0) < time.time():
        return None
    return payload.get("u")


def token_from_request(request: Request) -> Optional[str]:
    """Reads the token from the Authorization header, or the query string.

    EventSource cannot set headers, so the SSE stream has no way to present a
    bearer token except in the URL. That is the only reason the query parameter
    is accepted.
    """
    header = request.headers.get("Authorization", "")
    if header.startswith("Bearer "):
        return header[len("Bearer "):].strip()
    return request.query_params.get("access_token")


# Subject recorded on a token one service mints to call another. Distinct from
# an operator login so the two are told apart in logs; it carries no separate
# privilege, because there is nothing here to separate yet.
SERVICE_PRINCIPAL = "svc:internal"


def service_auth_headers() -> Dict[str, str]:
    """Bearer header for one service calling another."""
    token, _ = issue_token(SERVICE_PRINCIPAL)
    return {"Authorization": f"Bearer {token}"}


class ServiceAuth(httpx.Auth):
    """Attaches an internal token to every request an httpx client makes.

    Applied at the client rather than at each call site. The services call each
    other constantly -- api-gateway to render-sim, agent-worker to mcp-gateway,
    action-executor to all three -- and putting the credential on the client
    means a new call site cannot forget it. Forgetting it is not a subtle
    failure: the callee returns 401 and the caller reports a 500, which is
    exactly what protecting these services without this did.
    """

    def auth_flow(self, request: httpx.Request):
        # Minted per request rather than cached: signing is an HMAC over a few
        # dozen bytes, and a cached token would eventually expire mid-flight.
        request.headers["Authorization"] = service_auth_headers()["Authorization"]
        yield request


def internal_auth() -> ServiceAuth:
    """The auth to hand an httpx client that calls another service in this stack."""
    return ServiceAuth()


class SingleCredentialAuthMiddleware(BaseHTTPMiddleware):
    """Rejects unauthenticated requests to everything but the public paths."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path.rstrip("/") or "/"

        # CORS preflight carries no Authorization header by design.
        if request.method == "OPTIONS" or path in PUBLIC_PATHS:
            return await call_next(request)

        if verify_token(token_from_request(request)) is None:
            return JSONResponse(
                status_code=401,
                content={"detail": "Authentication required."},
                headers={"WWW-Authenticate": "Bearer"},
            )
        return await call_next(request)
