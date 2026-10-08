"""SAP connection provider factory.

Selects the appropriate SapConnectionProvider implementation based on
SAP_CONNECTION_MODE:

    odata  — ODataSAPConnectionProvider (preferred, Phase 2+)
             Uses httpx over HTTPS → SAP Gateway → ZNCOREIQ_REPO_SRV.

    local  — LocalSapConnectionProvider (legacy/optional)
             Uses PyRFC + SAP NetWeaver RFC SDK for direct RFC connectivity.
             Requires the proprietary SDK to be installed separately.

    btp    — BtpSapConnectionProvider (placeholder)
             Will use BTP Destination / Cloud Connector in a future phase.

The factory never falls back silently between modes. The configured mode is
used exactly as specified; a mis-configured mode surfaces a clear error.
"""

from app.core.config import Settings
from app.sap.connection.base import SapConnectionProvider
from app.sap.connection.btp import BtpSapConnectionProvider
from app.sap.connection.local import LocalSapConnectionProvider
from app.sap.connection.odata import ODataSAPConnectionProvider
from app.sap.exceptions import SAPConfigurationError


def build_sap_connection_provider(settings: Settings) -> SapConnectionProvider:
    """Return the provider for the configured SAP_CONNECTION_MODE.

    Raises:
        SAPConfigurationError: if the connection mode is not recognised.
    """
    mode = settings.sap_connection_mode

    if mode == "odata":
        return ODataSAPConnectionProvider(settings)

    if mode == "btp":
        return BtpSapConnectionProvider(settings)

    if mode == "local":
        return LocalSapConnectionProvider(settings)

    # Guard against any future Literal extension that slips through.
    raise SAPConfigurationError(
        f"Unknown SAP_CONNECTION_MODE '{mode}'. "
        "Supported values: odata, local, btp."
    )
