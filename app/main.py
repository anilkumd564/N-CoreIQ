"""N-CoreIQ — FastAPI application entry point.

Phase 6 adds:
  - Job subsystem initialisation (JobStore, ArtifactStore, JobService, Executor)
  - Lifespan context manager for startup/shutdown
  - Phase 6 jobs router mounted at /api/v1/repository/jobs
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.api.jobs import jobs_router
from app.core.config import get_settings

logger = logging.getLogger(__name__)
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup then shutdown."""
    # ------------------------------------------------------------------ #
    # STARTUP                                                              #
    # ------------------------------------------------------------------ #
    logger.info("event=startup app=%s version=%s", settings.app_name, settings.app_version)

    try:
        from app.jobs.store import SQLiteJobStore
        from app.jobs.artifacts import LocalArtifactStore
        from app.jobs.service import ExtractionJobService
        from app.jobs.executor import ExtractionJobExecutor
        from app.services.object_service import RepositoryObjectService
        from app.services.bulk_extraction import BulkExtractionService
        from app.services.export import RepositoryExportService
        from app.sap.connection.factory import build_sap_connection_provider

        # Ensure artifact directory exists
        artifact_dir = settings.ncoreiq_job_artifact_dir
        os.makedirs(artifact_dir, exist_ok=True)

        # Initialise job store (creates/migrates schema)
        job_store = SQLiteJobStore(db_path=settings.ncoreiq_job_db_path)
        job_store.initialize()
        logger.info("event=job_store_ready db=%s", settings.ncoreiq_job_db_path)

        # Initialise artifact store
        artifact_store = LocalArtifactStore(
            artifact_dir=artifact_dir,
            retention_days=settings.ncoreiq_artifact_retention_days,
        )
        logger.info("event=artifact_store_ready dir=%s", artifact_dir)

        # Build Phase 5 services (used by executor)
        sap_provider = build_sap_connection_provider(settings)
        object_service = RepositoryObjectService(sap_provider)
        bulk_service = BulkExtractionService(
            object_service=object_service,
            settings=settings,
        )
        export_service = RepositoryExportService(settings=settings)

        # Build job service (API layer)
        job_service = ExtractionJobService(
            job_store=job_store,
            artifact_store=artifact_store,
        )

        # Build executor (background worker pool)
        executor = ExtractionJobExecutor(
            job_store=job_store,
            artifact_store=artifact_store,
            bulk_service=bulk_service,
            export_service=export_service,
            workers=settings.ncoreiq_job_workers,
            poll_interval=settings.ncoreiq_job_poll_interval_seconds,
            lease_seconds=settings.ncoreiq_job_lease_seconds,
            heartbeat_seconds=settings.ncoreiq_job_heartbeat_seconds,
        )

        # Recover stale jobs from previous run before accepting new work
        recovered = executor.recover_stale_jobs()
        if recovered:
            logger.warning("event=stale_jobs_recovered count=%d", recovered)

        # Optional startup retention cleanup
        try:
            cleaned = job_service.cleanup_expired(
                job_retention_days=settings.ncoreiq_job_retention_days,
                artifact_retention_days=settings.ncoreiq_artifact_retention_days,
            )
            if cleaned:
                logger.info("event=startup_retention_cleanup cleaned=%d", cleaned)
        except Exception:
            logger.warning("event=startup_cleanup_failed", exc_info=True)

        # Start background workers.
        # Disabled when NCOREIQ_DISABLE_EXECUTOR=true (automated tests) to
        # prevent background workers from attempting live SAP connections.
        if not settings.ncoreiq_disable_executor:
            await executor.start()
            logger.info(
                "event=executor_started workers=%d", settings.ncoreiq_job_workers
            )
        else:
            logger.info("event=executor_skipped reason=ncoreiq_disable_executor=true")

        # Attach to app state so route handlers can access them
        app.state.job_store = job_store
        app.state.artifact_store = artifact_store
        app.state.job_service = job_service
        app.state.executor = executor

    except Exception:
        logger.exception("event=startup_failed")
        # Allow app to start even if job subsystem fails; routes will return 503

    yield

    # ------------------------------------------------------------------ #
    # SHUTDOWN                                                             #
    # ------------------------------------------------------------------ #
    logger.info("event=shutdown")

    executor_obj = getattr(app.state, "executor", None)
    if executor_obj is not None:
        try:
            await executor_obj.stop()
            logger.info("event=executor_stopped")
        except Exception:
            logger.warning("event=executor_stop_failed", exc_info=True)


def create_app(
    job_store=None,
    artifact_store=None,
    job_service=None,
    executor=None,
) -> FastAPI:
    """Create a FastAPI app instance for testing with injected dependencies."""
    test_app = FastAPI(title=settings.app_name, version=settings.app_version)
    test_app.include_router(router, prefix="/api/v1")
    test_app.include_router(jobs_router, prefix="/api/v1")
    if job_store is not None:
        test_app.state.job_store = job_store
    if artifact_store is not None:
        test_app.state.artifact_store = artifact_store
    if job_service is not None:
        test_app.state.job_service = job_service
    if executor is not None:
        test_app.state.executor = executor

    @test_app.get("/")
    async def _root() -> dict[str, str]:
        return {"name": settings.app_name, "version": settings.app_version}

    return test_app


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "BTP-ready SAP ECC repository discovery and intelligence service.\n\n"
        "**Phase 6** adds asynchronous extraction jobs with durable persistence, "
        "progress tracking, cancellation, retry, and artifact download."
    ),
    lifespan=lifespan,
)

app.include_router(router, prefix="/api/v1")
app.include_router(jobs_router, prefix="/api/v1")


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "health": "/api/v1/health",
        "docs": "/docs",
        "jobs": "/api/v1/repository/jobs",
    }
