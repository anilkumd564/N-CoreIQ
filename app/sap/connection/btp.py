from typing import Any

from app.core.config import Settings
from app.models.results import ConnectionTestResult
from app.sap.connection.base import SapConnectionProvider
from app.sap.exceptions import SAPProviderNotImplementedError


class BtpSapConnectionProvider(SapConnectionProvider):
    """BTP connectivity provider placeholder.

    Production implementation will resolve the configured SAP Destination and
    use BTP Connectivity/Cloud Connector for an on-premise ECC target. Phase 2
    intentionally does not implement this provider; it exists so the
    connection mode is recognized and reported in a controlled way rather
    than silently falling back to the local provider.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def ping(self) -> ConnectionTestResult:
        raise SAPProviderNotImplementedError(
            f"BTP SAP connectivity (destination '{self.settings.sap_destination_name}') "
            "is not implemented in Phase 2."
        )

    async def call_allowed_function(self, function_name: str, **parameters: Any) -> dict[str, Any]:
        raise SAPProviderNotImplementedError(
            "BTP Destination/Connectivity execution is not implemented in Phase 2."
        )
