"""Repository service — thin orchestration layer.

Routes Phase 3 discovery requests to RepositoryDiscoveryService, and
Phase 3 package resolution to the SAP provider's get_packages() method.

Phase 4 (source/metadata extraction) must NOT be implemented here.
"""

from __future__ import annotations

import logging

from app.models.repository import PackageResolutionResult
from app.models.requests import ObjectKey, RepositorySearchRequest
from app.models.results import RepositorySearchResponse
from app.sap.connection.base import SapConnectionProvider
from app.services.discovery import RepositoryDiscoveryService

logger = logging.getLogger("app.services.repository")

_MAX_DEPTH_UPPER = 20
_MAX_DEPTH_LOWER = 0


class RepositoryService:
    """Orchestrates repository operations.

    Delegates Phase 3 discovery to :class:`RepositoryDiscoveryService`.
    Placeholder stubs for Phase 4+ are kept here so the route layer
    does not need to change when later phases are added.
    """

    def __init__(self, connection: SapConnectionProvider) -> None:
        self._connection = connection
        self._discovery = RepositoryDiscoveryService(connection)

    # ------------------------------------------------------------------
    # Phase 3 – Repository Discovery
    # ------------------------------------------------------------------

    async def search(self, request: RepositorySearchRequest) -> RepositorySearchResponse:
        """Discover custom SAP repository objects.

        Delegates entirely to :class:`RepositoryDiscoveryService`.  All
        validation, normalisation, RFC invocation and response mapping
        live in that service.
        """
        return await self._discovery.search(request)

    # ------------------------------------------------------------------
    # Phase 3 – Package resolution (OData PackagesSet)
    # ------------------------------------------------------------------

    async def resolve_packages(
        self,
        package: str,
        include_subpackages: bool = False,
        max_depth: int = 20,
    ) -> PackageResolutionResult:
        """Resolve an SAP package hierarchy via PackagesSet (OData V2).

        Validates and normalises inputs before delegating to the SAP
        provider abstraction.  The provider is responsible for all HTTP
        communication, TLS, authentication, and response parsing.

        Args:
            package: SAP package name (trimmed and uppercased here).
            include_subpackages: Whether to resolve child packages.
            max_depth: Maximum hierarchy depth.  Must be 0–20.

        Returns:
            PackageResolutionResult with sorted, deduplicated package list.

        Raises:
            ValueError: blank package name or max_depth out of range.
            RepositoryPackageNotFoundError: SAP returned no packages (→ 404).
            SAPError subclasses: transport / SAP errors (→ 503/502).
        """
        # --- Input normalisation ----------------------------------------
        normalised = package.strip().upper()
        if not normalised:
            raise ValueError("Package name must not be blank.")

        if not (_MAX_DEPTH_LOWER <= max_depth <= _MAX_DEPTH_UPPER):
            raise ValueError(
                f"max_depth must be between {_MAX_DEPTH_LOWER} and "
                f"{_MAX_DEPTH_UPPER}, got {max_depth}."
            )

        logger.info(
            "repository_service_resolve_packages",
            extra={
                "operation": "package_resolution",
                "root_package": normalised,
                "include_subpackages": include_subpackages,
                "max_depth": max_depth,
            },
        )

        return await self._connection.get_packages(
            root_package=normalised,
            include_subpackages=include_subpackages,
            max_depth=max_depth,
        )

    # ------------------------------------------------------------------
    # Phase 4+ – Object detail (not yet implemented)
    # ------------------------------------------------------------------

    async def get_object(self, key: ObjectKey) -> None:
        """Retrieve a single repository object with full metadata.

        Phase 4 – not yet implemented.
        """
        raise NotImplementedError(
            "Phase 4: Single-object detail retrieval is not yet implemented."
        )
