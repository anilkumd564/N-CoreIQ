"""Phase 8 — Provider tests for DependencyRelationsSet OData parsing.

Tests the ODataSAPConnectionProvider._parse_dependencies() method and
get_object_dependencies() with mocked HTTP responses.

Coverage:
  - All canonical relationship types
  - All confidence levels
  - Dynamic flag
  - Empty result (zero dependencies)
  - Truncated result
  - Malformed envelope
  - Unknown relationship type (skipped)
  - Unknown confidence (defaults to MEDIUM)
  - Boolean normalization (X, true, false, blank)
  - Integer normalization (RelationId, LineNo, TotalRelations)
  - HTTP 401, 403, 404, 500 error codes
  - JSON parse error
  - Missing 'd' key
  - Missing 'results' key
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.phase8 import (
    DependencyConfidence,
    DependencyRelationshipType,
    DirectDependencyResult,
)
from app.sap.exceptions import (
    DependencyDataIntegrityError,
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPGatewayError,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_provider():
    """Create a minimal ODataSAPConnectionProvider with test settings."""
    from app.core.config import Settings, SapODataSettings
    settings = Settings(
        sap_connection_mode="odata",
        sap_odata=SapODataSettings(
            base_url="https://sap.example.com:44300",
            user="TESTUSER",
            password="TESTPASS",
            client="100",
        ),
    )
    from app.sap.connection.odata import ODataSAPConnectionProvider
    return ODataSAPConnectionProvider(settings)


def _make_dep_row(
    source_type="PROG",
    source_name="ZFI_REPORT",
    target_type="FUNC",
    target_name="ZFI_GET_DATA",
    rel_type="CALLS_FUNCTION",
    confidence="HIGH",
    dynamic=False,
    evidence_source="SOURCE_STATIC",
    source_component=None,
    line_no=None,
    detail=None,
    relation_id=None,
    system_id="S4H",
    client="100",
    truncated=False,
    total_relations=0,
    message="",
):
    row = {
        "RelationId": str(relation_id) if relation_id else None,
        "SystemId": system_id,
        "Client": client,
        "SourceObjectType": source_type,
        "SourceObjectName": source_name,
        "TargetObjectType": target_type,
        "TargetObjectName": target_name,
        "RelationshipType": rel_type,
        "EvidenceSource": evidence_source,
        "Confidence": confidence,
        "SourceComponent": source_component,
        "LineNo": str(line_no) if line_no else None,
        "DynamicFlag": "X" if dynamic else "",
        "Detail": detail,
        "Truncated": "X" if truncated else "",
        "TotalRelations": str(total_relations),
        "Message": message,
    }
    return row


def _wrap_odata(rows):
    return {"d": {"results": rows}}


# ---------------------------------------------------------------------------
# Parsing tests
# ---------------------------------------------------------------------------

class TestParseDepdendencies:
    """Tests for ODataSAPConnectionProvider._parse_dependencies()."""

    def setup_method(self):
        self.provider = _make_provider()

    def test_empty_result_returns_zero_dependencies(self):
        payload = _wrap_odata([])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.returned_relations == 0
        assert result.total_relations == 0
        assert result.truncated is False
        assert result.relationships == []

    def test_calls_function_relationship(self):
        payload = _wrap_odata([_make_dep_row(rel_type="CALLS_FUNCTION")])
        result = self.provider._parse_dependencies(payload, "PROG", "ZFI_REPORT")
        assert len(result.relationships) == 1
        assert result.relationships[0].relationship_type == DependencyRelationshipType.CALLS_FUNCTION

    def test_calls_program_relationship(self):
        payload = _wrap_odata([_make_dep_row(rel_type="CALLS_PROGRAM", target_type="PROG", target_name="ZSUB")])
        result = self.provider._parse_dependencies(payload, "PROG", "ZMAIN")
        assert result.relationships[0].relationship_type == DependencyRelationshipType.CALLS_PROGRAM

    def test_includes_relationship(self):
        payload = _wrap_odata([_make_dep_row(rel_type="INCLUDES", target_type="PROG", target_name="ZINCL")])
        result = self.provider._parse_dependencies(payload, "PROG", "ZMAIN")
        assert result.relationships[0].relationship_type == DependencyRelationshipType.INCLUDES

    def test_uses_class_relationship(self):
        payload = _wrap_odata([_make_dep_row(rel_type="USES_CLASS", target_type="CLAS", target_name="ZCL_HELPER")])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].relationship_type == DependencyRelationshipType.USES_CLASS

    def test_implements_interface_relationship(self):
        payload = _wrap_odata([_make_dep_row(rel_type="IMPLEMENTS_INTERFACE", target_type="INTF", target_name="ZIF_BASE")])
        result = self.provider._parse_dependencies(payload, "CLAS", "ZCL_IMPL")
        assert result.relationships[0].relationship_type == DependencyRelationshipType.IMPLEMENTS_INTERFACE

    def test_inherits_class_relationship(self):
        payload = _wrap_odata([_make_dep_row(rel_type="INHERITS_CLASS", target_type="CLAS", target_name="ZCL_BASE")])
        result = self.provider._parse_dependencies(payload, "CLAS", "ZCL_CHILD")
        assert result.relationships[0].relationship_type == DependencyRelationshipType.INHERITS_CLASS

    def test_reads_table_relationship(self):
        payload = _wrap_odata([_make_dep_row(rel_type="READS_TABLE", target_type="TABL", target_name="ZTABLE1")])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].relationship_type == DependencyRelationshipType.READS_TABLE

    def test_writes_table_relationship(self):
        payload = _wrap_odata([_make_dep_row(rel_type="WRITES_TABLE", target_type="TABL", target_name="ZTABLE2")])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].relationship_type == DependencyRelationshipType.WRITES_TABLE

    def test_uses_data_element_relationship(self):
        payload = _wrap_odata([_make_dep_row(rel_type="USES_DATA_ELEMENT", target_type="DTEL", target_name="ZFIELD")])
        result = self.provider._parse_dependencies(payload, "TABL", "ZTABLE")
        assert result.relationships[0].relationship_type == DependencyRelationshipType.USES_DATA_ELEMENT

    def test_uses_domain_relationship(self):
        payload = _wrap_odata([_make_dep_row(rel_type="USES_DOMAIN", target_type="DOMA", target_name="ZDOMAIN")])
        result = self.provider._parse_dependencies(payload, "DTEL", "ZFIELD")
        assert result.relationships[0].relationship_type == DependencyRelationshipType.USES_DOMAIN

    def test_transaction_target_relationship(self):
        payload = _wrap_odata([_make_dep_row(rel_type="TRANSACTION_TARGET", target_type="PROG", target_name="ZSAPMAIN")])
        result = self.provider._parse_dependencies(payload, "TRAN", "ZMY_TRAN")
        assert result.relationships[0].relationship_type == DependencyRelationshipType.TRANSACTION_TARGET

    def test_exact_confidence(self):
        payload = _wrap_odata([_make_dep_row(confidence="EXACT")])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].confidence == DependencyConfidence.EXACT

    def test_high_confidence(self):
        payload = _wrap_odata([_make_dep_row(confidence="HIGH")])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].confidence == DependencyConfidence.HIGH

    def test_medium_confidence(self):
        payload = _wrap_odata([_make_dep_row(confidence="MEDIUM")])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].confidence == DependencyConfidence.MEDIUM

    def test_low_confidence(self):
        payload = _wrap_odata([_make_dep_row(confidence="LOW")])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].confidence == DependencyConfidence.LOW

    def test_unknown_confidence_defaults_to_medium(self):
        payload = _wrap_odata([_make_dep_row(confidence="UNKNOWN_FUTURE_VALUE")])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].confidence == DependencyConfidence.MEDIUM

    def test_dynamic_flag_true_x_notation(self):
        row = _make_dep_row(dynamic=True)
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].dynamic is True

    def test_dynamic_flag_false_blank_notation(self):
        row = _make_dep_row(dynamic=False)
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].dynamic is False

    def test_dynamic_flag_true_string_true(self):
        row = _make_dep_row(dynamic=False)
        row["DynamicFlag"] = "true"
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].dynamic is True

    def test_dynamic_flag_false_string_false(self):
        row = _make_dep_row(dynamic=False)
        row["DynamicFlag"] = "false"
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].dynamic is False

    def test_truncated_flag_preserved(self):
        row = _make_dep_row(truncated=True)
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.truncated is True

    def test_total_relations_parsed(self):
        row = _make_dep_row(total_relations=42)
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.total_relations == 42

    def test_line_no_parsed_as_integer(self):
        row = _make_dep_row(line_no=123)
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].line_no == 123

    def test_evidence_source_preserved(self):
        row = _make_dep_row(evidence_source="DDIC")
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].evidence_source == "DDIC"

    def test_source_component_preserved(self):
        row = _make_dep_row(source_component="ZFI_REPORT=====CP")
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZFI_REPORT")
        assert result.relationships[0].source_component == "ZFI_REPORT=====CP"

    def test_detail_preserved(self):
        row = _make_dep_row(detail="CALL FUNCTION 'ZFI_GET_DATA'")
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.relationships[0].detail == "CALL FUNCTION 'ZFI_GET_DATA'"

    def test_unknown_relationship_type_skipped(self):
        rows = [
            _make_dep_row(rel_type="UNKNOWN_FUTURE_REL_TYPE"),
            _make_dep_row(rel_type="CALLS_FUNCTION"),
        ]
        payload = _wrap_odata(rows)
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        # Unknown type skipped; CALLS_FUNCTION kept
        assert result.returned_relations == 1
        assert result.relationships[0].relationship_type == DependencyRelationshipType.CALLS_FUNCTION

    def test_blank_target_row_skipped(self):
        rows = [
            _make_dep_row(target_name=""),  # blank target
            _make_dep_row(rel_type="CALLS_FUNCTION"),
        ]
        payload = _wrap_odata(rows)
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.returned_relations == 1

    def test_multiple_rows_all_returned(self):
        rows = [
            _make_dep_row(rel_type="CALLS_FUNCTION", target_name="ZFI_GET1"),
            _make_dep_row(rel_type="READS_TABLE", target_type="TABL", target_name="ZTABLE1"),
            _make_dep_row(rel_type="USES_CLASS", target_type="CLAS", target_name="ZCL_HELPER"),
        ]
        payload = _wrap_odata(rows)
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.returned_relations == 3

    def test_missing_d_envelope_raises(self):
        with pytest.raises(DependencyDataIntegrityError):
            self.provider._parse_dependencies({"wrong": "structure"}, "PROG", "ZTEST")

    def test_missing_results_list_raises(self):
        with pytest.raises(DependencyDataIntegrityError):
            self.provider._parse_dependencies({"d": {"notresults": []}}, "PROG", "ZTEST")

    def test_results_not_list_raises(self):
        with pytest.raises(DependencyDataIntegrityError):
            self.provider._parse_dependencies({"d": {"results": "notalist"}}, "PROG", "ZTEST")

    def test_system_id_extracted_from_first_row(self):
        row = _make_dep_row(system_id="D01")
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.system_id == "D01"

    def test_client_extracted_from_first_row(self):
        row = _make_dep_row(client="800")
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        assert result.client == "800"

    def test_metadata_ignored(self):
        """__metadata key should be ignored (standard OData V2 field)."""
        row = _make_dep_row()
        row["__metadata"] = {"type": "ZNCOREIQ_REPO_SRV.DependencyRelation", "uri": "https://sap.example.com/..."}
        payload = _wrap_odata([row])
        result = self.provider._parse_dependencies(payload, "PROG", "ZTEST")
        # Should parse normally without error
        assert result.returned_relations == 1


class TestProviderHTTPErrors:
    """Tests for HTTP error code handling in get_object_dependencies()."""

    def setup_method(self):
        self.provider = _make_provider()

    async def test_401_raises_sap_authentication_error(self):
        import httpx
        mock_response = MagicMock()
        mock_response.status_code = 401

        async def mock_get(*args, **kwargs):
            return mock_response

        with patch.object(self.provider, "_build_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            with pytest.raises(SAPAuthenticationError):
                await self.provider.get_object_dependencies("PROG", "ZTEST")

    async def test_403_raises_sap_authentication_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch.object(self.provider, "_build_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            with pytest.raises(SAPAuthenticationError):
                await self.provider.get_object_dependencies("PROG", "ZTEST")

    async def test_404_raises_gateway_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch.object(self.provider, "_build_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            with pytest.raises(SAPGatewayError):
                await self.provider.get_object_dependencies("PROG", "ZTEST")

    async def test_500_returns_empty_result_with_message(self):
        """HTTP 500 from DependencyRelationsSet returns empty result (graceful fallback).

        SAP may return 500 for objects not in its dependency tables rather
        than an empty results list. The provider now returns empty deps
        with a diagnostic message instead of hard-failing.
        """
        mock_response = MagicMock()
        mock_response.status_code = 500

        with patch.object(self.provider, "_build_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            result = await self.provider.get_object_dependencies("PROG", "ZTEST")
            # Graceful empty result — no exception raised
            assert result.returned_relations == 0
            assert result.total_relations == 0
            assert result.truncated is False
            assert result.message is not None
            assert "500" in result.message
            assert "PROG" in result.message
            assert "ZTEST" in result.message

    async def test_invalid_json_raises_data_integrity_error(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(side_effect=ValueError("not valid JSON"))

        with patch.object(self.provider, "_build_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            with pytest.raises(DependencyDataIntegrityError):
                await self.provider.get_object_dependencies("PROG", "ZTEST")

    async def test_200_empty_result_succeeds(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=_wrap_odata([]))

        with patch.object(self.provider, "_build_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            result = await self.provider.get_object_dependencies("PROG", "ZTEST")
            assert result.returned_relations == 0
            assert result.truncated is False

    async def test_200_with_rows_returns_dependencies(self):
        rows = [_make_dep_row(rel_type="CALLS_FUNCTION")]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=_wrap_odata(rows))

        with patch.object(self.provider, "_build_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.get = AsyncMock(return_value=mock_response)
            mock_client.return_value = mock_http

            result = await self.provider.get_object_dependencies("PROG", "ZTEST")
            assert result.returned_relations == 1
            assert result.relationships[0].relationship_type == DependencyRelationshipType.CALLS_FUNCTION

    async def test_odata_filter_includes_source_type_and_name(self):
        """Verify the OData filter string includes SourceObjectType/Name/MaxResults."""
        rows = [_make_dep_row()]
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json = MagicMock(return_value=_wrap_odata(rows))
        captured_params = {}

        async def capture_get(url, params=None, **kwargs):
            captured_params.update(params or {})
            return mock_response

        with patch.object(self.provider, "_build_client") as mock_client:
            mock_http = AsyncMock()
            mock_http.__aenter__ = AsyncMock(return_value=mock_http)
            mock_http.__aexit__ = AsyncMock(return_value=False)
            mock_http.get = capture_get
            mock_client.return_value = mock_http

            await self.provider.get_object_dependencies("PROG", "ZFI_REPORT", max_results=500)

        odata_filter = captured_params.get("$filter", "")
        assert "SourceObjectType eq 'PROG'" in odata_filter
        assert "SourceObjectName eq 'ZFI_REPORT'" in odata_filter
        assert "MaxResults eq 500" in odata_filter
