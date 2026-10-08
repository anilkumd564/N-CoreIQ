"""Phase 3 Repository Discovery Service.

Implements SAP repository object discovery through OData V2 (RepositoryObjectsSet)
and Z_MCP_REPO_PACKAGES (RFC).  All SAP interactions are mediated through the
SapConnectionProvider abstraction; no transport-specific logic lives here.

Architecture:
    REST API
       ↓
    RepositoryDiscoveryService
       ↓
    SapConnectionProvider.discover_repository_objects()  (OData V2)
    SapConnectionProvider.call("Z_MCP_REPO_PACKAGES")   (RFC – package resolution)
       ↓
    ZNCOREIQ_REPO_SRV / RepositoryObjectsSet
    Z_MCP_REPO_PACKAGES
       ↓
    SAP ECC
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.models.object_types import PHASE3_OBJECT_TYPES, get_friendly_type
from app.models.repository import MatchReason, PackageInfo, PackageResolutionResult, RepositoryObject
from app.models.requests import RepositorySearchRequest
from app.models.results import RepositorySearchResponse, SearchCriteria, SearchStatistics
from app.sap.connection.base import ODataDiscoveryResult, SapConnectionProvider

logger = logging.getLogger("app.services.discovery")

# Default Z/Y custom-namespace patterns applied when all_custom_objects=True.
_CUSTOM_NAMESPACE_PATTERNS: list[str] = ["Z*", "Y*"]

# SAP boolean token used in RFC exports (character 'X' = true, '' = false).
_SAP_TRUE = "X"

# Maximum number of OData calls the service will generate for a single search.
# Prevents accidental fan-out explosion from large multi-value inputs.
MAX_DISCOVERY_ODATA_CALLS = 50


# ---------------------------------------------------------------------------
# Internal search-plan dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _DiscoveryPlan:
    """Represents one controlled OData RepositoryObjectsSet call."""

    root_package: str | None
    name_pattern: str | None
    object_type: str | None  # None = no type filter (SAP returns all types)
    include_subpackages: bool
    max_results: int

    @property
    def source(self) -> str:
        """Return 'PACKAGE' if this is a package-based call, else 'NAME_PATTERN'."""
        return "PACKAGE" if self.root_package else "NAME_PATTERN"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RepositoryDiscoveryService:
    """Discovers SAP custom repository objects via OData V2 (RepositoryObjectsSet).

    Design principles:
    * No FastAPI / HTTP logic.  The service returns domain objects.
    * RFC exceptions propagate as-is; the route layer maps them to HTTP codes.
    * The request is already validated/normalised by RepositorySearchRequest.
    * Multi-value fan-out is handled here; the provider issues one OData call.
    """

    def __init__(self, connection: SapConnectionProvider) -> None:
        self._connection = connection

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def search(self, request: RepositorySearchRequest) -> RepositorySearchResponse:
        """Execute repository discovery and return a structured response.

        Args:
            request: Pre-validated RepositorySearchRequest from the API layer.

        Returns:
            RepositorySearchResponse with discovered objects and metadata.
        """
        start = time.monotonic()

        # ---- Determine effective filters --------------------------------

        effective_packages = list(request.packages)
        effective_patterns = list(request.name_patterns)

        if request.all_custom_objects and not effective_patterns:
            effective_patterns = list(_CUSTOM_NAMESPACE_PATTERNS)

        # Specific types requested, or None = no type filter per OData call.
        # When object_types is empty we still echo all Phase 3 types in criteria.
        specific_types: list[str] = list(request.object_types) if request.object_types else []
        effective_types_for_criteria: list[str] = specific_types if specific_types else sorted(PHASE3_OBJECT_TYPES)

        # For plan generation: send one type per call when types are specified,
        # or send None (no type clause) when no restriction is requested.
        plan_types: list[str | None] = specific_types if specific_types else [None]

        logger.info(
            "repository_search_start",
            extra={
                "operation": "repository_search",
                "package_count": len(effective_packages),
                "pattern_count": len(effective_patterns),
                "object_type_count": len(specific_types),
                "max_results": request.max_results,
                "include_subpackages": request.include_subpackages,
                "all_custom_objects": request.all_custom_objects,
            },
        )

        # ---- Build search plans -----------------------------------------

        plans = _build_plans(
            packages=effective_packages,
            patterns=effective_patterns,
            plan_types=plan_types,
            include_subpackages=request.include_subpackages,
            max_results=request.max_results,
        )

        logger.info(
            "repository_search_plan_generated",
            extra={
                "operation": "repository_search",
                "odata_call_count": len(plans),
            },
        )

        # ---- Execute plans ----------------------------------------------

        seen: dict[tuple[str, str], RepositoryObject] = {}
        any_truncated = False
        any_total_matches: int | None = None
        system_id: str | None = None
        client: str | None = None
        messages: list[str] = []

        for plan in plans:
            result: ODataDiscoveryResult = await self._connection.discover_repository_objects(
                root_package=plan.root_package,
                include_subpackages=plan.include_subpackages,
                name_pattern=plan.name_pattern,
                object_type=plan.object_type,
                max_results=plan.max_results,
            )

            if result.truncated:
                any_truncated = True

            # Collect total_matches from first populated OData call.
            # We avoid summing across calls because results may overlap.
            if result.total_matches is not None and any_total_matches is None:
                any_total_matches = result.total_matches

            if result.system_id and not system_id:
                system_id = result.system_id
            if result.client and not client:
                client = result.client
            if result.message and result.message not in messages:
                messages.append(result.message)

            # ---- Normalize and deduplicate rows -------------------------
            for row in result.rows:
                obj = self._normalize_object(row)
                if obj is None:
                    continue

                key = (obj.sap_object_type, obj.object_name)

                if key not in seen:
                    seen[key] = obj
                else:
                    # Object already found via another call — merge match reason.
                    existing = seen[key]
                    existing.match_reason = _merge_match_reason(
                        existing.match_reason, obj.match_reason
                    )

        # ---- Aggregate results ------------------------------------------

        objects = list(seen.values())

        # Sort deterministically: type → name → package.
        objects.sort(
            key=lambda o: (
                o.sap_object_type or "",
                o.object_name or "",
                o.package or "",
            )
        )

        # Apply public max_results cap.
        if len(objects) > request.max_results:
            objects = objects[: request.max_results]
            any_truncated = True

        returned = len(objects)

        # When we have multiple OData calls the individual total_matches
        # figures may overlap (same object in package + pattern call), so
        # we only use the value from a single-call result if it is safe to
        # do so; otherwise null signals "undetermined".
        total_matches: int | None
        if len(plans) == 1:
            total_matches = any_total_matches if any_total_matches is not None else returned
        else:
            # Multi-call: totals can overlap; trust returned count only.
            total_matches = None if any_truncated else returned

        # When ECC did not return total_matches and there is no truncation,
        # the returned count is exact.
        if total_matches is None and not any_truncated:
            total_matches = returned

        elapsed = time.monotonic() - start

        logger.info(
            "repository_search_complete",
            extra={
                "operation": "repository_search",
                "returned": returned,
                "total_matches": total_matches,
                "truncated": any_truncated,
                "odata_calls": len(plans),
                "duration_ms": round(elapsed * 1000),
            },
        )

        # ---- Build message ----------------------------------------------

        sap_message = " | ".join(messages) if messages else None
        if sap_message:
            message = sap_message
        elif returned == 0:
            message = "No matching repository objects were found."
        elif any_truncated:
            tm_str = str(total_matches) if total_matches is not None else "unknown"
            message = (
                f"Discovery completed. Results truncated at {request.max_results}; "
                f"ECC reports {tm_str} total matches."
            )
        else:
            message = "Discovery completed successfully."

        return RepositorySearchResponse(
            system_id=system_id,
            client=client,
            criteria=SearchCriteria(
                packages=effective_packages,
                include_subpackages=request.include_subpackages,
                name_patterns=effective_patterns,
                object_types=effective_types_for_criteria,
                all_custom_objects=request.all_custom_objects,
                max_results=request.max_results,
            ),
            statistics=SearchStatistics(
                total_matches=total_matches,
                returned=returned,
            ),
            truncated=any_truncated,
            message=message,
            objects=objects,
        )

    # ------------------------------------------------------------------
    # Package resolution (Z_MCP_REPO_PACKAGES via RFC)
    # ------------------------------------------------------------------

    async def resolve_packages(
        self,
        package: str,
        include_subpackages: bool = False,
        max_depth: int | None = None,
    ) -> PackageResolutionResult:
        """Resolve a root package (and optionally its hierarchy) via Z_MCP_REPO_PACKAGES.

        Args:
            package:              SAP package name (normalised, uppercase).
            include_subpackages:  When True, the ECC RFC returns child packages.
            max_depth:            Optional depth limit (None = no limit).

        Returns:
            PackageResolutionResult with the resolved package list.
        """
        params: dict[str, Any] = {
            "IV_PACKAGE": package,
            "IV_INCLUDE_SUBPACKAGES": _SAP_TRUE if include_subpackages else "",
        }
        if max_depth is not None:
            params["IV_MAX_DEPTH"] = max_depth

        raw = await self._connection.call("Z_MCP_REPO_PACKAGES", **params)

        truncated = bool(raw.get("EV_TRUNCATED") == _SAP_TRUE)
        message = str(raw.get("EV_MESSAGE") or "").strip() or None
        et_packages: list[dict[str, Any]] = raw.get("ET_PACKAGES") or []

        packages: list[PackageInfo] = []
        seen: set[str] = set()
        for row in et_packages:
            pkg_name = str(row.get("DEVCLASS") or row.get("PACKAGE") or "").strip().upper()
            if not pkg_name or pkg_name in seen:
                continue
            seen.add(pkg_name)
            packages.append(
                PackageInfo(
                    package=pkg_name,
                    parent_package=str(row.get("PARENTCL") or row.get("PARENT_PACKAGE") or "").strip().upper() or None,
                    description=str(row.get("CTEXT") or row.get("DESCRIPTION") or "").strip() or None,
                    depth=int(row.get("DEPTH") or 0),
                )
            )

        return PackageResolutionResult(
            root_package=package,
            packages=packages,
            truncated=truncated,
            message=message,
        )

    # ------------------------------------------------------------------
    # Internal normalisation helpers
    # ------------------------------------------------------------------

    def _normalize_object(self, row: dict[str, Any]) -> RepositoryObject | None:
        """Convert a raw RFC-style row into a RepositoryObject.

        Rows arrive from ODataDiscoveryResult.rows, which already maps OData
        field names to RFC-style keys (OBJECT, OBJ_NAME, DEVCLASS, …) inside
        _parse_repository_objects().

        Returns None for rows that are missing mandatory fields so a single
        bad row does not abort the entire result set.
        """
        sap_type = str(row.get("OBJECT") or "").strip().upper()
        obj_name = str(row.get("OBJ_NAME") or "").strip().upper()

        if not sap_type or not obj_name:
            logger.warning(
                "repository_object_missing_mandatory_fields",
                extra={"operation": "normalize_object", "row": str(row)[:200]},
            )
            return None

        friendly = get_friendly_type(sap_type)

        pgmid = str(row.get("PGMID") or "").strip().upper() or None
        package = str(row.get("DEVCLASS") or "").strip().upper() or None
        author = str(row.get("AUTHOR") or "").strip() or None
        description = str(row.get("DESCRIPTION") or "").strip() or None
        active_status = str(row.get("ACTIVE_STATUS") or "").strip() or None
        warning = str(row.get("WARNING") or "").strip() or None

        system_id = str(row.get("SYSTEM_ID") or "").strip() or None
        client = str(row.get("CLIENT") or "").strip() or None

        created_date = _parse_sap_date(row.get("CREATED_DATE"))
        changed_date = _parse_sap_date(row.get("CHANGED_DATE"))
        changed_by = str(row.get("CHANGED_BY") or "").strip() or None

        match_reason = _parse_match_reason(row.get("MATCH_REASON"))

        return RepositoryObject(
            system_id=system_id,
            client=client,
            pgmid=pgmid,
            sap_object_type=sap_type,
            friendly_type=friendly,
            object_name=obj_name,
            package=package,
            author=author,
            description=description,
            created_date=created_date,
            changed_by=changed_by,
            changed_date=changed_date,
            active_status=active_status,
            match_reason=match_reason,
            warning=warning,
        )


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _build_plans(
    packages: list[str],
    patterns: list[str],
    plan_types: list[str | None],
    include_subpackages: bool,
    max_results: int,
) -> list[_DiscoveryPlan]:
    """Generate the minimal set of OData RepositoryObjectsSet call plans.

    For each requested object type (or None for no-type-filter), emit one plan
    per package and one plan per pattern.  Duplicate combinations are skipped.

    Raises:
        ValueError: if the number of generated plans exceeds MAX_DISCOVERY_ODATA_CALLS.
    """
    plans: list[_DiscoveryPlan] = []
    seen_plans: set[tuple] = set()

    for otype in plan_types:
        for pkg in packages:
            key = ("PKG", pkg, otype)
            if key not in seen_plans:
                seen_plans.add(key)
                plans.append(
                    _DiscoveryPlan(
                        root_package=pkg,
                        name_pattern=None,
                        object_type=otype,
                        include_subpackages=include_subpackages,
                        max_results=max_results,
                    )
                )
        for pat in patterns:
            key = ("PAT", pat, otype)
            if key not in seen_plans:
                seen_plans.add(key)
                plans.append(
                    _DiscoveryPlan(
                        root_package=None,
                        name_pattern=pat,
                        object_type=otype,
                        include_subpackages=include_subpackages,
                        max_results=max_results,
                    )
                )

    if len(plans) > MAX_DISCOVERY_ODATA_CALLS:
        raise ValueError(
            f"The requested search would generate {len(plans)} OData calls, "
            f"which exceeds the safe limit of {MAX_DISCOVERY_ODATA_CALLS}. "
            "Reduce the number of packages, patterns, or object types."
        )

    return plans


def _merge_match_reason(
    existing: MatchReason | None,
    incoming: MatchReason | None,
) -> MatchReason | None:
    """Merge two MatchReason values when the same object appears in multiple calls.

    Rules:
    * BOTH always wins — never downgrade it.
    * PACKAGE + NAME_PATTERN (or vice-versa) → BOTH.
    * Same value → keep it.
    * One is None → keep the non-None value.
    """
    if existing == MatchReason.BOTH or incoming == MatchReason.BOTH:
        return MatchReason.BOTH
    if existing is None:
        return incoming
    if incoming is None:
        return existing
    if existing != incoming:
        return MatchReason.BOTH
    return existing


def _parse_match_reason(raw: Any) -> MatchReason | None:
    """Parse a raw SAP MATCH_REASON field into a MatchReason enum value."""
    if raw is None:
        return None
    value = str(raw).strip().upper()
    if value == "PACKAGE":
        return MatchReason.PACKAGE
    if value == "NAME_PATTERN":
        return MatchReason.NAME_PATTERN
    if value == "BOTH":
        return MatchReason.BOTH
    return None


def _parse_sap_date(raw: Any):
    """Parse a SAP YYYYMMDD date string into a Python date, or return None.

    SAP zero dates ('00000000', blank) become None.
    Malformed values are silently discarded.
    """
    if raw is None:
        return None
    value = str(raw).strip()
    if not value or value == "00000000":
        return None
    try:
        from datetime import date

        return date(int(value[:4]), int(value[4:6]), int(value[6:8]))
    except (ValueError, IndexError):
        logger.warning(
            "repository_object_invalid_date",
            extra={"operation": "parse_sap_date", "value": repr(raw)},
        )
        return None
