# N-CoreIQ — SAP Repository Intelligence Layer

Version 0.2.0 — BTP-ready, read-only SAP ECC custom repository discovery and
extraction platform.

**Current scope: Phase 1 (Application Foundation) + Phase 2 (SAP ECC
Connectivity).** Repository discovery, source extraction, export, dependency
analysis, and further MCP tooling are later phases and are intentionally not
implemented yet.

## Current capabilities

- FastAPI service with OpenAPI documentation
- BTP/local configuration abstraction
- SAP connection provider abstraction with a real local RFC implementation
- Defensive PyRFC / SAP NetWeaver RFC SDK adapter loading (no import-time crash)
- Explicit RFC allow-list (`Z_MCP_REPO_PING`, `RFC_SYSTEM_INFO`)
- Real `GET /api/v1/sap/ping` connectivity check against SAP ECC Development
- Canonical repository object model
- Package/name/object-type search contract (still 501 — Phase 3+)
- Repository service boundary for future `Z_MCP_REPO_*` RFC APIs
- ZIP export service with manifest support
- MCP tool contract boundary
- Docker and SAP BTP Cloud Foundry scaffolding
- Initial XSUAA role model
- Unit tests for health/config/SAP connectivity behavior

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

The BTP path is preserved as a placeholder and intentionally **not**
implemented in Phase 2:

```
FastAPI
   ↓
SAP service
   ↓
SAP connection provider abstraction
   ↓
BtpSapConnectionProvider   ← Phase 2 placeholder, returns HTTP 501
   ↓
SAP BTP Destination / Connectivity
   ↓
Cloud Connector
   ↓
ECC
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
4. A valid SAP ECC user with authorization to execute `Z_MCP_REPO_PING` (and
   optionally `RFC_SYSTEM_INFO`) via RFC.

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
- `http://127.0.0.1:8080/docs`

## How to test SAP connectivity

- **Without SAP SDK installed:** `GET /api/v1/sap/ping` returns HTTP 501
  (`SAP NetWeaver RFC SDK / PyRFC runtime is not available on this host.`).
  This is expected and controlled — it does not crash the app.
- **With PyRFC + SDK installed and `.env` configured, but SAP unreachable:**
  returns HTTP 503 (`SAP ECC is unreachable from this host.`).
- **With correct configuration and live ECC Development access:**
  `GET /api/v1/sap/ping` calls `Z_MCP_REPO_PING` and returns HTTP 200 with the
  normalized connectivity result (see contract below).
- **Automated / mocked tests:** `pytest -v` exercises the full connectivity
  flow (success, SAP application error, authentication failure, communication
  failure, allow-list enforcement, missing-runtime, missing-config) entirely
  with mocks — no live ECC, no SAP SDK, no real credentials required.

## Expected `Z_MCP_REPO_PING` contract

ECC RFC-enabled function module `Z_MCP_REPO_PING` is expected to return:

| Export parameter | Meaning |
|---|---|
| `EV_STATUS` | SAP-side status indicator |
| `EV_SYSID` | SAP system ID (e.g. `DEV`) |
| `EV_CLIENT` | SAP client |
| `EV_SAP_RELEASE` | SAP release string |
| `EV_TIMESTAMP` | SAP-side timestamp |
| `EV_MESSAGE` | Human-readable status message |

This is normalized into the N-CoreIQ application model:

```json
{
  "status": "connected",
  "connection_mode": "local",
  "system_id": "DEV",
  "client": "100",
  "sap_release": "...",
  "timestamp": "...",
  "ping_method": "Z_MCP_REPO_PING",
  "message": "N-CoreIQ RFC connectivity successful."
}
```

`RFC_SYSTEM_INFO` is on the allow-list as an optional diagnostic fallback
only; it is never treated as equivalent to a successful `Z_MCP_REPO_PING`
result, and any `Z_MCP_REPO_PING` application error is surfaced rather than
silently masked.

## Common error states

| HTTP | Condition |
|---|---|
| 422 | SAP local configuration incomplete or invalid (missing host/client/user/password) |
| 501 | PyRFC / SAP NetWeaver RFC SDK not installed, or `SAP_CONNECTION_MODE=btp` (not implemented in Phase 2) |
| 503 | SAP ECC unreachable |
| 503 | SAP authentication failed |
| 503 | RFC communication failure |
| 503 | `Z_MCP_REPO_PING` execution failure (SAP application error) |

None of these responses ever include a Python traceback, the SAP password,
raw service bindings, low-level RFC connection parameters, or sensitive SAP
error payloads.

## Security rules

- `.env` is excluded via `.gitignore`; `.env.example` contains placeholders
  only.
- The SAP password is never included in exceptions, `repr()` output, logs,
  API responses, diagnostics, or configuration dumps.
- Logs contain only sanitized diagnostic categories (operation, provider,
  connection mode, system/client *after* a successful connection, result
  category) — never passwords, connection strings containing passwords,
  complete credential dictionaries, service keys, or RFC SDK secrets.
- RFC execution is restricted to an explicit allow-list
  (`Z_MCP_REPO_PING`, `RFC_SYSTEM_INFO`); any other function name is rejected
  with `SAPRFCNotAllowedError` before it ever reaches SAP.
- `/api/v1/health` is independent of SAP connectivity by design.

## Version 0.2.0 API contracts

### Health

`GET /api/v1/health` — always returns HTTP 200 with application status,
independent of SAP/ECC availability.

### SAP connectivity ping (Phase 2)

`GET /api/v1/sap/ping` — performs a real RFC call to `Z_MCP_REPO_PING`
against SAP ECC Development (local connection mode).

Successful response:

```json
{
  "status": "connected",
  "connection_mode": "local",
  "system_id": "DEV",
  "client": "100",
  "sap_release": "...",
  "timestamp": "...",
  "ping_method": "Z_MCP_REPO_PING",
  "message": "N-CoreIQ RFC connectivity successful."
}
```

Error responses follow the mapping in "Common error states" above and are
returned via standard FastAPI `HTTPException` JSON (`{"detail": "..."}`),
never leaking SAP internals.

### Search repository (Phase 3+ — not implemented)

`POST /api/v1/repository/search`

```json
{
  "packages": ["ZFINANCE"],
  "include_subpackages": true,
  "name_patterns": ["Z*", "Y*"],
  "object_types": ["PROG", "CLAS", "FUGR", "TABL"],
  "max_results": 5000
}
```

The endpoint currently returns HTTP 501 until Phase 3 RFC adapters are
activated.

## Planned ECC RFC contract (future phases)

Phase 2 implements only `Z_MCP_REPO_PING` (plus `RFC_SYSTEM_INFO` as an
optional diagnostic fallback). Later phases will add:

- `Z_MCP_REPO_PACKAGES`
- `Z_MCP_REPO_DISCOVER`
- `Z_MCP_REPO_GET_OBJECT`
- `Z_MCP_REPO_GET_SOURCE`
- `Z_MCP_REPO_GET_METADATA`

These are explicitly **out of scope** for Phase 2 and are not implemented in
this codebase yet.

## Next implementation milestone

With `Z_MCP_REPO_PING` connectivity proven in Phase 2, Phase 3 will implement
recursive package discovery and TADIR-based object discovery via
`Z_MCP_REPO_PACKAGES` / `Z_MCP_REPO_DISCOVER`.
