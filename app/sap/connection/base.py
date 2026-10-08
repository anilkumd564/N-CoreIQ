from abc import ABC, abstractmethod
from typing import Any

from app.models.results import ConnectionTestResult
from app.sap.allowlist import ensure_allowed


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
