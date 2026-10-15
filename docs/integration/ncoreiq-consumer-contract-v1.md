# N-CoreIQ External Consumer Contract — v1

**Document version:** 1.0  
**Repository branch:** ncoreiq-integration  
**Baseline commit:** 8c11088  
**Date:** 2026-10-11  
**Status:** STABLE — Ready for N-CoreShift provider implementation

---

## Table of Contents

1. [Architecture Boundary](#1-architecture-boundary)
2. [Base URLs](#2-base-urls)
3. [Authentication](#3-authentication)
4. [Scopes and Role Mapping](#4-scopes-and-role-mapping)
5. [Common Response Conventions](#5-common-response-conventions)
6. [Error Contract](#6-error-contract)
7. [Operations](#7-operations)
8. [Evidence Provenance Fields](#8-evidence-provenance-fields)
9. [Pagination](#9-pagination)
10. [N-CoreShift Provider Mapping](#10-n-coreshift-provider-mapping)
11. [Service-to-Service Authentication Readiness](#11-service-to-service-authentication-readiness)
12. [What N-CoreIQ Does NOT Own](#12-what-n-coreiq-does-not-own)

---

## 1. Architecture Boundary

```
SAP ECC / S/4
      |
      v
N-CoreIQ  (this system)
      |
      | REST API (primary integration)
      | MCP /mcp/ (AI agent integration)
      |
      v
N-CoreShift
      |
      v
Clean Core assessment / 4R + Review / modernization planning
```

**N-CoreIQ owns:**
- SAP connectivity and credential management
- Package and repository discovery
- Object inventory and search
- Metadata extraction
- Complete source retrieval (no hidden line cap)
- Dependency extraction, traversal, reverse analysis, and impact analysis
- Bulk extraction jobs and artifacts
- MCP repository access

**N-CoreShift owns:**
- Functional intent interpretation
- Business requirement mapping
- Fit-to-Standard analysis
- Clean Core decisioning (RETIRE / REPLACE / REFACTOR / REPLATFORM / REVIEW)
- Modernization planning and implementation drafts

---

## 2. Base URLs

| Environment | URL |
|---|---|
| BTP Production (backend direct) | `https://ncoreiq.cfapps.us10.hana.ondemand.com` |
| BTP AppRouter (browser/UI) | via CF AppRouter — not for backend-to-backend integration |
| Local development | `http://localhost:8000` |

> **N-CoreShift integration MUST use the direct backend URL**, not the AppRouter
> URL. The AppRouter is for browser-based UI traffic only.

All REST endpoints are under: `{base}/api/v1/`

MCP endpoint (Streamable HTTP transport): `POST {base}/mcp/`

---

## 3. Authentication

### Local / Development Mode

`AUTH_MODE=disabled` — all requests are accepted with full anonymous access.
No token required.

### BTP Production Mode (`AUTH_MODE=xsuaa`)

All API endpoints (except `/health/live`, `/health/ready`, `/health`, `/`, `/docs`,
`/openapi.json`, `/redoc`) require a valid XSUAA Bearer JWT.

**Request header:**
```
Authorization: Bearer <jwt>
```

**Token acquisition (client credentials flow):**
```
POST https://<subaccount>.authentication.<region>.hana.ondemand.com/oauth/token
Content-Type: application/x-www-form-urlencoded

grant_type=client_credentials
&client_id=<ncoreshift_client_id>
&client_secret=<ncoreshift_client_secret>
```

> See [Section 11](#11-service-to-service-authentication-readiness) for
> service-to-service OAuth configuration status.

**JWT validation:**
- RS256 signature verified against XSUAA JWKS (`/token_keys`)
- Issuer and audience validated
- Expiry enforced
- JWKS cached (5 minutes TTL)

---

## 4. Scopes and Role Mapping

Scopes are defined in `deployment/btp/xs-security.json`.

| Scope | XSUAA Name | Capabilities |
|---|---|---|
| **Viewer** | `ncoreiq.NCoreIQ.Viewer` | SAP ping, packages, search, metadata, source, direct dependencies |
| **Analyst** | `ncoreiq.NCoreIQ.Analyst` | All Viewer + traversal, reverse lookup, impact analysis, bulk extract, export |
| **Operator** | `ncoreiq.NCoreIQ.Operator` | All Analyst + job management (list, get, create, cancel, retry, artifact download) |
| **Admin** | `ncoreiq.NCoreIQ.Admin` | All Operator + diagnostics, configuration visibility |

**N-CoreShift minimum required scopes:**
- `NCoreIQ.Viewer` — for all read operations (ping, packages, search, metadata, source, direct dependencies)
- `NCoreIQ.Analyst` — additionally required for traversal, reverse analysis, impact analysis, and extraction jobs

**Scope inheritance:** Higher roles always include all lower-role permissions.
The `NCoreIQ_Analyst` role-template in `xs-security.json` includes both
`NCoreIQ.Viewer` and `NCoreIQ.Analyst` scopes.

> **Note:** Scope enforcement is applied at the middleware layer (principal extraction and injection into
> `request.state.principal`). Authorization helpers are defined in `app/auth/principal.py`
> (`can_access()`, `is_viewer()`, `is_analyst()`, etc.) but per-route `Depends` enforcement
> is not yet wired on individual route handlers. In `AUTH_MODE=disabled` all requests succeed with
> `ANONYMOUS_PRINCIPAL` (all scopes granted). Full per-route enforcement is a future hardening task.

---

## 5. Common Response Conventions

- All responses are `application/json` unless noted (ZIP/binary downloads).
- Dates from SAP ECC are in `YYYYMMDD` string format.
- All object types and names are uppercase as returned by SAP.
- Field `truncated: true` indicates the result was capped by SAP or an explicit limit.
- Field `found: false` indicates the object does not exist in SAP.
- `system_id` and `client` in responses carry SAP system provenance.
- Correlation headers `X-Correlation-ID` and `X-Request-ID` are returned on all responses.
- `sha256` field on source responses contains the SHA-256 hex digest of the complete source text
  (see [Section 8](#8-evidence-provenance-fields)).

---

## 6. Error Contract

### Standard Error Body (5xx)

```json
{
  "error": "ERROR_CODE",
  "message": "Human-readable safe description.",
  "request_id": "uuid",
  "correlation_id": "uuid"
}
```

### Validation Error Body (422)

```json
{ "detail": "Validation message or Pydantic error array" }
```

### HTTP Status Codes

| Code | Meaning |
|---|---|
| `200` | Success |
| `202` | Accepted (job created, cancellation accepted) |
| `401` | Authentication required or token invalid/expired |
| `403` | Forbidden (authenticated but insufficient scope) |
| `404` | Object, package, or job not found |
| `409` | Conflict (idempotency key conflict; job not in expected state) |
| `410` | Gone (artifact expired and deleted) |
| `422` | Validation error (invalid body or query parameters) |
| `500` | Internal error (sanitized — no stack traces, no credentials exposed) |
| `501` | Not implemented (RFC mode disabled; unsupported connection mode) |
| `503` | SAP unavailable, SAP timeout, SAP authentication failure |

### Never Exposed in Error Responses

- SAP user passwords or RFC credentials
- XSUAA client secrets or OAuth tokens
- Destination Service credentials
- Connectivity Service proxy tokens
- Internal Python stack traces
- SAP raw `innererror` XML/JSON

---

## 7. Operations

---

### A. SAP System Information (Ping)

**Purpose:** Verify N-CoreIQ to SAP ECC connectivity; retrieve system ID, client, SAP release.

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/v1/sap/ping` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Viewer` |
| **Synchronous** | Yes |
| **Pagination** | No |
| **Complete source** | N/A |

**Response (200):**
```json
{
  "status": "connected",
  "connection_mode": "odata",
  "system_id": "NC1",
  "client": "800",
  "sap_release": "752",
  "timestamp": "20261011",
  "ping_method": "SystemInfoSet",
  "message": "N-CoreIQ OData connectivity successful."
}
```

**Error responses:** `422` (config incomplete), `501` (RFC unavailable), `503` (SAP unreachable/auth failed)

---

### B. Package Discovery

**Purpose:** Resolve an SAP development package hierarchy.

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/v1/repository/packages/{package}` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Viewer` |
| **Synchronous** | Yes |
| **Pagination** | No |

**Query parameters:**
- `include_subpackages` (bool, default `false`)
- `max_depth` (int, default `20`, range `0–20`)

**Response (200):**
```json
{
  "system_id": "NC1",
  "client": "800",
  "root_package": "ZNCOREIQ",
  "include_subpackages": true,
  "max_depth": 20,
  "truncated": false,
  "message": null,
  "packages": [
    {"package": "ZNCOREIQ", "parent_package": null, "description": "N-CoreIQ", "depth": 0},
    {"package": "ZNCOREIQ_CORE", "parent_package": "ZNCOREIQ", "description": "Core", "depth": 1}
  ]
}
```

**Error responses:** `404` (package not found), `422` (invalid params), `503` (SAP unavailable)

---

### C. Repository Search

**Purpose:** Discover SAP custom repository objects by package, name pattern, and/or object type.

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/v1/repository/search` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Viewer` |
| **Synchronous** | Yes |
| **Pagination** | No (control size with `max_results`) |

**Request body:**
```json
{
  "packages": ["ZNCOREIQ"],
  "name_patterns": ["Z*"],
  "object_types": ["PROG", "CLAS"],
  "include_subpackages": true,
  "all_custom_objects": false,
  "max_results": 200
}
```

At least one of `packages`, `name_patterns`, or `all_custom_objects=true` must be provided.

**Supported object types:** PROG, CLAS, INTF, FUGR, FUNC, TABL, DTEL, DOMA, VIEW, SHLP, ENQU, TRAN, MSAG

**Response (200):**
```json
{
  "system_id": "NC1",
  "client": "800",
  "truncated": false,
  "message": null,
  "statistics": {"total_matches": 42, "returned_count": 42},
  "objects": [
    {
      "sap_object_type": "PROG",
      "friendly_type": "Program",
      "object_name": "ZREPORT_DEMO",
      "package": "ZNCOREIQ",
      "description": "Demo report",
      "author": "DEVUSER",
      "match_reason": "PACKAGE"
    }
  ]
}
```

**Error responses:** `422` (missing selector), `503` (SAP unavailable)

---

### D. Object Metadata

**Purpose:** Retrieve technical metadata for a single SAP repository object.

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/v1/repository/objects/{object_type}/{object_name}/metadata` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Viewer` |
| **Synchronous** | Yes |
| **Pagination** | No |

**Response (200):**
```json
{
  "sap_object_type": "PROG",
  "object_name": "ZREPORT_DEMO",
  "system_id": "NC1",
  "client": "800",
  "pgmid": "R3TR",
  "package": "ZNCOREIQ",
  "object_subtype": null,
  "program_name": null,
  "function_group": null,
  "table_class": null,
  "author": "DEVUSER",
  "master_language": "EN",
  "source_system": null,
  "created_date": "20230115",
  "changed_by": "DEVUSER2",
  "changed_date": "20261001",
  "description": "Demo report",
  "active_status": "A",
  "found": true,
  "warning": null,
  "message": null
}
```

**Error responses:** `404` (not found), `422` (unsupported type or blank identifier), `503` (SAP unavailable)

---

### E. Object Source / Technical Definition

**Purpose:** Retrieve ABAP source code or structured technical definition.

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/v1/repository/objects/{object_type}/{object_name}/source` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Viewer` |
| **Synchronous** | Yes |
| **Pagination** | Transparent — SAP `__next` links followed automatically until complete |
| **Complete source by default** | **YES** |

**Query parameters:**
- `max_lines` (int, optional, range `1–20000`) — **omit for complete (unlimited) retrieval**.
  When supplied, returns at most N lines and sets `truncated=true` if more exist.

**Examples:**
```
GET /api/v1/repository/objects/PROG/ZREPORT_DEMO/source
   -> complete source, no truncation

GET /api/v1/repository/objects/PROG/ZREPORT_DEMO/source?max_lines=500
   -> at most 500 lines
```

**Response (200):**
```json
{
  "sap_object_type": "PROG",
  "object_name": "ZREPORT_DEMO",
  "system_id": "NC1",
  "client": "800",
  "max_lines": null,
  "returned_lines": 1250,
  "component_count": 1,
  "found": true,
  "truncated": false,
  "message": null,
  "sha256": "a3f1c2d4e5b6a7c8d9e0f1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d7e8f9a0b1c2",
  "components": [
    {
      "component_type": "PROGRAM",
      "component_name": "ZREPORT_DEMO",
      "line_count": 1250,
      "source_text": "REPORT zreport_demo.\n...",
      "lines": [
        {"component_type": "PROGRAM", "component_name": "ZREPORT_DEMO", "line_no": 1, "source_line": "REPORT zreport_demo."}
      ]
    }
  ]
}
```

**Important notes:**
- `sha256` is a SHA-256 hex digest of all source lines joined by newlines, computed automatically. Never null for non-empty source.
- `max_lines: null` means no limit was requested — complete source was retrieved.
- `truncated: true` only appears when an explicit `max_lines` was requested and more lines exist.
- For objects with multiple includes/methods (CLAS, FUGR), multiple components are returned.
- SAP Gateway may paginate internally via `__next` links; N-CoreIQ follows all pages transparently.

**Error responses:** `404` (not found), `422` (unsupported type, blank identifier, invalid max_lines), `503` (SAP unavailable)

---

### F. Direct Dependencies

**Purpose:** Retrieve direct dependency relationships for a single SAP repository object.

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/v1/repository/objects/{object_type}/{object_name}/dependencies` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Viewer` |
| **Synchronous** | Yes |
| **Pagination** | No (use `max_results`) |

**Query parameters:**
- `relationship_type` (repeatable, optional) — filter by type (e.g. `CALLS_FUNCTION`)
- `min_confidence` (optional) — `EXACT`, `HIGH`, `MEDIUM`, or `LOW`
- `max_results` (int, default `1000`, range `1–10000`)

**Response (200):**
```json
{
  "system_id": "NC1",
  "client": "800",
  "source_object_type": "PROG",
  "source_object_name": "ZREPORT_DEMO",
  "total_relations": 5,
  "returned_relations": 5,
  "truncated": false,
  "message": null,
  "relationships": [
    {
      "relation_id": 1,
      "system_id": "NC1",
      "client": "800",
      "source_object_type": "PROG",
      "source_object_name": "ZREPORT_DEMO",
      "target_object_type": "FUNC",
      "target_object_name": "ZFI_GET_VENDOR",
      "relationship_type": "CALLS_FUNCTION",
      "confidence": "EXACT",
      "evidence_source": "ABAP_PARSER",
      "source_component": "PROGRAM__ZREPORT_DEMO",
      "line_no": 45,
      "dynamic": false,
      "detail": null
    }
  ]
}
```

**Error responses:** `404` (object not found), `422` (unsupported type, invalid params), `503` (SAP unavailable)

---

### G. Dependency Traversal

**Purpose:** Bounded forward BFS traversal of SAP dependency graph.

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/v1/repository/dependencies/traverse` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Analyst` |
| **Synchronous** | Yes |
| **Pagination** | No |

**Request body:**
```json
{
  "root_object": {"sap_object_type": "PROG", "object_name": "ZREPORT_DEMO"},
  "max_depth": 2,
  "max_nodes": 200,
  "max_edges": 500
}
```

Hard limits: `max_depth <= 5`, `max_nodes <= 5000`, `max_edges <= 20000`.

**Response (200):** Graph with `nodes`, `edges`, `cycles_detected`, `truncated`, `statistics`.

**Error responses:** `422` (invalid params), `503` (SAP unavailable)

---

### H. Reverse Dependency Lookup

**Purpose:** Find SAP objects that depend on the specified target object (bounded scope required).

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/v1/repository/dependencies/reverse` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Analyst` |
| **Synchronous** | Yes |
| **Pagination** | No |

**Request body:**
```json
{
  "target_object": {"sap_object_type": "FUNC", "object_name": "ZFI_GET_VENDOR"},
  "scope": {"packages": ["ZFINANCE"], "include_subpackages": true},
  "max_candidates": 100
}
```

A scope (`packages`, `name_patterns`, or `all_custom_objects=true`) is required.

**Response (200):** `dependents` list with `sap_object_type`, `object_name`, `relationship_types`, `confidence`, `dynamic`.

**Error responses:** `422` (missing scope, invalid params), `503` (SAP unavailable)

---

### I. Dependency Impact Analysis

**Purpose:** Deterministic technical impact analysis (no AI/LLM).

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/v1/repository/dependencies/impact` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Analyst` |
| **Synchronous** | Yes |
| **Pagination** | No |

**Request body:**
```json
{
  "object": {"sap_object_type": "TABL", "object_name": "ZFIN_POSITIONS"},
  "scope": {"packages": ["ZFINANCE"], "include_subpackages": true}
}
```

**Response (200):** `direct_dependent_count`, `total_impacted_objects`, `relationship_type_counts`, `confidence_counts`, `dynamic_reference_count`, `impacted_objects`.

**Error responses:** `422` (missing scope), `503` (SAP unavailable)

---

### J. Extraction Job Creation

**Purpose:** Create an asynchronous bulk extraction job. Returns immediately with job ID.

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/v1/repository/jobs` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Analyst` |
| **Synchronous** | No — asynchronous (HTTP 202) |
| **Idempotency** | Supply `Idempotency-Key` header to prevent duplicate jobs |

**Request body:**
```json
{
  "extraction": {
    "objects": [
      {"object_type": "PROG", "object_name": "ZREPORT_DEMO"}
    ],
    "include_metadata": true,
    "include_source": true,
    "source_max_lines": null,
    "continue_on_error": true
  },
  "export_format": "zip"
}
```

`source_max_lines: null` means complete (unlimited) source retrieval per object.
`export_format`: `zip`, `json`, or `none`.

**Response (202):**
```json
{
  "job_id": "uuid",
  "status": "QUEUED",
  "stage": null,
  "created_at": "2026-10-11T18:00:00Z",
  "status_url": "https://ncoreiq.cfapps.us10.hana.ondemand.com/api/v1/repository/jobs/uuid"
}
```

**Error responses:** `409` (idempotency key conflict), `422` (invalid request)

---

### K. Extraction Job Status

**Purpose:** Poll job status and progress.

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/v1/repository/jobs/{job_id}` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Analyst` |
| **Synchronous** | Yes |

**Terminal statuses:** `SUCCEEDED`, `FAILED`, `CANCELLED`

**Response (200):** `job_id`, `status`, `stage`, `progress` (percent_complete, processed_objects, etc.), `artifact` summary if available.

**Error responses:** `404` (job not found)

---

### L. Extraction Job Result Summary

**Purpose:** Retrieve the extraction result summary for a completed job.

> **Important:** This endpoint returns a **summary only** — object counts,
> warnings, and job metadata. It does **not** return the complete extracted
> repository object payload (source, metadata, dependencies).
>
> Complete extracted evidence is available via the **artifact endpoint**
> (`GET /api/v1/repository/jobs/{job_id}/artifact`).

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/v1/repository/jobs/{job_id}/result` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Analyst` |
| **Synchronous** | Yes |

**Response (200):**
```json
{
  "job_id": "uuid",
  "status": "SUCCEEDED",
  "stage": "COMPLETED",
  "result_status": "COMPLETED",
  "completed_at": "2026-10-11T18:05:00Z",
  "progress": {
    "total_objects": 5,
    "processed_objects": 5,
    "successful_objects": 5,
    "partial_objects": 0,
    "failed_objects": 0,
    "skipped_objects": 0,
    "truncated_objects": 0,
    "percent_complete": 100.0,
    "current_object_type": null,
    "current_object_name": null,
    "current_stage": "COMPLETED",
    "message": null
  },
  "error": null,
  "artifact": {
    "artifact_id": "ncoreiq_uuid",
    "format": "zip",
    "filename": "ncoreiq_uuid.zip",
    "content_type": "application/zip",
    "size_bytes": 204800,
    "sha256": "a3f1c2d4...",
    "created_at": "2026-10-11T18:05:01Z",
    "expires_at": "2026-10-18T18:05:01Z"
  },
  "summary": {
    "extraction_id": "uuid",
    "status": "COMPLETED",
    "selection": "explicit",
    "requested_object_count": 5,
    "processed_object_count": 5,
    "successful_object_count": 5,
    "partial_object_count": 0,
    "failed_object_count": 0,
    "skipped_object_count": 0,
    "truncated_object_count": 0,
    "duration_ms": 1234.5,
    "metadata_enabled": true,
    "source_enabled": true,
    "warnings": []
  }
}
```

**Top-level fields:**

| Field | Type | Description |
|---|---|---|
| `job_id` | string | Job identifier |
| `status` | string | Terminal job status: `SUCCEEDED`, `FAILED`, or `CANCELLED` |
| `stage` | string | Final execution stage (typically `COMPLETED`) |
| `result_status` | string | Phase-5 extraction outcome: `COMPLETED`, `COMPLETED_WITH_ERRORS`, or `FAILED` |
| `completed_at` | ISO-8601 | UTC timestamp when the job reached a terminal state |
| `progress` | object | Final progress snapshot (counts, percent) |
| `error` | object \| null | Infrastructure-level error if the job failed; otherwise null |
| `artifact` | object \| null | Artifact metadata if an export file was produced; otherwise null |
| `summary` | object \| null | Phase-5 extraction statistics; null if job was cancelled before extraction began |

**`summary` fields:**

| Field | Type | Description |
|---|---|---|
| `extraction_id` | string | Unique Phase-5 extraction run ID |
| `status` | string | Phase-5 outcome: `COMPLETED`, `COMPLETED_WITH_ERRORS`, `FAILED` |
| `selection` | string | Selection mode (`explicit`, `package`, etc.) |
| `requested_object_count` | int | Objects submitted for extraction |
| `processed_object_count` | int | Objects actually processed |
| `successful_object_count` | int | Objects extracted successfully |
| `partial_object_count` | int | Objects extracted with partial data |
| `failed_object_count` | int | Objects that failed extraction |
| `skipped_object_count` | int | Objects skipped (e.g. unsupported type) |
| `truncated_object_count` | int | Objects whose source was truncated at `source_max_lines` |
| `duration_ms` | float | Phase-5 extraction duration in milliseconds |
| `metadata_enabled` | bool | Whether metadata retrieval was requested |
| `source_enabled` | bool | Whether source retrieval was requested |
| `warnings` | string[] | Non-fatal warnings emitted during extraction |

**`result_status` vs `status`:**
- `status` = infrastructure job lifecycle (`SUCCEEDED` / `FAILED` / `CANCELLED`)
- `result_status` = Phase-5 extraction outcome (`COMPLETED` / `COMPLETED_WITH_ERRORS` / `FAILED`)
- A job with `status=SUCCEEDED` may have `result_status=COMPLETED_WITH_ERRORS` when some objects
  failed but the infrastructure job itself completed normally.

**Error responses:**

| Code | Meaning |
|---|---|
| `404` | Job not found |
| `409` | Job has not yet reached a terminal state (result not available) |

---

### M. Extraction Artifact Download

**Purpose:** Download the ZIP or JSON export artifact from a completed job.

| | |
|---|---|
| **Method** | `GET` |
| **Path** | `/api/v1/repository/jobs/{job_id}/artifact` |
| **Auth required** | Yes (XSUAA mode) |
| **Minimum scope** | `NCoreIQ.Analyst` |
| **Response content type** | `application/zip` or `application/json` |

Response headers include `Content-Disposition` (filename), `X-Job-Id`, and `X-Artifact-SHA256`.

**Important:** Source within artifacts is complete (not truncated) unless `source_max_lines` was explicitly set in the job request.

**Error responses:** `404` (job not found), `409` (artifact not available), `410` (artifact expired)

---

### N. Additional: Synchronous Bulk Extract

**Purpose:** Synchronous bulk extraction (≤250 objects by default).

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/v1/repository/extract` |
| **Minimum scope** | `NCoreIQ.Analyst` |
| **Synchronous** | Yes |

### O. Additional: Synchronous Bulk Export

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/v1/repository/export` |
| **Minimum scope** | `NCoreIQ.Analyst` |
| **Response** | `application/json` or `application/zip` |

### P. Additional: Flat Source ZIP Export (Phase 8C)

| | |
|---|---|
| **Method** | `POST` |
| **Path** | `/api/v1/repository/source-export` |
| **Minimum scope** | `NCoreIQ.Analyst` |
| **Response** | `application/zip` |

---

## 8. Evidence Provenance Fields

N-CoreShift will create immutable evidence snapshots. The following provenance data is available in N-CoreIQ responses:

### Available in Source Response (`RepositoryObjectSource`)

| Field | Source | Notes |
|---|---|---|
| `system_id` | SAP ECC | System identifier (e.g. NC1) |
| `client` | SAP ECC | SAP client (e.g. 800) |
| `sap_object_type` | Request | SAP object type (PROG, CLAS, etc.) |
| `object_name` | Request | SAP object name |
| `returned_lines` | Computed | Total source lines returned |
| `max_lines` | Request | `null` = complete; integer = bounded |
| `truncated` | SAP/N-CoreIQ | `true` if result was capped |
| `sha256` | **Computed by N-CoreIQ** | SHA-256 of all source lines joined by newlines |
| `component_type` | SAP ECC | Per-component type label |
| `component_name` | SAP ECC | Per-component name |
| `line_no` | SAP ECC | 1-based line number within component |
| `source_line` | SAP ECC | Raw source text (Unicode preserved) |

### Available in Metadata Response (`RepositoryObjectMetadata`)

| Field | Source | Notes |
|---|---|---|
| `system_id` | SAP ECC | System identifier |
| `client` | SAP ECC | SAP client |
| `sap_object_type` | SAP ECC | Object type |
| `object_name` | SAP ECC | Object name |
| `package` | SAP ECC | Development package |
| `author` | SAP ECC | Original author (SAP user) |
| `created_date` | SAP ECC | YYYYMMDD |
| `changed_by` | SAP ECC | Last-changed user |
| `changed_date` | SAP ECC | YYYYMMDD |
| `master_language` | SAP ECC | Language key |
| `active_status` | SAP ECC | Active/inactive flag |
| `pgmid` | SAP ECC | SAP program ID |

### Source Hash (`sha256`)

The `sha256` field on `RepositoryObjectSource` is computed by N-CoreIQ as:

```
SHA-256( all_component_lines.join("\n").encode("utf-8") )
```

- Lines are joined across all components in component order, within each component in `line_no` order.
- The digest is deterministic for identical source regardless of when/how it was retrieved.
- N-CoreShift SHOULD store the `sha256` value alongside each captured evidence snapshot to detect
  subsequent changes to the SAP object.
- If N-CoreShift calculates its own snapshot hash after import, it should use the same algorithm
  for reproducibility.

### Retrieval Timestamp

N-CoreIQ does not embed a retrieval timestamp in source responses. N-CoreShift SHOULD record the
time of the API call alongside the captured evidence. This timestamp is then owned by N-CoreShift
as part of its immutable evidence record.

---

## 9. Pagination

### Source Retrieval (SourceLinesSet)

SAP Gateway may split large source responses across multiple pages using the OData V2 `__next`
continuation link. N-CoreIQ follows all continuation links automatically and returns the merged
complete result in a single HTTP response to the consumer.

**Consumer-visible behavior:**
- A single `GET .../source` call always returns all lines (when `max_lines` is omitted).
- The `returned_lines` count reflects the total from all merged pages.
- `truncated: false` on an unlimited request confirms complete retrieval.

**Supported continuation mechanisms inspected and followed:**
- `d.__next` (OData V2 standard)
- `@odata.nextLink`
- `nextLink`

**When `max_lines` is supplied:**
N-CoreIQ stops fetching additional pages once the requested limit has been collected.

### Other Entity Sets

PackagesSet, RepositoryObjectsSet, DependencyRelationsSet, ObjectMetadataSet are issued
as single-request operations. SAP Gateway is responsible for returning the complete result
within the configured filter parameters. Callers control result size via `max_results`, `max_depth`,
and similar parameters.

---

## 10. N-CoreShift Provider Mapping

This section documents the intended adapter interface for N-CoreShift's
`NCoreIQSourceProvider` implementation. This is documentation only.
**Do NOT implement NCoreIQSourceProvider inside N-CoreIQ.**

### Provider Hierarchy

```
CodeSourceProvider (abstract)
    |
    +-- FileUploadSourceProvider
    +-- AbapGitSourceProvider
    +-- NCoreIQSourceProvider    <-- N-CoreShift implements this
```

### Method Mapping

| NCoreIQSourceProvider Method | N-CoreIQ Operation | HTTP Method / Path |
|---|---|---|
| `testConnection()` | SAP system ping | `GET /api/v1/sap/ping` |
| `listPackages(root, depth)` | Package hierarchy | `GET /api/v1/repository/packages/{package}` |
| `previewObjects(packages, patterns, types)` | Repository search | `POST /api/v1/repository/search` |
| `getMetadata(type, name)` | Object metadata | `GET /api/v1/repository/objects/{type}/{name}/metadata` |
| `getSource(type, name)` | Complete source | `GET /api/v1/repository/objects/{type}/{name}/source` |
| `getSourceBounded(type, name, maxLines)` | Bounded source | `GET /api/v1/repository/objects/{type}/{name}/source?max_lines=N` |
| `getDependencies(type, name)` | Direct dependencies | `GET /api/v1/repository/objects/{type}/{name}/dependencies` |
| `traverseDependencies(type, name, depth)` | Forward traversal | `POST /api/v1/repository/dependencies/traverse` |
| `findDependents(type, name, scope)` | Reverse lookup | `POST /api/v1/repository/dependencies/reverse` |
| `analyzeImpact(type, name, scope)` | Impact analysis | `POST /api/v1/repository/dependencies/impact` |
| `startPackageImport(packages, options)` | Create extraction job | `POST /api/v1/repository/jobs` |
| `getImportStatus(jobId)` | Job status poll | `GET /api/v1/repository/jobs/{job_id}` |
| `getImportResult(jobId)` | Job result summary | `GET /api/v1/repository/jobs/{job_id}/result` |
| `downloadArtifact(jobId)` | Artifact download | `GET /api/v1/repository/jobs/{job_id}/artifact` |

### Recommended Integration Pattern

```
1. NCoreIQSourceProvider.testConnection()
   -> verify SAP reachability before starting import

2. NCoreIQSourceProvider.listPackages(root, include_subpackages=true)
   -> discover package hierarchy for user selection

3. NCoreIQSourceProvider.previewObjects(packages=[...])
   -> show user what will be imported (object count, types)

4. NCoreIQSourceProvider.startPackageImport(packages=[...], export_format="zip")
   -> create async job; store returned job_id

5. Poll NCoreIQSourceProvider.getImportStatus(job_id)
   -> until status in {SUCCEEDED, FAILED, CANCELLED}

6. NCoreIQSourceProvider.getImportResult(job_id)
   -> check counts, warnings

7. NCoreIQSourceProvider.downloadArtifact(job_id)
   -> retrieve ZIP; extract and process objects

For individual object detail:
   getMetadata(type, name) -> provenance + lifecycle data
   getSource(type, name)   -> complete source + sha256 for evidence
   getDependencies(type, name) -> direct call graph evidence
```

### Authentication in NCoreIQSourceProvider

```python
# Token acquisition (client_credentials grant)
token = oauth_client.get_token(
    token_url=xsuaa_url + "/oauth/token",
    client_id=ncoreshift_client_id,
    client_secret=ncoreshift_client_secret,
    grant_type="client_credentials",
    scope="ncoreiq.NCoreIQ.Analyst"
)
headers = {"Authorization": f"Bearer {token.access_token}"}
```

---

## 11. Service-to-Service Authentication Readiness

```
SERVICE_TO_SERVICE_AUTH: DEFERRED UNTIL N-CORESHIFT APPLICATION ID IS KNOWN
```

### Current Status

N-CoreIQ has a fully-wired XSUAA authentication middleware (`app/auth/middleware.py`) that:
- Validates RS256-signed JWTs against the XSUAA JWKS endpoint
- Enforces issuer, audience, and expiry
- Extracts scopes and maps them to `AuthenticatedPrincipal`
- Handles the client credentials token format (service-to-service)

The `xs-security.json` does **not** yet contain an explicit `authorities` or `foreign-scope-references`
entry for N-CoreShift. This is required for the XSUAA trust relationship.

### What Is Needed Before Service-to-Service Works

The following must be configured in BTP after the N-CoreShift XSUAA application is registered:

1. **N-CoreShift XSUAA application (`xsappname`)** must be known.
2. **Role collection assignment** — the N-CoreShift technical user (service instance) must be assigned
   the `NCoreIQ Analyst` role collection in the BTP subaccount.
3. **JWT audience** — N-CoreIQ derives accepted audiences dynamically from its own XSUAA service
   binding (VCAP_SERVICES) at runtime. No manual audience configuration is required in the normal
   production deployment path.
4. **`AUTH__AUDIENCE`** — do **not** set this to a tenant-qualified value (`!t...` suffix).
   N-CoreIQ automatically accepts tokens whose `aud` array contains the XSUAA scope-namespace
   audience (`<bound-xsappname>.NCoreIQ`), which XSUAA emits in any client_credentials token
   carrying `NCoreIQ.*` scopes. `AUTH__AUDIENCE` is an advanced override intended for non-standard
   environments only; it must never be populated with a manually constructed `!t` suffix.

### What Must NOT Be Done

- Do NOT hard-code client credentials in N-CoreIQ source
- Do NOT disable issuer or audience validation
- Do NOT expose authenticated repository APIs publicly
- Do NOT use technical SAP credentials in N-CoreShift
- Do NOT alter the SAP destination `NC1_ODATA`

### Configuration Required (when N-CoreShift app ID is known)

```
# On N-CoreIQ BTP application:
AUTH__MODE=xsuaa
# AUTH__AUDIENCE — do NOT set in normal production deployments.
# N-CoreIQ derives the accepted audience dynamically from its bound XSUAA identity.
# XSUAA emits <bound-xsappname>.NCoreIQ as an audience entry in client_credentials
# tokens that carry NCoreIQ.* scopes. This derivation is automatic, portable across
# tenants, and requires no operator intervention.
#
# ONLY set AUTH__AUDIENCE if advised by a specific advanced troubleshooting procedure.
# NEVER set it to a manually constructed !t... tenant-qualified value.

# In BTP subaccount:
# Assign "NCoreIQ Analyst" role collection to the N-CoreShift service instance
```

---

## 12. What N-CoreIQ Does NOT Own

The following functionality will NOT be implemented in N-CoreIQ:

| Functionality | Owner |
|---|---|
| Functional intent interpretation | N-CoreShift |
| Business requirement mapping | N-CoreShift |
| Fit-to-Standard analysis | N-CoreShift |
| Clean Core decisioning (4R + Review) | N-CoreShift |
| RETIRE / REPLACE / REFACTOR / REPLATFORM classification | N-CoreShift |
| Target SAP edition / release reasoning | N-CoreShift |
| Modernization planning | N-CoreShift |
| Implementation draft generation | N-CoreShift |
| Consultant / architect approval workflows | N-CoreShift |
| Immutable evidence snapshot persistence | N-CoreShift (Phase 11 may add storage to N-CoreIQ) |
| Evidence bundle API (`/api/v1/evidence/bundles`) | Not yet — deferred to Phase 11 |
| Repository persistence model | Phase 11 |
| Clean Core scoring | Phase 11 |
| S/4 transformation scoring | Phase 11 |
| Knowledge graph / vector embeddings | Phase 11 |
| AI Copilot | Phase 11 |

The following paths will never exist in N-CoreIQ:

```
/ncoreshift/*
/modernization/*
/fit-to-standard/*
/4r/*
/clean-core-decision/*
```

---

*End of N-CoreIQ External Consumer Contract v1*
