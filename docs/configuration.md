# N-CoreIQ — Configuration Reference (Version 1.0.0)

All configuration is loaded from environment variables (or a `.env` file in the project root).
Nested settings use double-underscore delimiter: `SAP_ODATA__PASSWORD` maps to `sap_odata.password`.

---

## Application Identity

| Setting | Type | Default | Required | Secret | Description |
|---------|------|---------|----------|--------|-------------|
| `APP_NAME` | string | `N-CoreIQ` | No | No | Application display name |
| `APP_VERSION` | string | `1.0.0` | No | No | Application version |
| `APP_ENV` | enum | `local` | No | No | Runtime environment: `local`, `btp`, `test` |
| `LOG_LEVEL` | string | `INFO` | No | No | Log level: `DEBUG`, `INFO`, `WARNING`, `ERROR` |

---

## SAP Connection

| Setting | Type | Default | Required | Secret | Description |
|---------|------|---------|----------|--------|-------------|
| `SAP_CONNECTION_MODE` | enum | `local` | Yes | No | Connection mode: `odata` (recommended), `local` (PyRFC), `btp` |
| `SAP_DESTINATION_NAME` | string | `ECC_DEV` | BTP only | No | Legacy BTP destination name (see `BTP__SAP_DESTINATION_NAME`) |

### OData Connection (`SAP_ODATA__*`)

| Setting | Type | Default | Required | Secret | Description |
|---------|------|---------|----------|--------|-------------|
| `SAP_ODATA__BASE_URL` | string | — | `odata` mode | No | SAP Gateway base URL, e.g. `https://host:44300` |
| `SAP_ODATA__SERVICE` | string | `ZNCOREIQ_REPO_SRV` | No | No | OData service name |
| `SAP_ODATA__CLIENT` | string | `800` | No | No | SAP client number |
| `SAP_ODATA__USER` | string | — | `odata` mode | No | SAP technical user name |
| `SAP_ODATA__PASSWORD` | string | — | `odata` mode | **Yes** | SAP technical user password |
| `SAP_ODATA__TIMEOUT` | int | `30` | No | No | Request timeout in seconds |
| `SAP_ODATA__CA_BUNDLE` | string | — | No | No | Path to CA bundle PEM file for TLS validation |
| `SAP_ODATA__SKIP_HOSTNAME_CHECK` | bool | `false` | No | No | Skip TLS hostname check (dev only) |
| `SAP_ODATA__SKIP_TLS_VERIFY` | bool | `false` | No | No | Disable TLS entirely (dev only, **never production**) |
| `SAP_ODATA__RETRY_ATTEMPTS` | int | `3` | No | No | Retries for transient SAP failures (0–5) |
| `SAP_ODATA__HTTP_CONNECT_TIMEOUT` | float | `10.0` | No | No | TCP connect timeout (seconds) |
| `SAP_ODATA__HTTP_READ_TIMEOUT` | float | `60.0` | No | No | HTTP read timeout (seconds) |
| `SAP_ODATA__HTTP_POOL_CONNECTIONS` | int | `10` | No | No | HTTP connection pool size |

### Local/RFC Connection (`SAP_LOCAL__*`)

Used when `SAP_CONNECTION_MODE=local`. Requires PyRFC and SAP NetWeaver RFC SDK.

| Setting | Type | Default | Required | Secret | Description |
|---------|------|---------|----------|--------|-------------|
| `SAP_LOCAL__HOST` | string | — | `local` mode | No | SAP application server hostname |
| `SAP_LOCAL__SYSTEM_NUMBER` | string | — | `local` mode | No | SAP system number (2 digits) |
| `SAP_LOCAL__CLIENT` | string | — | `local` mode | No | SAP client number |
| `SAP_LOCAL__USER` | string | — | `local` mode | No | SAP user name |
| `SAP_LOCAL__PASSWORD` | string | — | `local` mode | **Yes** | SAP user password |
| `SAP_LOCAL__LANGUAGE` | string | `EN` | No | No | SAP logon language |

---

## BTP Service Bindings (`BTP__*`)

In Cloud Foundry, populated automatically from `VCAP_SERVICES`. For local BTP simulation, set manually.

### XSUAA (`BTP__XSUAA__*`)

| Setting | Type | Default | Secret | Description |
|---------|------|---------|--------|-------------|
| `BTP__XSUAA__URL` | string | — | No | XSUAA service URL |
| `BTP__XSUAA__CLIENTID` | string | — | No | OAuth2 client ID |
| `BTP__XSUAA__CLIENTSECRET` | string | — | **Yes** | OAuth2 client secret |
| `BTP__XSUAA__XSAPPNAME` | string | — | No | XS application name |
| `BTP__XSUAA__UAADOMAIN` | string | — | No | UAA domain |
| `BTP__XSUAA__VERIFICATIONKEY` | string | — | **Yes** | JWT verification key (PEM) |

### Destination Service (`BTP__DESTINATION__*`)

| Setting | Type | Default | Secret | Description |
|---------|------|---------|--------|-------------|
| `BTP__DESTINATION__URI` | string | — | No | Destination service URI |
| `BTP__DESTINATION__CLIENTID` | string | — | No | OAuth2 client ID |
| `BTP__DESTINATION__CLIENTSECRET` | string | — | **Yes** | OAuth2 client secret |
| `BTP__DESTINATION__URL` | string | — | No | Destination service URL |

### Connectivity Service (`BTP__CONNECTIVITY__*`)

| Setting | Type | Default | Secret | Description |
|---------|------|---------|--------|-------------|
| `BTP__CONNECTIVITY__ONPREMISE_PROXY_HOST` | string | — | No | Cloud Connector proxy host |
| `BTP__CONNECTIVITY__ONPREMISE_PROXY_PORT` | int | — | No | Cloud Connector proxy port |
| `BTP__CONNECTIVITY__CLIENTID` | string | — | No | OAuth2 client ID |
| `BTP__CONNECTIVITY__CLIENTSECRET` | string | — | **Yes** | OAuth2 client secret |

### BTP General

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `BTP__SAP_DESTINATION_NAME` | string | `NCOREIQ_ECC` | Destination name for SAP ECC |
| `BTP__CLOUD_CONNECTOR_LOCATION_ID` | string | — | Cloud Connector location ID (multi-CC) |

---

## Authentication (`AUTH__*`)

| Setting | Type | Default | Required | Description |
|---------|------|---------|----------|-------------|
| `AUTH_MODE` | enum | `disabled` | No | Auth mode: `disabled` (local), `xsuaa` (BTP) |
| `AUTH__MODE` | enum | `disabled` | No | Nested form (takes precedence) |
| `AUTH__JWKS_URI` | string | — | `xsuaa` mode | JWKS endpoint for JWT validation |
| `AUTH__AUDIENCE` | string | — | `xsuaa` mode | Expected JWT audience |
| `AUTH__ISSUER` | string | — | `xsuaa` mode | Expected JWT issuer |
| `AUTH__JWKS_CACHE_TTL_SECONDS` | int | `300` | No | JWKS cache TTL (≥60) |
| `AUTH__CORS_ORIGINS` | list | `["http://localhost:5173"]` | No | Auth-specific CORS origins |

---

## CORS

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `CORS_ALLOWED_ORIGINS` | list | `["http://localhost:5173","http://localhost:3000"]` | Allowed CORS origins |
| `CORS_ALLOW_CREDENTIALS` | bool | `true` | Allow credentials in CORS |

---

## Logging (`LOGGING__*`)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `LOGGING__LEVEL` | string | `INFO` | Log level |
| `LOGGING__FORMAT` | enum | `text` | Log format: `text` or `json` |
| `LOGGING__INCLUDE_REQUEST_ID` | bool | `true` | Include request ID in logs |
| `LOGGING__INCLUDE_CORRELATION_ID` | bool | `true` | Include correlation ID in logs |
| `LOGGING__INSTANCE_ID` | string | — | Instance identifier (auto-detected in CF) |

---

## Export

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `EXPORT_MODE` | enum | `zip` | Export mode: `zip` or `local` |
| `LOCAL_EXPORT_ROOT` | string | `./exports` | Root directory for local exports |

---

## Bulk Extraction (`NCOREIQ_BULK_*`)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `NCOREIQ_BULK_MAX_OBJECTS` | int | `250` | Maximum objects per bulk extraction request |
| `NCOREIQ_BULK_CONCURRENCY` | int | `3` | Concurrent SAP object retrievals (1–5) |
| `NCOREIQ_BULK_MAX_TOTAL_SOURCE_LINES` | int | `250000` | Total source line limit per extraction |

---

## Extraction Jobs (`NCOREIQ_JOB_*` / `NCOREIQ_ARTIFACT_*`)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `NCOREIQ_JOB_WORKERS` | int | `2` | Background job worker count (1–10) |
| `NCOREIQ_JOB_POLL_INTERVAL_SECONDS` | float | `2.0` | Worker poll interval |
| `NCOREIQ_JOB_LEASE_SECONDS` | int | `120` | Job worker lease duration |
| `NCOREIQ_JOB_HEARTBEAT_SECONDS` | int | `30` | Worker heartbeat interval |
| `NCOREIQ_JOB_RETENTION_DAYS` | int | `7` | Job record retention (days) |
| `NCOREIQ_ARTIFACT_RETENTION_DAYS` | int | `7` | Artifact file retention (days) |
| `NCOREIQ_JOB_ARTIFACT_DIR` | string | `./job_artifacts` | Artifact storage directory |
| `NCOREIQ_JOB_DB_PATH` | string | `./ncoreiq_jobs.db` | SQLite job database path |
| `NCOREIQ_JOB_MAX_LIST_LIMIT` | int | `200` | Maximum jobs returned by list API |
| `NCOREIQ_DISABLE_EXECUTOR` | bool | `false` | Disable background executor (test mode) |
| `NCOREIQ_MAX_RUNNING_JOBS_PER_INSTANCE` | int | `2` | Max concurrent running jobs per instance |
| `NCOREIQ_MAX_SAP_CONCURRENCY_PER_INSTANCE` | int | `5` | Max concurrent SAP calls per instance |
| `NCOREIQ_DB_URL` | string | — | Production DB URL (empty = SQLite) |

---

## MCP Server (`NCOREIQ_MCP_*`)

| Setting | Type | Default | Description |
|---------|------|---------|-------------|
| `NCOREIQ_MCP_ENABLED` | bool | `true` | Enable MCP server |
| `NCOREIQ_MCP_SERVER_NAME` | string | `N-CoreIQ` | MCP server name |
| `NCOREIQ_MCP_SERVER_VERSION` | string | `1.0.0` | MCP server version |
| `NCOREIQ_MCP_TRANSPORT` | string | `stdio` | MCP transport: `stdio` or `streamable_http` |
| `NCOREIQ_MCP_MAX_SOURCE_LINES` | int | `500` | Max source lines per MCP response (1–5000) |
| `NCOREIQ_MCP_MAX_SEARCH_RESULTS` | int | `100` | Max search results per MCP call (1–1000) |
| `NCOREIQ_MCP_MAX_JOB_LIST_RESULTS` | int | `20` | Max jobs in MCP list (1–100) |
| `NCOREIQ_MCP_TOOL_TIMEOUT_SECONDS` | int | `120` | MCP tool execution timeout |
| `NCOREIQ_MCP_MAX_DEP_RESULTS` | int | `200` | Max dependency relations per call (1–1000) |
| `NCOREIQ_MCP_DEP_MAX_DEPTH` | int | `3` | Max dependency traversal depth (1–3) |
| `NCOREIQ_MCP_DEP_MAX_NODES` | int | `200` | Max graph nodes (1–200) |
| `NCOREIQ_MCP_DEP_MAX_EDGES` | int | `500` | Max graph edges (1–500) |
| `NCOREIQ_MCP_DEP_MAX_CANDIDATES` | int | `50` | Max reverse dependency candidates (1–50) |
