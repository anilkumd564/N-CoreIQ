# N-CoreIQ â€” Troubleshooting Guide (Version 1.0.0)

## Quick Diagnostics

```bash
# 1. Check application health
curl http://localhost:8000/health

# 2. Check SAP connectivity
curl http://localhost:8000/api/v1/sap/ping

# 3. Check recent job status
curl http://localhost:8000/api/v1/repository/jobs?limit=10
```

---

## Troubleshooting Matrix

### SAP Connectivity

| Symptom | Error Code | Likely Cause | Action |
|---------|-----------|--------------|--------|
| `/api/v1/sap/ping` returns 503 | `SAP_UNAVAILABLE` | SAP Gateway unreachable | Check `SAP_ODATA__BASE_URL`, network/firewall |
| `/api/v1/sap/ping` returns 503 | `SAP_AUTHENTICATION_FAILED` | Bad credentials | Check `SAP_ODATA__USER` / `SAP_ODATA__PASSWORD` |
| TLS certificate error | `SAP_TLS_ERROR` | Untrusted SAP CA | Set `SAP_ODATA__CA_BUNDLE` to path of CA PEM file |
| Request timeout | `SAP_TIMEOUT` | Network latency | Increase `SAP_ODATA__TIMEOUT`, check network |
| 500 from SAP | `SAP_GATEWAY_ERROR` | SAP Gateway internal error | Check SAP Gateway logs (SM21, SLG1) |
| Invalid OData response | `SAP_RESPONSE_INVALID` | Service not activated | Re-activate ZNCOREIQ_REPO_SRV in `/n/IWFND/MAINT_SERVICE` |

### Repository Search

| Symptom | Cause | Action |
|---------|-------|--------|
| Search returns empty result | Package doesn't exist | Verify package name in SAP (SE80) |
| Search returns empty result | Object type filter too restrictive | Remove `object_types` filter |
| Search returns empty result | No Z*/Y* objects in package | Normal â€” package may contain only standard objects |
| `VALIDATION_ERROR` | No selector supplied | Provide `packages`, `name_patterns`, or set `all_custom_objects=true` |
| `SAP_OBJECT_NOT_FOUND` | Package not found | Verify package name (case-insensitive, auto-uppercased) |

### Source Extraction

| Symptom | Cause | Action |
|---------|-------|--------|
| Source not returned | Object type not supported | Check supported types: PROG, CLAS, INTF, FUGR, FUNC, TABL, DTEL, DOMA, VIEW, SHLP, ENQU, TRAN, MSAG |
| Source truncated | Exceeds `max_lines` | Increase `max_lines` parameter (up to 5000) |
| `REPOSITORY_OBJECT_NOT_FOUND` | Object doesn't exist | Verify object name in SAP (SE80) |
| Empty source | DDIC object with no source | DDIC objects return technical definitions, not ABAP source |
| Source contains ??? characters | Encoding issue | Source is UTF-8; SAP uses Latin-1 internally â€” contact support |

### Dependency Intelligence

| Symptom | Cause | Action |
|---------|-------|--------|
| Dependency response empty | No static dependencies detected | Object may have only dynamic dependencies |
| Dependency response empty | FM Z_MCP_REPO_DEPENDENCIES not deployed | Deploy latest SAP transport |
| `DEPENDENCY_LIMIT_EXCEEDED` | Traversal depth/nodes exceeded limits | Reduce `max_depth` or `max_nodes` |
| `REVERSE_SCOPE_TOO_LARGE` | Too many candidates | Narrow scope with `packages` or `name_patterns` |
| Dynamic dependencies missing | Expected â€” static analysis only | See known limitations |

### Extraction Jobs

| Symptom | Cause | Action |
|---------|-------|--------|
| Job stuck in RUNNING | Worker crashed or lease expired | Restart application â€” stale jobs auto-recover |
| Job stuck in QUEUED | No workers running | Check `NCOREIQ_DISABLE_EXECUTOR` is `false` |
| `JOB_NOT_FOUND` | Job ID incorrect or expired | Check job ID; jobs expire after `NCOREIQ_JOB_RETENTION_DAYS` |
| `ARTIFACT_NOT_FOUND` | Artifact expired | Re-run extraction job |
| `ARTIFACT_EXPIRED` | Past retention period | Extend `NCOREIQ_ARTIFACT_RETENTION_DAYS` or re-run |
| Job fails immediately | SAP config invalid | Check SAP connectivity, run `/api/v1/sap/ping` |

### Authentication & Authorization

| Symptom | Cause | Action |
|---------|-------|--------|
| 401 on all requests | Auth mode enabled, no token | Provide `Authorization: Bearer <token>` header |
| 401 `TOKEN_EXPIRED` | JWT has expired | Re-authenticate to obtain a new token |
| 401 `TOKEN_INVALID` | Malformed JWT or wrong issuer | Verify token issuer matches `AUTH__ISSUER` |
| 403 `AUTHORIZATION_DENIED` | Missing required scope | Grant appropriate scope in XSUAA |
| 401 in local mode | `AUTH_MODE` accidentally set to `xsuaa` | Set `AUTH_MODE=disabled` for local development |

### BTP Specific

| Symptom | Cause | Action |
|---------|-------|--------|
| Destination error | NC1_ODATA destination missing | Create destination in BTP Destination service |
| 502 from SAP via Cloud Connector | Cloud Connector not running | Start Cloud Connector; check CC administration |
| 502 from SAP via Cloud Connector | System mapping missing | Add system mapping in Cloud Connector |
| BTP XSUAA binding missing | Service not bound | `cf bind-service ncoreiq ncoreiq-xsuaa && cf restage ncoreiq` |
| Database connection failed | PostgreSQL not bound | Verify DB service binding in CF |
| Multiple instances duplicate jobs | DB not shared | Use shared PostgreSQL via `NCOREIQ_DB_URL` |

### Frontend UI

| Symptom | Cause | Action |
|---------|-------|--------|
| UI shows "SAP not connected" | Backend cannot reach SAP | Check `/api/v1/sap/ping` response |
| UI blank, no error | Frontend not built | Run `cd frontend && npm run build` |
| CORS error in browser | Origin not in allowed list | Add origin to `CORS_ALLOWED_ORIGINS` |
| Session expired | JWT expired | Re-login |
| Export fails in browser | File System Access API unsupported | Use ZIP download fallback |

---

## Common Log Patterns

### SAP Authentication Failure
```
WARNING event=sap_ping_auth_failed operation=sap_ping
```
**Fix**: Verify `SAP_ODATA__USER` and `SAP_ODATA__PASSWORD` in `.env`.

### Stale Job Recovery
```
WARNING event=stale_jobs_recovered count=2
```
**Normal**: Indicates previous instance crashed; jobs re-queued automatically.

### Job Executor Stopped
```
INFO event=executor_skipped reason=ncoreiq_disable_executor=true
```
**Expected in test mode**: Set `NCOREIQ_DISABLE_EXECUTOR=false` for production.

### DB Write Error
```
ERROR event=startup_failed
```
**Fix**: Check disk space, file permissions on `NCOREIQ_JOB_DB_PATH`.

---

## Correlation ID Usage

Every request generates a `correlation_id` (logged and returned in error responses). Use it to trace a request across all log entries:

```bash
# Find all logs for a specific correlation ID
cf logs ncoreiq --recent | grep "correlation_id=abc-123"
```

Error responses include the correlation ID:
```json
{
  "code": "SAP_UNAVAILABLE",
  "message": "SAP ECC cannot be reached.",
  "correlation_id": "abc-123"
}
```

---

## Support Bundle Checklist

When contacting support, collect:

- [ ] Application version: `curl http://localhost:8000/ | jq .version`
- [ ] Health output: `curl http://localhost:8000/health`
- [ ] SAP ping result: `curl http://localhost:8000/api/v1/sap/ping`
- [ ] Error code and correlation_id from failing request
- [ ] Recent log excerpt (ERROR and WARNING level only)
- [ ] `APP_ENV` value (local / btp)
- [ ] `SAP_CONNECTION_MODE` value

**Do NOT include**:
- SAP passwords
- JWT tokens or client secrets
- Source code from SAP
- Customer data
