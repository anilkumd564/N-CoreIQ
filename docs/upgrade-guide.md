# N-CoreIQ — Upgrade Guide: 0.9.x → 1.0.0

## Overview

Version 1.0.0 is a hardening release. There are **no breaking API changes** from 0.9.x. The upgrade is safe and non-destructive.

---

## Pre-Upgrade Checklist

- [ ] Back up the SQLite database: `cp ncoreiq_jobs.db ncoreiq_jobs.db.backup`
- [ ] Note current running jobs — they will be recovered automatically
- [ ] Capture current configuration from `.env` or CF environment variables
- [ ] Verify disk space: `df -h ./job_artifacts`

---

## Upgrade Steps

### 1. Update Code

```bash
git pull origin main
# Or: git checkout v1.0.0
```

### 2. Update Python Dependencies

```bash
pip install -r requirements.txt
```

No new required dependencies in 1.0.0. Existing packages receive patch-level updates only.

### 3. Update Frontend

```bash
cd frontend
npm install
npm run build
```

The frontend version in `package.json` is now `1.0.0`.

### 4. Database Migration

**No migration required.** The SQLite schema is unchanged from 0.9.x.

Jobs in `RUNNING` state at time of upgrade are automatically recovered to `QUEUED` on next startup.

### 5. Configuration Changes

**No breaking configuration changes.**

New optional settings (all have safe defaults):
- None required for basic operation

Verify your existing `.env` settings are still correct:

```bash
# Check current settings load without error
python -c "from app.core.config import get_settings; s = get_settings(); print('OK:', s.app_version)"
```

Expected output: `OK: 1.0.0`

### 6. SAP Transport

**No new SAP transport required for 1.0.0.**

The OData service (`ZNCOREIQ_REPO_SRV`) and function modules are unchanged from 0.9.x.

### 7. Restart Application

#### Local:
```bash
# Stop existing instance (Ctrl+C)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

#### BTP Cloud Foundry:
```bash
cf push -f deployment/btp/manifest.yml
# Or rolling restart:
cf restart ncoreiq
```

---

## Post-Upgrade Verification

```bash
# 1. Verify version
curl http://localhost:8000/ | python -m json.tool
# Expected: "version": "1.0.0"

# 2. Verify SAP connectivity
curl http://localhost:8000/api/v1/sap/ping

# 3. Check job recovery
curl http://localhost:8000/health
# Expected: job_store showing active jobs if any were running

# 4. Run automated tests
py -m pytest tests/ -q
```

---

## Rollback Procedure

If rollback to 0.9.x is required:

```bash
# 1. Stop 1.0.0 instance
# 2. Restore from backup
cp ncoreiq_jobs.db.backup ncoreiq_jobs.db
# 3. Checkout previous version
git checkout v0.9.x
# 4. Restart
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

The database schema is backward-compatible so 0.9.x will read jobs created by 1.0.0.

---

## Known Differences from 0.9.x

| Area | 0.9.x | 1.0.0 |
|------|-------|-------|
| `app_version` | `0.9.0` | `1.0.0` |
| `ncoreiq_mcp_server_version` | `0.9.0` | `1.0.0` |
| Frontend `package.json` version | `0.8.5` | `1.0.0` |
| Error model | String detail in HTTPException | Canonical `APIError` model documented |
| SAP allow-list | `RFC_SYSTEM_INFO` undocumented | Now documented with rationale |
| Dependency taxonomy | Undocumented | Formally frozen |
| Documentation | Phase docs only | Full installation/config/ops/troubleshooting guides |

---

## Support

If you encounter issues during upgrade:

1. Check `docs/troubleshooting.md`
2. Review application logs: `LOG_LEVEL=DEBUG` for verbose output
3. Verify SAP connectivity: `GET /api/v1/sap/ping`
4. Open a support request with the diagnostics bundle (see `docs/operations.md`)
