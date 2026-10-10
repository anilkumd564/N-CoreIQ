"""Phase 9 — Production readiness models.

Health check results, diagnostic models, audit event models, and metrics.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Health check models
# ---------------------------------------------------------------------------


class ServiceHealthStatus(BaseModel):
    """Health status for a single service dependency."""

    name: str
    status: Literal["ok", "degraded", "unavailable", "unknown"] = "unknown"
    message: str | None = None
    latency_ms: float | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class LivenessResult(BaseModel):
    """Response for GET /health/live — process-level liveness check."""

    status: Literal["alive", "dead"] = "alive"
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    app: str = "N-CoreIQ"
    version: str = "0.9.0"


class ReadinessResult(BaseModel):
    """Response for GET /health/ready — dependency readiness check."""

    status: Literal["ready", "not_ready"] = "ready"
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    app: str = "N-CoreIQ"
    version: str = "0.9.0"
    runtime_mode: str = "local"
    services: list[ServiceHealthStatus] = Field(default_factory=list)
    message: str | None = None


# ---------------------------------------------------------------------------
# Diagnostics model
# ---------------------------------------------------------------------------


class DiagnosticsResult(BaseModel):
    """Safe diagnostics response for GET /api/v1/admin/diagnostics.

    Admin-only endpoint. Never exposes credentials.
    """

    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    app: str = "N-CoreIQ"
    version: str = "0.9.0"
    runtime_mode: str = "local"
    instance_id: str | None = None

    # Service connectivity
    database: ServiceHealthStatus | None = None
    artifact_store: ServiceHealthStatus | None = None
    destination_service: ServiceHealthStatus | None = None
    connectivity_service: ServiceHealthStatus | None = None
    cloud_connector: ServiceHealthStatus | None = None
    sap_gateway: ServiceHealthStatus | None = None

    # BTP bindings (safe — no credentials)
    btp_bindings: dict[str, Any] | None = None

    # Configuration summary (safe — no secrets)
    configuration: dict[str, Any] | None = None

    # Job subsystem
    job_store: str | None = None
    queued_jobs: int | None = None
    running_jobs: int | None = None
    worker_running: bool | None = None


# ---------------------------------------------------------------------------
# Audit event model
# ---------------------------------------------------------------------------


class AuditEvent(BaseModel):
    """Audit log record for security-relevant operations.

    Persisted to the application log stream in structured format.
    NEVER includes SAP source code or user credentials.
    """

    event_type: str
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    principal_subject: str | None = None
    principal_display_name: str | None = None
    action: str
    resource_type: str | None = None
    resource_id: str | None = None
    result: Literal["success", "failure", "denied"] = "success"
    request_id: str | None = None
    correlation_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Production API error model
# ---------------------------------------------------------------------------


class ProductionErrorResponse(BaseModel):
    """Sanitized API error response.

    Never exposes:
    - Python tracebacks
    - SAP innererror details
    - Database connection strings
    - BTP service binding data
    - Destination tokens
    - Local filesystem paths
    """

    error: str
    message: str
    request_id: str | None = None
    correlation_id: str | None = None
    # Optional guidance for the caller
    hint: str | None = None


# ---------------------------------------------------------------------------
# Metrics snapshot model
# ---------------------------------------------------------------------------


class MetricsSnapshot(BaseModel):
    """Lightweight application metrics snapshot."""

    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )
    runtime_mode: str = "local"

    # API metrics
    api_requests_total: int = 0
    api_errors_total: int = 0

    # SAP metrics
    sap_requests_total: int = 0
    sap_errors_total: int = 0
    sap_latency_p50_ms: float | None = None
    sap_latency_p95_ms: float | None = None

    # Job metrics
    jobs_queued: int = 0
    jobs_running: int = 0
    jobs_succeeded_total: int = 0
    jobs_failed_total: int = 0

    # Artifact metrics
    artifacts_stored: int = 0
    artifacts_total_bytes: int = 0
