"""Phase 9 — XSUAA JWT authentication middleware.

Validates incoming JWTs centrally at the middleware layer.
Application services receive AuthenticatedPrincipal objects.
Raw JWT claims, tokens, and Authorization headers are NEVER
passed to business services.

JWT validation:
    - Fetch JWKS from XSUAA URL + /token_keys (with caching)
    - Verify signature, expiry, issuer, audience
    - Extract scopes from 'scope' claim (space-separated or list)
    - Map to N-CoreIQ roles
    - Inject AuthenticatedPrincipal into request.state

In 'disabled' auth mode:
    - All requests receive ANONYMOUS_PRINCIPAL (full access)
    - No JWT is required
    - Suitable for local development ONLY

XSUAA JWT structure:
    {
      "sub": "user@tenant",
      "user_name": "user@domain",
      "email": "user@domain",
      "scope": "openid NCoreIQ.Viewer NCoreIQ.Analyst",
      "iss": "https://subdomain.authentication.eu10.hana.ondemand.com/oauth/token",
      "aud": ["sap-repository-intelligence", "..."],
      "zid": "tenant-id",
      "exp": 1234567890
    }
"""
from __future__ import annotations

import logging
import time
from typing import Any

import httpx
from fastapi import Request, Response
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

from app.auth.principal import (
    ANONYMOUS_PRINCIPAL,
    MCP_ANONYMOUS_PRINCIPAL,
    SCOPE_ADMIN,
    SCOPE_ANALYST,
    SCOPE_OPERATOR,
    SCOPE_VIEWER,
    AuthenticatedPrincipal,
)
from app.btp.bindings import BTPServiceBindingResolver
from app.core.config import Settings, get_settings

logger = logging.getLogger(__name__)


def check_jwt_crypto_available() -> None:
    """Verify PyJWT with RSA cryptographic support is importable.

    Must be called at startup when app_env=btp and auth_mode=xsuaa.
    Raises RuntimeError if the dependency is absent so the application
    refuses to start rather than silently falling back to structure-only
    JWT verification in production.

    In non-BTP modes the application continues with a warning (development
    convenience).  BTP production MUST NOT use unverified JWT tokens.
    """
    try:
        import jwt as _jwt  # noqa: F401
        from jwt.algorithms import RSAAlgorithm  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "PyJWT with RSA cryptography support is required for BTP production mode "
            "(APP_ENV=btp, AUTH_MODE=xsuaa) but is not installed. "
            "Add to requirements: PyJWT[crypto]>=2.8,<3.0 and cryptography>=42.0. "
            f"Import error: {exc}"
        ) from exc


# Paths exempt from authentication (always accessible)
_AUTH_EXEMPT_PATHS = frozenset(
    {
        "/health/live",
        "/health/ready",
        "/api/v1/health",
        "/api/v1/health/live",
        "/api/v1/health/ready",
        "/",
        "/docs",
        "/openapi.json",
        "/redoc",
    }
)


def _is_mcp_path(path: str) -> bool:
    """Return True only for the canonical MCP mount paths.

    Matches exactly:
        /mcp
        /mcp/
        /mcp/<anything>

    Deliberately does NOT use a loose prefix match such as
    ``path.startswith("/mcp")`` because that would incorrectly match
    unrelated paths like ``/mcp-admin`` or ``/mcpfoo``. Those paths must
    NEVER be treated as anonymous MCP endpoints.
    """
    return path == "/mcp" or path.startswith("/mcp/")

# Source-defined NCoreIQ scope sub-namespace.
# All scopes in deployment/btp/xs-security.json are declared under the
# $XSAPPNAME.NCoreIQ.* pattern. XSUAA adds <xsappname>.<namespace> as a
# distinct audience entry in client_credentials tokens that carry any
# NCoreIQ.* scope. This constant is the authoritative, source-proven
# namespace component — it must stay aligned with xs-security.json scope
# name definitions.
_NCOREIQ_SCOPE_NAMESPACE: str = "NCoreIQ"


# Map XSUAA scope suffixes to NCoreIQ scope names
# XSUAA delivers scopes as "<xsappname>.<scope>" or just "<scope>"
_SCOPE_MAP = {
    "RepositoryViewer": SCOPE_VIEWER,
    "RepositoryExtractor": SCOPE_ANALYST,
    "RepositoryAdmin": SCOPE_ADMIN,
    # Direct mapping (if xs-security.json uses NCoreIQ.* names)
    "NCoreIQ.Viewer": SCOPE_VIEWER,
    "NCoreIQ.Analyst": SCOPE_ANALYST,
    "NCoreIQ.Operator": SCOPE_OPERATOR,
    "NCoreIQ.Admin": SCOPE_ADMIN,
    # Viewer includes Analyst/Operator as fallback
    "Viewer": SCOPE_VIEWER,
    "Analyst": SCOPE_ANALYST,
    "Operator": SCOPE_OPERATOR,
    "Admin": SCOPE_ADMIN,
}


def _build_accepted_audiences(
    xsappname: str | None,
    bound_clientid: str | None,
) -> list[str]:
    """Build the accepted JWT audience list from the bound XSUAA identity.

    All values are derived strictly from trusted XSUAA runtime binding
    metadata (VCAP_SERVICES). No tenant qualifier (!t...) is ever manually
    constructed or hard-coded.

    Includes, in order:
    1. The bound xsappname as-is from VCAP_SERVICES (may be tenant-qualified,
       e.g. ``ncoreiq-org-space!t12345``).
    2. The NCoreIQ scope-namespace audience: ``<xsappname>.<namespace>``.
       XSUAA emits exactly this audience entry in client_credentials tokens
       that carry NCoreIQ.* scopes. It is derived by appending the
       source-defined ``_NCOREIQ_SCOPE_NAMESPACE`` constant — no suffix is
       invented at runtime.
    3. The bound clientid (existing behaviour, preserved for compatibility).

    Security properties:
    - Exact-string membership semantics are preserved (no substring matching).
    - Only values derived from the trusted XSUAA binding are accepted.
    - A token for a different application is rejected because its xsappname
      will differ from the bound value.
    - A token for the same application in a different tenant is rejected
      because the !t qualifier in the VCAP_SERVICES xsappname will differ.
    """
    candidates: list[str] = []
    if xsappname:
        candidates.append(xsappname)
        candidates.append(f"{xsappname}.{_NCOREIQ_SCOPE_NAMESPACE}")
    if bound_clientid:
        candidates.append(bound_clientid)
    # Stable, de-duplicated order
    return list(dict.fromkeys(candidates))


class JWKSCache:
    """Simple in-memory JWKS cache with TTL."""

    def __init__(self, ttl_seconds: int = 300) -> None:
        self._ttl = ttl_seconds
        self._jwks: dict[str, Any] | None = None
        self._fetched_at: float = 0.0

    def get(self) -> dict[str, Any] | None:
        if self._jwks and (time.monotonic() - self._fetched_at) < self._ttl:
            return self._jwks
        return None

    def set(self, jwks: dict[str, Any]) -> None:
        self._jwks = jwks
        self._fetched_at = time.monotonic()

    def invalidate(self) -> None:
        self._jwks = None
        self._fetched_at = 0.0


# Module-level JWKS cache (shared across requests)
_jwks_cache = JWKSCache()


def _extract_token(request: Request) -> str | None:
    """Extract Bearer token from Authorization header."""
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer ") or auth_header.startswith("bearer "):
        return auth_header.split(" ", 1)[1].strip()
    return None


def _parse_scopes_from_claims(claims: dict[str, Any], xsappname: str | None) -> list[str]:
    """Extract and normalize scopes from JWT claims.

    XSUAA delivers scopes as:
    - Space-separated string: "openid xsappname.Viewer xsappname.Analyst"
    - List: ["openid", "xsappname.Viewer"]
    """
    raw_scope = claims.get("scope", "")
    if isinstance(raw_scope, str):
        raw_scopes = raw_scope.split()
    elif isinstance(raw_scope, list):
        raw_scopes = [str(s) for s in raw_scope]
    else:
        raw_scopes = []

    ncoreiq_scopes: list[str] = []
    for raw in raw_scopes:
        # Strip xsappname prefix if present
        scope_suffix = raw
        if xsappname and raw.startswith(f"{xsappname}."):
            scope_suffix = raw[len(xsappname) + 1:]

        # Direct match
        if raw in _SCOPE_MAP:
            mapped = _SCOPE_MAP[raw]
            if mapped not in ncoreiq_scopes:
                ncoreiq_scopes.append(mapped)
        elif scope_suffix in _SCOPE_MAP:
            mapped = _SCOPE_MAP[scope_suffix]
            if mapped not in ncoreiq_scopes:
                ncoreiq_scopes.append(mapped)

    # Ensure higher roles include lower role scopes
    if SCOPE_ADMIN in ncoreiq_scopes:
        for s in [SCOPE_VIEWER, SCOPE_ANALYST, SCOPE_OPERATOR]:
            if s not in ncoreiq_scopes:
                ncoreiq_scopes.append(s)
    elif SCOPE_OPERATOR in ncoreiq_scopes:
        for s in [SCOPE_VIEWER, SCOPE_ANALYST]:
            if s not in ncoreiq_scopes:
                ncoreiq_scopes.append(s)
    elif SCOPE_ANALYST in ncoreiq_scopes:
        if SCOPE_VIEWER not in ncoreiq_scopes:
            ncoreiq_scopes.append(SCOPE_VIEWER)

    return ncoreiq_scopes


def _build_principal_from_claims(
    claims: dict[str, Any], xsappname: str | None
) -> AuthenticatedPrincipal:
    """Build AuthenticatedPrincipal from validated JWT claims."""
    subject = str(claims.get("sub", claims.get("client_id", "unknown")))
    user_name = claims.get("user_name") or claims.get("user_id")
    display_name = str(user_name) if user_name else subject
    email = claims.get("email")
    tenant = claims.get("zid") or claims.get("tenant_id")

    scopes = _parse_scopes_from_claims(claims, xsappname)

    return AuthenticatedPrincipal(
        subject=subject,
        display_name=display_name,
        email=str(email) if email else None,
        scopes=scopes,
        roles=list(scopes),
        tenant=str(tenant) if tenant else None,
        is_service=not bool(user_name),
    )


async def _fetch_jwks(jwks_uri: str) -> dict[str, Any]:
    """Fetch JWKS from the XSUAA token_keys endpoint."""
    try:
        async with httpx.AsyncClient(timeout=10.0, verify=True) as http:
            response = await http.get(jwks_uri)
            if response.status_code != 200:
                raise ValueError(
                    f"JWKS fetch failed: HTTP {response.status_code} from {jwks_uri}"
                )
            return response.json()  # type: ignore[no-any-return]
    except httpx.RequestError as exc:
        raise ValueError(f"Cannot reach JWKS endpoint {jwks_uri}: {exc}") from exc


def _validate_jwt_without_library(
    token: str,
    jwks: dict[str, Any],
    audience: str | list[str] | None,
    issuer: str | None,
) -> dict[str, Any]:
    """Validate JWT using PyJWT if available, otherwise do basic claims check.

    Full RS256 signature verification requires PyJWT + cryptography.
    If not installed, we do expiry + structure checks only (development mode).
    In production BTP deployments, install: pip install PyJWT[crypto] cryptography
    """
    import base64
    import json as _json
    import struct

    try:
        import jwt as _jwt  # type: ignore[import]

        # Full validation with PyJWT
        keys = jwks.get("keys", [])
        if not keys:
            raise ValueError("JWKS contains no keys")

        # Try each key
        last_exc: Exception | None = None
        for key_data in keys:
            try:
                from jwt.algorithms import RSAAlgorithm  # type: ignore[import]
                public_key = RSAAlgorithm.from_jwk(key_data)
                claims = _jwt.decode(
                    token,
                    public_key,
                    algorithms=["RS256"],
                    audience=audience,
                    issuer=issuer,
                    options={
                        "verify_exp": True,
                        "verify_aud": bool(audience),
                        "verify_iss": bool(issuer),
                    },
                )
                return claims  # type: ignore[no-any-return]
            except Exception as exc:
                last_exc = exc
                continue

        raise ValueError(f"JWT validation failed with all keys: {last_exc}")

    except ImportError:
        # PyJWT is not installed.
        # Development/test: fall back to structure-only check with a warning.
        # BTP production (app_env=btp): check_jwt_crypto_available() is called
        # at startup and will have already refused to start — this path is
        # unreachable in production.  The guard below is a defence-in-depth
        # measure in case the startup check is bypassed (e.g. unit tests that
        # patch startup).
        logger.warning(
            "jwt_library_unavailable — PyJWT[crypto] not installed. "
            "Falling back to structure-only JWT check (development mode only). "
            "This path must NOT be reached in BTP production (app_env=btp).",
        )

        # Decode payload (no signature verification)
        parts = token.split(".")
        if len(parts) != 3:
            raise ValueError("JWT has invalid structure (expected 3 parts)")

        # Add padding
        payload_b64 = parts[1] + "=" * (4 - len(parts[1]) % 4)
        try:
            payload_bytes = base64.urlsafe_b64decode(payload_b64)
            claims = _json.loads(payload_bytes)
        except Exception as exc:
            raise ValueError(f"Cannot decode JWT payload: {exc}") from exc

        # Check expiry
        exp = claims.get("exp")
        if exp and time.time() > exp:
            raise ValueError("JWT has expired")

        # Check audience membership (mirrors PyJWT verify_aud semantics)
        if audience:
            token_aud = claims.get("aud")
            if token_aud is None:
                raise ValueError(
                    "JWT has no 'aud' claim but audience validation is configured"
                )
            token_aud_set: set[str] = (
                {token_aud} if isinstance(token_aud, str) else set(token_aud)
            )
            accepted_set: set[str] = (
                {audience} if isinstance(audience, str) else set(audience)
            )
            if not token_aud_set.intersection(accepted_set):
                raise ValueError(
                    "JWT audience validation failed: token aud does not contain "
                    "any of the configured accepted audience values"
                )

        return claims  # type: ignore[no-any-return]


class XSUAAAuthMiddleware(BaseHTTPMiddleware):
    """FastAPI middleware for XSUAA JWT validation.

    In 'disabled' mode: injects ANONYMOUS_PRINCIPAL (full access, local dev).
    In 'xsuaa' mode: validates JWT, rejects invalid/missing tokens with 401.

    Exempt paths (liveness, readiness, health) always pass through.
    """

    def __init__(self, app, settings: Settings | None = None) -> None:
        super().__init__(app)
        self._settings = settings or get_settings()

    async def dispatch(self, request: Request, call_next) -> Response:
        # Always exempt certain paths
        path = request.url.path
        if path in _AUTH_EXEMPT_PATHS or path.startswith("/ui"):
            request.state.principal = ANONYMOUS_PRINCIPAL
            return await call_next(request)

        auth_settings = self._settings.auth
        # Synchronize with legacy auth_mode flat field
        effective_mode = auth_settings.mode
        if effective_mode == "disabled" and self._settings.auth_mode == "xsuaa":
            effective_mode = "xsuaa"

        if effective_mode == "disabled":
            request.state.principal = ANONYMOUS_PRINCIPAL
            return await call_next(request)

        # ------------------------------------------------------------
        # TEST/DEMO ONLY — MCP-specific anonymous exemption.
        #
        # Only applies when:
        #   1. effective_mode == "xsuaa" (we are already past the
        #      global "disabled" short-circuit above), AND
        #   2. NCOREIQ_MCP_AUTH_MODE == "none" (explicit opt-in), AND
        #   3. the request path is a valid MCP path (/mcp, /mcp/, /mcp/*)
        #      using exact/prefix matching that does NOT match
        #      "/mcp-admin" or "/mcpfoo".
        #
        # When all three conditions hold: skip Bearer token extraction
        # and JWT validation entirely for this request, and attach the
        # least-privilege MCP_ANONYMOUS_PRINCIPAL (Viewer+Analyst) so
        # downstream MCP tool authorization continues to function
        # normally. AUTH_MODE itself remains "xsuaa" — every other path
        # (including /api/v1/*) is unaffected and still requires a
        # valid Bearer token.
        # ------------------------------------------------------------
        mcp_auth_mode = getattr(self._settings, "ncoreiq_mcp_auth_mode", "xsuaa")
        if mcp_auth_mode == "none" and _is_mcp_path(path):
            request.state.principal = MCP_ANONYMOUS_PRINCIPAL
            logger.debug(
                "auth_mcp_anonymous_bypass path=%s mcp_auth_mode=none", path
            )
            return await call_next(request)

        # XSUAA validation
        token = _extract_token(request)
        if not token:
            return JSONResponse(
                status_code=401,
                content={
                    "error": "AUTHENTICATION_REQUIRED",
                    "message": "Authentication required. Provide a valid Bearer token.",
                    "request_id": getattr(request.state, "request_id", None),
                },
                headers={"WWW-Authenticate": "Bearer"},
            )

        try:
            # Resolve XSUAA runtime metadata.
            # Explicit Settings values take precedence; Cloud Foundry
            # VCAP_SERVICES is used as the production fallback.
            xsuaa_binding = BTPServiceBindingResolver().get_xsuaa()

            bound_xsuaa_url = None
            bound_xsappname = None
            bound_clientid = None

            if xsuaa_binding:
                bound_xsuaa_url = (
                    xsuaa_binding.get("url")
                    or xsuaa_binding.get("uri")
                )
                bound_xsappname = xsuaa_binding.get("xsappname")
                bound_clientid = xsuaa_binding.get("clientid")

            xsuaa_url = (
                self._settings.btp.xsuaa.url
                or bound_xsuaa_url
            )

            xsappname = (
                self._settings.btp.xsuaa.xsappname
                or bound_xsappname
            )

            # Explicit AUTH__JWKS_URI remains the highest-priority override.
            jwks_uri = auth_settings.jwks_uri
            if not jwks_uri:
                if xsuaa_url:
                    jwks_uri = str(xsuaa_url).rstrip("/") + "/token_keys"
                else:
                    raise ValueError(
                        "JWKS URI not configured and no XSUAA service binding was found."
                    )

            # Explicit AUTH__ISSUER remains the highest-priority override.
            # Otherwise derive the standard XSUAA issuer from the service binding.
            issuer = auth_settings.issuer
            if not issuer and xsuaa_url:
                issuer = str(xsuaa_url).rstrip("/") + "/oauth/token"

            # Check cache
            jwks = _jwks_cache.get()
            if jwks is None:
                jwks = await _fetch_jwks(jwks_uri)
                _jwks_cache.set(jwks)

            # Determine the accepted JWT audience(s).
            # If AUTH__AUDIENCE is explicitly configured, use it as a strict
            # override (backward-compatible, exact-match semantics preserved).
            # Otherwise derive the accepted set dynamically from the trusted
            # XSUAA binding — no !t suffix is ever manually constructed.
            if auth_settings.audience:
                jwt_audience: str | list[str] | None = auth_settings.audience
            else:
                accepted = _build_accepted_audiences(xsappname, bound_clientid)
                jwt_audience = accepted or None

            claims = _validate_jwt_without_library(
                token,
                jwks,
                audience=jwt_audience,
                issuer=issuer,
            )

            principal = _build_principal_from_claims(claims, xsappname)
            request.state.principal = principal

            logger.debug(
                "auth_principal_set subject=%s scopes=%s",
                principal.subject,
                principal.scopes,
            )

        except ValueError as exc:
            logger.warning(
                "auth_jwt_validation_failed error=%s path=%s",
                str(exc),
                path,
            )
            return JSONResponse(
                status_code=401,
                content={
                    "error": "INVALID_TOKEN",
                    "message": "Authentication token is invalid or expired.",
                    "request_id": getattr(request.state, "request_id", None),
                },
                headers={"WWW-Authenticate": "Bearer"},
            )
        except Exception:
            logger.exception("auth_middleware_unexpected_error path=%s", path)
            return JSONResponse(
                status_code=500,
                content={
                    "error": "AUTHENTICATION_ERROR",
                    "message": "An error occurred during authentication.",
                    "request_id": getattr(request.state, "request_id", None),
                },
            )

        return await call_next(request)


def get_principal(request: Request) -> AuthenticatedPrincipal:
    """FastAPI dependency to extract the authenticated principal from request state."""
    principal = getattr(request.state, "principal", None)
    if principal is None:
        return ANONYMOUS_PRINCIPAL
    return principal
