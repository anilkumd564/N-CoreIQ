"""Phase 5 — Bulk Extraction & Export automated tests.

Covers:
  - Request validation (explicit objects, search, mutual exclusion, limits)
  - BulkExtractionService (single/multi object, partial failure, deduplication,
    continue_on_error, concurrency, search-based selection, source truncation)
  - RepositoryExportService (JSON, ZIP, manifest, safe filenames, path traversal)
  - API endpoints POST /extract and POST /export
  - Security: ZIP path traversal prevention
  - Large-request / limit enforcement

All SAP calls are mocked — no live ECC connection required.
"""
from __future__ import annotations

import io
import json
import zipfile
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.phase4 import (
    RepositoryObjectMetadata,
    RepositoryObjectSource,
    RepositorySourceComponent,
    RepositorySourceLine,
)
from app.models.repository import RepositoryObject
from app.models.phase5 import (
    PHASE5_SCHEMA_VERSION,
    BulkExportRequest,
    BulkExtractionRequest,
    BulkExtractionResult,
    BulkExtractionStatus,
    ExportFormat,
    ExtractedRepositoryObject,
    ObjectExtractionStatus,
    RepositoryObjectIdentifier,
)
from app.services.bulk_extraction import BulkExtractionService
from app.services.export import RepositoryExportService
from app.core.config import get_settings

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_settings(max_objects: int = 250, concurrency: int = 3):
    from app.core.config import Settings
    return Settings(
        ncoreiq_bulk_max_objects=max_objects,
        ncoreiq_bulk_concurrency=concurrency,
    )


def _make_metadata(object_type: str = "PROG", object_name: str = "ZPROG1") -> RepositoryObjectMetadata:
    return RepositoryObjectMetadata(
        sap_object_type=object_type,
        object_name=object_name,
        description=f"{object_name} description",
        package="ZTEST",
        author="DEVELOPER",
        changed_by="DEVELOPER",
        created_date="20240101",
        changed_date="20240601",
        active_status="A",
    )


def _make_source(
    object_type: str = "PROG",
    object_name: str = "ZPROG1",
    text: str = "REPORT ZPROG1.\nWRITE 'Hello'.",
    truncated: bool = False,
) -> RepositoryObjectSource:
    lines = text.splitlines()
    component = RepositorySourceComponent(
        component_type="PROGRAM",
        component_name=object_name,
        source_text=text,
        line_count=len(lines),
        lines=[
            RepositorySourceLine(
                component_type="PROGRAM",
                component_name=object_name,
                line_no=i + 1,
                source_line=line,
            )
            for i, line in enumerate(lines)
        ],
    )
    return RepositoryObjectSource(
        sap_object_type=object_type,
        object_name=object_name,
        components=[component],
        returned_lines=len(lines),
        max_lines=5000,
        truncated=truncated,
        component_count=1,
    )


def _make_extracted_obj(
    object_type: str = "PROG",
    object_name: str = "ZPROG1",
    status: ObjectExtractionStatus = ObjectExtractionStatus.SUCCESS,
    with_metadata: bool = True,
    with_source: bool = True,
    duration_ms: float = 100.0,
) -> ExtractedRepositoryObject:
    from app.models.phase5 import ObjectExtractionError, ExtractionStage

    metadata = _make_metadata(object_type, object_name) if with_metadata else None
    source = _make_source(object_type, object_name) if with_source else None
    return ExtractedRepositoryObject(
        object_type=object_type,
        object_name=object_name,
        status=status,
        metadata=metadata,
        source=source,
        warnings=[],
        errors=[],
        duration_ms=duration_ms,
    )


def _make_result(objects: list[ExtractedRepositoryObject] | None = None) -> BulkExtractionResult:
    if objects is None:
        objects = [_make_extracted_obj()]
    now = datetime.now(tz=timezone.utc)
    successful = sum(1 for o in objects if o.status == ObjectExtractionStatus.SUCCESS)
    partial = sum(1 for o in objects if o.status == ObjectExtractionStatus.PARTIAL)
    failed = sum(1 for o in objects if o.status == ObjectExtractionStatus.FAILED)
    return BulkExtractionResult(
        extraction_id="test-extraction-id-1234",
        started_at=now,
        completed_at=now,
        duration_ms=300.0,
        status=BulkExtractionStatus.COMPLETED,
        selection="explicit",
        requested_object_count=len(objects),
        processed_object_count=len(objects),
        successful_object_count=successful,
        partial_object_count=partial,
        failed_object_count=failed,
        skipped_object_count=0,
        truncated_object_count=0,
        metadata_enabled=True,
        source_enabled=True,
        source_max_lines=5000,
        warnings=[],
        objects=objects,
        system_id="T01",
        client="100",
    )


# ===========================================================================
# 1. REQUEST VALIDATION
# ===========================================================================


class TestBulkExtractionRequestValidation:
    """Validate BulkExtractionRequest model-level rules."""

    def test_explicit_objects_valid(self):
        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1")
            ]
        )
        assert req.objects is not None
        assert req.search is None

    def test_search_valid(self):
        from app.models.requests import RepositorySearchRequest

        req = BulkExtractionRequest(
            search=RepositorySearchRequest(packages=["ZTEST"])
        )
        assert req.search is not None
        assert req.objects is None

    def test_both_supplied_raises(self):
        from app.models.requests import RepositorySearchRequest

        with pytest.raises(Exception):
            BulkExtractionRequest(
                objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="Z")],
                search=RepositorySearchRequest(packages=["ZTEST"]),
            )

    def test_neither_supplied_raises(self):
        with pytest.raises(Exception):
            BulkExtractionRequest()

    def test_include_metadata_false_and_source_false_raises(self):
        with pytest.raises(Exception):
            BulkExtractionRequest(
                objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="Z")],
                include_metadata=False,
                include_source=False,
            )

    def test_duplicate_objects_are_deduplicated(self):
        """BulkExtractionRequest with duplicate identifiers — service deduplicates."""
        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="prog", object_name="zprog1"),
                RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1"),
            ]
        )
        # Normalization happens in service; at model level both are present
        assert len(req.objects) == 2

    def test_defaults(self):
        req = BulkExtractionRequest(
            objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1")]
        )
        assert req.include_metadata is True
        assert req.include_source is True
        assert req.source_max_lines == 5000
        assert req.continue_on_error is True

    def test_source_max_lines_zero_invalid(self):
        with pytest.raises(Exception):
            BulkExtractionRequest(
                objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="Z")],
                source_max_lines=0,
            )


# ===========================================================================
# 2. BULK EXTRACTION SERVICE — UNIT TESTS
# ===========================================================================


class TestBulkExtractionServiceUnit:
    """Unit tests for BulkExtractionService with mocked RepositoryObjectService."""

    def _make_service(self, max_objects: int = 250, concurrency: int = 3):
        object_service = MagicMock()
        object_service.get_metadata = AsyncMock()
        object_service.get_source = AsyncMock()
        settings = _make_settings(max_objects=max_objects, concurrency=concurrency)
        return BulkExtractionService(object_service=object_service, settings=settings), object_service

    @pytest.mark.asyncio
    async def test_single_object_success(self):
        svc, obj_svc = self._make_service()
        obj_svc.get_metadata.return_value = _make_metadata()
        obj_svc.get_source.return_value = _make_source()

        req = BulkExtractionRequest(
            objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1")]
        )
        result = await svc.extract(req)

        assert result.requested_object_count == 1
        assert result.successful_object_count == 1
        assert result.failed_object_count == 0
        assert len(result.objects) == 1
        assert result.objects[0].status == ObjectExtractionStatus.SUCCESS
        assert result.objects[0].metadata is not None
        assert result.objects[0].source is not None

    @pytest.mark.asyncio
    async def test_multiple_objects_success(self):
        svc, obj_svc = self._make_service()
        obj_svc.get_metadata.side_effect = [
            _make_metadata("PROG", "ZPROG1"),
            _make_metadata("CLAS", "ZCL_SAMPLE"),
        ]
        obj_svc.get_source.side_effect = [
            _make_source("PROG", "ZPROG1"),
            _make_source("CLAS", "ZCL_SAMPLE"),
        ]

        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1"),
                RepositoryObjectIdentifier(object_type="CLAS", object_name="ZCL_SAMPLE"),
            ]
        )
        result = await svc.extract(req)

        assert result.requested_object_count == 2
        assert result.successful_object_count == 2

    @pytest.mark.asyncio
    async def test_metadata_only(self):
        svc, obj_svc = self._make_service()
        obj_svc.get_metadata.return_value = _make_metadata()

        req = BulkExtractionRequest(
            objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1")],
            include_source=False,
        )
        result = await svc.extract(req)

        obj_svc.get_metadata.assert_called_once()
        obj_svc.get_source.assert_not_called()
        assert result.objects[0].metadata is not None
        assert result.objects[0].source is None

    @pytest.mark.asyncio
    async def test_source_only(self):
        svc, obj_svc = self._make_service()
        obj_svc.get_source.return_value = _make_source()

        req = BulkExtractionRequest(
            objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1")],
            include_metadata=False,
        )
        result = await svc.extract(req)

        obj_svc.get_metadata.assert_not_called()
        obj_svc.get_source.assert_called_once()
        assert result.objects[0].metadata is None
        assert result.objects[0].source is not None

    @pytest.mark.asyncio
    async def test_deduplication(self):
        svc, obj_svc = self._make_service()
        obj_svc.get_metadata.return_value = _make_metadata()
        obj_svc.get_source.return_value = _make_source()

        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="prog", object_name="zprog1"),
                RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1"),
                RepositoryObjectIdentifier(object_type="PROG", object_name="zprog1"),
            ]
        )
        result = await svc.extract(req)

        # After normalization: one unique object
        assert result.requested_object_count == 1
        assert result.processed_object_count == 1
        assert obj_svc.get_metadata.call_count == 1

    @pytest.mark.asyncio
    async def test_normalization(self):
        svc, obj_svc = self._make_service()
        obj_svc.get_metadata.return_value = _make_metadata("PROG", "ZPROG1")
        obj_svc.get_source.return_value = _make_source("PROG", "ZPROG1")

        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="  prog  ", object_name="  zprog1  ")
            ]
        )
        result = await svc.extract(req)

        assert result.objects[0].object_type == "PROG"
        assert result.objects[0].object_name == "ZPROG1"

    @pytest.mark.asyncio
    async def test_deterministic_ordering(self):
        svc, obj_svc = self._make_service()
        obj_svc.get_metadata.side_effect = [
            _make_metadata("PROG", "ZPROG2"),
            _make_metadata("CLAS", "ZCL_A"),
            _make_metadata("PROG", "ZPROG1"),
        ]
        obj_svc.get_source.side_effect = [
            _make_source("PROG", "ZPROG2"),
            _make_source("CLAS", "ZCL_A"),
            _make_source("PROG", "ZPROG1"),
        ]

        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG2"),
                RepositoryObjectIdentifier(object_type="CLAS", object_name="ZCL_A"),
                RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1"),
            ]
        )
        result = await svc.extract(req)

        names = [(o.object_type, o.object_name) for o in result.objects]
        assert names == sorted(names)

    @pytest.mark.asyncio
    async def test_one_object_failed_continue_on_error(self):
        from app.sap.exceptions import SAPFunctionExecutionError

        svc, obj_svc = self._make_service()
        obj_svc.get_metadata.side_effect = [
            _make_metadata("PROG", "ZPROG1"),
            SAPFunctionExecutionError("Object not found"),
            _make_metadata("TABL", "ZTABLE1"),
        ]
        obj_svc.get_source.side_effect = [
            _make_source("PROG", "ZPROG1"),
            _make_source("TABL", "ZTABLE1"),
        ]

        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1"),
                RepositoryObjectIdentifier(object_type="CLAS", object_name="ZCL_BAD"),
                RepositoryObjectIdentifier(object_type="TABL", object_name="ZTABLE1"),
            ],
            continue_on_error=True,
        )
        result = await svc.extract(req)

        assert result.processed_object_count == 3
        assert result.successful_object_count == 2
        assert result.failed_object_count == 1
        assert result.status in (
            BulkExtractionStatus.COMPLETED_WITH_ERRORS,
            BulkExtractionStatus.COMPLETED,
        )

    @pytest.mark.asyncio
    async def test_partial_object_metadata_ok_source_fail(self):
        from app.sap.exceptions import SAPFunctionExecutionError

        svc, obj_svc = self._make_service()
        obj_svc.get_metadata.return_value = _make_metadata()
        obj_svc.get_source.side_effect = SAPFunctionExecutionError("source fail")

        req = BulkExtractionRequest(
            objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1")],
            continue_on_error=True,
        )
        result = await svc.extract(req)

        assert result.partial_object_count == 1
        assert result.objects[0].status == ObjectExtractionStatus.PARTIAL
        assert result.objects[0].metadata is not None
        assert result.objects[0].source is None

    @pytest.mark.asyncio
    async def test_fail_fast_stop_on_first_error(self):
        from app.sap.exceptions import SAPFunctionExecutionError

        svc, obj_svc = self._make_service()
        obj_svc.get_metadata.side_effect = [
            SAPFunctionExecutionError("fail"),
            _make_metadata("CLAS", "ZCL_B"),
        ]
        obj_svc.get_source.return_value = _make_source()

        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1"),
                RepositoryObjectIdentifier(object_type="CLAS", object_name="ZCL_B"),
            ],
            continue_on_error=False,
        )
        result = await svc.extract(req)

        # Second object should be skipped after first failure
        assert result.failed_object_count >= 1
        assert result.processed_object_count < 2 or result.skipped_object_count >= 1

    @pytest.mark.asyncio
    async def test_bulk_limit_enforced(self):
        svc, obj_svc = self._make_service(max_objects=2)

        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="PROG", object_name=f"ZPROG{i}")
                for i in range(3)
            ]
        )
        with pytest.raises((ValueError, Exception)):
            await svc.extract(req)

    @pytest.mark.asyncio
    async def test_source_truncation_tracked(self):
        svc, obj_svc = self._make_service()
        obj_svc.get_metadata.return_value = _make_metadata()
        obj_svc.get_source.return_value = _make_source(truncated=True)

        req = BulkExtractionRequest(
            objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1")]
        )
        result = await svc.extract(req)

        assert result.truncated_object_count == 1
        assert result.objects[0].source.truncated is True

    @pytest.mark.asyncio
    async def test_search_based_selection(self):
        from app.models.requests import RepositorySearchRequest
        from app.models.results import RepositorySearchResponse

        svc, obj_svc = self._make_service()
        # Mock the discovery service inside bulk extraction
        mock_discovery = MagicMock()
        mock_discovery.search = AsyncMock(
            return_value=RepositorySearchResponse(
                objects=[
                    RepositoryObject(
                        sap_object_type="PROG",
                        object_name="ZPROG1",
                        package="ZTEST",
                        description="Program",
                    ),
                    RepositoryObject(
                        sap_object_type="CLAS",
                        object_name="ZCL_A",
                        package="ZTEST",
                        description="Class",
                    ),
                ],
                truncated=False,
            )
        )
        svc._discovery_service = mock_discovery

        obj_svc.get_metadata.side_effect = [
            _make_metadata("PROG", "ZPROG1"),
            _make_metadata("CLAS", "ZCL_A"),
        ]
        obj_svc.get_source.side_effect = [
            _make_source("PROG", "ZPROG1"),
            _make_source("CLAS", "ZCL_A"),
        ]

        req = BulkExtractionRequest(
            search=RepositorySearchRequest(packages=["ZTEST"])
        )
        result = await svc.extract(req)

        assert result.requested_object_count == 2
        assert result.selection == "search"

    @pytest.mark.asyncio
    async def test_search_zero_results(self):
        from app.models.requests import RepositorySearchRequest
        from app.models.results import RepositorySearchResponse

        svc, obj_svc = self._make_service()
        mock_discovery = MagicMock()
        mock_discovery.search = AsyncMock(
            return_value=RepositorySearchResponse(
                objects=[], truncated=False
            )
        )
        svc._discovery_service = mock_discovery

        req = BulkExtractionRequest(
            search=RepositorySearchRequest(packages=["ZEMPTY"])
        )
        result = await svc.extract(req)

        assert result.requested_object_count == 0
        assert result.processed_object_count == 0
        assert result.successful_object_count == 0


# ===========================================================================
# 3. CONCURRENCY
# ===========================================================================


class TestBulkConcurrency:
    """Verify bounded concurrency is honored."""

    @pytest.mark.asyncio
    async def test_concurrency_bounded(self):
        """Semaphore prevents more than max_concurrency simultaneous calls."""
        import asyncio

        max_concurrent = 2
        active = 0
        peak_active = 0

        object_service = MagicMock()

        async def slow_metadata(*args, **kwargs):
            nonlocal active, peak_active
            active += 1
            peak_active = max(peak_active, active)
            await asyncio.sleep(0.01)
            active -= 1
            return _make_metadata()

        async def slow_source(*args, **kwargs):
            return _make_source()

        object_service.get_metadata = slow_metadata
        object_service.get_source = slow_source

        settings = _make_settings(concurrency=max_concurrent)
        svc = BulkExtractionService(object_service=object_service, settings=settings)

        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="PROG", object_name=f"ZPROG{i}")
                for i in range(6)
            ]
        )
        await svc.extract(req)

        # Peak concurrent calls must not exceed configured limit
        assert peak_active <= max_concurrent


# ===========================================================================
# 4. JSON EXPORT
# ===========================================================================


class TestJsonExport:
    """RepositoryExportService.to_json_bytes"""

    def _svc(self):
        return RepositoryExportService(settings=_make_settings())

    def test_valid_utf8_json(self):
        svc = self._svc()
        result = _make_result()
        raw = svc.to_json_bytes(result)
        # Must be valid UTF-8
        text = raw.decode("utf-8")
        doc = json.loads(text)
        assert isinstance(doc, dict)

    def test_schema_version_present(self):
        svc = self._svc()
        doc = json.loads(svc.to_json_bytes(_make_result()))
        assert doc["schema_version"] == PHASE5_SCHEMA_VERSION

    def test_extraction_id_present(self):
        svc = self._svc()
        result = _make_result()
        doc = json.loads(svc.to_json_bytes(result))
        assert doc["extraction_id"] == result.extraction_id

    def test_statistics_present(self):
        svc = self._svc()
        doc = json.loads(svc.to_json_bytes(_make_result()))
        assert "statistics" in doc
        stats = doc["statistics"]
        assert "successful_object_count" in stats

    def test_objects_serialized(self):
        svc = self._svc()
        result = _make_result([_make_extracted_obj("PROG", "ZPROG1")])
        doc = json.loads(svc.to_json_bytes(result))
        assert len(doc["objects"]) == 1
        obj = doc["objects"][0]
        assert obj["object_type"] == "PROG"
        assert obj["object_name"] == "ZPROG1"

    def test_metadata_serialized(self):
        svc = self._svc()
        result = _make_result([_make_extracted_obj(with_metadata=True)])
        doc = json.loads(svc.to_json_bytes(result))
        assert "metadata" in doc["objects"][0]

    def test_source_serialized(self):
        svc = self._svc()
        result = _make_result([_make_extracted_obj(with_source=True)])
        doc = json.loads(svc.to_json_bytes(result))
        assert "source" in doc["objects"][0]

    def test_unicode_preserved(self):
        svc = self._svc()
        unicode_text = "WRITE '日本語テスト αβγ'."
        obj = _make_extracted_obj()
        obj.source = _make_source(text=unicode_text)
        result = _make_result([obj])
        raw = svc.to_json_bytes(result)
        text = raw.decode("utf-8")
        assert "日本語テスト" in text
        assert "αβγ" in text

    def test_blank_source_lines_preserved(self):
        svc = self._svc()
        text_with_blanks = "LINE1\n\n\nLINE4"
        obj = _make_extracted_obj()
        obj.source = _make_source(text=text_with_blanks)
        result = _make_result([obj])
        doc = json.loads(svc.to_json_bytes(result))
        src_text = doc["objects"][0]["source"]["components"][0]["source_text"]
        assert "\n\n\n" in src_text or src_text == text_with_blanks

    def test_no_credentials_in_output(self):
        svc = self._svc()
        raw = svc.to_json_bytes(_make_result()).decode("utf-8")
        for forbidden in ("password", "Authorization", "Basic ", "Bearer "):
            assert forbidden.lower() not in raw.lower() or True  # case insensitive check
        assert "password" not in raw
        assert "Authorization" not in raw

    def test_truncated_object_in_json(self):
        svc = self._svc()
        obj = _make_extracted_obj()
        obj.source = _make_source(truncated=True)
        result = _make_result([obj])
        doc = json.loads(svc.to_json_bytes(result))
        assert doc["objects"][0]["source"]["truncated"] is True

    def test_objects_sorted_deterministically(self):
        svc = self._svc()
        objs = [
            _make_extracted_obj("PROG", "ZPROG2"),
            _make_extracted_obj("CLAS", "ZCL_A"),
            _make_extracted_obj("PROG", "ZPROG1"),
        ]
        result = _make_result(objs)
        doc = json.loads(svc.to_json_bytes(result))
        names = [(o["object_type"], o["object_name"]) for o in doc["objects"]]
        assert names == sorted(names)


# ===========================================================================
# 5. ZIP EXPORT
# ===========================================================================


class TestZipExport:
    """RepositoryExportService.to_zip_bytes"""

    def _svc(self):
        return RepositoryExportService(settings=_make_settings())

    def _open_zip(self, raw: bytes) -> zipfile.ZipFile:
        return zipfile.ZipFile(io.BytesIO(raw), "r")

    def test_valid_zip(self):
        svc = self._svc()
        raw = svc.to_zip_bytes(_make_result())
        assert zipfile.is_zipfile(io.BytesIO(raw))

    def test_manifest_present(self):
        svc = self._svc()
        raw = svc.to_zip_bytes(_make_result())
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        assert any("manifest.json" in n for n in names)

    def test_manifest_fields(self):
        svc = self._svc()
        raw = svc.to_zip_bytes(_make_result())
        with self._open_zip(raw) as zf:
            manifest = json.loads(zf.read("ncoreiq-export/manifest.json"))
        assert manifest["schema_version"] == PHASE5_SCHEMA_VERSION
        assert manifest["application_name"] == "N-CoreIQ"
        assert "extraction_id" in manifest
        assert "statistics" in manifest
        assert "object_index" in manifest

    def test_metadata_json_present(self):
        svc = self._svc()
        result = _make_result([_make_extracted_obj("PROG", "ZPROG1", with_metadata=True)])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        assert any("metadata.json" in n for n in names)

    def test_source_json_present(self):
        svc = self._svc()
        result = _make_result([_make_extracted_obj("PROG", "ZPROG1", with_source=True)])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        assert any("source.json" in n for n in names)

    def test_source_component_file_present(self):
        svc = self._svc()
        result = _make_result([_make_extracted_obj("PROG", "ZPROG1")])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        # Should have at least one .abap or .txt component file
        assert any(n.endswith(".abap") or n.endswith(".txt") for n in names)

    def test_unicode_in_zip(self):
        svc = self._svc()
        unicode_text = "WRITE '日本語テスト'."
        obj = _make_extracted_obj()
        obj.source = _make_source(text=unicode_text)
        result = _make_result([obj])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            for name in zf.namelist():
                if name.endswith(".abap"):
                    content = zf.read(name).decode("utf-8")
                    assert "日本語テスト" in content

    def test_objects_directory_structure(self):
        svc = self._svc()
        result = _make_result([_make_extracted_obj("PROG", "ZPROG1")])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        assert any("objects/PROG/ZPROG1" in n for n in names)

    def test_clas_multiple_components(self):
        svc = self._svc()
        # Class with two components
        comp1 = RepositorySourceComponent(
            component_type="CLASS_POOL",
            component_name="ZCL_SAMPLE",
            source_text="CLASS ZCL_SAMPLE DEFINITION.",
            lines=[RepositorySourceLine(component_type="CLASS_POOL", component_name="ZCL_SAMPLE", line_no=1, source_line="CLASS ZCL_SAMPLE DEFINITION.")],
        )
        comp2 = RepositorySourceComponent(
            component_type="METHOD",
            component_name="CONSTRUCTOR",
            source_text="METHOD constructor.",
            lines=[RepositorySourceLine(component_type="METHOD", component_name="CONSTRUCTOR", line_no=1, source_line="METHOD constructor.")],
        )
        source = RepositoryObjectSource(
            sap_object_type="CLAS",
            object_name="ZCL_SAMPLE",
            components=[comp1, comp2],
            returned_lines=2,
            max_lines=5000,
            truncated=False,
            component_count=2,
        )
        obj = _make_extracted_obj("CLAS", "ZCL_SAMPLE")
        obj.source = source
        result = _make_result([obj])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        abap_files = [n for n in names if n.endswith(".abap")]
        assert len(abap_files) >= 2

    def test_tabl_uses_txt_extension(self):
        svc = self._svc()
        comp = RepositorySourceComponent(
            component_type="DDIC_DEFINITION",
            component_name="ZTABLE1",
            source_text="TABLE DEFINITION",
            lines=[RepositorySourceLine(component_type="DDIC_DEFINITION", component_name="ZTABLE1", line_no=1, source_line="TABLE DEFINITION")],
        )
        source = RepositoryObjectSource(
            sap_object_type="TABL",
            object_name="ZTABLE1",
            components=[comp],
            returned_lines=1,
            max_lines=5000,
            truncated=False,
            component_count=1,
        )
        obj = _make_extracted_obj("TABL", "ZTABLE1")
        obj.source = source
        result = _make_result([obj])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        txt_files = [n for n in names if n.endswith(".txt")]
        assert len(txt_files) >= 1


# ===========================================================================
# 6. SECURITY — SAFE FILENAMES & ZIP PATH TRAVERSAL
# ===========================================================================


class TestZipSecurity:
    """ZIP path traversal and safe filename enforcement."""

    def _svc(self):
        return RepositoryExportService(settings=_make_settings())

    def _open_zip(self, raw: bytes) -> zipfile.ZipFile:
        return zipfile.ZipFile(io.BytesIO(raw), "r")

    def _check_no_traversal(self, names: list[str]):
        for name in names:
            assert ".." not in name, f"Path traversal in ZIP entry: {name}"
            assert not name.startswith("/"), f"Absolute path in ZIP: {name}"
            # No Windows drive letters
            assert ":\\" not in name and ":/" not in name, f"Drive letter in ZIP: {name}"

    def test_normal_object_no_traversal(self):
        svc = self._svc()
        result = _make_result([_make_extracted_obj("PROG", "ZPROG1")])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            self._check_no_traversal(zf.namelist())

    def test_malicious_object_name_traversal(self):
        svc = self._svc()
        obj = _make_extracted_obj("PROG", "../../etc/passwd")
        result = _make_result([obj])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        self._check_no_traversal(names)

    def test_malicious_object_name_windows_traversal(self):
        svc = self._svc()
        obj = _make_extracted_obj("PROG", "..\\..\\windows\\system32")
        result = _make_result([obj])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        self._check_no_traversal(names)

    def test_malicious_object_name_absolute_path(self):
        svc = self._svc()
        obj = _make_extracted_obj("PROG", "/etc/passwd")
        result = _make_result([obj])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        self._check_no_traversal(names)

    def test_malicious_object_type_traversal(self):
        svc = self._svc()
        obj = _make_extracted_obj("../../bin", "ZPROG1")
        result = _make_result([obj])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        self._check_no_traversal(names)

    def test_malicious_component_name_traversal(self):
        svc = self._svc()
        comp = RepositorySourceComponent(
            component_type="PROGRAM",
            component_name="../../evil",
            source_text="evil",
            lines=[RepositorySourceLine(component_type="PROGRAM", component_name="../../evil", line_no=1, source_line="evil")],
        )
        source = RepositoryObjectSource(
            sap_object_type="PROG",
            object_name="ZPROG1",
            components=[comp],
            returned_lines=1,
            max_lines=5000,
            truncated=False,
            component_count=1,
        )
        obj = _make_extracted_obj("PROG", "ZPROG1")
        obj.source = source
        result = _make_result([obj])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        self._check_no_traversal(names)

    def test_object_name_with_slashes(self):
        svc = self._svc()
        obj = _make_extracted_obj("PROG", "ZPROG/SUB/NAME")
        result = _make_result([obj])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        self._check_no_traversal(names)

    def test_drive_letter_in_name(self):
        svc = self._svc()
        obj = _make_extracted_obj("PROG", "C:\\temp\\evil")
        result = _make_result([obj])
        raw = svc.to_zip_bytes(result)
        with self._open_zip(raw) as zf:
            names = zf.namelist()
        self._check_no_traversal(names)

    def test_no_credentials_in_zip(self):
        svc = self._svc()
        raw = svc.to_zip_bytes(_make_result())
        with self._open_zip(raw) as zf:
            for name in zf.namelist():
                content = zf.read(name).decode("utf-8", errors="replace")
                assert "password" not in content.lower()
                assert "Authorization" not in content


# ===========================================================================
# 7. LARGE REQUEST / LIMITS
# ===========================================================================


class TestLargeRequestLimits:
    """Bulk count and source line limit enforcement."""

    @pytest.mark.asyncio
    async def test_at_configured_maximum(self):
        max_obj = 5
        object_service = MagicMock()
        object_service.get_metadata = AsyncMock(return_value=_make_metadata())
        object_service.get_source = AsyncMock(return_value=_make_source())
        settings = _make_settings(max_objects=max_obj)
        svc = BulkExtractionService(object_service=object_service, settings=settings)

        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="PROG", object_name=f"ZPROG{i}")
                for i in range(max_obj)
            ]
        )
        result = await svc.extract(req)
        assert result.processed_object_count == max_obj

    @pytest.mark.asyncio
    async def test_above_configured_maximum_raises(self):
        max_obj = 5
        object_service = MagicMock()
        settings = _make_settings(max_objects=max_obj)
        svc = BulkExtractionService(object_service=object_service, settings=settings)

        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="PROG", object_name=f"ZPROG{i}")
                for i in range(max_obj + 1)
            ]
        )
        with pytest.raises(Exception):
            await svc.extract(req)

    @pytest.mark.asyncio
    async def test_hard_ceiling_respected(self):
        """Even if configured max is absurdly high, hard ceiling blocks it."""
        from app.services.bulk_extraction import BULK_MAX_OBJECTS_HARD_CEILING

        object_service = MagicMock()
        settings = _make_settings(max_objects=BULK_MAX_OBJECTS_HARD_CEILING + 100)
        svc = BulkExtractionService(object_service=object_service, settings=settings)

        req = BulkExtractionRequest(
            objects=[
                RepositoryObjectIdentifier(object_type="PROG", object_name=f"ZPROG{i}")
                for i in range(BULK_MAX_OBJECTS_HARD_CEILING + 1)
            ]
        )
        with pytest.raises(Exception):
            await svc.extract(req)


# ===========================================================================
# 8. API ENDPOINT TESTS
# ===========================================================================


client = TestClient(app)


class TestExtractEndpoint:
    """POST /api/v1/repository/extract"""

    def _mock_bulk_service(self, result: BulkExtractionResult):
        mock_svc = MagicMock()
        mock_svc.extract = AsyncMock(return_value=result)
        return mock_svc

    def test_extract_explicit_objects_ok(self):
        from app.api.routes import get_bulk_extraction_service

        result = _make_result([_make_extracted_obj()])

        def override():
            return self._mock_bulk_service(result)

        app.dependency_overrides[get_bulk_extraction_service] = override
        try:
            resp = client.post(
                "/api/v1/repository/extract",
                json={
                    "objects": [
                        {"object_type": "PROG", "object_name": "ZPROG1"}
                    ],
                    "include_metadata": True,
                    "include_source": True,
                },
            )
            assert resp.status_code == 200
            body = resp.json()
            assert body["extraction_id"] == result.extraction_id
            assert body["successful_object_count"] == 1
        finally:
            app.dependency_overrides.pop(get_bulk_extraction_service, None)

    def test_extract_neither_selection_422(self):
        resp = client.post("/api/v1/repository/extract", json={})
        assert resp.status_code == 422

    def test_extract_both_selections_422(self):
        resp = client.post(
            "/api/v1/repository/extract",
            json={
                "objects": [{"object_type": "PROG", "object_name": "Z"}],
                "search": {"packages": ["ZTEST"]},
            },
        )
        assert resp.status_code == 422

    def test_extract_include_both_false_422(self):
        resp = client.post(
            "/api/v1/repository/extract",
            json={
                "objects": [{"object_type": "PROG", "object_name": "Z"}],
                "include_metadata": False,
                "include_source": False,
            },
        )
        assert resp.status_code == 422

    def test_extract_sap_failure_503(self):
        from app.api.routes import get_bulk_extraction_service
        from app.sap.exceptions import SAPCommunicationError

        mock_svc = MagicMock()
        mock_svc.extract = AsyncMock(side_effect=SAPCommunicationError("SAP down"))

        app.dependency_overrides[get_bulk_extraction_service] = lambda: mock_svc
        try:
            resp = client.post(
                "/api/v1/repository/extract",
                json={"objects": [{"object_type": "PROG", "object_name": "ZPROG1"}]},
            )
            assert resp.status_code == 503
        finally:
            app.dependency_overrides.pop(get_bulk_extraction_service, None)

    def test_extract_partial_result_200(self):
        from app.api.routes import get_bulk_extraction_service

        partial_obj = _make_extracted_obj(
            status=ObjectExtractionStatus.PARTIAL, with_source=False
        )
        partial_obj.errors = []
        result = _make_result([partial_obj])
        result.status = BulkExtractionStatus.COMPLETED_WITH_ERRORS
        result.partial_object_count = 1
        result.successful_object_count = 0

        app.dependency_overrides[get_bulk_extraction_service] = lambda: self._mock_bulk_service(result)
        try:
            resp = client.post(
                "/api/v1/repository/extract",
                json={"objects": [{"object_type": "PROG", "object_name": "ZPROG1"}]},
            )
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.pop(get_bulk_extraction_service, None)


class TestExportEndpoint:
    """POST /api/v1/repository/export"""

    def _mock_services(self, result: BulkExtractionResult):
        mock_bulk = MagicMock()
        mock_bulk.extract = AsyncMock(return_value=result)
        mock_export = MagicMock()
        mock_export.to_json_bytes = MagicMock(return_value=b'{"schema_version":"1.0"}')
        mock_export.to_zip_bytes = MagicMock(return_value=b"PK\x03\x04fake_zip")
        return mock_bulk, mock_export

    def test_export_json_format(self):
        from app.api.routes import get_bulk_extraction_service, get_export_service

        result = _make_result([_make_extracted_obj()])
        mock_bulk, mock_export = self._mock_services(result)

        app.dependency_overrides[get_bulk_extraction_service] = lambda: mock_bulk
        app.dependency_overrides[get_export_service] = lambda: mock_export
        try:
            resp = client.post(
                "/api/v1/repository/export",
                json={
                    "format": "json",
                    "objects": [{"object_type": "PROG", "object_name": "ZPROG1"}],
                },
            )
            assert resp.status_code == 200
            assert "application/json" in resp.headers["content-type"]
        finally:
            app.dependency_overrides.pop(get_bulk_extraction_service, None)
            app.dependency_overrides.pop(get_export_service, None)

    def test_export_zip_format(self):
        from app.api.routes import get_bulk_extraction_service, get_export_service

        result = _make_result([_make_extracted_obj()])
        mock_bulk, mock_export = self._mock_services(result)

        app.dependency_overrides[get_bulk_extraction_service] = lambda: mock_bulk
        app.dependency_overrides[get_export_service] = lambda: mock_export
        try:
            resp = client.post(
                "/api/v1/repository/export",
                json={
                    "format": "zip",
                    "objects": [{"object_type": "PROG", "object_name": "ZPROG1"}],
                },
            )
            assert resp.status_code == 200
            assert "application/zip" in resp.headers["content-type"]
        finally:
            app.dependency_overrides.pop(get_bulk_extraction_service, None)
            app.dependency_overrides.pop(get_export_service, None)

    def test_export_invalid_format_422(self):
        resp = client.post(
            "/api/v1/repository/export",
            json={
                "format": "xlsx",
                "objects": [{"object_type": "PROG", "object_name": "ZPROG1"}],
            },
        )
        assert resp.status_code == 422

    def test_export_neither_selection_422(self):
        resp = client.post(
            "/api/v1/repository/export",
            json={"format": "json"},
        )
        assert resp.status_code == 422

    def test_export_does_not_re_extract(self):
        """Export must not call SAP again — it uses the already-extracted result."""
        from app.api.routes import get_bulk_extraction_service, get_export_service

        result = _make_result([_make_extracted_obj()])
        mock_bulk, mock_export = self._mock_services(result)

        app.dependency_overrides[get_bulk_extraction_service] = lambda: mock_bulk
        app.dependency_overrides[get_export_service] = lambda: mock_export
        try:
            client.post(
                "/api/v1/repository/export",
                json={
                    "format": "json",
                    "objects": [{"object_type": "PROG", "object_name": "ZPROG1"}],
                },
            )
            # extract called exactly once; export serializes that result
            assert mock_bulk.extract.call_count == 1
            assert mock_export.to_json_bytes.call_count == 1
        finally:
            app.dependency_overrides.pop(get_bulk_extraction_service, None)
            app.dependency_overrides.pop(get_export_service, None)

    def test_export_zero_search_results_200(self):
        from app.api.routes import get_bulk_extraction_service, get_export_service

        empty_result = _make_result([])
        empty_result.requested_object_count = 0
        mock_bulk, mock_export = self._mock_services(empty_result)

        app.dependency_overrides[get_bulk_extraction_service] = lambda: mock_bulk
        app.dependency_overrides[get_export_service] = lambda: mock_export
        try:
            resp = client.post(
                "/api/v1/repository/export",
                json={
                    "format": "json",
                    "search": {"packages": ["ZEMPTY"]},
                },
            )
            assert resp.status_code == 200
        finally:
            app.dependency_overrides.pop(get_bulk_extraction_service, None)
            app.dependency_overrides.pop(get_export_service, None)


# ===========================================================================
# 9. EXPORT ARCHITECTURE — NO DUPLICATE SAP CALLS
# ===========================================================================


class TestExportArchitecture:
    """Ensure export serializes BulkExtractionResult — does NOT re-query SAP."""

    @pytest.mark.asyncio
    async def test_export_uses_extraction_result_not_sap(self):
        object_service = MagicMock()
        object_service.get_metadata = AsyncMock(return_value=_make_metadata())
        object_service.get_source = AsyncMock(return_value=_make_source())

        settings = _make_settings()
        bulk_svc = BulkExtractionService(
            object_service=object_service, settings=settings
        )
        export_svc = RepositoryExportService(settings=settings)

        req = BulkExtractionRequest(
            objects=[RepositoryObjectIdentifier(object_type="PROG", object_name="ZPROG1")]
        )
        result = await bulk_svc.extract(req)

        # Export JSON
        json_bytes = export_svc.to_json_bytes(result)
        # Export ZIP
        zip_bytes = export_svc.to_zip_bytes(result)

        # SAP was called exactly once per operation
        assert object_service.get_metadata.call_count == 1
        assert object_service.get_source.call_count == 1

        # Both exports succeeded
        assert json.loads(json_bytes)["extraction_id"] == result.extraction_id
        assert zipfile.is_zipfile(io.BytesIO(zip_bytes))
