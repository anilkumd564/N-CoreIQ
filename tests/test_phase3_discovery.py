"""Phase 3 — Package resolution and discovery service tests.

Tests RepositoryDiscoveryService in isolation by mocking SapConnectionProvider:
  - resolve_packages → mocks conn.call (Z_MCP_REPO_PACKAGES RFC)
  - search           → mocks conn.discover_repository_objects (OData V2)
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.repository import MatchReason
from app.models.requests import RepositorySearchRequest
from app.sap.connection.base import ODataDiscoveryResult
from app.services.discovery import RepositoryDiscoveryService


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_service_rfc(call_return: dict) -> RepositoryDiscoveryService:
    conn = MagicMock()
    conn.call = AsyncMock(return_value=call_return)
    return RepositoryDiscoveryService(conn)


def _make_service(odata_result: ODataDiscoveryResult) -> RepositoryDiscoveryService:
    conn = MagicMock()
    conn.discover_repository_objects = AsyncMock(return_value=odata_result)
    return RepositoryDiscoveryService(conn)


def _make_service_multi(odata_results: list) -> RepositoryDiscoveryService:
    conn = MagicMock()
    conn.discover_repository_objects = AsyncMock(side_effect=odata_results)
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


def _odata_result(
    objects=None,
    truncated: bool = False,
    total_matches=None,
    message: str = "",
    system_id=None,
    client=None,
) -> ODataDiscoveryResult:
    return ODataDiscoveryResult(
        rows=objects or [],
        truncated=truncated,
        total_matches=total_matches,
        system_id=system_id,
        client=client,
        message=message,
    )


# ---------------------------------------------------------------------------
# Package resolution tests
# ---------------------------------------------------------------------------


class TestPackageResolution:
    @pytest.mark.asyncio
    async def test_root_package_only(self):
        svc = _make_service_rfc(
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
        svc = _make_service_rfc(
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
        svc = _make_service_rfc(
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
        svc = _make_service_rfc(
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
        svc = _make_service_rfc(
            {"EV_TRUNCATED": "", "EV_MESSAGE": "", "ET_PACKAGES": []}
        )
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
    async def test_normalised_package_uppercase(self):
        svc = _make_service_rfc(
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
        # lowercase input should be normalised to uppercase
        assert "ZPAY" in names
        assert "ZPAY_CORE" in names
        zpay_core = next(p for p in result.packages if p.package == "ZPAY_CORE")
        assert zpay_core.parent_package == "ZPAY"
        assert zpay_core.depth == 1


# ---------------------------------------------------------------------------
# Discovery tests  (mocks conn.discover_repository_objects)
# ---------------------------------------------------------------------------


class TestRepositoryDiscovery:
    @pytest.mark.asyncio
    async def test_package_only_discovery(self):
        svc = _make_service(
            _odata_result(
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
            _odata_result(
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
            _odata_result(
                objects=[_prog_row(OBJ_NAME="YFI_REPORT", MATCH_REASON="NAME_PATTERN")],
                total_matches=1,
            )
        )
        req = RepositorySearchRequest(name_patterns=["Y*"])
        resp = await svc.search(req)
        assert resp.objects[0].object_name == "YFI_REPORT"

    @pytest.mark.asyncio
    async def test_multiple_patterns_fan_out(self):
        svc = _make_service_multi(
            [
                _odata_result(
                    objects=[_prog_row(OBJ_NAME="ZFI_RPT", MATCH_REASON="NAME_PATTERN")],
                    total_matches=1,
                ),
                _odata_result(
                    objects=[_prog_row(OBJ_NAME="YFI_RPT", MATCH_REASON="NAME_PATTERN")],
                    total_matches=1,
                ),
            ]
        )
        req = RepositorySearchRequest(name_patterns=["ZFI*", "YFI*"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 2
        names = [o.object_name for o in resp.objects]
        assert "ZFI_RPT" in names
        assert "YFI_RPT" in names

    @pytest.mark.asyncio
    async def test_multiple_packages_fan_out(self):
        svc = _make_service_multi(
            [
                _odata_result(
                    objects=[_prog_row(OBJ_NAME="ZFI_RPT", DEVCLASS="ZFINANCE", MATCH_REASON="PACKAGE")],
                    total_matches=1,
                ),
                _odata_result(
                    objects=[_prog_row(OBJ_NAME="ZPAY_RPT", DEVCLASS="ZPAYMENT", MATCH_REASON="PACKAGE")],
                    total_matches=1,
                ),
            ]
        )
        req = RepositorySearchRequest(packages=["ZFINANCE", "ZPAYMENT"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 2
        packages_in_result = [o.package for o in resp.objects]
        assert "ZFINANCE" in packages_in_result
        assert "ZPAYMENT" in packages_in_result

    @pytest.mark.asyncio
    async def test_object_type_filter_reflected_in_criteria(self):
        svc = _make_service(
            _odata_result(
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
            _odata_result(objects=[_prog_row(MATCH_REASON="PACKAGE")], total_matches=1)
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        assert resp.objects[0].match_reason == MatchReason.PACKAGE

    @pytest.mark.asyncio
    async def test_match_reason_name_pattern(self):
        svc = _make_service(
            _odata_result(objects=[_prog_row(MATCH_REASON="NAME_PATTERN")], total_matches=1)
        )
        req = RepositorySearchRequest(name_patterns=["Z*"])
        resp = await svc.search(req)
        assert resp.objects[0].match_reason == MatchReason.NAME_PATTERN

    @pytest.mark.asyncio
    async def test_match_reason_both_preserved(self):
        svc = _make_service(
            _odata_result(objects=[_prog_row(MATCH_REASON="BOTH")], total_matches=1)
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        assert resp.objects[0].match_reason == MatchReason.BOTH

    @pytest.mark.asyncio
    async def test_duplicate_object_deduplication(self):
        """Same object returned by two OData calls → appears only once in result."""
        svc = _make_service_multi(
            [
                _odata_result(
                    objects=[_prog_row(OBJ_NAME="ZFI_REPORT", MATCH_REASON="PACKAGE")],
                    total_matches=1,
                ),
                _odata_result(
                    objects=[_prog_row(OBJ_NAME="ZFI_REPORT", MATCH_REASON="NAME_PATTERN")],
                    total_matches=1,
                ),
            ]
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"], name_patterns=["ZFI*"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 1
        names = [o.object_name for o in resp.objects]
        assert names.count("ZFI_REPORT") == 1

    @pytest.mark.asyncio
    async def test_package_pattern_merge_to_both(self):
        """Object in pkg call (PACKAGE) + pattern call (NAME_PATTERN) → BOTH."""
        svc = _make_service_multi(
            [
                _odata_result(
                    objects=[_prog_row(OBJ_NAME="ZFI_REPORT", MATCH_REASON="PACKAGE")],
                    total_matches=1,
                ),
                _odata_result(
                    objects=[_prog_row(OBJ_NAME="ZFI_REPORT", MATCH_REASON="NAME_PATTERN")],
                    total_matches=1,
                ),
            ]
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"], name_patterns=["ZFI*"])
        resp = await svc.search(req)
        assert resp.objects[0].match_reason == MatchReason.BOTH

    @pytest.mark.asyncio
    async def test_zero_results_valid_200(self):
        svc = _make_service(_odata_result(objects=[], total_matches=0))
        req = RepositorySearchRequest(packages=["ZEMPTY"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 0
        assert resp.objects == []
        assert resp.truncated is False

    @pytest.mark.asyncio
    async def test_truncated_result_flagged(self):
        rows = [_prog_row(OBJ_NAME=f"ZPROG_{i:03d}", MATCH_REASON="PACKAGE") for i in range(5)]
        svc = _make_service(
            _odata_result(objects=rows, truncated=True, total_matches=500)
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"], max_results=5)
        resp = await svc.search(req)
        assert resp.truncated is True

    @pytest.mark.asyncio
    async def test_max_results_cap_applied(self):
        """Returned objects are capped at max_results even if SAP returns more."""
        rows = [
            _prog_row(OBJ_NAME=f"ZPROG_{i:03d}", MATCH_REASON="PACKAGE")
            for i in range(10)
        ]
        svc = _make_service(_odata_result(objects=rows, total_matches=10))
        req = RepositorySearchRequest(packages=["ZFINANCE"], max_results=3)
        resp = await svc.search(req)
        assert resp.statistics.returned == 3
        assert resp.truncated is True

    @pytest.mark.asyncio
    async def test_total_matches_single_call(self):
        svc = _make_service(
            _odata_result(objects=[_prog_row()], total_matches=42, truncated=True)
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        assert resp.statistics.total_matches == 42

    @pytest.mark.asyncio
    async def test_optional_fields_missing_handled(self):
        """Row with minimal fields only → no crash, object returned."""
        minimal = {"OBJECT": "PROG", "OBJ_NAME": "ZPROG_MIN"}
        svc = _make_service(_odata_result(objects=[minimal]))
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 1
        obj = resp.objects[0]
        assert obj.sap_object_type == "PROG"
        assert obj.package is None
        assert obj.author is None
        assert obj.description is None

    @pytest.mark.asyncio
    async def test_unknown_object_type_handled_safely(self):
        """Unknown SAP type does not crash; object is still returned."""
        row = _prog_row(OBJECT="XXXX", OBJ_NAME="ZUNKNOWN_OBJ")
        svc = _make_service(_odata_result(objects=[row]))
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 1
        assert resp.objects[0].sap_object_type == "XXXX"

    @pytest.mark.asyncio
    async def test_row_missing_object_type_skipped(self):
        """Row with blank OBJECT field is dropped without crashing."""
        bad_row = {"OBJECT": "", "OBJ_NAME": "ZBAD"}
        good_row = _prog_row(OBJ_NAME="ZGOOD")
        svc = _make_service(_odata_result(objects=[bad_row, good_row]))
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 1
        assert resp.objects[0].object_name == "ZGOOD"

    @pytest.mark.asyncio
    async def test_row_missing_obj_name_skipped(self):
        """Row with blank OBJ_NAME is dropped without crashing."""
        bad_row = {"OBJECT": "PROG", "OBJ_NAME": ""}
        good_row = _prog_row(OBJ_NAME="ZGOOD2")
        svc = _make_service(_odata_result(objects=[bad_row, good_row]))
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        assert resp.statistics.returned == 1
        assert resp.objects[0].object_name == "ZGOOD2"

    @pytest.mark.asyncio
    async def test_all_custom_objects_injects_z_y_patterns(self):
        """all_custom_objects=True → Z* and Y* pattern calls are generated."""
        svc = _make_service_multi(
            [
                _odata_result(
                    objects=[_prog_row(OBJ_NAME="ZPROG_CUSTOM", MATCH_REASON="NAME_PATTERN")],
                    total_matches=1,
                ),
                _odata_result(
                    objects=[_prog_row(OBJ_NAME="YPROG_CUSTOM", MATCH_REASON="NAME_PATTERN")],
                    total_matches=1,
                ),
            ]
        )
        req = RepositorySearchRequest(all_custom_objects=True, object_types=["PROG"])
        resp = await svc.search(req)
        names = [o.object_name for o in resp.objects]
        assert "ZPROG_CUSTOM" in names
        assert "YPROG_CUSTOM" in names

    @pytest.mark.asyncio
    async def test_system_id_and_client_propagated(self):
        svc = _make_service(
            _odata_result(
                objects=[_prog_row()],
                system_id="NC1",
                client="800",
            )
        )
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        assert resp.system_id == "NC1"
        assert resp.client == "800"

    @pytest.mark.asyncio
    async def test_deterministic_sort_order(self):
        """Results are sorted by sap_object_type → object_name → package."""
        rows = [
            _prog_row(OBJ_NAME="ZPROG_B", MATCH_REASON="PACKAGE"),
            _clas_row(OBJ_NAME="ZCLAS_A", MATCH_REASON="PACKAGE"),
            _prog_row(OBJ_NAME="ZPROG_A", MATCH_REASON="PACKAGE"),
        ]
        svc = _make_service(_odata_result(objects=rows, total_matches=3))
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        resp = await svc.search(req)
        types_and_names = [(o.sap_object_type, o.object_name) for o in resp.objects]
        assert types_and_names == sorted(types_and_names)

    @pytest.mark.asyncio
    async def test_call_budget_exceeded_raises(self):
        """More packages × types than MAX_DISCOVERY_ODATA_CALLS → ValueError."""
        from app.services.discovery import MAX_DISCOVERY_ODATA_CALLS

        # Build request that requires more calls than the budget.
        # 50+ packages, one object type = 50+ plans → should trip the limit.
        many_packages = [f"ZPKG_{i:03d}" for i in range(MAX_DISCOVERY_ODATA_CALLS + 1)]
        conn = MagicMock()
        conn.discover_repository_objects = AsyncMock(return_value=_odata_result())
        svc = RepositoryDiscoveryService(conn)
        req = RepositorySearchRequest(packages=many_packages)
        with pytest.raises(ValueError, match="safe limit"):
            await svc.search(req)

    @pytest.mark.asyncio
    async def test_sap_error_propagated(self):
        """SAP provider errors bubble through the service layer."""
        from app.sap.exceptions import SAPCommunicationError

        conn = MagicMock()
        conn.discover_repository_objects = AsyncMock(
            side_effect=SAPCommunicationError("Gateway unreachable")
        )
        svc = RepositoryDiscoveryService(conn)
        req = RepositorySearchRequest(packages=["ZFINANCE"])
        with pytest.raises(SAPCommunicationError):
            await svc.search(req)

    @pytest.mark.asyncio
    async def test_multiple_types_multiple_packages(self):
        """2 packages × 2 object types = 4 OData calls, results aggregated."""
        svc = _make_service_multi(
            [
                _odata_result(objects=[_prog_row(OBJ_NAME="ZFI_PROG", DEVCLASS="ZFINANCE")]),
                _odata_result(objects=[_clas_row(OBJ_NAME="ZFI_CLAS", DEVCLASS="ZFINANCE")]),
                _odata_result(objects=[_prog_row(OBJ_NAME="ZPAY_PROG", DEVCLASS="ZPAYMENT")]),
                _odata_result(objects=[_clas_row(OBJ_NAME="ZPAY_CLAS", DEVCLASS="ZPAYMENT")]),
            ]
        )
        req = RepositorySearchRequest(
            packages=["ZFINANCE", "ZPAYMENT"],
            object_types=["PROG", "CLAS"],
        )
        resp = await svc.search(req)
        assert resp.statistics.returned == 4
        names = {o.object_name for o in resp.objects}
        assert names == {"ZFI_PROG", "ZFI_CLAS", "ZPAY_PROG", "ZPAY_CLAS"}
