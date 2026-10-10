"""Phase 9 â€” SAP Destination Service resolver.

Responsibilities:
- Obtain destination configuration from SAP BTP Destination Service
- Resolve SAP URL, authentication, proxy type, SAP client
- Cache destination metadata safely (credentials refreshed on expiry)
- Forward Cloud Connector location ID where required
- NEVER log destination credentials

Architecture:
    N-CoreIQ
      â†’ DestinationResolver (this module)
        â†’ BTP Destination Service REST API
          â†’ Destination configuration
            â†’ ODataSAPConnectionProvider (configured)
              â†’ Connectivity Service proxy (if OnPremise)
                â†’ Cloud Connector
                  â†’ SAP ECC

The resolved SapODataSettings is passed back to ODataSAPConnectionProvider.
Repository services are NEVER aware of the destination name or BTP topology.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from app.btp.bindings import BTPServiceBindingResolver, ServiceBindingCredentials
from app.core.config import Settings
from app.sap.exceptions import SAPConfigurationError, SAPConnectionError

logger = logging.getLogger(__name__)

# Destination proxy types
_PROXY_ON_PREMISE = "OnPremise"
_PROXY_INTERNET = "Internet"

# Destination authentication types we support
_AUTH_BASIC = "BasicAuthentication"
_AUTH_NO_AUTH = "NoAuthentication"
_AUTH_PRINCIPAL_PROPAGATION = "PrincipalPropagation"


@dataclass
class ResolvedDestination:
    """Resolved destination configuration.

    Contains all connectivity parameters needed to build the HTTP client.
    Credentials are present but NEVER logged (use safe_repr for diagnostics).
    """

    name: str
    url: str
    sap_client: str | None
    user: str | None
    password: str | None
    proxy_type: str  # "OnPremise" | "Internet"
    auth_type: str
    connectivity_proxy_host: str | None = None
    connectivity_proxy_port: int | None = None
    connectivity_proxy_token: str | None = None
    cloud_connector_location_id: str | None = None
    resolved_at: float = field(default_factory=time.monotonic)

    def safe_repr(self) -> dict[str, Any]:
        """Return safe diagnostic representation (no credentials)."""
        return {
            "name": self.name,
            "url": self.url,
            "sap_client": self.sap_client,
            "user": self.user,
            "password": "***" if self.password else None,
            "proxy_type": self.proxy_type,
            "auth_type": self.auth_type,
            "connectivity_proxy_host": self.connectivity_proxy_host,
            "connectivity_proxy_port": self.connectivity_proxy_port,
            "connectivity_proxy_token": "***" if self.connectivity_proxy_token else None,
            "cloud_connector_location_id": self.cloud_connector_location_id,
        }


class DestinationResolver:
    """Resolves SAP Destination Service destinations for BTP connectivity.

    Usage:
        resolver = DestinationResolver(settings, binding_resolver)
        dest = await resolver.resolve(destination_name)

    The resolved destination is cached for cache_ttl_seconds.
    Cache is invalidated and refreshed on each cache miss.
    """

    def __init__(
        self,
        settings: Settings,
        binding_resolver: BTPServiceBindingResolver | None = None,
        cache_ttl_seconds: int = 300,
    ) -> None:
        self._settings = settings
        self._resolver = binding_resolver or BTPServiceBindingResolver()
        self._cache_ttl = cache_ttl_seconds
        self._cache: dict[str, ResolvedDestination] = {}
        self._cache_times: dict[str, float] = {}

    # ------------------------------------------------------------------
    # Token acquisition for Destination Service
    # ------------------------------------------------------------------

    async def _get_destination_service_token(
        self, dest_binding: ServiceBindingCredentials
    ) -> str:
        """Obtain an OAuth2 client_credentials token for the Destination Service."""
        token_url = dest_binding.get("tokenserviceurl") or dest_binding.get("url", "")
        if token_url and not token_url.endswith("/oauth/token"):
            token_url = token_url.rstrip("/") + "/oauth/token"

        client_id = dest_binding.require("clientid")
        client_secret = dest_binding.require("clientsecret")

        try:
            async with httpx.AsyncClient(timeout=15.0, verify=True) as http:
                response = await http.post(
                    token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if response.status_code != 200:
                    raise SAPConnectionError(
                        f"Destination Service token request failed: HTTP {response.status_code}. "
                        "Verify XSUAA/Destination binding credentials."
                    )
                data = response.json()
                token = data.get("access_token")
                if not token:
                    raise SAPConnectionError(
                        "Destination Service token response missing access_token."
                    )
                return str(token)
        except httpx.RequestError as exc:
            raise SAPConnectionError(
                f"Cannot reach Destination Service token endpoint: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Connectivity Service proxy token
    # ------------------------------------------------------------------

    async def _get_connectivity_token(
        self, conn_binding: ServiceBindingCredentials
    ) -> str:
        """Obtain an OAuth2 token for the Connectivity Service proxy."""
        token_url = conn_binding.get("tokenserviceurl") or conn_binding.get("url", "")
        if token_url and not token_url.endswith("/oauth/token"):
            token_url = token_url.rstrip("/") + "/oauth/token"

        client_id = conn_binding.require("clientid")
        client_secret = conn_binding.require("clientsecret")

        try:
            async with httpx.AsyncClient(timeout=15.0, verify=True) as http:
                response = await http.post(
                    token_url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if response.status_code != 200:
                    raise SAPConnectionError(
                        f"Connectivity Service token request failed: HTTP {response.status_code}."
                    )
                data = response.json()
                token = data.get("access_token")
                if not token:
                    raise SAPConnectionError(
                        "Connectivity Service token response missing access_token."
                    )
                return str(token)
        except httpx.RequestError as exc:
            raise SAPConnectionError(
                f"Cannot reach Connectivity Service token endpoint: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Destination lookup
    # ------------------------------------------------------------------

    async def _fetch_destination(
        self,
        destination_name: str,
        dest_binding: ServiceBindingCredentials,
        token: str,
    ) -> dict[str, Any]:
        """Fetch destination configuration from the Destination Service REST API."""
        dest_service_uri = dest_binding.require("uri")
        url = f"{dest_service_uri.rstrip('/')}/destination-configuration/v1/destinations/{destination_name}"

        try:
            async with httpx.AsyncClient(timeout=15.0, verify=True) as http:
                response = await http.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                )
                if response.status_code == 404:
                    raise SAPConfigurationError(
                        f"SAP Destination '{destination_name}' not found in the Destination Service. "
                        "Create the destination in the BTP subaccount cockpit."
                    )
                if response.status_code == 401 or response.status_code == 403:
                    raise SAPConfigurationError(
                        f"Destination Service access denied for destination '{destination_name}'. "
                        "Verify the service binding has the correct permissions."
                    )
                if response.status_code != 200:
                    raise SAPConnectionError(
                        f"Destination Service returned HTTP {response.status_code} "
                        f"for destination '{destination_name}'."
                    )
                return response.json()  # type: ignore[no-any-return]
        except httpx.RequestError as exc:
            raise SAPConnectionError(
                f"Cannot reach Destination Service: {exc}"
            ) from exc

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    async def resolve(self, destination_name: str) -> ResolvedDestination:
        """Resolve a named SAP Destination, with caching.

        Args:
            destination_name: The SAP Destination name (e.g. NC1_ODATA)

        Returns:
            ResolvedDestination with all connectivity parameters populated.

        Raises:
            SAPConfigurationError: destination not found or binding missing
            SAPConnectionError: cannot reach Destination Service
        """
        # Check cache
        cached = self._cache.get(destination_name)
        cache_time = self._cache_times.get(destination_name, 0.0)
        if cached and (time.monotonic() - cache_time) < self._cache_ttl:
            logger.debug(
                "destination_cache_hit name=%s", destination_name,
                extra={"operation": "destination_resolve"}
            )
            return cached

        logger.info(
            "destination_resolve_start name=%s", destination_name,
            extra={"operation": "destination_resolve"}
        )

        # Get Destination Service binding
        dest_binding = self._resolver.get_destination()
        if not dest_binding:
            raise SAPConfigurationError(
                "SAP Destination Service is not bound. "
                "Bind the 'destination' service instance in BTP manifest.yml / mta.yaml."
            )

        # Acquire token for Destination Service
        token = await self._get_destination_service_token(dest_binding)

        # Fetch destination configuration
        dest_config = await self._fetch_destination(destination_name, dest_binding, token)

        # Parse destination properties
        dest_props = dest_config.get("destinationConfiguration", dest_config)
        if not dest_props:
            raise SAPConfigurationError(
                f"Destination '{destination_name}' returned empty configuration."
            )

        url = dest_props.get("URL", "")
        if not url:
            raise SAPConfigurationError(
                f"Destination '{destination_name}' has no URL configured."
            )

        auth_type = dest_props.get("Authentication", _AUTH_NO_AUTH)
        proxy_type = dest_props.get("ProxyType", _PROXY_INTERNET)

        # Extract SAP client â€” destination may override settings value
        sap_client = dest_props.get("sap-client") or dest_props.get("SAPClient")
        if not sap_client:
            # Fall back to settings value
            sap_client = self._settings.sap_odata.client

        # Credentials
        user = dest_props.get("User") or dest_props.get("user")
        password = dest_props.get("Password") or dest_props.get("password")

        # Connectivity proxy for OnPremise destinations
        conn_proxy_host: str | None = None
        conn_proxy_port: int | None = None
        conn_proxy_token: str | None = None

        if proxy_type == _PROXY_ON_PREMISE:
            conn_binding = self._resolver.get_connectivity()
            if not conn_binding:
                raise SAPConfigurationError(
                    f"Destination '{destination_name}' has ProxyType=OnPremise but "
                    "SAP Connectivity Service is not bound. "
                    "Bind the 'connectivity' service instance."
                )
            conn_proxy_host = conn_binding.get("onpremise_proxy_host")
            raw_port = conn_binding.get("onpremise_proxy_port")
            if raw_port:
                try:
                    conn_proxy_port = int(raw_port)
                except (TypeError, ValueError):
                    pass
            # Acquire Connectivity Service proxy token
            try:
                conn_proxy_token = await self._get_connectivity_token(conn_binding)
            except Exception as exc:
                logger.warning(
                    "destination_connectivity_token_failed name=%s error=%s",
                    destination_name, exc
                )

        # Cloud Connector location ID
        location_id = (
            dest_props.get("CloudConnectorLocationId")
            or self._settings.btp.cloud_connector_location_id
        )

        resolved = ResolvedDestination(
            name=destination_name,
            url=url,
            sap_client=sap_client,
            user=user,
            password=password,
            proxy_type=proxy_type,
            auth_type=auth_type,
            connectivity_proxy_host=conn_proxy_host,
            connectivity_proxy_port=conn_proxy_port,
            connectivity_proxy_token=conn_proxy_token,
            cloud_connector_location_id=location_id,
        )

        # Cache result
        self._cache[destination_name] = resolved
        self._cache_times[destination_name] = time.monotonic()

        logger.info(
            "destination_resolved name=%s proxy_type=%s auth_type=%s url=%s",
            destination_name,
            proxy_type,
            auth_type,
            url,
            extra={"operation": "destination_resolve"},
        )
        return resolved

    def invalidate_cache(self, destination_name: str | None = None) -> None:
        """Invalidate cached destination(s)."""
        if destination_name:
            self._cache.pop(destination_name, None)
            self._cache_times.pop(destination_name, None)
        else:
            self._cache.clear()
            self._cache_times.clear()
