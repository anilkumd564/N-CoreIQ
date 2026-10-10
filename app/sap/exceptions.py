"""Domain exceptions for SAP connectivity (Phase 2) and repository operations (Phase 3).

These exceptions are intentionally free of secrets. Never include
SAP passwords, raw RFC payloads, or stack traces in exception messages.
"""


class SAPError(Exception):
    """Base class for all controlled SAP domain errors."""


class SAPConfigurationError(SAPError):
    """Raised when required SAP configuration is missing or invalid.

    Maps to HTTP 422.
    """


class SAPRFCRuntimeUnavailableError(SAPError):
    """Raised when the local RFC runtime (PyRFC / SAP NetWeaver RFC SDK)
    is not installed or cannot be loaded.

    Maps to HTTP 501.
    """


class SAPConnectionError(SAPError):
    """Raised when SAP ECC cannot be reached.

    Maps to HTTP 503.
    """


class SAPAuthenticationError(SAPError):
    """Raised when SAP authentication fails (bad user/password/client).

    Maps to HTTP 503.
    """


class SAPCommunicationError(SAPError):
    """Raised for generic RFC communication failures.

    Maps to HTTP 503.
    """


class SAPFunctionExecutionError(SAPError):
    """Raised when an allowed RFC executes but SAP returns an application error.

    Maps to HTTP 503.
    """


class SAPRFCNotAllowedError(SAPError):
    """Raised when a caller attempts to invoke a function module that is
    not present on the Phase 2 RFC allow-list.

    This must be raised before any RFC call reaches SAP.
    """

    def __init__(self, function_name: str):
        self.function_name = function_name
        super().__init__(f"RFC function '{function_name}' is not permitted.")


class SAPProviderNotImplementedError(SAPError):
    """Raised when a connection mode is recognized but not yet implemented
    (e.g. BTP in Phase 2).

    Maps to HTTP 501.
    """


# ---------------------------------------------------------------------------
# OData-specific exceptions (Phase 2 — OData transport layer)
# ---------------------------------------------------------------------------


class SAPTLSError(SAPConnectionError):
    """Raised when TLS/HTTPS certificate validation fails against the SAP Gateway.

    Subclasses SAPConnectionError; maps to HTTP 503.
    Callers should advise the user to configure SAP_ODATA__CA_BUNDLE if the
    SAP system uses an internal corporate CA.
    """


class SAPTimeoutError(SAPConnectionError):
    """Raised when an HTTP request to the SAP Gateway times out.

    Subclasses SAPConnectionError; maps to HTTP 503.
    """


class SAPGatewayError(SAPCommunicationError):
    """Raised when the SAP Gateway returns an HTTP 5xx server error.

    Subclasses SAPCommunicationError; maps to HTTP 503.
    """


class SAPResponseParseError(SAPCommunicationError):
    """Raised when the SAP OData response cannot be parsed or does not
    conform to the expected OData V2 envelope structure.

    Subclasses SAPCommunicationError; maps to HTTP 503.
    """


# ---------------------------------------------------------------------------
# Repository domain exceptions (Phase 3 — package resolution)
# ---------------------------------------------------------------------------


class RepositoryPackageNotFoundError(SAPError):
    """Raised when PackagesSet returns an empty result set for the requested package.

    Indicates the package does not exist in the SAP system (or the calling
    user does not have visibility of it).  Maps to HTTP 404.
    """

    def __init__(self, package: str) -> None:
        self.package = package
        super().__init__(f"Package '{package}' was not found.")


# ---------------------------------------------------------------------------
# Repository domain exceptions (Phase 4 — object metadata / source)
# ---------------------------------------------------------------------------


class RepositoryObjectNotFoundError(SAPError):
    """Raised when ObjectMetadataSet or SourceLinesSet reports that the
    requested SAP repository object does not exist (Found=false or empty result).

    Maps to HTTP 404.
    """

    def __init__(self, object_type: str, object_name: str) -> None:
        self.object_type = object_type
        self.object_name = object_name
        super().__init__(
            f"Repository object {object_type}/{object_name} was not found."
        )


class RepositoryObjectTypeNotSupportedError(SAPError, ValueError):
    """Raised when the caller supplies an object type that is outside the
    Phase 4 supported-type set.

    Inherits from both SAPError and ValueError so that:
      - Route handlers can catch it as a specific domain exception.
      - Service-level callers that expect ValueError also catch it.

    Maps to HTTP 422.
    """

    def __init__(self, object_type: str, supported: frozenset[str]) -> None:
        self.object_type = object_type
        self.supported = supported
        super().__init__(
            f"Unsupported object type '{object_type}' for metadata/source extraction. "
            f"Phase 4 supported types: {sorted(supported)}"
        )


class RepositorySourceDataIntegrityError(SAPError):
    """Raised when SourceLinesSet returns conflicting line numbers within the
    same component (same component_type + component_name + line_no but
    different source_line values).

    Maps to HTTP 503 (upstream data integrity failure).
    """

    def __init__(
        self,
        component_type: str,
        component_name: str,
        line_no: int,
    ) -> None:
        self.component_type = component_type
        self.component_name = component_name
        self.line_no = line_no
        super().__init__(
            f"Source data integrity error: conflicting source lines for "
            f"component {component_type}/{component_name} at line {line_no}."
        )


# ---------------------------------------------------------------------------
# Phase 8 — Dependency & Relationship Intelligence exceptions
# ---------------------------------------------------------------------------


class DependencyObjectNotFoundError(SAPError):
    """Raised when the source object for a dependency query does not exist.

    Maps to HTTP 404.
    """

    def __init__(self, object_type: str, object_name: str) -> None:
        self.object_type = object_type
        self.object_name = object_name
        super().__init__(
            f"Dependency source object {object_type}/{object_name} was not found."
        )


class DependencyScopeTooLargeError(SAPError, ValueError):
    """Raised when a reverse dependency or impact scope exceeds allowed limits.

    Maps to HTTP 422.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class DependencyTraversalLimitError(SAPError):
    """Raised when a traversal exceeds configured hard limits (depth/nodes/edges).

    Maps to HTTP 422.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class DependencyDataIntegrityError(SAPError):
    """Raised when DependencyRelationsSet returns data that cannot be parsed
    or fails integrity checks.

    Maps to HTTP 503.
    """

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class DependencyProviderError(SAPCommunicationError):
    """Raised when the SAP provider returns an unexpected error specifically
    during a DependencyRelationsSet call.

    Subclasses SAPCommunicationError; maps to HTTP 503.
    """
