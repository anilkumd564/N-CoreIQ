# N-CoreIQ — SAP Repository Intelligence Layer

Version 0.4.0 — BTP-ready, read-only SAP ECC custom repository discovery and
object-level metadata/source extraction platform.

**Current scope: Phase 1 (Application Foundation) + Phase 2 (SAP ECC
Connectivity via SAP Gateway OData V2) + Phase 3 (SAP Repository Discovery via
RepositoryObjectsSet OData V2) + Phase 4 (Object Metadata & Source Extraction
via ObjectMetadataSet / SourceLinesSet OData V2).**
Export, dependency analysis, Clean Core / S/4 analysis, and further MCP tooling
are later phases and are intentionally not implemented yet.

## Current capabilities

- FastAPI service with OpenAPI documentation
- BTP/local configuration abstraction
- SAP connection provider abstraction with **OData V2 (preferred)** and local
  RFC implementations
- **`ODataSAPConnectionProvider`** — SAP Gateway OData V2 over HTTPS using
  `httpx`; connects to the `ZNCOREIQ_REPO_SRV` Gateway service
- Defensive PyRFC / SAP NetWeaver RFC SDK adapter loading (no import-time
  crash); RFC is now optional/legacy transport
- Explicit RFC allow-list (`Z_MCP_REPO_PING`, `RFC_SYSTEM_INFO`,
  `Z_MCP_REPO_PACKAGES`, `Z_MCP_REPO_DISCOVER`)
- Real `GET /api/v1/sap/ping` connectivity check — calls `SystemInfoSet`
  (OData mode) or `Z_MCP_REPO_PING` (RFC mode)
- **Phase 3:** `POST /api/v1/repository/search` — custom repository object
  discovery via `RepositoryObjectsSet` (OData V2) with package resolution via
  `Z_MCP_REPO_PACKAGES` (RFC)
- **Phase 4:** `GET /api/v1/repository/objects/{type}/{name}/metadata` —
  object-level technical metadata via `ObjectMetadataSet` (OData V2)
- **Phase 4:** `GET /api/v1/repository/objects/{type}/{name}/source` —
  ABAP source / structured technical definition via `SourceLinesSet` (OData V2)
- Canonical repository object model with SAP-native and friendly type names
- Package normalization (trim, uppercase, deduplicate)
- Name-pattern normalization with injection-prevention validation
- Object-type allow-list (12 Phase 3 types; FUNC additionally supported in Phase 4)
- `all_custom_objects` mode: automatic Z\*/Y\* namespace scan
- Multi-value fan-out with controlled call budget (max 50 OData calls)
- Result deduplication with match-reason merging (PACKAGE / NAME_PATTERN / BOTH)
- Deterministic result sorting (type → name → package)
- Result limiting with truncation signalling
- Controlled safety rule: unfiltered full TADIR scans are blocked
- Source line ordering preserved (strict `line_no` ascending within each component)
- Source component grouping by `(component_type, component_name)`
- `max_lines` enforcement (default 5 000, hard max 20 000)
- Truncation propagation from SAP to API response
- Unicode source preservation
- Controlled HTTP 404 for missing objects
- ZIP export service with manifest support (placeholder)
- MCP tool contract boundary (placeholder)
- Docker and SAP BTP Cloud Foundry scaffolding
- Initial XSUAA role model
- Comprehensive unit tests for health/config/SAP connectivity/OData/Phase 3/Phase 4
  behavior (363 tests, fully offline)

## Architecture principles

1. MCP and REST never contain SAP extraction logic.
2. SAP connectivity is injected through a provider interface
   (`SapConnectionProvider`).
3. Local and BTP runtime modes use the same repository services.
4. No SAP credentials are committed to source control.
5. Production on-premise access is designed for SAP BTP Destination +
   Connectivity Service + Cloud Connector.
6. The initial SAP integration is read-only.
7. Export storage is portable; a BTP-hosted instance must not depend on a
   Windows Desktop path.
8. Application health is independent of SAP connectivity
   (`/api/v1/health` never fails because ECC is offline).
9. RFC execution is restricted to an explicit allow-list; arbitrary function
   module names can never reach SAP.
10. Repository discovery never exposes generic SAP table browsing, SQL
    injection surfaces, or arbitrary RFC invocation.
11. **OData V2 over HTTPS is the preferred ECC transport.** PyRFC/RFC SDK
    is preserved as optional legacy transport.
12. Repository search (`POST /api/v1/repository/search`) never automatically
    retrieves metadata or source for discovered objects (no N+1 extraction).
13. Source text is preserved exactly as returned by SAP — indentation,
    special characters, Unicode, and ABAP comments are never reformatted.

## Phase 2 — SAP ECC OData connectivity (preferred)

```
FastAPI  GET /api/v1/sap/ping
   ↓
SAP service layer (routes.py)
   ↓
SapConnectionProvider abstraction
   ↓
ODataSAPConnectionProvider          ← preferred / default (SAP_CONNECTION_MODE=odata)
   ↓
httpx (async HTTP client)
   ↓
HTTPS
   ↓
SAP Gateway (HYDRSAPT02:44300)
   ↓
ZNCOREIQ_REPO_SRV / SystemInfoSet
   ↓
SAP ECC NC1
```

### OData service details

| Attribute        | Value                                                                          |
|------------------|--------------------------------------------------------------------------------|
| SAP System ID    | NC1                                                                            |
| SAP Client       | 800                                                                            |
| SAP BASIS        | 740 SP09                                                                       |
| SAP_GWFND        | 740 SP10                                                                       |
| Gateway service  | `ZNCOREIQ_REPO_SRV`                                                            |
| Entity sets      | `SystemInfoSet`, `PackagesSet`, `RepositoryObjectsSet`, `ObjectMetadataSet`, `SourceLinesSet` |
| Protocol         | OData V2 over HTTPS                                                            |
| HTTP client      | `httpx` (async)                                                                |
| Authentication   | HTTP Basic (development); BTP Destination (future)                             |

### OData ping call

```
GET /sap/opu/odata/sap/ZNCOREIQ_REPO_SRV/SystemInfoSet
    ?sap-client=800
    &$format=json
Accept: application/json
Authorization: Basic <user:password>
```

## Phase 2 — SAP ECC RFC connectivity (legacy/optional)

```
FastAPI  GET /api/v1/sap/ping
   ↓
SapConnectionProvider abstraction
   ↓
LocalSapConnectionProvider          ← optional (SAP_CONNECTION_MODE=local)
   ↓
PyRFC (Python RFC adapter)
   ↓
SAP NetWeaver RFC SDK
   ↓
SAP ECC Development
   ↓
Z_MCP_REPO_PING
```

`SAP_CONNECTION_MODE=btp` is recognized by the provider factory but returns a
controlled `SAPProviderNotImplementedError` (HTTP 501) rather than silently
falling back to the local provider.

## Phase 3 — SAP Repository Discovery (RepositoryObjectsSet — OData V2)

```
REST API  POST /api/v1/repository/search
   ↓
FastAPI routes.py
   ↓
RepositoryDiscoveryService.search()
   ↓
SapConnectionProvider.discover_repository_objects()
   ↓
ODataSAPConnectionProvider
   ↓
httpx (async)
   ↓
HTTPS / OData V2
   ↓
SAP Gateway
   ↓
ZNCOREIQ_REPO_SRV / RepositoryObjectsSet
   ↓
Z_MCP_REPO_DISCOVER (ECC backend)
   ↓
TADIR / SAP ECC NC1
```

Repository object discovery is implemented exclusively over OData V2 via the
`RepositoryObjectsSet` entity set.  No direct RFC call to `Z_MCP_REPO_DISCOVER`
is made from Python; the Python layer constructs controlled `$filter` expressions
and sends them to the SAP Gateway, which routes them to the ECC backend.

### Supported object types (Phase 3)

| SAP native type | Friendly name        |
|-----------------|----------------------|
| `PROG`          | `PROGRAM`            |
| `CLAS`          | `CLASS`              |
| `INTF`          | `INTERFACE`          |
| `FUGR`          | `FUNCTION_GROUP`     |
| `TABL`          | `TABLE_OR_STRUCTURE` |
| `DTEL`          | `DATA_ELEMENT`       |
| `DOMA`          | `DOMAIN`             |
| `VIEW`          | `VIEW`               |
| `SHLP`          | `SEARCH_HELP`        |
| `ENQU`          | `LOCK_OBJECT`        |
| `TRAN`          | `TRANSACTION`        |
| `MSAG`          | `MESSAGE_CLASS`      |

### Repository search request

```json
{
  "packages": ["ZNCOREIQ"],
  "include_subpackages": true,
  "name_patterns": [],
  "object_types": ["PROG", "CLAS"],
  "max_results": 500,
  "all_custom_objects": false
}
```

### Repository search response

```json
{
  "system_id": "NC1",
  "client": "800",
  "criteria": { "...": "..." },
  "statistics": { "total_matches": 25, "returned": 25 },
  "truncated": false,
  "message": "Discovery completed successfully.",
  "objects": [
    {
      "sap_object_type": "PROG",
      "friendly_type": "PROGRAM",
      "object_name": "ZNCOREIQ_MAIN",
      "package": "ZNCOREIQ",
      "description": "N-CoreIQ main program",
      "match_reason": "PACKAGE"
    }
  ]
}
```

### Safety rule

A request with no meaningful selector is **rejected with HTTP 422**.
`all_custom_objects: true` must be set explicitly to perform a broad Z/Y
namespace scan.

---

## Phase 4 — Object Metadata & Source Extraction (OData V2)

### Architecture

```
N-CoreIQ REST API
        ↓
RepositoryObjectService
        ↓
SapConnectionProvider abstraction
        ↓
ODataSAPConnectionProvider
        ↓
httpx (async) / HTTPS / OData V2
        ↓
SAP Gateway
        ↓
ZNCOREIQ_REPO_SRV
        │
        ├── ObjectMetadataSet  →  Z_MCP_REPO_GET_METADATA (ECC backend)
        │
        └── SourceLinesSet     →  Z_MCP_REPO_GET_SOURCE (ECC backend)
```

Python never calls `Z_MCP_REPO_GET_METADATA` or `Z_MCP_REPO_GET_SOURCE`
directly.  All ECC communication goes through SAP Gateway OData V2 over HTTPS.

### Phase 4 endpoints

#### GET metadata

```
GET /api/v1/repository/objects/{object_type}/{object_name}/metadata
```

Example:

```
GET /api/v1/repository/objects/PROG/ZMY_PROGRAM/metadata
```

Returns `RepositoryObjectMetadata`.

#### GET source

```
GET /api/v1/repository/objects/{object_type}/{object_name}/source
    ?max_lines=5000
```

Example:

```
GET /api/v1/repository/objects/CLAS/ZCL_SAMPLE/source?max_lines=5000
```

Returns `RepositoryObjectSource`.

The `source` endpoint retrieves ABAP source for executable objects (`PROG`,
`CLAS`, `INTF`, `FUGR`, `FUNC`) and a structured textual representation for
DDIC/technical objects (`TABL`, `DTEL`, `DOMA`, `VIEW`, `SHLP`, `ENQU`) or
definitions for transactional objects (`TRAN`, `MSAG`).  The
`component_type` field in each source component identifies the representation
type so downstream consumers can distinguish ABAP source from structured
technical definitions.

### Supported object types (Phase 4)

| SAP native type | Metadata | Source/Definition |
|-----------------|----------|-------------------|
| `PROG`          | ✓        | ✓                 |
| `CLAS`          | ✓        | ✓ (multi-component) |
| `INTF`          | ✓        | ✓                 |
| `FUGR`          | ✓        | ✓ (multi-component) |
| `FUNC`          | ✓        | ✓                 |
| `TABL`          | ✓        | ✓ (structured definition) |
| `DTEL`          | ✓        | ✓                 |
| `DOMA`          | ✓        | ✓                 |
| `VIEW`          | ✓        | ✓                 |
| `SHLP`          | ✓        | ✓                 |
| `ENQU`          | ✓        | ✓                 |
| `TRAN`          | ✓        | ✓                 |
| `MSAG`          | ✓        | ✓                 |

`FUNC` is supported in Phase 4 metadata/source retrieval.  Phase 3 discovery
(`RepositoryObjectsSet`) is not extended to discover `FUNC` directly.

### ObjectMetadataSet OData request

```
GET /sap/opu/odata/sap/ZNCOREIQ_REPO_SRV/ObjectMetadataSet
    ?$filter=ObjectType eq 'PROG' and ObjectName eq 'ZMY_PROGRAM'
    &$format=json
    &sap-client=800
```

### RepositoryObjectMetadata model

| Field              | Type        | Description                              |
|--------------------|-------------|------------------------------------------|
| `system_id`        | str \| None | SAP System ID                            |
| `client`           | str \| None | SAP Client                               |
| `pgmid`            | str \| None | Program ID (R3TR, LIMU, etc.)            |
| `sap_object_type`  | str         | SAP object type code                     |
| `object_name`      | str         | SAP object name                          |
| `package`          | str \| None | Development package                      |
| `author`           | str \| None | Creating user                            |
| `master_language`  | str \| None | Master language key                      |
| `source_system`    | str \| None | Source system                            |
| `created_date`     | str \| None | Creation date (YYYYMMDD)                 |
| `changed_by`       | str \| None | Last change user                         |
| `changed_date`     | str \| None | Last change date (YYYYMMDD)              |
| `description`      | str \| None | Short description                        |
| `active_status`    | str \| None | Active/inactive status                   |
| `object_subtype`   | str \| None | Object subtype                           |
| `program_name`     | str \| None | Associated program name                  |
| `function_group`   | str \| None | Function group (for FUNC objects)        |
| `table_class`      | str \| None | Table class (for TABL objects)           |
| `warning`          | str \| None | Non-fatal SAP warning message            |
| `found`            | bool        | Whether the object was found in ECC      |
| `message`          | str \| None | SAP response message                     |

### SourceLinesSet OData request

```
GET /sap/opu/odata/sap/ZNCOREIQ_REPO_SRV/SourceLinesSet
    ?$filter=ObjectType eq 'PROG' and ObjectName eq 'ZMY_PROGRAM' and MaxLines eq 5000
    &$format=json
    &sap-client=800
```

### RepositoryObjectSource model

```json
{
  "system_id": "NC1",
  "client": "800",
  "sap_object_type": "PROG",
  "object_name": "ZMY_PROGRAM",
  "max_lines": 5000,
  "returned_lines": 125,
  "component_count": 1,
  "found": true,
  "truncated": false,
  "message": "Source retrieved successfully.",
  "components": [
    {
      "component_type": "PROGRAM",
      "component_name": "ZMY_PROGRAM",
      "line_count": 125,
      "lines": [
        { "component_type": "PROGRAM", "component_name": "ZMY_PROGRAM", "line_no": 1, "source_line": "REPORT zmy_program." },
        { "component_type": "PROGRAM", "component_name": "ZMY_PROGRAM", "line_no": 2, "source_line": "" }
      ]
    }
  ]
}
```

### Source component grouping

Source rows from `SourceLinesSet` are grouped by `(component_type, component_name)`.
Within each component, lines are sorted strictly by `line_no` ascending.
Ordering is deterministic regardless of the order SAP returns the rows.

For multi-component objects (e.g. `CLAS`, `FUGR`), each component is a separate
entry in the `components` list, preserving source component boundaries exactly
as returned by ECC.

### max_lines parameter

| Setting        | Value  |
|----------------|--------|
| Default        | 5 000  |
| Minimum        | 1      |
| Hard maximum   | 20 000 |

Values of `0`, negative integers, or values above `20 000` are rejected with
HTTP 422.  Silent unlimited source extraction is not permitted.

### Truncation

If SAP returns `Truncated = true` for any source row, the response sets
`truncated: true`.  The caller must not assume the extracted source is complete
when `truncated` is `true`.

### Object not found

If SAP reports `Found = false` or returns no rows for a genuinely missing
object, N-CoreIQ raises a controlled `RepositoryObjectNotFoundError` and
returns HTTP 404.  An empty result set for an existing object with no source
representation is handled separately using backend message/warning semantics.

### Source integrity

Duplicate rows (same `component_type`, `component_name`, `line_no`, different
`source_line`) raise `RepositorySourceDataIntegrityError` rather than silently
overwriting source.  Source text is preserved exactly — indentation, special
ABAP characters, comments, accented descriptions, and non-English Unicode text
are not modified.

### Phase 4 HTTP status codes

| Code  | Condition                                              |
|-------|--------------------------------------------------------|
| `200` | Successful metadata or source retrieval                |
| `404` | Object not found in ECC                                |
| `422` | Unsupported object type / blank identifier / invalid max_lines |
| `503` | SAP Gateway unavailable, TLS failure, timeout          |

### No N+1 extraction during search

`POST /api/v1/repository/search` **never** automatically calls
`ObjectMetadataSet` or `SourceLinesSet` for discovered objects.  Metadata and
source retrieval occur only when explicitly requested for an identified object
via the Phase 4 endpoints.

---

## API endpoints

| Method | Path                                                    | Description                               |
|--------|---------------------------------------------------------|-------------------------------------------|
| GET    | `/api/v1/health`                                        | Application liveness (no SAP required)    |
| GET    | `/api/v1/sap/ping`                                      | SAP ECC connectivity check                |
| GET    | `/api/v1/repository/packages/{p}`                       | SAP package hierarchy resolution          |
| POST   | `/api/v1/repository/search`                             | Repository object discovery               |
| GET    | `/api/v1/repository/objects/{type}/{name}/metadata`     | Object technical metadata (Phase 4)       |
| GET    | `/api/v1/repository/objects/{type}/{name}/source`       | Object ABAP source / definition (Phase 4) |
| GET    | `/docs`                                                 | Swagger / OpenAPI interactive docs        |
| GET    | `/redoc`                                                | ReDoc documentation                       |

## Configuration

Copy `.env.example` to `.env` and populate the OData settings:

```
SAP_CONNECTION_MODE=odata

SAP_ODATA__BASE_URL=https://<gateway-host>:<port>
SAP_ODATA__SERVICE=ZNCOREIQ_REPO_SRV
SAP_ODATA__CLIENT=800
SAP_ODATA__USER=<sap_user>
SAP_ODATA__PASSWORD=<sap_password>
SAP_ODATA__TIMEOUT=30
SAP_ODATA__CA_BUNDLE=./sap_ca.pem   # optional: path to corporate CA bundle
```

TLS verification is **always enforced in production**.  The
`SAP_ODATA__SKIP_TLS_VERIFY=true` setting is a development-only escape hatch
for environments where the SAP Gateway CA certificate cannot be imported.

## Running locally

```bash
pip install -r requirements.txt
uvicorn app.main:app --reload --port 8000
```

## Running tests

```bash
py -m pytest tests/ -v
```

All 363 tests run fully offline (no SAP connection required).

## Known limitations

- `RepositoryObjectsSet` supports **one** `RootPackage` or `NamePattern` and
  **one** `RequestedObjectType` per OData call; multi-value inputs fan out to
  multiple calls.
- `total_matches` is `null` for multi-call searches because per-call totals
  may count the same object more than once.
- `all_custom_objects` is implemented at the application layer as Z\*/Y\* pattern
  injection.  An ECC-native `AllCustomObjects` filter does not yet exist.
- Phase 4 metadata/source extraction is single-object per request.  Bulk
  extraction, ZIP export, and large-repository export are Phase 5+ concerns.
- `FUNC` is supported in Phase 4 metadata/source retrieval but not in Phase 3
  repository discovery via `RepositoryObjectsSet`.
- Dates are returned in `YYYYMMDD` format as provided by ECC.  ISO 8601
  conversion is not applied in Phase 4.
