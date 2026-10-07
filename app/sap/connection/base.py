from abc import ABC, abstractmethod
from typing import Any

from app.models.results import ConnectionTestResult


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
    async def call(self, function_name: str, **parameters: Any) -> dict[str, Any]:
        raise NotImplementedError
