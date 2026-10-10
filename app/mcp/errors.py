"""Phase 7 — MCP error normalisation.

Maps N-CoreIQ domain exceptions to safe, sanitised MCP-visible error messages.

Security rules (identical to REST layer):
- Never expose SAP innererror, ABAP stacks, or Gateway internals.
- Never expose credentials, passwords, or Authorization headers.
- Never expose internal filesystem paths or Python tracebacks.
- Reuse existing exception hierarchy from app.sap.exceptions.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class MCPToolError(Exception):
    """Safe MCP-visible error raised by tool handlers.

    The MCP SDK (FastMCP) propagates this as a tool error response.
    The ``code`` is a machine-readable identifier for the client.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"[{code}] {message}")


def _sanitise(text: str) -> str:
    """Remove obviously sensitive fragments from error messages."""
    lowered = text.lower()
    sensitive = ("password", "authorization", "bearer ", "api_key", "apikey", "secret")
    for kw in sensitive:
        if kw in lowered:
            return "An error occurred. Details have been redacted for security."
    return text


def map_exception(exc: Exception, operation: str = "operation") -> MCPToolError:
    """Map any domain/SAP exception to a sanitised MCPToolError.

    This is the single point through which all exceptions flow to MCP clients.
    """
    # Import lazily to avoid circular imports
    from app.sap.exceptions import (
        SAPAuthenticationError,
        SAPCommunicationError,
        SAPConfigurationError,
        SAPConnectionError,
        SAPFunctionExecutionError,
        RepositoryObjectNotFoundError,
        RepositoryObjectTypeNotSupportedError,
        RepositoryPackageNotFoundError,
    )

    exc_type = type(exc).__name__
    logger.warning(
        "event=mcp_tool_error operation=%s exc_type=%s msg=%.200s",
        operation,
        exc_type,
        str(exc),
    )

    # Import Phase 8 exceptions lazily
    from app.sap.exceptions import (
        DependencyDataIntegrityError,
        DependencyObjectNotFoundError,
        DependencyProviderError,
        DependencyScopeTooLargeError,
        DependencyTraversalLimitError,
    )

    if isinstance(exc, MCPToolError):
        return exc

    if isinstance(exc, DependencyObjectNotFoundError):
        return MCPToolError(
            "DEPENDENCY_OBJECT_NOT_FOUND",
            f"Dependency source object not found: {exc.object_type}/{exc.object_name}",
        )

    if isinstance(exc, DependencyScopeTooLargeError):
        return MCPToolError(
            "DEPENDENCY_SCOPE_TOO_LARGE",
            f"Dependency scope exceeds limits: {str(exc)[:200]}",
        )

    if isinstance(exc, DependencyTraversalLimitError):
        return MCPToolError(
            "DEPENDENCY_TRAVERSAL_LIMIT",
            f"Dependency traversal limit exceeded: {str(exc)[:200]}",
        )

    if isinstance(exc, DependencyDataIntegrityError):
        return MCPToolError(
            "DEPENDENCY_DATA_ERROR",
            "SAP dependency data integrity error. Check SAP Gateway logs.",
        )

    if isinstance(exc, DependencyProviderError):
        return MCPToolError(
            "SAP_UNAVAILABLE",
            "SAP dependency service error. The operation may be retried.",
        )

    if isinstance(exc, RepositoryObjectNotFoundError):
        return MCPToolError(
            "OBJECT_NOT_FOUND",
            f"SAP repository object not found: {exc.object_type}/{exc.object_name}",
        )

    if isinstance(exc, RepositoryPackageNotFoundError):
        return MCPToolError(
            "PACKAGE_NOT_FOUND",
            f"SAP package not found: {exc.package}",
        )

    if isinstance(exc, RepositoryObjectTypeNotSupportedError):
        return MCPToolError(
            "INVALID_ARGUMENT",
            f"Unsupported SAP object type: {exc.object_type}",
        )

    if isinstance(exc, SAPAuthenticationError):
        return MCPToolError(
            "SAP_AUTHENTICATION_FAILED",
            "SAP authentication failed. Verify credentials in N-CoreIQ configuration.",
        )

    if isinstance(exc, SAPConfigurationError):
        return MCPToolError(
            "SAP_CONFIGURATION_ERROR",
            "N-CoreIQ SAP configuration is incomplete or invalid.",
        )

    if isinstance(exc, SAPConnectionError):
        return MCPToolError(
            "SAP_UNAVAILABLE",
            "SAP system is currently unreachable. Check network connectivity.",
        )

    if isinstance(exc, SAPCommunicationError):
        return MCPToolError(
            "SAP_UNAVAILABLE",
            "SAP communication error. The operation may be retried.",
        )

    if isinstance(exc, SAPFunctionExecutionError):
        return MCPToolError(
            "SAP_UNAVAILABLE",
            "SAP function execution failed. The system may be temporarily unavailable.",
        )

    if isinstance(exc, ValueError):
        safe_msg = _sanitise(str(exc))
        return MCPToolError("INVALID_ARGUMENT", safe_msg)

    if isinstance(exc, KeyError):
        return MCPToolError(
            "JOB_NOT_FOUND",
            "The requested job was not found.",
        )

    # Generic fallback — no internal details exposed
    return MCPToolError(
        "INTERNAL_ERROR",
        "An internal error occurred. Please check N-CoreIQ server logs.",
    )
