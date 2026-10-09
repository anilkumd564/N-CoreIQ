"""SAP Gateway OData V2 connection provider.

Uses httpx over HTTPS to communicate with the SAP Gateway (transaction
/IWFND/MAINT_SERVICE) instead of the proprietary PyRFC / SAP NetWeaver RFC SDK.

This is the preferred transport for Phase 2+ connectivity.  The PyRFC-based
LocalSapConnectionProvider remains available as a legacy/optional transport
(SAP_CONNECTION_MODE=local), but OData is now the primary supported mode
(SAP_CONNECTION_MODE=odata).

Architecture:
    FastAPI route
      → SAP service layer
        → ODataSAPConnectionProvider (this module)
          → httpx
            → HTTPS
              → SAP Gateway (ZNCOREIQ_REPO_SRV)
                → SystemInfoSet / PackagesSet
                  → SAP ECC
"""

from __future__ import annotations

import logging
import ssl
import time
from typing import Any

import httpx

try:
    import truststore as _truststore  # type: ignore[import-untyped]

    _TRUSTSTORE_AVAILABLE = True
except ImportError:  # pragma: no cover
    _truststore = None  # type: ignore[assignment]
    _TRUSTSTORE_AVAILABLE = False

from app.core.config import Settings
from app.models.phase4 import (
    RepositoryObjectMetadata,
    RepositoryObjectSource,
    RepositorySourceComponent,
    RepositorySourceLine,
)
from app.models.repository import PackageResolutionResult, RepositoryPackage
from app.models.results import ConnectionTestResult
from app.sap.connection.base import ODataDiscoveryResult, SapConnectionProvider
from app.sap.exceptions import (
    RepositoryObjectNotFoundError,
    RepositoryPackageNotFoundError,
    RepositorySourceDataIntegrityError,
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPConfigurationError,
    SAPConnectionError,
    SAPFunctionExecutionError,
    SAPGatewayError,
    SAPProviderNotImplementedError,
    SAPResponseParseError,
    SAPTimeoutError,
    SAPTLSError,
)

logger = logging.getLogger("app.sap.connection.odata")

# SAP OData service path prefix — constant across all SAP Gateway deployments.
_ODATA_SERVICE_PATH = "/sap/opu/odata/sap"

# SAP OData Status values that indicate a successful response from ECC.
_SAP_SUCCESS_STATUSES = {"OK", "S", "SUCCESS"}


class ODataSAPConnectionProvider(SapConnectionProvider):
    """SAP Gateway OData V2 connection provider.

    Connects to SAP ECC via HTTP Basic Auth over HTTPS to the configured
    SAP Gateway endpoint.  The SystemInfoSet entity set on the
    ZNCOREIQ_REPO_SRV service is used to verify connectivity and retrieve
    system identification data.

    Configuration is sourced entirely from the injected Settings object.
    Credentials are never logged, serialised, or included in exceptions.
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    # ------------------------------------------------------------------
    # Configuration helpers
    # ------------------------------------------------------------------

    def _require_configuration(self) -> None:
        """Raise SAPConfigurationError if any required OData field is absent."""
        odata = self.settings.sap_odata
        missing: list[str] = []
        if not odata.base_url:
            missing.append("SAP_ODATA__BASE_URL")
        if not odata.user:
            missing.append("SAP_ODATA__USER")
        if not odata.password:
            missing.append("SAP_ODATA__PASSWORD")
        if missing:
            raise SAPConfigurationError(
                "OData SAP configuration is incomplete. Missing: "
                + ", ".join(missing)
            )

    def _service_root(self) -> str:
        """Return the fully-qualified OData service root URL without trailing slash."""
        odata = self.settings.sap_odata
        base = (odata.base_url or "").rstrip("/")
        service = odata.service.strip("/")
        return f"{base}{_ODATA_SERVICE_PATH}/{service}"

    def _tls_verify(self) -> "bool | str | ssl.SSLContext":
        """Return the httpx TLS verification setting."""
        odata = self.settings.sap_odata
        ca_bundle = odata.ca_bundle
        skip_hostname = odata.skip_hostname_check

        # Priority 0: development-only full TLS bypass.
        if odata.skip_tls_verify:
            logger.warning(
                "sap_odata_tls_verification_disabled",
                extra={
                    "operation": "tls_config",
                    "warning": (
                        "SAP_ODATA__SKIP_TLS_VERIFY=true — TLS certificate "
                        "verification is DISABLED. MUST be False in production."
                    ),
                },
            )
            return False

        if ca_bundle and not skip_hostname:
            return ca_bundle

        if ca_bundle and skip_hostname:
            ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            ctx.verify_mode = ssl.CERT_REQUIRED
            ctx.check_hostname = False
            ctx.load_verify_locations(ca_bundle)
            return ctx

        if skip_hostname:
            if _TRUSTSTORE_AVAILABLE:
                ctx = _truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            else:
                ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
                ctx.load_default_certs()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_REQUIRED
            return ctx

        if _TRUSTSTORE_AVAILABLE:
            ctx = _truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
            return ctx

        return True  # pragma: no cover

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _build_client(self) -> httpx.AsyncClient:
        """Build an httpx.AsyncClient with auth, TLS, and timeout configured."""
        odata = self.settings.sap_odata
        auth = httpx.BasicAuth(
            username=odata.user or "",
            password=odata.password or "",
        )
        return httpx.AsyncClient(
            auth=auth,
            verify=self._tls_verify(),
            timeout=float(odata.timeout),
            headers={"Accept": "application/json"},
        )

    @staticmethod
    def _map_http_exception(exc: httpx.RequestError) -> SAPConnectionError:
        """Map an httpx network-level exception to a controlled domain error."""
        exc_str = str(exc).lower()
        if isinstance(exc, httpx.ConnectError) and (
            "ssl" in exc_str
            or "certificate" in exc_str
            or "tls" in exc_str
            or "verify" in exc_str
        ):
            if "hostname mismatch" in exc_str or "not valid for" in exc_str:
                return SAPTLSError(
                    "SAP HTTPS certificate hostname mismatch: the SAP Gateway "
                    "certificate CN does not match the configured base URL. "
                    "Set SAP_ODATA__SKIP_HOSTNAME_CHECK=true and "
                    "SAP_ODATA__CA_BUNDLE=<path/to/sap_ca.pem>."
                )
            return SAPTLSError(
                "SAP HTTPS certificate could not be validated. "
                "Configure SAP_ODATA__CA_BUNDLE with the path to the "
                "corporate CA bundle, or ensure the SAP Gateway certificate "
                "is trusted by the system CA store."
            )
        if isinstance(exc, httpx.TimeoutException):
            return SAPTimeoutError(
                "SAP OData request timed out. "
                "Increase SAP_ODATA__TIMEOUT or verify SAP Gateway availability."
            )
        return SAPConnectionError(
            "SAP Gateway is unreachable. "
            "Check SAP_ODATA__BASE_URL and network/firewall configuration."
        )

    # ------------------------------------------------------------------
    # OData string helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _escape_odata_string(value: str) -> str:
        """Escape a string for use inside an OData V2 string literal.

        OData V2 string literals are delimited by single quotes.  An
        embedded single quote must be doubled (not backslash-escaped).

        Example:
            "O'Brien" → "O''Brien"
        """
        return value.replace("'", "''")

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        """Reliably convert an OData V2 value to a Python bool.

        OData V2 over JSON may return booleans as Python bool, or as the
        strings 'true'/'false' (lowercase). The SAP Gateway may also use
        'X' / '' (ABAP checkbox convention) or 1/0. This helper normalises
        all representations so that the string 'false' does NOT become True.
        """
        if isinstance(value, bool):
            return value
        if isinstance(value, int):
            return value != 0
        if isinstance(value, str):
            lower = value.strip().lower()
            if lower in ("true", "x", "1"):
                return True
            if lower in ("false", "", "0"):
                return False
        # Unknown representation — log and default to False (safe).
        logger.warning(
            "sap_odata_unexpected_bool_value",
            extra={"operation": "coerce_bool", "value": repr(value)},
        )
        return False

    # ------------------------------------------------------------------
    # Response parsing — SystemInfoSet
    # ------------------------------------------------------------------

    def _parse_system_info(self, payload: Any) -> ConnectionTestResult:
        """Parse a SystemInfoSet OData V2 JSON response into ConnectionTestResult."""
        if not isinstance(payload, dict) or "d" not in payload:
            raise SAPResponseParseError(
                "SAP OData response is missing the expected 'd' envelope."
            )

        d = payload["d"]
        if not isinstance(d, dict) or "results" not in d:
            raise SAPResponseParseError(
                "SAP OData 'd' object is missing the 'results' list."
            )

        results = d["results"]
        if not isinstance(results, list):
            raise SAPResponseParseError("SAP OData 'results' is not a list.")
        if len(results) == 0:
            raise SAPResponseParseError(
                "SAP OData SystemInfoSet returned an empty result set."
            )

        row = results[0]
        if not isinstance(row, dict):
            raise SAPResponseParseError("SAP OData result row is not a dict.")

        for required_field in ("Status", "SystemId", "Client", "SapRelease"):
            if required_field not in row:
                raise SAPResponseParseError(
                    f"SAP OData SystemInfoSet response is missing required field '{required_field}'."
                )

        sap_status = str(row.get("Status") or "").strip().upper()
        if sap_status not in _SAP_SUCCESS_STATUSES:
            raise SAPFunctionExecutionError(
                "SAP OData SystemInfoSet returned an unsuccessful status. "
                "The ECC application layer reported an error condition."
            )

        return ConnectionTestResult(
            status="connected",
            connection_mode="odata",
            system_id=str(row.get("SystemId") or "").strip() or None,
            client=str(row.get("Client") or "").strip() or None,
            sap_release=str(row.get("SapRelease") or "").strip() or None,
            timestamp=str(row.get("Timestamp") or "").strip() or None,
            ping_method="SystemInfoSet",
            message=str(
                row.get("Message") or "N-CoreIQ OData connectivity successful."
            ).strip(),
        )

    # ------------------------------------------------------------------
    # Response parsing — PackagesSet
    # ------------------------------------------------------------------

    def _parse_packages(
        self,
        payload: Any,
        root_package: str,
        include_subpackages: bool,
        max_depth: int,
    ) -> PackageResolutionResult:
        """Parse a PackagesSet OData V2 JSON response.

        Raises:
            SAPResponseParseError: if the envelope is malformed.
            RepositoryPackageNotFoundError: if results is empty.
        """
        if not isinstance(payload, dict) or "d" not in payload:
            raise SAPResponseParseError(
                "SAP OData PackagesSet response is missing the expected 'd' envelope."
            )

        d = payload["d"]
        if not isinstance(d, dict) or "results" not in d:
            raise SAPResponseParseError(
                "SAP OData PackagesSet 'd' object is missing the 'results' list."
            )

        results = d["results"]
        if not isinstance(results, list):
            raise SAPResponseParseError(
                "SAP OData PackagesSet 'results' is not a list."
            )

        if len(results) == 0:
            raise RepositoryPackageNotFoundError(root_package)

        first_row = results[0]
        if not isinstance(first_row, dict):
            raise SAPResponseParseError(
                "SAP OData PackagesSet result row is not a dict."
            )
        system_id = str(first_row.get("SystemId") or "").strip() or None
        client = str(first_row.get("Client") or "").strip() or None
        truncated = False
        message: str | None = None

        seen: dict[str, RepositoryPackage] = {}
        packages: list[RepositoryPackage] = []
        for idx, raw_row in enumerate(results):
            if not isinstance(raw_row, dict):
                raise SAPResponseParseError(
                    f"SAP OData PackagesSet result row [{idx}] is not a dict."
                )

            if self._coerce_bool(raw_row.get("Truncated", False)):
                truncated = True
            row_msg = str(raw_row.get("Message") or "").strip()
            if row_msg and message is None:
                message = row_msg

            pkg_name = str(raw_row.get("Package") or "").strip()
            if not pkg_name:
                logger.warning(
                    "sap_odata_packages_blank_row",
                    extra={
                        "operation": "package_resolution",
                        "root_package": root_package,
                        "row_index": idx,
                    },
                )
                continue

            parent = str(raw_row.get("ParentPackage") or "").strip() or None
            description = str(raw_row.get("Description") or "").strip() or None
            try:
                depth = int(raw_row.get("Depth", 0))
            except (TypeError, ValueError):
                depth = 0

            pkg = RepositoryPackage(
                package=pkg_name,
                parent_package=parent,
                description=description,
                depth=depth,
            )

            if pkg_name in seen:
                existing = seen[pkg_name]
                if (
                    existing.parent_package != pkg.parent_package
                    or existing.depth != pkg.depth
                    or existing.description != pkg.description
                ):
                    logger.warning(
                        "sap_odata_packages_duplicate_conflict",
                        extra={
                            "operation": "package_resolution",
                            "root_package": root_package,
                            "package": pkg_name,
                            "row_index": idx,
                            "kept_depth": existing.depth,
                            "duplicate_depth": pkg.depth,
                        },
                    )
                # Keep first occurrence; skip duplicate.
                continue

            seen[pkg_name] = pkg
            packages.append(pkg)

        # Sort deterministically: depth ascending, then package name ascending.
        packages.sort(key=lambda p: (p.depth, p.package))

        # Validate root package contract: the requested root must appear at depth=0.
        root_found = any(p.package == root_package and p.depth == 0 for p in packages)
        if packages and not root_found:
            raise SAPResponseParseError(
                f"SAP OData PackagesSet returned results but the root package "
                f"'{root_package}' was not present at depth 0. "
                "The SAP response does not match the expected contract."
            )

        return PackageResolutionResult(
            system_id=system_id,
            client=client,
            root_package=root_package,
            include_subpackages=include_subpackages,
            max_depth=max_depth,
            truncated=truncated,
            message=message,
            packages=packages,
        )

    # ------------------------------------------------------------------
    # Public interface — ping (Phase 2)
    # ------------------------------------------------------------------

    async def ping(self) -> ConnectionTestResult:
        """Verify SAP ECC connectivity via the OData SystemInfoSet entity set."""
        self._require_configuration()

        odata = self.settings.sap_odata
        url = f"{self._service_root()}/SystemInfoSet"
        params = {
            "sap-client": odata.client,
            "$format": "json",
        }

        logger.info(
            "sap_odata_ping_start",
            extra={
                "operation": "odata_ping",
                "provider": "odata",
                "service": odata.service,
                "entity_set": "SystemInfoSet",
            },
        )

        try:
            async with self._build_client() as http_client:
                response = await http_client.get(url, params=params)
        except httpx.RequestError as exc:
            raise self._map_http_exception(exc) from exc

        status_code = response.status_code
        if status_code == 401:
            raise SAPAuthenticationError(
                "SAP authentication failed. "
                "Check SAP_ODATA__USER and SAP_ODATA__PASSWORD."
            )
        if status_code == 403:
            raise SAPAuthenticationError(
                "SAP user is authenticated but lacks permission to access "
                f"the {odata.service} OData service. "
                "Assign the required OData service authorisation in SAP."
            )
        if status_code == 404:
            raise SAPGatewayError(
                f"SAP OData service '{odata.service}' or entity set "
                "'SystemInfoSet' was not found (HTTP 404). "
                "Verify the service is activated in /IWFND/MAINT_SERVICE."
            )
        if status_code >= 500:
            raise SAPGatewayError(
                "SAP OData service returned an internal server error."
            )
        if status_code != 200:
            raise SAPCommunicationError(
                f"SAP OData service returned unexpected HTTP status {status_code}."
            )

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise SAPResponseParseError(
                "SAP OData response body is not valid JSON."
            ) from exc

        result = self._parse_system_info(payload)

        logger.info(
            "sap_odata_ping_succeeded",
            extra={
                "operation": "odata_ping",
                "provider": "odata",
                "connection_mode": "odata",
                "system_id": result.system_id,
                "client": result.client,
                "sap_release": result.sap_release,
                "ping_method": result.ping_method,
            },
        )
        return result

    async def call_allowed_function(
        self, function_name: str, **parameters: Any
    ) -> dict[str, Any]:
        """OData provider does not support RFC function module calls."""
        raise SAPProviderNotImplementedError(
            "ODataSAPConnectionProvider does not support RFC function calls. "
            "Use get_packages() for Phase 3 OData package resolution."
        )

    # ------------------------------------------------------------------
    # Package resolution (Phase 3)
    # ------------------------------------------------------------------

    async def get_packages(
        self,
        root_package: str,
        include_subpackages: bool = False,
        max_depth: int = 20,
    ) -> PackageResolutionResult:
        """Resolve an SAP package hierarchy via PackagesSet (OData V2)."""
        self._require_configuration()

        odata = self.settings.sap_odata

        include_str = "true" if include_subpackages else "false"
        escaped_pkg = self._escape_odata_string(root_package)
        odata_filter = (
            f"RootPackage eq '{escaped_pkg}' "
            f"and IncludeSubpackages eq {include_str} "
            f"and MaxDepth eq {max_depth}"
        )

        url = f"{self._service_root()}/PackagesSet"
        params = {
            "sap-client": odata.client,
            "$filter": odata_filter,
            "$format": "json",
        }

        start_time = time.monotonic()
        logger.info(
            "sap_odata_packages_start",
            extra={
                "operation": "package_resolution",
                "root_package": root_package,
                "include_subpackages": include_subpackages,
                "max_depth": max_depth,
                "entity_set": "PackagesSet",
            },
        )

        try:
            async with self._build_client() as http_client:
                response = await http_client.get(url, params=params)
        except httpx.RequestError as exc:
            raise self._map_http_exception(exc) from exc

        status_code = response.status_code
        if status_code == 401:
            raise SAPAuthenticationError(
                "SAP authentication failed during package resolution. "
                "Check SAP_ODATA__USER and SAP_ODATA__PASSWORD."
            )
        if status_code == 403:
            raise SAPAuthenticationError(
                "SAP user lacks permission to access PackagesSet. "
                f"Assign the required OData authorisation in SAP ({odata.service})."
            )
        if status_code == 404:
            raise SAPGatewayError(
                f"SAP OData entity set 'PackagesSet' was not found (HTTP 404). "
                f"Verify the service '{odata.service}' is activated."
            )
        if status_code >= 500:
            raise SAPGatewayError(
                "SAP OData service returned an internal server error during package resolution."
            )
        if status_code != 200:
            raise SAPCommunicationError(
                f"SAP OData PackagesSet returned unexpected HTTP status {status_code}."
            )

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise SAPResponseParseError(
                "SAP OData PackagesSet response body is not valid JSON."
            ) from exc

        result = self._parse_packages(payload, root_package, include_subpackages, max_depth)

        elapsed = time.monotonic() - start_time
        logger.info(
            "sap_odata_packages_complete",
            extra={
                "operation": "package_resolution",
                "root_package": root_package,
                "include_subpackages": include_subpackages,
                "max_depth": max_depth,
                "package_count": len(result.packages),
                "truncated": result.truncated,
                "system_id": result.system_id,
                "client": result.client,
                "duration_seconds": round(elapsed, 3),
            },
        )
        return result


    # ------------------------------------------------------------------
    # Response parsing — RepositoryObjectsSet
    # ------------------------------------------------------------------

    def _parse_repository_objects(
        self,
        payload: Any,
        root_package: str | None,
        name_pattern: str | None,
        object_type: str | None,
    ) -> ODataDiscoveryResult:
        """Parse a RepositoryObjectsSet OData V2 JSON response.

        Unlike PackagesSet, an empty result is valid (no error raised).
        Metadata echo fields (Truncated, TotalMatches, Message, SystemId,
        Client) are extracted from the first row and then rows are mapped
        to RFC-style field names for ``_normalize_object()`` compatibility.
        """
        if not isinstance(payload, dict) or "d" not in payload:
            raise SAPResponseParseError(
                "SAP OData RepositoryObjectsSet response is missing the expected 'd' envelope."
            )

        d = payload["d"]
        if not isinstance(d, dict) or "results" not in d:
            raise SAPResponseParseError(
                "SAP OData RepositoryObjectsSet 'd' object is missing the 'results' list."
            )

        results = d["results"]
        if not isinstance(results, list):
            raise SAPResponseParseError(
                "SAP OData RepositoryObjectsSet 'results' is not a list."
            )

        # Empty result is valid for discovery (unlike PackagesSet).
        if len(results) == 0:
            return ODataDiscoveryResult()

        first_row = results[0]
        if not isinstance(first_row, dict):
            raise SAPResponseParseError(
                "SAP OData RepositoryObjectsSet result row is not a dict."
            )

        # Extract metadata from first row (SAP echoes these as per-row fields).
        system_id = str(first_row.get("SystemId") or "").strip() or None
        client = str(first_row.get("Client") or "").strip() or None
        message = str(first_row.get("Message") or "").strip()

        truncated = False
        total_matches: int | None = None
        rows: list[dict[str, Any]] = []

        for idx, raw_row in enumerate(results):
            if not isinstance(raw_row, dict):
                raise SAPResponseParseError(
                    f"SAP OData RepositoryObjectsSet result row [{idx}] is not a dict."
                )

            # Truncated is an echo field; if any row is True, the set is truncated.
            if self._coerce_bool(raw_row.get("Truncated", False)):
                truncated = True

            # TotalMatches — extract once from first populated row.
            if total_matches is None:
                raw_tm = raw_row.get("TotalMatches")
                if raw_tm is not None and str(raw_tm).strip():
                    try:
                        total_matches = int(raw_tm)
                    except (TypeError, ValueError):
                        logger.warning(
                            "sap_odata_repository_invalid_total_matches",
                            extra={
                                "operation": "repository_discovery",
                                "value": repr(raw_tm),
                                "row_index": idx,
                            },
                        )

            # Update message from first non-blank row.
            row_msg = str(raw_row.get("Message") or "").strip()
            if row_msg and not message:
                message = row_msg

            # Map OData field names to RFC-style names for _normalize_object() compat.
            obj_type = str(raw_row.get("ObjectType") or "").strip()
            obj_name = str(raw_row.get("ObjectName") or "").strip()

            if not obj_type or not obj_name:
                logger.warning(
                    "sap_odata_repository_blank_object_row",
                    extra={"operation": "repository_discovery", "row_index": idx},
                )
                continue

            rows.append(
                {
                    "OBJECT": obj_type,
                    "OBJ_NAME": obj_name,
                    "PGMID": str(raw_row.get("Pgmid") or "").strip(),
                    "DEVCLASS": str(raw_row.get("Package") or "").strip(),
                    "AUTHOR": str(raw_row.get("Author") or "").strip(),
                    "DESCRIPTION": str(raw_row.get("Description") or "").strip(),
                    "MATCH_REASON": str(raw_row.get("MatchReason") or "").strip(),
                    "WARNING": str(raw_row.get("Warning") or "").strip(),
                    "SYSTEM_ID": str(raw_row.get("SystemId") or "").strip(),
                    "CLIENT": str(raw_row.get("Client") or "").strip(),
                }
            )

        return ODataDiscoveryResult(
            rows=rows,
            truncated=truncated,
            total_matches=total_matches,
            system_id=system_id,
            client=client,
            message=message,
        )

    # ------------------------------------------------------------------
    # Repository object discovery (Phase 3)
    # ------------------------------------------------------------------

    async def discover_repository_objects(
        self,
        root_package: str | None,
        include_subpackages: bool,
        name_pattern: str | None,
        object_type: str | None,
        max_results: int,
    ) -> ODataDiscoveryResult:
        """Discover SAP repository objects via RepositoryObjectsSet (OData V2).

        Constructs a controlled OData $filter from validated parameters and
        calls the RepositoryObjectsSet entity set.  Exactly one OData request
        is issued per call; multi-value fan-out is handled by the service layer.

        Args:
            root_package:        SAP package to search (or None for pattern-only).
            include_subpackages: Include child packages recursively.
            name_pattern:        Object name pattern e.g. ``Z*`` (or None).
            object_type:         SAP object type e.g. ``PROG`` (or None = all types).
            max_results:         Maximum rows to return from this OData call.

        Returns:
            ODataDiscoveryResult with RFC-style rows and metadata.
        """
        self._require_configuration()

        odata = self.settings.sap_odata

        # Build OData filter — only include clauses for non-None values.
        filter_parts: list[str] = []

        if root_package:
            filter_parts.append(
                f"RootPackage eq '{self._escape_odata_string(root_package)}'"
            )

        include_str = "true" if include_subpackages else "false"
        filter_parts.append(f"IncludeSubpackages eq {include_str}")

        if name_pattern:
            filter_parts.append(
                f"NamePattern eq '{self._escape_odata_string(name_pattern)}'"
            )

        if object_type:
            filter_parts.append(
                f"RequestedObjectType eq '{self._escape_odata_string(object_type)}'"
            )

        filter_parts.append(f"MaxResults eq {max_results}")

        odata_filter = " and ".join(filter_parts)

        url = f"{self._service_root()}/RepositoryObjectsSet"
        params = {
            "sap-client": odata.client,
            "$filter": odata_filter,
            "$format": "json",
        }

        start_time = time.monotonic()
        logger.info(
            "sap_odata_discovery_start",
            extra={
                "operation": "repository_discovery",
                "root_package": root_package or "",
                "name_pattern": name_pattern or "",
                "object_type": object_type or "",
                "include_subpackages": include_subpackages,
                "max_results": max_results,
                "entity_set": "RepositoryObjectsSet",
            },
        )

        try:
            async with self._build_client() as http_client:
                response = await http_client.get(url, params=params)
        except httpx.RequestError as exc:
            raise self._map_http_exception(exc) from exc

        status_code = response.status_code
        if status_code == 401:
            raise SAPAuthenticationError(
                "SAP authentication failed during repository discovery. "
                "Check SAP_ODATA__USER and SAP_ODATA__PASSWORD."
            )
        if status_code == 403:
            raise SAPAuthenticationError(
                "SAP user lacks permission to access RepositoryObjectsSet. "
                f"Assign the required OData authorisation in SAP ({odata.service})."
            )
        if status_code == 404:
            raise SAPGatewayError(
                "SAP OData entity set 'RepositoryObjectsSet' was not found (HTTP 404). "
                f"Verify the service '{odata.service}' is activated in /IWFND/MAINT_SERVICE."
            )
        if status_code >= 500:
            raise SAPGatewayError(
                "SAP OData service returned an internal server error during repository discovery."
            )
        if status_code != 200:
            raise SAPCommunicationError(
                f"SAP OData RepositoryObjectsSet returned unexpected HTTP status {status_code}."
            )

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise SAPResponseParseError(
                "SAP OData RepositoryObjectsSet response body is not valid JSON."
            ) from exc

        result = self._parse_repository_objects(
            payload, root_package, name_pattern, object_type
        )

        elapsed = time.monotonic() - start_time
        logger.info(
            "sap_odata_discovery_complete",
            extra={
                "operation": "repository_discovery",
                "root_package": root_package or "",
                "name_pattern": name_pattern or "",
                "object_type": object_type or "",
                "rows_returned": len(result.rows),
                "truncated": result.truncated,
                "total_matches": result.total_matches,
                "system_id": result.system_id,
                "client": result.client,
                "duration_seconds": round(elapsed, 3),
            },
        )
        return result


    # ------------------------------------------------------------------
    # Response parsing — ObjectMetadataSet (Phase 4)
    # ------------------------------------------------------------------

    def _parse_object_metadata(
        self,
        payload: Any,
        object_type: str,
        object_name: str,
    ) -> RepositoryObjectMetadata:
        """Parse an ObjectMetadataSet OData V2 JSON response.

        Raises:
            SAPResponseParseError: if the OData envelope is malformed.
            RepositoryObjectNotFoundError: if Found=false or no results.
        """
        if not isinstance(payload, dict) or "d" not in payload:
            raise SAPResponseParseError(
                "SAP OData ObjectMetadataSet response is missing the expected 'd' envelope."
            )
        d = payload["d"]
        if not isinstance(d, dict) or "results" not in d:
            raise SAPResponseParseError(
                "SAP OData ObjectMetadataSet 'd' object is missing the 'results' list."
            )
        results = d["results"]
        if not isinstance(results, list):
            raise SAPResponseParseError(
                "SAP OData ObjectMetadataSet 'results' is not a list."
            )
        if len(results) == 0:
            raise RepositoryObjectNotFoundError(object_type, object_name)

        row = results[0]
        if not isinstance(row, dict):
            raise SAPResponseParseError(
                "SAP OData ObjectMetadataSet result row is not a dict."
            )

        # Check Found flag before trusting any other field.
        found = self._coerce_bool(row.get("Found", True))
        if not found:
            raise RepositoryObjectNotFoundError(object_type, object_name)

        def _str(key: str) -> str | None:
            v = str(row.get(key) or "").strip()
            return v if v else None

        return RepositoryObjectMetadata(
            sap_object_type=str(row.get("ObjectType") or object_type).strip(),
            object_name=str(row.get("ObjectName") or object_name).strip(),
            system_id=_str("SystemId"),
            client=_str("Client"),
            pgmid=_str("Pgmid"),
            package=_str("Package"),
            object_subtype=_str("ObjectSubtype"),
            program_name=_str("ProgramName"),
            function_group=_str("FunctionGroup"),
            table_class=_str("TableClass"),
            author=_str("Author"),
            master_language=_str("MasterLanguage"),
            source_system=_str("SourceSystem"),
            created_date=_str("CreatedDate"),
            changed_by=_str("ChangedBy"),
            changed_date=_str("ChangedDate"),
            description=_str("Description"),
            active_status=_str("ActiveStatus"),
            found=True,
            warning=_str("Warning"),
            message=_str("Message"),
        )

    # ------------------------------------------------------------------
    # Response parsing — SourceLinesSet (Phase 4)
    # ------------------------------------------------------------------

    def _parse_object_source(
        self,
        payload: Any,
        object_type: str,
        object_name: str,
        max_lines: int,
    ) -> RepositoryObjectSource:
        """Parse a SourceLinesSet OData V2 JSON response into RepositoryObjectSource.

        Raises:
            SAPResponseParseError: if the OData envelope is malformed.
            RepositoryObjectNotFoundError: if Found=false.
            RepositorySourceDataIntegrityError: if conflicting line numbers are detected.
        """
        if not isinstance(payload, dict) or "d" not in payload:
            raise SAPResponseParseError(
                "SAP OData SourceLinesSet response is missing the expected 'd' envelope."
            )
        d = payload["d"]
        if not isinstance(d, dict) or "results" not in d:
            raise SAPResponseParseError(
                "SAP OData SourceLinesSet 'd' object is missing the 'results' list."
            )
        results = d["results"]
        if not isinstance(results, list):
            raise SAPResponseParseError(
                "SAP OData SourceLinesSet 'results' is not a list."
            )

        system_id: str | None = None
        client: str | None = None
        message: str | None = None
        truncated = False
        found = True

        # Check Found on first row if present.
        if len(results) > 0:
            first = results[0]
            if isinstance(first, dict):
                found = self._coerce_bool(first.get("Found", True))
                system_id = str(first.get("SystemId") or "").strip() or None
                client = str(first.get("Client") or "").strip() or None
                message = str(first.get("Message") or "").strip() or None

        if not found and len(results) <= 1:
            raise RepositoryObjectNotFoundError(object_type, object_name)

        # Group rows by (component_type, component_name) preserving insertion order.
        # Within each component track: {line_no: source_line} for integrity checking.
        # component_order preserves first-seen order for determinism.
        ComponentKey = tuple  # (component_type, component_name)
        component_order: list[ComponentKey] = []
        component_lines: dict[ComponentKey, dict[int, str]] = {}

        for idx, raw_row in enumerate(results):
            if not isinstance(raw_row, dict):
                raise SAPResponseParseError(
                    f"SAP OData SourceLinesSet result row [{idx}] is not a dict."
                )

            if self._coerce_bool(raw_row.get("Truncated", False)):
                truncated = True

            row_msg = str(raw_row.get("Message") or "").strip()
            if row_msg and not message:
                message = row_msg

            comp_type = str(raw_row.get("ComponentType") or "").strip()
            comp_name = str(raw_row.get("ComponentName") or "").strip()

            # Skip rows with no component identity (echo/metadata-only rows).
            if not comp_type and not comp_name:
                # Still update system context from these rows.
                if not system_id:
                    system_id = str(raw_row.get("SystemId") or "").strip() or None
                if not client:
                    client = str(raw_row.get("Client") or "").strip() or None
                continue

            # Parse line number.
            raw_line_no = raw_row.get("LineNo")
            try:
                line_no = int(raw_line_no)
            except (TypeError, ValueError):
                logger.warning(
                    "sap_odata_source_invalid_line_no",
                    extra={
                        "operation": "source_extraction",
                        "object_type": object_type,
                        "object_name": object_name,
                        "row_index": idx,
                        "raw_line_no": repr(raw_line_no),
                    },
                )
                continue

            source_line_text = str(raw_row.get("SourceLine") or "")

            key: ComponentKey = (comp_type, comp_name)
            if key not in component_lines:
                component_order.append(key)
                component_lines[key] = {}

            existing = component_lines[key]
            if line_no in existing:
                if existing[line_no] != source_line_text:
                    raise RepositorySourceDataIntegrityError(
                        comp_type, comp_name, line_no
                    )
                # Identical duplicate — safe to skip.
                continue

            existing[line_no] = source_line_text

        # Build canonical component models in insertion order.
        components: list[RepositorySourceComponent] = []
        total_lines = 0

        for key in component_order:
            comp_type, comp_name = key
            line_map = component_lines[key]
            sorted_line_nos = sorted(line_map.keys())
            source_lines = [
                RepositorySourceLine(
                    component_type=comp_type,
                    component_name=comp_name,
                    line_no=ln,
                    source_line=line_map[ln],
                )
                for ln in sorted_line_nos
            ]
            source_text = "\n".join(sl.source_line for sl in source_lines)
            total_lines += len(source_lines)
            components.append(
                RepositorySourceComponent(
                    component_type=comp_type,
                    component_name=comp_name,
                    line_count=len(source_lines),
                    lines=source_lines,
                    source_text=source_text,
                )
            )

        return RepositoryObjectSource(
            sap_object_type=object_type,
            object_name=object_name,
            system_id=system_id,
            client=client,
            max_lines=max_lines,
            returned_lines=total_lines,
            component_count=len(components),
            found=found,
            truncated=truncated,
            message=message,
            components=components,
        )

    # ------------------------------------------------------------------
    # Object metadata retrieval (Phase 4)
    # ------------------------------------------------------------------

    async def get_object_metadata(
        self,
        object_type: str,
        object_name: str,
    ) -> RepositoryObjectMetadata:
        """Retrieve technical metadata via ObjectMetadataSet (OData V2)."""
        self._require_configuration()
        odata = self.settings.sap_odata

        escaped_type = self._escape_odata_string(object_type)
        escaped_name = self._escape_odata_string(object_name)
        odata_filter = (
            f"ObjectType eq '{escaped_type}' and ObjectName eq '{escaped_name}'"
        )

        url = f"{self._service_root()}/ObjectMetadataSet"
        params = {
            "sap-client": odata.client,
            "$filter": odata_filter,
            "$format": "json",
        }

        start_time = time.monotonic()
        logger.info(
            "sap_odata_metadata_start",
            extra={
                "operation": "object_metadata",
                "object_type": object_type,
                "object_name": object_name,
                "entity_set": "ObjectMetadataSet",
            },
        )

        try:
            async with self._build_client() as http_client:
                response = await http_client.get(url, params=params)
        except httpx.RequestError as exc:
            raise self._map_http_exception(exc) from exc

        status_code = response.status_code
        if status_code == 401:
            raise SAPAuthenticationError(
                "SAP authentication failed during metadata retrieval. "
                "Check SAP_ODATA__USER and SAP_ODATA__PASSWORD."
            )
        if status_code == 403:
            raise SAPAuthenticationError(
                "SAP user lacks permission to access ObjectMetadataSet. "
                f"Assign the required OData authorisation in SAP ({odata.service})."
            )
        if status_code == 404:
            raise SAPGatewayError(
                "SAP OData entity set 'ObjectMetadataSet' was not found (HTTP 404). "
                f"Verify the service '{odata.service}' is activated in /IWFND/MAINT_SERVICE."
            )
        if status_code >= 500:
            raise SAPGatewayError(
                "SAP OData service returned an internal server error during metadata retrieval."
            )
        if status_code != 200:
            raise SAPCommunicationError(
                f"SAP OData ObjectMetadataSet returned unexpected HTTP status {status_code}."
            )

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise SAPResponseParseError(
                "SAP OData ObjectMetadataSet response body is not valid JSON."
            ) from exc

        result = self._parse_object_metadata(payload, object_type, object_name)

        elapsed = time.monotonic() - start_time
        logger.info(
            "sap_odata_metadata_complete",
            extra={
                "operation": "object_metadata",
                "object_type": object_type,
                "object_name": object_name,
                "found": result.found,
                "system_id": result.system_id,
                "client": result.client,
                "duration_seconds": round(elapsed, 3),
            },
        )
        return result

    # ------------------------------------------------------------------
    # Source extraction (Phase 4)
    # ------------------------------------------------------------------

    async def get_object_source(
        self,
        object_type: str,
        object_name: str,
        max_lines: int = 5000,
    ) -> RepositoryObjectSource:
        """Retrieve source/definition via SourceLinesSet (OData V2)."""
        self._require_configuration()
        odata = self.settings.sap_odata

        escaped_type = self._escape_odata_string(object_type)
        escaped_name = self._escape_odata_string(object_name)
        odata_filter = (
            f"ObjectType eq '{escaped_type}' "
            f"and ObjectName eq '{escaped_name}' "
            f"and MaxLines eq {max_lines}"
        )

        url = f"{self._service_root()}/SourceLinesSet"
        params = {
            "sap-client": odata.client,
            "$filter": odata_filter,
            "$format": "json",
        }

        start_time = time.monotonic()
        logger.info(
            "sap_odata_source_start",
            extra={
                "operation": "source_extraction",
                "object_type": object_type,
                "object_name": object_name,
                "max_lines": max_lines,
                "entity_set": "SourceLinesSet",
            },
        )

        try:
            async with self._build_client() as http_client:
                response = await http_client.get(url, params=params)
        except httpx.RequestError as exc:
            raise self._map_http_exception(exc) from exc

        status_code = response.status_code
        if status_code == 401:
            raise SAPAuthenticationError(
                "SAP authentication failed during source extraction. "
                "Check SAP_ODATA__USER and SAP_ODATA__PASSWORD."
            )
        if status_code == 403:
            raise SAPAuthenticationError(
                "SAP user lacks permission to access SourceLinesSet. "
                f"Assign the required OData authorisation in SAP ({odata.service})."
            )
        if status_code == 404:
            raise SAPGatewayError(
                "SAP OData entity set 'SourceLinesSet' was not found (HTTP 404). "
                f"Verify the service '{odata.service}' is activated in /IWFND/MAINT_SERVICE."
            )
        if status_code >= 500:
            raise SAPGatewayError(
                "SAP OData service returned an internal server error during source extraction."
            )
        if status_code != 200:
            raise SAPCommunicationError(
                f"SAP OData SourceLinesSet returned unexpected HTTP status {status_code}."
            )

        try:
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            raise SAPResponseParseError(
                "SAP OData SourceLinesSet response body is not valid JSON."
            ) from exc

        result = self._parse_object_source(payload, object_type, object_name, max_lines)

        elapsed = time.monotonic() - start_time
        logger.info(
            "sap_odata_source_complete",
            extra={
                "operation": "source_extraction",
                "object_type": object_type,
                "object_name": object_name,
                "returned_lines": result.returned_lines,
                "component_count": result.component_count,
                "truncated": result.truncated,
                "found": result.found,
                "system_id": result.system_id,
                "client": result.client,
                "duration_seconds": round(elapsed, 3),
            },
        )
        return result


# ---------------------------------------------------------------------------
# Module-level aliases — allow unit tests to import helpers directly.
# Static methods are callable on the class without an instance.
# ---------------------------------------------------------------------------

_coerce_bool = ODataSAPConnectionProvider._coerce_bool
_odata_escape = ODataSAPConnectionProvider._escape_odata_string
