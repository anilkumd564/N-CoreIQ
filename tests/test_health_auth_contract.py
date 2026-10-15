"""Gate 0E10-R4-A1-R1 — Health contract and authorization regression tests.

Proves:
  1. GET /health/live  → HTTP 200 with no Authorization header (public)
  2. GET /health/ready → HTTP 200 or 503 (never 401/403) — always anonymous-accessible
  3. POST /api/v1/repository/search in XSUAA mode without token → HTTP 401
  4. POST /api/v1/repository/search authenticated but no scope → HTTP 403

Authentication model:
  Health endpoints (/health/live, /health/ready) are in _AUTH_EXEMPT_PATHS.
  The middleware injects ANONYMOUS_PRINCIPAL and bypasses all auth checks.
  The route handlers have no require_scope() dependency.
  Result: always HTTP 200 regardless of Authorization header presence.

  Protected API endpoints have require_scope() applied.
  In XSUAA mode: no token → middleware returns 401 before handler executes.
  With an authenticated no-scope principal: require_scope() raises 403.

HEALTH_AUTH_REPAIR_REQUIRED = NO
  Gate A1 did not modify app/api/health.py, app/auth/middleware.py, or
  app/main.py. /health/live and /health/ready were and remain public.
"""
from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

os.environ.setdefault("NCOREIQ_DISABLE_EXECUTOR", "true")

from app.auth.middleware import (
    XSUAAAuthMiddleware,
    _AUTH_EXEMPT_PATHS,
    get_principal,
)
from app.auth.principal import (
    ANONYMOUS_PRINCIPAL,
    SCOPE_VIEWER,
    AuthenticatedPrincipal,
)
from app.main import app


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _no_scope_principal() -> AuthenticatedPrincipal:
    return AuthenticatedPrincipal(
        subject="no-scope-test",
        display_name="No Scope",
        scopes=[],
        roles=[],
    )


def _make_xsuaa_test_app() -> FastAPI:
    """Minimal FastAPI app with XSUAA middleware for 401 testing.

    The middleware 401 code path only needs auth_mode='xsuaa' and no Bearer
    token — no JWKS fetch is attempted when the token is absent.
    """
    mini = FastAPI(title="xsuaa-test-app")

    @mini.post("/api/v1/repository/search")
    async def _search():  # pragma: no cover
        return {"status": "ok"}

    mock_settings = MagicMock()
    mock_settings.auth.mode = "xsuaa"
    mock_settings.auth_mode = "xsuaa"
    mock_settings.auth.cors_origins = []
    mock_settings.btp.xsuaa.url = None
    mock_settings.btp.xsuaa.xsappname = None

    mini.add_middleware(XSUAAAuthMiddleware, settings=mock_settings)
    return mini


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    yield
    app.dependency_overrides.pop(get_principal, None)


# ===========================================================================
# 1. Health endpoints — actual HTTP, no Authorization header
# ===========================================================================


class TestHealthPublicContract:
    """GET /health/live and /health/ready must always be reachable without auth."""

    def test_health_live_anonymous_returns_200(self):
        """GET /health/live without Authorization → HTTP 200."""
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health/live")
        assert resp.status_code == 200, (
            f"Anonymous /health/live returned {resp.status_code}. "
            "Expected 200. Liveness probe must be public."
        )
        assert resp.json().get("status") == "alive"

    def test_health_ready_anonymous_not_401_or_403(self):
        """GET /health/ready without Authorization must not return 401 or 403.

        In test mode job_service may not initialize; 200 or 503 are both
        acceptable.  A 401 or 403 is a critical failure.
        """
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health/ready")
        assert resp.status_code not in (401, 403), (
            f"Anonymous /health/ready returned {resp.status_code}. "
            "Readiness probe must never require a bearer token or scope."
        )
        assert resp.status_code in (200, 503), (
            f"Unexpected /health/ready status: {resp.status_code}. Expected 200 or 503."
        )

    def test_health_live_not_401(self):
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/health/live").status_code != 401

    def test_health_live_not_403(self):
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/health/live").status_code != 403

    def test_health_ready_not_401(self):
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/health/ready").status_code != 401

    def test_health_ready_not_403(self):
        client = TestClient(app, raise_server_exceptions=False)
        assert client.get("/health/ready").status_code != 403


# ===========================================================================
# 2. Middleware exempt-path static contract
# ===========================================================================


class TestMiddlewareExemptPaths:
    """Static assertions on _AUTH_EXEMPT_PATHS configuration."""

    def test_health_live_in_exempt_paths(self):
        assert "/health/live" in _AUTH_EXEMPT_PATHS, (
            "/health/live is NOT in _AUTH_EXEMPT_PATHS — liveness probe will require auth."
        )

    def test_health_ready_in_exempt_paths(self):
        assert "/health/ready" in _AUTH_EXEMPT_PATHS, (
            "/health/ready is NOT in _AUTH_EXEMPT_PATHS — readiness probe will require auth."
        )

    def test_api_v1_health_in_exempt_paths(self):
        assert "/api/v1/health" in _AUTH_EXEMPT_PATHS

    def test_protected_search_not_in_exempt_paths(self):
        assert "/api/v1/repository/search" not in _AUTH_EXEMPT_PATHS, (
            "/api/v1/repository/search is in _AUTH_EXEMPT_PATHS — search endpoint is public."
        )

    def test_protected_jobs_not_in_exempt_paths(self):
        assert "/api/v1/repository/jobs" not in _AUTH_EXEMPT_PATHS

    def test_protected_sap_ping_not_in_exempt_paths(self):
        assert "/api/v1/sap/ping" not in _AUTH_EXEMPT_PATHS


# ===========================================================================
# 3. XSUAA mode — no token → 401
# ===========================================================================


class TestXSUAAModeUnauthenticated:
    """In XSUAA mode, missing Bearer token → HTTP 401 before handler executes."""

    def test_search_without_token_returns_401_in_xsuaa_mode(self):
        client = TestClient(_make_xsuaa_test_app(), raise_server_exceptions=False)
        resp = client.post("/api/v1/repository/search", json={"all_custom_objects": True})
        assert resp.status_code == 401, (
            f"Expected 401 in XSUAA mode without token, got {resp.status_code}."
        )
        body = resp.json()
        assert "error" in body
        assert body["error"] in ("AUTHENTICATION_REQUIRED", "INVALID_TOKEN")

    def test_401_has_www_authenticate_header(self):
        client = TestClient(_make_xsuaa_test_app(), raise_server_exceptions=False)
        resp = client.post("/api/v1/repository/search", json={"all_custom_objects": True})
        assert resp.status_code == 401
        assert "WWW-Authenticate" in resp.headers

    def test_health_paths_are_exempt_not_protected(self):
        """Exempt paths bypass the XSUAA 401 path — confirmed by static inspection."""
        assert "/health/live" in _AUTH_EXEMPT_PATHS
        assert "/health/ready" in _AUTH_EXEMPT_PATHS
        assert "/api/v1/repository/search" not in _AUTH_EXEMPT_PATHS


# ===========================================================================
# 4. Authenticated but insufficient scope → 403
# ===========================================================================


class TestInsufficientScopeForbidden:
    """Authenticated principal with no NCoreIQ scope → HTTP 403."""

    def test_no_scope_search_returns_403(self):
        app.dependency_overrides[get_principal] = _no_scope_principal
        client = TestClient(app, raise_server_exceptions=False)
        with patch(
            "app.services.repository.RepositoryService.search",
            new_callable=AsyncMock,
        ) as mock_search:
            resp = client.post(
                "/api/v1/repository/search",
                json={"all_custom_objects": True, "max_results": 10},
            )
        assert resp.status_code == 403, (
            f"Expected 403 for no-scope principal on search, got {resp.status_code}."
        )
        mock_search.assert_not_called()

    def test_no_scope_job_create_returns_403(self):
        app.dependency_overrides[get_principal] = _no_scope_principal
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post(
            "/api/v1/repository/jobs",
            json={
                "extraction": {
                    "objects": [{"object_type": "PROG", "object_name": "ZTEST"}],
                    "include_metadata": True,
                    "include_source": False,
                },
            },
        )
        assert resp.status_code == 403

    def test_viewer_scope_search_returns_200(self):
        """Viewer principal IS authorized for search — proves 403 is scope-based."""
        from app.auth.principal import SCOPE_VIEWER

        def _viewer():
            return AuthenticatedPrincipal(
                subject="viewer", display_name="Viewer",
                scopes=[SCOPE_VIEWER], roles=[SCOPE_VIEWER],
            )

        app.dependency_overrides[get_principal] = _viewer
        client = TestClient(app, raise_server_exceptions=False)
        with patch(
            "app.services.repository.RepositoryService.search",
            new_callable=AsyncMock,
        ) as mock_search:
            from app.models.results import (
                RepositorySearchResponse,
                SearchCriteria,
                SearchStatistics,
            )
            mock_search.return_value = RepositorySearchResponse(
                system_id="NC1",
                client="800",
                criteria=SearchCriteria(
                    packages=[], include_subpackages=False,
                    name_patterns=[], object_types=[],
                    all_custom_objects=True, max_results=10,
                ),
                statistics=SearchStatistics(total_matches=0, returned=0),
                truncated=False, message="OK", objects=[],
            )
            resp = client.post(
                "/api/v1/repository/search",
                json={"all_custom_objects": True, "max_results": 10},
            )
        assert resp.status_code == 200, (
            f"Viewer should be authorized for search. Got {resp.status_code}."
        )
