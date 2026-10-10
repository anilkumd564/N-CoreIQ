"""Phase 8 — Repository Dependency Service.

Implements all dependency intelligence operations:

* get_direct_dependencies  — normalize, validate, fetch from ECC, filter, deduplicate
* traverse_dependencies    — bounded BFS forward traversal with cycle detection
* get_reverse_dependencies — bounded reverse lookup using RepositoryDiscoveryService
* get_impact_analysis      — deterministic reverse + transitive impact analysis

Architecture:
    REST / MCP
        ↓
    RepositoryDependencyService  (this module)
        ↓
    SapConnectionProvider.get_object_dependencies()
        ↓
    DependencyRelationsSet OData V2
        ↓
    SAP ECC (Z_MCP_REPO_DEPENDENCIES)

Rules:
  * No httpx / OData logic here — all SAP calls go through the provider.
  * No SourceLinesSet calls — ECC owns dependency extraction.
  * No metadata N+1 — do not enrich nodes with ObjectMetadataSet.
  * No graph database — in-memory request-local structures only.
  * No AI/LLM — all analysis is deterministic.
  * Read-only — no SAP write operations.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

from app.models.phase8 import (
    CONFIDENCE_RANK,
    DYNAMIC_TARGET_SENTINEL,
    PHASE8_DEPENDENCY_SOURCE_TYPES,
    REVERSE_CONCURRENCY_DEFAULT,
    REVERSE_CONCURRENCY_MAX,
    REVERSE_MAX_CANDIDATES_DEFAULT,
    REVERSE_MAX_CANDIDATES_HARD,
    DependencyConfidence,
    DependencyEdge,
    DependencyEvidence,
    DependencyGraph,
    DependencyGraphEdge,
    DependencyImpactRequest,
    DependencyImpactResult,
    DependencyNode,
    DependencyRelationshipType,
    DependencyTraversalRequest,
    DirectDependencyResult,
    GraphStatistics,
    RepositoryDependency,
    RepositoryObjectReference,
    ReverseDependencyRequest,
    ReverseDependencyResult,
    ReverseDependent,
    TraversalDirection,
    confidence_rank,
    strongest_confidence,
)
from app.models.requests import RepositorySearchRequest
from app.sap.connection.base import SapConnectionProvider
from app.sap.exceptions import (
    DependencyObjectNotFoundError,
    DependencyProviderError,
    DependencyScopeTooLargeError,
    DependencyTraversalLimitError,
)

logger = logging.getLogger("app.services.dependency")

# Global SAP call budget per synchronous operation.
MAX_DEPENDENCY_SAP_CALLS: int = 500


class RepositoryDependencyService:
    """SAP Dependency & Relationship Intelligence service.

    All SAP interactions go through the provider abstraction.
    All analysis is performed in-memory on the retrieved data.
    """

    def __init__(
        self,
        provider: SapConnectionProvider,
        discovery_service: Any = None,
    ) -> None:
        self._provider = provider
        self._discovery = discovery_service  # RepositoryDiscoveryService (optional for reverse)

    # ------------------------------------------------------------------
    # Input normalization helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_object_type(object_type: str) -> str:
        return object_type.strip().upper()

    @staticmethod
    def _normalize_object_name(object_name: str) -> str:
        return object_name.strip().upper()

    @staticmethod
    def _validate_object_type(object_type: str) -> None:
        if object_type not in PHASE8_DEPENDENCY_SOURCE_TYPES:
            raise ValueError(
                f"Unsupported dependency source object type '{object_type}'. "
                f"Phase 8 supported types: {sorted(PHASE8_DEPENDENCY_SOURCE_TYPES)}"
            )

    @staticmethod
    def _validate_object_name(object_name: str) -> None:
        if not object_name:
            raise ValueError("Object name must not be blank.")

    @staticmethod
    def _validate_max_results(max_results: int) -> None:
        if max_results < 1:
            raise ValueError("max_results must be at least 1.")
        if max_results > 10_000:
            raise ValueError("max_results must not exceed 10000.")

    # ------------------------------------------------------------------
    # Confidence filtering
    # ------------------------------------------------------------------

    @staticmethod
    def _passes_confidence(
        dep_confidence: DependencyConfidence,
        min_confidence: DependencyConfidence | None,
    ) -> bool:
        if min_confidence is None:
            return True
        return confidence_rank(dep_confidence) >= confidence_rank(min_confidence)

    # ------------------------------------------------------------------
    # Relationship type filtering
    # ------------------------------------------------------------------

    @staticmethod
    def _passes_relationship_type(
        rel_type: DependencyRelationshipType,
        allowed: list[str] | None,
    ) -> bool:
        if not allowed:
            return True
        return rel_type.value in allowed

    # ------------------------------------------------------------------
    # Deduplication
    # ------------------------------------------------------------------

    @staticmethod
    def _dedup_key(dep: RepositoryDependency) -> tuple:
        """Return a canonical deduplication key for an exact duplicate evidence row."""
        return (
            dep.source_object_type,
            dep.source_object_name,
            dep.target_object_type,
            dep.target_object_name,
            dep.relationship_type.value,
            dep.evidence_source or "",
            dep.source_component or "",
            dep.line_no,
            dep.dynamic,
            dep.detail or "",
        )

    @staticmethod
    def _remove_exact_duplicates(
        relationships: list[RepositoryDependency],
    ) -> list[RepositoryDependency]:
        """Remove exact duplicate evidence rows; preserve distinct source locations."""
        seen: set[tuple] = set()
        result: list[RepositoryDependency] = []
        for dep in relationships:
            key = RepositoryDependencyService._dedup_key(dep)
            if key not in seen:
                seen.add(key)
                result.append(dep)
        return result

    # ------------------------------------------------------------------
    # Edge grouping
    # ------------------------------------------------------------------

    @staticmethod
    def _group_into_edges(
        relationships: list[RepositoryDependency],
    ) -> list[DependencyEdge]:
        """Group dependency rows into conceptual edges.

        An edge key is: (source_type, source_name, target_type, target_name, relationship_type).
        Multiple evidence rows with the same key are grouped; edge confidence =
        strongest evidence confidence.
        """
        edge_map: dict[tuple, list[RepositoryDependency]] = {}
        for dep in relationships:
            edge_key = (
                dep.source_object_type,
                dep.source_object_name,
                dep.target_object_type,
                dep.target_object_name,
                dep.relationship_type.value,
            )
            if edge_key not in edge_map:
                edge_map[edge_key] = []
            edge_map[edge_key].append(dep)

        edges: list[DependencyEdge] = []
        for edge_key, deps in edge_map.items():
            src_type, src_name, tgt_type, tgt_name, rel_type_str = edge_key
            confidences = [d.confidence for d in deps]
            edge_confidence = strongest_confidence(confidences)
            any_dynamic = any(d.dynamic for d in deps)

            evidence_list = [
                DependencyEvidence(
                    evidence_source=d.evidence_source,
                    source_component=d.source_component,
                    line_no=d.line_no,
                    detail=d.detail,
                    confidence=d.confidence,
                    dynamic=d.dynamic,
                )
                for d in deps
            ]

            edge = DependencyEdge(
                source=RepositoryObjectReference(
                    sap_object_type=src_type,
                    object_name=src_name,
                ),
                target=RepositoryObjectReference(
                    sap_object_type=tgt_type,
                    object_name=tgt_name,
                ),
                relationship_type=DependencyRelationshipType(rel_type_str),
                confidence=edge_confidence,
                dynamic=any_dynamic,
                evidence=evidence_list,
            )
            edges.append(edge)

        return edges

    # ------------------------------------------------------------------
    # Public: direct dependencies
    # ------------------------------------------------------------------

    async def get_direct_dependencies(
        self,
        object_type: str,
        object_name: str,
        relationship_types: list[str] | None = None,
        min_confidence: str | None = None,
        max_results: int = 1000,
    ) -> DirectDependencyResult:
        """Fetch direct dependencies for a SAP object from ECC.

        Steps:
          1. Normalize and validate inputs.
          2. Fetch from DependencyRelationsSet via provider.
          3. Filter by relationship type.
          4. Filter by minimum confidence.
          5. Remove exact duplicate evidence rows.
          6. Return normalized DirectDependencyResult.
        """
        start = time.monotonic()

        # Normalize
        object_type = self._normalize_object_type(object_type)
        object_name = self._normalize_object_name(object_name)

        # Validate
        self._validate_object_type(object_type)
        self._validate_object_name(object_name)
        self._validate_max_results(max_results)

        # Parse min_confidence
        min_conf_enum: DependencyConfidence | None = None
        if min_confidence:
            min_conf_enum = DependencyConfidence(min_confidence.strip().upper())

        # Fetch from SAP
        logger.info(
            "dependency_service_direct_start",
            extra={
                "operation": "get_direct_dependencies",
                "object_type": object_type,
                "object_name": object_name,
                "relationship_types": relationship_types,
                "min_confidence": min_confidence,
                "max_results": max_results,
            },
        )

        raw_result = await self._provider.get_object_dependencies(
            object_type=object_type,
            object_name=object_name,
            max_results=max_results,
        )

        # Filter by relationship types
        filtered = raw_result.relationships
        if relationship_types:
            rt_set = {rt.upper() for rt in relationship_types}
            filtered = [r for r in filtered if r.relationship_type.value in rt_set]

        # Filter by confidence
        if min_conf_enum is not None:
            filtered = [r for r in filtered if self._passes_confidence(r.confidence, min_conf_enum)]

        # Remove exact duplicates
        filtered = self._remove_exact_duplicates(filtered)

        elapsed = time.monotonic() - start
        logger.info(
            "dependency_service_direct_complete",
            extra={
                "operation": "get_direct_dependencies",
                "object_type": object_type,
                "object_name": object_name,
                "raw_count": raw_result.returned_relations,
                "filtered_count": len(filtered),
                "truncated": raw_result.truncated,
                "duration_seconds": round(elapsed, 3),
            },
        )

        return DirectDependencyResult(
            system_id=raw_result.system_id,
            client=raw_result.client,
            source_object_type=raw_result.source_object_type,
            source_object_name=raw_result.source_object_name,
            total_relations=raw_result.total_relations,
            returned_relations=len(filtered),
            truncated=raw_result.truncated,
            message=raw_result.message,
            relationships=filtered,
        )

    # ------------------------------------------------------------------
    # Public: forward traversal (bounded BFS)
    # ------------------------------------------------------------------

    async def traverse_dependencies(
        self,
        request: DependencyTraversalRequest,
    ) -> DependencyGraph:
        """Bounded forward BFS traversal of SAP dependencies.

        Uses a request-local cache: each object's direct dependencies are
        fetched exactly once, even if the object is encountered at multiple
        depths.

        Cycle detection: when an edge would revisit a node already in the
        current traversal path, the edge is preserved with creates_cycle=True
        but traversal does NOT recurse into it again.

        Dynamic targets (<DYNAMIC>) are never traversed.
        """
        root = request.root_object
        root_type = self._normalize_object_type(root.sap_object_type)
        root_name = self._normalize_object_name(root.object_name)

        # Validate root
        self._validate_object_type(root_type)
        self._validate_object_name(root_name)

        min_conf_enum: DependencyConfidence | None = None
        if request.min_confidence:
            min_conf_enum = DependencyConfidence(request.min_confidence)

        # BFS state
        # visited_nodes: set of (type, name) that are added to graph
        visited_nodes: set[tuple[str, str]] = set()
        # fetched_nodes: set of (type, name) for which we have called SAP
        fetched_nodes: set[tuple[str, str]] = set()
        # request-local dependency cache
        dep_cache: dict[tuple[str, str], DirectDependencyResult] = {}
        # edge keys to deduplicate edges in the graph
        edge_keys: set[tuple] = set()

        nodes: list[DependencyNode] = []
        edges: list[DependencyGraphEdge] = []
        warnings: list[str] = []
        truncated = False
        sap_calls = 0
        depth_reached = 0
        cycles_detected = False

        # BFS queue: (object_type, object_name, depth)
        queue: deque[tuple[str, str, int]] = deque()

        root_key = (root_type, root_name)
        visited_nodes.add(root_key)
        nodes.append(DependencyNode(sap_object_type=root_type, object_name=root_name, depth=0))
        queue.append((root_type, root_name, 0))

        logger.info(
            "dependency_service_traversal_start",
            extra={
                "operation": "traverse_dependencies",
                "root_type": root_type,
                "root_name": root_name,
                "max_depth": request.max_depth,
                "max_nodes": request.max_nodes,
                "max_edges": request.max_edges,
            },
        )

        while queue:
            cur_type, cur_name, cur_depth = queue.popleft()

            # Do not traverse deeper than max_depth
            if cur_depth >= request.max_depth:
                continue

            # Check SAP call budget
            if sap_calls >= MAX_DEPENDENCY_SAP_CALLS:
                truncated = True
                warnings.append(
                    f"SAP call budget of {MAX_DEPENDENCY_SAP_CALLS} reached. "
                    "Traversal truncated."
                )
                break

            cur_key = (cur_type, cur_name)

            # Use cache if available
            if cur_key not in dep_cache:
                fetched_nodes.add(cur_key)
                raw = await self._provider.get_object_dependencies(
                    object_type=cur_type,
                    object_name=cur_name,
                    max_results=1000,
                )
                sap_calls += 1
                dep_cache[cur_key] = raw
                if raw.truncated:
                    truncated = True
                    warnings.append(
                        f"SAP dependency result for {cur_type}/{cur_name} was truncated."
                    )
                # Surface any message from SAP (e.g. object not found in dep tables)
                if raw.message and raw.returned_relations == 0 and cur_key == (root_type, root_name):
                    warnings.append(raw.message)
            else:
                raw = dep_cache[cur_key]

            relationships = raw.relationships

            # Apply filters
            if request.relationship_types:
                rt_set = {rt.upper() for rt in request.relationship_types}
                relationships = [r for r in relationships if r.relationship_type.value in rt_set]
            if min_conf_enum is not None:
                relationships = [r for r in relationships if self._passes_confidence(r.confidence, min_conf_enum)]
            if not request.include_dynamic:
                relationships = [r for r in relationships if not r.dynamic]

            next_depth = cur_depth + 1
            if next_depth > depth_reached:
                depth_reached = next_depth

            for dep in relationships:
                tgt_type = dep.target_object_type
                tgt_name = dep.target_object_name

                # Build edge key for deduplication
                ekey = (cur_type, cur_name, tgt_type, tgt_name, dep.relationship_type.value)

                is_dynamic_target = tgt_name == DYNAMIC_TARGET_SENTINEL
                is_cycle = (tgt_type, tgt_name) in visited_nodes and not is_dynamic_target
                if is_cycle:
                    cycles_detected = True

                # Check edge limits
                if len(edges) >= request.max_edges:
                    truncated = True
                    warnings.append(f"max_edges ({request.max_edges}) reached. Traversal truncated.")
                    break

                if ekey not in edge_keys:
                    edge_keys.add(ekey)
                    evidence = [
                        DependencyEvidence(
                            evidence_source=dep.evidence_source,
                            source_component=dep.source_component,
                            line_no=dep.line_no,
                            detail=dep.detail,
                            confidence=dep.confidence,
                            dynamic=dep.dynamic,
                        )
                    ]
                    graph_edge = DependencyGraphEdge(
                        source=RepositoryObjectReference(sap_object_type=cur_type, object_name=cur_name),
                        target=RepositoryObjectReference(sap_object_type=tgt_type, object_name=tgt_name),
                        relationship_type=dep.relationship_type,
                        confidence=dep.confidence,
                        dynamic=dep.dynamic,
                        evidence=evidence,
                        depth=cur_depth,
                        creates_cycle=is_cycle,
                    )
                    edges.append(graph_edge)

                # Only expand if:
                # - not a dynamic target
                # - not already visited (no cycles)
                # - within depth limit
                # - node limit not reached
                if (
                    not is_dynamic_target
                    and not is_cycle
                    and next_depth <= request.max_depth
                ):
                    tgt_key = (tgt_type, tgt_name)
                    if tgt_key not in visited_nodes:
                        # Check node limit
                        if len(nodes) >= request.max_nodes:
                            truncated = True
                            warnings.append(f"max_nodes ({request.max_nodes}) reached. Traversal truncated.")
                        else:
                            visited_nodes.add(tgt_key)
                            nodes.append(DependencyNode(
                                sap_object_type=tgt_type,
                                object_name=tgt_name,
                                depth=next_depth,
                            ))
                            queue.append((tgt_type, tgt_name, next_depth))

            else:
                # Inner loop completed without break — check edge limit from outer
                if len(edges) >= request.max_edges:
                    break
                continue
            break  # Edge limit hit in inner loop

        # Build statistics
        rt_counts: dict[str, int] = {}
        conf_counts: dict[str, int] = {}
        dynamic_count = 0
        cycle_count = 0
        for e in edges:
            rt_key = e.relationship_type.value
            rt_counts[rt_key] = rt_counts.get(rt_key, 0) + 1
            conf_key = e.confidence.value
            conf_counts[conf_key] = conf_counts.get(conf_key, 0) + 1
            if e.dynamic:
                dynamic_count += 1
            if e.creates_cycle:
                cycle_count += 1

        stats = GraphStatistics(
            node_count=len(nodes),
            edge_count=len(edges),
            relationship_type_counts=rt_counts,
            confidence_counts=conf_counts,
            dynamic_edge_count=dynamic_count,
            cycle_count=cycle_count,
            sap_calls=sap_calls,
        )

        logger.info(
            "dependency_service_traversal_complete",
            extra={
                "operation": "traverse_dependencies",
                "root_type": root_type,
                "root_name": root_name,
                "node_count": len(nodes),
                "edge_count": len(edges),
                "sap_calls": sap_calls,
                "depth_reached": depth_reached,
                "truncated": truncated,
                "cycles_detected": cycles_detected,
            },
        )

        return DependencyGraph(
            root=RepositoryObjectReference(sap_object_type=root_type, object_name=root_name),
            direction=TraversalDirection.FORWARD,
            requested_max_depth=request.max_depth,
            depth_reached=depth_reached,
            truncated=truncated,
            nodes=nodes,
            edges=edges,
            cycles_detected=cycles_detected,
            warnings=warnings,
            statistics=stats,
        )

    # ------------------------------------------------------------------
    # Public: reverse dependency lookup
    # ------------------------------------------------------------------

    async def get_reverse_dependencies(
        self,
        request: ReverseDependencyRequest,
    ) -> ReverseDependencyResult:
        """Bounded reverse dependency lookup.

        Algorithm:
          1. Run RepositoryDiscoveryService.search() with provided scope.
          2. For each candidate object, fetch its direct dependencies (bounded concurrency).
          3. Match edges whose target == requested target object.
          4. Deduplicate and return dependents.
        """
        if self._discovery is None:
            raise ValueError(
                "RepositoryDiscoveryService is required for reverse dependency lookup."
            )

        target_type = self._normalize_object_type(request.target_object.sap_object_type)
        target_name = self._normalize_object_name(request.target_object.object_name)

        scope = request.scope
        max_candidates = min(request.max_candidates, REVERSE_MAX_CANDIDATES_HARD)

        min_conf_enum: DependencyConfidence | None = None
        if request.min_confidence:
            min_conf_enum = DependencyConfidence(request.min_confidence)

        logger.info(
            "dependency_service_reverse_start",
            extra={
                "operation": "get_reverse_dependencies",
                "target_type": target_type,
                "target_name": target_name,
                "max_candidates": max_candidates,
            },
        )

        # Step 1: Discover candidate objects
        search_req = RepositorySearchRequest(
            packages=scope.packages,
            include_subpackages=scope.include_subpackages,
            name_patterns=scope.name_patterns,
            object_types=scope.object_types,
            all_custom_objects=scope.all_custom_objects,
            max_results=max_candidates,
        )
        search_result = await self._discovery.search(search_req)
        candidates = search_result.objects
        candidate_count = len(candidates)

        scope_summary = {
            "packages": scope.packages,
            "name_patterns": scope.name_patterns,
            "object_types": scope.object_types,
            "all_custom_objects": scope.all_custom_objects,
            "max_results": max_candidates,
            "returned": candidate_count,
        }

        if candidate_count == 0:
            return ReverseDependencyResult(
                target_object=RepositoryObjectReference(
                    sap_object_type=target_type, object_name=target_name
                ),
                scope_summary=scope_summary,
                candidate_count=0,
                matched_dependents=0,
                truncated=search_result.truncated,
                warnings=["No candidates found in specified scope."] if candidate_count == 0 else [],
            )

        # Step 2: Fetch dependencies for each candidate (bounded concurrency)
        concurrency = min(REVERSE_CONCURRENCY_DEFAULT, REVERSE_CONCURRENCY_MAX)
        semaphore = asyncio.Semaphore(concurrency)
        sap_calls = 0
        truncated = search_result.truncated
        warnings: list[str] = []

        async def fetch_for_candidate(obj):
            nonlocal sap_calls
            async with semaphore:
                if sap_calls >= MAX_DEPENDENCY_SAP_CALLS:
                    return None
                try:
                    result = await self._provider.get_object_dependencies(
                        object_type=obj.sap_object_type,
                        object_name=obj.object_name,
                        max_results=500,
                    )
                    sap_calls += 1
                    return (obj, result)
                except Exception as exc:
                    logger.warning(
                        "dependency_reverse_candidate_failed",
                        extra={
                            "operation": "get_reverse_dependencies",
                            "object_type": obj.sap_object_type,
                            "object_name": obj.object_name,
                            "error": str(exc)[:100],
                        },
                    )
                    return None

        tasks = [fetch_for_candidate(c) for c in candidates]
        results = await asyncio.gather(*tasks)

        # Step 3: Match edges targeting our target object
        # dependent_map: (src_type, src_name) -> list of matching RepositoryDependency
        dependent_map: dict[tuple[str, str], list] = {}

        for item in results:
            if item is None:
                continue
            obj, dep_result = item

            if dep_result is None:
                continue

            if sap_calls >= MAX_DEPENDENCY_SAP_CALLS:
                truncated = True
                warnings.append(f"SAP call budget of {MAX_DEPENDENCY_SAP_CALLS} reached.")

            if dep_result.truncated:
                truncated = True

            for dep in dep_result.relationships:
                # Check if this dep targets our object
                if (
                    dep.target_object_type == target_type
                    and dep.target_object_name == target_name
                ):
                    # Apply filters
                    if request.relationship_types:
                        if dep.relationship_type.value not in {rt.upper() for rt in request.relationship_types}:
                            continue
                    if min_conf_enum and not self._passes_confidence(dep.confidence, min_conf_enum):
                        continue
                    if not request.include_dynamic and dep.dynamic:
                        continue

                    src_key = (dep.source_object_type, dep.source_object_name)
                    if src_key not in dependent_map:
                        dependent_map[src_key] = []
                    dependent_map[src_key].append(dep)

        # Step 4: Build result edges and dependents
        all_edges: list[DependencyEdge] = []
        dependents: list[ReverseDependent] = []

        for (src_type, src_name), deps in dependent_map.items():
            deduped = self._remove_exact_duplicates(deps)
            grouped = self._group_into_edges(deduped)
            all_edges.extend(grouped)

            rel_types = list({d.relationship_type.value for d in deduped})
            conf = strongest_confidence([d.confidence for d in deduped])
            any_dynamic = any(d.dynamic for d in deduped)
            evidence = [
                DependencyEvidence(
                    evidence_source=d.evidence_source,
                    source_component=d.source_component,
                    line_no=d.line_no,
                    detail=d.detail,
                    confidence=d.confidence,
                    dynamic=d.dynamic,
                )
                for d in deduped
            ]
            dependents.append(ReverseDependent(
                sap_object_type=src_type,
                object_name=src_name,
                relationship_types=rel_types,
                confidence=conf,
                dynamic=any_dynamic,
                evidence=evidence,
            ))

        # Build statistics
        rt_counts: dict[str, int] = {}
        conf_counts: dict[str, int] = {}
        dynamic_count = 0
        for e in all_edges:
            rt_counts[e.relationship_type.value] = rt_counts.get(e.relationship_type.value, 0) + 1
            conf_counts[e.confidence.value] = conf_counts.get(e.confidence.value, 0) + 1
            if e.dynamic:
                dynamic_count += 1

        stats = GraphStatistics(
            node_count=len(dependents),
            edge_count=len(all_edges),
            relationship_type_counts=rt_counts,
            confidence_counts=conf_counts,
            dynamic_edge_count=dynamic_count,
            sap_calls=sap_calls,
        )

        logger.info(
            "dependency_service_reverse_complete",
            extra={
                "operation": "get_reverse_dependencies",
                "target_type": target_type,
                "target_name": target_name,
                "candidate_count": candidate_count,
                "matched_dependents": len(dependents),
                "sap_calls": sap_calls,
                "truncated": truncated,
            },
        )

        return ReverseDependencyResult(
            target_object=RepositoryObjectReference(
                sap_object_type=target_type, object_name=target_name
            ),
            scope_summary=scope_summary,
            candidate_count=candidate_count,
            matched_dependents=len(dependents),
            truncated=truncated,
            warnings=warnings,
            relationships=all_edges,
            dependents=dependents,
            statistics=stats,
        )

    # ------------------------------------------------------------------
    # Public: impact analysis (deterministic)
    # ------------------------------------------------------------------

    async def get_impact_analysis(
        self,
        request: DependencyImpactRequest,
    ) -> DependencyImpactResult:
        """Deterministic impact analysis — no LLM, no AI.

        Performs bounded reverse lookup to find direct and transitive dependents.
        Reports counts, relationship distributions, and confidence distributions.
        """
        target_type = self._normalize_object_type(request.object.sap_object_type)
        target_name = self._normalize_object_name(request.object.object_name)

        # Build reverse request from impact request scope
        reverse_req = ReverseDependencyRequest(
            target_object=RepositoryObjectReference(
                sap_object_type=target_type,
                object_name=target_name,
            ),
            scope=request.scope,
            relationship_types=request.relationship_types,
            min_confidence=request.min_confidence,
            include_dynamic=True,
            max_candidates=request.max_candidates or REVERSE_MAX_CANDIDATES_DEFAULT,
        )

        reverse_result = await self.get_reverse_dependencies(reverse_req)

        direct_dependents = reverse_result.dependents
        direct_dependent_count = len(direct_dependents)

        # Build result structures
        all_edges = list(reverse_result.relationships)

        # Aggregate statistics
        rt_counts: dict[str, int] = {}
        conf_counts: dict[str, int] = {}
        dynamic_count = 0
        for e in all_edges:
            rt_counts[e.relationship_type.value] = rt_counts.get(e.relationship_type.value, 0) + 1
            conf_counts[e.confidence.value] = conf_counts.get(e.confidence.value, 0) + 1
            if e.dynamic:
                dynamic_count += 1

        # Build impacted objects list from direct dependents
        impacted_objects = list(direct_dependents)
        total_impacted = len(impacted_objects)

        warnings = list(reverse_result.warnings)
        truncated = reverse_result.truncated

        logger.info(
            "dependency_service_impact_complete",
            extra={
                "operation": "get_impact_analysis",
                "target_type": target_type,
                "target_name": target_name,
                "direct_dependent_count": direct_dependent_count,
                "total_impacted": total_impacted,
                "truncated": truncated,
            },
        )

        return DependencyImpactResult(
            target_object=RepositoryObjectReference(
                sap_object_type=target_type,
                object_name=target_name,
            ),
            direct_dependent_count=direct_dependent_count,
            transitive_dependent_count=0,  # Phase 8: direct only
            total_impacted_objects=total_impacted,
            relationship_type_counts=rt_counts,
            confidence_counts=conf_counts,
            dynamic_reference_count=dynamic_count,
            max_depth_reached=1,
            truncated=truncated,
            warnings=warnings,
            impacted_objects=impacted_objects,
            edges=all_edges,
        )
