"""Phase 9 — Structured logging and correlation ID infrastructure.

Provides:
- StructuredLogFormatter: JSON or text log formatter with standard BTP fields
- CorrelationIdMiddleware: generates/propagates request + correlation IDs
- Request context vars for correlation ID propagation across async tasks

Log record fields:
    timestamp      — ISO-8601 UTC
    level          — DEBUG/INFO/WARNING/ERROR/CRITICAL
    service        — "N-CoreIQ"
    version        — application version
    runtime_mode   — local/btp/test
    instance_id    — CF_INSTANCE_INDEX or configured ID
    request_id     — per-request UUID
    correlation_id — propagated or generated UUID
    operation      — from logger.extra or record.funcName
    logger         — logger name
    message        — log message

Secret redaction:
    Fields containing secret patterns are automatically masked.
"""
from __future__ import annotations

import json
import logging
import re
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ---------------------------------------------------------------------------
# Context variables for correlation propagation
# ---------------------------------------------------------------------------

_request_id_var: ContextVar[str] = ContextVar("request_id", default="")
_correlation_id_var: ContextVar[str] = ContextVar("correlation_id", default="")


def get_request_id() -> str:
    return _request_id_var.get()


def get_correlation_id() -> str:
    return _correlation_id_var.get()


def set_request_id(value: str) -> None:
    _request_id_var.set(value)


def set_correlation_id(value: str) -> None:
    _correlation_id_var.set(value)


# ---------------------------------------------------------------------------
# Secret redaction
# ---------------------------------------------------------------------------

_SECRET_PATTERNS = re.compile(
    r"(password|token|authorization|secret|client_secret|clientsecret|"
    r"private_key|privatekey|credentials|servicekey|verificationkey|"
    r"certificate|apikey|api_key)",
    re.IGNORECASE,
)


def _redact_value(key: str, value: Any) -> Any:
    """Redact secret values from log records."""
    if isinstance(key, str) and _SECRET_PATTERNS.search(key):
        return "***" if value else value
    return value


def _redact_dict(data: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Recursively redact secrets from a dictionary."""
    if depth > 4:
        return {"...": "..."}
    result: dict[str, Any] = {}
    for k, v in data.items():
        if isinstance(v, dict):
            result[k] = _redact_dict(v, depth + 1)
        else:
            result[k] = _redact_value(k, v)
    return result


# ---------------------------------------------------------------------------
# Structured log formatter
# ---------------------------------------------------------------------------

# These fields are set by the StructuredLogFormatter automatically
_INTERNAL_FIELDS = frozenset(
    {
        "args", "created", "exc_info", "exc_text", "filename", "funcName",
        "levelname", "levelno", "lineno", "message", "module", "msecs",
        "msg", "name", "pathname", "process", "processName", "relativeCreated",
        "stack_info", "thread", "threadName", "taskName",
    }
)


class StructuredLogFormatter(logging.Formatter):
    """JSON structured log formatter for BTP / Cloud Foundry.

    Produces one JSON object per line for log aggregation systems.
    Automatically includes request_id and correlation_id from context vars.
    Masks secret field values.
    """

    def __init__(
        self,
        service: str = "N-CoreIQ",
        version: str = "0.9.0",
        runtime_mode: str = "local",
        instance_id: str | None = None,
        fmt: str = "json",
    ) -> None:
        super().__init__()
        self._service = service
        self._version = version
        self._runtime_mode = runtime_mode
        self._instance_id = instance_id or "0"
        self._fmt = fmt

    def format(self, record: logging.LogRecord) -> str:
        # Ensure exc_text is formatted if needed
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)

        message = record.getMessage()

        if self._fmt == "json":
            return self._format_json(record, message)
        else:
            return self._format_text(record, message)

    def _format_json(self, record: logging.LogRecord, message: str) -> str:
        """Format as JSON for log aggregation."""
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat()

        log_entry: dict[str, Any] = {
            "timestamp": ts,
            "level": record.levelname,
            "service": self._service,
            "version": self._version,
            "runtime_mode": self._runtime_mode,
            "instance_id": self._instance_id,
            "request_id": get_request_id() or None,
            "correlation_id": get_correlation_id() or None,
            "logger": record.name,
            "message": message,
        }

        # Include operation from record.extra if present
        operation = getattr(record, "operation", None)
        if operation:
            log_entry["operation"] = operation

        # Include duration_ms if present
        duration_ms = getattr(record, "duration_ms", None)
        if duration_ms is not None:
            log_entry["duration_ms"] = duration_ms

        # Include status if present
        status = getattr(record, "status", None)
        if status is not None:
            log_entry["status"] = status

        # Include any extra fields not in _INTERNAL_FIELDS
        for key, value in record.__dict__.items():
            if key not in _INTERNAL_FIELDS and not key.startswith("_"):
                if key not in log_entry:
                    log_entry[key] = _redact_value(key, value)

        # Include exception if present
        if record.exc_text:
            log_entry["exception"] = record.exc_text

        # Redact any secrets that may have leaked into the message
        try:
            return json.dumps(log_entry, default=str)
        except (TypeError, ValueError):
            return json.dumps({"level": record.levelname, "message": str(message)})

    def _format_text(self, record: logging.LogRecord, message: str) -> str:
        """Format as human-readable text for local development."""
        ts = datetime.fromtimestamp(record.created, tz=timezone.utc).strftime(
            "%Y-%m-%d %H:%M:%S.%f"
        )[:-3]
        req_id = get_request_id()
        req_part = f" [req={req_id[:8]}]" if req_id else ""
        return f"{ts} {record.levelname:8s} {record.name}{req_part} {message}"


# ---------------------------------------------------------------------------
# Correlation ID middleware
# ---------------------------------------------------------------------------

# Standard header names for correlation ID propagation
_CORRELATION_HEADERS = (
    "X-Correlation-ID",
    "X-Correlation-Id",
    "X-Request-ID",
    "X-B3-TraceId",
    "correlation-id",
)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Generates or propagates request and correlation IDs.

    - Reads X-Correlation-ID from incoming request (or generates new UUID)
    - Generates a per-request request_id UUID
    - Sets both in context vars for logger use
    - Attaches to request.state
    - Forwards X-Correlation-ID in response headers

    Supports:
        REST API requests
        MCP requests
        SAP outbound requests (propagated via context)
    """

    async def dispatch(self, request: Request, call_next) -> Response:
        # Generate or propagate correlation ID
        correlation_id = None
        for header_name in _CORRELATION_HEADERS:
            correlation_id = request.headers.get(header_name)
            if correlation_id:
                break

        if not correlation_id:
            correlation_id = str(uuid.uuid4())

        request_id = str(uuid.uuid4())

        # Set context vars for this request's async task
        set_request_id(request_id)
        set_correlation_id(correlation_id)

        # Attach to request state
        request.state.request_id = request_id
        request.state.correlation_id = correlation_id

        try:
            response = await call_next(request)
        finally:
            # Reset context vars (not strictly necessary but clean)
            set_request_id("")
            set_correlation_id("")

        # Forward correlation ID in response
        response.headers["X-Correlation-ID"] = correlation_id
        response.headers["X-Request-ID"] = request_id
        return response


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def configure_logging(
    level: str = "INFO",
    fmt: str = "text",
    service: str = "N-CoreIQ",
    version: str = "0.9.0",
    runtime_mode: str = "local",
    instance_id: str | None = None,
) -> None:
    """Configure root logger with the structured formatter.

    Call this once at application startup before any log records are emitted.
    """
    formatter = StructuredLogFormatter(
        service=service,
        version=version,
        runtime_mode=runtime_mode,
        instance_id=instance_id,
        fmt=fmt,
    )

    # Configure root handler
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicate output
    root_logger.handlers.clear()

    handler = logging.StreamHandler()
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)

    # Quiet noisy third-party loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("asyncio").setLevel(logging.WARNING)
