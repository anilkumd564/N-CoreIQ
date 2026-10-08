"""Phase 3 — Package resolution and discovery service tests.

Tests RepositoryDiscoveryService in isolation by mocking the
SapConnectionProvider.call() method.  All SAP RFC responses are synthetic.

Coverage:
  Package resolution (Z_MCP_REPO_PACKAGES):
    - root package only
    - recursive hierarchy with multiple depth levels
    - duplicate child packages deduplicated
    - truncated hierarchy flagged
    - unknown/empty package response
    - SAP RFC error propagated

  Discovery (Z_MCP_REPO_DISCOVER):
    - package-only discovery
    - Z* pattern discovery
    - Y* pattern discovery
    - multiple patterns
    - multiple packages
    - object-type filtering (criteria reflected in response)
    - PACKAGE match reason
    - NAME_PATTERN match reason
    - BOTH match reason
    - zero matches → valid 200 response
    - truncated result flagged
    - total_matches > returned rows
    - optional ECC fields missing (defensive handling)
    - unknown ECC object type handled safely (no crash)
    - all_custom_objects=true injects Z*/Y* patterns
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.repository import MatchReason
from app.models.requests import RepositorySearchRequest
from app.services.discovery import RepositoryDiscoveryService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service(call_return: dict) -> RepositoryDiscoveryService:
    """Build a RepositoryDiscoveryService with a mocked connection."""
    conn = MagicMock()
    conn.call = AsyncMock(return_value=call_return)
    return RepositoryDiscoveryService(conn)


def _prog_row(**overrides) -> dict:
    base = {
        "PGMID": "R3TR",
        "OBJECT": "PROG",
        "OBJ_NAME": "ZFI_REPORT",
        "DEVCLASS": "ZFINANCE",
        "AUTHOR": "DEVUSER",
    }
    base.update(overrides)
    return base


def _clas_row(**overrides) -> dict:
    base = {
        "PGMID": "R3TR",
        "OBJECT": "CLAS",
        "OBJ_NAME": "ZCL_PAYMENT",
        "DEVCLASS": "ZFINANCE",
        "AUTHOR": "DEVUSER",
    }
    base.update(overrides)
    return base


def _discover_response(
    objects: list[dict] | None = None,
    truncated: bool = False,
    total_matches: int = 0,
    message: str = "",
) -> dict:
    return {
        "EV_TRUNCATED": "X" if truncated else "",
        "EV_TOTAL_MATCHES": str(total_matches) if total_matches else "",
        "EV_MESSAGE": message,
        "ET_OBJECTS": objects or [],
    }


# ---------------------------------------------------------------------------
# Package resolution tests
# ---------------------------------------------------------------------------


class TestPackageResolution:
    @pytest.mark.asyncio
    async def test_root_package_only(self):
        svc = _make_service(
            {
                "EV_TRUNCATED": "",
                "EV_MESSAGE": "",
                "ET_PACKAGES": [
                    {"DEVCLASS": "ZFINANCE", "PARENTCL": "", "CTEXT": "Finance", "DEPTH": "0"}
                ],
            }
        )
        result = await svc.resolve_packages("ZFINANCE", include_subpackages=False)
        assert len(result.packages) == 1
        pkg = result.packages[0]
        assert pkg.package == "ZFINANCE"
        assert pkg.parent_package is None
        assert pkg.depth == 0
        assert pkg.description == "Finance"
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_recursive_hierarchy(self):
        svc = _make_service(
            {
                "EV_TRUNCATED": "",
                "EV_MESSAGE": "",
                "ET_PACKAGES": [
                    {"DEVCLASS": "ZFINANCE", "PARENTCL": "", "CTEXT": "Finance", "DEPTH": "0"},
                    {"DEVCLASS": "ZFI_AR", "PARENTCL": "ZFINANCE", "CTEXT": "AR", "DEPTH": "1"},
                    {"DEVCLASS": "ZFI_AP", "PARENTCL": "ZFINANCE", "CTEXT": "AP", "DEPTH": "1"},
                    {"DEVCLASS": "ZFI_AR_UTIL", "PARENTCL": "ZFI_AR", "CTEXT": "AR Util", "DEPTH": "2"},
                ],
            }
        )
        result = await svc.resolve_packages("ZFINANCE", include_subpackages=True)
        assert len(result.packages) == 4
        depths = [p.depth for p in result.packages]
        assert 0 in depths
        assert 1 in depths
        assert 2 in depths

    @pytest.mark.asyncio
    async def test_duplicate_child_packages_deduplicated(self):
        svc = _make_service(
            {
                "EV_TRUNCATED": "",
                "EV_MESSAGE": "",
                "ET_PACKAGES": [
                    {"DEVCLASS": "ZFINANCE", "PARENTCL": "", "CTEXT": "Finance", "DEPTH": "0"},
                    {"DEVCLASS": "ZFI_AR", "PARENTCL": "ZFINANCE", "CTEXT": "AR", "DEPTH": "1"},
                    {"DEVCLASS": "ZFI_AR", "PARENTCL": "ZFINANCE", "CTEXT": "AR dup", "DEPTH": "1"},
                ],
            }
        )
        result = await svc.resolve_packages("ZFINANCE", include_subpackages=True)
        names = [p.package for p in result.packages]
        assert names.count("ZFI_AR") == 1

    @pytest.mark.asyncio
    async def test_truncated_hierarchy_flagged(self):
        svc = _make_service(
            {
                "EV_TRUNCATED": "X",
                "EV_MESSAGE": "Hierarchy truncated",
                "ET_PACKAGES": [
                    {"DEVCLASS": "ZFINANCE", "PARENTCL": "", "CTEXT": "Finance", "DEPTH": "0"}
                ],
            }
        )
        result = await svc.resolve_packages("ZFINANCE", include_subpackages=True)
        assert result.truncated is True
        assert result.message == "Hierarchy truncated"

    @pytest.mark.asyncio
    async def test_empty_package_response(self):
        svc = _make_service({"EV_TRUNCATED": "", "EV_MESSAGE": "", "ET_PACKAGES": []})
        result = await svc.resolve_packages("ZUNKNOWN", include_subpackages=False)
        assert result.packages == []
        assert result.truncated is False

    @pytest.mark.asyncio
    async def test_sap_rfc_error_propagated(self):
        from app.sap.exceptions import SAPFunctionExecutionError

        conn = MagicMock()
        conn.call = AsyncMock(side_effect=SAPFunctionExecutionError("RFC failed"))
        svc = RepositoryDiscoveryService(conn)
        with pytest.raises(SAPFunctionExecutionError):
            await svc.resolve_packages("ZFINANCE")

    @pytest.mark.asyncio
    async def test_normalised_package_response(self):
        """Verify deterministic ordering and field mapping."""
        svc = _make_service(
            {
                "EV_TRUNCATED": "",
                "EV_MESSAGE": "OK",
                "ET_PACKAGES": [
                    {"DEVCLASS": "zpay", "PARENTCL": "", "CTEXT": "Payments", "DEPTH": "0"},
                    {"DEVCLASS": "ZPAY_CORE", "PARENTCL": "zpay", "CTEXT": "Core", "DEPTH": "1"},
                ],
            }
        )
        result = await svc.resolve_packages("ZPAY", include_subpackages=True)
        names = [p.package for p in result.packages]
        assert "ZPAY" in names
        assert "ZPAY_CORE" in names
        zpay_core = next(p for p in result.packages if p.package == "ZPAY_CORE")
        assert zpay_core.parent_package == "ZPAY"
        assert zpay_core.depth == 1


# ---------------------------------------------------------------------------
# Discovery tests
# ---------------------------------------------------------------------------


class TestRepositoryDiscovery:
    @pytest.mark.asyncio
    async def test_package_only_discovery(self):
        svc = _make_service(
            _discover_response(
                objects=[_prog_row(MATCH_REASON="PACKAGE")],
                total_matches=1,
            )
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 1
        assert resp.objects[0].sap_object_type == "PROG"
        assert resp.objects[0].object_name == "ZFI_REPORT"
        assert resp.objects[0].package == "ZFINANCE"

    @pytest.mark.asyncio
    async def test_z_star_pattern_discovery(self):
        svc = _make_service(
            _discover_response(
                objects=[_prog_row(OBJ_NAME="ZPAY_PROCESS", MATCH_REASON="NAME_PATTERN")],
                total_matches=1,
            )
        )
        req = RepositorySearchRequest(name_patterns=["Z*"])
        resp = await svc.search(req)
        assert resp.objects[0].object_name == "ZPAY_PROCESS"
        assert resp.objects[0].match_reason == MatchReason.NAME_PATTERN

    @pytest.mark.asyncio
    async def test_y_star_pattern_discovery(self):
        svc = _make_service(
            _discover_response(
                objects=[_prog_row(OBJ_NAME="YFI_REPORT", MATCH_REASON="NAME_PATTERN")],
                total_matches=1,
            )
        )
        req = RepositorySearchRequest(name_patterns=["Y*"])
        resp = await svc.search(req)
        assert resp.objects[0].object_name == "YFI_REPORT"

    @pytest.mark.asyncio
    async def test_multiple_patterns(self):
        svc = _make_service(
            _discover_response(
                objects=[
                    _prog_row(OBJ_NAME="ZFI_RPT", MATCH_REASON="NAME_PATTERN"),
                    _prog_row(OBJ_NAME="YFI_RPT", MATCH_REASON="NAME_PATTERN"),
                ],
                total_matches=2,
            )
        )
        req = RepositorySearchRequest(name_patterns=["ZFI*", "YFI*"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 2
        names = [o.object_name for o in resp.objects]
        assert "ZFI_RPT" in names
        assert "YFI_RPT" in names

    @pytest.mark.asyncio
    async def test_multiple_packages(self):
        svc = _make_service(
            _discover_response(
                objects=[
                    _prog_row(OBJ_NAME="ZFI_RPT", DEVCLASS="ZFINANCE", MATCH_REASON="PACKAGE"),
                    _prog_row(OBJ_NAME="ZPAY_RPT", DEVCLASS="ZPAYMENT", MATCH_REASON="PACKAGE"),
                ],
                total_matches=2,
            )
        )
        req = RepositorySearchRequest(packages=["ZFINANCE", "ZPAYMENT"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 2
        packages = [o.package for o in resp.objects]
        assert "ZFINANCE" in packages
        assert "ZPAYMENT" in packages

    @pytest.mark.asyncio
    async def test_object_type_reflected_in_criteria(self):
        svc = _make_service(
            _discover_response(
                objects=[_clas_row(MATCH_REASON="PACKAGE")],
                total_matches=1,
            )
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"], object_types=["CLAS"])
        resp = await svc.search(req)
        assert "CLAS" in resp.criteria.object_types
        assert resp.objects[0].sap_object_type == "CLAS"
        assert resp.objects[0].friendly_type == "CLASS"

    @pytest.mark.asyncio
    async def test_match_reason_package(self):
        svc = _make_service(
            _discover_response(
                objects=[_prog_row(MATCH_REASON="PACKAGE")],
                total_matches=1,
            )
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        assert resp.objects[0].match_reason == MatchReason.PACKAGE

    @pytest.mark.asyncio
    async def test_match_reason_name_pattern(self):
        svc = _make_service(
            _discover_response(
                objects=[_prog_row(MATCH_REASON="NAME_PATTERN")],
                total_matches=1,
            )
        )
        req = RepositorySearchRequest(name_patterns=["Z*"])
        resp = await svc.search(req)
        assert resp.objects[0].match_reason == MatchReason.NAME_PATTERN

    @pytest.mark.asyncio
    async def test_match_reason_both(self):
        svc = _make_service(
            _discover_response(
                objects=[_prog_row(MATCH_REASON="BOTH")],
                total_matches=1,
            )
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"], name_patterns=["Z*"])
        resp = await svc.search(req)
        assert resp.objects[0].match_reason == MatchReason.BOTH

    @pytest.mark.asyncio
    async def test_zero_matches_valid_response(self):
        svc = _make_service(_discover_response(objects=[], total_matches=0))
        req = RepositorySearchRequest(packages=["ZUNKNOWN"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 0
        assert resp.statistics.total_matches == 0
        assert resp.truncated is False
        assert resp.objects == []
        assert "no matching" in resp.message.lower()

    @pytest.mark.asyncio
    async def test_truncated_result_flagged(self):
        svc = _make_service(
            _discover_response(
                objects=[_prog_row()],
                truncated=True,
                total_matches=5000,
            )
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"], max_results=1)
        resp = await svc.search(req)
        assert resp.truncated is True
        assert resp.statistics.total_matches == 5000
        assert resp.statistics.returned == 1
        assert "truncated" in resp.message.lower() or "5000" in resp.message

    @pytest.mark.asyncio
    async def test_total_matches_greater_than_returned(self):
        svc = _make_service(
            _discover_response(
                objects=[_prog_row(), _clas_row()],
                truncated=True,
                total_matches=999,
            )
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"], max_results=2)
        resp = await svc.search(req)
        assert resp.statistics.returned == 2
        assert resp.statistics.total_matches == 999
        assert resp.truncated is True

    @pytest.mark.asyncio
    async def test_optional_ecc_fields_missing(self):
        """A minimal row with only mandatory fields must not crash the service."""
        minimal_row = {"OBJECT": "PROG", "OBJ_NAME": "ZMINIMAL"}
        svc = _make_service(_discover_response(objects=[minimal_row], total_matches=1))
        req = RepositorySearchRequest(packages=["ZTEST"])
        resp = await svc.search(req)
        obj = resp.objects[0]
        assert obj.sap_object_type == "PROG"
        assert obj.object_name == "ZMINIMAL"
        assert obj.package is None
        assert obj.author is None
        assert obj.description is None
        assert obj.created_date is None
        assert obj.changed_date is None
        assert obj.match_reason is None

    @pytest.mark.asyncio
    async def test_unknown_ecc_object_type_handled_safely(self):
        """An ECC row with an unrecognised object type must not raise an exception."""
        unknown_row = {"OBJECT": "ZUNK", "OBJ_NAME": "ZUNKNOWN_OBJ", "DEVCLASS": "ZTEST"}
        svc = _make_service(_discover_response(objects=[unknown_row], total_matches=1))
        req = RepositorySearchRequest(packages=["ZTEST"])
        resp = await svc.search(req)
        # The object should still be returned with the raw type preserved.
        assert resp.statistics.returned == 1
        assert resp.objects[0].sap_object_type == "ZUNK"
        assert resp.objects[0].friendly_type == "UNKNOWN"

    @pytest.mark.asyncio
    async def test_all_custom_objects_injects_zy_patterns(self):
        """all_custom_objects=True must result in Z*/Y* in the effective criteria."""
        svc = _make_service(_discover_response(objects=[], total_matches=0))
        req = RepositorySearchRequest(all_custom_objects=True)
        resp = await svc.search(req)
        assert "Z*" in resp.criteria.name_patterns
        assert "Y*" in resp.criteria.name_patterns
        assert resp.criteria.all_custom_objects is True

    @pytest.mark.asyncio
    async def test_empty_object_types_uses_all_phase3_types(self):
        """When object_types is empty, all Phase 3 types should appear in criteria."""
        from app.models.object_types import PHASE3_OBJECT_TYPES

        svc = _make_service(_discover_response(objects=[], total_matches=0))
        req = RepositorySearchRequest(packages=["ZTEST"], object_types=[])
        resp = await svc.search(req)
        assert set(resp.criteria.object_types) == PHASE3_OBJECT_TYPES

    @pytest.mark.asyncio
    async def test_friendly_type_mapping_all_phase3_types(self):
        """Every Phase 3 object type must map to a non-empty friendly name."""
        from app.models.object_types import PHASE3_OBJECT_TYPES, get_friendly_type

        for otype in PHASE3_OBJECT_TYPES:
            friendly = get_friendly_type(otype)
            assert friendly, f"No friendly name for {otype}"
            assert friendly != "UNKNOWN", f"Type {otype} mapped to UNKNOWN unexpectedly"

    @pytest.mark.asyncio
    async def test_sap_communication_error_propagated(self):
        from app.sap.exceptions import SAPCommunicationError

        conn = MagicMock()
        conn.call = AsyncMock(side_effect=SAPCommunicationError("ECC unreachable"))
        svc = RepositoryDiscoveryService(conn)
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        with pytest.raises(SAPCommunicationError):
            await svc.search(req)

    @pytest.mark.asyncio
    async def test_response_includes_criteria_echo(self):
        """The response criteria must echo the effective (normalised) filters."""
        svc = _make_service(_discover_response(objects=[], total_matches=0))
        req = RepositorySearchRequest(
            packages=["zfinance"],
            include_subpackages=True,
            object_types=["PROG"],
            max_results=50,
        )
        resp = await svc.search(req)
        assert resp.criteria.packages == ["ZFINANCE"]
        assert resp.criteria.include_subpackages is True
        assert resp.criteria.object_types == ["PROG"]
        assert resp.criteria.max_results == 50

    @pytest.mark.asyncio
    async def test_success_message_when_results_found(self):
        svc = _make_service(
            _discover_response(objects=[_prog_row()], total_matches=1)
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        assert "success" in resp.message.lower() or resp.message

    @pytest.mark.asyncio
    async def test_sap_date_parsing(self):
        """SAP YYYYMMDD date strings must be parsed into Python date objects."""
        row = _prog_row(CREATED_DATE="20230115", CHANGED_DATE="20240601")
        svc = _make_service(_discover_response(objects=[row], total_matches=1))
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        obj = resp.objects[0]
        assert obj.created_date is not None
        assert obj.created_date.year == 2023
        assert obj.created_date.month == 1
        assert obj.created_date.day == 15

    @pytest.mark.asyncio
    async def test_sap_zero_date_becomes_none(self):
        """SAP blank/zero date '00000000' must become None, not raise."""
        row = _prog_row(CREATED_DATE="00000000", CHANGED_DATE="")
        svc = _make_service(_discover_response(objects=[row], total_matches=1))
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        obj = resp.objects[0]
        assert obj.created_date is None
        assert obj.changed_date is None
