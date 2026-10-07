from app.core.config import Settings
from app.sap.connection.base import SapConnectionProvider
from app.sap.connection.btp import BtpSapConnectionProvider
from app.sap.connection.local import LocalSapConnectionProvider


def build_sap_connection_provider(settings: Settings) -> SapConnectionProvider:
    if settings.sap_connection_mode == "btp":
        return BtpSapConnectionProvider(settings)
    return LocalSapConnectionProvider(settings)
