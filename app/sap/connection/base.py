from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.models.results import ConnectionTestResult
from app.sap.allowlist import ensure_allowed

if TYPE_CHECKING:
    from app.models.repository import PackageResolutionResult


# ---------------------------------------------------------------------------
# Data transfer objects for OData-based discovery
# ---------------------------------------------------------------------------


@dataclass
class ODataDiscoveryResult:
    """Result of a single RepositoryObjectsSet OData call.

    The ``rows`` list contains RFC-style field dicts (OBJECT, OBJ_NAME,
    DEVCLASS, AUTHOR, MATCH_REASON, …) so that the service layer can reuse
    the existing ``_normalize_object()`` helper without modification.

    Attributes:
        rows:         Normalised row dicts from the OData response.
        truncated:    True when SAP reported that the result was truncated.
        total_matches: SAP-reported total match count (may be None).
        system_id:    SAP system ID extracted from the response (or None).
        client:       SAP client extracted from the response (or None).
        message:      Informational message from SAP (or empty string).
    """

    rows: list[dict[str, Any]] = field(default_factory=list)
    truncated: bool = False
    total_matches: int | None = None
    system_id: str | None = None
    client: str | None = None
    message: str = ""


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------


class SapConnectionProvider(ABC):
    """Abstract SAP connectivity boundary.

    Repository services depend on this contract instead of a concrete
    RFC implementation. This keeps local development and BTP deployment
    interchangeable.
    """

    @abstractmethod
    async def ping(self) -> ConnectionTestResult:
        raise NotImplementedError

    @abstractmethod
    async def call_allowed_function(
        self, function_name: str, **parameters: Any
    ) -> dict[str, Any]:
        """Invoke an RFC function module.

        Implementations must call ensure_allowed() (or equivalent) before
        issuing any RFC call so that unapproved function modules are
        rejected before they reach SAP.
        """
        raise NotImplementedError

    async def call(self, function_name: str, **parameters: Any) -> dict[str, Any]:
        """Backward-compatible entry point enforcing the RFC allow-list."""
        ensure_allowed(function_name)
        return await self.call_allowed_function(function_name, **parameters)

    async def get_packages(
        self,
        root_package: str,
        include_subpackages: bool = False,
        max_depth: int = 20,
    ) -> "PackageResolutionResult":
        """Resolve an SAP package hierarchy via PackagesSet (OData).

        Default implementation raises SAPProviderNotImplementedError.
        Subclasses that support OData package resolution must override this
        method.  RFC-only providers (local, btp) may leave this default.

        Args:
            root_package: Normalised uppercase SAP package name.
            include_subpackages: Whether to resolve child packages recursively.
            max_depth: Maximum recursion depth (0–20).

        Returns:
            PackageResolutionResult with normalized package hierarchy.

        Raises:
            SAPProviderNotImplementedError: if not overridden.
        """
        from app.sap.exceptions import SAPProviderNotImplementedError

        raise SAPProviderNotImplementedError(
            f"{type(self).__name__} does not support package resolution via OData. "
            "Configure SAP_CONNECTION_MODE=odata to enable PackagesSet."
        )

    async def discover_repository_objects(
        self,
        root_package: str | None,
        include_subpackages: bool,
        name_pattern: str | None,
        object_type: str | None,
        max_results: int,
    ) -> ODataDiscoveryResult:
        """Discover SAP repository objects via RepositoryObjectsSet (OData).

        Each call issues ONE controlled OData request against the
        RepositoryObjectsSet entity set.  The service layer orchestrates
        multi-value fan-out.

        Default implementation raises SAPProviderNotImplementedError.
        OData providers must override this method.

        Args:
            root_package:        SAP package to search (or None for pattern-only search).
            include_subpackages: Include child packages recursively.
            name_pattern:        Object name pattern, e.g. ``Z*`` (or None).
            object_type:         SAP object type, e.g. ``PROG`` (or None = all types).
            max_results:         Maximum rows to return from this single OData call.

        Returns:
            ODataDiscoveryResult with normalised rows and metadata.

        Raises:
            SAPProviderNotImplementedError: if not overridden.
        """
        from app.sap.exceptions import SAPProviderNotImplementedError

        raise SAPProviderNotImplementedError(
            f"{type(self).__name__} does not support repository object discovery via OData. "
            "Configure SAP_CONNECTION_MODE=odata to enable RepositoryObjectsSet."
        )
