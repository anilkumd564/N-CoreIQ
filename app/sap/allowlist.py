"""Explicit RFC allow-list for Phase 2.

Only function modules listed here may be invoked through the SAP connection
provider. Any other function name must be rejected before it reaches SAP.
"""

from app.sap.exceptions import SAPRFCNotAllowedError

ALLOWED_RFC_FUNCTIONS: frozenset[str] = frozenset(
    {
        "Z_MCP_REPO_PING",
        "RFC_SYSTEM_INFO",
        # Phase 3 — SAP Repository Discovery
        "Z_MCP_REPO_PACKAGES",
        "Z_MCP_REPO_DISCOVER",
    }
)

# Reserved for future phases. These function modules are intentionally NOT
# on the allow-list yet. Do not add them until the corresponding phase is
# implemented and reviewed.
#   Z_MCP_REPO_GET_SOURCE     (Phase 4 — source extraction)
#   Z_MCP_REPO_GET_METADATA   (Phase 4 — metadata extraction)
#   Z_MCP_REPO_DEPENDENCIES   (Phase 4 — dependency analysis)


def ensure_allowed(function_name: str) -> None:
    """Raise SAPRFCNotAllowedError if function_name is not on the allow-list."""
    if function_name not in ALLOWED_RFC_FUNCTIONS:
        raise SAPRFCNotAllowedError(function_name)
