"""N-CoreIQ — Canonical API error model (Phase 10).

Defines the Version 1.0 stable error response contract:

    {
        "code": "REPOSITORY_OBJECT_NOT_FOUND",
        "message": "Repository object was not found.",
        "correlation_id": "...",
        "details": {}          # optional
    }

Error codes are stable Version 1.0 identifiers.  They must not be
renamed or removed without a major-version increment.

Usage in route handlers::

    from app.models.errors import APIError, ErrorCode
    raise HTTPException(
        status_code=404,
        detail=APIError(
            code=ErrorCode.REPOSITORY_OBJECT_NOT_FOUND,
            message="Repository object PROG/ZMY_PROG was not found.",
            correlation_id=get_correlation_id(),
        ).model_dump(),
    )
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Error code catalog — Version 1.0 stable identifiers
# ---------------------------------------------------------------------------


class ErrorCode:
    """Stable Version 1.0 error code constants.

    These identifiers form part of the public API contract.  Do not rename
    or remove codes without a breaking-change notice.
    """

    # ------------------------------------------------------------------
    # Validation / input
    # ------------------------------------------------------------------
    VALIDATION_ERROR = "VALIDATION_ERROR"
    """Request body or query parameter failed validation."""

    # ------------------------------------------------------------------
    # SAP connectivity
    # ------------------------------------------------------------------
    SAP_UNAVAILABLE = "SAP_UNAVAILABLE"
    """SAP ECC system cannot be reached."""

    SAP_AUTHENTICATION_FAILED = "SAP_AUTHENTICATION_FAILED"
    """SAP ECC rejected the configured credentials."""

    SAP_AUTHORIZATION_FAILED = "SAP_AUTHORIZATION_FAILED"
    """SAP ECC denied the operation (authorization object missing)."""

    SAP_OBJECT_NOT_FOUND = "SAP_OBJECT_NOT_FOUND"
    """The SAP object referenced in the request does not exist."""

    SAP_RESPONSE_INVALID = "SAP_RESPONSE_INVALID"
    """SAP returned a response that could not be parsed or is structurally invalid."""

    SAP_NOT_CONFIGURED = "SAP_NOT_CONFIGURED"
    """Required SAP connection settings are missing or incomplete."""

    SAP_GATEWAY_ERROR = "SAP_GATEWAY_ERROR"
    """SAP Gateway (OData) returned an HTTP 5xx error."""

    SAP_TIMEOUT = "SAP_TIMEOUT"
    """Request to SAP ECC timed out."""

    SAP_TLS_ERROR = "SAP_TLS_ERROR"
    """TLS certificate validation failed for the SAP Gateway endpoint."""

    SAP_PROVIDER_NOT_IMPLEMENTED = "SAP_PROVIDER_NOT_IMPLEMENTED"
    """The requested SAP connection mode is not yet implemented."""

    SAP_RFC_NOT_ALLOWED = "SAP_RFC_NOT_ALLOWED"
    """Caller attempted to invoke an RFC function module not on the allow-list."""

    SAP_RFC_RUNTIME_UNAVAILABLE = "SAP_RFC_RUNTIME_UNAVAILABLE"
    """The local RFC runtime (PyRFC/NetWeaver RFC SDK) is not installed."""

    # ------------------------------------------------------------------
    # Repository objects
    # ------------------------------------------------------------------
    REPOSITORY_OBJECT_NOT_FOUND = "REPOSITORY_OBJECT_NOT_FOUND"
    """The requested repository object does not exist in SAP."""

    REPOSITORY_OBJECT_TYPE_NOT_SUPPORTED = "REPOSITORY_OBJECT_TYPE_NOT_SUPPORTED"
    """The supplied object type is not supported by this operation."""

    REPOSITORY_PACKAGE_NOT_FOUND = "REPOSITORY_PACKAGE_NOT_FOUND"
    """The requested SAP development package does not exist."""

    REPOSITORY_SOURCE_INTEGRITY_ERROR = "REPOSITORY_SOURCE_INTEGRITY_ERROR"
    """SAP returned conflicting source-line data for the same component."""

    # ------------------------------------------------------------------
    # Dependency intelligence
    # ------------------------------------------------------------------
    DEPENDENCY_LIMIT_EXCEEDED = "DEPENDENCY_LIMIT_EXCEEDED"
    """Dependency traversal exceeded configured node/edge/depth limits."""

    REVERSE_SCOPE_TOO_LARGE = "REVERSE_SCOPE_TOO_LARGE"
    """Reverse dependency scope exceeds maximum allowed candidate count."""

    # ------------------------------------------------------------------
    # Extraction jobs
    # ------------------------------------------------------------------
    EXTRACTION_LIMIT_EXCEEDED = "EXTRACTION_LIMIT_EXCEEDED"
    """Bulk extraction request exceeds the configured object limit."""

    JOB_NOT_FOUND = "JOB_NOT_FOUND"
    """The specified extraction job does not exist."""

    JOB_INVALID_STATE = "JOB_INVALID_STATE"
    """The requested operation is not valid for the job's current state."""

    JOB_NOT_CANCELLABLE = "JOB_NOT_CANCELLABLE"
    """The job cannot be cancelled in its current state."""

    JOB_NOT_RETRYABLE = "JOB_NOT_RETRYABLE"
    """The job cannot be retried — it is not in a terminal failed/cancelled state."""

    # ------------------------------------------------------------------
    # Artifacts
    # ------------------------------------------------------------------
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"
    """The requested artifact does not exist."""

    ARTIFACT_EXPIRED = "ARTIFACT_EXPIRED"
    """The requested artifact has expired and been removed."""

    ARTIFACT_UNAVAILABLE = "ARTIFACT_UNAVAILABLE"
    """The artifact exists in the job record but is not accessible."""

    # ------------------------------------------------------------------
    # Authentication / authorization
    # ------------------------------------------------------------------
    AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
    """The request requires authentication (no or invalid bearer token)."""

    AUTHORIZATION_DENIED = "AUTHORIZATION_DENIED"
    """The authenticated principal does not hold the required scope/role."""

    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    """The supplied JWT has expired."""

    TOKEN_INVALID = "TOKEN_INVALID"
    """The supplied JWT is structurally invalid or the signature is wrong."""

    # ------------------------------------------------------------------
    # Configuration / infrastructure
    # ------------------------------------------------------------------
    CONFIGURATION_ERROR = "CONFIGURATION_ERROR"
    """Application is misconfigured — a required setting is absent or invalid."""

    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    """A required downstream service (database, artifact store, etc.) is unavailable."""

    # ------------------------------------------------------------------
    # Generic
    # ------------------------------------------------------------------
    INTERNAL_ERROR = "INTERNAL_ERROR"
    """An unexpected internal error occurred.  Consult logs with correlation_id."""

    NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
    """The requested operation is not implemented in this version."""


# ---------------------------------------------------------------------------
# Canonical error response model
# ---------------------------------------------------------------------------


class APIError(BaseModel):
    """Version 1.0 canonical API error response.

    All error responses from N-CoreIQ REST endpoints use this structure.
    The ``code`` field is a stable machine-readable identifier from
    :class:`ErrorCode`.  The ``message`` field is human-readable and may
    change between versions.  The ``correlation_id`` (when present) allows
    support personnel to trace the request through structured logs.
    """

    code: str = Field(
        description="Stable machine-readable error code (e.g. REPOSITORY_OBJECT_NOT_FOUND)."
    )
    message: str = Field(
        description="Human-readable description of the error."
    )
    correlation_id: str | None = Field(
        default=None,
        description="Correlation ID for log tracing.  Include when contacting support.",
    )
    details: dict[str, Any] | None = Field(
        default=None,
        description="Optional structured details (field errors, context values).",
    )

    model_config = {
        "json_schema_extra": {
            "example": {
                "code": "REPOSITORY_OBJECT_NOT_FOUND",
                "message": "Repository object PROG/ZMY_PROGRAM was not found.",
                "correlation_id": "abc123",
                "details": None,
            }
        }
    }


# ---------------------------------------------------------------------------
# Exception-to-error-code mapping helpers
# ---------------------------------------------------------------------------


def exception_to_error_code(exc: Exception) -> str:
    """Map a known N-CoreIQ exception to its stable Version 1.0 error code.

    Unknown exceptions map to INTERNAL_ERROR.  This function never raises.
    """
    from app.sap.exceptions import (
        SAPAuthenticationError,
        SAPCommunicationError,
        SAPConfigurationError,
        SAPConnectionError,
        SAPFunctionExecutionError,
        SAPGatewayError,
        SAPProviderNotImplementedError,
        SAPRFCNotAllowedError,
        SAPRFCRuntimeUnavailableError,
        SAPResponseParseError,
        SAPTimeoutError,
        SAPTLSError,
        RepositoryObjectNotFoundError,
        RepositoryObjectTypeNotSupportedError,
        RepositoryPackageNotFoundError,
        RepositorySourceDataIntegrityError,
        DependencyObjectNotFoundError,
        DependencyScopeTooLargeError,
        DependencyTraversalLimitError,
        DependencyDataIntegrityError,
        DependencyProviderError,
    )

    if isinstance(exc, SAPTLSError):
        return ErrorCode.SAP_TLS_ERROR
    if isinstance(exc, SAPTimeoutError):
        return ErrorCode.SAP_TIMEOUT
    if isinstance(exc, SAPGatewayError):
        return ErrorCode.SAP_GATEWAY_ERROR
    if isinstance(exc, SAPResponseParseError):
        return ErrorCode.SAP_RESPONSE_INVALID
    if isinstance(exc, SAPAuthenticationError):
        return ErrorCode.SAP_AUTHENTICATION_FAILED
    if isinstance(exc, SAPConnectionError):
        return ErrorCode.SAP_UNAVAILABLE
    if isinstance(exc, SAPConfigurationError):
        return ErrorCode.SAP_NOT_CONFIGURED
    if isinstance(exc, SAPRFCNotAllowedError):
        return ErrorCode.SAP_RFC_NOT_ALLOWED
    if isinstance(exc, SAPRFCRuntimeUnavailableError):
        return ErrorCode.SAP_RFC_RUNTIME_UNAVAILABLE
    if isinstance(exc, SAPProviderNotImplementedError):
        return ErrorCode.SAP_PROVIDER_NOT_IMPLEMENTED
    if isinstance(exc, (SAPFunctionExecutionError, SAPCommunicationError)):
        return ErrorCode.SAP_UNAVAILABLE
    if isinstance(exc, RepositoryObjectNotFoundError):
        return ErrorCode.REPOSITORY_OBJECT_NOT_FOUND
    if isinstance(exc, RepositoryObjectTypeNotSupportedError):
        return ErrorCode.REPOSITORY_OBJECT_TYPE_NOT_SUPPORTED
    if isinstance(exc, RepositoryPackageNotFoundError):
        return ErrorCode.REPOSITORY_PACKAGE_NOT_FOUND
    if isinstance(exc, RepositorySourceDataIntegrityError):
        return ErrorCode.REPOSITORY_SOURCE_INTEGRITY_ERROR
    if isinstance(exc, DependencyObjectNotFoundError):
        return ErrorCode.REPOSITORY_OBJECT_NOT_FOUND
    if isinstance(exc, DependencyScopeTooLargeError):
        return ErrorCode.REVERSE_SCOPE_TOO_LARGE
    if isinstance(exc, DependencyTraversalLimitError):
        return ErrorCode.DEPENDENCY_LIMIT_EXCEEDED
    if isinstance(exc, (DependencyDataIntegrityError, DependencyProviderError)):
        return ErrorCode.SAP_RESPONSE_INVALID
    if isinstance(exc, ValueError):
        return ErrorCode.VALIDATION_ERROR
    if isinstance(exc, NotImplementedError):
        return ErrorCode.NOT_IMPLEMENTED
    return ErrorCode.INTERNAL_ERROR
