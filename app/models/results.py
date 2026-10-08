"""Phase 3 repository search response models.

Defines the structured response returned by POST /api/v1/repository/search
and related result models.  Kept separate from request models so the API
schema remains stable and explicit.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.repository import RepositoryObject


# ---------------------------------------------------------------------------
# Sub-models embedded in RepositorySearchResponse
# ---------------------------------------------------------------------------


class SearchCriteria(BaseModel):
    """Echo of the effective search criteria sent to ECC.

    Reflects the normalised, validated values that were actually forwarded to
    Z_MCP_REPO_DISCOVER – not the raw caller input.
    """

    packages: list[str] = Field(default_factory=list)
    include_subpackages: bool = False
    name_patterns: list[str] = Field(default_factory=list)
    object_types: list[str] = Field(default_factory=list)
    all_custom_objects: bool = False
    max_results: int = 1000


class SearchStatistics(BaseModel):
    """Numeric summary of the discovery operation."""

    total_matches: int = Field(
        description=(
            "Total number of matching objects reported by ECC "
            "(may exceed 'returned' when results are truncated)."
        )
    )
    returned: int = Field(
        description="Number of objects actually included in this response."
    )


# ---------------------------------------------------------------------------
# Primary response model
# ---------------------------------------------------------------------------


class RepositorySearchResponse(BaseModel):
    """Response payload for POST /api/v1/repository/search.

    Wraps the discovered objects together with the echo of effective criteria,
    statistics, and a truncation indicator so callers always know whether the
    result set was capped by the max_results limit.
    """

    system_id: str | None = Field(
        default=None,
        description="SAP system ID (SID) of the source ECC system.",
    )
    client: str | None = Field(
        default=None,
        description="SAP client used for the discovery call.",
    )

    criteria: SearchCriteria = Field(
        description="Effective search criteria forwarded to the ECC RFC."
    )

    statistics: SearchStatistics = Field(
        description="Numeric summary of the discovery operation."
    )

    truncated: bool = Field(
        default=False,
        description=(
            "True when the ECC result set was larger than max_results and "
            "the returned object list is therefore incomplete."
        ),
    )

    message: str | None = Field(
        default=None,
        description="Human-readable status or warning message from the discovery operation.",
    )

    objects: list[RepositoryObject] = Field(
        default_factory=list,
        description="Discovered SAP repository objects.",
    )


# ---------------------------------------------------------------------------
# Phase 1/2 result models – retained for backward compatibility.
# ---------------------------------------------------------------------------


class SearchResult(BaseModel):
    """Legacy search result wrapper.  Preserved for backward compatibility."""

    objects: list[RepositoryObject] = Field(default_factory=list)
    total: int = 0
    truncated: bool = False


class ConnectionTestResult(BaseModel):
    """Result of a SAP RFC connectivity test (GET /api/v1/sap/ping)."""

    status: str = Field(description="Connection status, e.g. 'connected'.")
    connection_mode: str = Field(description="Provider mode used, e.g. 'local' or 'btp'.")
    system_id: str | None = Field(default=None, description="SAP system ID (SID).")
    client: str | None = Field(default=None, description="SAP client number.")
    sap_release: str | None = Field(default=None, description="SAP Basis release string.")
    timestamp: str | None = Field(default=None, description="Timestamp returned by ECC.")
    ping_method: str = Field(description="RFC function used for the ping.")
    message: str = Field(description="Human-readable result message.")


class HealthResult(BaseModel):
    """Response payload for GET /api/v1/health."""

    status: str = Field(description="Application health status, e.g. 'ok'.")
    app: str = Field(description="Application name.")
    version: str = Field(description="Application version (semver).")
    environment: str = Field(description="Deployment environment, e.g. 'local'.")
