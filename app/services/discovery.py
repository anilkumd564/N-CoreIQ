"""Phase 3 Repository Discovery Service.

Implements SAP repository object discovery through Z_MCP_REPO_PACKAGES and
Z_MCP_REPO_DISCOVER.  All SAP RFC interactions are mediated through the
SapConnectionProvider abstraction; no RFC-specific logic lives in this file.

Architecture:
    REST API
       ↓
    RepositoryDiscoveryService
       ↓
    SapConnectionProvider (RFC allow-list enforced)
       ↓
    Z_MCP_REPO_PACKAGES / Z_MCP_REPO_DISCOVER
       ↓
    SAP ECC
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.models.object_types import PHASE3_OBJECT_TYPES, get_friendly_type
from app.models.repository import MatchReason, PackageInfo, PackageResolutionResult, RepositoryObject
from app.models.requests import RepositorySearchRequest
from app.models.results import RepositorySearchResponse, SearchCriteria, SearchStatistics
from app.sap.connection.base import SapConnectionProvider

logger = logging.getLogger("app.services.discovery")

# Default Z/Y custom-namespace patterns applied when all_custom_objects=True.
_CUSTOM_NAMESPACE_PATTERNS: list[str] = ["Z*", "Y*"]

# SAP boolean token used in RFC exports (character 'X' = true, '' = false).
_SAP_TRUE = "X"


class RepositoryDiscoveryService:
    """Discovers SAP custom repository objects via controlled ECC RFCs.

    Design principles:
    * No FastAPI / HTTP logic.  The service returns domain objects.
    * No N+1 RFC calls per discovered object.
    * RFC exceptions propagate as-is; the route layer maps them to HTTP codes.
    * The request is already validated/normalised by RepositorySearchRequest.
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

        # Determine effective filters.
        effective_packages = list(request.packages)
        effective_patterns = list(request.name_patterns)
        effective_types = list(request.object_types) if request.object_types else sorted(PHASE3_OBJECT_TYPES)

        if request.all_custom_objects and not effective_patterns:
            effective_patterns = list(_CUSTOM_NAMESPACE_PATTERNS)

        logger.info(
            "repository_search_start",
            extra={
                "operation": "repository_search",
                "package_count": len(effective_packages),
                "pattern_count": len(effective_patterns),
                "object_type_count": len(effective_types),
                "max_results": request.max_results,
                "include_subpackages": request.include_subpackages,
                "all_custom_objects": request.all_custom_objects,
            },
        )

        # Build the RFC input tables.
        it_packages = [{"DEVCLASS": pkg} for pkg in effective_packages]
        it_name_patterns = [{"PATTERN": pat} for pat in effective_patterns]
        it_object_types = [{"OBJECT": otype} for otype in effective_types]

        # Call Z_MCP_REPO_DISCOVER.
        raw = await self._connection.call(
            "Z_MCP_REPO_DISCOVER",
            IV_ALL_CUSTOM_OBJECTS=_SAP_TRUE if request.all_custom_objects else "",
            IV_INCLUDE_SUBPACKAGES=_SAP_TRUE if request.include_subpackages else "",
            IV_MAX_RESULTS=request.max_results,
            IT_PACKAGES=it_packages,
            IT_NAME_PATTERNS=it_name_patterns,
            IT_OBJECT_TYPES=it_object_types,
        )

        # Parse RFC output.
        truncated = bool(raw.get("EV_TRUNCATED") == _SAP_TRUE)
        total_matches = int(raw.get("EV_TOTAL_MATCHES") or 0)
        message = str(raw.get("EV_MESSAGE") or "").strip() or None
        et_objects: list[dict[str, Any]] = raw.get("ET_OBJECTS") or []

        # Normalise each returned SAP row into a RepositoryObject.
        objects: list[RepositoryObject] = []
        for row in et_objects:
            obj = self._normalize_object(row)
            if obj is not None:
                objects.append(obj)

        # ECC may report total_matches without including them all in ET_OBJECTS.
        # Use the row count as the canonical "returned" value.
        returned = len(objects)

        # When ECC does not populate EV_TOTAL_MATCHES, infer from returned rows.
        if total_matches == 0 and returned > 0:
            total_matches = returned

        elapsed = time.monotonic() - start

        logger.info(
            "repository_search_complete",
            extra={
                "operation": "repository_search",
                "returned": returned,
                "total_matches": total_matches,
                "truncated": truncated,
                "duration_ms": round(elapsed * 1000),
            },
        )

        if not message:
            if returned == 0:
                message = "No matching repository objects were found."
            elif truncated:
                message = (
                    f"Discovery completed. Results truncated at {request.max_results}; "
                    f"ECC reports {total_matches} total matches."
                )
            else:
                message = "Discovery completed successfully."

        # Determine system_id / client from first object if ECC didn't set them
        # at the export variable level.
        system_id: str | None = str(raw.get("EV_SYSTEM_ID") or "").strip() or None
        client: str | None = str(raw.get("EV_CLIENT") or "").strip() or None
        if not system_id and objects:
            system_id = objects[0].system_id
        if not client and objects:
            client = objects[0].client

        return RepositorySearchResponse(
            system_id=system_id,
            client=client,
            criteria=SearchCriteria(
                packages=effective_packages,
                include_subpackages=request.include_subpackages,
                name_patterns=effective_patterns,
                object_types=effective_types,
                all_custom_objects=request.all_custom_objects,
                max_results=request.max_results,
            ),
            statistics=SearchStatistics(
                total_matches=total_matches,
                returned=returned,
            ),
            truncated=truncated,
            message=message,
            objects=objects,
        )

    # ------------------------------------------------------------------
    # Package resolution (Z_MCP_REPO_PACKAGES)
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
            packages=packages,
            truncated=truncated,
            message=message,
        )

    # ------------------------------------------------------------------
    # Internal normalisation helpers
    # ------------------------------------------------------------------

    def _normalize_object(self, row: dict[str, Any]) -> RepositoryObject | None:
        """Convert a raw ECC ET_OBJECTS row into a RepositoryObject.

        Returns None for rows that are missing mandatory fields so a single
        bad row does not abort the entire result set.
        """
        # Mandatory fields from TADIR.
        sap_type = str(row.get("OBJECT") or "").strip().upper()
        obj_name = str(row.get("OBJ_NAME") or "").strip().upper()

        if not sap_type or not obj_name:
            logger.warning(
                "repository_object_missing_mandatory_fields",
                extra={"operation": "normalize_object", "row": str(row)[:200]},
            )
            return None

        friendly = get_friendly_type(sap_type)

        # Optional TADIR fields – treated defensively.
        pgmid = str(row.get("PGMID") or "").strip().upper() or None
        package = str(row.get("DEVCLASS") or "").strip().upper() or None
        author = str(row.get("AUTHOR") or "").strip() or None
        description = str(row.get("DESCRIPTION") or "").strip() or None
        active_status = str(row.get("ACTIVE_STATUS") or "").strip() or None
        warning = str(row.get("WARNING") or "").strip() or None

        # System context (optional – may be absent in Phase 3 ECC response).
        system_id = str(row.get("SYSTEM_ID") or "").strip() or None
        client = str(row.get("CLIENT") or "").strip() or None

        # Date fields – parse defensively; blank SAP dates (00000000) become None.
        created_date = _parse_sap_date(row.get("CREATED_DATE"))
        changed_date = _parse_sap_date(row.get("CHANGED_DATE"))
        changed_by = str(row.get("CHANGED_BY") or "").strip() or None

        # Match reason – normalise ECC string to enum.
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
# Private utility functions
# ---------------------------------------------------------------------------


def _parse_sap_date(value: Any) -> "date | None":
    """Parse an ECC date value into a Python date, returning None for blanks.

    SAP stores dates as 'YYYYMMDD' strings.  Blank or zero dates ('00000000'
    or empty string) are normalised to None.
    """
    from datetime import date

    if not value:
        return None
    s = str(value).strip()
    if not s or s == "00000000":
        return None
    try:
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    except (ValueError, IndexError):
        return None


def _parse_match_reason(value: Any) -> MatchReason | None:
    """Normalise an ECC MATCH_REASON string to the MatchReason enum.

    ECC may return values such as 'PACKAGE', 'NAME_PATTERN', 'BOTH',
    or blank.  Unknown values are silently returned as None.
    """
    if not value:
        return None
    s = str(value).strip().upper()
    try:
        return MatchReason(s)
    except ValueError:
        return None
