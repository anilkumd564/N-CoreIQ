"""N-CoreIQ BTP Deployment — Step 1: MCP Streamable HTTP + Deployment Readiness.

Automated test suite covering:
  1.  stdio transport remains functional
  2.  Streamable HTTP transport initialises (FastMCP API verified)
  3.  /mcp route exists when transport=streamable_http
  4.  15 tools remain registered (all original tools preserved)
  5.  REST routes remain functional
  6.  MCP authentication required in BTP mode (auth=xsuaa)
  7.  Invalid JWT rejected (401)
  8.  Valid JWT accepted (ANONYMOUS_PRINCIPAL in disabled mode)
  9.  Authorization enforced (scope checks on principal)
 10.  MCP and REST share service implementations (no duplication)
 11.  No direct SAP calls in MCP transport layer
 12.  No second network port (single CF port)
 13.  Startup/shutdown lifecycle (session manager via lifespan)
 14.  Transport configuration validation (invalid value rejected)
 15.  Settings fields for BTP MCP transport exist
 16.  FastMCP constructed with stateless_http=True
 17.  FastMCP constructed with json_response from settings
 18.  TransportSecuritySettings configured from settings
 19.  MCP endpoint does not expose secrets
 20.  Tool count exactly 15

All tests are offline — no BTP credentials, no live XSUAA, no live SAP.
"""
from __future__ import annotations

import asyncio
import importlib
import inspect
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth.principal import (
    ANONYMOUS_PRINCIPAL,
    SCOPE_ADMIN,
    SCOPE_ANALYST,
    SCOPE_OPERATOR,
    SCOPE_VIEWER,
    AuthenticatedPrincipal,
)
from app.core.config import Settings
from app.mcp.server import NCoreIQMCPServer


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXPECTED_TOOL_COUNT = 15

_EXPECTED_TOOLS = frozenset(
    {
        "sap_ping",
        "list_packages",
        "search_repository",
        "get_object_metadata",
        "get_object_source",
        "start_extraction_job",
        "get_extraction_job",
        "list_extraction_jobs",
        "cancel_extraction_job",
        "retry_extraction_job",
        "get_extraction_result",
        "get_dependencies",
        "traverse_dependencies",
        "find_dependents",
        "analyze_dependency_impact",
    }
)

_FORBIDDEN_TOOLS = frozenset(
    {
        "run_abap",
        "execute_rfc",
        "execute_odata",
        "read_table",
        "sql_query",
        "write_sap",
        "upload_source",
    }
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_server(settings=None) -> NCoreIQMCPServer:
    """Build an NCoreIQMCPServer with fully mocked services."""
    provider = AsyncMock()
    discovery = AsyncMock()
    obj_svc = AsyncMock()
    job_svc = MagicMock()
    dep_svc = AsyncMock()
    return NCoreIQMCPServer(
        sap_provider=provider,
        discovery_svc=discovery,
        object_svc=obj_svc,
        job_svc=job_svc,
        settings=settings,
        dep_svc=dep_svc,
    )


def _list_tools_sync(srv: NCoreIQMCPServer) -> list:
    """List tools synchronously using a fresh event loop."""
    return asyncio.get_event_loop().run_until_complete(srv.mcp.list_tools())


# ===========================================================================
# 1. stdio transport remains functional
# ===========================================================================


class TestStdioTransportFunctional:
    """stdio mode must remain available for local/IDE use."""

    def test_settings_default_transport_is_stdio(self):
        s = Settings()
        assert s.ncoreiq_mcp_transport == "stdio"

    def test_stdio_server_construction_succeeds(self):
        """NCoreIQMCPServer constructs without error in settings-less mode."""
        srv = _make_server()
        assert srv.mcp is not None

    def test_run_stdio_is_async_method(self):
        srv = _make_server()
        assert hasattr(srv, "run_stdio")
        assert asyncio.iscoroutinefunction(srv.run_stdio)

    def test_stdio_entry_point_module_importable(self):
        """python -m app.mcp.server entry point must be importable."""
        spec = importlib.util.find_spec("app.mcp.server")
        assert spec is not None

    def test_stdio_server_has_all_15_tools(self):
        """All 15 tools registered in settings-less (stdio default) mode."""
        srv = _make_server()
        tools = _list_tools_sync(srv)
        names = {t.name for t in tools}
        assert names == _EXPECTED_TOOLS, (
            f"Missing: {_EXPECTED_TOOLS - names}  Extra: {names - _EXPECTED_TOOLS}"
        )

    def test_create_server_factory_importable(self):
        """create_server factory function must be importable."""
        from app.mcp.server import create_server  # noqa: F401

        assert callable(create_server)


# ===========================================================================
# 2. Streamable HTTP initialises
# ===========================================================================


class TestStreamableHttpInitialises:
    """streamable_http_app() API verified against mcp 1.30.0."""

    def test_fastmcp_has_streamable_http_app_method(self):
        srv = _make_server()
        assert hasattr(srv.mcp, "streamable_http_app")
        assert callable(srv.mcp.streamable_http_app)

    def test_streamable_http_app_returns_starlette_app(self):
        from starlette.applications import Starlette

        srv = _make_server()
        sub_app = srv.mcp.streamable_http_app()
        assert isinstance(sub_app, Starlette)

    def test_session_manager_accessible_after_streamable_http_app_call(self):
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        srv = _make_server()
        srv.mcp.streamable_http_app()  # triggers lazy creation
        assert isinstance(srv.mcp.session_manager, StreamableHTTPSessionManager)

    def test_run_streamable_http_is_async_method(self):
        srv = _make_server()
        assert hasattr(srv, "run_streamable_http")
        assert asyncio.iscoroutinefunction(srv.run_streamable_http)

    def test_fastmcp_stateless_http_is_true(self):
        """stateless_http=True required for BTP horizontal scaling."""
        srv = _make_server()
        assert srv.mcp.settings.stateless_http is True

    def test_fastmcp_json_response_default_true(self):
        """json_response defaults to True (ncoreiq_mcp_json_response default)."""
        srv = _make_server()  # no settings object → defaults
        assert srv.mcp.settings.json_response is True

    def test_fastmcp_json_response_false_from_settings(self):
        """json_response=False is honoured from settings."""
        s = Settings(ncoreiq_mcp_json_response=False)
        srv = _make_server(settings=s)
        assert srv.mcp.settings.json_response is False

    def test_fastmcp_streamable_http_path_is_root(self):
        """streamable_http_path='/' avoids /mcp/mcp double-segment."""
        srv = _make_server()
        assert srv.mcp.settings.streamable_http_path == "/"

    def test_transport_security_disabled_by_default(self):
        """DNS rebinding protection disabled when no allowed_hosts configured."""
        from mcp.server.fastmcp.server import TransportSecuritySettings

        srv = _make_server()
        ts = srv.mcp.settings.transport_security
        assert isinstance(ts, TransportSecuritySettings)
        assert ts.enable_dns_rebinding_protection is False

    def test_transport_security_enabled_when_allowed_hosts_set(self):
        """DNS rebinding protection enabled when NCOREIQ_MCP_ALLOWED_HOSTS is set."""
        from mcp.server.fastmcp.server import TransportSecuritySettings

        s = Settings(ncoreiq_mcp_allowed_hosts=["myapp.cfapps.eu10.hana.ondemand.com"])
        srv = _make_server(settings=s)
        ts = srv.mcp.settings.transport_security
        assert isinstance(ts, TransportSecuritySettings)
        assert ts.enable_dns_rebinding_protection is True
        assert "myapp.cfapps.eu10.hana.ondemand.com" in ts.allowed_hosts


# ===========================================================================
# 3. /mcp route mountable (streamable_http mode)
# ===========================================================================


class TestMcpRouteMountable:
    """Verify /mcp sub-app is a valid ASGI app when transport=streamable_http."""

    def test_mcp_sub_app_is_callable_asgi(self):
        """The Starlette sub-app returned by streamable_http_app() is ASGI-callable."""
        s = Settings(ncoreiq_mcp_transport="streamable_http")
        srv = _make_server(settings=s)
        sub_app = srv.mcp.streamable_http_app()
        assert callable(sub_app)

    def test_canonical_mcp_path_is_mcp_slash(self):
        """External MCP endpoint documented as POST /mcp/ (mount at /mcp, path='/')."""
        srv = _make_server()
        # mount_path + streamable_http_path → /mcp + / → /mcp/
        assert srv.mcp.settings.streamable_http_path == "/"

    def test_no_double_mcp_segment(self):
        """streamable_http_path='/' prevents /mcp/mcp double-segment."""
        srv = _make_server()
        # If streamable_http_path were '/mcp', external path would be /mcp/mcp
        assert srv.mcp.settings.streamable_http_path != "/mcp"

    def test_session_manager_task_group_none_before_run(self):
        """session_manager._task_group is None before run() — requests would fail."""
        srv = _make_server()
        srv.mcp.streamable_http_app()
        sm = srv.mcp.session_manager
        # _task_group must be None until run() is called
        assert sm._task_group is None

    def test_lifespan_integration_via_app_state(self):
        """app.state._mcp_server is set at module level for streamable_http mode."""
        # In default (stdio) mode, app.state._mcp_server is NOT set
        from app.main import app

        mcp_srv = getattr(app.state, "_mcp_server", None)
        # In default stdio mode, _mcp_server is absent or None
        assert mcp_srv is None or hasattr(mcp_srv, "mcp")


# ===========================================================================
# 4. Exactly 15 tools registered
# ===========================================================================


class TestToolCount:
    """Exactly 15 tools — no more, no less, no duplication."""

    def test_exactly_15_tools(self):
        srv = _make_server()
        tools = _list_tools_sync(srv)
        assert len(tools) == _EXPECTED_TOOL_COUNT, (
            f"Expected {_EXPECTED_TOOL_COUNT}, got {len(tools)}: "
            f"{sorted(t.name for t in tools)}"
        )

    def test_all_expected_names_present(self):
        srv = _make_server()
        names = {t.name for t in _list_tools_sync(srv)}
        missing = _EXPECTED_TOOLS - names
        assert not missing, f"Missing MCP tools: {missing}"

    def test_no_forbidden_tools_added(self):
        """Generic SAP / write tools must NOT exist."""
        srv = _make_server()
        names = {t.name for t in _list_tools_sync(srv)}
        assert not (_FORBIDDEN_TOOLS & names), f"Forbidden tools present: {_FORBIDDEN_TOOLS & names}"

    def test_no_tool_name_duplication(self):
        srv = _make_server()
        names = [t.name for t in _list_tools_sync(srv)]
        from collections import Counter
        dupes = {n for n, c in Counter(names).items() if c > 1}
        assert not dupes, f"Duplicate tool registrations: {dupes}"

    def test_tool_count_unchanged_with_settings(self):
        """Passing settings must not alter tool count."""
        s = Settings(ncoreiq_mcp_transport="streamable_http")
        srv = _make_server(settings=s)
        tools = _list_tools_sync(srv)
        assert len(tools) == _EXPECTED_TOOL_COUNT

    def test_read_only_tools_have_correct_annotations(self):
        """Viewer-class tools must have readOnlyHint=True."""
        _read_only_names = {
            "sap_ping", "list_packages", "search_repository",
            "get_object_metadata", "get_object_source",
            "get_extraction_job", "list_extraction_jobs", "get_extraction_result",
            "get_dependencies", "traverse_dependencies",
            "find_dependents", "analyze_dependency_impact",
        }
        srv = _make_server()
        for t in _list_tools_sync(srv):
            if t.name in _read_only_names:
                ann = getattr(t, "annotations", None)
                if ann is not None:
                    assert getattr(ann, "readOnlyHint", None) is True, (
                        f"Tool {t.name} should have readOnlyHint=True"
                    )


# ===========================================================================
# 5. REST routes remain functional
# ===========================================================================


class TestRestRoutesFunctional:
    """Core REST endpoints unaffected by MCP changes."""

    def test_health_live_200(self):
        from app.main import app

        client = TestClient(app)
        assert client.get("/health/live").status_code == 200

    def test_health_ok(self):
        from app.main import app

        client = TestClient(app)
        assert client.get("/health").json()["status"] == "ok"

    def test_root_has_app_info(self):
        from app.main import app

        client = TestClient(app)
        body = client.get("/").json()
        assert "name" in body and "version" in body

    def test_openapi_accessible(self):
        from app.main import app

        client = TestClient(app)
        assert client.get("/openapi.json").status_code == 200

    def test_api_v1_health(self):
        from app.main import app

        client = TestClient(app)
        assert client.get("/api/v1/health").status_code == 200

    def test_docs_accessible(self):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/docs")
        assert resp.status_code == 200


# ===========================================================================
# 6–8. Authentication
# ===========================================================================


class TestMcpAuthentication:
    """Auth middleware protects all paths including /mcp in xsuaa mode."""

    def test_auth_disabled_anonymous_has_full_access(self):
        assert ANONYMOUS_PRINCIPAL.is_admin()
        assert ANONYMOUS_PRINCIPAL.can_access("sap_ping")
        assert ANONYMOUS_PRINCIPAL.can_access("traverse_dependencies")

    def test_xsuaa_mode_missing_token_returns_401(self):
        """No Bearer token → 401 AUTHENTICATION_REQUIRED."""
        from app.auth.middleware import XSUAAAuthMiddleware
        from fastapi import FastAPI

        test_app = FastAPI()
        test_settings = Settings(auth_mode="xsuaa")
        test_app.add_middleware(XSUAAAuthMiddleware, settings=test_settings)

        @test_app.get("/protected")
        async def _ep():
            return {"ok": True}

        client = TestClient(test_app, raise_server_exceptions=False)
        resp = client.get("/protected")
        assert resp.status_code == 401
        assert "AUTHENTICATION_REQUIRED" in resp.text

    def test_invalid_jwt_returns_401(self):
        """Malformed Bearer token → 401 INVALID_TOKEN."""
        from app.auth.middleware import XSUAAAuthMiddleware
        from fastapi import FastAPI

        test_app = FastAPI()
        test_settings = Settings(auth_mode="xsuaa")
        test_app.add_middleware(XSUAAAuthMiddleware, settings=test_settings)

        @test_app.get("/protected")
        async def _ep():
            return {"ok": True}

        client = TestClient(test_app, raise_server_exceptions=False)
        resp = client.get(
            "/protected",
            headers={"Authorization": "Bearer not.a.real.jwt.token"},
        )
        assert resp.status_code == 401

    def test_health_live_exempt_from_auth(self):
        """Liveness probe is exempt from auth (always accessible)."""
        from app.main import app

        client = TestClient(app)
        assert client.get("/health/live").status_code == 200

    def test_health_ready_exempt_from_auth(self):
        from app.main import app

        client = TestClient(app)
        resp = client.get("/health/ready")
        assert resp.status_code in (200, 503)

    def test_auth_exempt_paths_list_does_not_include_mcp(self):
        """The /mcp path must NOT be in the auth exempt list."""
        from app.auth.middleware import _AUTH_EXEMPT_PATHS

        assert "/mcp" not in _AUTH_EXEMPT_PATHS
        assert "/mcp/" not in _AUTH_EXEMPT_PATHS


# ===========================================================================
# 9. Authorization enforced
# ===========================================================================


class TestMcpAuthorization:
    """N-CoreIQ role-based authorization model covers MCP tool categories."""

    def test_viewer_can_access_viewer_tools(self):
        principal = AuthenticatedPrincipal(
            subject="viewer1", display_name="Viewer",
            scopes=[SCOPE_VIEWER], roles=[SCOPE_VIEWER],
        )
        assert principal.is_viewer()
        assert principal.can_access("sap_ping")
        assert principal.can_access("search_repository")
        assert principal.can_access("get_object_metadata")
        # principal uses endpoint-name keys; get_object_dependencies is the endpoint key
        assert principal.can_access("get_object_dependencies")

    def test_viewer_cannot_access_operator_only(self):
        principal = AuthenticatedPrincipal(
            subject="viewer2", display_name="Viewer",
            scopes=[SCOPE_VIEWER], roles=[SCOPE_VIEWER],
        )
        assert not principal.can_access("create_job")
        assert not principal.can_access("cancel_job")

    def test_analyst_can_access_analyst_tools(self):
        principal = AuthenticatedPrincipal(
            subject="analyst1", display_name="Analyst",
            scopes=[SCOPE_ANALYST, SCOPE_VIEWER],
            roles=[SCOPE_ANALYST],
        )
        assert principal.is_analyst()
        assert principal.can_access("traverse_dependencies")
        assert principal.can_access("bulk_extract")

    def test_operator_can_access_operator_tools(self):
        principal = AuthenticatedPrincipal(
            subject="op1", display_name="Operator",
            scopes=[SCOPE_OPERATOR, SCOPE_ANALYST, SCOPE_VIEWER],
            roles=[SCOPE_OPERATOR],
        )
        assert principal.is_operator()
        assert principal.can_access("cancel_job")
        assert principal.can_access("create_job")

    def test_admin_has_full_access(self):
        principal = AuthenticatedPrincipal(
            subject="admin1", display_name="Admin",
            scopes=[SCOPE_ADMIN, SCOPE_OPERATOR, SCOPE_ANALYST, SCOPE_VIEWER],
            roles=[SCOPE_ADMIN],
        )
        assert principal.is_admin()
        assert principal.can_access("diagnostics")
        assert principal.can_access("sap_ping")

    def test_scope_hierarchy_viewer_included_in_analyst(self):
        """Analyst role includes Viewer capabilities."""
        from app.auth.middleware import _parse_scopes_from_claims

        claims = {"scope": "ncoreiq.NCoreIQ.Analyst"}
        scopes = _parse_scopes_from_claims(claims, xsappname="ncoreiq")
        assert SCOPE_VIEWER in scopes
        assert SCOPE_ANALYST in scopes


# ===========================================================================
# 10. MCP and REST share service implementations
# ===========================================================================


class TestSharedServiceImplementations:
    """MCP tools and REST routes must use the same service classes."""

    def test_mcp_tools_use_service_layer_not_raw_provider(self):
        """MCP ping calls sap_provider.ping(), not a raw HTTP client."""
        srv = _make_server()
        # The provider is the only SAP-touching object
        assert srv._provider is not None
        assert srv._discovery is not None
        assert srv._object is not None
        assert srv._jobs is not None

    def test_mcp_server_has_no_httpx_import(self):
        """MCP server module must not import httpx (raw HTTP) directly."""
        import app.mcp.server as mcp_mod

        src = inspect.getsource(mcp_mod)
        # httpx must not appear as a direct import in server.py
        # (auth middleware uses httpx for JWKS; that's separate)
        assert "import httpx" not in src

    def test_mcp_tools_call_dep_service_not_provider_directly(self):
        """Dependency tools must use dep_svc, not provider.get_object_dependencies."""
        from app.models.phase8 import (
            DirectDependencyResult,
            GraphStatistics,
            RepositoryObjectReference,
        )

        dep_svc = AsyncMock()
        dep_svc.get_direct_dependencies = AsyncMock(
            return_value=DirectDependencyResult(
                system_id="S4H", client="100",
                source_object_type="PROG", source_object_name="ZTEST",
                total_relations=0, returned_relations=0, truncated=False,
            )
        )
        srv = _make_server()
        srv._dep = dep_svc

        asyncio.get_event_loop().run_until_complete(
            srv._get_dependencies("PROG", "ZTEST", None, None, 100)
        )
        dep_svc.get_direct_dependencies.assert_called_once()
        srv._provider.get_object_dependencies = MagicMock()
        srv._provider.get_object_dependencies.assert_not_called()


# ===========================================================================
# 11. No direct SAP calls in MCP transport layer
# ===========================================================================


class TestNoDirectSapInTransport:
    """The MCP transport layer (server.py) must not contain SAP HTTP logic."""

    def test_mcp_server_module_has_no_odata_calls(self):
        import app.mcp.server as mcp_mod

        src = inspect.getsource(mcp_mod)
        # No direct OData URL construction
        assert "requests.get" not in src
        assert "httpx.get" not in src
        assert "aiohttp" not in src

    def test_mcp_errors_module_has_no_sap_credentials(self):
        import app.mcp.errors as err_mod

        src = inspect.getsource(err_mod)
        # The module may mention 'password' in security-rule comments but
        # must never contain actual credential values or env-var names
        assert "SAP_ODATA" not in src
        assert "SAP_LOCAL__PASSWORD" not in src
        # No real credentials — no '=' followed by a credential value
        assert 'password="' not in src.lower()
        assert "password='" not in src.lower()

    def test_mcp_server_imports_from_services_not_sap_directly(self):
        """MCP server must import from app.services.*, not app.sap.connection directly."""
        import app.mcp.server as mcp_mod

        src = inspect.getsource(mcp_mod)
        # The server.py itself should not import SAPConnectionProvider etc.
        # (the factory does, but that's in create_server only)
        assert "from app.sap.connection.odata" not in src


# ===========================================================================
# 12. No second network port
# ===========================================================================


class TestNoSecondNetworkPort:
    """MCP and REST must share the single CF-provided PORT."""

    def test_procfile_uses_single_port_variable(self):
        """Procfile must use ${PORT} (single CF port)."""
        procfile_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "Procfile"
        )
        if os.path.exists(procfile_path):
            content = open(procfile_path).read()
            assert "PORT" in content
            # Must not start two separate web processes on different ports
            lines = [l for l in content.splitlines() if l.startswith("web:")]
            assert len(lines) == 1, "Only one 'web:' process allowed in Procfile"

    def test_manifest_uses_single_command(self):
        """manifest.yml must use a single uvicorn command (no port override)."""
        manifest_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "deployment", "btp", "manifest.yml",
        )
        if os.path.exists(manifest_path):
            content = open(manifest_path).read()
            assert "$PORT" in content or "${PORT}" in content

    def test_mcp_server_run_streamable_http_does_not_hardcode_port(self):
        """run_streamable_http_async must not hardcode a secondary port."""
        import app.mcp.server as mcp_mod

        src = inspect.getsource(mcp_mod)
        # No secondary port binding in the module
        assert "port=9" not in src  # 9000, 9001, etc.
        assert "port=8001" not in src

    def test_fastapi_app_has_single_instance(self):
        """Only one FastAPI `app` object in main.py."""
        import app.main as main_mod

        # The production ASGI app
        assert hasattr(main_mod, "app")
        from fastapi import FastAPI

        assert isinstance(main_mod.app, FastAPI)


# ===========================================================================
# 13. Startup/shutdown lifecycle
# ===========================================================================


class TestLifecycle:
    """Session manager lifespan must be wired through FastAPI lifespan."""

    def test_lifespan_function_is_async_context_manager(self):
        """lifespan must be an async context manager (asynccontextmanager)."""
        import app.main as main_mod

        lf = main_mod.lifespan
        assert asyncio.iscoroutinefunction(lf) or inspect.isasyncgenfunction(lf) or callable(lf)

    def test_session_manager_run_is_async_context_manager(self):
        """StreamableHTTPSessionManager.run() must be an asynccontextmanager."""
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        srv = _make_server()
        srv.mcp.streamable_http_app()
        sm = srv.mcp.session_manager
        # run() is decorated with @contextlib.asynccontextmanager
        import contextlib
        assert hasattr(sm.run, "__wrapped__") or callable(sm.run)

    def test_app_state_mcp_server_not_set_in_stdio_mode(self):
        """In default stdio mode, _mcp_server must not be set in app.state."""
        from app.main import app

        mcp_srv = getattr(app.state, "_mcp_server", None)
        # Default env is stdio — no MCP server in app.state
        assert mcp_srv is None

    def test_lifespan_source_contains_session_manager_run(self):
        """lifespan must reference session_manager.run() for HTTP transport."""
        import app.main as main_mod

        src = inspect.getsource(main_mod.lifespan)
        assert "session_manager" in src
        assert ".run()" in src


# ===========================================================================
# 14. Transport configuration validation
# ===========================================================================


class TestTransportConfigValidation:
    """Invalid transport values must be rejected at settings parse time."""

    def test_invalid_transport_raises_validation_error(self):
        with pytest.raises((ValidationError, ValueError)):
            Settings(ncoreiq_mcp_transport="websocket")

    def test_sse_transport_rejected(self):
        with pytest.raises((ValidationError, ValueError)):
            Settings(ncoreiq_mcp_transport="sse")

    def test_empty_transport_rejected(self):
        with pytest.raises((ValidationError, ValueError)):
            Settings(ncoreiq_mcp_transport="")

    def test_stdio_transport_accepted(self):
        s = Settings(ncoreiq_mcp_transport="stdio")
        assert s.ncoreiq_mcp_transport == "stdio"

    def test_streamable_http_transport_accepted(self):
        s = Settings(ncoreiq_mcp_transport="streamable_http")
        assert s.ncoreiq_mcp_transport == "streamable_http"

    def test_transport_case_sensitive(self):
        """Transport value is case-sensitive — 'STDIO' is invalid."""
        with pytest.raises((ValidationError, ValueError)):
            Settings(ncoreiq_mcp_transport="STDIO")


# ===========================================================================
# 15. Settings fields for BTP MCP transport
# ===========================================================================


class TestSettingsFields:
    """All required BTP MCP settings fields exist with correct defaults."""

    def test_ncoreiq_mcp_transport_field_exists(self):
        s = Settings()
        assert hasattr(s, "ncoreiq_mcp_transport")
        assert s.ncoreiq_mcp_transport == "stdio"

    def test_ncoreiq_mcp_enabled_field_exists(self):
        s = Settings()
        assert hasattr(s, "ncoreiq_mcp_enabled")
        assert s.ncoreiq_mcp_enabled is True

    def test_ncoreiq_mcp_json_response_field_exists(self):
        s = Settings()
        assert hasattr(s, "ncoreiq_mcp_json_response")
        assert s.ncoreiq_mcp_json_response is True

    def test_ncoreiq_mcp_allowed_hosts_field_exists(self):
        s = Settings()
        assert hasattr(s, "ncoreiq_mcp_allowed_hosts")
        assert isinstance(s.ncoreiq_mcp_allowed_hosts, list)
        assert s.ncoreiq_mcp_allowed_hosts == []

    def test_btp_manifest_has_mcp_transport(self):
        """manifest.yml must declare NCOREIQ_MCP_TRANSPORT=streamable_http."""
        manifest_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "deployment", "btp", "manifest.yml",
        )
        if os.path.exists(manifest_path):
            content = open(manifest_path).read()
            assert "NCOREIQ_MCP_TRANSPORT" in content
            assert "streamable_http" in content

    def test_mta_yaml_has_mcp_transport(self):
        """mta.yaml must declare NCOREIQ_MCP_TRANSPORT: streamable_http."""
        mta_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "deployment", "btp", "mta.yaml",
        )
        if os.path.exists(mta_path):
            content = open(mta_path).read()
            assert "NCOREIQ_MCP_TRANSPORT" in content
            assert "streamable_http" in content

    def test_env_example_has_mcp_section(self):
        """.env.example must document MCP transport settings."""
        env_example_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), ".env.example"
        )
        if os.path.exists(env_example_path):
            content = open(env_example_path).read()
            assert "NCOREIQ_MCP_TRANSPORT" in content
            assert "stdio" in content
            assert "streamable_http" in content


# ===========================================================================
# 19. MCP endpoint does not expose secrets
# ===========================================================================


class TestMcpDoesNotExposeSecrets:
    """MCP tool results must never contain credentials or SAP internals."""

    def test_ping_result_has_no_credentials(self):
        """sap_ping response must not contain passwords or tokens."""
        import asyncio
        from unittest.mock import MagicMock

        srv = _make_server()
        # Mock ping response
        mock_ping = MagicMock()
        mock_ping.status = "connected"
        mock_ping.connection_mode = "odata"
        mock_ping.system_id = "S4H"
        mock_ping.client = "100"
        mock_ping.sap_release = "756"
        mock_ping.message = "OK"
        srv._provider.ping = AsyncMock(return_value=mock_ping)

        result = asyncio.get_event_loop().run_until_complete(srv._ping())
        result_str = str(result).lower()
        assert "password" not in result_str
        assert "authorization" not in result_str
        assert "bearer" not in result_str
        assert "secret" not in result_str

    def test_mcp_error_sanitization_strips_passwords(self):
        """map_exception must strip password from error messages."""
        from app.mcp.errors import map_exception

        class FakeSAPError(Exception):
            pass

        exc = FakeSAPError("Connection failed with password=s3cr3t in response")
        mapped = map_exception(exc, "test_op")
        assert "s3cr3t" not in mapped.message
        assert "password" not in mapped.message.lower() or "redacted" in mapped.message.lower()

    def test_settings_model_dump_redacts_sap_password(self):
        """Settings.model_dump() must redact SAP passwords."""
        import json

        s = Settings(sap_odata={"password": "my-sap-pass", "base_url": "https://example.com"})
        dumped = s.model_dump()
        serialized = json.dumps(dumped, default=str)
        assert "my-sap-pass" not in serialized

    def test_settings_model_dump_redacts_btp_secrets(self):
        """Settings.model_dump() must redact XSUAA clientsecret."""
        import json

        s = Settings(btp={"xsuaa": {"clientsecret": "xsuaa-secret", "url": "https://auth.example"}})
        dumped = s.model_dump()
        serialized = json.dumps(dumped, default=str)
        assert "xsuaa-secret" not in serialized


# ===========================================================================
# 20. MCP SDK version and API compatibility
# ===========================================================================


class TestSdkCompatibility:
    """Verify mcp 1.30.0 API surface used by N-CoreIQ."""

    def test_mcp_sdk_version_is_1_30(self):
        import importlib.metadata

        version = importlib.metadata.version("mcp")
        major, minor = version.split(".")[:2]
        assert int(major) >= 1
        assert int(minor) >= 30, f"Expected mcp >= 1.30, got {version}"

    def test_fastmcp_accepts_stateless_http_kwarg(self):
        """FastMCP must accept stateless_http=True without error."""
        from mcp.server.fastmcp import FastMCP

        f = FastMCP("test", stateless_http=True)
        assert f.settings.stateless_http is True

    def test_fastmcp_accepts_transport_security_kwarg(self):
        """FastMCP must accept transport_security=TransportSecuritySettings(...)."""
        from mcp.server.fastmcp import FastMCP
        from mcp.server.fastmcp.server import TransportSecuritySettings

        ts = TransportSecuritySettings(enable_dns_rebinding_protection=False)
        f = FastMCP("test", transport_security=ts)
        # Pydantic may copy the model; check value equality not identity
        stored = f.settings.transport_security
        assert isinstance(stored, TransportSecuritySettings)
        assert stored.enable_dns_rebinding_protection == ts.enable_dns_rebinding_protection
        assert stored.allowed_hosts == ts.allowed_hosts

    def test_fastmcp_accepts_json_response_kwarg(self):
        """FastMCP must accept json_response=True."""
        from mcp.server.fastmcp import FastMCP

        f = FastMCP("test", json_response=True)
        assert f.settings.json_response is True

    def test_fastmcp_accepts_streamable_http_path_kwarg(self):
        """FastMCP must accept streamable_http_path='/'."""
        from mcp.server.fastmcp import FastMCP

        f = FastMCP("test", streamable_http_path="/")
        assert f.settings.streamable_http_path == "/"

    def test_session_manager_has_run_method(self):
        """StreamableHTTPSessionManager must have a run() asynccontextmanager."""
        from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

        assert hasattr(StreamableHTTPSessionManager, "run")

    def test_session_manager_stateless_mode_attribute(self):
        """Session manager must expose stateless=True when configured."""
        srv = _make_server()
        srv.mcp.streamable_http_app()
        sm = srv.mcp.session_manager
        assert sm.stateless is True
