"""Phase 8 — Dependency & Relationship Intelligence models.

Defines the complete type system for SAP dependency analysis:

* DependencyRelationshipType  — canonical SAP dependency relationship enum
* DependencyConfidence        — evidence confidence levels with ordering
* DependencyEvidence          — individual evidence record
* RepositoryDependency        — single flat relationship row (direct from ECC)
* DirectDependencyResult      — provider-level result container
* RepositoryObjectReference   — lightweight reference to a SAP object node
* DependencyEdge              — conceptual edge grouping multiple evidence records
* DependencyNode              — graph node
* DependencyGraphEdge         — edge in a traversal graph
* DependencyGraph             — full traversal graph result
* DependencyTraversalRequest  — forward/backward traversal request
* ReverseScopeSpec            — bounded reverse dependency scope
* ReverseDependencyRequest    — reverse dependency lookup request
* DependencyImpactRequest     — impact analysis request
* DependencyImpactResult      — impact analysis response
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field, field_validator, model_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class DependencyRelationshipType(str, Enum):
    """Canonical SAP dependency relationship types supported by Phase 8."""

    INCLUDES = "INCLUDES"
    CALLS_PROGRAM = "CALLS_PROGRAM"
    CALLS_FUNCTION = "CALLS_FUNCTION"
    USES_CLASS = "USES_CLASS"
    IMPLEMENTS_INTERFACE = "IMPLEMENTS_INTERFACE"
    INHERITS_CLASS = "INHERITS_CLASS"
    READS_TABLE = "READS_TABLE"
    WRITES_TABLE = "WRITES_TABLE"
    USES_DATA_ELEMENT = "USES_DATA_ELEMENT"
    USES_DOMAIN = "USES_DOMAIN"
    TRANSACTION_TARGET = "TRANSACTION_TARGET"


class DependencyConfidence(str, Enum):
    """Confidence level for a dependency relationship.

    EXACT   — authoritative SAP metadata / DDIC relationship
    HIGH    — explicit static literal source relationship
    MEDIUM  — static source parsing / heuristic evidence
    LOW     — dynamic or unresolved relationship
    """

    EXACT = "EXACT"
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


# Deterministic confidence rank. Higher = stronger.
CONFIDENCE_RANK: dict[DependencyConfidence, int] = {
    DependencyConfidence.EXACT: 4,
    DependencyConfidence.HIGH: 3,
    DependencyConfidence.MEDIUM: 2,
    DependencyConfidence.LOW: 1,
}


def confidence_rank(confidence: DependencyConfidence) -> int:
    """Return the numeric rank for a confidence level (higher = stronger)."""
    return CONFIDENCE_RANK.get(confidence, 0)


def strongest_confidence(confidences: list[DependencyConfidence]) -> DependencyConfidence:
    """Return the strongest (highest rank) confidence from a list."""
    if not confidences:
        return DependencyConfidence.LOW
    return max(confidences, key=confidence_rank)


class TraversalDirection(str, Enum):
    """Direction of traversal for dependency graph analysis."""

    FORWARD = "FORWARD"
    REVERSE = "REVERSE"


# ---------------------------------------------------------------------------
# Supported source object types for dependency queries
# ---------------------------------------------------------------------------

PHASE8_DEPENDENCY_SOURCE_TYPES: frozenset[str] = frozenset(
    {
        "PROG",
        "CLAS",
        "INTF",
        "FUGR",
        "FUNC",
        "TABL",
        "VIEW",
        "DTEL",
        "TRAN",
    }
)

# Hard limits
MAX_RESULTS_DEFAULT: int = 1000
MAX_RESULTS_MIN: int = 1
MAX_RESULTS_HARD: int = 10_000

TRAVERSAL_MAX_DEPTH_DEFAULT: int = 1
TRAVERSAL_MAX_DEPTH_HARD: int = 5
TRAVERSAL_MAX_NODES_DEFAULT: int = 500
TRAVERSAL_MAX_NODES_HARD: int = 5000
TRAVERSAL_MAX_EDGES_DEFAULT: int = 2000
TRAVERSAL_MAX_EDGES_HARD: int = 20_000

REVERSE_MAX_CANDIDATES_DEFAULT: int = 100
REVERSE_MAX_CANDIDATES_HARD: int = 1000
REVERSE_CONCURRENCY_DEFAULT: int = 3
REVERSE_CONCURRENCY_MAX: int = 5

MCP_MAX_RESULTS_DEFAULT: int = 200
MCP_MAX_RESULTS_HARD: int = 1000
MCP_TRAVERSAL_MAX_DEPTH: int = 3
MCP_TRAVERSAL_MAX_NODES: int = 200
MCP_TRAVERSAL_MAX_EDGES: int = 500
MCP_REVERSE_MAX_CANDIDATES: int = 50

DYNAMIC_TARGET_SENTINEL: str = "<DYNAMIC>"


# ---------------------------------------------------------------------------
# Core dependency models
# ---------------------------------------------------------------------------


class DependencyEvidence(BaseModel):
    """A single piece of evidence supporting a dependency relationship."""

    evidence_source: str | None = Field(
        default=None,
        description="Source of this evidence (DDIC, TSTC, SOURCE_STATIC, SOURCE_HEURISTIC, SOURCE_DYNAMIC).",
    )
    source_component: str | None = Field(
        default=None,
        description="SAP source component or include where the reference was found.",
    )
    line_no: int | None = Field(
        default=None,
        description="Source line number within the component.",
    )
    detail: str | None = Field(
        default=None,
        description="Additional detail text about this evidence record.",
    )
    confidence: DependencyConfidence = Field(
        default=DependencyConfidence.MEDIUM,
        description="Confidence level for this evidence.",
    )
    dynamic: bool = Field(
        default=False,
        description="True when this dependency was detected from a dynamic code pattern.",
    )


class RepositoryDependency(BaseModel):
    """A single flat dependency relationship row from DependencyRelationsSet."""

    relation_id: int | None = Field(default=None, description="SAP internal relation ID.")
    system_id: str | None = Field(default=None, description="SAP system ID.")
    client: str | None = Field(default=None, description="SAP client.")
    source_object_type: str = Field(description="SAP object type of the source (e.g. PROG).")
    source_object_name: str = Field(description="SAP object name of the source.")
    target_object_type: str = Field(description="SAP object type of the target.")
    target_object_name: str = Field(description="SAP object name of the target.")
    relationship_type: DependencyRelationshipType = Field(description="Canonical relationship type.")
    evidence_source: str | None = Field(default=None, description="Evidence source.")
    confidence: DependencyConfidence = Field(
        default=DependencyConfidence.MEDIUM,
        description="Confidence level.",
    )
    source_component: str | None = Field(default=None, description="Source component.")
    line_no: int | None = Field(default=None, description="Source line number.")
    dynamic: bool = Field(default=False, description="True when this is a dynamic reference.")
    detail: str | None = Field(default=None, description="Additional detail text.")


class DirectDependencyResult(BaseModel):
    """Provider-level result for a DependencyRelationsSet query."""

    system_id: str | None = Field(default=None)
    client: str | None = Field(default=None)
    source_object_type: str
    source_object_name: str
    total_relations: int = Field(default=0)
    returned_relations: int = Field(default=0)
    truncated: bool = Field(default=False)
    message: str | None = Field(default=None)
    relationships: list[RepositoryDependency] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Service-layer models
# ---------------------------------------------------------------------------


class RepositoryObjectReference(BaseModel):
    """Lightweight reference to a SAP repository object (no metadata calls)."""

    sap_object_type: str = Field(description="SAP object type (e.g. PROG).")
    object_name: str = Field(description="SAP object name.")
    package: str | None = Field(default=None, description="Package (only when already known).")
    description: str | None = Field(default=None, description="Description (only when already known).")


class DependencyEdge(BaseModel):
    """Conceptual dependency edge grouping multiple evidence records.

    SOURCE object → RELATIONSHIP TYPE → TARGET object

    Edge-level confidence = strongest evidence record (EXACT > HIGH > MEDIUM > LOW).
    All individual evidence records are preserved.
    """

    source: RepositoryObjectReference
    target: RepositoryObjectReference
    relationship_type: DependencyRelationshipType
    confidence: DependencyConfidence
    dynamic: bool = Field(default=False)
    evidence: list[DependencyEvidence] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Traversal graph models
# ---------------------------------------------------------------------------


class DependencyNode(BaseModel):
    """A node in a dependency traversal graph."""

    sap_object_type: str
    object_name: str
    depth: int = Field(default=0, description="Hops from root object.")


class DependencyGraphEdge(BaseModel):
    """An edge in a dependency traversal graph with cycle and depth info."""

    source: RepositoryObjectReference
    target: RepositoryObjectReference
    relationship_type: DependencyRelationshipType
    confidence: DependencyConfidence
    dynamic: bool = Field(default=False)
    evidence: list[DependencyEvidence] = Field(default_factory=list)
    depth: int = Field(default=0, description="Traversal depth at which this edge was discovered.")
    creates_cycle: bool = Field(default=False, description="True when this edge creates a cycle.")


class GraphStatistics(BaseModel):
    """Deterministic statistics about a dependency graph traversal."""

    node_count: int = Field(default=0)
    edge_count: int = Field(default=0)
    relationship_type_counts: dict[str, int] = Field(default_factory=dict)
    confidence_counts: dict[str, int] = Field(default_factory=dict)
    dynamic_edge_count: int = Field(default=0)
    cycle_count: int = Field(default=0)
    sap_calls: int = Field(default=0)


class DependencyGraph(BaseModel):
    """Result of a bounded forward dependency traversal (BFS)."""

    root: RepositoryObjectReference
    direction: TraversalDirection = Field(default=TraversalDirection.FORWARD)
    requested_max_depth: int
    depth_reached: int = Field(default=0)
    truncated: bool = Field(default=False)
    nodes: list[DependencyNode] = Field(default_factory=list)
    edges: list[DependencyGraphEdge] = Field(default_factory=list)
    cycles_detected: bool = Field(default=False)
    warnings: list[str] = Field(default_factory=list)
    statistics: GraphStatistics = Field(default_factory=GraphStatistics)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class DependencyTraversalRequest(BaseModel):
    """Request for bounded forward dependency traversal."""

    root_object: RepositoryObjectReference = Field(description="Object to start traversal from.")
    direction: TraversalDirection = Field(
        default=TraversalDirection.FORWARD,
        description="Traversal direction (FORWARD supported in Phase 8).",
    )
    max_depth: int = Field(
        default=TRAVERSAL_MAX_DEPTH_DEFAULT,
        ge=1,
        le=TRAVERSAL_MAX_DEPTH_HARD,
        description=f"Maximum traversal depth (1–{TRAVERSAL_MAX_DEPTH_HARD}).",
    )
    relationship_types: list[str] | None = Field(
        default=None,
        description="Optional relationship type filter. Must be canonical types.",
    )
    min_confidence: str | None = Field(
        default=None,
        description="Minimum confidence level (EXACT, HIGH, MEDIUM, LOW).",
    )
    max_nodes: int = Field(
        default=TRAVERSAL_MAX_NODES_DEFAULT,
        ge=1,
        le=TRAVERSAL_MAX_NODES_HARD,
        description=f"Maximum nodes in graph (1–{TRAVERSAL_MAX_NODES_HARD}).",
    )
    max_edges: int = Field(
        default=TRAVERSAL_MAX_EDGES_DEFAULT,
        ge=1,
        le=TRAVERSAL_MAX_EDGES_HARD,
        description=f"Maximum edges in graph (1–{TRAVERSAL_MAX_EDGES_HARD}).",
    )
    include_dynamic: bool = Field(
        default=True,
        description="Whether to include dynamic (unresolved) relationships.",
    )

    @field_validator("relationship_types")
    @classmethod
    def _validate_relationship_types(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        valid = {rt.value for rt in DependencyRelationshipType}
        for rt in v:
            if rt.strip().upper() not in valid:
                raise ValueError(
                    f"Invalid relationship type '{rt}'. "
                    f"Supported: {sorted(valid)}"
                )
        return [rt.strip().upper() for rt in v]

    @field_validator("min_confidence")
    @classmethod
    def _validate_min_confidence(cls, v: str | None) -> str | None:
        if v is None:
            return v
        valid = {c.value for c in DependencyConfidence}
        if v.strip().upper() not in valid:
            raise ValueError(
                f"Invalid confidence '{v}'. Supported: {sorted(valid)}"
            )
        return v.strip().upper()


class ReverseScopeSpec(BaseModel):
    """Bounded scope specification for reverse dependency searches.

    Reuses Phase 3 repository search semantics via RepositoryDiscoveryService.
    At least one scope selector must be provided.
    """

    packages: list[str] = Field(default_factory=list, description="SAP packages to search.")
    include_subpackages: bool = Field(default=False)
    name_patterns: list[str] = Field(default_factory=list, description="Name patterns (e.g. Z*).")
    object_types: list[str] = Field(default_factory=list, description="Object types to restrict search.")
    all_custom_objects: bool = Field(
        default=False,
        description="Search all Z*/Y* objects (must be explicitly requested).",
    )
    max_results: int = Field(
        default=REVERSE_MAX_CANDIDATES_DEFAULT,
        ge=1,
        le=REVERSE_MAX_CANDIDATES_HARD,
        description=f"Maximum candidate objects (1–{REVERSE_MAX_CANDIDATES_HARD}).",
    )

    @model_validator(mode="after")
    def _require_scope_selector(self) -> "ReverseScopeSpec":
        if not self.packages and not self.name_patterns and not self.all_custom_objects:
            raise ValueError(
                "Reverse dependency scope requires at least one package, "
                "name pattern, or all_custom_objects=true."
            )
        return self


class ReverseDependencyRequest(BaseModel):
    """Request for bounded reverse dependency lookup."""

    target_object: RepositoryObjectReference = Field(description="Object to find dependents of.")
    scope: ReverseScopeSpec = Field(description="Bounded candidate search scope.")
    relationship_types: list[str] | None = Field(
        default=None,
        description="Optional relationship type filter.",
    )
    min_confidence: str | None = Field(
        default=None,
        description="Minimum confidence level filter.",
    )
    include_dynamic: bool = Field(
        default=False,
        description="Whether to include dynamic references in reverse results.",
    )
    max_candidates: int = Field(
        default=REVERSE_MAX_CANDIDATES_DEFAULT,
        ge=1,
        le=REVERSE_MAX_CANDIDATES_HARD,
        description=f"Maximum candidate objects to inspect (1–{REVERSE_MAX_CANDIDATES_HARD}).",
    )

    @field_validator("relationship_types")
    @classmethod
    def _validate_relationship_types(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return v
        valid = {rt.value for rt in DependencyRelationshipType}
        for rt in v:
            if rt.strip().upper() not in valid:
                raise ValueError(
                    f"Invalid relationship type '{rt}'. Supported: {sorted(valid)}"
                )
        return [rt.strip().upper() for rt in v]

    @field_validator("min_confidence")
    @classmethod
    def _validate_min_confidence(cls, v: str | None) -> str | None:
        if v is None:
            return v
        valid = {c.value for c in DependencyConfidence}
        if v.strip().upper() not in valid:
            raise ValueError(f"Invalid confidence '{v}'. Supported: {sorted(valid)}")
        return v.strip().upper()


class DependencyImpactRequest(BaseModel):
    """Request for deterministic impact analysis (reverse + transitive analysis)."""

    object: RepositoryObjectReference = Field(description="Object to analyse impact for.")
    scope: ReverseScopeSpec = Field(description="Bounded candidate search scope.")
    max_depth: int = Field(
        default=TRAVERSAL_MAX_DEPTH_DEFAULT,
        ge=1,
        le=TRAVERSAL_MAX_DEPTH_HARD,
        description="Maximum transitive depth for impact analysis.",
    )
    relationship_types: list[str] | None = Field(default=None)
    min_confidence: str | None = Field(default=None)
    max_candidates: int | None = Field(default=None, ge=1)
    max_nodes: int | None = Field(default=None, ge=1)
    max_edges: int | None = Field(default=None, ge=1)


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ReverseDependent(BaseModel):
    """A single dependent object found during reverse lookup."""

    sap_object_type: str
    object_name: str
    relationship_types: list[str] = Field(default_factory=list)
    confidence: DependencyConfidence = Field(default=DependencyConfidence.MEDIUM)
    dynamic: bool = Field(default=False)
    evidence: list[DependencyEvidence] = Field(default_factory=list)


class ReverseDependencyResult(BaseModel):
    """Result of a bounded reverse dependency lookup."""

    target_object: RepositoryObjectReference
    scope_summary: dict[str, object] = Field(default_factory=dict)
    candidate_count: int = Field(default=0)
    matched_dependents: int = Field(default=0)
    truncated: bool = Field(default=False)
    warnings: list[str] = Field(default_factory=list)
    relationships: list[DependencyEdge] = Field(default_factory=list)
    dependents: list[ReverseDependent] = Field(default_factory=list)
    statistics: GraphStatistics = Field(default_factory=GraphStatistics)


class DependencyImpactResult(BaseModel):
    """Result of deterministic impact analysis.

    Shows which objects depend on (are impacted by) the target object,
    both directly and transitively. No LLM or AI is involved.
    """

    target_object: RepositoryObjectReference
    direct_dependent_count: int = Field(default=0)
    transitive_dependent_count: int = Field(default=0)
    total_impacted_objects: int = Field(default=0)
    relationship_type_counts: dict[str, int] = Field(default_factory=dict)
    confidence_counts: dict[str, int] = Field(default_factory=dict)
    dynamic_reference_count: int = Field(default=0)
    max_depth_reached: int = Field(default=0)
    truncated: bool = Field(default=False)
    warnings: list[str] = Field(default_factory=list)
    impacted_objects: list[ReverseDependent] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)
