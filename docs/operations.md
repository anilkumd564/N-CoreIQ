# N-CoreIQ — Operations Guide (Version 1.0.0)

## Application Lifecycle

### Start (Local)

```bash
# Backend only
uvicorn app.main:app --host 0.0.0.0 --port 8000

# With auto-reload (development)
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

# Frontend (development)
cd frontend && npm run dev

# MCP Server (stdio transport)
python -m app.mcp.server
```

### Stop (Local)

Send `SIGTERM` or `Ctrl+C`. The application performs graceful shutdown:
1. Stops accepting new requests
2. Signals job executor to stop
3. Workers complete current objects
4. Executor releases all job leases
5. HTTP connections are closed

### BTP Deployment

```bash
cf push -f deployment/btp/manifest.yml
cf stop ncoreiq
cf start ncoreiq
cf restage ncoreiq   # after env var changes
```

---

## Health Monitoring

| Endpoint | Purpose | Expected Response |
|----------|---------|-------------------|
| `GET /health/live` | Liveness — is the process alive? | `200 {"status": "alive"}` |
| `GET /health/ready` | Readiness — can it serve traffic? | `200 {"status": "ready"}` or `503` |
| `GET /health` | Detailed health + job stats | `200 {"status": "ok", "version": "1.0.0", ...}` |
| `GET /api/v1/sap/ping` | SAP connectivity check | `200 {"status": "connected", ...}` |
| `GET /api/v1/admin/health` | Admin health (authenticated) | Full diagnostics |
| `GET /api/v1/admin/metrics` | Runtime metrics | Job counts, timing |

**Cloud Foundry health-check configuration** (in `manifest.yml`):
```yaml
health-check-type: http
health-check-http-endpoint: /health/live
```

---

## Logging

### Log Format

Production (`LOGGING__FORMAT=json`):
```json
{
  "timestamp": "2026-10-10T12:00:00.000Z",
  "level": "INFO",
  "service": "N-CoreIQ",
  "version": "1.0.0",
  "runtime_mode": "btp",
  "instance_id": "0",
  "request_id": "req-uuid",
  "correlation_id": "corr-uuid",
  "event": "repository_search_complete",
  "duration_ms": 245,
  "status": "ok"
}
```

### Log Levels

| Level | Use |
|-------|-----|
| `ERROR` | Unhandled exceptions, critical failures |
| `WARNING` | Recoverable errors, stale job recovery |
| `INFO` | Normal operational events, job lifecycle |
| `DEBUG` | Detailed diagnostics (not recommended for production) |

### Key Log Events

| Event | Level | Meaning |
|-------|-------|---------|
| `event=startup_complete` | INFO | Application started successfully |
| `event=job_created` | INFO | Extraction job created |
| `event=job_claimed` | INFO | Worker claimed a job |
| `event=job_finished` | INFO | Job completed |
| `event=job_failed` | INFO | Job failed |
| `event=stale_jobs_recovered` | WARNING | Jobs recovered after restart |
| `event=sap_ping_auth_failed` | WARNING | SAP authentication failure |
| `event=unhandled_exception` | ERROR | Unexpected internal error |

---

## Job Management

### Job States

```
QUEUED → RUNNING → SUCCEEDED
                 → FAILED
                 → CANCEL_REQUESTED → CANCELLED
       → CANCELLED  (direct, if QUEUED)
```

### List Running Jobs

```bash
curl http://localhost:8000/api/v1/repository/jobs?status=RUNNING
```

### Cancel a Job

```bash
curl -X POST http://localhost:8000/api/v1/repository/jobs/{job_id}/cancel
```

### Retry a Failed Job

```bash
curl -X POST http://localhost:8000/api/v1/repository/jobs/{job_id}/retry
```

### Job Recovery

On startup, the application automatically recovers stale jobs:
- Jobs in `RUNNING` or `CANCEL_REQUESTED` with expired leases → reset to `QUEUED`
- Log: `event=stale_jobs_recovered count=N`

To manually trigger recovery (restart the application):
```bash
cf restart ncoreiq
```

---

## Artifact Management

### Storage Location

Local mode: `./job_artifacts/` (configurable via `NCOREIQ_JOB_ARTIFACT_DIR`)

### Download an Artifact

```bash
curl -o export.zip http://localhost:8000/api/v1/repository/jobs/{job_id}/artifact
```

### Retention

Artifacts are automatically expired after `NCOREIQ_ARTIFACT_RETENTION_DAYS` (default: 7 days).
Cleanup runs at startup and can be triggered via job service.

---

## Scaling

### Local (Single Instance)

Default configuration supports local single-instance deployment.

### BTP Multi-Instance

```yaml
# manifest.yml
instances: 2
```

Multi-instance safety:
- Job store (DB) is shared state
- Each instance claims jobs atomically (exclusive lock)
- No duplicate job execution via lease mechanism
- Artifacts stored in shared volume or object store

**Important**: With N instances and `NCOREIQ_JOB_WORKERS=2`, total SAP concurrency = `N × 2 workers × NCOREIQ_MAX_SAP_CONCURRENCY_PER_INSTANCE`. Scale conservatively to protect SAP ECC.

---

## SAP Load Protection

| Setting | Default | Purpose |
|---------|---------|---------|
| `NCOREIQ_BULK_CONCURRENCY` | 3 | Parallel SAP calls per extraction |
| `NCOREIQ_JOB_WORKERS` | 2 | Concurrent job workers |
| `NCOREIQ_MAX_SAP_CONCURRENCY_PER_INSTANCE` | 5 | Hard cap on SAP calls per instance |
| `NCOREIQ_BULK_MAX_OBJECTS` | 250 | Objects per bulk extraction |

---

## Error Recovery

### SAP Unavailable

Symptom: `SAP_UNAVAILABLE` errors in logs, `/api/v1/sap/ping` returns 503.

Actions:
1. Verify SAP Gateway is accessible
2. Check `SAP_ODATA__BASE_URL` is correct
3. Check network/firewall rules
4. In BTP: verify Destination and Cloud Connector status

### Database Unavailable

Symptom: Job API returns 503, startup logs show DB errors.

Actions:
1. Check `NCOREIQ_JOB_DB_PATH` is writable (local mode)
2. Check `NCOREIQ_DB_URL` connectivity (production mode)
3. Verify disk space

### Artifact Missing

Symptom: `ARTIFACT_NOT_FOUND` or `ARTIFACT_EXPIRED` error.

Actions:
1. Check artifact has not expired (default 7 days)
2. Check `NCOREIQ_JOB_ARTIFACT_DIR` is accessible
3. Retry the extraction job

### Authentication Failure

Symptom: API returns 401, logs show `AUTHENTICATION_REQUIRED`.

Actions:
1. Verify `AUTH_MODE` is configured correctly
2. In BTP: verify XSUAA binding is active
3. Check token expiry and re-authenticate

---

## Diagnostics Bundle

To create a diagnostics bundle for support:

```bash
# Application info
curl http://localhost:8000/health

# Recent errors (check logs)
cf logs ncoreiq --recent | grep ERROR

# Job status summary
curl http://localhost:8000/api/v1/repository/jobs

# SAP connectivity
curl http://localhost:8000/api/v1/sap/ping
```

**Never include**:
- SAP passwords
- JWT tokens
- Client secrets
- Source code from customer systems

---

## Data Retention Policy

| Data Type | Default Retention | Configuration |
|-----------|------------------|---------------|
| Job records | 7 days after completion | `NCOREIQ_JOB_RETENTION_DAYS` |
| Artifacts (ZIP/JSON) | 7 days after creation | `NCOREIQ_ARTIFACT_RETENTION_DAYS` |
| Application logs | Platform-managed | BTP log retention policy |
| Audit records | Platform-managed | BTP audit log service |

Source code extracted and stored in artifacts is subject to the same retention policy. N-CoreIQ does not create a permanent copy of SAP source code unless explicitly configured with extended retention.
