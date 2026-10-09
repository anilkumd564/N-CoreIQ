"""Phase 5 — Repository Export Service.

Serialises a BulkExtractionResult into JSON or ZIP format.

Architecture rule:
    SAP  →  BulkExtractionService  →  BulkExtractionResult  →  RepositoryExportService

The export service NEVER queries SAP.  It operates exclusively on the
already-completed BulkExtractionResult.

Security:
  - All archive entry paths are constructed internally.
  - User-supplied object/component names are sanitised before use as paths.
  - Every ZIP entry is verified to remain under the archive root.
  - No credentials, passwords, or Authorization headers are written to exports.

Temporary files (when used) are cleaned up after the response is sent.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import re
import zipfile
from datetime import datetime, timezone
from typing import Any

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.models.phase5 import (
    PHASE5_SCHEMA_VERSION,
    BulkExtractionResult,
    ExportFormat,
    ObjectExtractionStatus,
)

logger = logging.getLogger("app.services.export")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

APP_NAME = "N-CoreIQ"

# Characters that are unsafe in filesystem paths / ZIP entry names.
# We allow only alphanumeric, underscore, hyphen, and dot.
_UNSAFE_CHARS = re.compile(r"[^A-Za-z0-9_.\-]+")

# Maximum individual path component length (filesystem safety).
_MAX_NAME_LEN = 64

# ABAP-like source component types that should get .abap extension.
_ABAP_COMPONENT_TYPES: frozenset[str] = frozenset(
    {
        "PROGRAM",
        "CLASS",
        "CLASS_POOL",
        "INCLUDE",
        "METHOD",
        "INTERFACE",
        "FUNCTION",
        "FUNCTION_POOL",
        "LOCAL_CLASS",
        "TEST_CLASS",
        "FORM",
    }
)

# Archive root directory name.
_ARCHIVE_ROOT = "ncoreiq-export"


# ---------------------------------------------------------------------------
# RepositoryExportService
# ---------------------------------------------------------------------------


class RepositoryExportService:
    """Serialise a BulkExtractionResult into JSON or ZIP bytes.

    Methods:
        to_json_bytes  – produce a UTF-8 JSON document
        to_zip_bytes   – produce an in-memory ZIP archive
        export_filename – generate a meaningful download filename
    """

    def __init__(self, settings: Settings) -> None:
        self._settings = settings

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def export_filename(
        self,
        result: BulkExtractionResult,
        fmt: ExportFormat,
    ) -> str:
        """Return a deterministic, safe download filename.

        Format: ncoreiq_<system>_<client>_<timestamp>.<ext>
        """
        system = self._safe_component(result.system_id or "UNKNOWN")
        client = self._safe_component(result.client or "000")
        ts = result.started_at.strftime("%Y%m%d_%H%M%S")
        ext = "zip" if fmt == ExportFormat.ZIP else "json"
        return f"ncoreiq_{system}_{client}_{ts}.{ext}"

    def to_json_bytes(self, result: BulkExtractionResult) -> bytes:
        """Serialise BulkExtractionResult to a UTF-8 JSON document.

        Returns:
            UTF-8 encoded bytes of the JSON export.
        """
        doc = self._build_json_document(result)
        return json.dumps(doc, ensure_ascii=False, indent=2, default=str).encode("utf-8")

    def to_zip_bytes(self, result: BulkExtractionResult) -> bytes:
        """Produce an in-memory ZIP archive of the extraction result.

        ZIP structure::

            ncoreiq-export/
            ├── manifest.json
            └── objects/
                ├── PROG/
                │   └── ZREPORT1/
                │       ├── metadata.json
                │       ├── source.json
                │       └── source/
                │           └── PROGRAM__ZREPORT1.abap
                └── CLAS/
                    └── ZCL_SAMPLE/
                        ├── metadata.json
                        ├── source.json
                        └── source/
                            ├── CLASS_POOL__ZCL_SAMPLE.abap
                            └── METHOD__ZCL_SAMPLE_DO_SOMETHING.abap

        Returns:
            In-memory ZIP bytes.
        """
        buf = io.BytesIO()

        # Collect all object entries first so we can compute hashes before
        # writing the manifest (avoids duplicate manifest.json entry).
        manifest, hashes = self._build_manifest(result)
        entries: list[tuple[str, bytes]] = []

        for obj in sorted(result.objects, key=lambda o: (o.object_type, o.object_name)):
            obj_base = self._object_base_path(obj.object_type, obj.object_name)

            if obj.status == ObjectExtractionStatus.FAILED:
                error_doc = {
                    "object_type": obj.object_type,
                    "object_name": obj.object_name,
                    "status": obj.status.value,
                    "errors": [e.model_dump() for e in obj.errors],
                    "warnings": obj.warnings,
                }
                entries.append((
                    self._safe_zip_path(f"{obj_base}/error.json"),
                    json.dumps(error_doc, ensure_ascii=False, indent=2, default=str).encode("utf-8"),
                ))
                continue

            if obj.metadata is not None:
                entries.append((
                    self._safe_zip_path(f"{obj_base}/metadata.json"),
                    json.dumps(
                        obj.metadata.model_dump(mode="json"),
                        ensure_ascii=False, indent=2, default=str,
                    ).encode("utf-8"),
                ))

            if obj.source is not None:
                entries.append((
                    self._safe_zip_path(f"{obj_base}/source.json"),
                    json.dumps(
                        obj.source.model_dump(mode="json"),
                        ensure_ascii=False, indent=2, default=str,
                    ).encode("utf-8"),
                ))

                for component in sorted(
                    obj.source.components,
                    key=lambda c: (c.component_type, c.component_name),
                ):
                    ext = self._component_extension(component.component_type)
                    safe_ctype = self._safe_component(component.component_type)
                    safe_cname = self._safe_component(component.component_name)
                    filename = f"{safe_ctype}__{safe_cname}{ext}"
                    entry_path = self._safe_zip_path(f"{obj_base}/source/{filename}")
                    component_text = component.source_text or self._join_lines(component)
                    component_bytes = component_text.encode("utf-8")
                    entries.append((entry_path, component_bytes))
                    hashes[entry_path] = hashlib.sha256(component_bytes).hexdigest()

        # Finalise manifest with hashes and write everything once
        manifest["file_hashes"] = hashes

        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                self._safe_zip_path(f"{_ARCHIVE_ROOT}/manifest.json"),
                json.dumps(manifest, ensure_ascii=False, indent=2, default=str),
            )
            for entry_path, entry_bytes in entries:
                zf.writestr(entry_path, entry_bytes)

        buf.seek(0)
        raw = buf.getvalue()

        logger.info(
            "bulk_export_zip_complete",
            extra={
                "operation": "bulk_export",
                "extraction_id": result.extraction_id,
                "format": "zip",
                "zip_size_bytes": len(raw),
                "object_count": len(result.objects),
            },
        )

        return raw

    # ------------------------------------------------------------------
    # JSON document builder
    # ------------------------------------------------------------------

    def _build_json_document(self, result: BulkExtractionResult) -> dict[str, Any]:
        """Build the full JSON export document dict."""
        objects_list = []
        for obj in sorted(result.objects, key=lambda o: (o.object_type, o.object_name)):
            obj_doc: dict[str, Any] = {
                "object_type": obj.object_type,
                "object_name": obj.object_name,
                "status": obj.status.value,
                "duration_ms": obj.duration_ms,
                "warnings": obj.warnings,
                "errors": [e.model_dump() for e in obj.errors],
            }
            if obj.metadata is not None:
                obj_doc["metadata"] = obj.metadata.model_dump(mode="json")
            if obj.source is not None:
                obj_doc["source"] = obj.source.model_dump(mode="json")
            objects_list.append(obj_doc)

        doc: dict[str, Any] = {
            "schema_version": PHASE5_SCHEMA_VERSION,
            "application_name": APP_NAME,
            "application_version": self._settings.app_version,
            "extraction_id": result.extraction_id,
            "started_at": result.started_at.isoformat(),
            "completed_at": result.completed_at.isoformat(),
            "duration_ms": result.duration_ms,
            "status": result.status.value,
            "system_context": {
                "system_id": result.system_id,
                "client": result.client,
            },
            "selection": {
                "method": result.selection,
            },
            "extraction_options": {
                "metadata_enabled": result.metadata_enabled,
                "source_enabled": result.source_enabled,
                "source_max_lines": result.source_max_lines,
            },
            "statistics": {
                "requested_object_count": result.requested_object_count,
                "processed_object_count": result.processed_object_count,
                "successful_object_count": result.successful_object_count,
                "partial_object_count": result.partial_object_count,
                "failed_object_count": result.failed_object_count,
                "skipped_object_count": result.skipped_object_count,
                "truncated_object_count": result.truncated_object_count,
            },
            "warnings": result.warnings,
            "objects": objects_list,
        }

        logger.info(
            "bulk_export_json_complete",
            extra={
                "operation": "bulk_export",
                "extraction_id": result.extraction_id,
                "format": "json",
                "object_count": len(result.objects),
            },
        )

        return doc

    # ------------------------------------------------------------------
    # Manifest builder
    # ------------------------------------------------------------------

    def _build_manifest(
        self, result: BulkExtractionResult
    ) -> tuple[dict[str, Any], dict[str, str]]:
        """Build the manifest.json content and an initial (empty) hashes dict."""
        object_index = []
        for obj in sorted(result.objects, key=lambda o: (o.object_type, o.object_name)):
            object_index.append(
                {
                    "object_type": obj.object_type,
                    "object_name": obj.object_name,
                    "status": obj.status.value,
                    "has_metadata": obj.metadata is not None,
                    "has_source": obj.source is not None,
                    "source_truncated": (
                        obj.source.truncated if obj.source is not None else False
                    ),
                    "component_count": (
                        obj.source.component_count if obj.source is not None else 0
                    ),
                    "warnings": obj.warnings,
                }
            )

        manifest: dict[str, Any] = {
            "schema_version": PHASE5_SCHEMA_VERSION,
            "application_name": APP_NAME,
            "application_version": self._settings.app_version,
            "extraction_id": result.extraction_id,
            "created_at": datetime.now(tz=timezone.utc).isoformat(),
            "system_context": {
                "system_id": result.system_id,
                "client": result.client,
            },
            "selection": {
                "method": result.selection,
            },
            "extraction_options": {
                "metadata_included": result.metadata_enabled,
                "source_included": result.source_enabled,
                "source_max_lines": result.source_max_lines,
            },
            "statistics": {
                "total_requested_objects": result.requested_object_count,
                "successful_objects": result.successful_object_count,
                "partial_objects": result.partial_object_count,
                "failed_objects": result.failed_object_count,
                "skipped_objects": result.skipped_object_count,
                "truncated_objects": result.truncated_object_count,
            },
            "warnings": result.warnings,
            "object_index": object_index,
            # Populated after component files are written
            "file_hashes": {},
        }

        return manifest, {}

    # ------------------------------------------------------------------
    # Path / filename utilities
    # ------------------------------------------------------------------

    @staticmethod
    def _safe_component(value: str) -> str:
        """Sanitise a single path component (object type, name, etc.).

        - Removes any character that is not alphanumeric, underscore, hyphen, or dot.
        - Eliminates any remaining ``..`` sequences (path traversal prevention).
        - Truncates to _MAX_NAME_LEN characters.
        - Falls back to 'object' if the result is empty.
        - Strips leading/trailing dots (hidden files / Windows reserved names).
        """
        safe = _UNSAFE_CHARS.sub("_", value).strip(".")
        # Collapse any remaining consecutive dots (e.g. "_.._" from "../")
        while ".." in safe:
            safe = safe.replace("..", "_")
        safe = safe.strip(".")
        safe = safe[:_MAX_NAME_LEN]
        return safe or "object"

    @classmethod
    def _object_base_path(cls, object_type: str, object_name: str) -> str:
        """Return the base archive path for an object (without trailing slash)."""
        safe_type = cls._safe_component(object_type)
        safe_name = cls._safe_component(object_name)
        return f"{_ARCHIVE_ROOT}/objects/{safe_type}/{safe_name}"

    @staticmethod
    def _safe_zip_path(raw_path: str) -> str:
        """Verify and return a safe ZIP entry path.

        Raises:
            ValueError: If the normalised path escapes the archive root.
        """
        # Normalise slashes and collapse '..' sequences
        normalised = raw_path.replace("\\", "/")
        parts = []
        for part in normalised.split("/"):
            if part in ("", "."):
                continue
            if part == "..":
                raise ValueError(
                    f"ZIP path traversal detected in entry: {raw_path!r}"
                )
            parts.append(part)

        safe = "/".join(parts)

        # The first component must be our archive root
        if not safe.startswith(_ARCHIVE_ROOT):
            raise ValueError(
                f"ZIP entry escapes archive root: {raw_path!r}"
            )

        return safe

    @staticmethod
    def _component_extension(component_type: str) -> str:
        """Return .abap for ABAP-like components, .txt for structured definitions."""
        ctype_upper = component_type.upper()
        for abap_type in _ABAP_COMPONENT_TYPES:
            if abap_type in ctype_upper:
                return ".abap"
        return ".txt"

    @staticmethod
    def _join_lines(component: "RepositorySourceComponent") -> str:  # type: ignore[name-defined]
        """Join source lines into a single string preserving order."""
        return "\n".join(
            line.source_line
            for line in sorted(component.lines, key=lambda l: l.line_no)
        )


def get_export_service(
    settings: Settings = Depends(get_settings),
) -> RepositoryExportService:
    """FastAPI dependency factory for :class:`RepositoryExportService`.

    ``settings`` is injected by FastAPI via ``Depends(get_settings)`` so this
    function never appears as a naked Pydantic body-parameter when used inside
    a route via ``Depends(get_export_service)``.
    """
    return RepositoryExportService(settings=settings)
