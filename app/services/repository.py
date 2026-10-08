"""Repository service — thin orchestration layer.

Routes Phase 3 discovery requests to RepositoryDiscoveryService and retains
stub stubs for future phases (object detail, export, etc.).

Phase 4 (source/metadata extraction) must NOT be implemented here.
"""

from __future__ import annotations

from app.models.requests import ObjectKey, RepositorySearchRequest
from app.models.results import RepositorySearchResponse
from app.sap.connection.base import SapConnectionProvider
from app.services.discovery import RepositoryDiscoveryService


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
    # Phase 4+ – Object detail (not yet implemented)
    # ------------------------------------------------------------------

    async def get_object(self, key: ObjectKey) -> None:
        """Retrieve a single repository object with full metadata.

        Phase 4 – not yet implemented.
        """
        raise NotImplementedError(
            "Phase 4: Single-object detail retrieval is not yet implemented."
        )
