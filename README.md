# N-CoreIQ — SAP Repository Intelligence Layer

Version 0.3.0 — BTP-ready, read-only SAP ECC custom repository discovery
platform.

**Current scope: Phase 1 (Application Foundation) + Phase 2 (SAP ECC
Connectivity via SAP Gateway OData V2) + Phase 3 (SAP Repository Discovery via
RepositoryObjectsSet OData V2).**
Source extraction, export, dependency analysis, Clean Core / S/4 analysis, and
further MCP tooling are later phases and are intentionally not implemented yet.

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
- Canonical repository object model with SAP-native and friendly type names
- Package normalization (trim, uppercase, deduplicate)
- Name-pattern normalization with injection-prevention validation
- Object-type allow-list (12 Phase 3 supported types)
- `all_custom_objects` mode: automatic Z\*/Y\* namespace scan
- Multi-value fan-out with controlled call budget (max 50 OData calls)
- Result deduplication with match-reason merging (PACKAGE / NAME_PATTERN / BOTH)
- Deterministic result sorting (type → name → package)
- Result limiting with truncation signalling
- Controlled safety rule: unfiltered full TADIR scans are blocked
- ZIP export service with manifest support (placeholder)
- MCP tool contract boundary (placeholder)
- Docker and SAP BTP Cloud Foundry scaffolding
- Initial XSUAA role model
- Comprehensive unit tests for health/config/SAP connectivity/OData/Phase 3
  behavior (253 tests, fully offline)

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

| Attribute        | Value                                        |
|------------------|----------------------------------------------|
| SAP System ID    | NC1                                          |
| SAP Client       | 800                                          |
| SAP BASIS        | 740 SP09                                     |
| SAP_GWFND        | 740 SP10                                     |
| Gateway service  | `ZNCOREIQ_REPO_SRV`                          |
| Entity sets      | `SystemInfoSet`, `PackagesSet`, `RepositoryObjectsSet` |
| Protocol         | OData V2 over HTTPS                          |
| HTTP client      | `httpx` (async)                              |
| Authentication   | HTTP Basic (development); BTP Destination (future) |

### OData ping call

```
GET /sap/opu/odata/sap/ZNCOREIQ_REPO_SRV/SystemInfoSet
    ?sap-client=800
    &$format=json
Accept: application/json
Authorization: Basic <user:password>
```

Example SAP response:

```json
{
  "d": {
    "results": [
      {
        "Status": "OK",
        "Client": "800",
        "SapRelease": "740",
        "Timestamp": "20261008164359",
        "Message": "N-CoreIQ OData connectivity successful.",
        "SystemId": "NC1"
      }
    ]
  }
}
```

Normalized N-CoreIQ response:

```json
{
  "status": "connected",
  "connection_mode": "odata",
  "system_id": "NC1",
  "client": "800",
  "sap_release": "740",
  "timestamp": "20261008164359",
  "ping_method": "SystemInfoSet",
  "message": "N-CoreIQ OData connectivity successful."
}
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

### OData discovery request

```
GET /sap/opu/odata/sap/ZNCOREIQ_REPO_SRV/RepositoryObjectsSet
    ?$filter=RootPackage eq 'ZNCOREIQ'
             and IncludeSubpackages eq true
             and RequestedObjectType eq 'PROG'
             and MaxResults eq 100
    &$format=json
    &sap-client=800
```

Supported `$filter` properties:

| OData property          | Type    | Description                                |
|-------------------------|---------|--------------------------------------------|
| `RootPackage`           | String  | SAP package name to search in              |
| `IncludeSubpackages`    | Boolean | Traverse sub-package hierarchy             |
| `NamePattern`           | String  | SAP wildcard pattern (e.g. `Z*`, `ZFI*`)  |
| `RequestedObjectType`   | String  | SAP object type code (e.g. `PROG`, `CLAS`) |
| `MaxResults`            | Integer | Maximum rows to return per OData call      |

`RootPackage` and `NamePattern` are mutually exclusive per OData call.
Multi-value inputs are resolved through controlled fan-out (see below).

### Filter semantics

Object type acts as a filter.  Package and name-pattern criteria are the
discovery reasons:

```
OBJECT_TYPE  AND  ( PACKAGE_MATCH  OR  NAME_PATTERN_MATCH )
```

This supports the two primary discovery modes:

- **Package mode:** return all qualifying objects in the requested package(s).
- **Pattern mode:** return all qualifying objects matching Z\*/Y\*/custom patterns.
- **Combined mode:** return the union and record `PACKAGE`, `NAME_PATTERN`, or
  `BOTH` as the `match_reason` for each object.

### Safety rule

A request with no meaningful selector is **rejected with HTTP 422**:

```json
{ "packages": [], "name_patterns": [], "all_custom_objects": false }
```

`all_custom_objects: true` must be set explicitly to perform a broad Z/Y
namespace scan.  The application never performs an unfiltered full TADIR scan.

### Multi-value fan-out

The public `POST /api/v1/repository/search` API accepts multiple packages,
multiple name patterns, and multiple object types simultaneously.  Because the
current `RepositoryObjectsSet` OData contract supports one value of each per
call, the `RepositoryDiscoveryService` implements a controlled fan-out:

- Each `(package OR pattern) × object_type` combination generates one OData
  call.
- Maximum fan-out: **50 OData calls** per search request.  Requests that would
  exceed this limit are rejected with HTTP 422 before any SAP call is made.
- Results are aggregated, deduplicated, and sorted in the service layer before
  being returned to the caller.

Example: `packages=["ZPKG1","ZPKG2"]`, `object_types=["PROG","CLAS"]`
generates 4 OData calls (`ZPKG1×PROG`, `ZPKG1×CLAS`, `ZPKG2×PROG`,
`ZPKG2×CLAS`).

### Deduplication and match-reason merging

When the same SAP object appears in multiple OData responses (e.g. it matches
both a package search and a name-pattern search), the service deduplicates on
`(sap_object_type, object_name)` and merges the `match_reason`:

| Call sources                        | Resulting `match_reason` |
|-------------------------------------|--------------------------|
| Package call only                   | `PACKAGE`                |
| Name-pattern call only              | `NAME_PATTERN`           |
| Both package and name-pattern calls | `BOTH`                   |
| SAP already returned `BOTH`         | `BOTH` (preserved)       |

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

If `object_types` is omitted or empty, all 12 Phase 3 types are included in the
`criteria` echo; no type filter clause is sent to ECC so ECC returns all types.
Requesting an unsupported type returns HTTP 422.

### Package behavior

- Package names are trimmed, uppercased, and deduplicated before being sent
  to ECC.  `["zfinance", "ZFINANCE", " ZFI_UTIL "]` → `["ZFINANCE", "ZFI_UTIL"]`.
- `include_subpackages: true` sends `IncludeSubpackages eq true` in the OData
  filter; ECC's `Z_MCP_REPO_DISCOVER` backend performs the hierarchy traversal.
- Package depth: root = 0, child = 1, grandchild = 2, etc.
- Custom packages are not restricted to the Z/Y namespace — an explicitly
  supplied package name is accepted regardless of its initial letter.

### all_custom_objects behavior

When `all_custom_objects: true` the service injects name patterns `Z*` and
`Y*` automatically.  This is an explicit opt-in for customer namespace objects —
it does **not** scan all SAP repository objects.

If ECC later exposes a native `AllCustomObjects` filter property, the service
layer can move the logic server-side without changing the public API.

### Result limits

| Setting              | Value  |
|----------------------|--------|
| Default `max_results`| 1 000  |
| Hard application limit | 10 000 |
| Minimum `max_results`| 1      |

Requests above the hard limit are rejected with HTTP 422.  When ECC returns
more matches than requested, `truncated: true` is set in the response.

### Truncation

`truncated: true` is set in the response when:

- Any underlying OData call reports `Truncated = true`, **or**
- The aggregated unique-object count had to be capped to `max_results`.

`total_matches` is `null` for multi-call requests where results may overlap
and a precise total cannot be determined safely.

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

| Field               | Type         | Default | Description                                       |
|---------------------|--------------|---------|---------------------------------------------------|
| `packages`          | list[str]    | `[]`    | SAP package names (trimmed, uppercased, deduplicated) |
| `include_subpackages` | bool       | `false` | Traverse sub-package hierarchy in ECC                 |
| `name_patterns`     | list[str]    | `[]`    | SAP wildcard patterns (e.g. `ZFI*`)                   |
| `object_types`      | list[str]    | `[]`    | Phase 3 SAP type codes; empty = all 12 types           |
| `max_results`       | int          | `1000`  | Cap on returned objects (1–10 000)                    |
| `all_custom_objects`| bool         | `false` | Inject `Z*`/`Y*` patterns automatically               |

### Repository search examples

**Package search:**
```json
{
  "packages": ["ZNCOREIQ"],
  "include_subpackages": true,
  "max_results": 1000
}
```

**Name-pattern search:**
```json
{
  "packages": [],
  "name_patterns": ["ZFI*"],
  "object_types": ["PROG"],
  "max_results": 500
}
```

**Multiple-type search:**
```json
{
  "packages": ["ZFINANCE"],
  "include_subpackages": true,
  "object_types": ["PROG", "CLAS", "TABL"],
  "max_results": 1000
}
```

**Broad custom-object scan:**
```json
{
  "all_custom_objects": true,
  "object_types": ["PROG", "CLAS"],
  "max_results": 1000
}
```

### Repository search response

```json
{
  "system_id": "NC1",
  "client": "800",
  "criteria": {
    "packages": ["ZNCOREIQ"],
    "include_subpackages": true,
    "name_patterns": [],
    "object_types": ["PROG", "CLAS"],
    "all_custom_objects": false,
    "max_results": 500
  },
  "statistics": {
    "total_matches": 25,
    "returned": 25
  },
  "truncated": false,
  "message": "Discovery completed successfully.",
  "objects": [
    {
      "system_id": "NC1",
      "client": "800",
      "sap_object_type": "PROG",
      "friendly_type": "PROGRAM",
      "object_name": "ZNCOREIQ_MAIN",
      "package": "ZNCOREIQ",
      "description": "N-CoreIQ main program",
      "author": "DEVELOPER",
      "match_reason": "PACKAGE",
      "warning": null
    }
  ]
}
```

| Response field               | Description                                              |
|------------------------------|----------------------------------------------------------|
| `system_id`                  | SAP System ID (from ECC response)                        |
| `client`                     | SAP Client (from ECC response)                           |
| `criteria`                   | Echo of effective search criteria after normalization    |
| `statistics.total_matches`   | Total known matches; `null` if undetermined (multi-call) |
| `statistics.returned`        | Number of unique objects actually returned               |
| `truncated`                  | `true` if any OData call was truncated or cap applied    |
| `message`                    | Human-readable summary                                   |
| `objects`                    | List of normalized `RepositoryObject` records            |

### HTTP status codes

| Code  | Condition                                              |
|-------|--------------------------------------------------------|
| `200` | Successful search (including zero results)             |
| `422` | Invalid request (blank search, unsupported type, etc.) |
| `422` | Search fan-out exceeds 50 OData call budget            |
| `401` / `403` | SAP authentication / authorization failure   |
| `501` | SAP connection mode not yet implemented (BTP)          |
| `503` | SAP Gateway unavailable, TLS failure, timeout          |
| `502` / `503` | Malformed OData response from SAP Gateway    |

## Package resolution endpoint (Phase 3)

```
GET /api/v1/repository/packages/{package}
    ?include_subpackages=true
    &max_depth=5
```

Uses `Z_MCP_REPO_PACKAGES` (RFC) to resolve SAP package hierarchy.

## API endpoints

| Method | Path                              | Description                            |
|--------|-----------------------------------|----------------------------------------|
| GET    | `/api/v1/health`                  | Application liveness (no SAP required) |
| GET    | `/api/v1/sap/ping`                | SAP ECC connectivity check             |
| GET    | `/api/v1/repository/packages/{p}` | SAP package hierarchy resolution       |
| POST   | `/api/v1/repository/search`       | Repository object discovery            |
| GET    | `/docs`                           | Swagger / OpenAPI interactive docs     |
| GET    | `/redoc`                          | ReDoc documentation                    |

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

All 253 tests run fully offline (no SAP connection required).

## Known limitations (Phase 3)

- `RepositoryObjectsSet` supports **one** `RootPackage` or `NamePattern` and
  **one** `RequestedObjectType` per OData call; multi-value inputs fan out to
  multiple calls.
- `total_matches` is `null` for multi-call searches because per-call totals
  may count the same object more than once.
- `all_custom_objects` is implemented at the application layer as Z\*/Y\* pattern
  injection.  An ECC-native `AllCustomObjects` filter does not yet exist.
- Source extraction, dependency analysis, and export are Phase 4+.
