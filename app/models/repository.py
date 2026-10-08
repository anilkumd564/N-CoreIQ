"""Phase 3 SAP repository object models.

Defines the canonical RepositoryObject returned by the discovery service,
the MatchReason enum, the PackageInfo model used by package resolution, and
the supporting enumerations carried forward from Phase 1.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class RepositoryCategory(StrEnum):
    """High-level grouping used for classification in future phases."""

    PROGRAM = "PROGRAM"
    CLASS = "CLASS"
    INTERFACE = "INTERFACE"
    FUNCTION_GROUP = "FUNCTION_GROUP"
    DATA_DICTIONARY = "DATA_DICTIONARY"
    TRANSACTION = "TRANSACTION"
    OTHER = "OTHER"


class MatchReason(StrEnum):
    """Indicates which discovery criterion caused an object to be returned.

    ``PACKAGE``      – the object's package matched a requested package.
    ``NAME_PATTERN`` – the object name matched a requested wildcard pattern.
    ``BOTH``         – both package and name-pattern criteria matched.
    """

    PACKAGE = "PACKAGE"
    NAME_PATTERN = "NAME_PATTERN"
    BOTH = "BOTH"


# ---------------------------------------------------------------------------
# Supporting models kept for Phase 4+ compatibility
# ---------------------------------------------------------------------------


class RepositoryMetadata(BaseModel):
    """Placeholder for Phase 4 metadata enrichment."""

    description: str | None = None
    created_date: date | None = None
    changed_date: date | None = None
    changed_by: str | None = None
    author: str | None = None
    active_status: str | None = None
    extra: dict[str, Any] = Field(default_factory=dict)


class RepositorySource(BaseModel):
    """Placeholder for Phase 4 source extraction."""

    language: str | None = None
    lines: list[str] = Field(default_factory=list)


class RepositoryRelationships(BaseModel):
    """Placeholder for Phase 8 dependency analysis."""

    uses: list[str] = Field(default_factory=list)
    used_by: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Primary repository object model (Phase 3)
# ---------------------------------------------------------------------------


class RepositoryObject(BaseModel):
    """A single SAP repository object returned by discovery.

    Phase 3 populates the core identification and classification fields.
    ``source_components`` and ``relationships`` remain empty until Phase 4
    and Phase 8 respectively.  The ``metadata`` dict may carry optional ECC
    fields such as description or dates when the RFC returns them.
    """

    # ---- System context ------------------------------------------------
    system_id: str | None = Field(
        default=None,
        description="SAP system ID (SID) where the object resides.",
    )
    client: str | None = Field(
        default=None,
        description="SAP client where the object was discovered.",
    )

    # ---- TADIR identification ------------------------------------------
    pgmid: str | None = Field(
        default=None,
        description="SAP program ID (e.g. 'R3TR').  Comes from TADIR.PGMID.",
    )
    sap_object_type: str = Field(
        description="Native SAP object type from TADIR (e.g. 'PROG', 'CLAS').",
    )
    friendly_type: str = Field(
        description="Human-readable object type name (e.g. 'PROGRAM', 'CLASS').",
    )
    object_name: str = Field(
        description="SAP object name (e.g. 'ZFI_PAYMENT_REPORT').",
    )
    package: str | None = Field(
        default=None,
        description="Development package the object belongs to (TADIR.DEVCLASS).",
    )

    # ---- Ownership / authorship ----------------------------------------
    author: str | None = Field(
        default=None,
        description="SAP user who created the object (TADIR.AUTHOR).",
    )
    description: str | None = Field(
        default=None,
        description="Short description of the object, when available from ECC.",
    )

    # ---- Dates ---------------------------------------------------------
    created_date: date | None = Field(
        default=None,
        description="Date the object was created in the SAP system.",
    )
    changed_by: str | None = Field(
        default=None,
        description="SAP user who last changed the object.",
    )
    changed_date: date | None = Field(
        default=None,
        description="Date the object was last changed.",
    )

    # ---- Status --------------------------------------------------------
    active_status: str | None = Field(
        default=None,
        description="Object activation status returned by the ECC RFC.",
    )

    # ---- Discovery metadata --------------------------------------------
    match_reason: MatchReason | None = Field(
        default=None,
        description=(
            "Indicates which discovery criterion matched this object: "
            "PACKAGE, NAME_PATTERN, or BOTH."
        ),
    )
    warning: str | None = Field(
        default=None,
        description="Optional warning message returned by the ECC RFC for this object.",
    )

    # ---- Phase 4+ placeholders (always empty in Phase 3) ---------------
    metadata: dict[str, Any] = Field(
        default_factory=dict,
        description="Supplementary metadata from ECC (Phase 3: minimal).",
    )
    source_components: list[Any] = Field(
        default_factory=list,
        description="ABAP source components – empty until Phase 4.",
    )
    relationships: list[Any] = Field(
        default_factory=list,
        description="Object relationships – empty until Phase 8.",
    )


# ---------------------------------------------------------------------------
# Package resolution models (Phase 3 — OData PackagesSet)
# ---------------------------------------------------------------------------


class RepositoryPackage(BaseModel):
    """A single SAP development package returned by PackagesSet (OData).

    Maps the OData V2 fields from ZNCOREIQ_REPO_SRV/PackagesSet to
    normalized Python names.
    """

    package: str = Field(description="SAP package name (e.g. 'ZNCOREIQ').")
    parent_package: str | None = Field(
        default=None,
        description="Parent package name; None or empty string for root packages.",
    )
    description: str | None = Field(
        default=None,
        description="Short text description of the package.",
    )
    depth: int = Field(
        default=0,
        description="Hierarchy depth: 0 = root, 1 = child, 2 = grandchild, etc.",
    )


# Backward-compatible alias — existing code that imports PackageInfo continues to work.
PackageInfo = RepositoryPackage


class PackageResolutionResult(BaseModel):
    """Normalized result of a PackagesSet OData call.

    Returned by ODataSAPConnectionProvider.get_packages() and surfaced
    through RepositoryService.resolve_packages() to the REST API.
    """

    system_id: str | None = Field(
        default=None,
        description="SAP system ID (SID) of the source ECC system.",
    )
    client: str | None = Field(
        default=None,
        description="SAP client used for the package resolution call.",
    )
    root_package: str = Field(
        description="The root package name that was requested.",
    )
    include_subpackages: bool = Field(
        default=False,
        description="Whether subpackage resolution was requested.",
    )
    max_depth: int = Field(
        default=20,
        description="Maximum recursion depth that was applied.",
    )
    truncated: bool = Field(
        default=False,
        description=(
            "True when SAP reported that the result set was truncated "
            "due to max_depth or an internal SAP limit."
        ),
    )
    message: str | None = Field(
        default=None,
        description="Human-readable status or warning message from SAP.",
    )
    packages: list[RepositoryPackage] = Field(default_factory=list)
