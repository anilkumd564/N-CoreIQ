"""Explicit RFC allow-list for Phase 2.

Only function modules listed here may be invoked through the SAP connection
provider. Any other function name must be rejected before it reaches SAP.
"""

from app.sap.exceptions import SAPRFCNotAllowedError

ALLOWED_RFC_FUNCTIONS: frozenset[str] = frozenset(
    {
        "Z_MCP_REPO_PING",
        "RFC_SYSTEM_INFO",
    }
)


def ensure_allowed(function_name: str) -> None:
    """Raise SAPRFCNotAllowedError if function_name is not on the allow-list."""
    if function_name not in ALLOWED_RFC_FUNCTIONS:
        raise SAPRFCNotAllowedError(function_name)
