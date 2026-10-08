# N-CoreIQ — SAP Repository Intelligence Layer

Version 0.3.0 — BTP-ready, read-only SAP ECC custom repository discovery
platform.

**Current scope: Phase 1 (Application Foundation) + Phase 2 (SAP ECC
Connectivity) + Phase 3 (SAP Repository Discovery).** Source extraction,
export, dependency analysis, Clean Core / S/4 analysis, and further MCP
tooling are later phases and are intentionally not implemented yet.

## Current capabilities

- FastAPI service with OpenAPI documentation
- BTP/local configuration abstraction
- SAP connection provider abstraction with a real local RFC implementation
- Defensive PyRFC / SAP NetWeaver RFC SDK adapter loading (no import-time crash)
- Explicit RFC allow-list (`Z_MCP_REPO_PING`, `RFC_SYSTEM_INFO`,
  `Z_MCP_REPO_PACKAGES`, `Z_MCP_REPO_DISCOVER`)
- Real `GET /api/v1/sap/ping` connectivity check against SAP ECC Development
- **Phase 3:** `POST /api/v1/repository/search` — custom repository object
  discovery via `Z_MCP_REPO_PACKAGES` + `Z_MCP_REPO_DISCOVER`
- Canonical repository object model with SAP-native and friendly type names
- Package normalization (trim, uppercase, deduplicate)
- Name-pattern normalization with injection-prevention validation
- Object-type allow-list (12 Phase 3 supported types)
- Recursive subpackage resolution
- Result limiting with truncation signalling
- Controlled safety rule: unfiltered full TADIR scans are blocked
- ZIP export service with manifest support (placeholder)
- MCP tool contract boundary (placeholder)
- Docker and SAP BTP Cloud Foundry scaffolding
- Initial XSUAA role model
- Comprehensive unit tests for health/config/SAP connectivity/Phase 3 behavior

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

## Phase 3 — SAP Repository Discovery architecture

```
REST API  POST /api/v1/repository/search
   ↓
RepositoryService
   ↓
RepositoryDiscoveryService
   ↓
SapConnectionProvider
   ↓
RFC allow-list
   ↓
Z_MCP_REPO_PACKAGES / Z_MCP_REPO_DISCOVER
   ↓
SAP ECC (TADIR)
```

### Filter semantics

Object type acts as a filter.  Package and name-pattern criteria are the
discovery reasons:

```
OBJECT_TYPE  AND  ( PACKAGE_MATCH  OR  NAME_PATTERN_MATCH )
```

This supports the two primary discovery modes:

- **Package mode:** return all qualifying objects in the requested package(s).
- **Pattern mode:** return all qualifying objects matching Z*/Y*/custom patterns.
- **Combined mode:** return the union and record `PACKAGE`, `NAME_PATTERN`, or
  `BOTH` as the `match_reason` for each object.

### Safety rule

A request with no meaningful selector is **rejected with HTTP 422**:

```json
{ "packages": [], "name_patterns": [], "all_custom_objects": false }
```

`all_custom_objects: true` must be set explicitly to perform a broad Z/Y
namespace scan.  The application never performs an unfiltered full TADIR scan.

### Supported object types (Phase 3)

| SAP native type | Friendly name |
|---|---|
| `PROG` | `PROGRAM` |
| `CLAS` | `CLASS` |
| `INTF` | `INTERFACE` |
| `FUGR` | `FUNCTION_GROUP` |
| `TABL` | `TABLE_OR_STRUCTURE` |
| `DTEL` | `DATA_ELEMENT` |
| `DOMA` | `DOMAIN` |
| `VIEW` | `VIEW` |
| `SHLP` | `SEARCH_HELP` |
| `ENQU` | `LOCK_OBJECT` |
| `TRAN` | `TRANSACTION` |
| `MSAG` | `MESSAGE_CLASS` |

If `object_types` is omitted or empty, all 12 Phase 3 types are included.
Requesting an unsupported type returns HTTP 422.

### Package behavior

- Package names are trimmed, uppercased, and deduplicated before being sent
  to ECC.  `["zfinance", "ZFINANCE", " ZFI_UTIL "]` → `["ZFINANCE", "ZFI_UTIL"]`.
- `include_subpackages: true` resolves the full package hierarchy via
  `Z_MCP_REPO_PACKAGES` before discovery.
- Package depth: root = 0, child = 1, grandchild = 2, etc.
- Custom packages are not restricted to the Z/Y namespace — an explicitly
  supplied package name is accepted regardless of its initial letter.

### all_custom_objects behavior

When `all_custom_objects: true` the service applies default name patterns
`Z*` and `Y*`.  This is an explicit opt-in for customer namespace objects —
it does **not** scan all SAP repository objects.

### Result limits

| Setting | Value |
|---|---|
| Default `max_results` | 1000 |
| Hard application limit | 10 000 |
| Minimum `max_results` | 1 |

Requests above the hard limit are rejected with HTTP 422.  When ECC returns
more matches than requested, `truncated: true` is set in the response.

## Phase 2 — SAP ECC connectivity architecture

```
FastAPI
   ↓
SAP service (RepositoryService / routes)
   ↓
SAP connection provider abstraction (SapConnectionProvider)
   ↓
LocalSapConnectionProvider   ← implemented in Phase 2
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

## Local SAP prerequisites

To exercise **real** SAP ECC connectivity (not just the mocked test suite)
you need, on the machine running the app:

1. **SAP NetWeaver RFC SDK** (nwrfcsdk) — proprietary SAP binaries, licensed
   separately from SAP. Download from SAP Support Portal. These binaries
   must **never** be committed to this repository.
2. **PyRFC** — the Python RFC adapter used by this project. It links against
   the NetWeaver RFC SDK native libraries above.
   ```
   pip install pyrfc
   ```
3. Network/VPN access from the local machine to the SAP ECC Development
   application server (`SAP_LOCAL_HOST` / `SAP_LOCAL_SYSTEM_NUMBER`).
4. A valid SAP ECC user with authorization to execute `Z_MCP_REPO_PING`,
   `Z_MCP_REPO_PACKAGES`, and `Z_MCP_REPO_DISCOVER` via RFC.

The application and automated test suite run **fully without** PyRFC or the
NetWeaver RFC SDK installed. If PyRFC cannot be imported (missing package or
missing native libraries), the app starts normally and `/api/v1/sap/ping`
returns a controlled HTTP 501 instead of crashing at import time.

## Required environment variables

| Variable | Purpose | Example |
|---|---|---|
| `APP_ENV` | Application environment | `local` |
| `SAP_CONNECTION_MODE` | `local` or `btp` | `local` |
| `SAP_LOCAL__HOST` | SAP ECC application server host | `10.0.0.5` or `ecc-dev.example.com` |
| `SAP_LOCAL__SYSTEM_NUMBER` | SAP system number | `00` |
| `SAP_LOCAL__CLIENT` | SAP client | `100` |
| `SAP_LOCAL__USER` | RFC user | *(set locally, never commit)* |
| `SAP_LOCAL__PASSWORD` | RFC password | *(set locally, never commit)* |
| `SAP_LOCAL__LANGUAGE` | Logon language | `EN` |
| `SAP_DESTINATION_NAME` | BTP destination name (used only in `btp` mode) | `ECC_DEV` |
| `AUTH_MODE` | API auth mode | `disabled` |

These are nested under `SAP_LOCAL__*` using Pydantic's `env_nested_delimiter="__"`
and map to the `sap_local` settings sub-model.

## How to configure `.env`

```bash
copy .env.example .env   # Windows cmd
# cp .env.example .env   # macOS/Linux
```

Then fill in `SAP_LOCAL__HOST`, `SAP_LOCAL__CLIENT`, `SAP_LOCAL__USER`,
`SAP_LOCAL__PASSWORD` with real (non-production, non-committed) values for
your SAP ECC Development system.

`.env` is excluded via `.gitignore`. `.env.example` contains placeholders
only. **No real credentials are ever committed.**

## Run locally

```bash
python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install --upgrade pip
pip install -r requirements.txt

pytest -v

uvicorn app.main:app --reload --host 127.0.0.1 --port 8080
```

Then open:

- `http://127.0.0.1:8080/api/v1/health`
- `http://127.0.0.1:8080/api/v1/sap/ping`
- `http://127.0.0.1:8080/api/v1/repository/search` *(POST)*
- `http://127.0.0.1:8080/docs`

## How to test SAP connectivity

- **Without SAP SDK installed:** `GET /api/v1/sap/ping` returns HTTP 501.
  `POST /api/v1/repository/search` also returns HTTP 501.  Neither crashes the
  app.
- **With PyRFC + SDK installed and `.env` configured, but SAP unreachable:**
  both endpoints return HTTP 503.
- **With correct configuration and live ECC Development access:** the ping
  calls `Z_MCP_REPO_PING`; the search calls `Z_MCP_REPO_PACKAGES` (when
  `include_subpackages: true`) and `Z_MCP_REPO_DISCOVER`.
- **Automated / mocked tests:** `pytest -v` exercises the full connectivity
  and discovery flow entirely with mocks — no live ECC, no SAP SDK, no real
  credentials required.

## Live ECC validation procedure (Phase 3)

Run these controlled tests against a live ECC Development system once RFC
function modules `Z_MCP_REPO_PACKAGES` and `Z_MCP_REPO_DISCOVER` are
deployed.

**Test 1 — Package-only search:**

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/repository/search \
  -H "Content-Type: application/json" \
  -d '{"packages":["<KNOWN_Z_PACKAGE>"],"include_subpackages":false,"object_types":["PROG"],"max_results":100}'
```

**Test 2 — Recursive subpackages:**

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/repository/search \
  -H "Content-Type: application/json" \
  -d '{"packages":["<KNOWN_Z_PACKAGE>"],"include_subpackages":true,"max_results":200}'
```

**Test 3 — Z\* pattern with result limiting:**

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/repository/search \
  -H "Content-Type: application/json" \
  -d '{"name_patterns":["Z*"],"object_types":["PROG"],"max_results":25}'
```

**Test 4 — Class filter only:**

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/repository/search \
  -H "Content-Type: application/json" \
  -d '{"packages":["<KNOWN_Z_PACKAGE>"],"object_types":["CLAS"],"max_results":50}'
```

**Test 5 — Package + pattern (verify match reasons):**

```bash
curl -s -X POST http://127.0.0.1:8080/api/v1/repository/search \
  -H "Content-Type: application/json" \
  -d '{"packages":["<KNOWN_Z_PACKAGE>"],"name_patterns":["Z*"],"max_results":100}'
```

Verify `match_reason` values include `PACKAGE`, `NAME_PATTERN`, and `BOTH`
as appropriate.  **Do not run a full Z\*/Y\* scan without a sensible
`max_results`.**

## RFC allow-list (Phase 3)

| RFC function | Phase | Purpose |
|---|---|---|
| `Z_MCP_REPO_PING` | 2 | ECC connectivity health check |
| `RFC_SYSTEM_INFO` | 2 | Optional diagnostic fallback |
| `Z_MCP_REPO_PACKAGES` | 3 | Package hierarchy resolution |
| `Z_MCP_REPO_DISCOVER` | 3 | Repository object discovery |

Not yet allowed (Phase 4+):

- `Z_MCP_REPO_GET_SOURCE`
- `Z_MCP_REPO_GET_METADATA`
- `Z_MCP_REPO_DEPENDENCIES`

## Z_MCP_REPO_PACKAGES contract

**Import parameters:**

| Parameter | Meaning |
|---|---|
| `IV_PACKAGE` | Root package name |
| `IV_INCLUDE_SUBPACKAGES` | `X` to include full hierarchy |
| `IV_MAX_DEPTH` | Maximum recursion depth |

**Export parameters:**

| Parameter | Meaning |
|---|---|
| `EV_TRUNCATED` | `X` if hierarchy was cut |
| `EV_MESSAGE` | Human-readable result message |

**Table output:**

| Parameter | Meaning |
|---|---|
| `ET_PACKAGES` | Resolved package rows (`PACKAGE`, `PARENT_PACKAGE`, `DESCRIPTION`, `DEPTH`) |

## Z_MCP_REPO_DISCOVER contract

**Import parameters:**

| Parameter | Meaning |
|---|---|
| `IV_ALL_CUSTOM_OBJECTS` | `X` to apply default Z*/Y* patterns |
| `IV_INCLUDE_SUBPACKAGES` | `X` to include subpackages |
| `IV_MAX_RESULTS` | Maximum rows to return |

**Table inputs:**

| Parameter | Meaning |
|---|---|
| `IT_PACKAGES` | Package filter rows |
| `IT_NAME_PATTERNS` | Name-pattern filter rows |
| `IT_OBJECT_TYPES` | Object-type filter rows |

**Export parameters:**

| Parameter | Meaning |
|---|---|
| `EV_TRUNCATED` | `X` if result was cut |
| `EV_TOTAL_MATCHES` | Total matches found in TADIR |
| `EV_MESSAGE` | Human-readable result message |

**Table output:**

| Parameter | Meaning |
|---|---|
| `ET_OBJECTS` | Discovered repository objects (see fields below) |

**ET_OBJECTS row fields:**

| Field | Mandatory | Meaning |
|---|---|---|
| `PGMID` | Yes | Program ID (usually `R3TR`) |
| `OBJECT` | Yes | SAP native object type |
| `OBJ_NAME` | Yes | Object name |
| `DEVCLASS` | Yes | Package |
| `AUTHOR` | No | Author user ID |
| `CREATED_DATE` | No | Creation date |
| `CHANGED_BY` | No | Last changed by user ID |
| `CHANGED_DATE` | No | Last changed date |
| `DESCRIPTION` | No | Short description |
| `ACTIVE_STATUS` | No | Active status |
| `MATCH_REASON` | No | `PACKAGE`, `NAME_PATTERN`, or `BOTH` |
| `WARNING` | No | Object-level warning |
| `SYSTEM_ID` | No | Source system ID |
| `CLIENT` | No | Source client |

## Known limitations (Phase 3)

- `changed_after`, `changed_before`, `changed_by` filter fields are reserved
  in the request model but not forwarded to ECC.  They will be activated once
  the ECC RFC contract supports them.
- `source_components` and `relationships` are always empty in Phase 3 (Phase 4+).
- `metadata` is minimal — only fields returned directly by `Z_MCP_REPO_DISCOVER`.
- Object descriptions depend on ECC returning `DESCRIPTION` in `ET_OBJECTS`.
- Package resolution depth depends on ECC-side `Z_MCP_REPO_PACKAGES`
  implementation; a truncated hierarchy is flagged but not retried.
- BTP connectivity provider is a placeholder (HTTP 501).

## Security considerations

Phase 3 introduces no new attack surfaces beyond Phase 2.  Specifically:

- `RFC_READ_TABLE` is not on the allow-list and cannot be called.
- No SQL `WHERE` clauses are accepted from callers.
- No arbitrary SAP table names are accepted.
- No arbitrary RFC function module names are accepted.
- No ABAP source code is read (Phase 4).
- No SAP write operations of any kind are performed.
- All object-type values are validated against the Phase 3 allow-list.
- All name-pattern values are validated against a safe-character rule.
- SAP credentials never appear in API responses, logs, or exceptions.

## Project structure

```
app/
  api/routes.py          — FastAPI route handlers (health, sap/ping, repository/search)
  core/config.py         — Settings + sub-models (Pydantic BaseSettings)
  models/
    object_types.py      — Phase 3 object-type allow-list + friendly name map
    repository.py        — RepositoryObject + RepositoryPackage + enums
    requests.py          — RepositorySearchRequest (normalized, validated)
    results.py           — RepositorySearchResponse + supporting models
  sap/
    allowlist.py         — RFC allow-list enforcement
    exceptions.py        — SAP domain exception hierarchy
    rfc_runtime.py       — Defensive PyRFC adapter loader
    connection/
      base.py            — SapConnectionProvider ABC
      local.py           — LocalSapConnectionProvider (PyRFC)
      btp.py             — BtpSapConnectionProvider (placeholder)
      factory.py         — Provider factory
  services/
    discovery.py         — RepositoryDiscoveryService
    repository.py        — RepositoryService (delegates to discovery)
    export.py            — ExportService (placeholder)
  mcp/server.py          — MCP adapter boundary (placeholder)
  main.py                — FastAPI application factory

tests/
  test_foundation.py          — Phase 1 foundation tests
  test_health.py              — Health endpoint tests
  test_sap_connectivity.py    — Phase 2 SAP connectivity tests
  test_search_request.py      — Request model normalization tests
  test_phase3_validation.py   — Phase 3 request validation + normalization
  test_phase3_discovery.py    — Phase 3 package resolution + discovery service
  test_phase3_api.py          — Phase 3 API endpoint + regression tests
```

## Roadmap

| Phase | Scope | Status |
|---|---|---|
| 1 | Application Foundation | ✅ Complete |
| 2 | SAP ECC Connectivity | ✅ Complete |
| 3 | SAP Repository Discovery | ✅ Complete |
| 4 | Source Extraction | Planned |
| 5 | Metadata Extraction | Planned |
| 6 | Bulk Export | Planned |
| 7 | Extraction Jobs | Planned |
| 8 | MCP Repository Tools | Planned |
| 9 | Dependency Analysis | Planned |
| 10 | Clean Core / S/4 Analysis | Planned |
