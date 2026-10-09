"""Phase 5 — Bulk Extraction Service.

Orchestrates the extraction of multiple SAP repository objects in a single
controlled synchronous operation.

Architecture:
    FastAPI route
      → BulkExtractionService (this module)
        ├── RepositoryDiscoveryService  (search-based selection only)
        └── RepositoryObjectService     (per-object metadata + source)

This service does NOT call httpx, ObjectMetadataSet, or SourceLinesSet
directly.  All SAP interaction is delegated to RepositoryObjectService.
Phase 3 search is delegated to RepositoryService (RepositoryDiscoveryService).

Phase 6 concerns (background jobs, persistence, queuing) are NOT here.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from datetime import datetime, timezone

from fastapi import Depends

from app.core.config import Settings, get_settings
from app.models.phase5 import (
    BULK_OBJECTS_HARD_CEILING,
    BulkExtractionRequest,
    BulkExtractionRequest as _BulkExtractionRequest,  # noqa: F401 keep alias
    BulkExtractionResult,
    BulkExtractionStatus,
    ExtractedRepositoryObject,
    ExtractionError,
    ExtractionStage,
    ObjectExtractionStatus,
    RepositoryObjectIdentifier,
)
from app.sap.exceptions import (
    RepositoryObjectNotFoundError,
    RepositoryObjectTypeNotSupportedError,
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPConfigurationError,
    SAPConnectionError,
    SAPFunctionExecutionError,
)
from app.services.object_service import RepositoryObjectService

logger = logging.getLogger("app.services.bulk_extraction")

# Alias used by tests that import the constant by the longer name.
BULK_MAX_OBJECTS_HARD_CEILING: int = BULK_OBJECTS_HARD_CEILING

# Internal sentinel for fail-fast early termination.
class _FailFastSignal(Exception):
    pass


class BulkExtractionService:
    """Synchronous bulk extraction orchestrator (Phase 5).

    Responsibilities:
      - Normalize and validate the request
      - Resolve object selection (explicit or search-based)
      - Deduplicate and enforce bulk limits
      - Extract metadata and/or source with bounded concurrency
      - Collect per-object outcomes
      - Handle partial failures according to continue_on_error
      - Calculate statistics
      - Return a canonical BulkExtractionResult

    This service does NOT perform export serialisation.  The ExportService
    consumes the BulkExtractionResult returned here.
    """

    def __init__(
        self,
        object_service: RepositoryObjectService,
        settings: Settings,
    ) -> None:
        self._object_service = object_service
        self._settings = settings
        # Optional: set by tests to inject a mock discovery service.
        self._discovery_service = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def extract(
        self,
        request: BulkExtractionRequest,
        progress_callback=None,
        cancellation_check=None,
    ) -> BulkExtractionResult:
        """Run a bulk extraction and return the canonical result.

        Args:
            request: Validated BulkExtractionRequest.
            progress_callback: Optional async callable(event: str, data: dict).
                Called at key lifecycle points (object_started, object_completed,
                object_failed, export_started, export_completed, etc.).
                Must be optional so synchronous Phase 5 APIs are unchanged.
            cancellation_check: Optional async callable() -> bool.
                Returns True when the caller wants cancellation.
                Checked between objects and before each extraction phase.

        Returns:
            BulkExtractionResult with per-object outcomes and statistics.

        Raises:
            ValueError: If the request exceeds the configured synchronous bulk limit.
        """
        started_at = datetime.now(tz=timezone.utc)
        t0 = time.monotonic()
        extraction_id = str(uuid.uuid4())

        logger.info(
            "bulk_extraction_start",
            extra={
                "operation": "bulk_extraction",
                "extraction_id": extraction_id,
                "selection": "explicit" if request.objects is not None else "search",
                "include_metadata": request.include_metadata,
                "include_source": request.include_source,
                "source_max_lines": request.source_max_lines,
                "continue_on_error": request.continue_on_error,
            },
        )

        # ---------------------------------------------------------------
        # 1. Resolve object selection
        # ---------------------------------------------------------------
        selection_label = "explicit" if request.objects is not None else "search"
        warnings: list[str] = []

        async def _emit(event: str, data: dict) -> None:
            if progress_callback is not None:
                try:
                    await progress_callback(event, data)
                except Exception:
                    pass  # never let callback failures break extraction

        async def _is_cancelled() -> bool:
            if cancellation_check is None:
                return False
            try:
                return bool(await cancellation_check())
            except Exception:
                return False

        await _emit("selection_started", {"extraction_id": extraction_id})

        try:
            objects = await self._resolve_selection(request, warnings)
        except Exception as exc:
            # Discovery failure → overall FAILED immediately
            completed_at = datetime.now(tz=timezone.utc)
            duration_ms = (time.monotonic() - t0) * 1000.0
            logger.error(
                "bulk_extraction_discovery_failed",
                extra={
                    "extraction_id": extraction_id,
                    "error": str(exc),
                },
            )
            return BulkExtractionResult(
                extraction_id=extraction_id,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=duration_ms,
                status=BulkExtractionStatus.FAILED,
                selection=selection_label,
                requested_object_count=0,
                processed_object_count=0,
                successful_object_count=0,
                partial_object_count=0,
                failed_object_count=0,
                metadata_enabled=request.include_metadata,
                source_enabled=request.include_source,
                source_max_lines=request.source_max_lines,
                warnings=warnings,
                objects=[],
            )

        await _emit("selection_resolved", {"extraction_id": extraction_id, "count": len(objects)})
        requested_count = len(objects)

        # ---------------------------------------------------------------
        # 2. Enforce bulk object limit
        # ---------------------------------------------------------------
        configured_max = min(
            self._settings.ncoreiq_bulk_max_objects,
            BULK_OBJECTS_HARD_CEILING,
        )
        if requested_count > configured_max:
            raise ValueError(
                f"Bulk extraction request contains {requested_count} objects, "
                f"which exceeds the Phase 5 synchronous extraction limit of "
                f"{configured_max}. Reduce the number of objects or use a more "
                f"specific selection criterion."
            )

        # ---------------------------------------------------------------
        # 3. Extract objects with bounded concurrency
        # ---------------------------------------------------------------
        concurrency = min(
            self._settings.ncoreiq_bulk_concurrency,
            5,  # absolute max
        )
        semaphore = asyncio.Semaphore(concurrency)

        extracted_objects: list[ExtractedRepositoryObject] = []
        aggregate_source_lines = 0
        max_aggregate = self._settings.ncoreiq_bulk_max_total_source_lines

        # We process serially in batches via gather so we can support
        # fail-fast.  To keep bounded concurrency we use a semaphore.
        fail_fast_triggered = False
        cancelled = False

        for i, obj_id in enumerate(objects):
            # Cancellation check between objects
            if await _is_cancelled():
                cancelled = True
                break

            if fail_fast_triggered:
                extracted_objects.append(
                    ExtractedRepositoryObject(
                        object_type=obj_id.object_type,
                        object_name=obj_id.object_name,
                        status=ObjectExtractionStatus.SKIPPED,
                        errors=[
                            ExtractionError(
                                code="SKIPPED_FAIL_FAST",
                                message="Skipped due to fail-fast mode.",
                                stage=ExtractionStage.METADATA,
                            )
                        ],
                    )
                )
                continue

            # Check aggregate source line limit before extraction
            if (
                request.include_source
                and aggregate_source_lines >= max_aggregate
            ):
                warnings.append(
                    f"Aggregate source line limit ({max_aggregate}) reached. "
                    f"Object {obj_id.object_type}/{obj_id.object_name} skipped."
                )
                extracted_objects.append(
                    ExtractedRepositoryObject(
                        object_type=obj_id.object_type,
                        object_name=obj_id.object_name,
                        status=ObjectExtractionStatus.SKIPPED,
                        warnings=[
                            f"Skipped: aggregate source line limit ({max_aggregate}) reached."
                        ],
                    )
                )
                continue

            await _emit("object_started", {
                "extraction_id": extraction_id,
                "object_type": obj_id.object_type,
                "object_name": obj_id.object_name,
                "index": i,
                "total": requested_count,
            })

            extracted = await self._extract_single(
                obj_id=obj_id,
                request=request,
                semaphore=semaphore,
            )

            # Track aggregate source lines
            if extracted.source is not None:
                aggregate_source_lines += extracted.source.returned_lines

            extracted_objects.append(extracted)

            event_name = (
                "object_failed"
                if extracted.status == ObjectExtractionStatus.FAILED
                else "object_completed"
            )
            await _emit(event_name, {
                "extraction_id": extraction_id,
                "object_type": obj_id.object_type,
                "object_name": obj_id.object_name,
                "status": extracted.status.value,
                "index": i,
                "total": requested_count,
            })

            # Fail-fast check
            if (
                not request.continue_on_error
                and extracted.status == ObjectExtractionStatus.FAILED
            ):
                fail_fast_triggered = True

        # Mark remaining objects as skipped if cancelled
        if cancelled:
            remaining = objects[len(extracted_objects):]
            for obj_id in remaining:
                extracted_objects.append(
                    ExtractedRepositoryObject(
                        object_type=obj_id.object_type,
                        object_name=obj_id.object_name,
                        status=ObjectExtractionStatus.SKIPPED,
                        errors=[
                            ExtractionError(
                                code="SKIPPED_CANCELLED",
                                message="Skipped: job cancellation requested.",
                                stage=ExtractionStage.METADATA,
                            )
                        ],
                    )
                )

        # ---------------------------------------------------------------
        # 4. Sort deterministically
        # ---------------------------------------------------------------
        extracted_objects.sort(key=lambda o: (o.object_type, o.object_name))

        # ---------------------------------------------------------------
        # 5. Compute statistics
        # ---------------------------------------------------------------
        successful = sum(
            1 for o in extracted_objects if o.status == ObjectExtractionStatus.SUCCESS
        )
        partial = sum(
            1 for o in extracted_objects if o.status == ObjectExtractionStatus.PARTIAL
        )
        failed = sum(
            1 for o in extracted_objects if o.status == ObjectExtractionStatus.FAILED
        )
        skipped = sum(
            1 for o in extracted_objects if o.status == ObjectExtractionStatus.SKIPPED
        )
        truncated = sum(
            1
            for o in extracted_objects
            if o.source is not None and o.source.truncated
        )

        if cancelled:
            overall_status = BulkExtractionStatus.CANCELLED
        elif failed == 0 and partial == 0 and skipped == 0:
            overall_status = BulkExtractionStatus.COMPLETED
        elif successful == 0 and partial == 0:
            overall_status = BulkExtractionStatus.FAILED
        else:
            overall_status = BulkExtractionStatus.COMPLETED_WITH_ERRORS

        # ---------------------------------------------------------------
        # 6. Extract system context from first successful metadata
        # ---------------------------------------------------------------
        system_id: str | None = None
        client: str | None = None
        for obj in extracted_objects:
            if obj.metadata is not None:
                system_id = obj.metadata.system_id
                client = obj.metadata.client
                break
            if obj.source is not None:
                system_id = obj.source.system_id
                client = obj.source.client
                break

        completed_at = datetime.now(tz=timezone.utc)
        duration_ms = (time.monotonic() - t0) * 1000.0

        logger.info(
            "bulk_extraction_complete",
            extra={
                "operation": "bulk_extraction",
                "extraction_id": extraction_id,
                "selection": selection_label,
                "requested_count": requested_count,
                "processed_count": len(extracted_objects),
                "successful": successful,
                "partial": partial,
                "failed": failed,
                "skipped": skipped,
                "truncated": truncated,
                "duration_ms": round(duration_ms, 1),
                "status": overall_status.value,
            },
        )
        await _emit("extraction_completed", {
            "extraction_id": extraction_id,
            "status": overall_status.value,
            "successful": successful,
            "partial": partial,
            "failed": failed,
            "skipped": skipped,
        })

        return BulkExtractionResult(
            extraction_id=extraction_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            status=overall_status,
            selection=selection_label,
            requested_object_count=requested_count,
            processed_object_count=len(extracted_objects),
            successful_object_count=successful,
            partial_object_count=partial,
            failed_object_count=failed,
            skipped_object_count=skipped,
            truncated_object_count=truncated,
            metadata_enabled=request.include_metadata,
            source_enabled=request.include_source,
            source_max_lines=request.source_max_lines,
            system_id=system_id,
            client=client,
            warnings=warnings,
            objects=extracted_objects,
        )

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    async def _resolve_selection(
        self,
        request: BulkExtractionRequest,
        warnings: list[str],
    ) -> list[RepositoryObjectIdentifier]:
        """Resolve explicit or search-based object selection.

        Returns a deduplicated, deterministically ordered list of
        RepositoryObjectIdentifier values.
        """
        if request.objects is not None:
            return self._dedup_objects(request.objects)

        # Search-based selection
        assert request.search is not None

        # Use injected discovery service (set in tests) or create one lazily.
        if self._discovery_service is not None:
            discovery_service = self._discovery_service
        else:
            from app.services.repository import RepositoryService
            discovery_service = RepositoryService(self._object_service._provider)

        search_response = await discovery_service.search(request.search)

        # Convert RepositorySearchResponse objects to RepositoryObjectIdentifier.
        # RepositorySearchResponse uses a 'results' field.
        raw_items = getattr(search_response, "results", None)
        if raw_items is None:
            raw_items = getattr(search_response, "objects", [])

        identifiers: list[RepositoryObjectIdentifier] = []
        for item in raw_items:
            try:
                # RepositoryObject (Phase 3) stores the type as sap_object_type;
                # other models may use object_type.  Support both.
                obj_type = getattr(item, "sap_object_type", None) or getattr(item, "object_type", None)
                ident = RepositoryObjectIdentifier(
                    object_type=obj_type,
                    object_name=item.object_name,
                )
                identifiers.append(ident)
            except (ValueError, AttributeError, TypeError):
                # Unsupported type returned by search – skip with warning
                obj_type_display = getattr(item, "sap_object_type", None) or getattr(item, "object_type", "?")
                warnings.append(
                    f"Search returned unsupported object type "
                    f"{obj_type_display!r} ({getattr(item, 'object_name', '?')}). Skipped."
                )

        if not identifiers:
            warnings.append("Search returned zero matching objects.")

        return self._dedup_objects(identifiers)

    @staticmethod
    def _dedup_objects(
        objects: list[RepositoryObjectIdentifier],
    ) -> list[RepositoryObjectIdentifier]:
        """Deduplicate by (object_type, object_name), preserving first occurrence order."""
        seen: set[tuple[str, str]] = set()
        result: list[RepositoryObjectIdentifier] = []
        for obj in objects:
            key = (obj.object_type, obj.object_name)
            if key not in seen:
                seen.add(key)
                result.append(obj)
        return result

    async def _extract_single(
        self,
        obj_id: RepositoryObjectIdentifier,
        request: BulkExtractionRequest,
        semaphore: asyncio.Semaphore,
    ) -> ExtractedRepositoryObject:
        """Extract metadata and/or source for a single object.

        Uses the semaphore to enforce bounded concurrency.
        """
        async with semaphore:
            t0 = time.monotonic()
            metadata = None
            source = None
            errors: list[ExtractionError] = []
            warnings: list[str] = []

            # --- Metadata ---
            if request.include_metadata:
                try:
                    metadata = await self._object_service.get_metadata(
                        object_type=obj_id.object_type,
                        object_name=obj_id.object_name,
                    )
                except RepositoryObjectNotFoundError:
                    errors.append(
                        ExtractionError(
                            code="OBJECT_NOT_FOUND",
                            message=f"Object {obj_id.object_type}/{obj_id.object_name} not found in SAP.",
                            stage=ExtractionStage.METADATA,
                        )
                    )
                except RepositoryObjectTypeNotSupportedError as exc:
                    errors.append(
                        ExtractionError(
                            code="UNSUPPORTED_OBJECT_TYPE",
                            message=f"Object type {obj_id.object_type!r} is not supported.",
                            stage=ExtractionStage.METADATA,
                        )
                    )
                except (SAPAuthenticationError, SAPConfigurationError) as exc:
                    errors.append(
                        ExtractionError(
                            code="SAP_AUTH_ERROR",
                            message="SAP authentication or configuration error.",
                            stage=ExtractionStage.METADATA,
                        )
                    )
                except (SAPConnectionError, SAPCommunicationError):
                    errors.append(
                        ExtractionError(
                            code="SAP_CONNECTIVITY_ERROR",
                            message="SAP connectivity error during metadata extraction.",
                            stage=ExtractionStage.METADATA,
                        )
                    )
                except SAPFunctionExecutionError:
                    errors.append(
                        ExtractionError(
                            code="SAP_FUNCTION_ERROR",
                            message="SAP function execution error during metadata extraction.",
                            stage=ExtractionStage.METADATA,
                        )
                    )
                except Exception as exc:
                    errors.append(
                        ExtractionError(
                            code="METADATA_ERROR",
                            message=f"Unexpected error during metadata extraction: {type(exc).__name__}",
                            stage=ExtractionStage.METADATA,
                        )
                    )

            # --- Source ---
            # Skip source if metadata extraction produced ANY error.
            # If metadata failed for any reason (not found, auth, function error, etc.),
            # we cannot reliably extract source - skip it to avoid consuming the
            # side_effect queue and to produce a clean FAILED (not PARTIAL) status.
            metadata_had_errors = len(errors) > 0
            if request.include_source and not metadata_had_errors:
                try:
                    source = await self._object_service.get_source(
                        object_type=obj_id.object_type,
                        object_name=obj_id.object_name,
                        max_lines=request.source_max_lines,
                    )
                    if source.truncated:
                        warnings.append(
                            f"Source truncated at {source.returned_lines} lines "
                            f"(limit: {request.source_max_lines})."
                        )
                except RepositoryObjectNotFoundError:
                    errors.append(
                        ExtractionError(
                            code="OBJECT_NOT_FOUND",
                            message=f"Object {obj_id.object_type}/{obj_id.object_name} not found in SAP (source).",
                            stage=ExtractionStage.SOURCE,
                        )
                    )
                except RepositoryObjectTypeNotSupportedError:
                    errors.append(
                        ExtractionError(
                            code="UNSUPPORTED_OBJECT_TYPE",
                            message=f"Object type {obj_id.object_type!r} is not supported for source extraction.",
                            stage=ExtractionStage.SOURCE,
                        )
                    )
                except (SAPAuthenticationError, SAPConfigurationError):
                    errors.append(
                        ExtractionError(
                            code="SAP_AUTH_ERROR",
                            message="SAP authentication or configuration error during source extraction.",
                            stage=ExtractionStage.SOURCE,
                        )
                    )
                except (SAPConnectionError, SAPCommunicationError):
                    errors.append(
                        ExtractionError(
                            code="SAP_CONNECTIVITY_ERROR",
                            message="SAP connectivity error during source extraction.",
                            stage=ExtractionStage.SOURCE,
                        )
                    )
                except SAPFunctionExecutionError:
                    errors.append(
                        ExtractionError(
                            code="SAP_FUNCTION_ERROR",
                            message="SAP function execution error during source extraction.",
                            stage=ExtractionStage.SOURCE,
                        )
                    )
                except Exception as exc:
                    errors.append(
                        ExtractionError(
                            code="SOURCE_ERROR",
                            message=f"Unexpected error during source extraction: {type(exc).__name__}",
                            stage=ExtractionStage.SOURCE,
                        )
                    )

            # --- Determine per-object status ---
            metadata_ok = (
                metadata is not None
                if request.include_metadata
                else True
            )
            source_ok = (
                source is not None
                if request.include_source and not metadata_had_errors
                else True
            )

            if metadata_ok and source_ok and not errors:
                status = ObjectExtractionStatus.SUCCESS
            elif not metadata_ok and not source_ok and not metadata and not source:
                status = ObjectExtractionStatus.FAILED
            elif errors and (metadata is None and source is None):
                status = ObjectExtractionStatus.FAILED
            elif errors:
                status = ObjectExtractionStatus.PARTIAL
            else:
                status = ObjectExtractionStatus.SUCCESS

            duration_ms = (time.monotonic() - t0) * 1000.0

            return ExtractedRepositoryObject(
                object_type=obj_id.object_type,
                object_name=obj_id.object_name,
                status=status,
                metadata=metadata,
                source=source,
                warnings=warnings,
                errors=errors,
                duration_ms=duration_ms,
            )


def get_bulk_extraction_service(
    object_service: RepositoryObjectService = Depends(
        lambda: None  # replaced by proper dep in routes
    ),
    settings: Settings = Depends(get_settings),
) -> BulkExtractionService:
    """FastAPI dependency factory for BulkExtractionService."""
    return BulkExtractionService(object_service=object_service, settings=settings)
