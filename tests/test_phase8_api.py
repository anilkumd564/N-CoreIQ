"""Phase 8 — REST API endpoint tests for dependency intelligence.

Tests:
  - GET /api/v1/repository/objects/{type}/{name}/dependencies
  - POST /api/v1/repository/dependencies/traverse
  - POST /api/v1/repository/dependencies/reverse
  - POST /api/v1/repository/dependencies/impact

Uses FastAPI TestClient with mocked RepositoryDependencyService.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.phase8 import (
    DependencyConfidence,
    DependencyGraph,
    DependencyRelationshipType,
    DirectDependencyResult,
    DependencyImpactResult,
    GraphStatistics,
    RepositoryObjectReference,
    ReverseDependencyResult,
    RepositoryDependency,
)
from app.sap.exceptions import (
    DependencyDataIntegrityError,
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPConnectionError,
    SAPProviderNotImplementedError,
)

client = TestClient(app)

DEP_BASE = "/api/v1/repository/objects"
TRAVERSE_URL = "/api/v1/repository/dependencies/traverse"
REVERSE_URL = "/api/v1/repository/dependencies/reverse"
IMPACT_URL = "/api/v1/repository/dependencies/impact"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_result(obj_type="PROG", obj_name="ZTEST") -> DirectDependencyResult:
    return DirectDependencyResult(
        system_id="S4H",
        client="100",
        source_object_type=obj_type,
        source_object_name=obj_name,
        total_relations=0,
        returned_relations=0,
        truncated=False,
    )


def _result_with_dep(obj_type="PROG", obj_name="ZTEST") -> DirectDependencyResult:
    dep = RepositoryDependency(
        source_object_type=obj_type,
        source_object_name=obj_name,
        target_object_type="FUNC",
        target_object_name="ZFI_GET_DATA",
        relationship_type=DependencyRelationshipType.CALLS_FUNCTION,
        confidence=DependencyConfidence.HIGH,
        evidence_source="SOURCE_STATIC",
        dynamic=False,
    )
    return DirectDependencyResult(
        system_id="S4H",
        client="100",
        source_object_type=obj_type,
        source_object_name=obj_name,
        total_relations=1,
        returned_relations=1,
        truncated=False,
        relationships=[dep],
    )


def _empty_graph(obj_type="PROG", obj_name="ZTEST") -> DependencyGraph:
    return DependencyGraph(
        root=RepositoryObjectReference(sap_object_type=obj_type, object_name=obj_name),
        requested_max_depth=2,
        depth_reached=0,
        statistics=GraphStatistics(node_count=1, edge_count=0, sap_calls=1),
    )


def _empty_reverse() -> ReverseDependencyResult:
    return ReverseDependencyResult(
        target_object=RepositoryObjectReference(sap_object_type="FUNC", object_name="ZFI_GET"),
        candidate_count=5,
        matched_dependents=0,
    )


def _empty_impact() -> DependencyImpactResult:
    return DependencyImpactResult(
        target_object=RepositoryObjectReference(sap_object_type="TABL", object_name="ZTABLE"),
        direct_dependent_count=0,
        total_impacted_objects=0,
    )


# ---------------------------------------------------------------------------
# 1. GET /dependencies — direct dependency endpoint
# ---------------------------------------------------------------------------

class TestGetDirectDependencies:
    def test_200_empty_dependencies(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            return_value=_empty_result(),
        ):
            resp = client.get(f"{DEP_BASE}/PROG/ZFI_REPORT/dependencies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned_relations"] == 0
        assert data["relationships"] == []
        assert data["truncated"] is False

    def test_200_with_dependencies(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            return_value=_result_with_dep(),
        ):
            resp = client.get(f"{DEP_BASE}/PROG/ZFI_REPORT/dependencies")
        assert resp.status_code == 200
        data = resp.json()
        assert data["returned_relations"] == 1
        assert len(data["relationships"]) == 1
        assert data["relationships"][0]["relationship_type"] == "CALLS_FUNCTION"
        assert data["relationships"][0]["confidence"] == "HIGH"

    def test_422_unsupported_object_type(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            side_effect=ValueError("Unsupported dependency source object type 'UNKN'."),
        ):
            resp = client.get(f"{DEP_BASE}/UNKN/ZBAD/dependencies")
        assert resp.status_code == 422

    def test_422_blank_object_name_rejected_by_service(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            side_effect=ValueError("Object name must not be blank."),
        ):
            resp = client.get(f"{DEP_BASE}/PROG/ /dependencies")
        assert resp.status_code == 422

    def test_503_sap_connection_error(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            side_effect=SAPConnectionError("SAP not reachable"),
        ):
            resp = client.get(f"{DEP_BASE}/PROG/ZTEST/dependencies")
        assert resp.status_code == 503

    def test_503_sap_auth_error(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            side_effect=SAPAuthenticationError("Auth failed"),
        ):
            resp = client.get(f"{DEP_BASE}/PROG/ZTEST/dependencies")
        assert resp.status_code == 503

    def test_503_sap_communication_error(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            side_effect=SAPCommunicationError("Comm error"),
        ):
            resp = client.get(f"{DEP_BASE}/PROG/ZTEST/dependencies")
        assert resp.status_code == 503

    def test_501_provider_not_implemented(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            side_effect=SAPProviderNotImplementedError("Not implemented"),
        ):
            resp = client.get(f"{DEP_BASE}/PROG/ZTEST/dependencies")
        assert resp.status_code == 501

    def test_503_data_integrity_error(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            side_effect=DependencyDataIntegrityError("Malformed"),
        ):
            resp = client.get(f"{DEP_BASE}/PROG/ZTEST/dependencies")
        assert resp.status_code == 503

    def test_no_secret_leakage(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            side_effect=SAPConnectionError("auth failure"),
        ):
            resp = client.get(f"{DEP_BASE}/PROG/ZTEST/dependencies")
        body = resp.text.lower()
        assert "password" not in body
        assert "traceback" not in body
        assert "authorization" not in body

    def test_relationship_type_filter_passed(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            return_value=_empty_result(),
        ) as mock_svc:
            resp = client.get(
                f"{DEP_BASE}/PROG/ZTEST/dependencies",
                params={"relationship_type": ["CALLS_FUNCTION", "READS_TABLE"]}
            )
        assert resp.status_code == 200
        call_kwargs = mock_svc.call_args.kwargs
        assert "CALLS_FUNCTION" in (call_kwargs.get("relationship_types") or [])

    def test_min_confidence_filter_passed(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_direct_dependencies",
            new_callable=AsyncMock,
            return_value=_empty_result(),
        ) as mock_svc:
            resp = client.get(
                f"{DEP_BASE}/PROG/ZTEST/dependencies",
                params={"min_confidence": "HIGH"}
            )
        assert resp.status_code == 200
        call_kwargs = mock_svc.call_args.kwargs
        assert call_kwargs.get("min_confidence") == "HIGH"


# ---------------------------------------------------------------------------
# 2. POST /traverse
# ---------------------------------------------------------------------------

class TestTraverseDependencies:
    def _traverse_body(self, obj_type="PROG", obj_name="ZFI_REPORT", max_depth=2):
        return {
            "root_object": {"sap_object_type": obj_type, "object_name": obj_name},
            "max_depth": max_depth,
        }

    def test_200_empty_traversal(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.traverse_dependencies",
            new_callable=AsyncMock,
            return_value=_empty_graph(),
        ):
            resp = client.post(TRAVERSE_URL, json=self._traverse_body())
        assert resp.status_code == 200
        data = resp.json()
        assert "root" in data
        assert "nodes" in data
        assert "edges" in data
        assert "statistics" in data

    def test_422_invalid_max_depth(self):
        body = self._traverse_body(max_depth=99)
        resp = client.post(TRAVERSE_URL, json=body)
        assert resp.status_code == 422

    def test_422_missing_root_object(self):
        resp = client.post(TRAVERSE_URL, json={"max_depth": 2})
        assert resp.status_code == 422

    def test_503_sap_error(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.traverse_dependencies",
            new_callable=AsyncMock,
            side_effect=SAPConnectionError("unreachable"),
        ):
            resp = client.post(TRAVERSE_URL, json=self._traverse_body())
        assert resp.status_code == 503

    def test_traversal_with_relationship_filter(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.traverse_dependencies",
            new_callable=AsyncMock,
            return_value=_empty_graph(),
        ):
            body = self._traverse_body()
            body["relationship_types"] = ["CALLS_FUNCTION"]
            resp = client.post(TRAVERSE_URL, json=body)
        assert resp.status_code == 200


# ---------------------------------------------------------------------------
# 3. POST /reverse
# ---------------------------------------------------------------------------

class TestReverseDepedencies:
    def _reverse_body(self):
        return {
            "target_object": {"sap_object_type": "FUNC", "object_name": "ZFI_GET_DATA"},
            "scope": {"packages": ["ZFINANCE"]},
        }

    def test_200_no_dependents_is_success(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_reverse_dependencies",
            new_callable=AsyncMock,
            return_value=_empty_reverse(),
        ):
            resp = client.post(REVERSE_URL, json=self._reverse_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["matched_dependents"] == 0

    def test_422_missing_scope(self):
        body = {"target_object": {"sap_object_type": "FUNC", "object_name": "ZFI_GET_DATA"}}
        resp = client.post(REVERSE_URL, json=body)
        assert resp.status_code == 422

    def test_422_empty_scope(self):
        body = {
            "target_object": {"sap_object_type": "FUNC", "object_name": "ZFI_GET_DATA"},
            "scope": {"packages": []},
        }
        resp = client.post(REVERSE_URL, json=body)
        assert resp.status_code == 422

    def test_503_sap_error(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_reverse_dependencies",
            new_callable=AsyncMock,
            side_effect=SAPConnectionError("SAP down"),
        ):
            resp = client.post(REVERSE_URL, json=self._reverse_body())
        assert resp.status_code == 503


# ---------------------------------------------------------------------------
# 4. POST /impact
# ---------------------------------------------------------------------------

class TestImpactAnalysis:
    def _impact_body(self):
        return {
            "object": {"sap_object_type": "TABL", "object_name": "ZFIN_POSITIONS"},
            "scope": {"packages": ["ZFINANCE"]},
        }

    def test_200_zero_impact(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_impact_analysis",
            new_callable=AsyncMock,
            return_value=_empty_impact(),
        ):
            resp = client.post(IMPACT_URL, json=self._impact_body())
        assert resp.status_code == 200
        data = resp.json()
        assert data["direct_dependent_count"] == 0

    def test_422_missing_scope(self):
        body = {"object": {"sap_object_type": "TABL", "object_name": "ZTABLE"}}
        resp = client.post(IMPACT_URL, json=body)
        assert resp.status_code == 422

    def test_503_sap_error(self):
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_impact_analysis",
            new_callable=AsyncMock,
            side_effect=SAPCommunicationError("comm error"),
        ):
            resp = client.post(IMPACT_URL, json=self._impact_body())
        assert resp.status_code == 503

    def test_impact_response_no_ai_fields(self):
        """Impact result must not contain LLM/AI-specific fields."""
        with patch(
            "app.services.dependency.RepositoryDependencyService.get_impact_analysis",
            new_callable=AsyncMock,
            return_value=_empty_impact(),
        ):
            resp = client.post(IMPACT_URL, json=self._impact_body())
        assert resp.status_code == 200
        data = resp.json()
        # Must not contain AI-specific fields
        for prohibited in ("llm_analysis", "ai_recommendation", "gpt_response", "semantic_score"):
            assert prohibited not in data


# ---------------------------------------------------------------------------
# 5. Architecture boundary test
# ---------------------------------------------------------------------------

class TestArchitectureBoundary:
    def test_dep_module_does_not_import_httpx(self):
        """dependency.py must not import httpx directly."""
        import app.services.dependency as dep_mod
        source = open(dep_mod.__file__, encoding="utf-8").read()
        assert "import httpx" not in source
        assert "from httpx" not in source

    def test_dep_module_does_not_call_source_lines_set(self):
        """dependency.py must not call get_object_source() (no SourceLinesSet calls)."""
        import app.services.dependency as dep_mod
        source = open(dep_mod.__file__, encoding="utf-8").read()
        # These are actual code call patterns, not comments.
        # Check that no SAP source extraction calls exist in real code.
        assert "get_object_source(" not in source
        assert ".get_object_source" not in source

    def test_dep_module_does_not_call_object_metadata_set(self):
        """dependency.py must not call get_object_metadata() (no ObjectMetadataSet calls)."""
        import app.services.dependency as dep_mod
        source = open(dep_mod.__file__, encoding="utf-8").read()
        # These are actual code call patterns, not comments.
        assert "get_object_metadata(" not in source
        assert ".get_object_metadata" not in source
