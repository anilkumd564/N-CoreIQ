"""Phase 6 — Extraction Job Models.

Domain models for persistent asynchronous extraction jobs.

Architecture:
    ExtractionJobRequest  →  ExtractionJobService  →  ExtractionJob
    ExtractionJob has: status, stage, progress, result_status, error, artifact

State machine:
    QUEUED → RUNNING → SUCCEEDED
    QUEUED → CANCELLED
    RUNNING → CANCEL_REQUESTED → CANCELLED
    RUNNING → FAILED
    RUNNING → SUCCEEDED

Retry creates a NEW job with retry_of_job_id set to the original.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.phase5 import BulkExtractionRequest  # Phase 5 canonical type


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class JobStatus(str, Enum):
    """Job lifecycle state machine.

    Terminal states: CANCELLED, SUCCEEDED, FAILED
    """
    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    CANCEL_REQUESTED = "CANCEL_REQUESTED"
    CANCELLED = "CANCELLED"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


class JobStage(str, Enum):
    """Processing stage for operational visibility.

    Tracks what the executor is currently doing without affecting the main state machine.
    """
    VALIDATING = "VALIDATING"
    DISCOVERY = "DISCOVERY"
    EXTRACTING = "EXTRACTING"
    EXPORTING = "EXPORTING"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"


class ExportJobFormat(str, Enum):
    """Export format requested in job creation."""
    JSON = "json"
    ZIP = "zip"
    NONE = "none"


# Alias used in artifact model and tests
JobArtifactFormat = ExportJobFormat


# ---------------------------------------------------------------------------
# Valid state transitions
# ---------------------------------------------------------------------------

VALID_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.CANCEL_REQUESTED, JobStatus.SUCCEEDED, JobStatus.FAILED},
    JobStatus.CANCEL_REQUESTED: {JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.CANCELLED: set(),   # terminal
    JobStatus.SUCCEEDED: set(),   # terminal
    JobStatus.FAILED: set(),      # terminal
}

TERMINAL_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.CANCELLED, JobStatus.SUCCEEDED, JobStatus.FAILED}
)

RETRYABLE_STATUSES: frozenset[JobStatus] = frozenset(
    {JobStatus.FAILED, JobStatus.CANCELLED}
)


def is_valid_transition(from_status: JobStatus, to_status: JobStatus) -> bool:
    """Return True if transitioning from_status → to_status is permitted."""
    return to_status in VALID_TRANSITIONS.get(from_status, set())


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class ExtractionJobRequest(BaseModel):
    """Job creation request.

    Wraps BulkExtractionRequest to add export format.
    The extraction field is the canonical Phase 5 request.
    """
    extraction: BulkExtractionRequest = Field(
        ...,
        description="Phase 5 BulkExtractionRequest payload.",
    )
    export_format: ExportJobFormat = Field(
        default=ExportJobFormat.ZIP,
        description="Export format: json, zip, or none.",
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description=(
            "Optional client idempotency key. If the same key + request is "
            "resubmitted the existing job is returned (HTTP 200). If the same "
            "key is reused with a different request, HTTP 409 is returned."
        ),
    )

    def compute_request_hash(self) -> str:
        """SHA-256 of the normalised extraction request (without secrets)."""
        # Serialise extraction to a stable canonical form.
        if hasattr(self.extraction, "model_dump"):
            raw = self.extraction.model_dump(mode="json")
        else:
            raw = self.extraction
        # Include export_format in the hash so same objects + different format
        # produces a different hash.
        canonical = {
            "extraction": raw,
            "export_format": self.export_format.value,
        }
        canonical_str = json.dumps(canonical, sort_keys=True, ensure_ascii=True, default=str)
        return hashlib.sha256(canonical_str.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Progress model
# ---------------------------------------------------------------------------


class ExtractionJobProgress(BaseModel):
    """Snapshot of job execution progress.

    Reported periodically by the executor and persisted in the job store.
    percent_complete is always computed from total/processed; it is never
    reported as 100% unless current_stage is COMPLETED.
    """
    total_objects: int = 0
    processed_objects: int = 0
    successful_objects: int = 0
    partial_objects: int = 0
    failed_objects: int = 0
    skipped_objects: int = 0
    truncated_objects: int = 0
    percent_complete: float = 0.0
    current_object_type: str | None = None
    current_object_name: str | None = None
    current_stage: str | None = None
    message: str | None = None

    @model_validator(mode="after")
    def _compute_percent(self) -> "ExtractionJobProgress":
        """Recompute percent_complete from total/processed each time the model is built."""
        finalized = self.current_stage == "COMPLETED" or self.current_stage == JobStage.COMPLETED
        if self.total_objects > 0:
            raw = (self.processed_objects / self.total_objects) * 100.0
            self.percent_complete = 100.0 if finalized else min(round(raw, 1), 99.0)
        else:
            self.percent_complete = 100.0 if finalized else 0.0
        return self

    @classmethod
    def calculate(
        cls,
        total: int,
        processed: int,
        successful: int = 0,
        partial: int = 0,
        failed: int = 0,
        skipped: int = 0,
        truncated: int = 0,
        current_object_type: str | None = None,
        current_object_name: str | None = None,
        current_stage: str | None = None,
        message: str | None = None,
        finalized: bool = False,
    ) -> "ExtractionJobProgress":
        """Calculate progress, never reporting 100% until finalized=True."""
        if total > 0:
            raw_pct = (processed / total) * 100.0
            # Cap at 99% until explicitly finalized
            pct = min(raw_pct, 99.0) if not finalized else 100.0
        else:
            pct = 0.0 if not finalized else 100.0

        return cls(
            total_objects=total,
            processed_objects=processed,
            successful_objects=successful,
            partial_objects=partial,
            failed_objects=failed,
            skipped_objects=skipped,
            truncated_objects=truncated,
            percent_complete=round(pct, 1),
            current_object_type=current_object_type,
            current_object_name=current_object_name,
            current_stage=current_stage,
            message=message,
        )


# ---------------------------------------------------------------------------
# Error model
# ---------------------------------------------------------------------------


class JobError(BaseModel):
    """Safe infrastructure-level error representation.

    Must NEVER expose: stack traces, SAP credentials, raw innererror,
    authorization headers, or filesystem paths.
    """
    code: str
    message: str
    stage: str | None = None
    retryable: bool = False
    timestamp: datetime = Field(
        default_factory=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Artifact model
# ---------------------------------------------------------------------------


class JobArtifact(BaseModel):
    """Metadata about a generated export artifact.

    The internal filesystem path is NEVER exposed.
    """
    artifact_id: str
    format: ExportJobFormat
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    expires_at: datetime | None = None


# ---------------------------------------------------------------------------
# Job model
# ---------------------------------------------------------------------------


class ExtractionJob(BaseModel):
    """Full extraction job domain model.

    This is the canonical representation used throughout the application.
    The JobStore persists and retrieves jobs as this model.
    """
    job_id: str
    status: JobStatus
    stage: JobStage | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime
    created_by: str | None = None

    # Stored request snapshot.  May be ExtractionJobRequest or a plain dict
    # when rehydrated from the job store (JSON round-trip).
    request: Any

    # Progress (updated periodically while RUNNING)
    progress: ExtractionJobProgress = Field(default_factory=ExtractionJobProgress)

    # Phase 5 extraction outcome (separate from job infrastructure status)
    result_status: str | None = None  # e.g. "COMPLETED", "COMPLETED_WITH_ERRORS", "FAILED"

    # Safe error (only if FAILED/CANCELLED with error info)
    error: JobError | None = None

    # Export artifact (if export_format != none and job reached SUCCEEDED)
    artifact: JobArtifact | None = None

    # Retry chain
    retry_of_job_id: str | None = None
    attempt: int = 1

    # Worker tracking (internal - not exposed in API responses directly)
    worker_id: str | None = None
    heartbeat_at: datetime | None = None
    lease_expires_at: datetime | None = None

    # Idempotency
    idempotency_key: str | None = None
    request_hash: str | None = None

    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES

    def can_cancel(self) -> bool:
        return self.status in (JobStatus.QUEUED, JobStatus.RUNNING)

    def can_retry(self) -> bool:
        return self.status in RETRYABLE_STATUSES


# ---------------------------------------------------------------------------
# API response models
# ---------------------------------------------------------------------------


class JobCreatedResponse(BaseModel):
    """Response returned when a job is successfully created (HTTP 202)."""
    job_id: str
    status: JobStatus
    created_at: datetime
    status_url: str
    idempotency_key: str | None = None


class JobStatusResponse(BaseModel):
    """Response for GET /jobs/{job_id}."""
    job_id: str
    status: JobStatus
    stage: JobStage | None = None
    created_at: datetime
    started_at: datetime | None = None
    completed_at: datetime | None = None
    updated_at: datetime
    created_by: str | None = None
    progress: ExtractionJobProgress
    result_status: str | None = None
    error: JobError | None = None
    artifact: JobArtifact | None = None
    retry_of_job_id: str | None = None
    attempt: int = 1


class JobListResponse(BaseModel):
    """Response for GET /jobs."""
    items: list[JobStatusResponse]
    total: int
    limit: int
    offset: int


class JobListFilter(BaseModel):
    """Query filters for GET /jobs."""
    status: JobStatus | None = None
    created_after: datetime | None = None
    created_before: datetime | None = None
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0)


# ---------------------------------------------------------------------------
# Health fragment
# ---------------------------------------------------------------------------


class JobSubsystemHealth(BaseModel):
    """Health info for the job subsystem (included in GET /health)."""
    job_store: str = "unknown"
    worker_running: bool = False
    queued_jobs: int = 0
    running_jobs: int = 0
