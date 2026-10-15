# N-CoreIQ Phase 9 â€” SAP BTP Production Readiness

## Overview

Phase 9 makes N-CoreIQ deployable, secure, scalable, observable, and supportable on SAP BTP while preserving all Phase 1â€“8C functionality.

**Version:** 0.9.0  
**Status:** Production Ready (local mode) / BTP-Ready (requires bound services)

---

## Architecture

```
Users / Browser / MCP Clients
            |
            v
    Correlation ID Middleware
            |
            v
    XSUAA Auth Middleware
            |
            v
       N-CoreIQ API (FastAPI)
            |
   -----------------------
   |          |          |
   v          v          v
 SQLite    Local FS   SAP Provider
 JobStore  Artifacts  (local/btp/odata)
                         |
                    [BTP mode only]
                         |
                    Destination
                     Service
                         |
                    Connectivity
                     Service
                         |
                   Cloud Connector
                         |
                      SAP ECC
                         |
                  ZNCOREIQ_REPO_SRV
```

---

## Runtime Modes

| Mode | SAP Connectivity | Job Store | Artifact Store | Auth |
|------|-----------------|-----------|----------------|------|
| `local` | Direct HTTPS to SAP Gateway | SQLite | Local filesystem | disabled |
| `btp` | BTP Destination â†’ Connectivity â†’ Cloud Connector | SQLite/DB | Local/Object Store | xsuaa |
| `test` | Mocked | In-memory | In-memory | disabled |

Set via: `APP_ENV=local` / `APP_ENV=btp` / `APP_ENV=test`

---

## Configuration Reference

### Application
```
APP_ENV=local                          # local | btp | test
APP_VERSION=0.9.0
LOG_LEVEL=INFO
LOGGING__FORMAT=text                   # text | json (json recommended for BTP)
LOGGING__INSTANCE_ID=0                 # auto-detected from CF_INSTANCE_INDEX
```

### SAP Connection (local/odata mode)
```
SAP_CONNECTION_MODE=odata              # local | odata | btp
SAP_ODATA__BASE_URL=https://host:44300
SAP_ODATA__SERVICE=ZNCOREIQ_REPO_SRV
SAP_ODATA__CLIENT=800
SAP_ODATA__USER=<sap_user>
SAP_ODATA__PASSWORD=<secret>
SAP_ODATA__TIMEOUT=30
SAP_ODATA__CA_BUNDLE=<path>           # optional corporate CA bundle
SAP_ODATA__SKIP_TLS_VERIFY=false      # NEVER true in production
SAP_ODATA__RETRY_ATTEMPTS=3
```

### SAP Connection (BTP mode)
```
SAP_CONNECTION_MODE=btp
BTP__SAP_DESTINATION_NAME=NC1_ODATA
BTP__CLOUD_CONNECTOR_LOCATION_ID=     # optional
# Credential values populated from VCAP_SERVICES by BTPServiceBindingResolver
```

### Authentication (XSUAA)
```
AUTH__MODE=xsuaa                       # disabled | xsuaa
AUTH__JWKS_URI=                        # auto-derived from BTP__XSUAA__URL
# AUTH__AUDIENCE is optional and auto-derived from the bound XSUAA identity.
# Do NOT set this to a manually constructed tenant-qualified value (!t...).
# N-CoreIQ automatically derives: <bound-xsappname> and <bound-xsappname>.NCoreIQ
# AUTH__AUDIENCE=                    # leave unset for normal production deployments
AUTH__ISSUER=                          # optional XSUAA issuer
AUTH__CORS_ORIGINS=["https://ncoreiq.cfapps.eu10.hana.ondemand.com"]
```

### CORS
```
CORS_ALLOWED_ORIGINS=["https://ncoreiq.cfapps.eu10.hana.ondemand.com"]
CORS_ALLOW_CREDENTIALS=true
```

### Jobs & Concurrency
```
NCOREIQ_MAX_RUNNING_JOBS_PER_INSTANCE=2
NCOREIQ_MAX_SAP_CONCURRENCY_PER_INSTANCE=5
NCOREIQ_JOB_WORKERS=2
NCOREIQ_JOB_LEASE_SECONDS=120
NCOREIQ_JOB_HEARTBEAT_SECONDS=30
```

---

## BTP Service Bindings

Phase 9 reads BTP service credentials from **VCAP_SERVICES** (Cloud Foundry standard). The `BTPServiceBindingResolver` discovers and validates bindings at startup.

### Required Services (BTP mode)

| Service | Purpose |
|---------|---------|
| XSUAA (xsuaa) | JWT authentication and authorization |
| Destination (destination) | SAP Destination Service for ECC connectivity |
| Connectivity (connectivity) | Cloud Connector proxy for on-premise ECC |

### Binding Validation

In `APP_ENV=btp`, N-CoreIQ validates required bindings at startup:
- Missing bindings are logged as warnings
- If `SAP_CONNECTION_MODE=btp` and no bindings are found AND no direct `SAP_ODATA__BASE_URL` is configured, startup will warn clearly

---

## SAP Destination Setup

### In BTP Subaccount Cockpit

Create a destination named `NC1_ODATA` (or configured value of `BTP__SAP_DESTINATION_NAME`):

| Field | Value |
|-------|-------|
| Name | `NC1_ODATA` |
| Type | HTTP |
| URL | `https://<sap-gateway-host>:44300` |
| Authentication | BasicAuthentication |
| User | `<sap_user>` |
| Password | `<sap_password>` |
| ProxyType | `OnPremise` (for Cloud Connector) or `Internet` |
| Additional Property: `sap-client` | `800` |

### Cloud Connector Setup

When `ProxyType=OnPremise`:
1. Install and configure SAP Cloud Connector
2. Map virtual host to physical SAP Gateway host
3. Add virtual host to Cloud Connector allowed resources
4. Optionally configure `CloudConnectorLocationId` if multiple Cloud Connectors

---

## Authentication & Authorization

### JWT Validation

Phase 9 validates XSUAA JWTs at the middleware layer:
- JWKS fetched from `<XSUAA_URL>/token_keys` (cached 300s)
- Signature validated using RS256
- Expiry, audience, and issuer validated

**Production requirement:** Install `PyJWT[crypto]` and `cryptography`:
```bash
pip install PyJWT[crypto] cryptography
```
Without these packages, expiry-only check is performed (not suitable for production).

### Roles and Scopes

| Role | Scope | Permissions |
|------|-------|-------------|
| NCoreIQ_Viewer | `NCoreIQ.Viewer` | Read-only: ping, packages, search, metadata, source, dependencies |
| NCoreIQ_Analyst | `NCoreIQ.Analyst` | Viewer + traversal, reverse lookup, impact analysis, source export |
| NCoreIQ_Operator | `NCoreIQ.Operator` | Analyst + job management, artifact operations |
| NCoreIQ_Admin | `NCoreIQ.Admin` | All capabilities + diagnostics, configuration |

### Local Mode

Set `AUTH__MODE=disabled` (default) to skip authentication. All requests receive `ANONYMOUS_PRINCIPAL` with full Admin access.

---

## Health Endpoints

| Endpoint | Purpose | Auth Required |
|----------|---------|---------------|
| `GET /health/live` | Liveness probe (process alive) | None |
| `GET /health/ready` | Readiness probe (dependencies ready) | None |
| `GET /health` | Legacy health (backward compat) | None |
| `GET /api/v1/health` | Phase 1 health format | None |

### Liveness

Always returns 200 if the process is running. Does NOT check SAP connectivity â€” a SAP outage must not restart healthy application instances.

### Readiness

Checks:
- Job store database reachable
- Artifact store accessible  
- BTP configuration present (BTP mode only)

Returns 503 if any mandatory dependency is unavailable.

---

## Admin Endpoints

| Endpoint | Purpose | Required Scope |
|----------|---------|----------------|
| `GET /api/v1/admin/diagnostics` | Full safe diagnostics | NCoreIQ.Admin |
| `GET /api/v1/admin/metrics` | Lightweight metrics | NCoreIQ.Admin |

These endpoints **never expose credentials**, connection strings, or secrets.

---

## Structured Logging

Phase 9 implements BTP-compatible structured JSON logging.

### Log Fields

```json
{
  "timestamp": "2026-10-10T05:07:11.669+00:00",
  "level": "INFO",
  "service": "N-CoreIQ",
  "version": "0.9.0",
  "runtime_mode": "btp",
  "instance_id": "0",
  "request_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
  "correlation_id": "b3c4d5e6-...",
  "logger": "app.sap.connection.btp",
  "message": "btp_provider_ping destination=NC1_ODATA",
  "operation": "btp_ping"
}
```

### Correlation ID Propagation

N-CoreIQ generates a `correlation_id` per request and echoes it in:
- Response headers: `X-Correlation-ID`, `X-Request-ID`
- All log records for that request
- SAP outbound request context

Clients can provide `X-Correlation-ID` in requests to propagate their own correlation ID.

### Secret Redaction

All log records automatically redact fields matching secret patterns:
- `password`, `token`, `authorization`, `secret`, `clientsecret`, `private_key`, `verificationkey`, `certificate`, `servicekey`

---

## Security

### TLS

- Strict TLS verification enabled by default (`skip_tls_verify=false`)
- BTP mode always uses strict TLS â€” no override possible
- Local mode supports `SAP_ODATA__CA_BUNDLE` for corporate CAs
- `SAP_ODATA__SKIP_TLS_VERIFY=true` is available for local PoC only â€” **NEVER in production**

### CORS

Production CORS never uses wildcard `*`. Configure specific trusted origins:
```
CORS_ALLOWED_ORIGINS=["https://ncoreiq.cfapps.eu10.hana.ondemand.com"]
```

### Security Headers

All responses include:
- `X-Content-Type-Options: nosniff`
- `Referrer-Policy: strict-origin-when-cross-origin`
- `X-Frame-Options: SAMEORIGIN`
- `Content-Security-Policy` on `/ui` routes

---

## Deployment

### Cloud Foundry (manifest.yml)

```yaml
applications:
  - name: ncoreiq
    memory: 768M
    instances: 1
    buildpacks:
      - python_buildpack
    command: uvicorn app.main:app --host 0.0.0.0 --port $PORT
    health-check-type: http
    health-check-http-endpoint: /health/live
    env:
      APP_ENV: btp
      SAP_CONNECTION_MODE: btp
      AUTH_MODE: xsuaa
      LOGGING__FORMAT: json
    services:
      - ncoreiq-xsuaa
      - ncoreiq-destination
      - ncoreiq-connectivity
```

### MTA (mta.yaml)

Use `deployment/btp/mta.yaml` for multi-target application deployment:
```bash
mbt build
cf deploy mta_archives/ncoreiq_0.9.0.mtar
```

---

## Operations & Troubleshooting

### Application Startup Failure

1. Check CF logs: `cf logs ncoreiq --recent`
2. Look for `event=startup_failed` or `event=btp_startup_missing_bindings`
3. Verify all required services are bound: `cf services`
4. Check environment variables: `cf env ncoreiq`

### Destination Missing

**Symptom:** `SAPConfigurationError: SAP Destination Service is not bound`

**Resolution:**
1. Verify Destination Service is bound: `cf services | grep destination`
2. Verify destination `NC1_ODATA` exists in BTP subaccount
3. Check `BTP__SAP_DESTINATION_NAME` matches the destination name

### Connectivity Service Unavailable

**Symptom:** `SAPConnectionError: Cannot reach Connectivity Service token endpoint`

**Resolution:**
1. Verify Connectivity Service binding: `cf services | grep connectivity`
2. Check Cloud Connector is running and connected to the BTP subaccount

### Cloud Connector Disconnected

**Symptom:** SAP requests fail with connection refused at proxy

**Resolution:**
1. Check Cloud Connector administration UI
2. Verify virtual host mapping is configured
3. Verify the destination `ProxyType=OnPremise` is set

### SAP Gateway Unavailable

**Symptom:** `SAPConnectionError: SAP Gateway is unreachable`

**Resolution:**
1. Verify Cloud Connector has the correct virtual host mapped to SAP Gateway
2. Test connectivity: `cf ssh ncoreiq` â†’ curl to Connectivity proxy
3. Check SAP Gateway service `/IWFND/MAINT_SERVICE` is active

### Job Stuck / Lease Expired

**Symptom:** Job remains in `RUNNING` state with expired `lease_expires_at`

**Resolution:**
The lease recovery runs automatically at startup via `recover_stale_jobs()`. Restart the application to trigger immediate recovery.

### High SAP Latency

**Symptom:** Slow API responses, job timeouts

**Mitigation:**
1. Reduce `NCOREIQ_MAX_SAP_CONCURRENCY_PER_INSTANCE`
2. Increase `SAP_ODATA__TIMEOUT`
3. Check SAP Gateway performance via `/SDF/E2E_TRACE`

### Frontend Authentication Issue

**Symptom:** Browser receives 401 on API calls

**Resolution:**
1. Verify XSUAA configuration in xs-security.json
2. Check token expiry (default 12 hours)
3. Verify `AUTH__CORS_ORIGINS` includes the frontend URL

---

## Multi-Instance Notes

- Job leasing (`claimed_by`, `lease_expires_at`) prevents duplicate job processing
- Artifact store uses local filesystem â€” use shared persistent storage for multi-instance BTP deployments
- All API instances are stateless â€” no session affinity required for API calls

---

## Known Limitations (Phase 9)

1. **SQLite job store** â€” suitable for single-instance BTP deployments. For multi-instance production, configure a managed relational database via `NCOREIQ_DB_URL`.
2. **Local artifact store** â€” CF ephemeral filesystem. Configure object store for persistent multi-instance artifacts.
3. **PyJWT optional** â€” Full RS256 signature validation requires `pip install PyJWT[crypto] cryptography`.
4. **MCP remote exposure** â€” If MCP is exposed remotely, implement the same XSUAA auth as the REST API.

---

## Phase 9 Completion Gates

| Gate | Status |
|------|--------|
| Local mode works | âœ… |
| BTP mode infrastructure in place | âœ… |
| Destination Service resolver | âœ… |
| Connectivity Service support | âœ… |
| Cloud Connector support | âœ… |
| Strict TLS | âœ… |
| XSUAA-compatible authentication | âœ… |
| Authorization (4 roles) | âœ… |
| Production JobStore (SQLite with leasing) | âœ… |
| Stateless API instances | âœ… |
| Liveness probe | âœ… |
| Readiness probe | âœ… |
| Structured logging | âœ… |
| Correlation IDs | âœ… |
| Secret redaction | âœ… |
| Production error sanitization | âœ… |
| CORS restricted | âœ… |
| Security headers | âœ… |
| MTA deployment descriptor | âœ… |
| xs-security.json | âœ… |
| Phase 8B Repository Explorer preserved
