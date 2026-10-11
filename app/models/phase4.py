"""Phase 4 canonical models for SAP repository object metadata and source extraction.

These models represent the normalised N-CoreIQ API contract for:
  - ObjectMetadataSet  → RepositoryObjectMetadata
  - SourceLinesSet     → RepositoryObjectSource (containing RepositorySourceComponent
                         which contains RepositorySourceLine items)

Source text is preserved exactly as returned by SAP.  Do not reformat,
uppercase, strip indentation, or collapse whitespace on source lines.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Phase 4 supported object types
# ---------------------------------------------------------------------------

#: All Phase 3 types plus FUNC for Phase 4 metadata/source extraction.
PHASE4_SUPPORTED_OBJECT_TYPES: frozenset[str] = frozenset(
    {
        "PROG",
        "CLAS",
        "INTF",
        "FUGR",
        "FUNC",
        "TABL",
        "DTEL",
        "DOMA",
        "VIEW",
        "SHLP",
        "ENQU",
        "TRAN",
        "MSAG",
    }
)

# Hard maximum for source line extraction (used only when an explicit limit is requested).
MAX_SOURCE_LINES_HARD_LIMIT: int = 20_000
# Default is None = unlimited (complete source retrieval).
DEFAULT_MAX_SOURCE_LINES: int | None = None
MIN_SOURCE_LINES: int = 1


# ---------------------------------------------------------------------------
# RepositoryObjectMetadata
# ---------------------------------------------------------------------------


class RepositoryObjectMetadata(BaseModel):
    """Canonical N-CoreIQ metadata record for a single SAP repository object.

    Fields map to the ObjectMetadataSet entity attributes returned by
    the ZNCOREIQ_REPO_SRV OData V2 service.  All fields are optional
    except the identity pair (sap_object_type, object_name) and found.

    Dates are preserved in YYYYMMDD format as returned by SAP ECC.
    Blank / null dates from SAP are stored as None.
    """

    # --- Identity -------------------------------------------------------
    sap_object_type: str = Field(
        description="SAP repository object type, e.g. PROG, CLAS, TABL."
    )
    object_name: str = Field(description="SAP repository object name.")

    # --- System context -------------------------------------------------
    system_id: str | None = Field(
        default=None, description="SAP system ID (e.g. NC1)."
    )
    client: str | None = Field(default=None, description="SAP client (e.g. 800).")

    # --- Object classification ------------------------------------------
    pgmid: str | None = Field(
        default=None, description="SAP program ID (PGMID), e.g. R3TR."
    )
    package: str | None = Field(
        default=None, description="Development package containing the object."
    )
    object_subtype: str | None = Field(
        default=None, description="Object subtype, if applicable."
    )
    program_name: str | None = Field(
        default=None,
        description="Program name (for includes / function module contexts).",
    )
    function_group: str | None = Field(
        default=None, description="Function group (for FUNC objects)."
    )
    table_class: str | None = Field(
        default=None, description="Table class (for TABL objects)."
    )

    # --- Authoring / lifecycle ------------------------------------------
    author: str | None = Field(
        default=None, description="SAP user who created the object."
    )
    master_language: str | None = Field(
        default=None, description="Master language key, e.g. EN."
    )
    source_system: str | None = Field(
        default=None, description="Originating source system."
    )
    created_date: str | None = Field(
        default=None, description="Creation date in YYYYMMDD format."
    )
    changed_by: str | None = Field(
        default=None, description="SAP user who last changed the object."
    )
    changed_date: str | None = Field(
        default=None, description="Last-changed date in YYYYMMDD format."
    )

    # --- Descriptive ----------------------------------------------------
    description: str | None = Field(
        default=None, description="Short description / text of the object."
    )
    active_status: str | None = Field(
        default=None, description="Active/inactive status flag."
    )

    # --- Result meta ----------------------------------------------------
    found: bool = Field(
        default=True,
        description="True when SAP confirmed the object exists.",
    )
    warning: str | None = Field(
        default=None, description="Warning message from SAP, if any."
    )
    message: str | None = Field(
        default=None, description="Informational message from SAP, if any."
    )


# ---------------------------------------------------------------------------
# RepositorySourceLine
# ---------------------------------------------------------------------------


class RepositorySourceLine(BaseModel):
    """A single numbered source line within a source component.

    Source text is preserved exactly as returned by SAP (Unicode, ABAP
    indentation, comments, special characters).  Do not transform this value.
    """

    component_type: str = Field(
        description="Component type label returned by SAP SourceLinesSet."
    )
    component_name: str = Field(
        description="Component name returned by SAP SourceLinesSet."
    )
    line_no: int = Field(
        description="1-based line number within this component, ascending."
    )
    source_line: str = Field(
        description="Raw source text for this line (Unicode preserved)."
    )


# ---------------------------------------------------------------------------
# RepositorySourceComponent
# ---------------------------------------------------------------------------


class RepositorySourceComponent(BaseModel):
    """Grouped source lines for one logical component of a SAP repository object.

    A single SAP repository object may consist of multiple components
    (e.g. a class pool, method includes, local implementations, test classes).
    Each component is identified by the (component_type, component_name) pair
    and contains ordered source lines.

    Attributes:
        component_type:  Component type label (e.g. PROGRAM, CLASS, INCLUDE).
        component_name:  Component name (e.g. ZCL_SAMPLE, ZCL_SAMPLE=====CCDEF).
        line_count:      Number of source lines in this component.
        lines:           Ordered source lines (ascending line_no).
        source_text:     Derived full source text (newline-joined from lines).
                         Informational only; lines[] is the authoritative source.
    """

    component_type: str
    component_name: str
    line_count: int | None = Field(
        default=None,
        description="Number of source lines; computed from lines if not supplied.",
    )
    lines: list[RepositorySourceLine]
    source_text: str | None = Field(
        default=None,
        description=(
            "Full source text derived by joining lines with newlines. "
            "Informational; lines[] is authoritative."
        ),
    )

    @model_validator(mode="after")
    def _compute_line_count(self) -> "RepositorySourceComponent":
        if self.line_count is None:
            self.line_count = len(self.lines)
        return self


# ---------------------------------------------------------------------------
# RepositoryObjectSource
# ---------------------------------------------------------------------------


class RepositoryObjectSource(BaseModel):
    """Canonical N-CoreIQ source/definition record for a single SAP repository object.

    'source' in N-CoreIQ Phase 4 means the ABAP source code for executable
    objects (PROG, CLAS, INTF, FUGR, FUNC) or the structured textual
    technical definition for DDIC/configuration objects (TABL, DTEL, DOMA,
    VIEW, SHLP, ENQU, TRAN, MSAG).

    component_type on each RepositorySourceComponent identifies the
    representation type so downstream consumers can distinguish ABAP source
    from structured technical definitions.
    """

    # --- Identity -------------------------------------------------------
    sap_object_type: str = Field(
        description="SAP repository object type, e.g. PROG, CLAS, TABL."
    )
    object_name: str = Field(description="SAP repository object name.")

    # --- System context -------------------------------------------------
    system_id: str | None = Field(default=None)
    client: str | None = Field(default=None)

    # --- Extraction parameters ------------------------------------------
    max_lines: int | None = Field(
        default=None,
        description="Maximum lines requested by the caller. None means complete (unlimited) retrieval.",
    )
    returned_lines: int = Field(
        description="Total number of source lines returned across all components."
    )
    component_count: int = Field(
        description="Number of source components in the response."
    )

    # --- Result meta ----------------------------------------------------
    found: bool = Field(
        default=True,
        description="True when SAP confirmed the object was found.",
    )
    truncated: bool = Field(
        default=False,
        description="True when SAP reported that the result was truncated.",
    )
    message: str | None = Field(
        default=None,
        description="Informational message from SAP, if any.",
    )

    # --- Source components ----------------------------------------------
    components: list[RepositorySourceComponent] = Field(
        default_factory=list,
        description="Ordered list of source components.",
    )
