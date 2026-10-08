"""FastAPI route definitions for N-CoreIQ.

Implements:
  GET  /api/v1/health                 – application health check
  GET  /api/v1/sap/ping               – SAP ECC connectivity test
  POST /api/v1/repository/search      – Phase 3 repository discovery
  POST /api/v1/repository/object      – Phase 4+ object detail (stub)

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
from app.sap.exceptions import (
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
        "Calls Z_MCP_REPO_PING on the configured ECC system and returns "
        "the system ID, client, and release.  Requires a reachable SAP ECC "
        "system and a configured RFC user."
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
