"""Defensive loader for the Python RFC adapter (PyRFC).

PyRFC requires the proprietary SAP NetWeaver RFC SDK native libraries to be
installed on the host machine. Those libraries must never be bundled in this
repository, and the application must be able to start even when they are not
present (e.g. CI, local dev without the SDK, BTP buildpacks without RFC).

This module performs the import lazily and defensively, exposing a simple
boolean/status check that callers can use before attempting any RFC call.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger("app.sap.rfc_runtime")

_pyrfc_module = None
_import_error: str | None = None
_import_attempted = False


@dataclass(frozen=True)
class RFCRuntimeStatus:
    available: bool
    detail: str


def _attempt_import() -> None:
    """Attempt to import pyrfc exactly once, capturing any failure safely."""
    global _pyrfc_module, _import_error, _import_attempted

    if _import_attempted:
        return
    _import_attempted = True

    try:
        import pyrfc  # type: ignore

        _pyrfc_module = pyrfc
        logger.info(
            "sap_rfc_runtime_loaded",
            extra={"operation": "rfc_runtime_import", "result": "success"},
        )
    except Exception as exc:  # noqa: BLE001 - defensive by design
        # Deliberately broad: PyRFC can fail with ImportError (module missing)
        # or OSError/RuntimeError (native SAP NetWeaver RFC SDK libraries missing).
        _pyrfc_module = None
        _import_error = (
            "SAP NetWeaver RFC SDK / PyRFC runtime is not available on this host. "
            f"({exc.__class__.__name__})"
        )
        logger.warning(
            "sap_rfc_runtime_unavailable",
            extra={"operation": "rfc_runtime_import", "result": "unavailable"},
        )


def get_rfc_runtime_status() -> RFCRuntimeStatus:
    """Return whether the RFC runtime is available, without raising."""
    _attempt_import()
    if _pyrfc_module is not None:
        return RFCRuntimeStatus(available=True, detail="PyRFC runtime loaded.")
    return RFCRuntimeStatus(
        available=False,
        detail=_import_error or "PyRFC runtime unavailable.",
    )


def get_pyrfc_module():
    """Return the imported pyrfc module, or None if unavailable.

    Callers must check get_rfc_runtime_status() first and raise a controlled
    SAPRFCRuntimeUnavailableError rather than letting an AttributeError
    propagate from a None module.
    """
    _attempt_import()
    return _pyrfc_module
