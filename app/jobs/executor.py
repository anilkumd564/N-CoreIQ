"""Phase 6 — ExtractionJobExecutor.

Orchestrates asynchronous job execution via a bounded worker pool.

Architecture:
    ExtractionJobExecutor
      → claim_next_job() from SQLiteJobStore (atomic)
      → await BulkExtractionService.extract()  (Phase 5 — async)
      → RepositoryExportService.to_zip_bytes() / to_json_bytes()
      → ArtifactStore.save()
      → finish_job() / fail_job() / finalize_cancellation()

BulkExtractionService.extract() is an async coroutine.  The executor
runs it directly in the async worker loop, and wraps it with asyncio.run()
for the synchronous process_one() path (used in tests).

Cancellation is cooperative: the executor checks cancel status at safe
checkpoints between objects and before export.  In-flight SAP HTTP requests
are allowed to complete before the job halts.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Optional

from app.jobs.artifacts import ArtifactStore
from app.jobs.store import SQLiteJobStore as JobStore
from app.models.phase5 import BulkExtractionResult
from app.models.phase6 import (
    ExtractionJobProgress,
    ExtractionJobRequest,
    JobArtifact,
    JobArtifactFormat,
    JobError,
    JobStage,
    JobStatus,
)
from app.services.bulk_extraction import BulkExtractionService
from app.services.export import RepositoryExportService

logger = logging.getLogger(__name__)
_UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(_UTC)


_FORMAT_MAP: dict[str, JobArtifactFormat] = {
    "json": JobArtifactFormat.JSON,
    "zip": JobArtifactFormat.ZIP,
}


def _norm_export_fmt(fmt_value) -> str:
    """Normalize export format to lowercase string."""
    if hasattr(fmt_value, "value"):
        return str(fmt_value.value).lower()
    return str(fmt_value).lower()


_MIME_MAP: dict[JobArtifactFormat, str] = {
    JobArtifactFormat.JSON: "application/json",
    JobArtifactFormat.ZIP: "application/zip",
}


def _build_result_summary(r: BulkExtractionResult) -> dict:
    return {
        "extraction_id": r.extraction_id,
        "status": r.status.value,
        "selection": r.selection,
        "requested_object_count": r.requested_object_count,
        "processed_object_count": r.processed_object_count,
        "successful_object_count": r.successful_object_count,
        "partial_object_count": r.partial_object_count,
        "failed_object_count": r.failed_object_count,
        "skipped_object_count": getattr(r, "skipped_object_count", 0),
        "truncated_object_count": getattr(r, "truncated_object_count", 0),
        "duration_ms": r.duration_ms,
        "metadata_enabled": r.metadata_enabled,
        "source_enabled": r.source_enabled,
        "warnings": r.warnings,
    }


def _safe_obj_count(extraction) -> int:
    if isinstance(extraction, dict):
        return len(extraction.get("objects") or [])
    return len(getattr(extraction, "objects", None) or [])


def _resolve_extraction_request(raw_request) -> ExtractionJobRequest:
    """Parse job.request (may be dict or already ExtractionJobRequest)."""
    if isinstance(raw_request, dict):
        return ExtractionJobRequest.model_validate(raw_request)
    elif isinstance(raw_request, ExtractionJobRequest):
        return raw_request
    raise TypeError(f"Unexpected request type: {type(raw_request)}")


def _resolve_bulk_extraction(extraction_field) -> Any:
    """Parse the extraction field (may be dict or BulkExtractionRequest)."""
    if isinstance(extraction_field, dict):
        from app.models.phase5 import BulkExtractionRequest
        return BulkExtractionRequest.model_validate(extraction_field)
    return extraction_field


class ExtractionJobExecutor:
    """Application-managed async worker pool for extraction jobs.

    Worker count is bounded (default 2).  Each worker independently claims
    jobs from the JobStore using an atomic EXCLUSIVE transaction, so two
    workers cannot execute the same job concurrently.

    The synchronous ``process_one()`` method is provided for unit tests and
    for the case where the executor is called outside the async event loop.
    It wraps the async extraction in a new asyncio event loop when the real
    BulkExtractionService (async) is used.  Mock services that return plain
    values (not coroutines) continue to work without change.
    """

    def __init__(
        self,
        job_store: JobStore,
        artifact_store: ArtifactStore,
        bulk_service: Optional[BulkExtractionService] = None,
        export_service: Optional[RepositoryExportService] = None,
        workers: int = 1,
        poll_interval: float = 2.0,
        lease_seconds: int = 120,
        heartbeat_seconds: int = 30,
        # Alternate keyword aliases for backward compatibility with tests
        bulk_extraction_service: Optional[BulkExtractionService] = None,
        export_svc: Optional[RepositoryExportService] = None,
        worker_id: Optional[str] = None,
        heartbeat_interval: Optional[int] = None,
    ) -> None:
        self._store = job_store
        self._artifacts = artifact_store
        self._bulk = bulk_service or bulk_extraction_service
        self._export = export_service or export_svc
        self._workers = max(1, workers)
        self._poll_interval = poll_interval
        self._lease_seconds = lease_seconds
        self._heartbeat_seconds = (
            heartbeat_interval if heartbeat_interval is not None else heartbeat_seconds
        )
        self._worker_id = worker_id if worker_id is not None else str(uuid.uuid4())[:8]
        self._running = False
        self._tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        if self._running:
            return
        self._running = True
        for i in range(self._workers):
            wid = f"{self._worker_id}-w{i}"
            task = asyncio.create_task(
                self._worker_loop(wid), name=f"ncoreiq-worker-{wid}"
            )
            self._tasks.append(task)
        logger.info(
            "event=executor_started workers=%d worker_id=%s",
            self._workers, self._worker_id,
        )

    async def stop(self) -> None:
        self._running = False
        for task in self._tasks:
            task.cancel()
        if self._tasks:
            await asyncio.gather(*self._tasks, return_exceptions=True)
        self._tasks.clear()
        logger.info("event=executor_stopped worker_id=%s", self._worker_id)

    @property
    def is_running(self) -> bool:
        return self._running

    # ------------------------------------------------------------------
    # Stale job recovery
    # ------------------------------------------------------------------

    def recover_stale_jobs(self) -> int:
        try:
            count = self._store.recover_stale_jobs()
            if count:
                logger.warning(
                    "event=stale_job_recovered count=%d worker_id=%s",
                    count, self._worker_id,
                )
            return count
        except Exception:
            logger.exception(
                "event=stale_job_recovery_error worker_id=%s", self._worker_id
            )
            return 0

    # ------------------------------------------------------------------
    # Synchronous single-job processing (tests / simple callers)
    # ------------------------------------------------------------------

    def process_one(self) -> bool:
        """Claim and execute one queued job synchronously.

        Returns True if a job was claimed and processed, False if queue empty.

        For real async BulkExtractionService: runs coroutine via asyncio.run().
        For mock services returning plain values: calls extract() directly.
        """
        worker_id = f"{self._worker_id}-sync"
        job = self._store.claim_next_job(
            worker_id=worker_id, lease_seconds=self._lease_seconds
        )
        if job is None:
            return False

        job_id = job.job_id
        attempt = job.attempt or 1

        try:
            request = _resolve_extraction_request(job.request)
        except Exception as exc:
            error = JobError(
                code="INVALID_REQUEST",
                message=f"Deserialise error: {exc}",
                stage=JobStage.VALIDATING.value,
                retryable=False,
            )
            self._store.fail_job(
                job_id=job_id, error=error, stage=JobStage.VALIDATING.value
            )
            return True

        self._store.update_job(
            job_id, stage=JobStage.VALIDATING, started_at=_now()
        )
        logger.info(
            "event=job_started job_id=%s worker_id=%s attempt=%d",
            job_id, worker_id, attempt,
        )

        progress = ExtractionJobProgress(
            total_objects=_safe_obj_count(request.extraction),
            current_stage=JobStage.VALIDATING.value,
        )
        extraction_result: Optional[BulkExtractionResult] = None

        def _cancellation_check() -> bool:
            current = self._store.get_job(job_id)
            return current is not None and current.status == JobStatus.CANCEL_REQUESTED

        def _progress_callback(event_type: str, data: dict) -> None:
            self._handle_progress_event(job_id, event_type, progress, **data)

        try:
            progress.current_stage = JobStage.DISCOVERY.value
            self._store.update_job(
                job_id, stage=JobStage.DISCOVERY, progress=progress
            )

            progress.current_stage = JobStage.EXTRACTING.value
            self._store.update_job(
                job_id, stage=JobStage.EXTRACTING, progress=progress
            )

            extraction_result = self._run_extraction_sync(
                request, _cancellation_check, _progress_callback
            )

            # Cancellation check after extraction completes
            if _cancellation_check():
                rs = _build_result_summary(extraction_result) if extraction_result else None
                progress.current_stage = JobStage.COMPLETED.value
                self._store.finish_job(
                    job_id=job_id,
                    status=JobStatus.CANCELLED,
                    stage=JobStage.COMPLETED,
                    progress=progress,
                    result_summary=rs,
                    result_status=rs.get("status") if rs else None,
                    artifact=None,
                )
                logger.info("event=job_cancelled job_id=%s", job_id)
                return True

            # Optional export
            artifact: Optional[JobArtifact] = None
            export_fmt_value = getattr(request, "export_format", None)
            fmt_str = (
                _norm_export_fmt(export_fmt_value)
                if export_fmt_value is not None
                else ""
            )
            if fmt_str and fmt_str not in ("none", ""):
                if _cancellation_check():
                    rs = (
                        _build_result_summary(extraction_result)
                        if extraction_result
                        else None
                    )
                    progress.current_stage = JobStage.COMPLETED.value
                    self._store.finish_job(
                        job_id=job_id,
                        status=JobStatus.CANCELLED,
                        stage=JobStage.COMPLETED,
                        progress=progress,
                        result_summary=rs,
                        result_status=rs.get("status") if rs else None,
                        artifact=None,
                    )
                    logger.info("event=job_cancelled job_id=%s", job_id)
                    return True

                progress.current_stage = JobStage.EXPORTING.value
                self._store.update_job(
                    job_id, stage=JobStage.EXPORTING, progress=progress
                )
                artifact = self._run_export(
                    job_id, request, extraction_result, fmt_str
                )

            # FINALIZING → SUCCEEDED
            progress.current_stage = JobStage.FINALIZING.value
            self._store.update_job(
                job_id, stage=JobStage.FINALIZING, progress=progress
            )

            rs = _build_result_summary(extraction_result) if extraction_result else None
            progress.current_stage = JobStage.COMPLETED.value
            self._store.finish_job(
                job_id=job_id,
                status=JobStatus.SUCCEEDED,
                stage=JobStage.COMPLETED,
                progress=progress,
                result_summary=rs,
                result_status=rs.get("status") if rs else None,
                artifact=artifact,
            )
            logger.info("event=job_succeeded job_id=%s", job_id)

        except Exception as exc:
            logger.exception(
                "event=job_error job_id=%s worker_id=%s", job_id, worker_id
            )
            error = JobError(
                code="EXECUTION_ERROR",
                message=str(exc),
                stage=progress.current_stage or JobStage.EXTRACTING.value,
                retryable=True,
            )
            self._store.fail_job(
                job_id=job_id,
                error=error,
                stage=progress.current_stage,
                progress=progress,
            )

        return True

    # ------------------------------------------------------------------
    # Extraction helpers
    # ------------------------------------------------------------------

    def _run_extraction_sync(
        self,
        request: ExtractionJobRequest,
        cancellation_check: Callable[[], bool],
        progress_callback: Callable[..., None],
    ) -> BulkExtractionResult:
        """Run extraction, handling both async (real) and sync (mock) services.

        Real BulkExtractionService.extract() is an async coroutine.
        When detected, sync callbacks are wrapped as async shims and the
        coroutine is executed in a fresh asyncio event loop.

        Mock services in tests return plain BulkExtractionResult values.
        These are returned directly without event loop overhead.
        """
        if self._bulk is None:
            raise RuntimeError("BulkExtractionService not configured in executor.")

        extraction = _resolve_bulk_extraction(request.extraction)

        if inspect.iscoroutinefunction(self._bulk.extract):
            # Real async service — run in a new event loop
            async def _async_run() -> BulkExtractionResult:
                async def _async_cancel() -> bool:
                    return cancellation_check()

                async def _async_progress(event_type: str, data: dict) -> None:
                    try:
                        progress_callback(event_type, data)
                    except Exception:
                        pass

                return await self._bulk.extract(
                    extraction,
                    cancellation_check=_async_cancel,
                    progress_callback=_async_progress,
                )

            return asyncio.run(_async_run())
        else:
            # Sync mock service (tests) — returns plain value directly
            result = self._bulk.extract(
                extraction,
                cancellation_check=cancellation_check,
                progress_callback=progress_callback,
            )
            if inspect.isawaitable(result):
                # Fallback: if mock somehow returned a coroutine, run it
                return asyncio.run(result)
            return result

    def _run_export(
        self,
        job_id: str,
        request: ExtractionJobRequest,
        extraction_result: BulkExtractionResult,
        fmt_str: str,
    ) -> Optional[JobArtifact]:
        """Build export artifact and persist it via ArtifactStore.

        Uses RepositoryExportService.to_zip_bytes() or to_json_bytes().
        Returns a JobArtifact with checksum, or None if export is skipped.
        """
        p6_fmt = _FORMAT_MAP.get(fmt_str)
        if p6_fmt is None:
            logger.warning(
                "event=export_skip job_id=%s reason=unknown_format fmt=%s",
                job_id, fmt_str,
            )
            return None
        if self._export is None:
            logger.warning(
                "event=export_skip job_id=%s reason=no_export_service", job_id
            )
            return None

        try:
            if p6_fmt == JobArtifactFormat.ZIP:
                content_bytes = self._export.to_zip_bytes(extraction_result)
                ext = "zip"
            else:
                content_bytes = self._export.to_json_bytes(extraction_result)
                ext = "json"

            filename = f"ncoreiq_{job_id}.{ext}"
            mime = _MIME_MAP.get(p6_fmt, "application/octet-stream")
            artifact_id = f"ncoreiq_{job_id}"
            meta = self._artifacts.save(
                artifact_id=artifact_id,
                data=content_bytes,
                filename=filename,
                content_type=mime,
                job_id=job_id,
            )
            artifact = JobArtifact(
                artifact_id=meta.artifact_id,
                format=p6_fmt,
                filename=meta.filename,
                content_type=meta.content_type,
                size_bytes=meta.size_bytes,
                sha256=meta.sha256,
                created_at=meta.created_at,
                expires_at=meta.expires_at,
            )
            logger.info(
                "event=artifact_created job_id=%s artifact_id=%s size=%d sha256=%.12s",
                job_id, artifact.artifact_id, artifact.size_bytes, artifact.sha256,
            )
            return artifact
        except Exception:
            logger.exception("event=export_error job_id=%s", job_id)
            raise

    def _handle_progress_event(
        self,
        job_id: str,
        event_type: str,
        progress: ExtractionJobProgress,
        **kwargs: Any,
    ) -> None:
        if event_type == "object_started":
            progress.current_object_type = kwargs.get("object_type")
            progress.current_object_name = kwargs.get("object_name")
        elif event_type == "object_completed":
            progress.processed_objects += 1
            progress.successful_objects += 1
            if progress.total_objects > 0:
                progress.percent_complete = min(
                    99, int(progress.processed_objects * 100 / progress.total_objects)
                )
        elif event_type == "object_failed":
            progress.processed_objects += 1
            progress.failed_objects += 1
        elif event_type == "object_partial":
            progress.processed_objects += 1
            progress.partial_objects += 1
        try:
            self._store.update_job(job_id, progress=progress)
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Async worker loop
    # ------------------------------------------------------------------

    async def _worker_loop(self, worker_id: str) -> None:
        logger.info("event=worker_started worker_id=%s", worker_id)
        while self._running:
            try:
                job = await asyncio.to_thread(
                    self._store.claim_next_job,
                    worker_id=worker_id,
                    lease_seconds=self._lease_seconds,
                )
                if job is None:
                    await asyncio.sleep(self._poll_interval)
                    continue
                await self._execute_job(job, worker_id)
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("event=worker_error worker_id=%s", worker_id)
                await asyncio.sleep(self._poll_interval)
        logger.info("event=worker_stopped worker_id=%s", worker_id)

    async def _execute_job(self, job, worker_id: str) -> None:
        job_id = job.job_id
        attempt = job.attempt or 1
        logger.info(
            "event=job_claimed job_id=%s worker_id=%s attempt=%d",
            job_id, worker_id, attempt,
        )

        try:
            request = _resolve_extraction_request(job.request)
        except Exception as exc:
            error = JobError(
                code="INVALID_REQUEST",
                message=str(exc),
                stage=JobStage.VALIDATING.value,
                retryable=False,
            )
            self._store.fail_job(
                job_id=job_id, error=error, stage=JobStage.VALIDATING.value
            )
            return

        await asyncio.to_thread(
            self._store.update_job,
            job_id,
            stage=JobStage.VALIDATING,
            started_at=_now(),
        )

        progress = ExtractionJobProgress(
            total_objects=_safe_obj_count(request.extraction),
            current_stage=JobStage.VALIDATING.value,
        )
        extraction_result: Optional[BulkExtractionResult] = None

        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(job_id, worker_id)
        )

        def _cancellation_check() -> bool:
            current = self._store.get_job(job_id)
            return current is not None and current.status == JobStatus.CANCEL_REQUESTED

        def _progress_callback_sync(event_type: str, data: dict) -> None:
            self._handle_progress_event(job_id, event_type, progress, **data)

        try:
            await self._update_stage(job_id, JobStage.DISCOVERY, progress)
            await self._update_stage(job_id, JobStage.EXTRACTING, progress)

            extraction = _resolve_bulk_extraction(request.extraction)

            # Async cancellation and progress wrappers for BulkExtractionService
            async def _async_cancel() -> bool:
                return _cancellation_check()

            async def _async_progress(event_type: str, data: dict) -> None:
                try:
                    _progress_callback_sync(event_type, data)
                except Exception:
                    pass

            # Execute Phase 5 BulkExtractionService directly (it is async)
            extraction_result = await self._bulk.extract(
                extraction,
                cancellation_check=_async_cancel,
                progress_callback=_async_progress,
            )

            if _cancellation_check():
                await self._finish_cancelled(job_id, progress, extraction_result)
                return

            artifact: Optional[JobArtifact] = None
            export_fmt_value = getattr(request, "export_format", None)
            fmt_str = (
                _norm_export_fmt(export_fmt_value)
                if export_fmt_value is not None
                else ""
            )
            if fmt_str and fmt_str not in ("none", ""):
                await self._update_stage(job_id, JobStage.EXPORTING, progress)
                if _cancellation_check():
                    await self._finish_cancelled(job_id, progress, extraction_result)
                    return
                artifact = await asyncio.to_thread(
                    self._run_export,
                    job_id, request, extraction_result, fmt_str,
                )

            await self._update_stage(job_id, JobStage.FINALIZING, progress)
            await self._finish_succeeded(job_id, progress, extraction_result, artifact)

        except Exception as exc:
            logger.exception(
                "event=job_error job_id=%s worker_id=%s", job_id, worker_id
            )
            error = JobError(
                code="EXECUTION_ERROR",
                message=str(exc),
                stage=progress.current_stage or JobStage.EXTRACTING.value,
                retryable=True,
            )
            self._store.fail_job(
                job_id=job_id,
                error=error,
                stage=progress.current_stage,
                progress=progress,
            )
        finally:
            heartbeat_task.cancel()
            try:
                await heartbeat_task
            except asyncio.CancelledError:
                pass

    async def _heartbeat_loop(self, job_id: str, worker_id: str) -> None:
        while True:
            await asyncio.sleep(self._heartbeat_seconds)
            try:
                self._store.heartbeat_job(
                    job_id, worker_id=worker_id, lease_seconds=self._lease_seconds
                )
                logger.debug(
                    "event=heartbeat job_id=%s worker_id=%s", job_id, worker_id
                )
            except Exception:
                logger.warning(
                    "event=heartbeat_error job_id=%s worker_id=%s",
                    job_id, worker_id,
                )

    async def _update_stage(
        self, job_id: str, stage: JobStage, progress: ExtractionJobProgress
    ) -> None:
        progress.current_stage = stage.value
        await asyncio.to_thread(
            self._store.update_job, job_id, stage=stage, progress=progress
        )

    async def _finish_succeeded(
        self,
        job_id: str,
        progress: ExtractionJobProgress,
        extraction_result: Optional[BulkExtractionResult],
        artifact: Optional[JobArtifact],
    ) -> None:
        rs = _build_result_summary(extraction_result) if extraction_result else None
        progress.current_stage = JobStage.COMPLETED.value
        progress.percent_complete = 100.0
        self._store.finish_job(
            job_id=job_id,
            status=JobStatus.SUCCEEDED,
            stage=JobStage.COMPLETED,
            progress=progress,
            result_summary=rs,
            result_status=rs.get("status") if rs else None,
            artifact=artifact,
        )
        logger.info("event=job_succeeded job_id=%s", job_id)

    async def _finish_cancelled(
        self,
        job_id: str,
        progress: ExtractionJobProgress,
        extraction_result: Optional[BulkExtractionResult],
    ) -> None:
        rs = _build_result_summary(extraction_result) if extraction_result else None
        progress.current_stage = JobStage.COMPLETED.value
        self._store.finish_job(
            job_id=job_id,
            status=JobStatus.CANCELLED,
            stage=JobStage.COMPLETED,
            progress=progress,
            result_summary=rs,
            result_status=rs.get("status") if rs else None,
            artifact=None,
        )
        logger.info("event=job_cancelled job_id=%s", job_id)
