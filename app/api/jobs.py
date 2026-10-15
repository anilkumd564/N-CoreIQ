"""Phase 6 — Extraction Job API routes.

Implements:
  POST   /api/v1/repository/jobs                        — create job (202)
  GET    /api/v1/repository/jobs                        — list jobs
  GET    /api/v1/repository/jobs/{job_id}               — get job status
  GET    /api/v1/repository/jobs/{job_id}/result        — get extraction result
  GET    /api/v1/repository/jobs/{job_id}/artifact      — download artifact
  POST   /api/v1/repository/jobs/{job_id}/cancel        — request cancellation
  POST   /api/v1/repository/jobs/{job_id}/retry         — create retry job

Architecture:
    Route handlers → ExtractionJobService → JobStore / ArtifactStore
    Never calls SAP, BulkExtractionService, or RepositoryExportService directly.
    Execution is delegated to the background ExtractionJobExecutor.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request
from fastapi.responses import Response

from app.core.config import Settings, get_settings
from app.jobs.service import JobConflictError
from app.models.phase6 import (
    ExtractionJob,
    ExtractionJobRequest,
    JobListResponse,
    JobResultResponse,
    JobStatus,
    TERMINAL_STATUSES,
    JobArtifactFormat,
)

logger = logging.getLogger("app.api.jobs")

jobs_router = APIRouter(prefix="/repository/jobs", tags=["jobs", "phase6"])


# ---------------------------------------------------------------------------
# Dependency — ExtractionJobService
# ---------------------------------------------------------------------------

def get_job_service(request: Request):
    """Retrieve ExtractionJobService from app state (set during startup)."""
    svc = getattr(request.app.state, "job_service", None)
    if svc is None:
        raise HTTPException(
            status_code=503,
            detail="Job service is not available. Application may still be starting.",
        )
    return svc


# ---------------------------------------------------------------------------
# POST /repository/jobs  — Create job
# ---------------------------------------------------------------------------

@jobs_router.post(
    "",
    status_code=202,
    summary="Create an asynchronous extraction job",
    description=(
        "Validates the request, persists a job with status=QUEUED, and returns "
        "immediately with HTTP 202.  The background worker picks up the job and "
        "calls Phase 5 BulkExtractionService.\n\n"
        "**Idempotency:** supply an `Idempotency-Key` header to prevent duplicate "
        "jobs when retrying a failed HTTP call.  The same key with the same "
        "request body returns the existing job.  The same key with a different "
        "body returns HTTP 409.\n\n"
        "**export_format:** `json`, `zip`, or `none`.  When omitted defaults to `zip`."
    ),
    responses={
        202: {"description": "Job accepted and queued."},
        409: {"description": "Idempotency key conflict."},
        422: {"description": "Validation error."},
    },
)
async def create_job(
    body: ExtractionJobRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    job_service=Depends(get_job_service),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict:
    created_by: str | None = None  # auth not yet enabled

    try:
        job = job_service.create_job(
            request=body,
            created_by=created_by,
            idempotency_key=idempotency_key,
        )
    except JobConflictError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    base_url = str(request.base_url).rstrip("/")
    status_url = f"{base_url}/api/v1/repository/jobs/{job.job_id}"

    return {
        "job_id": job.job_id,
        "status": job.status.value,
        "stage": job.stage.value if job.stage else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "status_url": status_url,
    }


# ---------------------------------------------------------------------------
# GET /repository/jobs  — List jobs
# ---------------------------------------------------------------------------

@jobs_router.get(
    "",
    response_model=JobListResponse,
    summary="List extraction jobs",
    description=(
        "Returns a paginated list of extraction jobs, newest first.\n\n"
        "**Filters:** `status`, `created_after`, `created_before`.\n\n"
        "**Pagination:** `limit` (default 50, max 200) and `offset`."
    ),
)
async def list_jobs(
    status: str | None = Query(default=None, description="Filter by job status."),
    created_after: str | None = Query(default=None, description="ISO-8601 datetime lower bound."),
    created_before: str | None = Query(default=None, description="ISO-8601 datetime upper bound."),
    limit: int = Query(default=50, ge=1, le=200, description="Maximum results per page."),
    offset: int = Query(default=0, ge=0, description="Pagination offset."),
    settings: Settings = Depends(get_settings),
    job_service=Depends(get_job_service),
) -> JobListResponse:
    # Cap limit to configured maximum
    effective_limit = min(limit, settings.ncoreiq_job_max_list_limit)

    try:
        return job_service.list_jobs(
            status=status,
            created_after=created_after,
            created_before=created_before,
            limit=effective_limit,
            offset=offset,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# GET /repository/jobs/{job_id}  — Get job status
# ---------------------------------------------------------------------------

@jobs_router.get(
    "/{job_id}",
    response_model=ExtractionJob,
    summary="Get extraction job status",
    description=(
        "Returns the current status, stage, progress, result summary, and "
        "artifact summary for a job.  Poll this endpoint to track progress."
    ),
    responses={
        200: {"description": "Job found."},
        404: {"description": "Job not found."},
    },
)
async def get_job(
    job_id: str,
    job_service=Depends(get_job_service),
) -> ExtractionJob:
    job = job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")
    return job


# ---------------------------------------------------------------------------
# GET /repository/jobs/{job_id}/result  — Get extraction result
# ---------------------------------------------------------------------------

@jobs_router.get(
    "/{job_id}/result",
    response_model=JobResultResponse,
    summary="Get extraction result summary for a completed job",
    description=(
        "Returns the **job result summary** for a completed extraction job.\n\n"
        "The summary contains object counts and warnings from the Phase-5 "
        "extraction run. It does **not** contain the complete per-object "
        "extracted evidence payload.\n\n"
        "**Complete extracted evidence** (source, metadata, dependencies) is "
        "available via `GET /api/v1/repository/jobs/{job_id}/artifact`.\n\n"
        "**Canonical consumer workflow:**\n"
        "1. Poll job status until terminal.\n"
        "2. Call this endpoint to inspect counts/warnings.\n"
        "3. Download artifact for extracted object payload.\n\n"
        "**Status codes:**\n"
        "- `200` — Result summary available.\n"
        "- `404` — Job not found.\n"
        "- `409` — Job has not yet completed (result not available).\n"
    ),
    responses={
        200: {"description": "Job result summary."},
        404: {"description": "Job not found."},
        409: {"description": "Result not yet available."},
    },
)
async def get_job_result(
    job_id: str,
    job_service=Depends(get_job_service),
) -> JobResultResponse:
    job = job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")

    if job.status not in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id!r} is still {job.status.value}. Result not yet available.",
        )
    try:
        result = job_service.get_job_result(job_id)
    except (KeyError, ValueError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return result


# ---------------------------------------------------------------------------
# GET /repository/jobs/{job_id}/artifact  — Download artifact
# ---------------------------------------------------------------------------

@jobs_router.get(
    "/{job_id}/artifact",
    summary="Download export artifact for a completed job",
    description=(
        "Returns the export file (JSON or ZIP) produced by the job.\n\n"
        "**Status codes:**\n"
        "- `200` — Artifact available.\n"
        "- `404` — Job not found.\n"
        "- `409` — Job not yet complete or no artifact was requested.\n"
        "- `410` — Artifact has expired and been deleted.\n"
    ),
    responses={
        200: {
            "content": {
                "application/json": {},
                "application/zip": {},
            },
            "description": "Export artifact.",
        },
        404: {"description": "Job not found."},
        409: {"description": "Artifact not yet available."},
        410: {"description": "Artifact has expired."},
    },
)
async def get_job_artifact(
    job_id: str,
    job_service=Depends(get_job_service),
) -> Response:
    job = job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")

    if job.artifact is None:
        if job.status not in TERMINAL_STATUSES:
            raise HTTPException(
                status_code=409,
                detail=f"Job {job_id!r} is still {job.status.value}. Artifact not yet available.",
            )
        raise HTTPException(
            status_code=409,
            detail=f"Job {job_id!r} completed without an artifact. Check export_format in request.",
        )

    try:
        stream, artifact_meta = job_service.get_artifact_stream(job_id)
        content = stream.read()
    except (KeyError, FileNotFoundError):
        raise HTTPException(
            status_code=409,
            detail=f"Artifact for job {job_id!r} is not yet available.",
        )
    except LookupError:
        raise HTTPException(
            status_code=410,
            detail=f"Artifact for job {job_id!r} has expired.",
        )

    artifact = job.artifact
    media_type = "application/zip" if artifact.format == JobArtifactFormat.ZIP else "application/json"

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{artifact.filename}"',
            "X-Job-Id": job_id,
            "X-Artifact-SHA256": artifact.sha256 or "",
        },
    )


# ---------------------------------------------------------------------------
# POST /repository/jobs/{job_id}/cancel  — Cancel job
# ---------------------------------------------------------------------------

@jobs_router.post(
    "/{job_id}/cancel",
    status_code=202,
    summary="Request cancellation of a job",
    description=(
        "Cancels a QUEUED job immediately or requests cooperative cancellation "
        "for a RUNNING job.  A RUNNING job transitions to CANCEL_REQUESTED; the "
        "worker stops at the next safe checkpoint.\n\n"
        "**Note:** An in-flight SAP HTTP request will be allowed to complete "
        "before the job halts.\n\n"
        "Cancelling a job that is already terminal is a no-op (idempotent)."
    ),
    responses={
        202: {"description": "Cancellation accepted."},
        404: {"description": "Job not found."},
        409: {"description": "Job is already in a terminal state."},
    },
)
async def cancel_job(
    job_id: str,
    job_service=Depends(get_job_service),
) -> dict:
    job = job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")

    try:
        updated_job = job_service.cancel_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    return {
        "job_id": updated_job.job_id,
        "status": updated_job.status.value,
        "message": "Cancellation accepted.",
    }


# ---------------------------------------------------------------------------
# POST /repository/jobs/{job_id}/retry  — Retry job
# ---------------------------------------------------------------------------

@jobs_router.post(
    "/{job_id}/retry",
    status_code=202,
    summary="Retry a failed or cancelled job",
    description=(
        "Creates a NEW job using the same validated request as the original.  "
        "The new job references the original via `retry_of_job_id` and increments "
        "`attempt`.  The original job history is preserved.\n\n"
        "**Eligible states:** FAILED, CANCELLED.\n\n"
        "**Status codes:**\n"
        "- `202` — New retry job created and queued.\n"
        "- `404` — Original job not found.\n"
        "- `409` — Job is not in a retryable terminal state.\n"
    ),
    responses={
        202: {"description": "Retry job created."},
        404: {"description": "Original job not found."},
        409: {"description": "Job is not retryable."},
    },
)
async def retry_job(
    job_id: str,
    request: Request,
    job_service=Depends(get_job_service),
) -> dict:
    job = job_service.get_job(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id!r} not found.")

    try:
        new_job = job_service.retry_job(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    base_url = str(request.base_url).rstrip("/")
    status_url = f"{base_url}/api/v1/repository/jobs/{new_job.job_id}"

    return {
        "job_id": new_job.job_id,
        "status": new_job.status.value,
        "retry_of_job_id": new_job.retry_of_job_id,
        "attempt": new_job.attempt,
        "created_at": new_job.created_at.isoformat() if new_job.created_at else None,
        "status_url": status_url,
    }
