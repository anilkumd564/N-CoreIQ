"""Domain exceptions for SAP connectivity (Phase 2).

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
