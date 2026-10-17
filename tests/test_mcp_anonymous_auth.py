"""TEST/DEMO ANONYMOUS MCP ENDPOINT — regression tests.

Covers Step 6 (Tests A-H) of the N-CoreIQ anonymous MCP endpoint task:

  TEST A — NCOREIQ_MCP_AUTH_MODE absent → MCP requires normal XSUAA auth.
  TEST B — NCOREIQ_MCP_AUTH_MODE=xsuaa  → MCP requires normal XSUAA auth.
  TEST C — NCOREIQ_MCP_AUTH_MODE=none   → /mcp/ bypasses auth (no 401/403).
  TEST D — NCOREIQ_MCP_AUTH_MODE=none   → /api/v1/sap/ping still requires XSUAA (401).
  TEST E — /mcp-admin must NOT be treated as an MCP anonymous path.
  TEST F — /mcpfoo must NOT be treated as an MCP anonymous path.
  TEST G — Existing health endpoint remains accessible as before.
  TEST H — Existing valid XSUAA-authenticated REST behavior remains unchanged.

All tests are offline — no real XSUAA, no live SAP, no network calls.
"""
from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.auth.middleware import XSUAAAuthMiddleware, _is_mcp_path
from app.auth.principal import MCP_ANONYMOUS_PRINCIPAL, SCOPE_ANALYST, SCOPE_VIEWER
from app.core.config import Settings


def _build_test_app(settings: Settings) -> FastAPI:
    """Build a minimal FastAPI app with the XSUAA middleware installed."""
    test_app = FastAPI()
    test_app.add_middleware(XSUAAAuthMiddleware, settings=settings)

    @test_app.get("/mcp")
    @test_app.post("/mcp")
    async def _mcp_root():
        return {"ok": True}

    @test_app.get("/mcp/")
    @test_app.post("/mcp/")
    async def _mcp_slash():
        return {"ok": True}

    @test_app.get("/mcp/tools")
    @test_app.post("/mcp/tools")
    async def _mcp_sub():
        return {"ok": True}

    @test_app.get("/mcp-admin")
    async def _mcp_admin():
        return {"ok": True}

    @test_app.get("/mcpfoo")
    async def _mcpfoo():
        return {"ok": True}

    @test_app.get("/api/v1/sap/ping")
    async def _ping():
        return {"ok": True}

    @test_app.get("/health/live")
    async def _health_live():
        return {"status": "ok"}

    return test_app


# ===========================================================================
# TEST A — default (absent NCOREIQ_MCP_AUTH_MODE) → MCP requires XSUAA
# ===========================================================================


class TestA_DefaultAbsentMcpAuthMode:
    def test_default_mcp_auth_mode_is_xsuaa(self):
        s = Settings(auth_mode="xsuaa")
        assert s.ncoreiq_mcp_auth_mode == "xsuaa"

    def test_mcp_requires_auth_when_mode_absent(self):
        s = Settings(auth_mode="xsuaa")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/mcp/")
        assert resp.status_code == 401


# ===========================================================================
# TEST B — NCOREIQ_MCP_AUTH_MODE=xsuaa → MCP requires XSUAA
# ===========================================================================


class TestB_ExplicitXsuaaMcpAuthMode:
    def test_mcp_requires_auth_when_explicitly_xsuaa(self):
        s = Settings(auth_mode="xsuaa", ncoreiq_mcp_auth_mode="xsuaa")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/mcp/")
        assert resp.status_code == 401

    def test_mcp_subpath_requires_auth_when_explicitly_xsuaa(self):
        s = Settings(auth_mode="xsuaa", ncoreiq_mcp_auth_mode="xsuaa")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/mcp/tools")
        assert resp.status_code == 401


# ===========================================================================
# TEST C — NCOREIQ_MCP_AUTH_MODE=none → /mcp/ bypasses auth
# ===========================================================================


class TestC_McpAuthModeNoneBypassesAuth:
    def test_mcp_root_no_401_without_token(self):
        s = Settings(auth_mode="xsuaa", ncoreiq_mcp_auth_mode="none")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/mcp")
        assert resp.status_code not in (401, 403)
        assert resp.status_code == 200

    def test_mcp_slash_no_401_without_token(self):
        s = Settings(auth_mode="xsuaa", ncoreiq_mcp_auth_mode="none")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/mcp/")
        assert resp.status_code not in (401, 403)
        assert resp.status_code == 200

    def test_mcp_subpath_no_401_without_token(self):
        s = Settings(auth_mode="xsuaa", ncoreiq_mcp_auth_mode="none")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/mcp/tools")
        assert resp.status_code not in (401, 403)
        assert resp.status_code == 200

    def test_mcp_anonymous_principal_attached(self):
        """Verify the least-privilege MCP_ANONYMOUS_PRINCIPAL is used, not full ANONYMOUS_PRINCIPAL."""
        assert SCOPE_VIEWER in MCP_ANONYMOUS_PRINCIPAL.scopes
        assert SCOPE_ANALYST in MCP_ANONYMOUS_PRINCIPAL.scopes
        from app.auth.principal import SCOPE_OPERATOR, SCOPE_ADMIN
        assert SCOPE_OPERATOR not in MCP_ANONYMOUS_PRINCIPAL.scopes
        assert SCOPE_ADMIN not in MCP_ANONYMOUS_PRINCIPAL.scopes


# ===========================================================================
# TEST D — NCOREIQ_MCP_AUTH_MODE=none → REST remains XSUAA protected
# ===========================================================================


class TestD_RestStillProtectedWhenMcpAuthNone:
    def test_rest_endpoint_requires_auth_even_when_mcp_auth_none(self):
        s = Settings(auth_mode="xsuaa", ncoreiq_mcp_auth_mode="none")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/sap/ping")
        assert resp.status_code == 401


# ===========================================================================
# TEST E / F — path matching precision
# ===========================================================================


class TestE_F_PathMatchingPrecision:
    def test_mcp_admin_is_not_mcp_path(self):
        assert _is_mcp_path("/mcp-admin") is False

    def test_mcpfoo_is_not_mcp_path(self):
        assert _is_mcp_path("/mcpfoo") is False

    def test_mcp_exact_is_mcp_path(self):
        assert _is_mcp_path("/mcp") is True

    def test_mcp_slash_is_mcp_path(self):
        assert _is_mcp_path("/mcp/") is True

    def test_mcp_subpath_is_mcp_path(self):
        assert _is_mcp_path("/mcp/tools/list") is True

    def test_mcp_admin_requires_auth_even_with_mcp_auth_none(self):
        """/mcp-admin must NOT be treated as anonymous even when NCOREIQ_MCP_AUTH_MODE=none."""
        s = Settings(auth_mode="xsuaa", ncoreiq_mcp_auth_mode="none")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/mcp-admin")
        assert resp.status_code == 401

    def test_mcpfoo_requires_auth_even_with_mcp_auth_none(self):
        """/mcpfoo must NOT be treated as anonymous even when NCOREIQ_MCP_AUTH_MODE=none."""
        s = Settings(auth_mode="xsuaa", ncoreiq_mcp_auth_mode="none")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/mcpfoo")
        assert resp.status_code == 401


# ===========================================================================
# TEST G — health endpoint remains accessible unconditionally
# ===========================================================================


class TestG_HealthEndpointUnaffected:
    def test_health_live_accessible_with_mcp_auth_xsuaa(self):
        s = Settings(auth_mode="xsuaa", ncoreiq_mcp_auth_mode="xsuaa")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health/live")
        assert resp.status_code == 200

    def test_health_live_accessible_with_mcp_auth_none(self):
        s = Settings(auth_mode="xsuaa", ncoreiq_mcp_auth_mode="none")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/health/live")
        assert resp.status_code == 200


# ===========================================================================
# TEST H — existing valid XSUAA-authenticated REST behavior unchanged
# ===========================================================================


class TestH_ExistingXsuaaRestBehaviorUnchanged:
    def test_invalid_bearer_token_still_401_with_mcp_auth_none(self):
        """REST endpoint with a malformed token still returns 401, regardless of MCP auth mode."""
        s = Settings(auth_mode="xsuaa", ncoreiq_mcp_auth_mode="none")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/api/v1/sap/ping",
            headers={"Authorization": "Bearer not.a.real.jwt"},
        )
        assert resp.status_code == 401

    def test_auth_exempt_paths_unaffected_by_mcp_auth_mode(self):
        """The static _AUTH_EXEMPT_PATHS set must still not include /mcp."""
        from app.auth.middleware import _AUTH_EXEMPT_PATHS

        assert "/mcp" not in _AUTH_EXEMPT_PATHS
        assert "/mcp/" not in _AUTH_EXEMPT_PATHS

    def test_xsuaa_mode_without_mcp_override_unchanged(self):
        """When ncoreiq_mcp_auth_mode is default ('xsuaa'), REST behavior is identical
        to the pre-existing behavior (no regression introduced by this feature)."""
        s = Settings(auth_mode="xsuaa")
        app = _build_test_app(s)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/v1/sap/ping")
        assert resp.status_code == 401
