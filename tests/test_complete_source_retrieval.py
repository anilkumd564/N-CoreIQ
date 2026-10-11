"""Tests for complete source retrieval — removal of 500-line default truncation.

Covers:
  A. Constants    — DEFAULT_MAX_SOURCE_LINES is None
  B. Service      — None passes validation; 0/negative/20001 rejected
  C. OData filter — MaxLines omitted when max_lines=None; present when specified
  D. OData pagination — multi-page __next links are followed and merged
  E. Backend API  — no max_lines → complete; explicit max_lines → bounded; 0 → 422
  F. Bulk extraction — source_max_lines=None flows through correctly
  G. MCP          — default (None) → complete; explicit int → bounded
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.models.phase4 import (
    DEFAULT_MAX_SOURCE_LINES,
    MAX_SOURCE_LINES_HARD_LIMIT,
    MIN_SOURCE_LINES,
    RepositoryObjectSource,
    RepositorySourceComponent,
    RepositorySourceLine,
)
from app.models.phase5 import BulkExtractionRequest, RepositoryObjectIdentifier
from app.sap.connection.odata import ODataSAPConnectionProvider
from app.services.object_service import RepositoryObjectService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_lines(
    n: int, comp_type: str = "PROGRAM", comp_name: str = "ZPROG"
) -> list[RepositorySourceLine]:
    return [
        RepositorySourceLine(
            component_type=comp_type,
            component_name=comp_name,
            line_no=i + 1,
            source_line=f"* line {i + 1}",
        )
        for i in range(n)
    ]


def _make_source(n: int, max_lines: int | None = None) -> RepositoryObjectSource:
    lines = _make_lines(n)
    comp = RepositorySourceComponent(
        component_type="PROGRAM",
        component_name="ZPROG",
        line_count=n,
        lines=lines,
        source_text="\n".join(f"* line {i + 1}" for i in range(n)),
    )
    return RepositoryObjectSource(
        sap_object_type="PROG",
        object_name="ZPROG",
        system_id="NC1",
        client="800",
        max_lines=max_lines,
        returned_lines=n,
        component_count=1,
        found=True,
        truncated=(max_lines is not None and n >= max_lines),
        components=[comp],
    )


def _make_odata_settings() -> MagicMock:
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
    return settings


def _make_odata_provider() -> ODataSAPConnectionProvider:
    return ODataSAPConnectionProvider(_make_odata_settings())


def _mock_http_client(pages: list[dict]) -> MagicMock:
    """Return a mock httpx client that returns pages in order.

    Each page is a dict that becomes response.json().
    Pagination: if page["d"]["__next"] exists the provider should follow it.
    """
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


# ===========================================================================
# A. Constants
# ===========================================================================


class TestDefaultConstant:
    def test_default_max_source_lines_is_none(self):
        """DEFAULT_MAX_SOURCE_LINES must be None (unlimited) — 500-line default removed."""
        assert DEFAULT_MAX_SOURCE_LINES is None

    def test_max_source_lines_hard_limit_preserved(self):
        assert MAX_SOURCE_LINES_HARD_LIMIT == 20_000

    def test_min_source_lines_preserved(self):
        assert MIN_SOURCE_LINES == 1


# ===========================================================================
# B. Service — max_lines validation
# ===========================================================================


class TestServiceMaxLinesValidation:
    def setup_method(self):
        self.provider = MagicMock()
        self.provider.get_object_source = AsyncMock(return_value=_make_source(10))
        self.service = RepositoryObjectService(provider=self.provider)

    @pytest.mark.asyncio
    async def test_none_unlimited_returns_all_lines(self):
        """max_lines=None — unlimited; all 1250 lines must be returned."""
        self.provider.get_object_source.return_value = _make_source(1250)
        result = await self.service.get_source("PROG", "ZPROG", max_lines=None)
        assert result.returned_lines == 1250
        assert result.max_lines is None

    @pytest.mark.asyncio
    async def test_none_passed_to_provider(self):
        """Service must forward max_lines=None to the OData provider unchanged."""
        await self.service.get_source("PROG", "ZPROG", max_lines=None)
        call_kwargs = self.provider.get_object_source.call_args[1]
        assert call_kwargs.get("max_lines") is None

    @pytest.mark.asyncio
    async def test_default_is_none(self):
        """Calling get_source() without max_lines must default to None (unlimited)."""
        await self.service.get_source("PROG", "ZPROG")
        call_kwargs = self.provider.get_object_source.call_args[1]
        assert call_kwargs.get("max_lines") is None

    @pytest.mark.asyncio
    async def test_explicit_500_forwarded(self):
        """Explicit max_lines=500 must be forwarded to provider as 500."""
        self.provider.get_object_source.return_value = _make_source(500, max_lines=500)
        await self.service.get_source("PROG", "ZPROG", max_lines=500)
        call_kwargs = self.provider.get_object_source.call_args[1]
        assert call_kwargs.get("max_lines") == 500

    @pytest.mark.asyncio
    async def test_zero_rejected(self):
        with pytest.raises(ValueError, match="max_lines"):
            await self.service.get_source("PROG", "ZPROG", max_lines=0)

    @pytest.mark.asyncio
    async def test_negative_rejected(self):
        with pytest.raises(ValueError, match="max_lines"):
            await self.service.get_source("PROG", "ZPROG", max_lines=-10)

    @pytest.mark.asyncio
    async def test_above_hard_limit_rejected(self):
        with pytest.raises(ValueError, match="max_lines"):
            await self.service.get_source("PROG", "ZPROG", max_lines=MAX_SOURCE_LINES_HARD_LIMIT + 1)

    @pytest.mark.asyncio
    async def test_exactly_hard_limit_accepted(self):
        self.provider.get_object_source.return_value = _make_source(100, max_lines=MAX_SOURCE_LINES_HARD_LIMIT)
        result = await self.service.get_source("PROG", "ZPROG", max_lines=MAX_SOURCE_LINES_HARD_LIMIT)
        assert result.max_lines == MAX_SOURCE_LINES_HARD_LIMIT

    @pytest.mark.asyncio
    async def test_1250_lines_complete_retrieval(self):
        """More than 500 lines must be returned when max_lines=None."""
        self.provider.get_object_source.return_value = _make_source(1250)
        result = await self.service.get_source("PROG", "ZPROG")
        assert result.returned_lines == 1250
        assert result.truncated is False


# ===========================================================================
# C. OData filter — MaxLines clause presence/absence
# ===========================================================================


class TestODataMaxLinesFilter:
    @pytest.mark.asyncio
    async def test_none_max_lines_omits_maxlines_from_filter(self):
        """When max_lines=None the OData $filter must NOT contain 'MaxLines eq'."""
        provider = _make_odata_provider()
        page = _odata_page(1, 0)
        mock_client = _mock_http_client([page])
        captured: dict = {}

        original_side_effect = mock_client.get.side_effect

        async def capture_get(url, params=None):
            captured["params"] = dict(params or {})
            return await original_side_effect(url, params=params)

        mock_client.get.side_effect = capture_get

        with patch.object(provider, "_build_client", return_value=mock_client):
            await provider.get_object_source("PROG", "ZPROG", max_lines=None)

        odata_filter = captured.get("params", {}).get("$filter", "")
        assert "MaxLines" not in odata_filter, (
            f"MaxLines must not appear when max_lines=None. Filter was: {odata_filter!r}"
        )
        assert "ObjectType eq 'PROG'" in odata_filter
        assert "ObjectName eq 'ZPROG'" in odata_filter

    @pytest.mark.asyncio
    async def test_explicit_500_adds_maxlines_to_filter(self):
        """When max_lines=500 the OData $filter must contain 'MaxLines eq 500'."""
        provider = _make_odata_provider()
        page = _odata_page(1, 0)
        mock_client = _mock_http_client([page])
        captured: dict = {}

        original_side_effect = mock_client.get.side_effect

        async def capture_get(url, params=None):
            captured["params"] = dict(params or {})
            return await original_side_effect(url, params=params)

        mock_client.get.side_effect = capture_get

        with patch.object(provider, "_build_client", return_value=mock_client):
            await provider.get_object_source("PROG", "ZPROG", max_lines=500)

        odata_filter = captured.get("params", {}).get("$filter", "")
        assert "MaxLines eq 500" in odata_filter, (
            f"MaxLines eq 500 must appear when max_lines=500. Filter was: {odata_filter!r}"
        )


# ===========================================================================
# D. OData pagination — __next link followed
# ===========================================================================


class TestODataPagination:
    @pytest.mark.asyncio
    async def test_three_pages_merged_into_1250_lines(self):
        """Three pages (500 + 500 + 250) must be merged into 1250 total lines."""
        provider = _make_odata_provider()

        # Page 1: lines 1–500, has __next
        page1 = _odata_page(1, 500, next_link="https://sap.example.com/page2")
        # Page 2: lines 501–1000, has __next
        page2 = _odata_page(501, 500, next_link="https://sap.example.com/page3")
        # Page 3: lines 1001–1250, no __next
        page3 = _odata_page(1001, 250)

        mock_client = _mock_http_client([page1, page2, page3])

        with patch.object(provider, "_build_client", return_value=mock_client):
            result = await provider.get_object_source("PROG", "ZPROG", max_lines=None)

        assert result.returned_lines == 1250, (
            f"Expected 1250 lines from 3 pages; got {result.returned_lines}"
        )
        assert result.truncated is False
        assert result.component_count == 1
        assert len(result.components[0].lines) == 1250

    @pytest.mark.asyncio
    async def test_single_page_no_pagination(self):
        """Single page with no __next must return all lines without extra calls."""
        provider = _make_odata_provider()
        page1 = _odata_page(1, 842)  # no next_link
        mock_client = _mock_http_client([page1])

        with patch.object(provider, "_build_client", return_value=mock_client):
            result = await provider.get_object_source("PROG", "ZPROG")

        assert result.returned_lines == 842
        assert mock_client.get.call_count == 1

    @pytest.mark.asyncio
    async def test_two_pages_merged(self):
        """Two pages must produce combined line count."""
        provider = _make_odata_provider()
        page1 = _odata_page(1, 500, next_link="https://sap.example.com/p2")
        page2 = _odata_page(501, 300)
        mock_client = _mock_http_client([page1, page2])

        with patch.object(provider, "_build_client", return_value=mock_client):
            result = await provider.get_object_source("PROG", "ZPROG")

        assert result.returned_lines == 800
        assert mock_client.get.call_count == 2


# ===========================================================================
# E. Backend API endpoint
# ===========================================================================


@pytest.fixture()
def mock_provider_api():
    p = MagicMock()
    p.get_object_source = AsyncMock(return_value=_make_source(1250))
    p.get_object_metadata = AsyncMock()
    return p


@pytest.fixture()
def api_client(mock_provider_api):
    from app.main import app
    from app.services.object_service import get_object_service

    async def _override():
        return RepositoryObjectService(provider=mock_provider_api)

    app.dependency_overrides[get_object_service] = _override
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    app.dependency_overrides.pop(get_object_service, None)


class TestAPISourceEndpointUnlimited:
    def test_no_max_lines_returns_complete_source(self, api_client, mock_provider_api):
        """GET /source without max_lines must return all lines (no 500 default)."""
        mock_provider_api.get_object_source.return_value = _make_source(1250)
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source")
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned_lines"] == 1250
        assert data["max_lines"] is None
        assert data["truncated"] is False

    def test_explicit_500_limits_source(self, api_client, mock_provider_api):
        """GET /source?max_lines=500 must return at most 500 lines."""
        mock_provider_api.get_object_source.return_value = _make_source(500, max_lines=500)
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source?max_lines=500")
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned_lines"] == 500
        assert data["max_lines"] == 500

    def test_max_lines_0_returns_422(self, api_client):
        """max_lines=0 must be rejected with HTTP 422."""
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source?max_lines=0")
        assert resp.status_code == 422

    def test_max_lines_negative_returns_422(self, api_client):
        """max_lines=-1 must be rejected with HTTP 422."""
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source?max_lines=-1")
        assert resp.status_code == 422

    def test_max_lines_above_hard_limit_returns_422(self, api_client):
        """max_lines=20001 must be rejected with HTTP 422."""
        resp = api_client.get(
            f"/api/v1/repository/objects/PROG/ZPROG/source?max_lines={MAX_SOURCE_LINES_HARD_LIMIT + 1}"
        )
        assert resp.status_code == 422

    def test_no_max_lines_provider_receives_none(self, api_client, mock_provider_api):
        """When max_lines absent, provider must receive max_lines=None."""
        api_client.get("/api/v1/repository/objects/PROG/ZPROG/source")
        call_kwargs = mock_provider_api.get_object_source.call_args[1]
        assert call_kwargs.get("max_lines") is None

    def test_response_includes_returned_lines_field(self, api_client):
        """Response must include returned_lines showing actual count."""
        resp = api_client.get("/api/v1/repository/objects/PROG/ZPROG/source")
        assert resp.status_code == 200
        assert "returned_lines" in resp.json()


# ===========================================================================
# F. Bulk extraction — source_max_lines=None default
# ===========================================================================


class TestBulkExtractionSourceMaxLines:
    def test_default_source_max_lines_is_none(self):
        """BulkExtractionRequest default source_max_lines must be None."""
        req = BulkExtractionRequest(
            objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG")],
        )
        assert req.source_max_lines is None

    def test_explicit_source_max_lines_accepted(self):
        """Explicit source_max_lines=500 must be accepted."""
        req = BulkExtractionRequest(
            objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG")],
            source_max_lines=500,
        )
        assert req.source_max_lines == 500

    def test_source_max_lines_zero_rejected(self):
        """source_max_lines=0 must be rejected by Pydantic validation."""
        with pytest.raises(Exception):
            BulkExtractionRequest(
                objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG")],
                source_max_lines=0,
            )

    def test_source_max_lines_above_hard_limit_rejected(self):
        """source_max_lines above hard limit must be rejected."""
        with pytest.raises(Exception):
            BulkExtractionRequest(
                objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG")],
                source_max_lines=MAX_SOURCE_LINES_HARD_LIMIT + 1,
            )


# ===========================================================================
# G. MCP — default unlimited, explicit bounded
# ===========================================================================


class TestMCPSourceRetrieval:
    def _make_mcp_server(self, max_source_cfg=None):
        from app.mcp.server import NCoreIQMCPServer

        mock_object_svc = MagicMock()
        mock_object_svc.get_source = AsyncMock(return_value=_make_source(1250))

        mock_settings = MagicMock()
        mock_settings.ncoreiq_mcp_max_source_lines = max_source_cfg
        mock_settings.ncoreiq_mcp_max_search_results = 100
        mock_settings.ncoreiq_mcp_max_job_list_results = 20
        mock_settings.ncoreiq_mcp_max_dep_results = 200
        mock_settings.ncoreiq_mcp_dep_max_depth = 3
        mock_settings.ncoreiq_mcp_dep_max_nodes = 200
        mock_settings.ncoreiq_mcp_dep_max_edges = 500
        mock_settings.ncoreiq_mcp_dep_max_candidates = 50
        mock_settings.ncoreiq_mcp_server_name = "N-CoreIQ-Test"
        mock_settings.app_version = "1.0.0"
        mock_settings.ncoreiq_mcp_json_response = True
        mock_settings.ncoreiq_mcp_allowed_hosts = []

        server = NCoreIQMCPServer(
            sap_provider=MagicMock(),
            discovery_svc=MagicMock(),
            object_svc=mock_object_svc,
            job_svc=MagicMock(),
            settings=mock_settings,
        )
        return server, mock_object_svc

    @pytest.mark.asyncio
    async def test_default_none_calls_service_with_none(self):
        """MCP get_object_source default must call service with max_lines=None."""
        server, mock_svc = self._make_mcp_server(max_source_cfg=None)
        result = await server._get_source("PROG", "ZPROG", max_lines=None)
        call_kwargs = mock_svc.get_source.call_args[1]
        assert call_kwargs.get("max_lines") is None
        assert result["returned_lines"] == 1250

    @pytest.mark.asyncio
    async def test_explicit_500_calls_service_with_500(self):
        """MCP get_object_source with max_lines=500 must call service with 500."""
        server, mock_svc = self._make_mcp_server(max_source_cfg=None)
        mock_svc.get_source.return_value = _make_source(500, max_lines=500)
        await server._get_source("PROG", "ZPROG", max_lines=500)
        call_kwargs = mock_svc.get_source.call_args[1]
        assert call_kwargs.get("max_lines") == 500

    @pytest.mark.asyncio
    async def test_admin_ceiling_applied_when_set(self):
        """When admin configures max_source=1000, explicit 5000 must be capped at 1000."""
        server, mock_svc = self._make_mcp_server(max_source_cfg=1000)
        mock_svc.get_source.return_value = _make_source(1000, max_lines=1000)
        await server._get_source("PROG", "ZPROG", max_lines=5000)
        call_kwargs = mock_svc.get_source.call_args[1]
        assert call_kwargs.get("max_lines") == 1000

    @pytest.mark.asyncio
    async def test_admin_ceiling_used_when_caller_omits_max_lines(self):
        """When admin ceiling is set, omitting max_lines uses admin ceiling."""
        server, mock_svc = self._make_mcp_server(max_source_cfg=2000)
        mock_svc.get_source.return_value = _make_source(2000, max_lines=2000)
        await server._get_source("PROG", "ZPROG", max_lines=None)
        call_kwargs = mock_svc.get_source.call_args[1]
        assert call_kwargs.get("max_lines") == 2000

    def test_mcp_default_source_constant_is_none(self):
        """_DEFAULT_SOURCE module constant must be None."""
        from app.mcp.server import _DEFAULT_SOURCE
        assert _DEFAULT_SOURCE is None
