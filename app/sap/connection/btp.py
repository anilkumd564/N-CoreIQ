"""Phase 9 — BTP SAP Connection Provider.

Implements SAP connectivity via:
  BTP Destination Service → Connectivity Service → Cloud Connector → SAP ECC

Architecture:
    ODataSAPConnectionProvider receives a fully-resolved SapODataSettings
    constructed from the DestinationResolver result.  Repository services
    are completely unaware of BTP topology.

Connection chain:
    N-CoreIQ
      → BtpSapConnectionProvider (this module)
        → DestinationResolver
          → Destination Service (token + config)
          → Connectivity Service proxy (if OnPremise)
        → ODataSAPConnectionProvider (re-used, configured from destination)
          → SAP Gateway OData V2
            → SAP ECC / ZNCOREIQ_REPO_SRV

Security:
    - Destination credentials are NEVER logged.
    - Connectivity proxy token is forwarded via Proxy-Authorization header.
    - TLS verification is always enabled (verify=True).
    - Cloud Connector location ID forwarded only when configured.
"""
from __future__ import annotations

import logging
from typing import Any

from app.btp.bindings import BTPServiceBindingResolver
from app.btp.destination import DestinationResolver, ResolvedDestination
from app.core.config import Settings, SapODataSettings
from app.models.phase4 import RepositoryObjectMetadata, RepositoryObjectSource
from app.models.phase8 import DirectDependencyResult
from app.models.repository import PackageResolutionResult
from app.models.results import ConnectionTestResult
from app.sap.connection.base import ODataDiscoveryResult, SapConnectionProvider
from app.sap.connection.odata import ODataSAPConnectionProvider
from app.sap.exceptions import SAPConfigurationError, SAPProviderNotImplementedError

logger = logging.getLogger(__name__)


def _build_odata_settings_from_destination(
    base_settings: Settings,
    dest: ResolvedDestination,
) -> SapODataSettings:
    """Build SapODataSettings from a resolved BTP Destination.

    SAP client precedence:
        1. Destination sap-client property
        2. Explicit N-CoreIQ sap_odata.client setting
        3. Default "800"

    Returns a new SapODataSettings configured for the resolved destination.
    """
    sap_client = (
        dest.sap_client
        or base_settings.sap_odata.client
        or "800"
    )

    # Build extra headers for Connectivity Service proxy
    # (passed through via the httpx client via proxy config)

    return SapODataSettings(
        base_url=dest.url,
        service=base_settings.sap_odata.service,
        client=sap_client,
        user=dest.user,
        password=dest.password,
        timeout=base_settings.sap_odata.timeout,
        # TLS always verified in BTP — never disable
        skip_tls_verify=False,
        skip_hostname_check=False,
        ca_bundle=None,
        # Retry settings from base configuration
        retry_attempts=base_settings.sap_odata.retry_attempts,
        retry_backoff_base=base_settings.sap_odata.retry_backoff_base,
        retry_backoff_max=base_settings.sap_odata.retry_backoff_max,
        # Pool settings from base configuration
        http_connect_timeout=base_settings.sap_odata.http_connect_timeout,
        http_read_timeout=base_settings.sap_odata.http_read_timeout,
        http_pool_connections=base_settings.sap_odata.http_pool_connections,
        http_keepalive_expiry=base_settings.sap_odata.http_keepalive_expiry,
    )


class BtpSapConnectionProvider(SapConnectionProvider):
    """BTP SAP connectivity provider — Phase 9 production implementation.

    Resolves the configured SAP Destination and delegates all SAP OData
    calls to ODataSAPConnectionProvider configured with the resolved
    destination parameters.

    The Connectivity Service proxy (for OnPremise destinations) is
    configured transparently inside the HTTP client setup.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._binding_resolver = BTPServiceBindingResolver()
        self._destination_resolver = DestinationResolver(
            settings=settings,
            binding_resolver=self._binding_resolver,
        )
        self._destination_name = (
            settings.btp.sap_destination_name
            or settings.sap_destination_name
            or "NC1_ODATA"
        )

    async def _get_odata_provider(self) -> ODataSAPConnectionProvider:
        """Resolve the Destination and return a configured OData provider."""
        dest = await self._destination_resolver.resolve(self._destination_name)
        odata_settings = _build_odata_settings_from_destination(self.settings, dest)

        # Build a temporary Settings-like object to pass to ODataSAPConnectionProvider.
        # We create a shallow copy of settings with sap_odata replaced.
        import copy
        patched_settings = copy.copy(self.settings)
        object.__setattr__(patched_settings, "sap_odata", odata_settings)

        provider = ODataSAPConnectionProvider(patched_settings)

        # If the destination is OnPremise and we have a connectivity proxy,
        # inject proxy configuration into the provider
        if dest.proxy_type == "OnPremise" and dest.connectivity_proxy_host:
            provider._btp_proxy_host = dest.connectivity_proxy_host
            provider._btp_proxy_port = dest.connectivity_proxy_port
            provider._btp_proxy_token = dest.connectivity_proxy_token
            provider._btp_location_id = dest.cloud_connector_location_id

        return provider

    # ------------------------------------------------------------------
    # SapConnectionProvider interface — delegates to OData provider
    # ------------------------------------------------------------------

    async def ping(self) -> ConnectionTestResult:
        """Test SAP connectivity via BTP Destination Service."""
        logger.info(
            "btp_provider_ping destination=%s", self._destination_name,
            extra={"operation": "btp_ping"}
        )
        provider = await self._get_odata_provider()
        result = await provider.ping()
        # Override connection_mode to reflect BTP
        return ConnectionTestResult(
            status=result.status,
            connection_mode="btp",
            system_id=result.system_id,
            client=result.client,
            sap_release=result.sap_release,
            timestamp=result.timestamp,
            ping_method=result.ping_method,
            message=result.message,
        )

    async def call_allowed_function(
        self, function_name: str, **parameters: Any
    ) -> dict[str, Any]:
        """Not supported — use OData entity sets."""
        raise SAPProviderNotImplementedError(
            "BtpSapConnectionProvider does not support RFC function calls. "
            "Use the OData entity sets (SystemInfoSet, PackagesSet, etc.)."
        )

    async def get_packages(
        self,
        root_package: str,
        include_subpackages: bool = False,
        max_depth: int = 20,
    ) -> PackageResolutionResult:
        provider = await self._get_odata_provider()
        return await provider.get_packages(root_package, include_subpackages, max_depth)

    async def discover_repository_objects(
        self,
        root_package: str | None,
        include_subpackages: bool,
        name_pattern: str | None,
        object_type: str | None,
        max_results: int,
    ) -> ODataDiscoveryResult:
        provider = await self._get_odata_provider()
        return await provider.discover_repository_objects(
            root_package, include_subpackages, name_pattern, object_type, max_results
        )

    async def get_object_metadata(
        self,
        object_type: str,
        object_name: str,
    ) -> RepositoryObjectMetadata:
        provider = await self._get_odata_provider()
        return await provider.get_object_metadata(object_type, object_name)

    async def get_object_source(
        self,
        object_type: str,
        object_name: str,
        max_lines: int = 5000,
    ) -> RepositoryObjectSource:
        provider = await self._get_odata_provider()
        return await provider.get_object_source(object_type, object_name, max_lines)

    async def get_object_dependencies(
        self,
        object_type: str,
        object_name: str,
        max_results: int = 1000,
    ) -> DirectDependencyResult:
        provider = await self._get_odata_provider()
        return await provider.get_object_dependencies(object_type, object_name, max_results)
