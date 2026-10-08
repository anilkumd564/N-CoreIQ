"""Phase 3 repository search request models.

Defines the canonical RepositorySearchRequest used by the REST API and the
RepositoryDiscoveryService.  All normalisation, safety rules, and object-type
validation live here so the service layer receives clean, pre-validated input.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field, field_validator, model_validator

from app.models.object_types import PHASE3_OBJECT_TYPES

# ---------------------------------------------------------------------------
# Suspicious pattern characters that must never be forwarded to ECC.
# The check is intentionally conservative: SAP pattern syntax uses only
# * (wildcard) and + (single-char wildcard).  Anything resembling SQL or
# shell injection is rejected outright.
# ---------------------------------------------------------------------------
_INVALID_PATTERN_CHARS = {";", '"', "'", "--", "SELECT", "WHERE", "DROP", "FROM"}

# Default result ceiling applied when the caller does not supply max_results.
_DEFAULT_MAX_RESULTS: int = 1000

# Hard application limit – requests above this are rejected with 422.
_HARD_MAX_RESULTS: int = 10_000


class RepositorySearchRequest(BaseModel):
    """Canonical repository discovery request.

    At least one of the following must be present to prevent an accidental
    full-TADIR scan:

    * ``packages``          – one or more SAP package names
    * ``name_patterns``     – one or more Z/Y-style wildcard patterns
    * ``all_custom_objects`` – explicit flag to request all Z* / Y* objects

    ``object_types`` acts as a filter and is independent of the selector
    requirement above.  When omitted the service uses all Phase 3 supported
    object types.
    """

    packages: list[str] = Field(
        default_factory=list,
        description=(
            "One or more SAP development package names (e.g. 'ZFINANCE'). "
            "Inputs are trimmed, uppercased, and deduplicated automatically."
        ),
    )
    include_subpackages: bool = Field(
        default=False,
        description=(
            "When true the ECC side resolves the full package hierarchy "
            "below each supplied package."
        ),
    )
    name_patterns: list[str] = Field(
        default_factory=list,
        description=(
            "SAP-style wildcard patterns for object names (e.g. 'ZFI*', 'Y*'). "
            "Inputs are trimmed, uppercased, and deduplicated automatically. "
            "SQL-style expressions are not supported."
        ),
    )
    object_types: list[str] = Field(
        default_factory=list,
        description=(
            "Phase 3 supported SAP object types to include in the search. "
            "When empty, all Phase 3 supported types are used. "
            f"Supported: {sorted(PHASE3_OBJECT_TYPES)}"
        ),
    )
    all_custom_objects: bool = Field(
        default=False,
        description=(
            "When true, N-CoreIQ treats this as an explicit request for all "
            "customer-namespace (Z* / Y*) objects. This is the only way to "
            "perform a broad custom-namespace scan without supplying packages "
            "or name patterns."
        ),
    )
    max_results: int = Field(
        default=_DEFAULT_MAX_RESULTS,
        ge=1,
        le=_HARD_MAX_RESULTS,
        description=(
            f"Maximum number of repository objects to return "
            f"(1–{_HARD_MAX_RESULTS}, default {_DEFAULT_MAX_RESULTS})."
        ),
    )

    # ------------------------------------------------------------------
    # Reserved for future use – Phase 4+ date-range filtering.
    # The ECC RFC contract does not yet expose these as server-side filters
    # so they are accepted by the model but deliberately NOT forwarded.
    # ------------------------------------------------------------------
    changed_after: date | None = Field(
        default=None,
        description="(Reserved – not yet implemented) Filter by last-changed date.",
    )
    changed_before: date | None = Field(
        default=None,
        description="(Reserved – not yet implemented) Filter by last-changed date.",
    )
    changed_by: str | None = Field(
        default=None,
        description="(Reserved – not yet implemented) Filter by last-changed user.",
    )

    # ------------------------------------------------------------------
    # Field-level validators
    # ------------------------------------------------------------------

    @field_validator("packages")
    @classmethod
    def _normalize_packages(cls, values: list[str]) -> list[str]:
        """Trim, uppercase, deduplicate package names; drop blank entries."""
        seen: set[str] = set()
        result: list[str] = []
        for raw in values:
            normalised = raw.strip().upper()
            if not normalised:
                continue
            if normalised not in seen:
                seen.add(normalised)
                result.append(normalised)
        return result

    @field_validator("name_patterns")
    @classmethod
    def _normalize_patterns(cls, values: list[str]) -> list[str]:
        """Trim, uppercase, deduplicate patterns; reject unsafe expressions."""
        seen: set[str] = set()
        result: list[str] = []
        for raw in values:
            normalised = raw.strip().upper()
            if not normalised:
                continue
            # Reject any pattern that contains SQL-like or shell-injection
            # fragments that should never be forwarded to the ECC RFC.
            for bad in _INVALID_PATTERN_CHARS:
                if bad in normalised:
                    raise ValueError(
                        f"Invalid or unsupported name pattern {raw!r}. "
                        "Only SAP wildcard syntax (* and +) is supported."
                    )
            if normalised not in seen:
                seen.add(normalised)
                result.append(normalised)
        return result

    @field_validator("object_types")
    @classmethod
    def _validate_object_types(cls, values: list[str]) -> list[str]:
        """Normalise, deduplicate, and validate object types against the Phase 3 allow-list."""
        seen: set[str] = set()
        result: list[str] = []
        for raw in values:
            normalised = raw.strip().upper()
            if not normalised:
                continue
            if normalised not in PHASE3_OBJECT_TYPES:
                raise ValueError(
                    f"Unsupported object type {raw!r}. "
                    f"Phase 3 supported types: {sorted(PHASE3_OBJECT_TYPES)}"
                )
            if normalised not in seen:
                seen.add(normalised)
                result.append(normalised)
        return result

    # ------------------------------------------------------------------
    # Cross-field safety validator
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _require_discovery_selector(self) -> "RepositorySearchRequest":
        """Reject requests that would cause an unfiltered full TADIR scan.

        A request is only valid when at least one of the following is true:
        * ``packages`` is non-empty
        * ``name_patterns`` is non-empty
        * ``all_custom_objects`` is True
        """
        if not self.packages and not self.name_patterns and not self.all_custom_objects:
            raise ValueError(
                "Repository discovery requires at least one package, "
                "name pattern, or all_custom_objects=true."
            )
        return self


# ---------------------------------------------------------------------------
# Ancillary models
# ---------------------------------------------------------------------------


class ObjectKey(BaseModel):
    """Identifies a single SAP repository object by type and name."""

    object_type: str
    object_name: str


class ExportRequest(RepositorySearchRequest):
    """Bulk export request – extends the search request with export options."""

    export_name: str = "ecc_custom_repository"
    include_source: bool = True
    include_metadata: bool = True
