"""Explicit RFC allow-list for Phase 2.

Only function modules listed here may be invoked through the SAP connection
provider. Any other function name must be rejected before it reaches SAP.
"""

from app.sap.exceptions import SAPRFCNotAllowedError

# Standard SAP read-only RFCs permitted for infrastructure use.
# RFC_SYSTEM_INFO: standard read-only SAP FM to retrieve system info (SID, release).
# Used by the local/RFC connection mode for the ping operation.
# This function does NOT modify any SAP data.
_STANDARD_SAP_READONLY_FUNCTIONS: frozenset[str] = frozenset({"RFC_SYSTEM_INFO"})

# N-CoreIQ custom function modules (Z_MCP_REPO_* namespace).
# All custom FMs are read-only repository operations; none write SAP data.
_NCOREIQ_CUSTOM_FUNCTIONS: frozenset[str] = frozenset(
    {
        "Z_MCP_REPO_PING",
        # Phase 3 — SAP Repository Discovery
        "Z_MCP_REPO_PACKAGES",
        "Z_MCP_REPO_DISCOVER",
        # Phase 4 — Object Metadata & Source (OData layer; RFC names kept for completeness)
        "Z_MCP_REPO_GET_METADATA",
        "Z_MCP_REPO_GET_SOURCE",
        # Phase 8 — Dependency Intelligence
        "Z_MCP_REPO_DEPENDENCIES",
    }
)

ALLOWED_RFC_FUNCTIONS: frozenset[str] = _STANDARD_SAP_READONLY_FUNCTIONS | _NCOREIQ_CUSTOM_FUNCTIONS

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
