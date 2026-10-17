"""N-CoreIQ — FastAPI application entry point (Phase 9).

Phase 9 adds:
  - Structured logging initialization
  - Correlation ID middleware
  - XSUAA authentication middleware
  - CORS with configurable trusted origins
  - Security response headers
  - Liveness / readiness health endpoints
  - Admin diagnostics / metrics endpoints
  - Fail-fast startup validation in BTP mode
  - Graceful shutdown (stop workers, close pools, expire leases)
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.api.routes import router
from app.api.jobs import jobs_router
from app.api.dependencies import deps_router
from app.api.health import admin_router, health_router
from app.auth.middleware import XSUAAAuthMiddleware
from app.core.config import get_settings
from app.core.log_config import CorrelationIdMiddleware, configure_logging

settings = get_settings()

# ---------------------------------------------------------------------------
# Configure structured logging (must be first)
# ---------------------------------------------------------------------------

import os as _os

_instance_id = (
    settings.logging.instance_id
    or _os.environ.get("CF_INSTANCE_INDEX")
    or "0"
)

configure_logging(
    level=settings.log_level,
    fmt=settings.logging.format,
    service=settings.app_name,
    version=settings.app_version,
    runtime_mode=settings.app_env,
    instance_id=_instance_id,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Application lifespan
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan — startup then shutdown."""
    # ------------------------------------------------------------------ #
    # STARTUP                                                              #
    # ------------------------------------------------------------------ #
    logger.info(
        "event=startup app=%s version=%s mode=%s instance=%s",
        settings.app_name,
        settings.app_version,
        settings.app_env,
        _instance_id,
    )

    # BTP mode fail-fast: validate required service bindings before accepting traffic
    if settings.app_env == "btp":
        _validate_btp_startup(settings)

    # TEST/DEMO ONLY — MCP anonymous-access startup warning.
    # Never logs credentials, tokens, secrets, or service-key contents.
    if getattr(settings, "ncoreiq_mcp_auth_mode", "xsuaa") == "none":
        logger.warning(
            "event=mcp_auth_disabled "
            "N-CoreIQ MCP authentication is DISABLED for /mcp/. "
            "This configuration is intended for controlled TEST/DEMO use only. "
            "AUTH_MODE=%s continues to protect all other application routes.",
            settings.auth_mode,
        )

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

        # Initialise job store
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

        # Recover stale jobs from previous run
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

        # Start background workers (skipped in test mode)
        if not settings.ncoreiq_disable_executor:
            await executor.start()
            logger.info(
                "event=executor_started workers=%d", settings.ncoreiq_job_workers
            )
        else:
            logger.info("event=executor_skipped reason=ncoreiq_disable_executor=true")

        # Attach to app state
        app.state.job_store = job_store
        app.state.artifact_store = artifact_store
        app.state.job_service = job_service
        app.state.executor = executor

    except Exception:
        logger.exception("event=startup_failed")
        # Allow app to start even if job subsystem fails; routes will return 503

    logger.info("event=startup_complete")

    # ------------------------------------------------------------------ #
    # MCP SESSION MANAGER (streamable_http transport only)                #
    # ------------------------------------------------------------------ #
    # StreamableHTTPSessionManager.run() MUST be active before any /mcp
    # requests arrive.  It creates the anyio task group that stateless
    # request handling asserts on.  The Starlette sub-app returned by
    # FastMCP.streamable_http_app() has its own lifespan, but Starlette
    # does NOT propagate lifespan ASGI events to mounted sub-applications;
    # only the root app receives them.  We therefore start the session
    # manager explicitly here, wrapping the entire "app running" period.
    _mcp_srv = getattr(app.state, "_mcp_server", None)
    if _mcp_srv is not None:
        logger.info("event=mcp_session_manager_starting transport=streamable_http")
        async with _mcp_srv.mcp.session_manager.run():
            logger.info("event=mcp_session_manager_started")
            yield
        logger.info("event=mcp_session_manager_stopped")
    else:
        yield

    # ------------------------------------------------------------------ #
    # SHUTDOWN                                                             #
    # ------------------------------------------------------------------ #
    logger.info("event=shutdown_start")

    executor_obj = getattr(app.state, "executor", None)
    if executor_obj is not None:
        try:
            await executor_obj.stop()
            logger.info("event=executor_stopped")
        except Exception:
            logger.warning("event=executor_stop_failed", exc_info=True)

    logger.info("event=shutdown_complete")


def _validate_btp_startup(settings) -> None:
    """Fail fast in BTP mode if required bindings/config are missing."""
    from app.btp.bindings import BTPServiceBindingResolver

    resolver = BTPServiceBindingResolver()
    missing = resolver.validate_btp_mode()

    # In BTP mode, SAP connectivity is via Destination Service OR direct odata config
    if settings.sap_connection_mode == "btp" and missing:
        # Allow partial binding if direct SAP config is provided as fallback
        if not settings.sap_odata.base_url:
            logger.error(
                "event=btp_startup_missing_bindings missing=%s", missing
            )
            # Log as warning, not hard failure — allow fallback to direct config
            logger.warning(
                "event=btp_mode_binding_warning missing_services=%s "
                "— configure VCAP_SERVICES or bind services in BTP",
                missing,
            )
    else:
        logger.info(
            "event=btp_mode_validation_ok connection_mode=%s",
            settings.sap_connection_mode,
        )

    # ------------------------------------------------------------------ #
    # JWT Crypto dependency check (BTP + XSUAA mode only)                 #
    # ------------------------------------------------------------------ #
    # BTP production mode MUST use full RS256 signature verification.
    # Structure-only JWT fallback is blocked in BTP+xsuaa to prevent
    # silently accepting forged tokens in production.
    effective_auth = settings.auth.mode
    if effective_auth == "disabled" and settings.auth_mode == "xsuaa":
        effective_auth = "xsuaa"
    if effective_auth == "xsuaa":
        from app.auth.middleware import check_jwt_crypto_available
        try:
            check_jwt_crypto_available()
            logger.info("event=jwt_crypto_available pyjwt=ok")
        except RuntimeError as exc:
            logger.error(
                "event=jwt_crypto_unavailable error=%s "
                "— BTP production cannot start without PyJWT[crypto]",
                exc,
            )
            raise  # Hard fail — do not serve requests with unverified JWTs


# ---------------------------------------------------------------------------
# Application factory (for testing with injected dependencies)
# ---------------------------------------------------------------------------


def create_app(
    job_store=None,
    artifact_store=None,
    job_service=None,
    executor=None,
) -> FastAPI:
    """Create a FastAPI app instance for testing with injected dependencies."""
    test_app = FastAPI(title=settings.app_name, version=settings.app_version)
    _configure_app(test_app)
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


def _configure_app(app: FastAPI) -> None:
    """Register routers on a FastAPI instance."""
    app.include_router(router, prefix="/api/v1")
    app.include_router(jobs_router, prefix="/api/v1")
    app.include_router(deps_router, prefix="/api/v1")
    app.include_router(health_router)           # /health/live, /health/ready, /health
    app.include_router(admin_router, prefix="/api/v1")  # /api/v1/admin/*


# ---------------------------------------------------------------------------
# Build the production application
# ---------------------------------------------------------------------------


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "BTP-ready SAP ECC repository discovery and intelligence service.\n\n"
        "**Phase 9** adds SAP BTP production readiness: XSUAA authentication, "
        "Destination Service, Connectivity Service, Cloud Connector support, "
        "structured logging, health probes, and production deployment packaging."
    ),
    lifespan=lifespan,
)

# -----------------------------------------------------------------------
# Phase 9 — Middleware stack (order matters: outermost = last in chain)
# -----------------------------------------------------------------------

# 1. Correlation ID middleware — must be first so all subsequent middleware
#    and handlers have request_id / correlation_id in context
app.add_middleware(CorrelationIdMiddleware)

# 2. CORS — configured trusted origins only (never wildcard in production)
_cors_origins = settings.cors_allowed_origins
# Also include auth sub-settings origins
_auth_origins = settings.auth.cors_origins
_all_origins = list(dict.fromkeys(_cors_origins + _auth_origins))

app.add_middleware(
    CORSMiddleware,
    allow_origins=_all_origins,
    allow_credentials=settings.cors_allow_credentials,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Correlation-ID", "X-Request-ID"],
    expose_headers=["X-Correlation-ID", "X-Request-ID", "Content-Disposition"],
)

# 3. XSUAA authentication middleware
app.add_middleware(XSUAAAuthMiddleware, settings=settings)

# -----------------------------------------------------------------------
# Phase 9 — Security response headers middleware
# -----------------------------------------------------------------------


@app.middleware("http")
async def security_headers_middleware(request: Request, call_next):
    """Add production security headers to all responses."""
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    # CSP: allow same-origin scripts and styles for the UI
    if request.url.path.startswith("/ui"):
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline'; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; "
            "connect-src 'self';"
        )
    return response


# -----------------------------------------------------------------------
# Phase 9 — Production error handler
# -----------------------------------------------------------------------


@app.exception_handler(Exception)
async def production_error_handler(request: Request, exc: Exception):
    """Sanitized error handler — never exposes internal details."""
    from app.core.log_config import get_correlation_id, get_request_id

    request_id = getattr(request.state, "request_id", get_request_id()) or None
    correlation_id = getattr(request.state, "correlation_id", get_correlation_id()) or None

    logger.error(
        "event=unhandled_exception request_id=%s correlation_id=%s path=%s",
        request_id,
        correlation_id,
        request.url.path,
        exc_info=True,
    )

    return JSONResponse(
        status_code=500,
        content={
            "error": "INTERNAL_ERROR",
            "message": "An internal error occurred. Please contact support.",
            "request_id": request_id,
            "correlation_id": correlation_id,
        },
    )


# -----------------------------------------------------------------------
# Register routers
# -----------------------------------------------------------------------

_configure_app(app)


# -----------------------------------------------------------------------
# Root endpoint
# -----------------------------------------------------------------------


@app.get("/")
async def root() -> dict[str, str]:
    return {
        "name": settings.app_name,
        "version": settings.app_version,
        "health": "/health",
        "liveness": "/health/live",
        "readiness": "/health/ready",
        "docs": "/docs",
        "ui": "/ui",
        "jobs": "/api/v1/repository/jobs",
    }


# -----------------------------------------------------------------------
# Phase 8B — Serve built frontend from /ui
# -----------------------------------------------------------------------

_FRONTEND_DIST = Path(__file__).parent.parent / "frontend" / "dist"

if _FRONTEND_DIST.is_dir():
    app.mount(
        "/ui",
        StaticFiles(directory=str(_FRONTEND_DIST), html=True),
        name="frontend",
    )
    logger.info("event=frontend_mounted path=%s", str(_FRONTEND_DIST))
else:
    logger.info(
        "event=frontend_not_built path=%s — run: cd frontend && npm run build",
        str(_FRONTEND_DIST),
    )

# -----------------------------------------------------------------------
# Phase 7 — MCP Streamable HTTP mount
# Activated when NCOREIQ_MCP_TRANSPORT=streamable_http (default: stdio).
# The FastMCP sub-app is mounted at /mcp; all existing FastAPI middleware
# (XSUAA auth, CORS, correlation-ID, security headers) applies automatically
# because Starlette middleware wraps the entire app including sub-apps.
# External endpoint: POST /mcp/
# -----------------------------------------------------------------------

if settings.ncoreiq_mcp_enabled and settings.ncoreiq_mcp_transport == "streamable_http":
    from app.mcp.server import create_server as _create_mcp_server

    _mcp_http_server = _create_mcp_server(settings)
    # Call streamable_http_app() NOW so the session_manager is lazily created
    # before the lifespan reads app.state._mcp_server.session_manager.
    # streamable_http_path="/" inside FastMCP means the effective external
    # endpoint is POST /mcp/ (no /mcp/mcp double-segment).
    _mcp_starlette_app = _mcp_http_server.mcp.streamable_http_app()
    app.state._mcp_server = _mcp_http_server
    app.mount("/mcp", _mcp_starlette_app)
    logger.info(
        "event=mcp_http_mounted path=/mcp canonical_url=POST /mcp/ transport=streamable_http"
        " stateless=True json_response=%s",
        settings.ncoreiq_mcp_json_response,
    )
else:
    logger.info(
        "event=mcp_http_skipped transport=%s enabled=%s"
        " — set NCOREIQ_MCP_TRANSPORT=streamable_http to enable HTTP transport",
        settings.ncoreiq_mcp_transport,
        settings.ncoreiq_mcp_enabled,
    )
