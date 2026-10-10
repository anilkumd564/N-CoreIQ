"""Phase 8 — MCP tool tests for dependency intelligence.

Tests:
  - get_dependencies tool (registration, call, limits, errors)
  - traverse_dependencies tool (registration, call, MCP depth/node/edge limits)
  - find_dependents tool (registration, call, scope required)
  - analyze_dependency_impact tool (registration, call, deterministic, no AI)
  - MCP tools call RepositoryDependencyService, NOT ODataSAPConnectionProvider
  - Error sanitisation (no credentials/traces in MCP errors)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.mcp.errors import MCPToolError, map_exception
from app.mcp.server import NCoreIQMCPServer
from app.models.phase8 import (
    DependencyConfidence,
    DependencyGraph,
    DependencyRelationshipType,
    DirectDependencyResult,
    DependencyImpactResult,
    GraphStatistics,
    RepositoryDependency,
    RepositoryObjectReference,
    ReverseDependencyResult,
    ReverseDependent,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_dep_svc():
    return AsyncMock()


def _make_server(dep_svc=None):
    """Create an NCoreIQMCPServer with a mocked dependency service."""
    provider = AsyncMock()
    discovery = AsyncMock()
    object_svc = AsyncMock()
    job_svc = MagicMock()
    if dep_svc is None:
        dep_svc = _make_dep_svc()
    return NCoreIQMCPServer(
        sap_provider=provider,
        discovery_svc=discovery,
        object_svc=object_svc,
        job_svc=job_svc,
        dep_svc=dep_svc,
    )


def _ref(obj_type: str, obj_name: str) -> RepositoryObjectReference:
    return RepositoryObjectReference(sap_object_type=obj_type, object_name=obj_name)


def _empty_dep_result(obj_type="PROG", obj_name="ZTEST") -> DirectDependencyResult:
    return DirectDependencyResult(
        system_id="S4H",
        client="100",
        source_object_type=obj_type,
        source_object_name=obj_name,
        total_relations=0,
        returned_relations=0,
        truncated=False,
    )


def _dep_result_with_rel(obj_type="PROG", obj_name="ZTEST") -> DirectDependencyResult:
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


def _empty_graph() -> DependencyGraph:
    return DependencyGraph(
        root=_ref("PROG", "ZTEST"),
        requested_max_depth=2,
        depth_reached=1,
        statistics=GraphStatistics(node_count=2, edge_count=1, sap_calls=2),
    )


def _empty_reverse() -> ReverseDependencyResult:
    return ReverseDependencyResult(
        target_object=_ref("FUNC", "ZFI_GET"),
        candidate_count=5,
        matched_dependents=0,
        statistics=GraphStatistics(),
    )


def _empty_impact() -> DependencyImpactResult:
    return DependencyImpactResult(
        target_object=_ref("TABL", "ZTABLE"),
        direct_dependent_count=0,
        total_impacted_objects=0,
    )


# ---------------------------------------------------------------------------
# 1. Tool registration
# ---------------------------------------------------------------------------

class TestPhase8ToolRegistration:
    EXPECTED_DEP_TOOLS = {
        "get_dependencies",
        "traverse_dependencies",
        "find_dependents",
        "analyze_dependency_impact",
    }

    async def test_phase8_tools_registered(self):
        srv = _make_server()
        tools = await srv.mcp.list_tools()
        names = {t.name for t in tools}
        for tool in self.EXPECTED_DEP_TOOLS:
            assert tool in names, f"Missing Phase 8 tool: {tool}"

    async def test_phase8_tools_read_only(self):
        srv = _make_server()
        tools = await srv.mcp.list_tools()
        for t in tools:
            if t.name in self.EXPECTED_DEP_TOOLS:
                ann = getattr(t, "annotations", None)
                if ann is not None:
                    assert getattr(ann, "readOnlyHint", None) is True, (
                        f"Phase 8 tool {t.name} should be readOnlyHint=True"
                    )


# ---------------------------------------------------------------------------
# 2. get_dependencies
# ---------------------------------------------------------------------------

class TestGetDependenciesMCP:
    async def test_empty_dependencies(self):
        dep_svc = _make_dep_svc()
        dep_svc.get_direct_dependencies = AsyncMock(return_value=_empty_dep_result())
        srv = _make_server(dep_svc=dep_svc)
        result = await srv._get_dependencies("PROG", "ZTEST", None, None, 200)
        assert result["success"] is True
        assert result["returned_relations"] == 0
        assert result["relationships"] == []

    async def test_with_dependency(self):
        dep_svc = _make_dep_svc()
        dep_svc.get_direct_dependencies = AsyncMock(return_value=_dep_result_with_rel())
        srv = _make_server(dep_svc=dep_svc)
        result = await srv._get_dependencies("PROG", "ZTEST", None, None, 200)
        assert result["returned_relations"] == 1
        assert result["relationships"][0]["relationship_type"] == "CALLS_FUNCTION"
        assert result["relationships"][0]["confidence"] == "HIGH"
        assert result["relationships"][0]["dynamic"] is False

    async def test_mcp_limit_enforced(self):
        """MCP hard limit of 1000 should be enforced."""
        dep_svc = _make_dep_svc()
        dep_svc.get_direct_dependencies = AsyncMock(return_value=_empty_dep_result())
        srv = _make_server(dep_svc=dep_svc)
        await srv._get_dependencies("PROG", "ZTEST", None, None, 99999)
        call_kwargs = dep_svc.get_direct_dependencies.call_args.kwargs
        assert call_kwargs["max_results"] <= 1000

    async def test_calls_dep_service_not_provider(self):
        """get_dependencies must call dep service, not OData provider directly."""
        dep_svc = _make_dep_svc()
        dep_svc.get_direct_dependencies = AsyncMock(return_value=_empty_dep_result())
        srv = _make_server(dep_svc=dep_svc)
        await srv._get_dependencies("PROG", "ZTEST", None, None, 100)
        dep_svc.get_direct_dependencies.assert_called_once()
        # The raw SAP provider should NOT be called
        srv._provider.get_object_dependencies.assert_not_called()

    async def test_empty_type_raises(self):
        srv = _make_server()
        with pytest.raises(MCPToolError) as exc_info:
            await srv._get_dependencies("", "ZTEST", None, None, 100)
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_empty_name_raises(self):
        srv = _make_server()
        with pytest.raises(MCPToolError) as exc_info:
            await srv._get_dependencies("PROG", "", None, None, 100)
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_no_dep_service_raises(self):
        srv = _make_server(dep_svc=None)
        srv._dep = None
        with pytest.raises(MCPToolError) as exc_info:
            await srv._get_dependencies("PROG", "ZTEST", None, None, 100)
        assert exc_info.value.code == "SERVICE_UNAVAILABLE"

    async def test_sap_error_mapped_to_mcp_error(self):
        from app.sap.exceptions import SAPConnectionError
        dep_svc = _make_dep_svc()
        dep_svc.get_direct_dependencies = AsyncMock(side_effect=SAPConnectionError("down"))
        srv = _make_server(dep_svc=dep_svc)
        with pytest.raises(MCPToolError) as exc_info:
            await srv._get_dependencies("PROG", "ZTEST", None, None, 100)
        assert exc_info.value.code == "SAP_UNAVAILABLE"

    async def test_result_contains_no_credentials(self):
        dep_svc = _make_dep_svc()
        dep_svc.get_direct_dependencies = AsyncMock(return_value=_empty_dep_result())
        srv = _make_server(dep_svc=dep_svc)
        result = await srv._get_dependencies("PROG", "ZTEST", None, None, 100)
        result_str = str(result).lower()
        assert "password" not in result_str
        assert "authorization" not in result_str

    async def test_relationship_type_filter_passed(self):
        dep_svc = _make_dep_svc()
        dep_svc.get_direct_dependencies = AsyncMock(return_value=_empty_dep_result())
        srv = _make_server(dep_svc=dep_svc)
        await srv._get_dependencies("PROG", "ZTEST", ["CALLS_FUNCTION"], None, 100)
        call_kwargs = dep_svc.get_direct_dependencies.call_args.kwargs
        assert call_kwargs.get("relationship_types") == ["CALLS_FUNCTION"]

    async def test_min_confidence_passed(self):
        dep_svc = _make_dep_svc()
        dep_svc.get_direct_dependencies = AsyncMock(return_value=_empty_dep_result())
        srv = _make_server(dep_svc=dep_svc)
        await srv._get_dependencies("PROG", "ZTEST", None, "HIGH", 100)
        call_kwargs = dep_svc.get_direct_dependencies.call_args.kwargs
        assert call_kwargs.get("min_confidence") == "HIGH"


# ---------------------------------------------------------------------------
# 3. traverse_dependencies
# ---------------------------------------------------------------------------

class TestTraverseDependenciesMCP:
    async def test_successful_traversal(self):
        dep_svc = _make_dep_svc()
        dep_svc.traverse_dependencies = AsyncMock(return_value=_empty_graph())
        srv = _make_server(dep_svc=dep_svc)
        result = await srv._traverse_dependencies("PROG", "ZTEST", 2, None, None, 100, 300)
        assert result["success"] is True
        assert "nodes" in result
        assert "edges" in result
        assert "sap_calls" in result

    async def test_mcp_depth_limit_enforced(self):
        """MCP max depth of 3 should be enforced."""
        dep_svc = _make_dep_svc()
        dep_svc.traverse_dependencies = AsyncMock(return_value=_empty_graph())
        srv = _make_server(dep_svc=dep_svc)
        await srv._traverse_dependencies("PROG", "ZTEST", 99, None, None, 100, 300)
        req = dep_svc.traverse_dependencies.call_args[0][0]
        assert req.max_depth <= 3

    async def test_mcp_node_limit_enforced(self):
        dep_svc = _make_dep_svc()
        dep_svc.traverse_dependencies = AsyncMock(return_value=_empty_graph())
        srv = _make_server(dep_svc=dep_svc)
        await srv._traverse_dependencies("PROG", "ZTEST", 2, None, None, 99999, 300)
        req = dep_svc.traverse_dependencies.call_args[0][0]
        assert req.max_nodes <= 200

    async def test_mcp_edge_limit_enforced(self):
        dep_svc = _make_dep_svc()
        dep_svc.traverse_dependencies = AsyncMock(return_value=_empty_graph())
        srv = _make_server(dep_svc=dep_svc)
        await srv._traverse_dependencies("PROG", "ZTEST", 2, None, None, 100, 99999)
        req = dep_svc.traverse_dependencies.call_args[0][0]
        assert req.max_edges <= 500

    async def test_calls_dep_service_not_provider(self):
        dep_svc = _make_dep_svc()
        dep_svc.traverse_dependencies = AsyncMock(return_value=_empty_graph())
        srv = _make_server(dep_svc=dep_svc)
        await srv._traverse_dependencies("PROG", "ZTEST", 2, None, None, 100, 300)
        dep_svc.traverse_dependencies.assert_called_once()
        srv._provider.get_object_dependencies.assert_not_called()

    async def test_invalid_request_maps_to_mcp_error(self):
        dep_svc = _make_dep_svc()
        dep_svc.traverse_dependencies = AsyncMock(side_effect=ValueError("bad request"))
        srv = _make_server(dep_svc=dep_svc)
        with pytest.raises(MCPToolError) as exc_info:
            await srv._traverse_dependencies("PROG", "ZTEST", 2, None, None, 100, 300)
        assert exc_info.value.code in ("INVALID_ARGUMENT", "INTERNAL_ERROR")


# ---------------------------------------------------------------------------
# 4. find_dependents
# ---------------------------------------------------------------------------

class TestFindDependentsMCP:
    async def test_no_dependents_is_success(self):
        dep_svc = _make_dep_svc()
        dep_svc.get_reverse_dependencies = AsyncMock(return_value=_empty_reverse())
        srv = _make_server(dep_svc=dep_svc)
        result = await srv._find_dependents(
            "FUNC", "ZFI_GET", ["ZTEST"], None, False, None, None, 50
        )
        assert result["success"] is True
        assert result["matched_dependents"] == 0
        assert result["dependents"] == []

    async def test_calls_dep_service(self):
        dep_svc = _make_dep_svc()
        dep_svc.get_reverse_dependencies = AsyncMock(return_value=_empty_reverse())
        srv = _make_server(dep_svc=dep_svc)
        await srv._find_dependents("FUNC", "ZFI_GET", ["ZTEST"], None, False, None, None, 50)
        dep_svc.get_reverse_dependencies.assert_called_once()
        srv._provider.get_object_dependencies.assert_not_called()

    async def test_missing_scope_raises(self):
        srv = _make_server()
        with pytest.raises(MCPToolError) as exc_info:
            await srv._find_dependents("FUNC", "ZFI_GET", None, None, False, None, None, 50)
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_candidate_limit_enforced(self):
        dep_svc = _make_dep_svc()
        dep_svc.get_reverse_dependencies = AsyncMock(return_value=_empty_reverse())
        srv = _make_server(dep_svc=dep_svc)
        await srv._find_dependents("FUNC", "ZFI_GET", ["ZTEST"], None, False, None, None, 99999)
        req = dep_svc.get_reverse_dependencies.call_args[0][0]
        assert req.max_candidates <= 50

    async def test_sap_error_mapped(self):
        from app.sap.exceptions import SAPConnectionError
        dep_svc = _make_dep_svc()
        dep_svc.get_reverse_dependencies = AsyncMock(side_effect=SAPConnectionError("down"))
        srv = _make_server(dep_svc=dep_svc)
        with pytest.raises(MCPToolError) as exc_info:
            await srv._find_dependents("FUNC", "ZFI_GET", ["ZTEST"], None, False, None, None, 50)
        assert exc_info.value.code == "SAP_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 5. analyze_dependency_impact
# ---------------------------------------------------------------------------

class TestAnalyzeDependencyImpactMCP:
    async def test_zero_impact_is_success(self):
        dep_svc = _make_dep_svc()
        dep_svc.get_impact_analysis = AsyncMock(return_value=_empty_impact())
        srv = _make_server(dep_svc=dep_svc)
        result = await srv._analyze_dependency_impact(
            "TABL", "ZTABLE", ["ZTEST"], None, False, None, None
        )
        assert result["success"] is True
        assert result["direct_dependent_count"] == 0
        assert result["total_impacted_objects"] == 0

    async def test_result_is_deterministic_not_ai(self):
        """Impact result must not contain AI/LLM fields."""
        dep_svc = _make_dep_svc()
        dep_svc.get_impact_analysis = AsyncMock(return_value=_empty_impact())
        srv = _make_server(dep_svc=dep_svc)
        result = await srv._analyze_dependency_impact(
            "TABL", "ZTABLE", ["ZTEST"], None, False, None, None
        )
        result_str = str(result).lower()
        for prohibited in ("llm_analysis", "ai_score", "gpt_response", "semantic_"):
            assert prohibited not in result_str

    async def test_calls_dep_service_not_provider(self):
        dep_svc = _make_dep_svc()
        dep_svc.get_impact_analysis = AsyncMock(return_value=_empty_impact())
        srv = _make_server(dep_svc=dep_svc)
        await srv._analyze_dependency_impact("TABL", "ZTABLE", ["ZTEST"], None, False, None, None)
        dep_svc.get_impact_analysis.assert_called_once()
        srv._provider.get_object_dependencies.assert_not_called()

    async def test_missing_scope_raises(self):
        srv = _make_server()
        with pytest.raises(MCPToolError) as exc_info:
            await srv._analyze_dependency_impact("TABL", "ZTABLE", None, None, False, None, None)
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_sap_error_mapped(self):
        from app.sap.exceptions import SAPCommunicationError
        dep_svc = _make_dep_svc()
        dep_svc.get_impact_analysis = AsyncMock(side_effect=SAPCommunicationError("error"))
        srv = _make_server(dep_svc=dep_svc)
        with pytest.raises(MCPToolError) as exc_info:
            await srv._analyze_dependency_impact("TABL", "ZTABLE", ["ZTEST"], None, False, None, None)
        assert exc_info.value.code == "SAP_UNAVAILABLE"


# ---------------------------------------------------------------------------
# 6. Error mapping for Phase 8 exceptions
# ---------------------------------------------------------------------------

class TestPhase8ErrorMapping:
    def test_dependency_object_not_found_mapped(self):
        from app.sap.exceptions import DependencyObjectNotFoundError
        exc = DependencyObjectNotFoundError("PROG", "ZBAD")
        mapped = map_exception(exc, "test")
        assert mapped.code == "DEPENDENCY_OBJECT_NOT_FOUND"
        assert "PROG" in mapped.message
        assert "ZBAD" in mapped.message

    def test_dependency_scope_too_large_mapped(self):
        from app.sap.exceptions import DependencyScopeTooLargeError
        exc = DependencyScopeTooLargeError("scope too large")
        mapped = map_exception(exc, "test")
        assert mapped.code == "DEPENDENCY_SCOPE_TOO_LARGE"

    def test_dependency_traversal_limit_mapped(self):
        from app.sap.exceptions import DependencyTraversalLimitError
        exc = DependencyTraversalLimitError("limit exceeded")
        mapped = map_exception(exc, "test")
        assert mapped.code == "DEPENDENCY_TRAVERSAL_LIMIT"

    def test_dependency_data_error_mapped(self):
        from app.sap.exceptions import DependencyDataIntegrityError
        exc = DependencyDataIntegrityError("bad data")
        mapped = map_exception(exc, "test")
        assert mapped.code == "DEPENDENCY_DATA_ERROR"

    def test_dependency_provider_error_mapped(self):
        from app.sap.exceptions import DependencyProviderError
        exc = DependencyProviderError("provider failed")
        mapped = map_exception(exc, "test")
        assert mapped.code == "SAP_UNAVAILABLE"

    def test_no_sap_internals_in_mapped_error(self):
        """Phase 8 errors must never expose SAP internal details."""
        from app.sap.exceptions import DependencyDataIntegrityError
        exc = DependencyDataIntegrityError(
            "innererror: ABAP stack trace: /SAP/BC/... password=mysecret"
        )
        mapped = map_exception(exc, "test")
        # The message should be sanitised
        assert "mysecret" not in mapped.message
        assert "password" not in mapped.message.lower() or "redacted" in mapped.message.lower()
