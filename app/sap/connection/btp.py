from typing import Any

from app.core.config import Settings
from app.models.results import ConnectionTestResult
from app.sap.connection.base import SapConnectionProvider


class BtpSapConnectionProvider(SapConnectionProvider):
    """BTP connectivity provider placeholder.

    Production implementation will resolve the configured SAP Destination and
    use BTP Connectivity/Cloud Connector for an on-premise ECC target.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def ping(self) -> ConnectionTestResult:
        return ConnectionTestResult(
            connected=False,
            mode="btp",
            message=(
                f"BTP provider selected for destination '{self.settings.sap_destination_name}'. "
                "Service-binding integration is scaffolded but not activated in Version 0.1."
            ),
        )

    async def call(self, function_name: str, **parameters: Any) -> dict[str, Any]:
        raise NotImplementedError(
            "BTP Destination/Connectivity execution will be implemented after local RFC discovery is proven."
        )
