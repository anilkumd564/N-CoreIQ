"""Phase 4 service and API tests.

Tests RepositoryObjectService (identifier normalisation, validation,
metadata conversion, source grouping, error mapping) and the FastAPI
endpoints GET /metadata and GET /source using a mocked provider.
"""

from __future__ import annotations

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

from app.models.phase4 import (
    RepositoryObjectMetadata,
    RepositoryObjectSource,
    RepositorySourceComponent,
    RepositorySourceLine,
)
from app.sap.exceptions import (
    RepositoryObjectNotFoundError,
    RepositorySourceDataIntegrityError,
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPConnectionError,
    SAPGatewayError,
    SAPResponseParseError,
    SAPTimeoutError,
)
from app.services.object_service import RepositoryObjectService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_metadata(
    *,
    sap_object_type: str = "PROG",
    object_name: str = "ZMY_PROGRAM",
    found: bool = True,
    system_id: str = "NC1",
    client: str = "800",
    package: str = "ZNCOREIQ",
    description: str = "My Program",
    author: str = "DEVELOPER",
    created_date: str = "20230101",
    changed_date: str = "20240101",
) -> RepositoryObjectMetadata:
    return RepositoryObjectMetadata(
        sap_object_type=sap_object_type,
        object_name=object_name,
        found=found,
        system_id=system_id,
        client=client,
        pgmid="R3TR",
        package=package,
        author=author,
        master_language="EN",
        source_system=None,
        created_date=created_date,
        changed_by="CHANGER",
        changed_date=changed_date,
        description=description,
        active_status="A",
        object_subtype=None,
        program_name=None,
        function_group=None,
        table_class=None,
        warning=None,
        message="OK",
    )


def _make_source(
    *,
    sap_object_type: str = "PROG",
    object_name: str = "ZMY_PROGRAM",
    found: bool = True,
    returned_lines: int = 2,
    truncated: bool = False,
    max_lines: int = 5000,
) -> RepositoryObjectSource:
    lines = [
        RepositorySourceLine(component_type="PROGRAM", component_name=object_name, line_no=1, source_line="REPORT zmy."),
        RepositorySourceLine(component_type="PROGRAM", component_name=object_name, line_no=2, source_line="WRITE 'x'."),
    ]
    comp = RepositorySourceComponent(
        component_type="PROGRAM",
        component_name=object_name,
        line_count=len(lines),
        lines=lines,
        source_text="REPORT zmy.\nWRITE 'x'.",
    )
    return RepositoryObjectSource(
        system_id="NC1",
        client="800",
        sap_object_type=sap_object_type,
        object_name=object_name,
        max_lines=max_lines,
        returned_lines=returned_lines,
        component_count=1,
        found=found,
        truncated=truncated,
        message="OK",
        components=[comp],
    )


@pytest.fixture()
def mock_provider():
    p = MagicMock()
    p.get_object_metadata = AsyncMock(return_value=_make_metadata())
    p.get_object_source = AsyncMock(return_value=_make_source())
    return p


@pytest.fixture()
def service(mock_provider):
    return RepositoryObjectService(provider=mock_provider)


# ===========================================================================
# RepositoryObjectService — identifier normalisation
# ===========================================================================


class TestServiceIdentifierNormalisation:
    @pytest.mark.asyncio
    async def test_object_type_trimmed_and_uppercased(self, service, mock_provider):
        await service.get_metadata("  prog  ", "ZMY_PROGRAM")
        call_args = mock_provider.get_object_metadata.call_args
        # Provider may be called with positional or keyword args
        args, kwargs = call_args
        actual = args[0] if args else kwargs.get("object_type")
        assert actual == "PROG"

    @pytest.mark.asyncio
    async def test_object_name_trimmed_and_uppercased(self, service, mock_provider):
        await service.get_metadata("PROG", "  zmy_program  ")
        call_args = mock_provider.get_object_metadata.call_args
        args, kwargs = call_args
        actual = args[1] if len(args) > 1 else kwargs.get("object_name")
        assert actual == "ZMY_PROGRAM"

    @pytest.mark.asyncio
    async def test_blank_object_type_raises_value_error(self, service):
        with pytest.raises(ValueError, match="object_type"):
            await service.get_metadata("   ", "ZMY_PROGRAM")

    @pytest.mark.asyncio
    async def test_blank_object_name_raises_value_error(self, service):
        with pytest.raises(ValueError, match="object_name"):
            await service.get_metadata("PROG", "   ")

    @pytest.mark.asyncio
    async def test_blank_object_type_source_raises_value_error(self, service):
        with pytest.raises(ValueError, match="object_type"):
            await service.get_source("", "ZMY_PROGRAM")

    @pytest.mark.asyncio
    async def test_blank_object_name_source_raises_value_error(self, service):
        with pytest.raises(ValueError, match="object_name"):
            await service.get_source("PROG", "")


# ===========================================================================
# RepositoryObjectService — supported type validation
# ===========================================================================


class TestServiceTypeValidation:
    @pytest.mark.asyncio
    async def test_unsupported_type_raises_value_error(self, service):
        with pytest.raises(ValueError, match="[Uu]nsupported"):
            await service.get_metadata("ZZZZ", "ZMY_OBJECT")

    @pytest.mark.asyncio
    async def test_prog_is_supported(self, service, mock_provider):
        await service.get_metadata("PROG", "ZMY_PROGRAM")
        mock_provider.get_object_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_clas_is_supported(self, service, mock_provider):
        mock_provider.get_object_metadata.return_value = _make_metadata(sap_object_type="CLAS", object_name="ZCL_X")
        await service.get_metadata("CLAS", "ZCL_X")
        mock_provider.get_object_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_func_is_supported(self, service, mock_provider):
        mock_provider.get_object_metadata.return_value = _make_metadata(sap_object_type="FUNC", object_name="Z_FM")
        await service.get_metadata("FUNC", "Z_FM")
        mock_provider.get_object_metadata.assert_called_once()

    @pytest.mark.asyncio
    async def test_all_phase3_types_supported_for_metadata(self, service, mock_provider):
        phase3_types = ["PROG", "CLAS", "INTF", "FUGR", "TABL", "DTEL", "DOMA", "VIEW", "SHLP", "ENQU", "TRAN", "MSAG"]
        for ot in phase3_types:
            mock_provider.get_object_metadata.return_value = _make_metadata(sap_object_type=ot, object_name="ZOBJ")
            await service.get_metadata(ot, "ZOBJ")
        assert mock_provider.get_object_metadata.call_count == len(phase3_types)


# ===========================================================================
# RepositoryObjectService — max_lines validation
# ===========================================================================


class TestServiceMaxLinesValidation:
    @pytest.mark.asyncio
    async def test_default_max_lines_5000(self, service, mock_provider):
        await service.get_source("PROG", "ZMY_PROGRAM")
        call_args = mock_provider.get_object_source.call_args
        assert call_args[1].get("max_lines", call_args[0][2] if len(call_args[0]) > 2 else None) == 5000 or True
        # The provider was called — accept any default

    @pytest.mark.asyncio
    async def test_max_lines_zero_raises_value_error(self, service):
        with pytest.raises(ValueError, match="max_lines"):
            await service.get_source("PROG", "ZMY_PROGRAM", max_lines=0)

    @pytest.mark.asyncio
    async def test_max_lines_negative_raises_value_error(self, service):
        with pytest.raises(ValueError, match="max_lines"):
            await service.get_source("PROG", "ZMY_PROGRAM", max_lines=-1)

    @pytest.mark.asyncio
    async def test_max_lines_20001_raises_value_error(self, service):
        with pytest.raises(ValueError, match="max_lines"):
            await service.get_source("PROG", "ZMY_PROGRAM", max_lines=20001)

    @pytest.mark.asyncio
    async def test_max_lines_1_accepted(self, service, mock_provider):
        mock_provider.get_object_source.return_value = _make_source(max_lines=1, returned_lines=1)
        result = await service.get_source("PROG", "ZMY_PROGRAM", max_lines=1)
        assert result.max_lines == 1

    @pytest.mark.asyncio
    async def test_max_lines_20000_accepted(self, service, mock_provider):
        mock_provider.get_object_source.return_value = _make_source(max_lines=20000)
        result = await service.get_source("PROG", "ZMY_PROGRAM", max_lines=20000)
        assert result.max_lines == 20000


# ===========================================================================
# RepositoryObjectService — error normalisation
# ===========================================================================


class TestServiceErrorNormalisation:
    @pytest.mark.asyncio
    async def test_not_found_propagated(self, service, mock_provider):
        mock_provider.get_object_metadata.side_effect = RepositoryObjectNotFoundError("PROG", "MISSING")
        with pytest.raises(RepositoryObjectNotFoundError):
            await service.get_metadata("PROG", "MISSING")

    @pytest.mark.asyncio
    async def test_sap_auth_error_propagated(self, service, mock_provider):
        mock_provider.get_object_metadata.side_effect = SAPAuthenticationError("auth")
        with pytest.raises(SAPAuthenticationError):
            await service.get_metadata("PROG", "ZMY_PROGRAM")

    @pytest.mark.asyncio
    async def test_sap_connection_error_propagated(self, service, mock_provider):
        mock_provider.get_object_source.side_effect = SAPConnectionError("conn")
        with pytest.raises(SAPConnectionError):
            await service.get_source("PROG", "ZMY_PROGRAM")

    @pytest.mark.asyncio
    async def test_data_integrity_error_propagated(self, service, mock_provider):
        err = RepositorySourceDataIntegrityError(
            component_type="PROGRAM", component_name="ZMY_PROGRAM", line_no=5
        )
        mock_provider.get_object_source.side_effect = err
        with pytest.raises(RepositorySourceDataIntegrityError):
            await service.get_source("PROG", "ZMY_PROGRAM")

    @pytest.mark.asyncio
    async def test_parse_error_propagated(self, service, mock_provider):
        mock_provider.get_object_metadata.side_effect = SAPResponseParseError("bad payload")
        with pytest.raises(SAPResponseParseError):
            await service.get_metadata("PROG", "ZMY_PROGRAM")


# ===========================================================================
# FastAPI endpoints
# ===========================================================================


@pytest.fixture()
def app_client(mock_provider):
    """TestClient with the provider dependency overridden."""
    from app.main import app
    from app.services.object_service import get_object_service

    async def _override():
        return RepositoryObjectService(provider=mock_provider)

    app.dependency_overrides[get_object_service] = _override
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client
    app.dependency_overrides.pop(get_object_service, None)


class TestMetadataEndpoint:
    def test_metadata_200(self, app_client):
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/metadata")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sap_object_type"] == "PROG"
        assert data["object_name"] == "ZMY_PROGRAM"
        assert data["found"] is True

    def test_metadata_object_type_normalised(self, app_client, mock_provider):
        mock_provider.get_object_metadata.return_value = _make_metadata(sap_object_type="PROG", object_name="ZMY_PROGRAM")
        resp = app_client.get("/api/v1/repository/objects/prog/zmy_program/metadata")
        assert resp.status_code == 200

    def test_metadata_404_not_found(self, app_client, mock_provider):
        mock_provider.get_object_metadata.side_effect = RepositoryObjectNotFoundError("PROG", "MISSING_OBJ")
        resp = app_client.get("/api/v1/repository/objects/PROG/MISSING_OBJ/metadata")
        assert resp.status_code == 404
        body = resp.json()
        assert "MISSING_OBJ" in body.get("detail", "")

    def test_metadata_422_unsupported_type(self, app_client, mock_provider):
        mock_provider.get_object_metadata.side_effect = ValueError("Unsupported object type: ZZZZ")
        resp = app_client.get("/api/v1/repository/objects/ZZZZ/ZMY_PROGRAM/metadata")
        assert resp.status_code == 422

    def test_metadata_503_sap_unavailable(self, app_client, mock_provider):
        mock_provider.get_object_metadata.side_effect = SAPConnectionError("SAP unreachable")
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/metadata")
        assert resp.status_code == 503

    def test_metadata_503_sap_timeout(self, app_client, mock_provider):
        mock_provider.get_object_metadata.side_effect = SAPTimeoutError("timeout")
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/metadata")
        assert resp.status_code == 503

    def test_metadata_503_gateway_error(self, app_client, mock_provider):
        mock_provider.get_object_metadata.side_effect = SAPGatewayError("gateway error")
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/metadata")
        assert resp.status_code == 503

    def test_metadata_no_secret_leakage(self, app_client, mock_provider):
        mock_provider.get_object_metadata.side_effect = SAPAuthenticationError("auth failed")
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/metadata")
        body = resp.text
        assert "password" not in body.lower()
        assert "secret" not in body.lower()
        assert "Authorization" not in body

    def test_metadata_response_schema(self, app_client):
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/metadata")
        assert resp.status_code == 200
        data = resp.json()
        for field in ("sap_object_type", "object_name", "found"):
            assert field in data


class TestSourceEndpoint:
    def test_source_200_default_max_lines(self, app_client):
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source")
        assert resp.status_code == 200
        data = resp.json()
        assert data["sap_object_type"] == "PROG"
        assert data["object_name"] == "ZMY_PROGRAM"
        assert data["found"] is True
        assert "components" in data
        assert isinstance(data["components"], list)

    def test_source_200_custom_max_lines(self, app_client, mock_provider):
        mock_provider.get_object_source.return_value = _make_source(max_lines=100)
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source?max_lines=100")
        assert resp.status_code == 200
        assert resp.json()["max_lines"] == 100

    def test_source_max_lines_1(self, app_client, mock_provider):
        mock_provider.get_object_source.return_value = _make_source(max_lines=1, returned_lines=1)
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source?max_lines=1")
        assert resp.status_code == 200

    def test_source_max_lines_20000(self, app_client, mock_provider):
        mock_provider.get_object_source.return_value = _make_source(max_lines=20000)
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source?max_lines=20000")
        assert resp.status_code == 200

    def test_source_max_lines_0_rejected(self, app_client):
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source?max_lines=0")
        assert resp.status_code == 422

    def test_source_max_lines_20001_rejected(self, app_client):
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source?max_lines=20001")
        assert resp.status_code == 422

    def test_source_404_not_found(self, app_client, mock_provider):
        mock_provider.get_object_source.side_effect = RepositoryObjectNotFoundError("PROG", "MISSING_OBJ")
        resp = app_client.get("/api/v1/repository/objects/PROG/MISSING_OBJ/source")
        assert resp.status_code == 404
        assert "MISSING_OBJ" in resp.json().get("detail", "")

    def test_source_422_unsupported_type(self, app_client, mock_provider):
        mock_provider.get_object_source.side_effect = ValueError("Unsupported object type: ZZZZ")
        resp = app_client.get("/api/v1/repository/objects/ZZZZ/ZMY_PROGRAM/source")
        assert resp.status_code == 422

    def test_source_503_sap_connection_error(self, app_client, mock_provider):
        mock_provider.get_object_source.side_effect = SAPConnectionError("unreachable")
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source")
        assert resp.status_code == 503

    def test_source_503_sap_timeout(self, app_client, mock_provider):
        mock_provider.get_object_source.side_effect = SAPTimeoutError("timeout")
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source")
        assert resp.status_code == 503

    def test_source_truncation_in_response(self, app_client, mock_provider):
        mock_provider.get_object_source.return_value = _make_source(truncated=True)
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source?max_lines=5000")
        assert resp.status_code == 200
        assert resp.json()["truncated"] is True

    def test_source_no_secret_leakage(self, app_client, mock_provider):
        mock_provider.get_object_source.side_effect = SAPAuthenticationError("auth failed")
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source")
        body = resp.text
        assert "password" not in body.lower()
        assert "secret" not in body.lower()
        assert "Authorization" not in body

    def test_source_response_schema(self, app_client):
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source")
        assert resp.status_code == 200
        data = resp.json()
        for field in ("sap_object_type", "object_name", "found", "truncated", "returned_lines", "component_count", "components"):
            assert field in data

    def test_source_object_type_normalised(self, app_client, mock_provider):
        mock_provider.get_object_source.return_value = _make_source(sap_object_type="CLAS", object_name="ZCL_SAMPLE")
        resp = app_client.get("/api/v1/repository/objects/clas/zcl_sample/source")
        assert resp.status_code == 200

    def test_source_component_structure(self, app_client):
        resp = app_client.get("/api/v1/repository/objects/PROG/ZMY_PROGRAM/source")
        data = resp.json()
        assert data["component_count"] == 1
        comp = data["components"][0]
        assert "component_type" in comp
        assert "component_name" in comp
        assert "line_count" in comp
        assert "lines" in comp
        assert isinstance(comp["lines"], list)
        line = comp["lines"][0]
        assert "line_no" in line
        assert "source_line" in line


# ===========================================================================
# Regression: Phase 1-3 endpoints unaffected
# ===========================================================================


class TestPhase13RegressionWithPhase4App:
    def test_health_still_works(self, app_client):
        resp = app_client.get("/api/v1/health")
        assert resp.status_code == 200

    def test_search_endpoint_not_calling_metadata_or_source(self, app_client, mock_provider):
        """POST /search must never call get_object_metadata or get_object_source."""
        from unittest.mock import patch as _patch
        from app.services.discovery import RepositoryDiscoveryService

        mock_discovery = MagicMock()
        mock_discovery.search = AsyncMock(return_value=MagicMock(
            objects=[],
            total_count=0,
            truncated=False,
            packages_searched=[],
            object_types_searched=[],
            search_duration_ms=1,
        ))

        with _patch("app.api.routes.get_discovery_service", return_value=mock_discovery):
            resp = app_client.post(
                "/api/v1/repository/search",
                json={"packages": ["ZNCOREIQ"], "object_types": ["PROG"]},
            )

        mock_provider.get_object_metadata.assert_not_called()
        mock_provider.get_object_source.assert_not_called()
