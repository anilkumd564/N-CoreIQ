# N-CoreIQ Phase 8 — Dependency & Relationship Intelligence

## Overview

Phase 8 adds SAP ABAP dependency analysis to N-CoreIQ. It consumes the
`DependencyRelationsSet` entity set from the existing `ZNCOREIQ_REPO_SRV`
OData V2 service, backed by the `Z_MCP_REPO_DEPENDENCIES` function module
on SAP ECC.

All dependency extraction is performed by SAP ECC. N-CoreIQ is responsible
for normalization, deduplication, confidence filtering, traversal, reverse
lookup, and impact analysis.

---

## Architecture

```
REST / MCP
    ↓
RepositoryDependencyService
    ↓
SapConnectionProvider.get_object_dependencies()
    ↓
ODataSAPConnectionProvider → DependencyRelationsSet (OData V2 HTTPS)
    ↓
ZNCOREIQ_REPO_SRV → Z_MCP_REPO_DEPENDENCIES
    ↓
SAP ECC
```

**Key design principles:**
- No direct RFC/FM calls from Python
- No source re-parsing in Python (ECC owns dependency extraction)
- No metadata N+1 (ObjectMetadataSet not called during traversal)
- No graph database (in-memory structures only)
- No AI/LLM analysis (all logic is deterministic)
- Read-only (no SAP write operations)

---

## SAP Service Details

| Property | Value |
|---|---|
| OData Service | `ZNCOREIQ_REPO_SRV` |
| Entity Set | `DependencyRelationsSet` |
| Transport | OData V2 / HTTPS |
| SAP Function Module | `Z_MCP_REPO_DEPENDENCIES` |

### OData Filter Parameters

```
SourceObjectType eq 'PROG'
and SourceObjectName eq 'ZFI_REPORT'
and MaxResults eq 1000
```

---

## Relationship Taxonomy

Phase 8 supports exactly these canonical relationship types:

| Value | Meaning |
|---|---|
| `INCLUDES` | ABAP INCLUDE statement |
| `CALLS_PROGRAM` | SUBMIT / CALL PROGRAM |
| `CALLS_FUNCTION` | CALL FUNCTION |
| `USES_CLASS` | Class instantiation / static call |
| `IMPLEMENTS_INTERFACE` | Class implements interface |
| `INHERITS_CLASS` | Class inherits from another class |
| `READS_TABLE` | SELECT / OPEN CURSOR on a table |
| `WRITES_TABLE` | INSERT / UPDATE / MODIFY / DELETE on a table |
| `USES_DATA_ELEMENT` | DDIC data element reference |
| `USES_DOMAIN` | DDIC domain reference |
| `TRANSACTION_TARGET` | Transaction → program mapping (TSTC) |

Only these values are accepted by REST and MCP layers. Unknown future
relationship strings from ECC are **skipped** and logged as warnings.

---

## Confidence Model

| Level | Rank | Semantics |
|---|---|---|
| `EXACT` | 4 | Authoritative SAP metadata / DDIC relationship |
| `HIGH` | 3 | Explicit static literal source reference |
| `MEDIUM` | 2 | Static source parsing / heuristic evidence |
| `LOW` | 1 | Dynamic or unresolved relationship |

Confidence is **never upgraded** by N-CoreIQ Python code. The value
from ECC is preserved as-is.

When filtering with `min_confidence`:
- `min_confidence=HIGH` → includes EXACT and HIGH; excludes MEDIUM and LOW
- `min_confidence=MEDIUM` → includes EXACT, HIGH, MEDIUM; excludes LOW
- `min_confidence=LOW` → includes all

---

## Evidence Model

Each dependency row carries evidence fields:

| Field | Description |
|---|---|
| `evidence_source` | Source of evidence: `DDIC`, `TSTC`, `SOURCE_STATIC`, `SOURCE_HEURISTIC`, `SOURCE_DYNAMIC` |
| `source_component` | SAP source component / include where reference was found |
| `line_no` | Source line number (where applicable) |
| `detail` | Additional detail text (e.g. the statement text) |
| `confidence` | Evidence confidence level |
| `dynamic` | True when this is a dynamic/unresolved reference |

Multiple evidence rows for the same logical relationship (same
source→target→relationship) represent distinct source locations and are
**all preserved**.

---

## Dynamic References

SAP ECC uses the sentinel value `<DYNAMIC>` as the target name for
unresolved dynamic references (dynamic CALL FUNCTION, SUBMIT, etc.).

N-CoreIQ behavior:
- Dynamic references are returned as normal dependency edges
- `<DYNAMIC>` targets are **never traversed** (traversal stops at them)
- `dynamic=true` is preserved in all evidence records and edges
- Dynamic references can be filtered out via `include_dynamic=false` in
  traversal/reverse requests

---

## Supported Source Object Types

Phase 8 dependency lookup supports these source types:

`PROG`, `CLAS`, `INTF`, `FUGR`, `FUNC`, `TABL`, `VIEW`, `DTEL`, `TRAN`

Object type is validated before any SAP call is issued.

---

## REST API Endpoints

### GET Direct Dependencies

```
GET /api/v1/repository/objects/{object_type}/{object_name}/dependencies
```

**Query parameters:**

| Parameter | Type | Default | Description |
|---|---|---|---|
| `relationship_type` | string (repeatable) | all | Filter by relationship type |
| `min_confidence` | string | none | Minimum confidence filter |
| `max_results` | integer | 1000 | Maximum dependency rows (1–10000) |

**Example:**
```
GET /api/v1/repository/objects/PROG/ZFI_REPORT/dependencies
    ?min_confidence=MEDIUM&max_results=500
    &relationship_type=CALLS_FUNCTION
    &relationship_type=READS_TABLE
```

**Response:** `DirectDependencyResult`
```json
{
  "system_id": "S4H",
  "client": "100",
  "source_object_type": "PROG",
  "source_object_name": "ZFI_REPORT",
  "total_relations": 25,
  "returned_relations": 25,
  "truncated": false,
  "message": null,
  "relationships": [...]
}
```

**Status codes:**
- `200` — Success (may return empty `relationships` list)
- `404` — Object not found
- `422` — Unsupported object type, invalid parameters
- `501` — SAP provider not available
- `503` — SAP connectivity failure

---

### POST Forward Traversal

```
POST /api/v1/repository/dependencies/traverse
```

Performs bounded BFS forward traversal starting from a root object.

**Request body:** `DependencyTraversalRequest`
```json
{
  "root_object": {"sap_object_type": "PROG", "object_name": "ZFI_REPORT"},
  "max_depth": 2,
  "relationship_types": ["CALLS_FUNCTION", "READS_TABLE"],
  "min_confidence": "MEDIUM",
  "max_nodes": 500,
  "max_edges": 2000,
  "include_dynamic": true
}
```

**Hard limits:** max_depth ≤ 5, max_nodes ≤ 5000, max_edges ≤ 20000

**Response:** `DependencyGraph`
```json
{
  "root": {"sap_object_type": "PROG", "object_name": "ZFI_REPORT"},
  "direction": "FORWARD",
  "requested_max_depth": 2,
  "depth_reached": 2,
  "truncated": false,
  "nodes": [...],
  "edges": [...],
  "cycles_detected": false,
  "warnings": [],
  "statistics": {
    "node_count": 12,
    "edge_count": 15,
    "relationship_type_counts": {"CALLS_FUNCTION": 8, "READS_TABLE": 7},
    "confidence_counts": {"HIGH": 10, "MEDIUM": 5},
    "dynamic_edge_count": 2,
    "cycle_count": 0,
    "sap_calls": 12
  }
}
```

**BFS behavior:**
- Root object is at depth 0
- Each target object is fetched once per traversal (request-local cache)
- `<DYNAMIC>` targets are included as edges but never traversed
- Cycles are detected and marked via `creates_cycle=true` on edges
- Traversal terminates safely regardless of cycles

---

### POST Reverse Dependency Lookup

```
POST /api/v1/repository/dependencies/reverse
```

Finds objects that depend on the specified target object within a bounded scope.

**A scope is required.** Never performs unbounded repository-wide scan.

**Request body:** `ReverseDependencyRequest`
```json
{
  "target_object": {"sap_object_type": "FUNC", "object_name": "ZFI_GET_DATA"},
  "scope": {
    "packages": ["ZFINANCE"],
    "include_subpackages": true,
    "max_results": 100
  },
  "relationship_types": ["CALLS_FUNCTION"],
  "min_confidence": null,
  "include_dynamic": false,
  "max_candidates": 100
}
```

**Algorithm:**
1. Discover candidate source objects using RepositoryDiscoveryService
2. Fetch each candidate's direct dependencies (bounded concurrency: max 3)
3. Match edges whose target equals the requested object
4. Deduplicate and return dependents

**Default limits:** max_candidates=100, hard maximum=1000

**Response:** `ReverseDependencyResult`
```json
{
  "target_object": {...},
  "scope_summary": {...},
  "candidate_count": 45,
  "matched_dependents": 3,
  "truncated": false,
  "warnings": [],
  "relationships": [...],
  "dependents": [...],
  "statistics": {...}
}
```

**Empty result (no dependents) returns HTTP 200** — not 404.

---

### POST Impact Analysis

```
POST /api/v1/repository/dependencies/impact
```

Deterministic technical impact analysis. **No AI or LLM involved.**

**Request body:** `DependencyImpactRequest`
```json
{
  "object": {"sap_object_type": "TABL", "object_name": "ZFIN_POSITIONS"},
  "scope": {
    "packages": ["ZFINANCE"],
    "include_subpackages": true
  },
  "relationship_types": null,
  "min_confidence": null,
  "max_candidates": 100
}
```

**Response:** `DependencyImpactResult`
```json
{
  "target_object": {...},
  "direct_dependent_count": 8,
  "transitive_dependent_count": 0,
  "total_impacted_objects": 8,
  "relationship_type_counts": {"READS_TABLE": 5, "WRITES_TABLE": 3},
  "confidence_counts": {"HIGH": 6, "MEDIUM": 2},
  "dynamic_reference_count": 1,
  "max_depth_reached": 1,
  "truncated": false,
  "warnings": [],
  "impacted_objects": [...],
  "edges": [...]
}
```

---

## MCP Tools

### get_dependencies

Returns direct SAP dependencies for one repository object.

```json
{
  "object_type": "PROG",
  "object_name": "ZFI_REPORT",
  "relationship_types": ["CALLS_FUNCTION"],
  "min_confidence": "HIGH",
  "max_results": 200
}
```

**MCP limits:** max_results ≤ 1000 (default 200)

### traverse_dependencies

Bounded BFS traversal of SAP dependency relationships.

```json
{
  "object_type": "PROG",
  "object_name": "ZFI_REPORT",
  "max_depth": 2,
  "relationship_types": null,
  "min_confidence": null,
  "max_nodes": 100,
  "max_edges": 300
}
```

**MCP limits:** max_depth ≤ 3, max_nodes ≤ 200, max_edges ≤ 500

### find_dependents

Find SAP objects that depend on the specified target. Bounded scope required.

```json
{
  "object_type": "FUNC",
  "object_name": "ZFI_GET_DATA",
  "packages": ["ZFINANCE"],
  "name_patterns": null,
  "all_custom_objects": false,
  "relationship_types": null,
  "min_confidence": null,
  "max_candidates": 50
}
```

**MCP limits:** max_candidates ≤ 50

### analyze_dependency_impact

Deterministic technical impact analysis. No AI/LLM.

```json
{
  "object_type": "TABL",
  "object_name": "ZFIN_POSITIONS",
  "packages": ["ZFINANCE"],
  "name_patterns": null,
  "all_custom_objects": false,
  "relationship_types": null,
  "min_confidence": null
}
```

---

## Limits Reference

| Parameter | Default | Hard Maximum |
|---|---|---|
| max_results (direct) | 1000 | 10000 |
| max_depth (traversal) | 1 | 5 |
| max_nodes (traversal) | 500 | 5000 |
| max_edges (traversal) | 2000 | 20000 |
| max_candidates (reverse) | 100 | 1000 |
| MCP max_results | 200 | 1000 |
| MCP max_depth | 2 | 3 |
| MCP max_nodes | 100 | 200 |
| MCP max_edges | 300 | 500 |
| MCP max_candidates | 50 | 50 |
| Global SAP call budget | 500 | — |

---

## Known Limitations

Phase 8 dependency extraction is **static and bounded**. It may not resolve
all dependencies in cases involving:

- **Dynamic function calls** — `CALL FUNCTION lv_func_name` (target = `<DYNAMIC>`)
- **Dynamic program calls** — `SUBMIT (lv_prog)` (target = `<DYNAMIC>`)
- **Dynamic Open SQL** — `SELECT ... FROM (lv_tabname)`
- **Reflection** — `CREATE OBJECT` with dynamic class names
- **BAdIs and enhancements** — Runtime-resolved enhancement implementations
- **Workflow** — Background workflow task assignments
- **Macros** — ABAP macro expansions
- **Runtime-generated code** — `GENERATE SUBROUTINE POOL`
- **Multi-line source heuristics** — Complex statements spanning multiple lines
- **External integration dependencies** — IDocs, RFCs to external systems
- **Implicit dependencies** — Authorization objects, system tables

**Phase 8 does not claim complete runtime call-graph accuracy.**
All results are based on static analysis performed by `Z_MCP_REPO_DEPENDENCIES`
on the ECC server. Dynamic references are flagged but not resolved.

---

## Security

- No SAP credentials are ever returned in API or MCP responses
- No ABAP stack traces or SAP innererror details are exposed
- No filesystem paths are exposed in error messages
- Read-only: no SAP write operations are performed
- All dependency data is consumed from ECC over HTTPS/OData
