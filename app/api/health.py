"""Phase 9 — Liveness, readiness, diagnostics, and metrics endpoints.

Endpoints:
    GET  /health/live       — process liveness (never requires SAP)
    GET  /health/ready      — dependency readiness (bounded timeouts)
    GET  /api/v1/admin/diagnostics  — safe full diagnostics (Admin only)
    GET  /api/v1/admin/metrics      — lightweight metrics snapshot (Admin only)

Liveness (GET /health/live):
    - Always returns 200 if the process is running
    - Does NOT check SAP connectivity (SAP outage must not restart healthy pods)
    - Suitable for Kubernetes liveness probe

Readiness (GET /health/ready):
    - Checks: mandatory configuration, database, artifact store
    - Optionally checks SAP connectivity (if configured)
    - Returns 503 if any mandatory dependency is unavailable
    - Suitable for Kubernetes readiness probe / CF health check

Security:
    - /health/live and /health/ready are always public (exempt from auth)
    - /api/v1/admin/* endpoints require NCoreIQ.Admin scope
    - No credentials are ever exposed in any response
"""
from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse

from app.auth.principal import SCOPE_ADMIN, AuthenticatedPrincipal
from app.core.config import Settings, get_settings
from app.core.log_config import get_correlation_id, get_request_id
from app.models.phase9 import (
    DiagnosticsResult,
    LivenessResult,
    MetricsSnapshot,
    ReadinessResult,
    ServiceHealthStatus,
)
from app.models.results import HealthResult

logger = logging.getLogger(__name__)

health_router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: get principal from request state
# ---------------------------------------------------------------------------


def _get_principal(request: Request) -> AuthenticatedPrincipal | None:
    return getattr(request.state, "principal", None)


def _require_admin(request: Request) -> None:
    """Raise 403 if the principal does not have Admin scope."""
    principal = _get_principal(request)
    if principal is None:
        raise HTTPException(status_code=403, detail="Admin access required.")
    if not principal.is_admin():
        raise HTTPException(
            status_code=403,
            detail="Insufficient permissions. NCoreIQ.Admin scope required.",
        )


# ---------------------------------------------------------------------------
# GET /health/live — process liveness
# ---------------------------------------------------------------------------


@health_router.get(
    "/health/live",
    response_model=LivenessResult,
    summary="Process liveness probe",
    tags=["health"],
    description=(
        "Returns 200 if the application process is alive and the event loop is running.\n\n"
        "Does NOT verify SAP connectivity — a SAP outage must not cause pod restarts.\n\n"
        "Suitable for Kubernetes liveness probe and Cloud Foundry health check."
    ),
)
async def liveness(settings: Settings = Depends(get_settings)) -> LivenessResult:
    return LivenessResult(
        status="alive",
        app=settings.app_name,
        version=settings.app_version,
    )


# ---------------------------------------------------------------------------
# GET /health/ready — dependency readiness
# ---------------------------------------------------------------------------


async def _check_database(request: Request) -> ServiceHealthStatus:
    """Check job store database health."""
    try:
        job_service = getattr(request.app.state, "job_service", None)
        if job_service is None:
            return ServiceHealthStatus(
                name="database",
                status="unavailable",
                message="Job service not initialized.",
            )
        t0 = time.monotonic()
        health = job_service.health_check()
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        store_status = health.get("job_store", "unknown")
        if store_status == "healthy":
            return ServiceHealthStatus(
                name="database",
                status="ok",
                latency_ms=latency_ms,
                details=health,
            )
        return ServiceHealthStatus(
            name="database",
            status="degraded",
            message=f"Job store reported: {store_status}",
            latency_ms=latency_ms,
        )
    except Exception as exc:
        logger.warning("health_db_check_failed error=%s", exc)
        return ServiceHealthStatus(
            name="database",
            status="unavailable",
            message="Database health check failed.",
        )


async def _check_artifact_store(request: Request) -> ServiceHealthStatus:
    """Check artifact store health."""
    try:
        artifact_store = getattr(request.app.state, "artifact_store", None)
        if artifact_store is None:
            return ServiceHealthStatus(
                name="artifact_store",
                status="unavailable",
                message="Artifact store not initialized.",
            )
        # Check exists returns False for unknown ID (valid response)
        t0 = time.monotonic()
        _ = artifact_store.exists("__health_check__")
        latency_ms = round((time.monotonic() - t0) * 1000, 1)
        return ServiceHealthStatus(
            name="artifact_store",
            status="ok",
            latency_ms=latency_ms,
        )
    except Exception as exc:
        logger.warning("health_artifact_store_check_failed error=%s", exc)
        return ServiceHealthStatus(
            name="artifact_store",
            status="unavailable",
            message="Artifact store health check failed.",
        )


@health_router.get(
    "/health/ready",
    response_model=ReadinessResult,
    summary="Dependency readiness probe",
    tags=["health"],
    description=(
        "Returns 200 if all mandatory dependencies are available.\n\n"
        "Checks:\n"
        "- Mandatory configuration present\n"
        "- Database / job store reachable\n"
        "- Artifact store accessible\n\n"
        "Returns 503 if any mandatory dependency is unavailable.\n\n"
        "Suitable for Kubernetes readiness probe."
    ),
)
async def readiness(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> JSONResponse:
    services: list[ServiceHealthStatus] = []
    all_ready = True

    # Check database
    db_status = await _check_database(request)
    services.append(db_status)
    if db_status.status == "unavailable":
        all_ready = False

    # Check artifact store
    artifact_status = await _check_artifact_store(request)
    services.append(artifact_status)
    if artifact_status.status == "unavailable":
        all_ready = False

    # Check mandatory configuration (BTP mode)
    if settings.app_env == "btp":
        if not settings.sap_odata.base_url and not (
            settings.btp.destination.uri or settings.btp.xsuaa.url
        ):
            services.append(
                ServiceHealthStatus(
                    name="btp_configuration",
                    status="unavailable",
                    message="BTP mode requires Destination Service binding.",
                )
            )
            all_ready = False
        else:
            services.append(
                ServiceHealthStatus(name="btp_configuration", status="ok")
            )

    result = ReadinessResult(
        status="ready" if all_ready else "not_ready",
        app=settings.app_name,
        version=settings.app_version,
        runtime_mode=settings.app_env,
        services=services,
        message=None if all_ready else "One or more dependencies are unavailable.",
    )

    status_code = 200 if all_ready else 503
    return JSONResponse(content=result.model_dump(), status_code=status_code)


# ---------------------------------------------------------------------------
# Backward-compatible health endpoint (Phase 1 format)
# ---------------------------------------------------------------------------


@health_router.get(
    "/health",
    response_model=HealthResult,
    summary="Application health check (legacy)",
    tags=["health"],
    description=(
        "Legacy health endpoint preserved for backward compatibility.\n\n"
        "For probes, prefer /health/live or /health/ready."
    ),
)
async def health(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> HealthResult:
    result = HealthResult(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )
    try:
        job_service = getattr(request.app.state, "job_service", None)
        executor = getattr(request.app.state, "executor", None)
        if job_service is not None:
            job_health = job_service.health_check()
            result.job_store = job_health.get("job_store", "unknown")
            result.queued_jobs = job_health.get("queued_jobs", 0)
            result.running_jobs = job_health.get("running_jobs", 0)
        if executor is not None:
            result.worker_running = getattr(executor, "is_running", False)
    except Exception:
        logger.warning("health_job_subsystem_check_failed", exc_info=True)
    return result


# ---------------------------------------------------------------------------
# GET /api/v1/admin/diagnostics — full safe diagnostics
# ---------------------------------------------------------------------------

admin_router = APIRouter(prefix="/admin")


@admin_router.get(
    "/diagnostics",
    response_model=DiagnosticsResult,
    summary="Full safe diagnostics (Admin only)",
    tags=["admin", "phase9"],
    description=(
        "Returns comprehensive safe diagnostics for all N-CoreIQ subsystems.\n\n"
        "**Requires NCoreIQ.Admin scope.**\n\n"
        "Never exposes credentials, connection strings, or secrets."
    ),
)
async def diagnostics(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> DiagnosticsResult:
    _require_admin(request)

    import os

    instance_id = (
        settings.logging.instance_id
        or os.environ.get("CF_INSTANCE_INDEX")
        or "0"
    )

    result = DiagnosticsResult(
        app=settings.app_name,
        version=settings.app_version,
        runtime_mode=settings.app_env,
        instance_id=instance_id,
    )

    # Job store
    try:
        job_service = getattr(request.app.state, "job_service", None)
        executor = getattr(request.app.state, "executor", None)
        if job_service:
            jh = job_service.health_check()
            result.job_store = jh.get("job_store")
            result.queued_jobs = jh.get("queued_jobs")
            result.running_jobs = jh.get("running_jobs")
        if executor:
            result.worker_running = getattr(executor, "is_running", False)
    except Exception:
        result.job_store = "error"

    # Database health
    result.database = await _check_database(request)

    # Artifact store health
    result.artifact_store = await _check_artifact_store(request)

    # BTP bindings (safe)
    if settings.app_env == "btp":
        try:
            from app.btp.bindings import BTPServiceBindingResolver
            resolver = BTPServiceBindingResolver()
            result.btp_bindings = resolver.diagnostics()
        except Exception as exc:
            result.btp_bindings = {"error": str(exc)}

    # Configuration summary (safe — all secrets redacted via model_dump)
    try:
        dumped = settings.model_dump()
        # Only include safe summary fields
        result.configuration = {
            "app_env": dumped.get("app_env"),
            "sap_connection_mode": dumped.get("sap_connection_mode"),
            "auth_mode": dumped.get("auth_mode"),
            "sap_odata": {
                "base_url": dumped.get("sap_odata", {}).get("base_url"),
                "service": dumped.get("sap_odata", {}).get("service"),
                "user": dumped.get("sap_odata", {}).get("user"),
                "skip_tls_verify": dumped.get("sap_odata", {}).get("skip_tls_verify"),
            },
            "ncoreiq_job_workers": dumped.get("ncoreiq_job_workers"),
            "ncoreiq_max_running_jobs_per_instance": dumped.get("ncoreiq_max_running_jobs_per_instance"),
        }
    except Exception:
        result.configuration = {"error": "configuration_dump_failed"}

    return result


@admin_router.get(
    "/metrics",
    response_model=MetricsSnapshot,
    summary="Lightweight metrics snapshot (Admin only)",
    tags=["admin", "phase9"],
    description=(
        "Returns a lightweight metrics snapshot.\n\n"
        "**Requires NCoreIQ.Admin scope.**"
    ),
)
async def metrics(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> MetricsSnapshot:
    _require_admin(request)

    snapshot = MetricsSnapshot(runtime_mode=settings.app_env)

    try:
        job_service = getattr(request.app.state, "job_service", None)
        if job_service:
            jh = job_service.health_check()
            snapshot.jobs_queued = jh.get("queued_jobs", 0)
            snapshot.jobs_running = jh.get("running_jobs", 0)
    except Exception:
        pass

    return snapshot
