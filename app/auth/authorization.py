"""Gate 0E10-R4-A1 — Centralized endpoint authorization for N-CoreIQ.

Provides the require_scope() factory that returns FastAPI dependency functions
enforcing scope-based authorization on individual route handlers.

Usage:
    from app.auth.authorization import require_scope

    @router.get("/sap/ping")
    async def sap_ping(
        principal: AuthenticatedPrincipal = Depends(require_scope("sap_ping")),
        settings: Settings = Depends(get_settings),
    ) -> ConnectionTestResult:
        ...

Authorization behavior:
    AUTH_MODE=disabled:
        All requests receive ANONYMOUS_PRINCIPAL (full access, all scopes).
        require_scope() always returns the principal — suitable for local dev.

    AUTH_MODE=xsuaa, valid token with required scope:
        Returns the AuthenticatedPrincipal — handler executes normally.

    AUTH_MODE=xsuaa, valid token WITHOUT required scope:
        Raises HTTP 403 Forbidden before the handler runs.

    AUTH_MODE=xsuaa, no token / invalid token:
        XSUAAAuthMiddleware returns HTTP 401 before this dependency runs.

    Principal missing from request.state (middleware bypass, should not occur):
        Fails closed with HTTP 403 — never allows access.

The dependency chain:
    require_scope(endpoint_name)
        └─ Depends(get_principal)   ← reads request.state.principal
              └─ set by XSUAAAuthMiddleware

In tests, override get_principal via app.dependency_overrides to inject
specific principals without real XSUAA tokens:

    from app.auth.middleware import get_principal
    from app.auth.principal import AuthenticatedPrincipal, SCOPE_VIEWER

    def _viewer():
        return AuthenticatedPrincipal(
            subject="test-viewer", display_name="Viewer",
            scopes=[SCOPE_VIEWER],
        )

    app.dependency_overrides[get_principal] = _viewer
"""
from __future__ import annotations

import logging

from fastapi import Depends, HTTPException

from app.auth.middleware import get_principal
from app.auth.principal import AuthenticatedPrincipal

logger = logging.getLogger(__name__)


def require_scope(endpoint_name: str):
    """Return a FastAPI dependency that enforces the required scope for an endpoint.

    The returned async function:
      1. Retrieves the AuthenticatedPrincipal established by XSUAAAuthMiddleware
         via Depends(get_principal).
      2. Fails closed (HTTP 403) if the principal is missing — never allows access
         when the authentication state is indeterminate.
      3. Evaluates the canonical endpoint capability via AuthenticatedPrincipal.can_access().
         can_access() uses the role-inheritance endpoint sets defined in principal.py:
             VIEWER_ENDPOINTS ⊂ ANALYST_ENDPOINTS ⊂ OPERATOR_ENDPOINTS ⊂ ADMIN_ENDPOINTS
      4. Raises HTTP 403 when the principal is authenticated but not authorized.
      5. Returns the AuthenticatedPrincipal to the route handler on success.

    The HTTP 401 (unauthenticated) case is handled exclusively by XSUAAAuthMiddleware
    before this dependency runs; this function is only concerned with 403 authorization.

    Args:
        endpoint_name: The canonical endpoint name as defined in
            app.auth.principal.VIEWER_ENDPOINTS / ANALYST_ENDPOINTS /
            OPERATOR_ENDPOINTS / ADMIN_ENDPOINTS.
    """
    async def _enforce(
        principal: AuthenticatedPrincipal = Depends(get_principal),
    ) -> AuthenticatedPrincipal:
        # Fail closed: if no principal is available, deny access unconditionally.
        # In production XSUAA mode this path should never be reached because
        # XSUAAAuthMiddleware always sets request.state.principal before the
        # route handler is called.  Defence-in-depth guard.
        if principal is None:
            logger.warning(
                "auth_enforcement_no_principal endpoint=%s — denying access (fail-closed)",
                endpoint_name,
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "FORBIDDEN",
                    "message": "Authorization failed: authentication state is missing.",
                    "endpoint": endpoint_name,
                },
            )

        if not principal.can_access(endpoint_name):
            logger.warning(
                "auth_enforcement_forbidden subject=%s endpoint=%s scopes=%s",
                principal.subject,
                endpoint_name,
                principal.scopes,
            )
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "FORBIDDEN",
                    "message": (
                        "You do not have the required authorization to access this endpoint."
                    ),
                    "endpoint": endpoint_name,
                    "required_capability": endpoint_name,
                },
            )

        return principal

    # Give the inner function a stable name for FastAPI dependency caching.
    _enforce.__name__ = f"require_scope_{endpoint_name}"
    return _enforce
