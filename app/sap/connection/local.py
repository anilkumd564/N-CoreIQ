"""Local SAP RFC connection provider.

Uses PyRFC + the SAP NetWeaver RFC SDK to connect directly to SAP ECC
Development for Phase 2. Connections are opened lazily (only when SAP is
actually required) and always closed via a context manager / try-finally
pattern so RFC handles are never leaked.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any, Iterator

from app.core.config import Settings
from app.models.results import ConnectionTestResult
from app.sap.connection.base import SapConnectionProvider
from app.sap.exceptions import (
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPConfigurationError,
    SAPConnectionError,
    SAPError,
    SAPFunctionExecutionError,
    SAPRFCRuntimeUnavailableError,
)
from app.sap.rfc_runtime import get_pyrfc_module, get_rfc_runtime_status

logger = logging.getLogger("app.sap.connection.local")

# Substrings in a PyRFC CommunicationError/LogonError message that indicate
# an authentication problem rather than a pure network/communication issue.
_AUTH_ERROR_MARKERS = ("logon", "password", "user", "authoriz")


class LocalSapConnectionProvider(SapConnectionProvider):
    """Local RFC provider for direct SAP ECC Development connectivity."""

    def __init__(self, settings: Settings):
        self.settings = settings

    # -- configuration ---------------------------------------------------

    def _require_configuration(self) -> None:
        local = self.settings.sap_local
        missing = [
            name
            for name, value in (
                ("host", local.host),
                ("system_number", local.system_number),
                ("client", local.client),
                ("user", local.user),
                ("password", local.password),
            )
            if not value
        ]
        if missing:
            raise SAPConfigurationError(
                "Local SAP RFC configuration is incomplete. Missing: "
                + ", ".join(missing)
            )

    def _require_runtime(self):
        status = get_rfc_runtime_status()
        if not status.available:
            raise SAPRFCRuntimeUnavailableError(status.detail)
        return get_pyrfc_module()

    def _connection_params(self) -> dict[str, str]:
        local = self.settings.sap_local
        return {
            "ashost": local.host or "",
            "sysnr": local.system_number or "",
            "client": local.client or "",
            "user": local.user or "",
            "passwd": local.password or "",
            "lang": local.language or "EN",
        }

    # -- connection lifecycle ---------------------------------------------

    @contextmanager
    def _connection(self) -> Iterator[Any]:
        """Open an RFC connection only for the duration of the call.

        Never connects during application startup; only invoked when a
        caller actually requests ping()/call_allowed_function().

        Only exceptions raised while *establishing* the connection are
        mapped to authentication/connection/communication errors here.
        Exceptions raised by the caller's use of the connection (e.g. an
        RFC function call failing) are intentionally left unmapped so they
        propagate to the caller, which maps them to the appropriate
        function-execution error category instead of being misclassified
        as a connectivity failure.
        """
        self._require_configuration()
        pyrfc = self._require_runtime()

        params = self._connection_params()
        try:
            conn = pyrfc.Connection(**params)
        except SAPError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise self._map_connection_exception(exc) from exc

        try:
            yield conn
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 - best-effort cleanup
                logger.warning(
                    "sap_rfc_connection_close_failed",
                    extra={"operation": "rfc_connection_close"},
                )

    def _map_connection_exception(self, exc: Exception) -> Exception:
        message = str(exc).lower()
        if any(marker in message for marker in _AUTH_ERROR_MARKERS):
            return SAPAuthenticationError(
                "SAP authentication failed. Check SAP_LOCAL user/client/password."
            )
        exc_name = exc.__class__.__name__.lower()
        if "communication" in exc_name or "communication" in message:
            return SAPConnectionError("SAP ECC is unreachable from this host.")
        return SAPCommunicationError("SAP RFC communication failure.")

    # -- public operations ------------------------------------------------

    async def ping(self) -> ConnectionTestResult:
        """Primary Phase 2 connectivity check using Z_MCP_REPO_PING."""
        try:
            result = await self.call_allowed_function("Z_MCP_REPO_PING")
        except SAPError:
            raise

        # Z_MCP_REPO_PING is expected to signal success via EV_STATUS; if the
        # function module itself raised no exception, treat the call as a
        # successful connectivity test and surface SAP's own status text.
        return ConnectionTestResult(
            status="connected",
            connection_mode="local",
            system_id=str(result.get("EV_SYSID") or "") or None,
            client=str(result.get("EV_CLIENT") or "") or None,
            sap_release=str(result.get("EV_SAP_RELEASE") or "") or None,
            timestamp=str(result.get("EV_TIMESTAMP") or "") or None,
            ping_method="Z_MCP_REPO_PING",
            message=str(result.get("EV_MESSAGE") or "N-CoreIQ RFC connectivity successful."),
        )

    async def call_allowed_function(self, function_name: str, **parameters: Any) -> dict[str, Any]:
        """Execute an RFC function module and return its raw result dict.

        The allow-list is enforced by the base class's call() wrapper; this
        method assumes function_name has already been approved, but we still
        guard defensively since it can be invoked directly by ping().
        """
        from app.sap.allowlist import ensure_allowed

        ensure_allowed(function_name)

        try:
            with self._connection() as conn:
                raw_result = conn.call(function_name, **parameters)
        except SAPError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise SAPFunctionExecutionError(
                f"Execution of '{function_name}' failed."
            ) from exc

        if not isinstance(raw_result, dict):
            raise SAPFunctionExecutionError(
                f"Unexpected response shape from '{function_name}'."
            )

        logger.info(
            "sap_rfc_call_succeeded",
            extra={
                "operation": "rfc_call",
                "provider": "local",
                "connection_mode": "local",
                "function": function_name,
            },
        )
        return dict(raw_result)
