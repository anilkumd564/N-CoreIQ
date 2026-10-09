"""Phase 5 canonical models for bulk SAP repository object extraction and export.

These models represent the N-CoreIQ API contract for:
  - BulkExtractionRequest   → input for POST /api/v1/repository/extract
  - BulkExtractionResult    → output from bulk extraction
  - ExtractedRepositoryObject → per-object extraction outcome
  - ExportRequest           → input for POST /api/v1/repository/export

All normalisation and validation live here so service and route layers
receive clean, pre-validated input.
"""

from __future__ import annotations

import re
from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator

from app.models.phase4 import (
    DEFAULT_MAX_SOURCE_LINES,
    MAX_SOURCE_LINES_HARD_LIMIT,
    MIN_SOURCE_LINES,
    PHASE4_SUPPORTED_OBJECT_TYPES,
    RepositoryObjectMetadata,
    RepositoryObjectSource,
)
from app.models.requests import RepositorySearchRequest

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PHASE5_SCHEMA_VERSION: str = "1.0"

# Hard safety ceiling – no matter what settings say, never exceed this.
BULK_OBJECTS_HARD_CEILING: int = 1000


# ---------------------------------------------------------------------------
# Enumerations
# ---------------------------------------------------------------------------


class ObjectExtractionStatus(str, Enum):
    """Per-object extraction outcome."""

    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class BulkExtractionStatus(str, Enum):
    """Overall bulk extraction outcome."""

    COMPLETED = "COMPLETED"
    COMPLETED_WITH_ERRORS = "COMPLETED_WITH_ERRORS"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ExtractionStage(str, Enum):
    """Stage at which an extraction error occurred."""

    DISCOVERY = "DISCOVERY"
    METADATA = "METADATA"
    SOURCE = "SOURCE"
    EXPORT = "EXPORT"


class ExportFormat(str, Enum):
    """Supported export formats."""

    JSON = "json"
    ZIP = "zip"


# ---------------------------------------------------------------------------
# RepositoryObjectIdentifier
# ---------------------------------------------------------------------------


class RepositoryObjectIdentifier(BaseModel):
    """Identifies a single SAP repository object by type and name.

    Values are trimmed, uppercased, and validated on construction.
    """

    object_type: str = Field(description="SAP object type, e.g. PROG, CLAS, TABL.")
    object_name: str = Field(description="SAP object name, e.g. ZREPORT1.")

    @model_validator(mode="after")
    def _normalise_and_validate(self) -> "RepositoryObjectIdentifier":
        object.__setattr__(self, "object_type", self.object_type.strip().upper())
        object.__setattr__(self, "object_name", self.object_name.strip().upper())
        if not self.object_type:
            raise ValueError("object_type must not be blank.")
        if not self.object_name:
            raise ValueError("object_name must not be blank.")
        if self.object_type not in PHASE4_SUPPORTED_OBJECT_TYPES:
            raise ValueError(
                f"Unsupported object type {self.object_type!r}. "
                f"Phase 5 supported types: {sorted(PHASE4_SUPPORTED_OBJECT_TYPES)}"
            )
        return self

    def __hash__(self) -> int:
        return hash((self.object_type, self.object_name))

    def __eq__(self, other: object) -> bool:
        if isinstance(other, RepositoryObjectIdentifier):
            return self.object_type == other.object_type and self.object_name == other.object_name
        return NotImplemented

    def __lt__(self, other: "RepositoryObjectIdentifier") -> bool:
        return (self.object_type, self.object_name) < (other.object_type, other.object_name)


# ---------------------------------------------------------------------------
# ExtractionError
# ---------------------------------------------------------------------------


class ExtractionError(BaseModel):
    """Safe object-level error record.

    Never contains stack traces, SAP innererror, credentials, or raw HTTP
    Authorization details.
    """

    code: str = Field(description="Short machine-readable error code.")
    message: str = Field(description="Human-readable error message (sanitised).")
    stage: ExtractionStage = Field(description="Extraction stage where the error occurred.")


# ---------------------------------------------------------------------------
# BulkExtractionRequest
# ---------------------------------------------------------------------------


class BulkExtractionRequest(BaseModel):
    """Input model for POST /api/v1/repository/extract.

    Exactly one selection method must be provided:
      - ``objects``  – explicit list of RepositoryObjectIdentifier
      - ``search``   – RepositorySearchRequest (Phase 3)

    Supplying both or neither raises a validation error.
    """

    # --- Selection -------------------------------------------------------
    objects: list[RepositoryObjectIdentifier] | None = Field(
        default=None,
        description="Explicit list of SAP repository objects to extract.",
    )
    search: RepositorySearchRequest | None = Field(
        default=None,
        description=(
            "Phase 3 search criteria.  N-CoreIQ will discover matching objects "
            "then extract them.  Mutually exclusive with 'objects'."
        ),
    )

    # --- Extraction options ----------------------------------------------
    include_metadata: bool = Field(
        default=True,
        description="Retrieve technical metadata for each object.",
    )
    include_source: bool = Field(
        default=True,
        description="Retrieve source code or technical definition for each object.",
    )
    source_max_lines: int = Field(
        default=DEFAULT_MAX_SOURCE_LINES,
        ge=MIN_SOURCE_LINES,
        le=MAX_SOURCE_LINES_HARD_LIMIT,
        description=(
            f"Maximum source lines per object "
            f"({MIN_SOURCE_LINES}–{MAX_SOURCE_LINES_HARD_LIMIT}). "
            f"Default: {DEFAULT_MAX_SOURCE_LINES}."
        ),
    )
    continue_on_error: bool = Field(
        default=True,
        description=(
            "When true (default), extraction continues after per-object failures. "
            "When false, extraction stops at the first object-level failure."
        ),
    )

    # ------------------------------------------------------------------
    # Cross-field validators
    # ------------------------------------------------------------------

    @model_validator(mode="after")
    def _validate_selection_and_options(self) -> "BulkExtractionRequest":
        # Exactly one selection method
        has_objects = self.objects is not None
        has_search = self.search is not None

        if has_objects and has_search:
            raise ValueError(
                "Provide either 'objects' or 'search', not both."
            )
        if not has_objects and not has_search:
            raise ValueError(
                "One of 'objects' or 'search' must be provided."
            )

        # At least one extraction type
        if not self.include_metadata and not self.include_source:
            raise ValueError(
                "At least one of include_metadata or include_source must be true."
            )

        # Empty explicit list is not useful
        if has_objects and self.objects is not None and len(self.objects) == 0:
            raise ValueError("'objects' list must not be empty.")

        return self


# ---------------------------------------------------------------------------
# ExtractedRepositoryObject
# ---------------------------------------------------------------------------


class ExtractedRepositoryObject(BaseModel):
    """Per-object extraction outcome within a BulkExtractionResult."""

    object_type: str = Field(description="SAP object type.")
    object_name: str = Field(description="SAP object name.")
    status: ObjectExtractionStatus = Field(description="Per-object extraction status.")
    metadata: RepositoryObjectMetadata | None = Field(
        default=None,
        description="Extracted metadata (populated when include_metadata=true and successful).",
    )
    source: RepositoryObjectSource | None = Field(
        default=None,
        description="Extracted source/definition (populated when include_source=true and successful).",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Non-fatal warnings for this object.",
    )
    errors: list[ExtractionError] = Field(
        default_factory=list,
        description="Errors that occurred during extraction of this object.",
    )
    duration_ms: float | None = Field(
        default=None,
        description="Wall-clock extraction time in milliseconds.",
    )


# ---------------------------------------------------------------------------
# BulkExtractionResult
# ---------------------------------------------------------------------------


class BulkExtractionResult(BaseModel):
    """Canonical result from a Phase 5 bulk extraction operation.

    This is the authoritative in-memory representation.  The ExportService
    serialises this into JSON or ZIP format.  The export service must NOT
    re-query SAP.
    """

    # --- Identity -------------------------------------------------------
    extraction_id: str = Field(description="UUID correlation identifier for this extraction.")
    schema_version: str = Field(
        default=PHASE5_SCHEMA_VERSION,
        description="Export schema version.",
    )

    # --- Timing ---------------------------------------------------------
    started_at: datetime = Field(description="UTC timestamp when extraction started.")
    completed_at: datetime = Field(description="UTC timestamp when extraction completed.")
    duration_ms: float = Field(description="Total extraction wall-clock time in milliseconds.")

    # --- Overall status -------------------------------------------------
    status: BulkExtractionStatus = Field(description="Overall extraction status.")

    # --- Selection context ----------------------------------------------
    selection: str = Field(description="Selection method used: 'explicit' or 'search'.")

    # --- Counts ---------------------------------------------------------
    requested_object_count: int = Field(description="Number of objects requested.")
    processed_object_count: int = Field(description="Number of objects processed.")
    successful_object_count: int = Field(description="Number of objects fully successful.")
    partial_object_count: int = Field(description="Number of objects partially successful.")
    failed_object_count: int = Field(description="Number of objects that failed.")
    skipped_object_count: int = Field(default=0, description="Number of objects skipped.")
    truncated_object_count: int = Field(
        default=0,
        description="Number of objects where source was truncated.",
    )

    # --- Extraction options used ----------------------------------------
    metadata_enabled: bool = Field(description="Whether metadata extraction was enabled.")
    source_enabled: bool = Field(description="Whether source extraction was enabled.")
    source_max_lines: int = Field(description="Source max lines setting used.")

    # --- System context (no credentials) --------------------------------
    system_id: str | None = Field(default=None, description="SAP system ID.")
    client: str | None = Field(default=None, description="SAP client.")

    # --- Warnings -------------------------------------------------------
    warnings: list[str] = Field(default_factory=list, description="Non-fatal extraction warnings.")

    # --- Objects --------------------------------------------------------
    objects: list[ExtractedRepositoryObject] = Field(
        default_factory=list,
        description="Per-object extraction results, sorted by (object_type, object_name).",
    )


# ---------------------------------------------------------------------------
# ExportRequest
# ---------------------------------------------------------------------------


class ExportRequest(BaseModel):
    """Input model for POST /api/v1/repository/export.

    Wraps a BulkExtractionRequest with an export format selector.
    """

    format: ExportFormat = Field(
        default=ExportFormat.ZIP,
        description="Export format: 'json' or 'zip'.",
    )
    extraction: BulkExtractionRequest = Field(
        description="Bulk extraction parameters."
    )


class BulkExportRequest(BulkExtractionRequest):
    """Input model for POST /api/v1/repository/export — flat form.

    Extends BulkExtractionRequest by adding an ``format`` field.
    All BulkExtractionRequest fields (objects, search, include_metadata, …)
    are inherited directly so the JSON body is a single flat object:

    .. code-block:: json

        {
          "format": "zip",
          "objects": [{"object_type": "PROG", "object_name": "ZREPORT1"}],
          "include_metadata": true,
          "include_source": true
        }
    """

    format: ExportFormat = Field(
        default=ExportFormat.ZIP,
        description="Export format: 'json' or 'zip'.",
    )

    def to_extraction_request(self) -> BulkExtractionRequest:
        """Return a BulkExtractionRequest view (drops the format field)."""
        return BulkExtractionRequest(
            objects=self.objects,
            search=self.search,
            include_metadata=self.include_metadata,
            include_source=self.include_source,
            source_max_lines=self.source_max_lines,
            continue_on_error=self.continue_on_error,
        )


# Backward-compatible alias kept for ExportRequest (nested-wrapper style).
# New code should prefer BulkExportRequest (flat style).

# Alias: tests and external code may import ObjectExtractionError.
ObjectExtractionError = ExtractionError

# Alias: Phase 6 test code uses ExtractionObjectRef for RepositoryObjectIdentifier.
ExtractionObjectRef = RepositoryObjectIdentifier

# Alias: Phase 6 test code uses ExtractionStatus for BulkExtractionStatus.
ExtractionStatus = BulkExtractionStatus
