"""Phase 8 — RepositoryDependencyService tests.

Tests:
  - Object normalization (upper, trim)
  - Relationship type filtering
  - Confidence filtering (ordering: EXACT=4, HIGH=3, MEDIUM=2, LOW=1)
  - Exact duplicate removal
  - Distinct evidence preservation
  - Edge grouping (strongest confidence)
  - Dynamic relationship preservation
  - Forward traversal: A→B, A→B→C, depth limits, cycles, diamond, dynamic targets
  - Traversal: max_depth, max_nodes, max_edges, request-local cache
  - Reverse lookup: one dependent, multiple dependents, none, scope
  - Impact analysis: zero/one/multiple dependents, truncation
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.phase8 import (
    DYNAMIC_TARGET_SENTINEL,
    DependencyConfidence,
    DependencyRelationshipType,
    DependencyTraversalRequest,
    DirectDependencyResult,
    ReverseDependencyRequest,
    ReverseScopeSpec,
    RepositoryObjectReference,
    RepositoryDependency,
    DependencyImpactRequest,
)
from app.services.dependency import RepositoryDependencyService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ref(obj_type: str, obj_name: str) -> RepositoryObjectReference:
    return RepositoryObjectReference(sap_object_type=obj_type, object_name=obj_name)


def _dep(
    src_type="PROG",
    src_name="ZA",
    tgt_type="FUNC",
    tgt_name="ZB",
    rel_type=DependencyRelationshipType.CALLS_FUNCTION,
    confidence=DependencyConfidence.HIGH,
    dynamic=False,
    evidence_source="SOURCE_STATIC",
    source_component=None,
    line_no=None,
    detail=None,
) -> RepositoryDependency:
    return RepositoryDependency(
        source_object_type=src_type,
        source_object_name=src_name,
        target_object_type=tgt_type,
        target_object_name=tgt_name,
        relationship_type=rel_type,
        confidence=confidence,
        dynamic=dynamic,
        evidence_source=evidence_source,
        source_component=source_component,
        line_no=line_no,
        detail=detail,
    )


def _result(
    src_type="PROG",
    src_name="ZA",
    rels=None,
    truncated=False,
    total_relations=None,
    system_id="S4H",
    client="100",
) -> DirectDependencyResult:
    rels = rels or []
    return DirectDependencyResult(
        system_id=system_id,
        client=client,
        source_object_type=src_type,
        source_object_name=src_name,
        total_relations=total_relations or len(rels),
        returned_relations=len(rels),
        truncated=truncated,
        relationships=rels,
    )


def _make_service(provider=None, discovery=None):
    if provider is None:
        provider = AsyncMock()
    if discovery is None:
        discovery = AsyncMock()
    return RepositoryDependencyService(provider=provider, discovery_service=discovery)


# ---------------------------------------------------------------------------
# 1. Input normalization and validation
# ---------------------------------------------------------------------------

class TestInputNormalization:
    async def test_object_type_uppercased(self):
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result())
        svc = _make_service(provider=provider)
        await svc.get_direct_dependencies("prog", "ztest")
        call_kwargs = provider.get_object_dependencies.call_args.kwargs
        assert call_kwargs["object_type"] == "PROG"

    async def test_object_name_uppercased(self):
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result())
        svc = _make_service(provider=provider)
        await svc.get_direct_dependencies("PROG", "zfi_report")
        call_kwargs = provider.get_object_dependencies.call_args.kwargs
        assert call_kwargs["object_name"] == "ZFI_REPORT"

    async def test_object_type_trimmed(self):
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result())
        svc = _make_service(provider=provider)
        await svc.get_direct_dependencies("  PROG  ", "ZTEST")
        call_kwargs = provider.get_object_dependencies.call_args.kwargs
        assert call_kwargs["object_type"] == "PROG"

    async def test_unsupported_object_type_raises_value_error(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="Unsupported"):
            await svc.get_direct_dependencies("UNKN", "ZTEST")

    async def test_blank_object_name_raises_value_error(self):
        svc = _make_service()
        with pytest.raises(ValueError, match="blank"):
            await svc.get_direct_dependencies("PROG", "   ")

    async def test_max_results_below_minimum_raises(self):
        svc = _make_service()
        with pytest.raises(ValueError):
            await svc.get_direct_dependencies("PROG", "ZTEST", max_results=0)

    async def test_max_results_above_hard_limit_raises(self):
        svc = _make_service()
        with pytest.raises(ValueError):
            await svc.get_direct_dependencies("PROG", "ZTEST", max_results=99999)


# ---------------------------------------------------------------------------
# 2. Relationship type filtering
# ---------------------------------------------------------------------------

class TestRelationshipTypeFiltering:
    async def test_no_filter_returns_all(self):
        rels = [
            _dep(rel_type=DependencyRelationshipType.CALLS_FUNCTION),
            _dep(rel_type=DependencyRelationshipType.READS_TABLE, tgt_type="TABL", tgt_name="ZTABLE1"),
        ]
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result(rels=rels))
        svc = _make_service(provider=provider)
        result = await svc.get_direct_dependencies("PROG", "ZA")
        assert result.returned_relations == 2

    async def test_filter_to_calls_function_only(self):
        rels = [
            _dep(rel_type=DependencyRelationshipType.CALLS_FUNCTION),
            _dep(rel_type=DependencyRelationshipType.READS_TABLE, tgt_type="TABL", tgt_name="ZTABLE1"),
        ]
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result(rels=rels))
        svc = _make_service(provider=provider)
        result = await svc.get_direct_dependencies(
            "PROG", "ZA", relationship_types=["CALLS_FUNCTION"]
        )
        assert result.returned_relations == 1
        assert result.relationships[0].relationship_type == DependencyRelationshipType.CALLS_FUNCTION

    async def test_filter_to_multiple_types(self):
        rels = [
            _dep(rel_type=DependencyRelationshipType.CALLS_FUNCTION, tgt_name="ZFN1"),
            _dep(rel_type=DependencyRelationshipType.READS_TABLE, tgt_type="TABL", tgt_name="ZTABLE1"),
            _dep(rel_type=DependencyRelationshipType.USES_CLASS, tgt_type="CLAS", tgt_name="ZCL1"),
        ]
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result(rels=rels))
        svc = _make_service(provider=provider)
        result = await svc.get_direct_dependencies(
            "PROG", "ZA", relationship_types=["CALLS_FUNCTION", "READS_TABLE"]
        )
        assert result.returned_relations == 2


# ---------------------------------------------------------------------------
# 3. Confidence filtering
# ---------------------------------------------------------------------------

class TestConfidenceFiltering:
    async def test_min_confidence_high_excludes_medium_and_low(self):
        rels = [
            _dep(confidence=DependencyConfidence.EXACT, tgt_name="ZFN1"),
            _dep(confidence=DependencyConfidence.HIGH, tgt_name="ZFN2"),
            _dep(confidence=DependencyConfidence.MEDIUM, tgt_name="ZFN3"),
            _dep(confidence=DependencyConfidence.LOW, tgt_name="ZFN4"),
        ]
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result(rels=rels))
        svc = _make_service(provider=provider)
        result = await svc.get_direct_dependencies("PROG", "ZA", min_confidence="HIGH")
        assert result.returned_relations == 2
        for r in result.relationships:
            assert r.confidence in (DependencyConfidence.EXACT, DependencyConfidence.HIGH)

    async def test_min_confidence_exact_includes_only_exact(self):
        rels = [
            _dep(confidence=DependencyConfidence.EXACT, tgt_name="ZFN1"),
            _dep(confidence=DependencyConfidence.HIGH, tgt_name="ZFN2"),
        ]
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result(rels=rels))
        svc = _make_service(provider=provider)
        result = await svc.get_direct_dependencies("PROG", "ZA", min_confidence="EXACT")
        assert result.returned_relations == 1
        assert result.relationships[0].confidence == DependencyConfidence.EXACT

    async def test_min_confidence_low_includes_all(self):
        rels = [
            _dep(confidence=DependencyConfidence.EXACT, tgt_name="ZFN1"),
            _dep(confidence=DependencyConfidence.LOW, tgt_name="ZFN2"),
        ]
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result(rels=rels))
        svc = _make_service(provider=provider)
        result = await svc.get_direct_dependencies("PROG", "ZA", min_confidence="LOW")
        assert result.returned_relations == 2

    async def test_confidence_not_modified_by_service(self):
        """Service must not upgrade confidence levels."""
        rels = [_dep(confidence=DependencyConfidence.MEDIUM, tgt_name="ZFN1")]
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result(rels=rels))
        svc = _make_service(provider=provider)
        result = await svc.get_direct_dependencies("PROG", "ZA")
        assert result.relationships[0].confidence == DependencyConfidence.MEDIUM


# ---------------------------------------------------------------------------
# 4. Deduplication
# ---------------------------------------------------------------------------

class TestDeduplication:
    async def test_exact_duplicate_removed(self):
        """Exact duplicate = all fields identical."""
        rel = _dep(evidence_source="DDIC", line_no=10)
        rels = [rel, rel]  # exact duplicate
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result(rels=rels))
        svc = _make_service(provider=provider)
        result = await svc.get_direct_dependencies("PROG", "ZA")
        assert result.returned_relations == 1

    async def test_distinct_evidence_preserved(self):
        """Same target but different line numbers = distinct evidence."""
        rels = [
            _dep(evidence_source="SOURCE_STATIC", line_no=10),
            _dep(evidence_source="SOURCE_STATIC", line_no=25),  # different line
        ]
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result(rels=rels))
        svc = _make_service(provider=provider)
        result = await svc.get_direct_dependencies("PROG", "ZA")
        assert result.returned_relations == 2

    async def test_distinct_evidence_different_source(self):
        """Same target, different evidence source = distinct."""
        rels = [
            _dep(evidence_source="DDIC"),
            _dep(evidence_source="SOURCE_STATIC"),  # different source
        ]
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(return_value=_result(rels=rels))
        svc = _make_service(provider=provider)
        result = await svc.get_direct_dependencies("PROG", "ZA")
        assert result.returned_relations == 2


# ---------------------------------------------------------------------------
# 5. Edge grouping
# ---------------------------------------------------------------------------

class TestEdgeGrouping:
    def test_same_edge_two_evidence_rows_grouped(self):
        """Two evidence rows for same conceptual edge should group."""
        rels = [
            _dep(evidence_source="DDIC", line_no=10, confidence=DependencyConfidence.HIGH),
            _dep(evidence_source="SOURCE_STATIC", line_no=25, confidence=DependencyConfidence.MEDIUM),
        ]
        edges = RepositoryDependencyService._group_into_edges(rels)
        assert len(edges) == 1
        assert len(edges[0].evidence) == 2

    def test_edge_confidence_strongest(self):
        """Edge confidence = strongest across evidence records."""
        rels = [
            _dep(evidence_source="DDIC", confidence=DependencyConfidence.MEDIUM),
            _dep(evidence_source="SOURCE_STATIC", confidence=DependencyConfidence.EXACT),
        ]
        edges = RepositoryDependencyService._group_into_edges(rels)
        assert edges[0].confidence == DependencyConfidence.EXACT

    def test_multiple_different_edges(self):
        """Different target objects = different edges."""
        rels = [
            _dep(tgt_name="ZFN1", rel_type=DependencyRelationshipType.CALLS_FUNCTION),
            _dep(tgt_name="ZFN2", rel_type=DependencyRelationshipType.CALLS_FUNCTION),
        ]
        edges = RepositoryDependencyService._group_into_edges(rels)
        assert len(edges) == 2

    def test_dynamic_any_evidence_propagates_to_edge(self):
        """Edge dynamic=True if any evidence is dynamic."""
        rels = [
            _dep(dynamic=False),
            _dep(dynamic=True, line_no=99),
        ]
        edges = RepositoryDependencyService._group_into_edges(rels)
        assert edges[0].dynamic is True

    def test_weaker_evidence_not_discarded(self):
        """Both EXACT and LOW evidence preserved in grouped edge."""
        rels = [
            _dep(evidence_source="DDIC", confidence=DependencyConfidence.EXACT),
            _dep(evidence_source="SOURCE_HEURISTIC", confidence=DependencyConfidence.LOW, line_no=50),
        ]
        edges = RepositoryDependencyService._group_into_edges(rels)
        confidences = {e.confidence for e in edges[0].evidence}
        assert DependencyConfidence.EXACT in confidences
        assert DependencyConfidence.LOW in confidences


# ---------------------------------------------------------------------------
# 6. Forward traversal
# ---------------------------------------------------------------------------

def _make_traversal_provider(dep_map: dict):
    """Build a mock provider that returns deps based on (type, name) key."""
    provider = AsyncMock()

    async def mock_get_deps(object_type, object_name, max_results=1000):
        key = (object_type.upper(), object_name.upper())
        rels = dep_map.get(key, [])
        return _result(src_type=object_type, src_name=object_name, rels=rels)

    provider.get_object_dependencies = mock_get_deps
    return provider


class TestForwardTraversal:
    def _req(self, obj_type, obj_name, max_depth=2, **kwargs):
        return DependencyTraversalRequest(
            root_object=_ref(obj_type, obj_name),
            max_depth=max_depth,
            **kwargs,
        )

    async def test_simple_a_to_b(self):
        """A → B: root and one target."""
        deps = {
            ("PROG", "ZA"): [_dep(src_type="PROG", src_name="ZA", tgt_type="FUNC", tgt_name="ZB")],
            ("FUNC", "ZB"): [],
        }
        provider = _make_traversal_provider(deps)
        svc = _make_service(provider=provider)
        graph = await svc.traverse_dependencies(self._req("PROG", "ZA", max_depth=1))
        assert graph.statistics.node_count >= 2
        assert graph.statistics.edge_count == 1
        assert graph.depth_reached >= 1

    async def test_a_to_b_to_c_depth2(self):
        """A → B → C at depth 2."""
        deps = {
            ("PROG", "ZA"): [_dep(src_type="PROG", src_name="ZA", tgt_type="FUNC", tgt_name="ZB")],
            ("FUNC", "ZB"): [_dep(src_type="FUNC", src_name="ZB", tgt_type="TABL", tgt_name="ZC",
                                  rel_type=DependencyRelationshipType.READS_TABLE)],
            ("TABL", "ZC"): [],
        }
        provider = _make_traversal_provider(deps)
        svc = _make_service(provider=provider)
        graph = await svc.traverse_dependencies(self._req("PROG", "ZA", max_depth=2))
        node_names = {n.object_name for n in graph.nodes}
        assert "ZA" in node_names
        assert "ZB" in node_names
        assert "ZC" in node_names

    async def test_max_depth_1_stops_after_one_hop(self):
        """max_depth=1 should not fetch B's dependencies."""
        deps = {
            ("PROG", "ZA"): [_dep(src_type="PROG", src_name="ZA", tgt_type="FUNC", tgt_name="ZB")],
            ("FUNC", "ZB"): [_dep(src_type="FUNC", src_name="ZB", tgt_type="TABL", tgt_name="ZC",
                                  rel_type=DependencyRelationshipType.READS_TABLE)],
            ("TABL", "ZC"): [],
        }
        provider = _make_traversal_provider(deps)
        svc = _make_service(provider=provider)
        graph = await svc.traverse_dependencies(self._req("PROG", "ZA", max_depth=1))
        node_names = {n.object_name for n in graph.nodes}
        # ZC should NOT be in the graph (depth 2 is beyond max_depth=1)
        assert "ZC" not in node_names

    async def test_cycle_a_to_b_to_a(self):
        """A → B → A: cycle must be detected, traversal must terminate."""
        deps = {
            ("PROG", "ZA"): [_dep(src_type="PROG", src_name="ZA", tgt_type="PROG", tgt_name="ZB",
                                  rel_type=DependencyRelationshipType.CALLS_PROGRAM)],
            ("PROG", "ZB"): [_dep(src_type="PROG", src_name="ZB", tgt_type="PROG", tgt_name="ZA",
                                  rel_type=DependencyRelationshipType.CALLS_PROGRAM)],
        }
        provider = _make_traversal_provider(deps)
        svc = _make_service(provider=provider)
        graph = await svc.traverse_dependencies(self._req("PROG", "ZA", max_depth=3))
        assert graph.cycles_detected is True
        # Cycle edge should be marked
        cycle_edges = [e for e in graph.edges if e.creates_cycle]
        assert len(cycle_edges) >= 1

    async def test_self_cycle_a_to_a(self):
        """A → A: self-cycle detected."""
        deps = {
            ("PROG", "ZA"): [_dep(src_type="PROG", src_name="ZA", tgt_type="PROG", tgt_name="ZA",
                                  rel_type=DependencyRelationshipType.CALLS_PROGRAM)],
        }
        provider = _make_traversal_provider(deps)
        svc = _make_service(provider=provider)
        graph = await svc.traverse_dependencies(self._req("PROG", "ZA", max_depth=2))
        assert graph.cycles_detected is True

    async def test_diamond_a_b_c_d(self):
        """A→B, A→C, B→D, C→D: D fetched only once (cache)."""
        call_count = {"PROG/ZD": 0}

        deps_map = {
            ("PROG", "ZA"): [
                _dep(src_type="PROG", src_name="ZA", tgt_type="PROG", tgt_name="ZB",
                     rel_type=DependencyRelationshipType.CALLS_PROGRAM),
                _dep(src_type="PROG", src_name="ZA", tgt_type="PROG", tgt_name="ZC",
                     rel_type=DependencyRelationshipType.CALLS_PROGRAM),
            ],
            ("PROG", "ZB"): [_dep(src_type="PROG", src_name="ZB", tgt_type="PROG", tgt_name="ZD",
                                  rel_type=DependencyRelationshipType.CALLS_PROGRAM)],
            ("PROG", "ZC"): [_dep(src_type="PROG", src_name="ZC", tgt_type="PROG", tgt_name="ZD",
                                  rel_type=DependencyRelationshipType.CALLS_PROGRAM)],
            ("PROG", "ZD"): [],
        }

        provider = AsyncMock()

        async def tracked_get_deps(object_type, object_name, max_results=1000):
            if object_name == "ZD":
                call_count["PROG/ZD"] += 1
            key = (object_type.upper(), object_name.upper())
            rels = deps_map.get(key, [])
            return _result(src_type=object_type, src_name=object_name, rels=rels)

        provider.get_object_dependencies = tracked_get_deps
        svc = _make_service(provider=provider)
        graph = await svc.traverse_dependencies(self._req("PROG", "ZA", max_depth=3))

        node_names = {n.object_name for n in graph.nodes}
        assert "ZD" in node_names
        # ZD should be fetched at most once due to request-local cache
        assert call_count["PROG/ZD"] <= 1

    async def test_dynamic_target_not_traversed(self):
        """<DYNAMIC> targets should appear as edges but not be traversed."""
        deps = {
            ("PROG", "ZA"): [
                _dep(src_type="PROG", src_name="ZA", tgt_type="FUNC",
                     tgt_name=DYNAMIC_TARGET_SENTINEL, dynamic=True),
            ],
        }
        provider = _make_traversal_provider(deps)
        svc = _make_service(provider=provider)
        graph = await svc.traverse_dependencies(self._req("PROG", "ZA", max_depth=2))
        # <DYNAMIC> should appear as edge target but provider should NOT be called for it
        edge_targets = {e.target.object_name for e in graph.edges}
        assert DYNAMIC_TARGET_SENTINEL in edge_targets
        # Dynamic target should not appear as a traversable node
        node_names = {n.object_name for n in graph.nodes}
        assert DYNAMIC_TARGET_SENTINEL not in node_names

    async def test_max_nodes_truncates(self):
        """When max_nodes is reached, truncated=True."""
        deps = {
            ("PROG", "ZA"): [
                _dep(src_type="PROG", src_name="ZA", tgt_type="FUNC", tgt_name=f"ZFN{i}")
                for i in range(10)
            ],
        }
        for i in range(10):
            deps[("FUNC", f"ZFN{i}")] = []

        provider = _make_traversal_provider(deps)
        svc = _make_service(provider=provider)
        graph = await svc.traverse_dependencies(
            self._req("PROG", "ZA", max_depth=2, max_nodes=3)
        )
        assert graph.truncated is True
        assert len(graph.nodes) <= 3

    async def test_max_edges_truncates(self):
        """When max_edges is reached, truncated=True."""
        deps = {
            ("PROG", "ZA"): [
                _dep(src_type="PROG", src_name="ZA", tgt_type="FUNC", tgt_name=f"ZFN{i}")
                for i in range(10)
            ],
        }
        for i in range(10):
            deps[("FUNC", f"ZFN{i}")] = []

        provider = _make_traversal_provider(deps)
        svc = _make_service(provider=provider)
        graph = await svc.traverse_dependencies(
            self._req("PROG", "ZA", max_depth=2, max_edges=3)
        )
        assert graph.truncated is True
        assert len(graph.edges) <= 3

    async def test_request_local_cache(self):
        """Same object encountered twice should be fetched only once."""
        call_counts = {}

        deps_map = {
            ("PROG", "ZA"): [
                _dep(src_type="PROG", src_name="ZA", tgt_type="FUNC", tgt_name="ZB"),
                _dep(src_type="PROG", src_name="ZA", tgt_type="FUNC", tgt_name="ZC",
                     rel_type=DependencyRelationshipType.CALLS_FUNCTION),
            ],
            ("FUNC", "ZB"): [_dep(src_type="FUNC", src_name="ZB", tgt_type="FUNC", tgt_name="ZD")],
            ("FUNC", "ZC"): [_dep(src_type="FUNC", src_name="ZC", tgt_type="FUNC", tgt_name="ZD")],
            ("FUNC", "ZD"): [],
        }

        provider = AsyncMock()

        async def counted_get_deps(object_type, object_name, max_results=1000):
            key = f"{object_type}/{object_name}"
            call_counts[key] = call_counts.get(key, 0) + 1
            rels = deps_map.get((object_type.upper(), object_name.upper()), [])
            return _result(src_type=object_type, src_name=object_name, rels=rels)

        provider.get_object_dependencies = counted_get_deps
        svc = _make_service(provider=provider)
        await svc.traverse_dependencies(self._req("PROG", "ZA", max_depth=3))

        # ZD should be fetched at most once
        assert call_counts.get("FUNC/ZD", 0) <= 1

    async def test_sap_dep_truncated_sets_traversal_truncated(self):
        """When ECC truncates dependency result, traversal should mark truncated."""
        deps_map = {
            ("PROG", "ZA"): [_dep(src_type="PROG", src_name="ZA", tgt_type="FUNC", tgt_name="ZB")],
        }

        provider = AsyncMock()

        async def get_deps_with_truncation(object_type, object_name, max_results=1000):
            rels = deps_map.get((object_type.upper(), object_name.upper()), [])
            return _result(src_type=object_type, src_name=object_name, rels=rels, truncated=True)

        provider.get_object_dependencies = get_deps_with_truncation
        svc = _make_service(provider=provider)
        graph = await svc.traverse_dependencies(self._req("PROG", "ZA", max_depth=1))
        assert graph.truncated is True
        assert len(graph.warnings) > 0

    async def test_traversal_statistics_accurate(self):
        deps = {
            ("PROG", "ZA"): [
                _dep(src_type="PROG", src_name="ZA", tgt_type="FUNC", tgt_name="ZB"),
                _dep(src_type="PROG", src_name="ZA", tgt_type="TABL", tgt_name="ZT",
                     rel_type=DependencyRelationshipType.READS_TABLE),
            ],
            ("FUNC", "ZB"): [],
            ("TABL", "ZT"): [],
        }
        provider = _make_traversal_provider(deps)
        svc = _make_service(provider=provider)
        graph = await svc.traverse_dependencies(self._req("PROG", "ZA", max_depth=2))
        assert graph.statistics.node_count == len(graph.nodes)
        assert graph.statistics.edge_count == len(graph.edges)
        assert graph.statistics.sap_calls >= 1


# ---------------------------------------------------------------------------
# 7. Reverse dependency tests
# ---------------------------------------------------------------------------

def _make_search_obj(obj_type: str, obj_name: str):
    """Create a mock RepositoryObject for discovery search results."""
    obj = MagicMock()
    obj.sap_object_type = obj_type
    obj.object_name = obj_name
    return obj


def _make_search_result_mock(objects=None, truncated=False):
    r = MagicMock()
    r.objects = objects or []
    r.truncated = truncated
    return r


class TestReverseDepedencies:
    def _scope(self, packages=None):
        return ReverseScopeSpec(packages=packages or ["ZTEST"])

    def _req(self, tgt_type="FUNC", tgt_name="ZFI_GET", scope=None):
        return ReverseDependencyRequest(
            target_object=_ref(tgt_type, tgt_name),
            scope=scope or self._scope(),
        )

    async def test_one_dependent_found(self):
        dep_on_target = _dep(
            src_type="PROG", src_name="ZCALLER",
            tgt_type="FUNC", tgt_name="ZFI_GET",
            rel_type=DependencyRelationshipType.CALLS_FUNCTION,
        )
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(
            return_value=_result(src_type="PROG", src_name="ZCALLER", rels=[dep_on_target])
        )
        discovery = AsyncMock()
        discovery.search = AsyncMock(
            return_value=_make_search_result_mock([_make_search_obj("PROG", "ZCALLER")])
        )
        svc = RepositoryDependencyService(provider=provider, discovery_service=discovery)
        result = await svc.get_reverse_dependencies(self._req())
        assert result.matched_dependents == 1
        assert result.dependents[0].object_name == "ZCALLER"

    async def test_no_dependents_returns_empty(self):
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(
            return_value=_result(src_type="PROG", src_name="ZUNRELATED", rels=[])
        )
        discovery = AsyncMock()
        discovery.search = AsyncMock(
            return_value=_make_search_result_mock([_make_search_obj("PROG", "ZUNRELATED")])
        )
        svc = RepositoryDependencyService(provider=provider, discovery_service=discovery)
        result = await svc.get_reverse_dependencies(self._req())
        assert result.matched_dependents == 0
        assert result.dependents == []

    async def test_no_candidates_returns_empty(self):
        discovery = AsyncMock()
        discovery.search = AsyncMock(return_value=_make_search_result_mock([]))
        svc = RepositoryDependencyService(provider=AsyncMock(), discovery_service=discovery)
        result = await svc.get_reverse_dependencies(self._req())
        assert result.candidate_count == 0
        assert result.matched_dependents == 0

    async def test_multiple_dependents(self):
        dep1 = _dep(src_type="PROG", src_name="ZA", tgt_type="FUNC", tgt_name="ZFI_GET")
        dep2 = _dep(src_type="PROG", src_name="ZB", tgt_type="FUNC", tgt_name="ZFI_GET")

        call_map = {
            "ZA": _result(src_type="PROG", src_name="ZA", rels=[dep1]),
            "ZB": _result(src_type="PROG", src_name="ZB", rels=[dep2]),
        }

        provider = AsyncMock()

        async def get_deps(object_type, object_name, max_results=500):
            return call_map.get(object_name.upper(), _result(src_type=object_type, src_name=object_name))

        provider.get_object_dependencies = get_deps
        discovery = AsyncMock()
        discovery.search = AsyncMock(
            return_value=_make_search_result_mock([
                _make_search_obj("PROG", "ZA"),
                _make_search_obj("PROG", "ZB"),
            ])
        )
        svc = RepositoryDependencyService(provider=provider, discovery_service=discovery)
        result = await svc.get_reverse_dependencies(self._req())
        assert result.matched_dependents == 2

    async def test_requires_discovery_service(self):
        svc = RepositoryDependencyService(provider=AsyncMock(), discovery_service=None)
        with pytest.raises(ValueError, match="RepositoryDiscoveryService"):
            await svc.get_reverse_dependencies(self._req())


# ---------------------------------------------------------------------------
# 8. Impact analysis
# ---------------------------------------------------------------------------

class TestImpactAnalysis:
    def _scope(self, packages=None):
        return ReverseScopeSpec(packages=packages or ["ZTEST"])

    def _req(self, tgt_type="TABL", tgt_name="ZFIN_TABLE", scope=None):
        return DependencyImpactRequest(
            object=_ref(tgt_type, tgt_name),
            scope=scope or self._scope(),
        )

    async def test_zero_dependents(self):
        discovery = AsyncMock()
        discovery.search = AsyncMock(return_value=_make_search_result_mock([]))
        svc = RepositoryDependencyService(provider=AsyncMock(), discovery_service=discovery)
        result = await svc.get_impact_analysis(self._req())
        assert result.direct_dependent_count == 0
        assert result.total_impacted_objects == 0

    async def test_one_direct_dependent(self):
        dep = _dep(
            src_type="PROG", src_name="ZCALLER",
            tgt_type="TABL", tgt_name="ZFIN_TABLE",
            rel_type=DependencyRelationshipType.READS_TABLE,
        )
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(
            return_value=_result(src_type="PROG", src_name="ZCALLER", rels=[dep])
        )
        discovery = AsyncMock()
        discovery.search = AsyncMock(
            return_value=_make_search_result_mock([_make_search_obj("PROG", "ZCALLER")])
        )
        svc = RepositoryDependencyService(provider=provider, discovery_service=discovery)
        result = await svc.get_impact_analysis(self._req())
        assert result.direct_dependent_count == 1
        assert result.total_impacted_objects == 1

    async def test_relationship_type_counts_accurate(self):
        rels = [
            _dep(src_type="PROG", src_name="ZA", tgt_type="TABL", tgt_name="ZFIN_TABLE",
                 rel_type=DependencyRelationshipType.READS_TABLE),
            _dep(src_type="PROG", src_name="ZA", tgt_type="TABL", tgt_name="ZFIN_TABLE",
                 rel_type=DependencyRelationshipType.WRITES_TABLE, line_no=99),
        ]
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(
            return_value=_result(src_type="PROG", src_name="ZA", rels=rels)
        )
        discovery = AsyncMock()
        discovery.search = AsyncMock(
            return_value=_make_search_result_mock([_make_search_obj("PROG", "ZA")])
        )
        svc = RepositoryDependencyService(provider=provider, discovery_service=discovery)
        result = await svc.get_impact_analysis(self._req())
        assert "READS_TABLE" in result.relationship_type_counts
        assert "WRITES_TABLE" in result.relationship_type_counts

    async def test_truncated_propagated(self):
        dep = _dep(src_type="PROG", src_name="ZA", tgt_type="TABL", tgt_name="ZFIN_TABLE",
                   rel_type=DependencyRelationshipType.READS_TABLE)
        provider = AsyncMock()
        provider.get_object_dependencies = AsyncMock(
            return_value=_result(src_type="PROG", src_name="ZA", rels=[dep], truncated=True)
        )
        discovery = AsyncMock()
        discovery.search = AsyncMock(
            return_value=_make_search_result_mock([_make_search_obj("PROG", "ZA")], truncated=True)
        )
        svc = RepositoryDependencyService(provider=provider, discovery_service=discovery)
        result = await svc.get_impact_analysis(self._req())
        assert result.truncated is True
