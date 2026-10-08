import logging

from fastapi import APIRouter, Depends, HTTPException

from app.core.config import Settings, get_settings
from app.models.requests import ObjectKey, RepositorySearchRequest
from app.models.results import ConnectionTestResult, HealthResult, SearchResult
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


@router.get("/health", response_model=HealthResult)
async def health(settings: Settings = Depends(get_settings)) -> HealthResult:
    return HealthResult(
        status="ok",
        app=settings.app_name,
        version=settings.app_version,
        environment=settings.app_env,
    )


@router.get("/sap/ping", response_model=ConnectionTestResult)
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


@router.post("/repository/search", response_model=SearchResult)
async def search_repository(
    request: RepositorySearchRequest,
    service: RepositoryService = Depends(repository_service),
) -> SearchResult:
    try:
        return await service.search(request)
    except (NotImplementedError, SAPRFCNotAllowedError) as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc


@router.post("/repository/object")
async def get_repository_object(
    key: ObjectKey,
    service: RepositoryService = Depends(repository_service),
):
    try:
        return await service.get_object(key)
    except (NotImplementedError, SAPRFCNotAllowedError) as exc:
        raise HTTPException(status_code=501, detail=str(exc)) from exc
