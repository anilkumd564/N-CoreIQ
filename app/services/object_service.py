"""Phase 4 — Repository Object Service.

Handles metadata and source extraction for individual SAP repository objects.
This service is deliberately separate from RepositoryDiscoveryService to
keep responsibilities clear:

  RepositoryDiscoveryService  → search/discovery (lightweight, no N+1)
  RepositoryObjectService     → one object's metadata or source (explicit call)

Architecture:
    FastAPI route
      → RepositoryObjectService (this module)
        → SapConnectionProvider
          → ODataSAPConnectionProvider
            → ObjectMetadataSet  (get_object_metadata)
            → SourceLinesSet     (get_object_source)
"""

from __future__ import annotations

import logging

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.models.phase4 import (
    DEFAULT_MAX_SOURCE_LINES,
    MAX_SOURCE_LINES_HARD_LIMIT,
    MIN_SOURCE_LINES,
    PHASE4_SUPPORTED_OBJECT_TYPES,
    RepositoryObjectMetadata,
    RepositoryObjectSource,
)
from app.sap.connection.base import SapConnectionProvider
from app.sap.exceptions import RepositoryObjectTypeNotSupportedError

logger = logging.getLogger("app.services.object_service")


class RepositoryObjectService:
    """Object-level metadata and source extraction service (Phase 4).

    Responsibilities:
      - Input normalisation (trim, uppercase)
      - Supported-type validation
      - Provider invocation (one OData call per operation)
      - Canonical model conversion (delegated to the provider parser)
      - Error normalisation (provider exceptions propagate to the route handler)

    The FastAPI route must NOT call httpx or SAP APIs directly.
    """

    def __init__(self, provider: SapConnectionProvider) -> None:
        self._provider = provider

    # ------------------------------------------------------------------
    # Normalisation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _normalise_object_type(raw: str) -> str:
        """Trim and uppercase an object type string.

        Raises:
            ValueError: if the result is blank.
        """
        normalised = raw.strip().upper()
        if not normalised:
            raise ValueError("object_type must not be blank.")
        return normalised

    @staticmethod
    def _normalise_object_name(raw: str) -> str:
        """Trim and uppercase an object name string.

        Raises:
            ValueError: if the result is blank.
        """
        normalised = raw.strip().upper()
        if not normalised:
            raise ValueError("object_name must not be blank.")
        return normalised

    @staticmethod
    def _validate_object_type(object_type: str) -> None:
        """Raise RepositoryObjectTypeNotSupportedError if the type is unsupported."""
        if object_type not in PHASE4_SUPPORTED_OBJECT_TYPES:
            raise RepositoryObjectTypeNotSupportedError(
                object_type, PHASE4_SUPPORTED_OBJECT_TYPES
            )

    @staticmethod
    def _validate_max_lines(max_lines: int) -> None:
        """Raise ValueError if max_lines is outside the permitted range.

        Permitted range: MIN_SOURCE_LINES (1) to MAX_SOURCE_LINES_HARD_LIMIT (20000).
        """
        if max_lines < MIN_SOURCE_LINES:
            raise ValueError(
                f"max_lines must be at least {MIN_SOURCE_LINES}. "
                f"Received: {max_lines}."
            )
        if max_lines > MAX_SOURCE_LINES_HARD_LIMIT:
            raise ValueError(
                f"max_lines must not exceed {MAX_SOURCE_LINES_HARD_LIMIT}. "
                f"Received: {max_lines}."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_metadata(
        self,
        object_type: str,
        object_name: str,
    ) -> RepositoryObjectMetadata:
        """Retrieve technical metadata for a single SAP repository object.

        Normalises input, validates the object type, then calls the provider's
        get_object_metadata() which issues one OData request to ObjectMetadataSet.

        Args:
            object_type: SAP object type string (e.g. ``'PROG'``).  Will be
                         trimmed and uppercased.
            object_name: SAP object name (e.g. ``'ZMY_PROGRAM'``).  Will be
                         trimmed and uppercased.

        Returns:
            RepositoryObjectMetadata populated from the SAP OData response.

        Raises:
            ValueError: on blank identifiers.
            RepositoryObjectTypeNotSupportedError: on unsupported type (→ HTTP 422).
            RepositoryObjectNotFoundError: if SAP reports Found=false (→ HTTP 404).
            SAPError subclass: on connectivity/parse failure (→ HTTP 503).
        """
        norm_type = self._normalise_object_type(object_type)
        norm_name = self._normalise_object_name(object_name)
        self._validate_object_type(norm_type)

        logger.info(
            "object_service_metadata_start",
            extra={
                "operation": "get_metadata",
                "object_type": norm_type,
                "object_name": norm_name,
            },
        )

        result = await self._provider.get_object_metadata(
            object_type=norm_type,
            object_name=norm_name,
        )

        logger.info(
            "object_service_metadata_complete",
            extra={
                "operation": "get_metadata",
                "object_type": norm_type,
                "object_name": norm_name,
                "found": result.found,
                "system_id": result.system_id,
            },
        )
        return result

    async def get_source(
        self,
        object_type: str,
        object_name: str,
        max_lines: int = DEFAULT_MAX_SOURCE_LINES,
    ) -> RepositoryObjectSource:
        """Retrieve source or structured technical definition for one SAP object.

        Normalises input, validates type and max_lines, then calls the
        provider's get_object_source() which issues one OData request to
        SourceLinesSet.

        Args:
            object_type: SAP object type string (e.g. ``'PROG'``).  Will be
                         trimmed and uppercased.
            object_name: SAP object name (e.g. ``'ZMY_PROGRAM'``).  Will be
                         trimmed and uppercased.
            max_lines:   Maximum source lines to retrieve (1–20000).  Default: 5000.

        Returns:
            RepositoryObjectSource with ordered components and source lines.

        Raises:
            ValueError: on blank identifiers or invalid max_lines.
            RepositoryObjectTypeNotSupportedError: on unsupported type (→ HTTP 422).
            RepositoryObjectNotFoundError: if SAP reports Found=false (→ HTTP 404).
            RepositorySourceDataIntegrityError: on conflicting line numbers (→ HTTP 503).
            SAPError subclass: on connectivity/parse failure (→ HTTP 503).
        """
        norm_type = self._normalise_object_type(object_type)
        norm_name = self._normalise_object_name(object_name)
        self._validate_object_type(norm_type)
        self._validate_max_lines(max_lines)

        logger.info(
            "object_service_source_start",
            extra={
                "operation": "get_source",
                "object_type": norm_type,
                "object_name": norm_name,
                "max_lines": max_lines,
            },
        )

        result = await self._provider.get_object_source(
            object_type=norm_type,
            object_name=norm_name,
            max_lines=max_lines,
        )

        logger.info(
            "object_service_source_complete",
            extra={
                "operation": "get_source",
                "object_type": norm_type,
                "object_name": norm_name,
                "returned_lines": result.returned_lines,
                "component_count": result.component_count,
                "truncated": result.truncated,
                "found": result.found,
            },
        )
        return result


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


def get_object_service(
    settings: Settings = Depends(get_settings),
) -> RepositoryObjectService:
    """FastAPI dependency that constructs a :class:`RepositoryObjectService`.

    Resolves the configured SAP connection provider via
    :func:`build_sap_connection_provider` and injects it into the service.

    Usage in a route::

        @router.get("/...")
        async def my_route(
            service: RepositoryObjectService = Depends(get_object_service),
        ):
            ...
    """
    from app.sap.connection.factory import build_sap_connection_provider

    return RepositoryObjectService(build_sap_connection_provider(settings))
