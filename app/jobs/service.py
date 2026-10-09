"""Phase 6 — ExtractionJobService: orchestrates job lifecycle."""

from __future__ import annotations

import hashlib
import json
import logging
import uuid
from datetime import datetime, timezone

from app.jobs.artifacts import ArtifactStore
from app.jobs.store import JobStore
from app.models.phase6 import (
    ExtractionJob,
    ExtractionJobProgress,
    ExtractionJobRequest,
    JobArtifact,
    JobArtifactFormat,
    JobError,
    JobListFilter,
    JobStage,
    JobStatus,
    RETRYABLE_STATUSES,
    TERMINAL_STATUSES,
)

logger = logging.getLogger(__name__)
_UTC = timezone.utc

# Valid state transitions
_TRANSITIONS: dict[JobStatus, set[JobStatus]] = {
    JobStatus.QUEUED: {JobStatus.RUNNING, JobStatus.CANCELLED},
    JobStatus.RUNNING: {JobStatus.CANCEL_REQUESTED, JobStatus.SUCCEEDED, JobStatus.FAILED},
    JobStatus.CANCEL_REQUESTED: {JobStatus.CANCELLED, JobStatus.FAILED},
    JobStatus.CANCELLED: set(),
    JobStatus.SUCCEEDED: set(),
    JobStatus.FAILED: set(),
}


def _now() -> datetime:
    return datetime.now(_UTC)


def _request_hash(request: ExtractionJobRequest) -> str:
    payload = json.dumps(request.model_dump(mode="json"), sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


class JobTransitionError(ValueError):
    """Raised when an invalid state transition is attempted."""


class JobNotFoundError(KeyError):
    """Raised when a job_id does not exist (internal use; API layer converts to 404)."""


class JobConflictError(ValueError):
    """Raised when an idempotency key conflict is detected (HTTP 409)."""


class ExtractionJobService:
    """High-level job management service.

    Orchestrates job lifecycle through JobStore and ArtifactStore.
    Does NOT access SAP directly — all extraction is delegated to
    BulkExtractionService via the executor layer.
    """

    def __init__(self, job_store: JobStore, artifact_store: ArtifactStore) -> None:
        self._store = job_store
        self._artifacts = artifact_store

    # ------------------------------------------------------------------
    # Creation
    # ------------------------------------------------------------------

    def create_job(
        self,
        request: ExtractionJobRequest,
        created_by: str | None = None,
        idempotency_key: str | None = None,
    ) -> ExtractionJob:
        req_hash = _request_hash(request)

        # Idempotency check
        if idempotency_key:
            existing = self._store.find_by_idempotency_key(idempotency_key)
            if existing is not None:
                if existing.request_hash != req_hash:
                    raise JobConflictError(
                        f"Conflict: Idempotency key '{idempotency_key}' was already used "
                        "with a different request."
                    )
                logger.info(
                    "event=idempotency_hit job_id=%s key=%s",
                    existing.job_id, idempotency_key,
                )
                return existing

        now = _now()
        job = ExtractionJob(
            job_id=str(uuid.uuid4()),
            status=JobStatus.QUEUED,
            stage=None,
            created_at=now,
            updated_at=now,
            created_by=created_by,
            request=request.model_dump(mode="json"),
            progress=ExtractionJobProgress(),
            idempotency_key=idempotency_key,
            request_hash=req_hash,
        )
        self._store.create_job(job)
        logger.info(
            "event=job_created job_id=%s created_by=%s", job.job_id, created_by
        )
        return job

    # ------------------------------------------------------------------
    # Retrieval
    # ------------------------------------------------------------------

    def get_job(self, job_id: str) -> ExtractionJob | None:
        """Return the job, or None if not found."""
        return self._store.get_job(job_id)

    def list_jobs(
        self,
        status: str | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ):
        """List jobs with optional filters. Returns a JobListResult."""
        return self._store.list_jobs(
            status=status,
            created_after=created_after,
            created_before=created_before,
            limit=limit,
            offset=offset,
        )

    # ------------------------------------------------------------------
    # Cancellation
    # ------------------------------------------------------------------

    def cancel_job(self, job_id: str) -> ExtractionJob:
        job = self._store.get_job(job_id)
        if job is None:
            raise ValueError(f"Job '{job_id}' not found.")

        # Idempotent: already cancelled
        if job.status == JobStatus.CANCELLED:
            return job

        # Terminal states other than CANCELLED cannot be cancelled
        if job.status in TERMINAL_STATUSES:
            raise ValueError(
                f"Job {job_id} is already in terminal state {job.status.value}."
            )

        # CANCEL_REQUESTED — already being cancelled
        if job.status == JobStatus.CANCEL_REQUESTED:
            return job

        # QUEUED → CANCELLED (direct transition via store)
        if job.status == JobStatus.QUEUED:
            result = self._store.cancel_queued_job(job_id)
            if result is None:
                # Race condition: job was already claimed, re-fetch
                result = self._store.get_job(job_id)
            logger.info(
                "event=cancel_requested job_id=%s new_status=%s",
                job_id, result.status.value if result else "unknown",
            )
            return result or job

        # RUNNING → CANCEL_REQUESTED
        if job.status == JobStatus.RUNNING:
            self._store.request_cancel(job_id)
            result = self._store.get_job(job_id)
            logger.info(
                "event=cancel_requested job_id=%s new_status=%s",
                job_id, result.status.value if result else "unknown",
            )
            return result or job

        raise ValueError(
            f"Cannot cancel job {job_id} in state {job.status.value}."
        )

    # ------------------------------------------------------------------
    # Retry
    # ------------------------------------------------------------------

    def retry_job(
        self,
        job_id: str,
        created_by: str | None = None,
    ) -> ExtractionJob:
        original = self._store.get_job(job_id)
        if original is None:
            raise ValueError(f"Job '{job_id}' not found.")
        if original.status not in RETRYABLE_STATUSES:
            raise ValueError(
                f"Job {job_id} is not in a retryable state (current={original.status.value}). "
                f"Only {[s.value for s in RETRYABLE_STATUSES]} are retryable."
            )
        now = _now()
        new_job = ExtractionJob(
            job_id=str(uuid.uuid4()),
            status=JobStatus.QUEUED,
            stage=None,
            created_at=now,
            updated_at=now,
            created_by=created_by or original.created_by,
            request=original.request,
            progress=ExtractionJobProgress(),
            retry_of_job_id=original.job_id,
            attempt=original.attempt + 1,
            request_hash=original.request_hash,
        )
        self._store.create_job(new_job)
        logger.info(
            "event=job_retried original_job_id=%s new_job_id=%s attempt=%d",
            job_id, new_job.job_id, new_job.attempt,
        )
        return new_job

    # ------------------------------------------------------------------
    # Result & Artifact retrieval
    # ------------------------------------------------------------------

    def get_result(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status not in TERMINAL_STATUSES:
            raise ValueError(
                f"Job {job_id} is not yet complete (status={job.status.value})."
            )
        return {
            "job_id": job.job_id,
            "status": job.status.value,
            "stage": job.stage.value if job.stage else None,
            "result_status": job.result_status,
            "progress": job.progress.model_dump() if job.progress else None,
            "error": job.error.model_dump() if job.error else None,
            "artifact": job.artifact.model_dump() if job.artifact else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
        }

    def get_artifact_stream(self, job_id: str):
        """Returns (stream, artifact) for download, or raises."""
        job = self.get_job(job_id)
        if job is None:
            raise KeyError(job_id)
        if job.status not in TERMINAL_STATUSES:
            raise ValueError(
                f"Job {job_id} is not yet complete (status={job.status.value})."
            )
        if job.artifact is None:
            raise FileNotFoundError(f"No artifact available for job {job_id}.")
        artifact_id = job.artifact.artifact_id
        if not self._artifacts.exists(artifact_id):
            raise LookupError(f"Artifact for job {job_id} has expired or been deleted.")
        stream = self._artifacts.open(artifact_id)
        return stream, job.artifact

    # ------------------------------------------------------------------
    # Transition validation helper (used by executor)
    # ------------------------------------------------------------------

    @staticmethod
    def validate_transition(current: JobStatus, target: JobStatus) -> None:
        allowed = _TRANSITIONS.get(current, set())
        if target not in allowed:
            raise JobTransitionError(
                f"Transition {current.value} → {target.value} is not allowed."
            )

    # ------------------------------------------------------------------
    # Cleanup / Retention
    # ------------------------------------------------------------------

    def cleanup_expired(
        self,
        job_retention_days: int = 7,
        artifact_retention_days: int = 7,
    ) -> dict:
        """Delete expired artifacts and optionally old terminal job records.

        Safe to call at startup or periodically. Never touches RUNNING or
        QUEUED jobs.
        """
        artifact_count = 0
        job_count = 0
        try:
            artifact_count = self._artifacts.cleanup_expired(
                retention_days=artifact_retention_days
            )
        except Exception as exc:
            logger.warning("event=artifact_cleanup_error error=%s", exc)

        try:
            job_count = self._store.delete_expired_jobs(
                retention_days=job_retention_days
            )
        except Exception as exc:
            logger.warning("event=job_cleanup_error error=%s", exc)

        logger.info(
            "event=cleanup_completed jobs_deleted=%d artifacts_deleted=%d",
            job_count, artifact_count,
        )
        return {"jobs_deleted": job_count, "artifacts_deleted": artifact_count}

    # Aliases for API layer compatibility
    def get_job_result(self, job_id: str) -> dict:
        """Alias for get_result()."""
        return self.get_result(job_id)

    def get_artifact_content(self, job_id: str):
        """Alias for get_artifact_stream()."""
        return self.get_artifact_stream(job_id)

    # ------------------------------------------------------------------
    # Health
    # ------------------------------------------------------------------

    def health_check(self) -> dict:
        try:
            counts = self._store.count_by_status()
            return {
                "job_store": "healthy",
                "queued_jobs": counts.get(JobStatus.QUEUED, 0),
                "running_jobs": counts.get(JobStatus.RUNNING, 0),
            }
        except Exception as exc:
            logger.warning("event=job_store_health_check_failed error=%s", exc)
            return {"job_store": "unavailable"}
