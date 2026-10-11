"""FastAPI route definitions for N-CoreIQ.

Implements:
  GET  /api/v1/health                     – application health check
  GET  /api/v1/sap/ping                   – SAP ECC connectivity test
  POST /api/v1/repository/search          – Phase 3 repository discovery
  GET  /api/v1/repository/packages/{pkg}  – Phase 3 package resolution
  POST /api/v1/repository/object          – Phase 4+ object detail (stub)

Route handlers must NOT contain SAP-specific RFC logic.  All SAP interaction
is delegated through the service layer.
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response

from app.core.config import Settings, get_settings
from app.models.requests import ObjectKey, RepositorySearchRequest
from app.models.results import (
    ConnectionTestResult,
    HealthResult,
    RepositorySearchResponse,
    SearchResult,
)
from app.sap.connection.factory import build_sap_connection_provider
from app.models.repository import PackageResolutionResult
from app.models.phase4 import (
    MAX_SOURCE_LINES_HARD_LIMIT,
    MIN_SOURCE_LINES,
    RepositoryObjectMetadata,
    RepositoryObjectSource,
)
from app.models.phase5 import (
    BulkExtractionRequest,
    BulkExtractionResult,
    BulkExportRequest,
    ExportFormat,
)
from app.sap.exceptions import (
    RepositoryObjectNotFoundError,
    RepositoryObjectTypeNotSupportedError,
    RepositoryPackageNotFoundError,
    RepositorySourceDataIntegrityError,
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPConfigurationError,
    SAPConnectionError,
    SAPFunctionExecutionError,
    SAPProviderNotImplementedError,
    SAPRFCNotAllowedError,
    SAPRFCRuntimeUnavailableError,
)
from app.services.repository import RepositoryService
from app.services.object_service import RepositoryObjectService, get_object_service
from app.services.bulk_extraction import BulkExtractionService
from app.services.export import RepositoryExportService, get_export_service

logger = logging.getLogger("app.api.routes")

router = APIRouter()


def get_discovery_service(settings: Settings = Depends(get_settings)) -> RepositoryService:
    """FastAPI dependency that constructs a :class:`RepositoryService` for discovery."""
    return RepositoryService(build_sap_connection_provider(settings))


def get_bulk_extraction_service(
    settings: Settings = Depends(get_settings),
) -> BulkExtractionService:
    """FastAPI dependency for BulkExtractionService."""
    object_service = RepositoryObjectService(build_sap_connection_provider(settings))
    return BulkExtractionService(object_service=object_service, settings=settings)


# Keep the old name as an alias so any existing internal references still resolve.
repository_service = get_discovery_service


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    response_model=HealthResult,
    summary="Application health check",
    description=(
        "Returns the application name, version, environment, and optional "
        "Phase 6 job subsystem diagnostics.  "
        "Does not require a live SAP ECC connection."
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
    # Phase 6 — add job subsystem health when available.
    # A failure in the job subsystem must NOT cause overall health to fail.
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
# SAP Ping
# ---------------------------------------------------------------------------


@router.get(
    "/sap/ping",
    response_model=ConnectionTestResult,
    summary="SAP ECC connectivity test",
    description=(
        "Tests connectivity to the configured SAP ECC system and returns a "
        "normalised ConnectionTestResult containing the system ID, client, and "
        "SAP release.  "
        "When SAP_CONNECTION_MODE=odata (preferred), calls the SystemInfoSet "
        "entity set on the ZNCOREIQ_REPO_SRV OData V2 service over HTTPS.  "
        "When SAP_CONNECTION_MODE=local (legacy), calls Z_MCP_REPO_PING via "
        "PyRFC / SAP NetWeaver RFC SDK."
    ),
)
async def sap_ping(settings: Settings = Depends(get_settings)) -> ConnectionTestResult:
    provider = build_sap_connection_provider(settings)
    try:
        return await provider.ping()
    except SAPConfigurationError as exc:
        logger.warning("sap_ping_config_invalid", extra={"operation": "sap_ping"})
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SAPRFCRuntimeUnavailableError as exc:
        logger.warning("sap_ping_runtime_unavailable", extra={"operation": "sap_ping"})
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except SAPProviderNotImplementedError as exc:
        logger.warning("sap_ping_provider_not_implemented", extra={"operation": "sap_ping"})
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except SAPAuthenticationError as exc:
        logger.warning("sap_ping_auth_failed", extra={"operation": "sap_ping"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPConnectionError as exc:
        logger.warning("sap_ping_unreachable", extra={"operation": "sap_ping"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPFunctionExecutionError as exc:
        logger.warning("sap_ping_function_failed", extra={"operation": "sap_ping"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPCommunicationError as exc:
        logger.warning("sap_ping_communication_failed", extra={"operation": "sap_ping"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Phase 3 — Repository Discovery
# ---------------------------------------------------------------------------


@router.post(
    "/repository/search",
    response_model=RepositorySearchResponse,
    summary="Discover SAP custom repository objects",
    description=(
        "Discovers SAP ABAP custom repository objects (Z*/Y* namespace) "
        "from the configured ECC system using Z_MCP_REPO_DISCOVER.  "
        "Filters may be combined: packages, name patterns, and object types.  "
        "At least one of 'packages', 'name_patterns', or 'all_custom_objects=true' "
        "must be supplied.  Never performs an unfiltered full TADIR scan."
    ),
)
async def search_repository(
    request: RepositorySearchRequest,
    service: RepositoryService = Depends(get_discovery_service),
) -> RepositorySearchResponse:
    try:
        return await service.search(request)
    except ValueError as exc:
        # Raised by service-layer validation (safety rules, unsupported types, etc.)
        logger.warning(
            "repository_search_validation_error",
            extra={"operation": "repository_search", "detail": str(exc)},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SAPConfigurationError as exc:
        logger.warning(
            "repository_search_config_invalid",
            extra={"operation": "repository_search"},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SAPRFCRuntimeUnavailableError as exc:
        logger.warning(
            "repository_search_runtime_unavailable",
            extra={"operation": "repository_search"},
        )
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except SAPProviderNotImplementedError as exc:
        logger.warning(
            "repository_search_provider_not_implemented",
            extra={"operation": "repository_search"},
        )
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except SAPRFCNotAllowedError as exc:
        logger.warning(
            "repository_search_rfc_not_allowed",
            extra={"operation": "repository_search"},
        )
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except SAPAuthenticationError as exc:
        logger.warning(
            "repository_search_auth_failed",
            extra={"operation": "repository_search"},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPConnectionError as exc:
        logger.warning(
            "repository_search_unreachable",
            extra={"operation": "repository_search"},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPFunctionExecutionError as exc:
        logger.warning(
            "repository_search_function_failed",
            extra={"operation": "repository_search"},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPCommunicationError as exc:
        logger.warning(
            "repository_search_communication_failed",
            extra={"operation": "repository_search"},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except NotImplementedError as exc:
        logger.warning(
            "repository_search_not_implemented",
            extra={"operation": "repository_search"},
        )
        raise HTTPException(status_code=501, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Phase 3 — Package Resolution (OData PackagesSet)
# ---------------------------------------------------------------------------


@router.get(
    "/repository/packages/{package}",
    response_model=PackageResolutionResult,
    summary="Resolve SAP package hierarchy",
    tags=["repository"],
    description=(
        "Resolves an SAP development package hierarchy by calling the "
        "PackagesSet entity set on the ZNCOREIQ_REPO_SRV OData V2 service.  "
        "Returns a normalised package tree with system context.  "
        "The package name is automatically trimmed and uppercased.\n\n"
        "**Example request:**\n"
        "```\n"
        "GET /api/v1/repository/packages/ZNCOREIQ"
        "?include_subpackages=true&max_depth=5\n"
        "```\n\n"
        "**include_subpackages** – when `true`, child packages are resolved "
        "recursively up to `max_depth` levels deep.  Defaults to `false`.\n\n"
        "**max_depth** – maximum recursion depth (0–20). "
        "0 returns only the root package.  "
        "20 is the maximum allowed.  "
        "When SAP truncates the result, `truncated=true` is set in the response.\n\n"
        "**truncated** – `true` when SAP reported that the hierarchy was "
        "truncated due to `max_depth` or an internal SAP limit."
    ),
)
async def get_packages(
    package: str,
    include_subpackages: bool = False,
    max_depth: int = 20,
    service: RepositoryService = Depends(repository_service),
) -> PackageResolutionResult:
    try:
        return await service.resolve_packages(
            package=package,
            include_subpackages=include_subpackages,
            max_depth=max_depth,
        )
    except ValueError as exc:
        logger.warning(
            "repository_packages_validation_error",
            extra={"operation": "package_resolution", "detail": str(exc)},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RepositoryPackageNotFoundError as exc:
        logger.info(
            "repository_packages_not_found",
            extra={"operation": "package_resolution", "package": exc.package},
        )
        raise HTTPException(
            status_code=404,
            detail=str(exc),
        ) from exc
    except SAPConfigurationError as exc:
        logger.warning(
            "repository_packages_config_invalid",
            extra={"operation": "package_resolution"},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SAPProviderNotImplementedError as exc:
        logger.warning(
            "repository_packages_provider_not_implemented",
            extra={"operation": "package_resolution"},
        )
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except SAPAuthenticationError as exc:
        logger.warning(
            "repository_packages_auth_failed",
            extra={"operation": "package_resolution"},
        )
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except SAPConnectionError as exc:
        logger.warning(
            "repository_packages_unreachable",
            extra={"operation": "package_resolution"},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPFunctionExecutionError as exc:
        logger.warning(
            "repository_packages_function_failed",
            extra={"operation": "package_resolution"},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPCommunicationError as exc:
        logger.warning(
            "repository_packages_communication_failed",
            extra={"operation": "package_resolution"},
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Phase 4 — Object Metadata
# ---------------------------------------------------------------------------


@router.get(
    "/repository/objects/{object_type}/{object_name}/metadata",
    response_model=RepositoryObjectMetadata,
    summary="Retrieve SAP repository object metadata",
    tags=["repository", "phase4"],
    description=(
        "Returns technical metadata for a single SAP repository object by "
        "calling **ObjectMetadataSet** on the ZNCOREIQ_REPO_SRV OData V2 service.\n\n"
        "Object type and name are automatically trimmed and uppercased.\n\n"
        "**Supported object types:** "
        "PROG, CLAS, INTF, FUGR, FUNC, TABL, DTEL, DOMA, VIEW, SHLP, ENQU, TRAN, MSAG\n\n"
        "**Example:**\n"
        "```\n"
        "GET /api/v1/repository/objects/PROG/ZMY_PROGRAM/metadata\n"
        "```\n\n"
        "**Status codes:**\n"
        "- `200` — Object found, metadata returned.\n"
        "- `404` — Object does not exist in SAP.\n"
        "- `422` — Unsupported object type or blank identifier.\n"
        "- `503` — SAP connectivity failure.\n"
    ),
)
async def get_object_metadata(
    object_type: str,
    object_name: str,
    service: RepositoryObjectService = Depends(get_object_service),
) -> RepositoryObjectMetadata:
    try:
        return await service.get_metadata(
            object_type=object_type,
            object_name=object_name,
        )
    except ValueError as exc:
        logger.warning(
            "object_metadata_validation_error",
            extra={"operation": "get_object_metadata", "detail": str(exc)},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RepositoryObjectTypeNotSupportedError as exc:
        logger.warning(
            "object_metadata_unsupported_type",
            extra={
                "operation": "get_object_metadata",
                "object_type": exc.object_type,
            },
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RepositoryObjectNotFoundError as exc:
        logger.info(
            "object_metadata_not_found",
            extra={
                "operation": "get_object_metadata",
                "object_type": exc.object_type,
                "object_name": exc.object_name,
            },
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except SAPConfigurationError as exc:
        logger.warning("object_metadata_config_invalid", extra={"operation": "get_object_metadata"})
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SAPAuthenticationError as exc:
        logger.warning("object_metadata_auth_failed", extra={"operation": "get_object_metadata"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPConnectionError as exc:
        logger.warning("object_metadata_unreachable", extra={"operation": "get_object_metadata"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPFunctionExecutionError as exc:
        logger.warning("object_metadata_function_failed", extra={"operation": "get_object_metadata"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPCommunicationError as exc:
        logger.warning("object_metadata_communication_failed", extra={"operation": "get_object_metadata"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Phase 4 — Object Source / Technical Definition
# ---------------------------------------------------------------------------


@router.get(
    "/repository/objects/{object_type}/{object_name}/source",
    response_model=RepositoryObjectSource,
    summary="Retrieve SAP repository object source or technical definition",
    tags=["repository", "phase4"],
    description=(
        "Returns the ABAP source code or structured technical definition for a "
        "single SAP repository object by calling **SourceLinesSet** on the "
        "ZNCOREIQ_REPO_SRV OData V2 service.\n\n"
        "For executable objects (PROG, CLAS, INTF, FUGR, FUNC) this returns "
        "actual ABAP source.  For DDIC/technical objects (TABL, DTEL, DOMA, "
        "VIEW, SHLP, ENQU) this returns a structured textual definition.  "
        "For TRAN/MSAG this returns transaction/message definitions.\n\n"
        "Source text is **never** reformatted, uppercased, or indentation-stripped.\n\n"
        "**Supported object types:** "
        "PROG, CLAS, INTF, FUGR, FUNC, TABL, DTEL, DOMA, VIEW, SHLP, ENQU, TRAN, MSAG\n\n"
        "**Query parameters:**\n"
        f"- `max_lines` — Maximum source lines to return (optional). "
        f"Range: {MIN_SOURCE_LINES}–{MAX_SOURCE_LINES_HARD_LIMIT}. "
        f"**Omit for complete (unlimited) source retrieval.**\n\n"
        "**Example (complete source):**\n"
        "```\n"
        "GET /api/v1/repository/objects/PROG/ZMY_PROGRAM/source\n"
        "```\n\n"
        "**Example (bounded preview):**\n"
        "```\n"
        "GET /api/v1/repository/objects/PROG/ZMY_PROGRAM/source?max_lines=500\n"
        "```\n\n"
        "**Status codes:**\n"
        "- `200` — Object found, source returned.\n"
        "- `404` — Object does not exist in SAP.\n"
        "- `422` — Unsupported object type, blank identifier, or invalid max_lines.\n"
        "- `503` — SAP connectivity failure or source data integrity error.\n"
    ),
)
async def get_object_source(
    object_type: str,
    object_name: str,
    max_lines: int | None = Query(
        default=None,
        ge=1,
        le=MAX_SOURCE_LINES_HARD_LIMIT,
        description=(
            f"Maximum source lines to return ({MIN_SOURCE_LINES}–{MAX_SOURCE_LINES_HARD_LIMIT}). "
            "Omit to retrieve the complete source without truncation."
        ),
    ),
    service: RepositoryObjectService = Depends(get_object_service),
) -> RepositoryObjectSource:
    try:
        return await service.get_source(
            object_type=object_type,
            object_name=object_name,
            max_lines=max_lines,
        )
    except ValueError as exc:
        logger.warning(
            "object_source_validation_error",
            extra={"operation": "get_object_source", "detail": str(exc)},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RepositoryObjectTypeNotSupportedError as exc:
        logger.warning(
            "object_source_unsupported_type",
            extra={
                "operation": "get_object_source",
                "object_type": exc.object_type,
            },
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RepositoryObjectNotFoundError as exc:
        logger.info(
            "object_source_not_found",
            extra={
                "operation": "get_object_source",
                "object_type": exc.object_type,
                "object_name": exc.object_name,
            },
        )
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RepositorySourceDataIntegrityError as exc:
        logger.error(
            "object_source_integrity_error",
            extra={
                "operation": "get_object_source",
                "component_type": exc.component_type,
                "component_name": exc.component_name,
                "line_no": exc.line_no,
            },
        )
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPConfigurationError as exc:
        logger.warning("object_source_config_invalid", extra={"operation": "get_object_source"})
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SAPAuthenticationError as exc:
        logger.warning("object_source_auth_failed", extra={"operation": "get_object_source"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPConnectionError as exc:
        logger.warning("object_source_unreachable", extra={"operation": "get_object_source"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPFunctionExecutionError as exc:
        logger.warning("object_source_function_failed", extra={"operation": "get_object_source"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPCommunicationError as exc:
        logger.warning("object_source_communication_failed", extra={"operation": "get_object_source"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Phase 5 — Bulk Extraction
# ---------------------------------------------------------------------------


@router.post(
    "/repository/extract",
    response_model=BulkExtractionResult,
    summary="Bulk extract SAP repository objects",
    tags=["repository", "phase5"],
    description=(
        "Extracts metadata and/or source for multiple SAP repository objects in "
        "a single synchronous operation.  Objects may be specified explicitly or "
        "resolved via a search criterion.\n\n"
        "**Selection methods:**\n"
        "- `objects` — explicit list of `{object_type, object_name}` pairs\n"
        "- `search` — search criteria forwarded to Phase 3 discovery\n\n"
        "Exactly one of `objects` or `search` must be supplied.\n\n"
        f"**Synchronous limit:** configurable (default 250 objects).\n\n"
        "**Status codes:**\n"
        "- `200` — Extraction completed (possibly with per-object errors).\n"
        "- `422` — Invalid request (missing selection, both supplied, limits exceeded).\n"
        "- `503` — SAP connectivity failure during discovery phase.\n"
    ),
)
async def bulk_extract(
    request: BulkExtractionRequest,
    service: BulkExtractionService = Depends(get_bulk_extraction_service),
) -> BulkExtractionResult:
    try:
        return await service.extract(request)
    except ValueError as exc:
        logger.warning(
            "bulk_extract_validation_error",
            extra={"operation": "bulk_extraction", "detail": str(exc)},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SAPAuthenticationError as exc:
        logger.warning("bulk_extract_auth_failed", extra={"operation": "bulk_extraction"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPConnectionError as exc:
        logger.warning("bulk_extract_unreachable", extra={"operation": "bulk_extraction"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPCommunicationError as exc:
        logger.warning("bulk_extract_communication_failed", extra={"operation": "bulk_extraction"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc


# ---------------------------------------------------------------------------
# Phase 5 — Bulk Export
# ---------------------------------------------------------------------------


@router.post(
    "/repository/export",
    summary="Bulk extract and export SAP repository objects",
    tags=["repository", "phase5"],
    description=(
        "Performs bulk extraction (identical to `/extract`) and then serialises "
        "the result to the requested format.\n\n"
        "**Formats:**\n"
        "- `json` — `application/json` UTF-8 document\n"
        "- `zip`  — `application/zip` archive with manifest, metadata, source, "
        "and component files\n\n"
        "**Selection methods** and **limits** are identical to `/extract`.\n\n"
        "**ZIP structure:**\n"
        "```\n"
        "ncoreiq-export/\n"
        "├── manifest.json\n"
        "└── objects/\n"
        "    ├── PROG/ZREPORT1/metadata.json\n"
        "    ├── PROG/ZREPORT1/source.json\n"
        "    └── PROG/ZREPORT1/source/PROGRAM__ZREPORT1.abap\n"
        "```\n\n"
        "**Status codes:**\n"
        "- `200` — Export produced (may contain per-object errors in manifest).\n"
        "- `422` — Invalid request.\n"
        "- `503` — SAP connectivity failure.\n"
    ),
    responses={
        200: {
            "content": {
                "application/json": {},
                "application/zip": {},
            },
            "description": "Export file (JSON or ZIP)",
        }
    },
)
async def bulk_export(
    request: BulkExportRequest,
    bulk_service: BulkExtractionService = Depends(get_bulk_extraction_service),
    export_service: RepositoryExportService = Depends(get_export_service),
) -> Response:
    # 1. Run extraction — BulkExportRequest inherits BulkExtractionRequest;
    #    pass it directly (the extra `format` field is ignored by the service).
    try:
        result = await bulk_service.extract(request.to_extraction_request())
    except ValueError as exc:
        logger.warning(
            "bulk_export_validation_error",
            extra={"operation": "bulk_export", "detail": str(exc)},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SAPAuthenticationError as exc:
        logger.warning("bulk_export_auth_failed", extra={"operation": "bulk_export"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPConnectionError as exc:
        logger.warning("bulk_export_unreachable", extra={"operation": "bulk_export"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPCommunicationError as exc:
        logger.warning("bulk_export_communication_failed", extra={"operation": "bulk_export"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    # 2. Serialise — export service does NOT re-query SAP
    filename = export_service.export_filename(result, request.format)

    if request.format == ExportFormat.ZIP:
        content = export_service.to_zip_bytes(result)
        media_type = "application/zip"
    else:
        content = export_service.to_json_bytes(result)
        media_type = "application/json"

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Extraction-Id": result.extraction_id,
        },
    )


# ---------------------------------------------------------------------------
# Phase 8C — Flat Source Export
# ---------------------------------------------------------------------------


@router.post(
    "/repository/source-export",
    summary="Export selected SAP objects as a flat source ZIP (Phase 8C)",
    tags=["repository", "phase8c"],
    description=(
        "Performs bulk extraction (source only) for the specified objects then "
        "serialises the result into the Phase 8C flat source ZIP format.\n\n"
        "**ZIP structure:**\n"
        "```\n"
        "N-CoreIQ-Source-{timestamp}/\n"
        "├── manifest.json\n"
        "├── PROG/\n"
        "│   ├── ZREPORT1.abap         ← single-component: object name\n"
        "│   └── ZREPORT2.abap\n"
        "└── CLAS/\n"
        "    ├── CLASS_POOL__ZCL_SAMPLE.abap\n"
        "    └── METHOD__ZCL_SAMPLE_DO_X.abap\n"
        "```\n\n"
        "Filename rules:\n"
        "- Single-component object → `{OBJECTNAME}.abap`\n"
        "- Multi-component object  → `{COMPONENT_TYPE}__{COMPONENT_NAME}.abap`\n\n"
        "All path components are sanitised to prevent path traversal.\n\n"
        "**Status codes:**\n"
        "- `200` — ZIP produced (may contain per-object errors in manifest).\n"
        "- `422` — Invalid request.\n"
        "- `503` — SAP connectivity failure.\n"
    ),
    responses={
        200: {
            "content": {"application/zip": {}},
            "description": "Flat source ZIP archive",
        }
    },
)
async def source_export(
    request: BulkExportRequest,
    bulk_service: BulkExtractionService = Depends(get_bulk_extraction_service),
    export_service: RepositoryExportService = Depends(get_export_service),
) -> Response:
    """Extract source for the specified objects and return a flat source ZIP."""
    # Force source-only for Phase 8C (metadata not needed in the flat format)
    extraction_req = BulkExtractionRequest(
        objects=request.objects,
        search=request.search,
        include_metadata=False,
        include_source=True,
        source_max_lines=request.source_max_lines,
        continue_on_error=True,
    )

    try:
        result = await bulk_service.extract(extraction_req)
    except ValueError as exc:
        logger.warning(
            "source_export_validation_error",
            extra={"operation": "source_export", "detail": str(exc)},
        )
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except SAPAuthenticationError as exc:
        logger.warning("source_export_auth_failed", extra={"operation": "source_export"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPConnectionError as exc:
        logger.warning("source_export_unreachable", extra={"operation": "source_export"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except SAPCommunicationError as exc:
        logger.warning("source_export_communication_failed", extra={"operation": "source_export"})
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    ts = result.started_at.strftime("%Y%m%d_%H%M%S")
    filename = export_service.source_export_filename(ts)
    content = export_service.to_source_zip_bytes(result)

    return Response(
        content=content,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Extraction-Id": result.extraction_id,
        },
    )


# ---------------------------------------------------------------------------
# Phase 4+ — Object detail (stub — kept for backward compat, returns 501)
# ---------------------------------------------------------------------------


@router.post(
    "/repository/object",
    summary="Retrieve a single repository object (Phase 4 stub)",
    description=(
        "Legacy stub endpoint.  Use GET "
        "/api/v1/repository/objects/{object_type}/{object_name}/metadata "
        "or /source instead."
    ),
)
async def get_repository_object(
    key: ObjectKey,
    service: RepositoryService = Depends(get_discovery_service),
):
    try:
        return await service.get_object(key)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except (SAPRFCNotAllowedError, SAPProviderNotImplementedError) as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
