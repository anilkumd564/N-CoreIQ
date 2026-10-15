"""Phase 8 — Dependency & Relationship Intelligence REST API endpoints.

Endpoints:
    GET  /api/v1/repository/objects/{object_type}/{object_name}/dependencies
    POST /api/v1/repository/dependencies/traverse
    POST /api/v1/repository/dependencies/reverse
    POST /api/v1/repository/dependencies/impact

All endpoints are read-only. No SAP write operations are performed.
Route handlers delegate all logic to RepositoryDependencyService.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query

from app.auth.authorization import require_scope
from app.auth.principal import AuthenticatedPrincipal

from app.core.config import Settings, get_settings
from app.models.phase8 import (
    MAX_RESULTS_DEFAULT,
    MAX_RESULTS_HARD,
    DependencyGraph,
    DependencyImpactRequest,
    DependencyImpactResult,
    DependencyTraversalRequest,
    DirectDependencyResult,
    RepositoryObjectReference,
    ReverseDependencyRequest,
    ReverseDependencyResult,
)
from app.sap.connection.factory import build_sap_connection_provider
from app.sap.exceptions import (
    DependencyDataIntegrityError,
    DependencyObjectNotFoundError,
    DependencyProviderError,
    DependencyScopeTooLargeError,
    DependencyTraversalLimitError,
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPConfigurationError,
    SAPConnectionError,
    SAPFunctionExecutionError,
    SAPProviderNotImplementedError,
)
from app.services.dependency import RepositoryDependencyService
from app.services.discovery import RepositoryDiscoveryService

logger = logging.getLogger("app.api.dependencies")

deps_router = APIRouter()


def get_dependency_service(
    settings: Settings = Depends(get_settings),
) -> RepositoryDependencyService:
    """FastAPI dependency that constructs a RepositoryDependencyService."""
    provider = build_sap_connection_provider(settings)
    discovery = RepositoryDiscoveryService(provider)
    return RepositoryDependencyService(provider=provider, discovery_service=discovery)


def _handle_sap_exceptions(exc: Exception, operation: str) -> HTTPException:
    """Map domain exceptions to HTTP responses, never exposing SAP internals."""
    if isinstance(exc, DependencyObjectNotFoundError):
        logger.info(
            "dependency_object_not_found",
            extra={"operation": operation, "object_type": exc.object_type, "object_name": exc.object_name},
        )
        return HTTPException(status_code=404, detail=str(exc))

    if isinstance(exc, (DependencyScopeTooLargeError, DependencyTraversalLimitError, ValueError)):
        logger.warning("dependency_validation_error", extra={"operation": operation, "detail": str(exc)})
        return HTTPException(status_code=422, detail=str(exc))

    if isinstance(exc, DependencyDataIntegrityError):
        logger.error("dependency_data_integrity_error", extra={"operation": operation})
        return HTTPException(status_code=503, detail=str(exc))

    if isinstance(exc, SAPConfigurationError):
        logger.warning("dependency_config_invalid", extra={"operation": operation})
        return HTTPException(status_code=422, detail=str(exc))

    if isinstance(exc, SAPProviderNotImplementedError):
        logger.warning("dependency_provider_not_implemented", extra={"operation": operation})
        return HTTPException(status_code=501, detail=str(exc))

    if isinstance(exc, SAPAuthenticationError):
        logger.warning("dependency_auth_failed", extra={"operation": operation})
        return HTTPException(status_code=503, detail=str(exc))

    if isinstance(exc, SAPConnectionError):
        logger.warning("dependency_unreachable", extra={"operation": operation})
        return HTTPException(status_code=503, detail=str(exc))

    if isinstance(exc, (DependencyProviderError, SAPFunctionExecutionError, SAPCommunicationError)):
        logger.warning("dependency_communication_failed", extra={"operation": operation})
        return HTTPException(status_code=503, detail=str(exc))

    # Generic fallback
    logger.error("dependency_unexpected_error", extra={"operation": operation}, exc_info=True)
    return HTTPException(
        status_code=503,
        detail="An unexpected error occurred during dependency analysis.",
    )


# ---------------------------------------------------------------------------
# GET /repository/objects/{object_type}/{object_name}/dependencies
# ---------------------------------------------------------------------------


@deps_router.get(
    "/repository/objects/{object_type}/{object_name}/dependencies",
    response_model=DirectDependencyResult,
    summary="Get direct SAP object dependencies",
    tags=["dependencies", "phase8"],
    description=(
        "Returns direct dependency relationships for a SAP repository object "
        "by calling **DependencyRelationsSet** on ZNCOREIQ_REPO_SRV.\n\n"
        "**Supported source object types:** PROG, CLAS, INTF, FUGR, FUNC, TABL, VIEW, DTEL, TRAN\n\n"
        "**Query parameters:**\n"
        "- `relationship_type` — optional, repeatable; filter to specific relationship types\n"
        "- `min_confidence` — optional; minimum confidence (EXACT, HIGH, MEDIUM, LOW)\n"
        "- `max_results` — maximum dependency rows (1–10000, default 1000)\n\n"
        "**Example:**\n"
        "```\n"
        "GET /api/v1/repository/objects/PROG/ZFI_REPORT/dependencies"
        "?min_confidence=MEDIUM&max_results=500\n"
        "```\n\n"
        "**Status codes:**\n"
        "- `200` — Dependencies returned (may be empty list with zero dependencies).\n"
        "- `404` — Object does not exist.\n"
        "- `422` — Unsupported object type, blank name, or invalid parameters.\n"
        "- `503` — SAP connectivity failure.\n"
    ),
)
async def get_object_dependencies(
    object_type: str,
    object_name: str,
    relationship_type: Annotated[list[str] | None, Query()] = None,
    min_confidence: str | None = None,
    max_results: int = MAX_RESULTS_DEFAULT,
    _auth: AuthenticatedPrincipal = Depends(require_scope("get_object_dependencies")),
    service: RepositoryDependencyService = Depends(get_dependency_service),
) -> DirectDependencyResult:
    try:
        return await service.get_direct_dependencies(
            object_type=object_type,
            object_name=object_name,
            relationship_types=relationship_type,
            min_confidence=min_confidence,
            max_results=max_results,
        )
    except Exception as exc:
        raise _handle_sap_exceptions(exc, "get_object_dependencies") from exc


# ---------------------------------------------------------------------------
# POST /repository/dependencies/traverse
# ---------------------------------------------------------------------------


@deps_router.post(
    "/repository/dependencies/traverse",
    response_model=DependencyGraph,
    summary="Forward dependency traversal (bounded BFS)",
    tags=["dependencies", "phase8"],
    description=(
        "Performs bounded forward BFS traversal of SAP dependency relationships.\n\n"
        "Uses a request-local cache — each object's dependencies are fetched only once "
        "even if encountered at multiple depths.\n\n"
        "**Cycle detection:** cycles are detected and marked; traversal terminates safely.\n\n"
        "**Dynamic targets** (`<DYNAMIC>`) are preserved as edges but never traversed.\n\n"
        "**Hard limits:** max_depth ≤ 5, max_nodes ≤ 5000, max_edges ≤ 20000\n\n"
        "**Example:**\n"
        "```json\n"
        '{"root_object": {"sap_object_type": "PROG", "object_name": "ZFI_REPORT"},\n'
        ' "max_depth": 2, "max_nodes": 200, "max_edges": 500}\n'
        "```\n\n"
        "**Status codes:**\n"
        "- `200` — Traversal completed (may be truncated).\n"
        "- `422` — Invalid request parameters.\n"
        "- `503` — SAP connectivity failure.\n"
    ),
)
async def traverse_dependencies(
    request: DependencyTraversalRequest,
    _auth: AuthenticatedPrincipal = Depends(require_scope("traverse_dependencies")),
    service: RepositoryDependencyService = Depends(get_dependency_service),
) -> DependencyGraph:
    try:
        return await service.traverse_dependencies(request)
    except Exception as exc:
        raise _handle_sap_exceptions(exc, "traverse_dependencies") from exc


# ---------------------------------------------------------------------------
# POST /repository/dependencies/reverse
# ---------------------------------------------------------------------------


@deps_router.post(
    "/repository/dependencies/reverse",
    response_model=ReverseDependencyResult,
    summary="Bounded reverse dependency lookup",
    tags=["dependencies", "phase8"],
    description=(
        "Finds objects that depend on the specified target object, within a "
        "bounded candidate scope.\n\n"
        "**A scope is required** — reverse lookup never scans the entire repository.\n"
        "Scope can be: package(s), name patterns, or all_custom_objects=true.\n\n"
        "**Algorithm:**\n"
        "1. Discover candidate source objects using the provided scope.\n"
        "2. Fetch each candidate's direct dependencies (bounded concurrency).\n"
        "3. Match edges whose target equals the requested object.\n"
        "4. Deduplicate and return dependents.\n\n"
        "**Default limits:** max_candidates=100 (hard max: 1000)\n\n"
        "**Example:**\n"
        "```json\n"
        '{"target_object": {"sap_object_type": "FUNC", "object_name": "ZFI_GET_DATA"},\n'
        ' "scope": {"packages": ["ZFINANCE"], "include_subpackages": true}}\n'
        "```\n\n"
        "**Status codes:**\n"
        "- `200` — Reverse lookup completed. Empty dependents list = no dependents found.\n"
        "- `422` — Missing scope or invalid parameters.\n"
        "- `503` — SAP connectivity failure.\n"
    ),
)
async def get_reverse_dependencies(
    request: ReverseDependencyRequest,
    _auth: AuthenticatedPrincipal = Depends(require_scope("get_reverse_dependencies")),
    service: RepositoryDependencyService = Depends(get_dependency_service),
) -> ReverseDependencyResult:
    try:
        return await service.get_reverse_dependencies(request)
    except Exception as exc:
        raise _handle_sap_exceptions(exc, "get_reverse_dependencies") from exc


# ---------------------------------------------------------------------------
# POST /repository/dependencies/impact
# ---------------------------------------------------------------------------


@deps_router.post(
    "/repository/dependencies/impact",
    response_model=DependencyImpactResult,
    summary="Deterministic dependency impact analysis",
    tags=["dependencies", "phase8"],
    description=(
        "Performs deterministic technical impact analysis for a SAP repository object.\n\n"
        "**No AI or LLM is involved.** All analysis is computed from SAP dependency data.\n\n"
        "Returns the set of objects that depend on the target, relationship distributions, "
        "confidence distributions, and dynamic reference counts.\n\n"
        "**A scope is required** — impact analysis never scans the entire repository.\n\n"
        "**Example:**\n"
        "```json\n"
        '{"object": {"sap_object_type": "TABL", "object_name": "ZFIN_POSITIONS"},\n'
        ' "scope": {"packages": ["ZFINANCE"], "include_subpackages": true}}\n'
        "```\n\n"
        "**Status codes:**\n"
        "- `200` — Impact analysis completed.\n"
        "- `422` — Missing scope or invalid parameters.\n"
        "- `503` — SAP connectivity failure.\n"
    ),
)
async def get_impact_analysis(
    request: DependencyImpactRequest,
    _auth: AuthenticatedPrincipal = Depends(require_scope("get_impact_analysis")),
    service: RepositoryDependencyService = Depends(get_dependency_service),
) -> DependencyImpactResult:
    try:
        return await service.get_impact_analysis(request)
    except Exception as exc:
        raise _handle_sap_exceptions(exc, "get_impact_analysis") from exc
