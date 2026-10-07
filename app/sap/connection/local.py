from typing import Any

from app.core.config import Settings
from app.models.results import ConnectionTestResult
from app.sap.connection.base import SapConnectionProvider


class LocalSapConnectionProvider(SapConnectionProvider):
    """Local RFC provider placeholder.

    Phase 2 will wire this to an RFC client after the SAP NetWeaver RFC SDK
    and the approved Python RFC library are available in the developer environment.
    """

    def __init__(self, settings: Settings):
        self.settings = settings

    async def ping(self) -> ConnectionTestResult:
        configured = all(
            [
                self.settings.sap_local.host,
                self.settings.sap_local.system_number,
                self.settings.sap_local.client,
                self.settings.sap_local.user,
            ]
        )
        if not configured:
            return ConnectionTestResult(
                connected=False,
                mode="local",
                message="Local SAP RFC configuration is incomplete. No connection attempted.",
            )
        return ConnectionTestResult(
            connected=False,
            mode="local",
            message="RFC adapter not yet enabled. Foundation configuration is valid.",
        )

    async def call(self, function_name: str, **parameters: Any) -> dict[str, Any]:
        raise NotImplementedError(
            "Local RFC execution will be implemented in Phase 2 using the approved SAP RFC adapter."
        )
