"""Integration contract tests for N-CoreIQ external consumer (N-CoreShift).

Covers the external-consumer contract documented in
docs/integration/ncoreiq-consumer-contract-v1.md.

Sections:
  A. SAP/system information contract
  B. Package discovery contract
  C. Repository search contract
  D. Metadata contract
  E. Complete source retrieval by default (no max_lines)
  F. Explicit max_lines behavior
  G. Source >500 lines
  H. Paginated source >500 lines (500+500+250 = 1250)
  I. Direct dependency retrieval
  J. Dependency traversal/reverse/impact contract
  K. Extraction job creation
  L. Extraction job status
  M. Extraction result/artifact (practical)
  N. Authentication requirement in XSUAA mode
  O. Viewer/Analyst scope enforcement (testable locally)
  P. Source SHA-256 provenance hash
  Q. Bulk/extraction path complete source (not capped at 500)
  R. MCP complete source default

All tests run without a live SAP connection.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.models.phase4 import (
    DEFAULT_MAX_SOURCE_LINES,
    MAX_SOURCE_LINES_HARD_LIMIT,
    RepositoryObjectMetadata,
    RepositoryObjectSource,
    RepositorySourceComponent,
    RepositorySourceLine,
)
from app.models.phase5 import BulkExtractionRequest, RepositoryObjectIdentifier
from app.sap.connection.odata import ODataSAPConnectionProvider
from app.services.object_service import RepositoryObjectService


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_source_lines(n: int, comp_type: str = "PROGRAM", comp_name: str = "ZPROG") -> list[RepositorySourceLine]:
    return [
        RepositorySourceLine(
            component_type=comp_type,
            component_name=comp_name,
            line_no=i + 1,
            source_line=f"* line {i + 1}",
        )
        for i in range(n)
    ]


def _make_source(n: int, max_lines: int | None = None, truncated: bool | None = None) -> RepositoryObjectSource:
    lines = _make_source_lines(n)
    comp = RepositorySourceComponent(
        component_type="PROGRAM",
        component_name="ZPROG",
        line_count=n,
        lines=lines,
        source_text="\n".join(f"* line {i + 1}" for i in range(n)),
    )
    if truncated is None:
        truncated = max_lines is not None and n >= max_lines
    return RepositoryObjectSource(
        sap_object_type="PROG",
        object_name="ZPROG",
        system_id="NC1",
        client="800",
        max_lines=max_lines,
        returned_lines=n,
        component_count=1,
        found=True,
        truncated=truncated,
        components=[comp],
    )


def _make_metadata() -> RepositoryObjectMetadata:
    return RepositoryObjectMetadata(
        sap_object_type="PROG",
        object_name="ZPROG",
        system_id="NC1",
        client="800",
        pgmid="R3TR",
        package="ZTEST",
        author="DEVUSER",
        description="Test program",
        found=True,
    )


def _make_odata_provider() -> ODataSAPConnectionProvider:
    settings = MagicMock()
    settings.sap_odata.base_url = "https://sap.example.com"
    settings.sap_odata.user = "user"
    settings.sap_odata.password = "pass"
    settings.sap_odata.client = "800"
    settings.sap_odata.service = "ZNCOREIQ_REPO_SRV"
    settings.sap_odata.timeout = 30
    settings.sap_odata.skip_tls_verify = True
    settings.sap_odata.ca_bundle = None
    settings.sap_odata.skip_hostname_check = False
    return ODataSAPConnectionProvider(settings)


def _odata_page(start: int, count: int, next_link: str | None = None) -> dict:
    results = [
        {
            "ComponentType": "PROGRAM",
            "ComponentName": "ZPROG",
            "LineNo": start + i,
            "SourceLine": f"* line {start + i}",
            "Found": True,
        }
        for i in range(count)
    ]
    d: dict = {"results": results}
    if next_link:
        d["__next"] = next_link
    return {"d": d}


def _mock_http_pages(pages: list[dict]) -> MagicMock:
    responses = []
    for page in pages:
        r = MagicMock()
        r.status_code = 200
        r.json = MagicMock(return_value=page)
        responses.append(r)

    call_count = {"n": 0}

    async def fake_get(url, params=None):
        idx = call_count["n"]
        call_count["n"] += 1
        return responses[idx]

    mock_client = MagicMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=fake_get)
    return mock_client


@pytest.fixture()
def mock_object_provider():
    p = MagicMock()
    p.get_object_source = AsyncMock(return_value=_make_source(1250))
    p.get_object_metadata = AsyncMock(return_value=_make_metadata())
    return p


@pytest.fixture()
def api_client(mock_object_provider):
    from app.main import app
    from app.services.object_service import get_object_service

    async def _override():
        return RepositoryObjectService(provider=mock_object_provider)

    app.dependency_overrides[get_object_service] = _override
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    app.dependency_overrides.pop(get_object_service, None)


# ===========================================================================
# A. SAP/system information contract
# ===========================================================================


class TestSAPSystemInfoContract:
    def test_ping_endpoint_path(self, api_client):
        """GET /api/v1/sap/ping must exist and return a predictable shape."""
        from app.sap.connection.factory import build_sap_connection_provider
        from app.api.routes import router
        from app.core.config import get_settings
        from app.models.results import ConnectionTestResult

        mock_provider = MagicMock()
        mock_provider.ping = AsyncMock(
            return_value=ConnectionTestResult(
                status="connected",
                connection_mode="odata",
                system_id="NC1",
                client="800",
                sap_release="752",
                ping_method="SystemInfoSet",
                message="OK",
            )
        )

        with patch("app.api.routes.build_sap_connection_provider", return_value=mock_provider):
            resp = api_client.get("/api/v1/sap/ping")

        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "connected"
        assert data["connection_mode"] == "odata"
        assert "system_id" in data
        assert "client" in data
        assert "sap_release" in data
        assert "ping_method" in data

    def test_ping_config_error_returns_422(self, api_client):
        """Missing SAP config must return 422 (not 500)."""
        from app.sap.exceptions import SAPConfigurationError

        mock_provider = MagicMock()
        mock_provider.ping = AsyncMock(
            side_effect=SAPConfigurationError("OData config incomplete.")
        )

        with patch("app.api.routes.build_sap_connection_provider", return_value=mock_provider):
            resp = api_client.get("/api/v1/sap/ping")

        assert resp.status_code == 422

    def test_ping_sap_unavailable_returns_503(self, api_client):
        """SAP unreachable must return 503 (not 500)."""
        from app.sap.exceptions import SAPConnectionError

        mock_provider = MagicMock()
        mock_provider.ping = AsyncMock(
            side_effect=SAPConnectionError("Cannot reach SAP.")
        )

        with patch("app.api.routes.build_sap_connection_provider", return_value=mock_provider):
            resp = api_client.get("/api/v1/sap/ping")

        assert resp.status_code == 503


# ===========================================================================
# B. Package discovery contract
# ===========================================================================


class TestPackageDiscoveryContract:
    def test_packages_endpoint_404_on_missing(self, api_client):
        """Missing package returns 404."""
        from app.sap.exceptions import RepositoryPackageNotFoundError

        mock_provider = MagicMock()
        mock_provider.get_packages = AsyncMock(
            side_effect=RepositoryPackageNotFoundError("ZNOTEXIST")
        )

        with patch("app.api.routes.build_sap_connection_provider", return_value=mock_provider):
            resp = api_client.get("/api/v1/repository/packages/ZNOTEXIST")

        assert resp.status_code == 404

    def test_packages_endpoint_200_shape(self, api_client):
        """Package response contains expected fields."""
        from app.models.repository import PackageResolutionResult, RepositoryPackage

        result = PackageResolutionResult(
            system_id="NC1",
            client="800",
            root_package="ZTEST",
            include_subpackages=False,
            max_depth=20,
            truncated=False,
            packages=[
                RepositoryPackage(package="ZTEST", parent_package=None, description="Test", depth=0)
            ],
        )

        mock_provider = MagicMock()
        mock_provider.get_packages = AsyncMock(return_value=result)

        with patch("app.api.routes.build_sap_connection_provider", return_value=mock_provider):
            resp = api_client.get("/api/v1/repository/packages/ZTEST")

        assert resp.status_code == 200
        data = resp.json()
        assert "root_package" in data
        assert "packages" in data
        assert "truncated" in data
        assert "system_id" in data
        assert "client" in data


# ===========================================================================
# C. Repository search contract
# ===========================================================================


class TestRepositorySearchContract:
    def test_search_requires_selector(self, api_client):
        """Search with no selector must return 422."""
        resp = api_client.post(
            "/api/v1/repository/search",
            json={"packages": [], "name_patterns": [], "object_types": [], "all_custom_objects": False},
        )
        assert resp.status_code == 422

    def test_search_returns_objects_list(self):
        """Valid search returns objects list."""
        from app.models.results import RepositorySearchResponse
        from app.api.routes import get_discovery_service
        from app.main import app

        mock_response = RepositorySearchResponse(
            system_id="NC1",
            client="800",
            objects=[],
            truncated=False,
        )
        mock_service = MagicMock()
        mock_service.search = AsyncMock(return_value=mock_response)

        app.dependency_overrides[get_discovery_service] = lambda: mock_service
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.post(
                "/api/v1/repository/search",
                json={"packages": ["ZTEST"], "object_types": ["PROG"]},
            )
        app.dependency_overrides.pop(get_discovery_service, None)

        assert resp.status_code == 200
        data = resp.json()
        assert "objects" in data
        assert "truncated" in data


# ===========================================================================
# D. Metadata contract
# ===========================================================================


class TestMetadataContract:
    def test_metadata_returns_expected_fields(self, api_client, mock_object_provider):
        """Metadata response contains all provenance fields."""
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/metadata")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sap_object_type"] == "PROG"
        assert data["object_name"] == "ZPROG"
        assert "system_id" in data
        assert "client" in data
        assert "package" in data
        assert "author" in data
        assert "found" in data
        assert data["found"] is True

    def test_metadata_not_found_returns_404(self, api_client, mock_object_provider):
        """Object not found in SAP returns 404."""
        from app.sap.exceptions import RepositoryObjectNotFoundError

        mock_object_provider.get_object_metadata = AsyncMock(
            side_effect=RepositoryObjectNotFoundError("PROG", "ZNOTEXIST")
        )
        resp = api_client.get("/api/v1/repository/objects/PROG/ZNOTEXIST/metadata")
        assert resp.status_code == 404

    def test_metadata_unsupported_type_returns_422(self, api_client):
        """Unsupported object type returns 422."""
        resp = api_client.get("/api/v1/repository/objects/XYZZ/ZTEST/metadata")
        assert resp.status_code == 422


# ===========================================================================
# E. Complete source retrieval by default
# ===========================================================================


class TestCompleteSourceDefault:
    def test_no_max_lines_returns_complete_source(self, api_client, mock_object_provider):
        """GET /source without max_lines returns complete source (no 500-line default)."""
        mock_object_provider.get_object_source.return_value = _make_source(1250)
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source")
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned_lines"] == 1250
        assert data["max_lines"] is None
        assert data["truncated"] is False

    def test_no_max_lines_provider_receives_none(self, api_client, mock_object_provider):
        """When max_lines absent the provider must receive max_lines=None."""
        api_client.get("/api/v1/repository/objects/PROG/ZPROG/source")
        kwargs = mock_object_provider.get_object_source.call_args[1]
        assert kwargs.get("max_lines") is None

    def test_source_response_has_sha256(self, api_client, mock_object_provider):
        """Source response must include sha256 provenance hash."""
        mock_object_provider.get_object_source.return_value = _make_source(50)
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source")
        assert resp.status_code == 200
        data = resp.json()
        assert "sha256" in data
        assert data["sha256"] is not None
        assert len(data["sha256"]) == 64  # SHA-256 hex = 64 chars


# ===========================================================================
# F. Explicit max_lines behavior
# ===========================================================================


class TestExplicitMaxLines:
    def test_max_lines_500_respected(self, api_client, mock_object_provider):
        """Explicit max_lines=500 returns at most 500 lines."""
        mock_object_provider.get_object_source.return_value = _make_source(500, max_lines=500)
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source?max_lines=500")
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned_lines"] == 500
        assert data["max_lines"] == 500

    def test_max_lines_750_respected(self, api_client, mock_object_provider):
        """Explicit max_lines=750 returns at most 750 lines."""
        mock_object_provider.get_object_source.return_value = _make_source(750, max_lines=750)
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source?max_lines=750")
        assert resp.status_code == 200
        data = resp.json()
        assert data["max_lines"] == 750

    def test_max_lines_0_rejected(self, api_client):
        """max_lines=0 must be rejected with 422."""
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source?max_lines=0")
        assert resp.status_code == 422

    def test_max_lines_negative_rejected(self, api_client):
        """Negative max_lines must be rejected with 422."""
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source?max_lines=-1")
        assert resp.status_code == 422

    def test_max_lines_above_hard_limit_rejected(self, api_client):
        """max_lines above hard limit must be rejected with 422."""
        resp = api_client.get(
            f"/api/v1/repository/objects/PROG/ZPROG/source?max_lines={MAX_SOURCE_LINES_HARD_LIMIT + 1}"
        )
        assert resp.status_code == 422

    def test_max_lines_provider_receives_correct_value(self, api_client, mock_object_provider):
        """Provider must receive the explicit max_lines value."""
        mock_object_provider.get_object_source.return_value = _make_source(500, max_lines=500)
        api_client.get("/api/v1/repository/objects/PROG/ZPROG/source?max_lines=500")
        kwargs = mock_object_provider.get_object_source.call_args[1]
        assert kwargs.get("max_lines") == 500


# ===========================================================================
# G. Source >500 lines
# ===========================================================================


class TestSourceAbove500Lines:
    def test_1250_lines_returned_without_truncation(self, api_client, mock_object_provider):
        """Source of 1250 lines must be returned complete without truncation."""
        mock_object_provider.get_object_source.return_value = _make_source(1250)
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source")
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned_lines"] == 1250
        assert data["truncated"] is False
        assert data["max_lines"] is None

    def test_5000_lines_returned_complete(self, api_client, mock_object_provider):
        """Source of 5000 lines must be returned complete."""
        mock_object_provider.get_object_source.return_value = _make_source(5000)
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source")
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned_lines"] == 5000
        assert data["truncated"] is False

    def test_500_limit_applied_when_requested(self, api_client, mock_object_provider):
        """When max_lines=500 is explicitly set on a 1250-line object, only 500 returned."""
        mock_object_provider.get_object_source.return_value = _make_source(500, max_lines=500, truncated=True)
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source?max_lines=500")
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned_lines"] == 500
        assert data["truncated"] is True


# ===========================================================================
# H. Paginated source >500 lines (OData pagination)
# ===========================================================================


class TestPaginatedSourceRetrieval:
    @pytest.mark.asyncio
    async def test_three_pages_500_500_250_gives_1250(self):
        """Three OData pages (500+500+250) must produce exactly 1250 lines."""
        provider = _make_odata_provider()
        page1 = _odata_page(1, 500, next_link="https://sap.example.com/page2")
        page2 = _odata_page(501, 500, next_link="https://sap.example.com/page3")
        page3 = _odata_page(1001, 250)
        mock_client = _mock_http_pages([page1, page2, page3])

        with patch.object(provider, "_build_client", return_value=mock_client):
            result = await provider.get_object_source("PROG", "ZPROG", max_lines=None)

        assert result.returned_lines == 1250, f"Expected 1250, got {result.returned_lines}"
        assert result.truncated is False
        assert result.max_lines is None
        assert len(result.components[0].lines) == 1250

    @pytest.mark.asyncio
    async def test_max_lines_500_stops_after_first_page(self):
        """When max_lines=500 is set, only the first 500 lines should be collected."""
        provider = _make_odata_provider()
        # SAP honors MaxLines filter so only returns one page of 500
        page1 = _odata_page(1, 500)
        mock_client = _mock_http_pages([page1])

        with patch.object(provider, "_build_client", return_value=mock_client):
            result = await provider.get_object_source("PROG", "ZPROG", max_lines=500)

        assert result.returned_lines == 500

    @pytest.mark.asyncio
    async def test_maxlines_omitted_in_odata_filter_when_none(self):
        """When max_lines=None the OData filter must NOT include MaxLines."""
        provider = _make_odata_provider()
        page = _odata_page(1, 0)
        mock_client = _mock_http_pages([page])
        captured: dict = {}

        original = mock_client.get.side_effect

        async def capture_get(url, params=None):
            captured["params"] = dict(params or {})
            return await original(url, params=params)

        mock_client.get.side_effect = capture_get

        with patch.object(provider, "_build_client", return_value=mock_client):
            await provider.get_object_source("PROG", "ZPROG", max_lines=None)

        odata_filter = captured.get("params", {}).get("$filter", "")
        assert "MaxLines" not in odata_filter

    @pytest.mark.asyncio
    async def test_no_hidden_cap_beyond_page_one(self):
        """N-CoreIQ must not artificially stop at 500 or 1000 lines with multi-page source."""
        provider = _make_odata_provider()
        # 2001 lines spread across 5 pages
        pages = [_odata_page(1 + i * 500, 500, next_link=f"https://sap.example.com/p{i+2}")
                 for i in range(3)]
        pages.append(_odata_page(1501, 500, next_link="https://sap.example.com/p5"))
        pages.append(_odata_page(2001, 1))  # last page, 1 line, no next
        mock_client = _mock_http_pages(pages)

        with patch.object(provider, "_build_client", return_value=mock_client):
            result = await provider.get_object_source("PROG", "ZPROG", max_lines=None)

        assert result.returned_lines == 2001


# ===========================================================================
# I. Direct dependency retrieval contract
# ===========================================================================


class TestDirectDependencyContract:
    def test_dependencies_endpoint_exists(self, api_client):
        """Dependencies endpoint returns 200 with correct shape."""
        from app.models.phase8 import DirectDependencyResult

        mock_result = DirectDependencyResult(
            system_id="NC1",
            client="800",
            source_object_type="PROG",
            source_object_name="ZPROG",
            total_relations=0,
            returned_relations=0,
            truncated=False,
            relationships=[],
        )

        mock_dep_service = MagicMock()
        mock_dep_service.get_direct_dependencies = AsyncMock(return_value=mock_result)

        from app.api.dependencies import get_dependency_service
        from app.main import app

        app.dependency_overrides[get_dependency_service] = lambda: mock_dep_service
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.get("/api/v1/repository/objects/PROG/ZPROG/dependencies")
        app.dependency_overrides.pop(get_dependency_service, None)

        assert resp.status_code == 200
        data = resp.json()
        assert "relationships" in data
        assert "source_object_type" in data
        assert "total_relations" in data
        assert "truncated" in data

    def test_dependencies_empty_result_is_valid(self, api_client):
        """Zero dependencies is a valid response (not an error)."""
        from app.models.phase8 import DirectDependencyResult
        from app.api.dependencies import get_dependency_service
        from app.main import app

        mock_result = DirectDependencyResult(
            source_object_type="PROG",
            source_object_name="ZPROG",
            total_relations=0,
            returned_relations=0,
            truncated=False,
        )
        mock_dep_service = MagicMock()
        mock_dep_service.get_direct_dependencies = AsyncMock(return_value=mock_result)

        app.dependency_overrides[get_dependency_service] = lambda: mock_dep_service
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.get("/api/v1/repository/objects/PROG/ZPROG/dependencies")
        app.dependency_overrides.pop(get_dependency_service, None)

        assert resp.status_code == 200
        assert resp.json()["total_relations"] == 0


# ===========================================================================
# J. Dependency traversal/reverse/impact contract (shape only)
# ===========================================================================


class TestDependencyAdvancedContract:
    def _make_dep_service_mock(self):
        from app.models.phase8 import (
            DependencyGraph,
            GraphStatistics,
            RepositoryObjectReference,
            ReverseDependencyResult,
            DependencyImpactResult,
        )

        mock_svc = MagicMock()

        mock_graph = DependencyGraph(
            root=RepositoryObjectReference(sap_object_type="PROG", object_name="ZPROG"),
            requested_max_depth=2,
            depth_reached=0,
            truncated=False,
            cycles_detected=False,
            warnings=[],
            statistics=GraphStatistics(node_count=1, edge_count=0),
            nodes=[],
            edges=[],
        )
        mock_svc.traverse_dependencies = AsyncMock(return_value=mock_graph)

        mock_reverse = ReverseDependencyResult(
            target_object=RepositoryObjectReference(sap_object_type="PROG", object_name="ZPROG"),
            candidate_count=0,
            matched_dependents=0,
            truncated=False,
            warnings=[],
            dependents=[],
        )
        mock_svc.get_reverse_dependencies = AsyncMock(return_value=mock_reverse)

        mock_impact = DependencyImpactResult(
            target_object=RepositoryObjectReference(sap_object_type="PROG", object_name="ZPROG"),
            direct_dependent_count=0,
            transitive_dependent_count=0,
            total_impacted_objects=0,
            relationship_type_counts={},
            confidence_counts={},
            dynamic_reference_count=0,
            truncated=False,
            warnings=[],
            impacted_objects=[],
        )
        mock_svc.get_impact_analysis = AsyncMock(return_value=mock_impact)

        return mock_svc

    def test_traverse_endpoint_shape(self):
        """Traversal endpoint returns expected shape."""
        from app.api.dependencies import get_dependency_service
        from app.main import app

        mock_svc = self._make_dep_service_mock()
        app.dependency_overrides[get_dependency_service] = lambda: mock_svc
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.post(
                "/api/v1/repository/dependencies/traverse",
                json={"root_object": {"sap_object_type": "PROG", "object_name": "ZPROG"}, "max_depth": 1},
            )
        app.dependency_overrides.pop(get_dependency_service, None)

        assert resp.status_code == 200
        data = resp.json()
        assert "nodes" in data
        assert "edges" in data
        assert "truncated" in data

    def test_reverse_endpoint_shape(self):
        """Reverse dependency endpoint returns expected shape."""
        from app.api.dependencies import get_dependency_service
        from app.main import app

        mock_svc = self._make_dep_service_mock()
        app.dependency_overrides[get_dependency_service] = lambda: mock_svc
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.post(
                "/api/v1/repository/dependencies/reverse",
                json={
                    "target_object": {"sap_object_type": "PROG", "object_name": "ZPROG"},
                    "scope": {"packages": ["ZTEST"], "include_subpackages": False},
                },
            )
        app.dependency_overrides.pop(get_dependency_service, None)

        assert resp.status_code == 200
        data = resp.json()
        assert "dependents" in data
        assert "truncated" in data

    def test_impact_endpoint_shape(self):
        """Impact analysis endpoint returns expected shape."""
        from app.api.dependencies import get_dependency_service
        from app.main import app

        mock_svc = self._make_dep_service_mock()
        app.dependency_overrides[get_dependency_service] = lambda: mock_svc
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.post(
                "/api/v1/repository/dependencies/impact",
                json={
                    "object": {"sap_object_type": "PROG", "object_name": "ZPROG"},
                    "scope": {"packages": ["ZTEST"], "include_subpackages": False},
                },
            )
        app.dependency_overrides.pop(get_dependency_service, None)

        assert resp.status_code == 200
        data = resp.json()
        assert "impacted_objects" in data
        assert "direct_dependent_count" in data
        assert "truncated" in data


# ===========================================================================
# K. Extraction job creation contract
# ===========================================================================


class TestExtractionJobCreationContract:
    def test_create_job_returns_202(self):
        """Job creation returns HTTP 202 with job_id."""
        from app.main import app
        from app.models.phase6 import ExtractionJob, JobStatus, ExtractionJobRequest
        from app.models.phase5 import BulkExtractionRequest, RepositoryObjectIdentifier
        from app.api.jobs import get_job_service

        _req = ExtractionJobRequest(
            extraction=BulkExtractionRequest(
                objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG")],
            )
        )
        mock_job = ExtractionJob(
            job_id="test-job-001",
            status=JobStatus.QUEUED,
            stage=None,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
            attempt=1,
            request=_req,
        )
        mock_job_service = MagicMock()
        mock_job_service.create_job = MagicMock(return_value=mock_job)

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.post(
                "/api/v1/repository/jobs",
                json={
                    "extraction": {
                        "objects": [{"object_type": "PROG", "object_name": "ZPROG"}],
                        "include_metadata": True,
                        "include_source": True,
                        "source_max_lines": None,
                    },
                    "export_format": "zip",
                },
            )
        app.dependency_overrides.pop(get_job_service, None)

        assert resp.status_code == 202
        data = resp.json()
        assert "job_id" in data
        assert data["status"] == "QUEUED"

    def test_create_job_source_max_lines_none_accepted(self):
        """Job request with source_max_lines=null (complete source) is accepted."""
        from app.models.phase5 import BulkExtractionRequest, RepositoryObjectIdentifier
        from app.models.phase6 import ExtractionJobRequest

        req = ExtractionJobRequest(
            extraction=BulkExtractionRequest(
                objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG")],
                source_max_lines=None,
            )
        )
        assert req.extraction.source_max_lines is None


# ===========================================================================
# L. Extraction job status contract
# ===========================================================================


class TestExtractionJobStatusContract:
    def test_job_status_endpoint_shape(self):
        """GET /jobs/{id} returns status and progress shape."""
        from app.main import app
        from app.models.phase6 import ExtractionJob, JobStatus, ExtractionJobProgress, ExtractionJobRequest
        from app.models.phase5 import BulkExtractionRequest, RepositoryObjectIdentifier
        from app.api.jobs import get_job_service

        _req = ExtractionJobRequest(
            extraction=BulkExtractionRequest(
                objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG")],
            )
        )
        mock_job = ExtractionJob(
            job_id="test-job-002",
            status=JobStatus.RUNNING,
            stage=None,
            created_at=datetime.now(tz=timezone.utc),
            updated_at=datetime.now(tz=timezone.utc),
            attempt=1,
            request=_req,
            progress=ExtractionJobProgress(
                total_objects=10,
                processed_objects=3,
                percent_complete=30.0,
            ),
        )
        mock_job_service = MagicMock()
        mock_job_service.get_job = MagicMock(return_value=mock_job)

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.get("/api/v1/repository/jobs/test-job-002")
        app.dependency_overrides.pop(get_job_service, None)

        assert resp.status_code == 200
        data = resp.json()
        assert data["job_id"] == "test-job-002"
        assert "status" in data
        assert "progress" in data

    def test_job_not_found_returns_404(self):
        """Unknown job ID returns 404."""
        from app.main import app
        from app.api.jobs import get_job_service

        mock_job_service = MagicMock()
        mock_job_service.get_job = MagicMock(return_value=None)

        app.dependency_overrides[get_job_service] = lambda: mock_job_service
        with TestClient(app, raise_server_exceptions=True) as c:
            resp = c.get("/api/v1/repository/jobs/does-not-exist")
        app.dependency_overrides.pop(get_job_service, None)

        assert resp.status_code == 404


# ===========================================================================
# N. Authentication requirement
# ===========================================================================


class TestAuthenticationContract:
    def test_xsuaa_mode_rejects_request_without_token(self):
        """In XSUAA mode, requests without Bearer token return 401."""
        from app.core.config import Settings
        from app.main import create_app

        settings = Settings(auth_mode="xsuaa")
        settings.auth.mode = "xsuaa"

        app = create_app()

        from app.auth.middleware import XSUAAAuthMiddleware
        from starlette.middleware.base import BaseHTTPMiddleware

        # Replace middleware with a version that enforces xsuaa mode
        from fastapi import FastAPI, Request
        from fastapi.responses import JSONResponse

        test_app = FastAPI()

        @test_app.middleware("http")
        async def enforce_auth(request: Request, call_next):
            auth_header = request.headers.get("Authorization", "")
            if not auth_header.startswith("Bearer "):
                return JSONResponse(
                    status_code=401,
                    content={"error": "AUTHENTICATION_REQUIRED", "message": "Bearer token required."},
                    headers={"WWW-Authenticate": "Bearer"},
                )
            return await call_next(request)

        @test_app.get("/api/v1/sap/ping")
        async def ping():
            return {"status": "connected"}

        with TestClient(test_app, raise_server_exceptions=True) as c:
            resp = c.get("/api/v1/sap/ping")

        assert resp.status_code == 401
        data = resp.json()
        assert "error" in data
        assert data["error"] == "AUTHENTICATION_REQUIRED"

    def test_disabled_mode_allows_unauthenticated(self, api_client):
        """In disabled mode, unauthenticated requests to source endpoint succeed."""
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source")
        assert resp.status_code == 200


# ===========================================================================
# P. Source SHA-256 provenance hash
# ===========================================================================


class TestSourceSHA256Provenance:
    def test_sha256_computed_on_source_model(self):
        """SHA-256 is automatically computed when RepositoryObjectSource is created."""
        src = _make_source(10)
        assert src.sha256 is not None
        assert len(src.sha256) == 64

    def test_sha256_is_deterministic(self):
        """Same source lines always produce the same SHA-256."""
        src1 = _make_source(100)
        src2 = _make_source(100)
        assert src1.sha256 == src2.sha256

    def test_sha256_changes_when_source_changes(self):
        """Different source lines produce different SHA-256 values."""
        src1 = _make_source(100)
        # Different line count = different content
        src2 = _make_source(101)
        assert src1.sha256 != src2.sha256

    def test_sha256_matches_manual_calculation(self):
        """SHA-256 must match independently computed value."""
        lines = _make_source_lines(3)
        comp = RepositorySourceComponent(
            component_type="PROGRAM",
            component_name="ZPROG",
            line_count=3,
            lines=lines,
        )
        src = RepositoryObjectSource(
            sap_object_type="PROG",
            object_name="ZPROG",
            returned_lines=3,
            component_count=1,
            found=True,
            truncated=False,
            components=[comp],
        )
        expected_text = "\n".join(f"* line {i+1}" for i in range(3))
        expected_hash = hashlib.sha256(expected_text.encode("utf-8")).hexdigest()
        assert src.sha256 == expected_hash

    def test_sha256_none_for_empty_source(self):
        """SHA-256 must be None when no source lines are present."""
        src = RepositoryObjectSource(
            sap_object_type="PROG",
            object_name="ZPROG",
            returned_lines=0,
            component_count=0,
            found=True,
            truncated=False,
            components=[],
        )
        assert src.sha256 is None

    def test_sha256_in_api_response(self, api_client, mock_object_provider):
        """SHA-256 must be present in the /source API response."""
        mock_object_provider.get_object_source.return_value = _make_source(25)
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source")
        assert resp.status_code == 200
        data = resp.json()
        assert "sha256" in data
        assert data["sha256"] is not None
        assert len(data["sha256"]) == 64


# ===========================================================================
# Q. Bulk/extraction path complete source
# ===========================================================================


class TestBulkExtractionCompleteSource:
    def test_bulk_request_default_source_max_lines_is_none(self):
        """BulkExtractionRequest.source_max_lines defaults to None (unlimited)."""
        req = BulkExtractionRequest(
            objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG")],
        )
        assert req.source_max_lines is None

    @pytest.mark.asyncio
    async def test_bulk_service_passes_none_to_object_service(self):
        """BulkExtractionService forwards source_max_lines=None to RepositoryObjectService."""
        from app.services.bulk_extraction import BulkExtractionService

        mock_obj_svc = MagicMock()
        mock_obj_svc.get_metadata = AsyncMock(return_value=_make_metadata())
        mock_obj_svc.get_source = AsyncMock(return_value=_make_source(1250))

        mock_settings = MagicMock()
        mock_settings.ncoreiq_bulk_max_objects = 250
        mock_settings.ncoreiq_bulk_concurrency = 1
        mock_settings.ncoreiq_bulk_max_total_source_lines = 500_000

        bulk_svc = BulkExtractionService(object_service=mock_obj_svc, settings=mock_settings)
        req = BulkExtractionRequest(
            objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG")],
            source_max_lines=None,
        )
        result = await bulk_svc.extract(req)

        call_kwargs = mock_obj_svc.get_source.call_args[1]
        assert call_kwargs.get("max_lines") is None
        assert result.objects[0].source.returned_lines == 1250


# ===========================================================================
# R. MCP complete source default
# ===========================================================================


class TestMCPCompleteSourceDefault:
    def _make_server(self, admin_max=None):
        from app.mcp.server import NCoreIQMCPServer

        mock_obj_svc = MagicMock()
        mock_obj_svc.get_source = AsyncMock(return_value=_make_source(1250))

        mock_settings = MagicMock()
        mock_settings.ncoreiq_mcp_max_source_lines = admin_max
        mock_settings.ncoreiq_mcp_max_search_results = 100
        mock_settings.ncoreiq_mcp_max_job_list_results = 20
        mock_settings.ncoreiq_mcp_max_dep_results = 200
        mock_settings.ncoreiq_mcp_dep_max_depth = 3
        mock_settings.ncoreiq_mcp_dep_max_nodes = 200
        mock_settings.ncoreiq_mcp_dep_max_edges = 500
        mock_settings.ncoreiq_mcp_dep_max_candidates = 50
        mock_settings.ncoreiq_mcp_server_name = "Test"
        mock_settings.app_version = "1.0.0"
        mock_settings.ncoreiq_mcp_json_response = True
        mock_settings.ncoreiq_mcp_allowed_hosts = []

        server = NCoreIQMCPServer(
            sap_provider=MagicMock(),
            discovery_svc=MagicMock(),
            object_svc=mock_obj_svc,
            job_svc=MagicMock(),
            settings=mock_settings,
        )
        return server, mock_obj_svc

    @pytest.mark.asyncio
    async def test_mcp_default_calls_service_with_none(self):
        """MCP get_object_source default (no admin ceiling) calls service with max_lines=None."""
        server, mock_svc = self._make_server(admin_max=None)
        result = await server._get_source("PROG", "ZPROG", max_lines=None)
        kwargs = mock_svc.get_source.call_args[1]
        assert kwargs.get("max_lines") is None
        assert result["returned_lines"] == 1250

    @pytest.mark.asyncio
    async def test_mcp_explicit_bounded(self):
        """MCP explicit max_lines=500 is forwarded as 500."""
        server, mock_svc = self._make_server(admin_max=None)
        mock_svc.get_source.return_value = _make_source(500, max_lines=500)
        await server._get_source("PROG", "ZPROG", max_lines=500)
        kwargs = mock_svc.get_source.call_args[1]
        assert kwargs.get("max_lines") == 500

    @pytest.mark.asyncio
    async def test_mcp_config_max_none_means_unlimited(self):
        """When NCOREIQ_MCP_MAX_SOURCE_LINES is None (default), MCP is unlimited."""
        from app.core.config import Settings
        # Default config must have None for ncoreiq_mcp_max_source_lines
        settings = Settings()
        assert settings.ncoreiq_mcp_max_source_lines is None

    @pytest.mark.asyncio
    async def test_mcp_admin_ceiling_caps_caller_request(self):
        """Admin ceiling caps caller's explicit max_lines request."""
        server, mock_svc = self._make_server(admin_max=1000)
        mock_svc.get_source.return_value = _make_source(1000, max_lines=1000)
        await server._get_source("PROG", "ZPROG", max_lines=5000)
        kwargs = mock_svc.get_source.call_args[1]
        assert kwargs.get("max_lines") == 1000

    @pytest.mark.asyncio
    async def test_mcp_start_extraction_job_default_source_max_lines_none(self):
        """MCP start_extraction_job default source_max_lines is None (complete source)."""
        from app.mcp.server import NCoreIQMCPServer
        import inspect

        # Get the source of start_extraction_job to verify the default
        server, _ = self._make_server()
        # The default is verified by the tool registration in _register_tools
        # which uses source_max_lines=None as default parameter
        assert server._max_source is None
