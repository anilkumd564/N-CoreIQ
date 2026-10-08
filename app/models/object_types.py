"""Phase 3 SAP repository object-type definitions.

Contains the Phase 3 approved native SAP object types and the canonical
native-to-friendly display name mapping.  This module is the single source
of truth for object-type validation and presentation throughout N-CoreIQ.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Phase 3 approved native SAP repository object types (TADIR OBJECT column).
# ---------------------------------------------------------------------------
PHASE3_OBJECT_TYPES: frozenset[str] = frozenset(
    {
        "PROG",  # ABAP Program
        "CLAS",  # ABAP Class
        "INTF",  # ABAP Interface
        "FUGR",  # Function Group
        "TABL",  # Table / Structure
        "DTEL",  # Data Element
        "DOMA",  # Domain
        "VIEW",  # Database View
        "SHLP",  # Search Help
        "ENQU",  # Lock Object
        "TRAN",  # Transaction
        "MSAG",  # Message Class
    }
)

# ---------------------------------------------------------------------------
# Mapping: SAP native type  →  friendly display name used in API responses.
# Both names are preserved in every RepositoryObject; neither replaces the
# other.
# ---------------------------------------------------------------------------
FRIENDLY_TYPE_MAP: dict[str, str] = {
    "PROG": "PROGRAM",
    "CLAS": "CLASS",
    "INTF": "INTERFACE",
    "FUGR": "FUNCTION_GROUP",
    "TABL": "TABLE_OR_STRUCTURE",
    "DTEL": "DATA_ELEMENT",
    "DOMA": "DOMAIN",
    "VIEW": "VIEW",
    "SHLP": "SEARCH_HELP",
    "ENQU": "LOCK_OBJECT",
    "TRAN": "TRANSACTION",
    "MSAG": "MESSAGE_CLASS",
}


def get_friendly_type(sap_object_type: str) -> str:
    """Return the friendly display name for a SAP native object type.

    Falls back to ``"UNKNOWN"`` when the type is outside the Phase 3 known
    set (e.g. a future or unknown ECC type received from ECC).  The raw SAP
    native type is always preserved in ``sap_object_type``; this function
    only governs the presentation name.
    """
    return FRIENDLY_TYPE_MAP.get(sap_object_type, "UNKNOWN")
