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

from fastapi import APIRouter, Depends, HTTPException

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
from app.sap.exceptions import (
    RepositoryPackageNotFoundError,
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

logger = logging.getLogger("app.api.routes")

router = APIRouter()


def repository_service(settings: Settings = Depends(get_settings)) -> RepositoryService:
    return RepositoryService(build_sap_connection_provider(settings))


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------


@router.get(
    "/health",
    response_model=HealthResult,
    summary="Application health check",
    description=(
        "Returns the application name, version, and environment.  "
        "Does not require a live SAP ECC connection."
    ),
)
async def health(settings: Settings = Depends(get_settings)) -> HealthResult:
    return HealthResult(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )


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
    service: RepositoryService = Depends(repository_service),
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
# Phase 4+ — Object detail (stub)
# ---------------------------------------------------------------------------


@router.post(
    "/repository/object",
    summary="Retrieve a single repository object (Phase 4)",
    description=(
        "Returns full metadata and source details for a single SAP repository "
        "object.  Phase 4 – not yet implemented."
    ),
)
async def get_repository_object(
    key: ObjectKey,
    service: RepositoryService = Depends(repository_service),
):
    try:
        return await service.get_object(key)
    except NotImplementedError as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
    except (SAPRFCNotAllowedError, SAPProviderNotImplementedError) as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
