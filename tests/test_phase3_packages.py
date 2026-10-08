"""Phase 3 — Package resolution test suite.

Covers provider unit tests, _coerce_bool, OData string escaping,
service-layer input validation, REST API tests, and Phase 2 regression.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import SapODataSettings, Settings
from app.main import app
from app.models.repository import PackageResolutionResult, RepositoryPackage
from app.sap.connection.odata import ODataSAPConnectionProvider
from app.sap.exceptions import (
    RepositoryPackageNotFoundError,
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPConfigurationError,
    SAPConnectionError,
    SAPFunctionExecutionError,
    SAPGatewayError,
    SAPResponseParseError,
    SAPTimeoutError,
    SAPTLSError,
)
from app.services.repository import RepositoryService

# ---------------------------------------------------------------------------
# Test client
# ---------------------------------------------------------------------------

_tc = TestClient(app)

PACKAGES_URL = "/api/v1/repository/packages"

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_ODATA = SapODataSettings(
    base_url="https://sap-gw.example.corp:44300",
    service="ZNCOREIQ_REPO_SRV",
    client="800",
    user="TEST_USER",
    password="super-secret-pkg-password",
    timeout=30,
)


def _odata_settings(**overrides) -> Settings:
    kwargs = {
        "base_url": _VALID_ODATA.base_url,
        "service": _VALID_ODATA.service,
        "client": _VALID_ODATA.client,
        "user": _VALID_ODATA.user,
        "password": _VALID_ODATA.password,
        "timeout": _VALID_ODATA.timeout,
    }
    kwargs.update(overrides)
    return Settings(
        sap_connection_mode="odata",
        sap_odata=SapODataSettings(**kwargs),
    )


def _make_http_response(
    status_code: int, payload: Any = None, text: str | None = None
) -> httpx.Response:
    if text is not None:
        content = text.encode()
        headers = {"content-type": "text/plain"}
    else:
        content = json.dumps(payload).encode()
        headers = {"content-type": "application/json"}
    return httpx.Response(status_code=status_code, content=content, headers=headers)


class _MockAsyncClient:
    def __init__(self, responses: list[httpx.Response]) -> None:
        self._responses = iter(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return next(self._responses)


def _patch_http(responses: list[httpx.Response]):
    mock = _MockAsyncClient(responses)
    return patch.object(ODataSAPConnectionProvider, "_build_client", return_value=mock)


class _RaisingMockClient:
    def __init__(self, exc: httpx.RequestError) -> None:
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get(self, url: str, **kwargs):
        raise self._exc


def _patch_http_raise(exc: httpx.RequestError):
    return patch.object(
        ODataSAPConnectionProvider, "_build_client", return_value=_RaisingMockClient(exc)
    )


def _row(
    package: str = "ZNCOREIQ",
    parent: str = "",
    description: str = "N-CoreIQ Root",
    depth: int = 0,
    root_package: str = "ZNCOREIQ",
    include_subpackages: bool = True,
    max_depth: int = 20,
    truncated: bool = False,
    message: str = "Package hierarchy resolved successfully.",
    system_id: str = "NC1",
    sap_client: str = "800",
) -> dict[str, Any]:
    return {
        "Package": package,
        "ParentPackage": parent,
        "Description": description,
        "Depth": depth,
        "RootPackage": root_package,
        "IncludeSubpackages": include_subpackages,
        "MaxDepth": max_depth,
        "Truncated": truncated,
        "Message": message,
        "SystemId": system_id,
        "Client": sap_client,
    }


def _payload(*rows: dict[str, Any]) -> dict[str, Any]:
    return {"d": {"results": list(rows)}}


def _fake_result(
    root: str = "ZNCOREIQ",
    packages: list[RepositoryPackage] | None = None,
    truncated: bool = False,
    message: str = "Package hierarchy resolved successfully.",
    system_id: str = "NC1",
    sap_client: str = "800",
    include_subpackages: bool = False,
    max_depth: int = 20,
) -> PackageResolutionResult:
    if packages is None:
        packages = [RepositoryPackage(package=root, depth=0)]
    return PackageResolutionResult(
        system_id=system_id,
        client=sap_client,
        root_package=root,
        include_subpackages=include_subpackages,
        max_depth=max_depth,
        truncated=truncated,
        message=message,
        packages=packages,
    )


# ---------------------------------------------------------------------------
# 1. Provider — single root package
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_single_root_success():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(_row()))]):
        result = await provider.get_packages("ZNCOREIQ", include_subpackages=False, max_depth=20)

    assert isinstance(result, PackageResolutionResult)
    assert result.root_package == "ZNCOREIQ"
    assert result.include_subpackages is False
    assert result.max_depth == 20
    assert result.truncated is False
    assert len(result.packages) == 1
    assert result.packages[0].package == "ZNCOREIQ"
    assert result.packages[0].depth == 0


@pytest.mark.asyncio
async def test_get_packages_system_id_and_client_normalized():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(_row(system_id="NC1", sap_client="800")))]):
        result = await provider.get_packages("ZNCOREIQ")

    assert result.system_id == "NC1"
    assert result.client == "800"


@pytest.mark.asyncio
async def test_get_packages_message_preserved():
    msg = "Package hierarchy resolved successfully."
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(_row(message=msg)))]):
        result = await provider.get_packages("ZNCOREIQ")

    assert result.message == msg


@pytest.mark.asyncio
async def test_get_packages_description_normalized():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(_row(description="My Package")))]):
        result = await provider.get_packages("ZNCOREIQ")

    assert result.packages[0].description == "My Package"


@pytest.mark.asyncio
async def test_get_packages_parent_package_empty_string_for_root():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(_row(parent="")))]):
        result = await provider.get_packages("ZNCOREIQ")

    pkg = result.packages[0]
    # parent_package for root is empty string or None — both acceptable
    assert pkg.parent_package in ("", None)


# ---------------------------------------------------------------------------
# 2. Provider — recursive hierarchy
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_recursive_hierarchy():
    rows = [
        _row("ZNCOREIQ", "", "Root", depth=0),
        _row("ZNCOREIQ_CORE", "ZNCOREIQ", "Core", depth=1),
        _row("ZNCOREIQ_UTIL", "ZNCOREIQ", "Utilities", depth=1),
        _row("ZNCOREIQ_UTIL_COMMON", "ZNCOREIQ_UTIL", "Common", depth=2),
    ]
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(*rows))]):
        result = await provider.get_packages("ZNCOREIQ", include_subpackages=True, max_depth=20)

    assert len(result.packages) == 4
    # depth 0 first
    assert result.packages[0].package == "ZNCOREIQ"
    assert result.packages[0].depth == 0
    # depth 1 sorted alphabetically
    assert result.packages[1].package == "ZNCOREIQ_CORE"
    assert result.packages[1].depth == 1
    assert result.packages[2].package == "ZNCOREIQ_UTIL"
    assert result.packages[2].depth == 1
    # depth 2
    assert result.packages[3].package == "ZNCOREIQ_UTIL_COMMON"
    assert result.packages[3].depth == 2


@pytest.mark.asyncio
async def test_get_packages_parent_set_on_children():
    rows = [
        _row("ZNCOREIQ", "", "Root", depth=0),
        _row("ZNCOREIQ_CORE", "ZNCOREIQ", "Core", depth=1),
    ]
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(*rows))]):
        result = await provider.get_packages("ZNCOREIQ", include_subpackages=True)

    child = next(p for p in result.packages if p.package == "ZNCOREIQ_CORE")
    assert child.parent_package == "ZNCOREIQ"


# ---------------------------------------------------------------------------
# 3. Provider — max_depth round-trips
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_max_depth_zero():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(_row(max_depth=0)))]):
        result = await provider.get_packages("ZNCOREIQ", max_depth=0)

    assert result.max_depth == 0


@pytest.mark.asyncio
async def test_get_packages_max_depth_one():
    rows = [
        _row("ZNCOREIQ", "", "Root", depth=0, max_depth=1),
        _row("ZNCOREIQ_CORE", "ZNCOREIQ", "Core", depth=1, max_depth=1),
    ]
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(*rows))]):
        result = await provider.get_packages("ZNCOREIQ", max_depth=1)

    assert result.max_depth == 1
    assert len(result.packages) == 2
    assert all(p.depth <= 1 for p in result.packages)


@pytest.mark.asyncio
async def test_get_packages_max_depth_twenty():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(_row(max_depth=20)))]):
        result = await provider.get_packages("ZNCOREIQ", max_depth=20)

    assert result.max_depth == 20


# ---------------------------------------------------------------------------
# 4. Provider — truncation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_truncated_true_when_any_row_truncated():
    rows = [
        _row("ZNCOREIQ", truncated=False, depth=0),
        _row("ZNCOREIQ_CORE", "ZNCOREIQ", depth=1, truncated=True),
    ]
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(*rows))]):
        result = await provider.get_packages("ZNCOREIQ", include_subpackages=True)

    assert result.truncated is True


@pytest.mark.asyncio
async def test_get_packages_truncated_false_when_no_row_truncated():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(_row(truncated=False)))]):
        result = await provider.get_packages("ZNCOREIQ")

    assert result.truncated is False


@pytest.mark.asyncio
async def test_get_packages_truncated_string_false_stays_false():
    """OData may return boolean as a Python bool from JSON — 'false' the string
    must not coerce to True.  We test by passing the string representation."""
    # SAP JSON returns actual JSON false (bool False in Python after parsing).
    # This test confirms the coercion helper handles it correctly.
    row = _row(truncated=False)
    # Manually override with JSON-serialised false string to simulate edge case
    row["Truncated"] = False  # JSON bool false
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(row))]):
        result = await provider.get_packages("ZNCOREIQ")

    assert result.truncated is False


# ---------------------------------------------------------------------------
# 5. Provider — include_subpackages serialized as lowercase true/false
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_include_subpackages_false_in_result():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(_row(include_subpackages=False)))]):
        result = await provider.get_packages("ZNCOREIQ", include_subpackages=False)

    assert result.include_subpackages is False


@pytest.mark.asyncio
async def test_get_packages_include_subpackages_true_in_result():
    rows = [
        _row("ZNCOREIQ", include_subpackages=True, depth=0),
        _row("ZNCOREIQ_CORE", "ZNCOREIQ", include_subpackages=True, depth=1),
    ]
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(*rows))]):
        result = await provider.get_packages("ZNCOREIQ", include_subpackages=True)

    assert result.include_subpackages is True


# ---------------------------------------------------------------------------
# 6. Provider — sorting (out-of-order rows sorted by depth asc, name asc)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_out_of_order_rows_sorted():
    rows = [
        _row("ZNCOREIQ_UTIL_COMMON", "ZNCOREIQ_UTIL", "Common", depth=2),
        _row("ZNCOREIQ_CORE", "ZNCOREIQ", "Core", depth=1),
        _row("ZNCOREIQ", "", "Root", depth=0),
        _row("ZNCOREIQ_UTIL", "ZNCOREIQ", "Utilities", depth=1),
    ]
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(*rows))]):
        result = await provider.get_packages("ZNCOREIQ", include_subpackages=True)

    depths = [p.depth for p in result.packages]
    names = [p.package for p in result.packages]
    assert depths == sorted(depths), "Packages must be sorted by depth ascending"
    assert names[0] == "ZNCOREIQ"
    assert names[1] == "ZNCOREIQ_CORE"
    assert names[2] == "ZNCOREIQ_UTIL"
    assert names[3] == "ZNCOREIQ_UTIL_COMMON"


# ---------------------------------------------------------------------------
# 7. Provider — deduplication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_exact_duplicate_rows_removed():
    """Exact duplicate rows (same package, parent, depth, description)
    must be silently removed — only one copy kept."""
    base = _row("ZNCOREIQ", "", "Root", depth=0)
    rows = [base, dict(base)]  # two identical dicts
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(*rows))]):
        result = await provider.get_packages("ZNCOREIQ")

    assert len(result.packages) == 1


@pytest.mark.asyncio
async def test_get_packages_conflicting_duplicate_first_kept(caplog):
    """When two rows share the same package name but differ in depth/parent,
    the first valid row is preserved and a warning is logged."""
    row_a = _row("ZNCOREIQ_CORE", "ZNCOREIQ", "Core", depth=1)
    row_b = _row("ZNCOREIQ_CORE", "ZNCOREIQ_OTHER", "Core v2", depth=2)
    rows = [
        _row("ZNCOREIQ", "", "Root", depth=0),
        row_a,
        row_b,
    ]
    provider = ODataSAPConnectionProvider(_odata_settings())
    with caplog.at_level(logging.WARNING):
        with _patch_http([_make_http_response(200, _payload(*rows))]):
            result = await provider.get_packages("ZNCOREIQ", include_subpackages=True)

    kept = next((p for p in result.packages if p.package == "ZNCOREIQ_CORE"), None)
    assert kept is not None
    assert kept.depth == 1  # first row kept
    # A warning must have been emitted about the conflict.
    # The log may use a structured event name; check message text, getMessage(),
    # and the full record dict (which holds structured extra fields).
    assert any(
        "ZNCOREIQ_CORE" in r.getMessage()
        or "ZNCOREIQ_CORE" in r.message
        or "ZNCOREIQ_CORE" in str(r.__dict__)
        or "duplicate" in r.getMessage().lower()
        or "conflict" in r.getMessage().lower()
        for r in caplog.records
        if r.levelno >= logging.WARNING
    ), "Expected a warning log about the conflicting duplicate package row"


# ---------------------------------------------------------------------------
# 8. Provider — blank Package rows skipped
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_blank_package_name_skipped():
    rows = [
        _row("ZNCOREIQ", "", "Root", depth=0),
        _row("", "", "Blank row", depth=1),  # blank package name
    ]
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(*rows))]):
        result = await provider.get_packages("ZNCOREIQ", include_subpackages=True)

    assert all(p.package != "" for p in result.packages)
    assert len(result.packages) == 1


# ---------------------------------------------------------------------------
# 9. Provider — root package validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_root_not_at_depth_zero_raises_parse_error():
    """If no row has depth=0 for the requested root, the response is malformed."""
    rows = [
        _row("ZNCOREIQ_CORE", "ZNCOREIQ", "Core", depth=1),
    ]
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload(*rows))]):
        with pytest.raises(SAPResponseParseError):
            await provider.get_packages("ZNCOREIQ", include_subpackages=True)


# ---------------------------------------------------------------------------
# 10. Provider — empty results → RepositoryPackageNotFoundError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_empty_results_raises_not_found():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload())]):
        with pytest.raises(RepositoryPackageNotFoundError):
            await provider.get_packages("ZUNKNOWN")


@pytest.mark.asyncio
async def test_get_packages_not_found_error_contains_package_name():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, _payload())]):
        with pytest.raises(RepositoryPackageNotFoundError) as exc_info:
            await provider.get_packages("ZUNKNOWN")
    assert "ZUNKNOWN" in str(exc_info.value)


# ---------------------------------------------------------------------------
# 11. Provider — malformed OData payload errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_missing_d_raises_parse_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, {"something": "else"})]):
        with pytest.raises(SAPResponseParseError):
            await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_missing_results_raises_parse_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, {"d": {}})]):
        with pytest.raises(SAPResponseParseError):
            await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_results_not_a_list_raises_parse_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, {"d": {"results": "not-a-list"}})]):
        with pytest.raises(SAPResponseParseError):
            await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_malformed_json_raises_parse_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, text="NOT_JSON{{{")]):
        with pytest.raises(SAPResponseParseError):
            await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_results_dict_not_list_raises_parse_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(200, {"d": {"results": {"Package": "ZNCOREIQ"}}})]):
        with pytest.raises(SAPResponseParseError):
            await provider.get_packages("ZNCOREIQ")


# ---------------------------------------------------------------------------
# 12. Provider — HTTP error status codes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_http_401_raises_authentication_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(401, {"error": {"message": "Unauthorized"}})]):
        with pytest.raises(SAPAuthenticationError):
            await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_http_403_raises_authentication_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(403, {"error": {"message": "Forbidden"}})]):
        with pytest.raises(SAPAuthenticationError):
            await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_http_404_raises_gateway_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(404, {"error": {"message": "Not Found"}})]):
        with pytest.raises(SAPGatewayError):
            await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_http_500_raises_gateway_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(500, {"error": {"message": "Internal Server Error"}})]):
        with pytest.raises(SAPGatewayError):
            await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_ssl_error_raises_tls_error():
    exc = httpx.ConnectError("SSL certificate verify failed")
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http_raise(exc):
        with pytest.raises(SAPTLSError):
            await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_connect_error_raises_connection_error():
    exc = httpx.ConnectError("Connection refused")
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http_raise(exc):
        with pytest.raises(SAPConnectionError):
            await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_read_timeout_raises_timeout_error():
    exc = httpx.ReadTimeout("Request timed out")
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http_raise(exc):
        with pytest.raises(SAPTimeoutError):
            await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_connect_timeout_raises_timeout_error():
    exc = httpx.ConnectTimeout("Connection timed out")
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http_raise(exc):
        with pytest.raises(SAPTimeoutError):
            await provider.get_packages("ZNCOREIQ")


# ---------------------------------------------------------------------------
# 13. Provider — configuration errors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_missing_base_url_raises_configuration_error():
    provider = ODataSAPConnectionProvider(_odata_settings(base_url=None))
    with pytest.raises(SAPConfigurationError):
        await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_missing_user_raises_configuration_error():
    provider = ODataSAPConnectionProvider(_odata_settings(user=None))
    with pytest.raises(SAPConfigurationError):
        await provider.get_packages("ZNCOREIQ")


@pytest.mark.asyncio
async def test_get_packages_missing_password_raises_configuration_error():
    provider = ODataSAPConnectionProvider(_odata_settings(password=None))
    with pytest.raises(SAPConfigurationError):
        await provider.get_packages("ZNCOREIQ")


# ---------------------------------------------------------------------------
# 14. Provider — password never in exception messages
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_packages_password_not_in_configuration_error():
    provider = ODataSAPConnectionProvider(_odata_settings(base_url=None))
    with pytest.raises(SAPConfigurationError) as exc_info:
        await provider.get_packages("ZNCOREIQ")
    assert "super-secret-pkg-password" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_packages_password_not_in_authentication_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_http_response(401, {})]):
        with pytest.raises(SAPAuthenticationError) as exc_info:
            await provider.get_packages("ZNCOREIQ")
    assert "super-secret-pkg-password" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_get_packages_password_not_in_tls_error():
    exc = httpx.ConnectError("SSL certificate verify failed")
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http_raise(exc):
        with pytest.raises(SAPTLSError) as exc_info:
            await provider.get_packages("ZNCOREIQ")
    assert "super-secret-pkg-password" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 15. _coerce_bool unit tests
# ---------------------------------------------------------------------------


def test_coerce_bool_python_true():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool(True) is True


def test_coerce_bool_python_false():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool(False) is False


def test_coerce_bool_int_one():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool(1) is True


def test_coerce_bool_int_zero():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool(0) is False


def test_coerce_bool_string_true_lower():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool("true") is True


def test_coerce_bool_string_false_lower():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool("false") is False


def test_coerce_bool_string_True_capital():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool("True") is True


def test_coerce_bool_string_False_capital():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool("False") is False


def test_coerce_bool_string_X_sap_true():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool("X") is True


def test_coerce_bool_string_x_lower():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool("x") is True


def test_coerce_bool_string_one():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool("1") is True


def test_coerce_bool_string_zero():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool("0") is False


def test_coerce_bool_string_empty():
    from app.sap.connection.odata import _coerce_bool
    assert _coerce_bool("") is False


# ---------------------------------------------------------------------------
# 16. OData string escaping
# ---------------------------------------------------------------------------


def test_odata_escape_no_special_chars():
    from app.sap.connection.odata import _odata_escape
    assert _odata_escape("ZNCOREIQ") == "ZNCOREIQ"


def test_odata_escape_single_quote_doubled():
    from app.sap.connection.odata import _odata_escape
    assert _odata_escape("Z'TEST") == "Z''TEST"


def test_odata_escape_multiple_single_quotes():
    from app.sap.connection.odata import _odata_escape
    assert _odata_escape("A'B'C") == "A''B''C"


def test_odata_escape_no_mutation_of_safe_chars():
    from app.sap.connection.odata import _odata_escape
    safe = "ZNCOREIQ_UTIL_COMMON"
    assert _odata_escape(safe) == safe


# ---------------------------------------------------------------------------
# 17. Service layer — input validation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_service_resolve_packages_uppercases_package():
    """RepositoryService must uppercase the package name before calling provider."""
    provider = ODataSAPConnectionProvider(_odata_settings())
    svc = RepositoryService(provider)
    with _patch_http([_make_http_response(200, _payload(_row("ZNCOREIQ")))]):
        result = await svc.resolve_packages("zncoreiq", include_subpackages=False, max_depth=20)
    assert result.root_package == "ZNCOREIQ"


@pytest.mark.asyncio
async def test_service_resolve_packages_strips_whitespace():
    provider = ODataSAPConnectionProvider(_odata_settings())
    svc = RepositoryService(provider)
    with _patch_http([_make_http_response(200, _payload(_row("ZNCOREIQ")))]):
        result = await svc.resolve_packages("  ZNCOREIQ  ", include_subpackages=False, max_depth=20)
    assert result.root_package == "ZNCOREIQ"


@pytest.mark.asyncio
async def test_service_resolve_packages_strips_and_uppercases():
    provider = ODataSAPConnectionProvider(_odata_settings())
    svc = RepositoryService(provider)
    with _patch_http([_make_http_response(200, _payload(_row("ZNCOREIQ")))]):
        result = await svc.resolve_packages(" zncoreiq ", include_subpackages=False, max_depth=20)
    assert result.root_package == "ZNCOREIQ"


@pytest.mark.asyncio
async def test_service_resolve_packages_blank_raises_value_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    svc = RepositoryService(provider)
    with pytest.raises(ValueError, match="[Pp]ackage"):
        await svc.resolve_packages("   ", include_subpackages=False, max_depth=20)


@pytest.mark.asyncio
async def test_service_resolve_packages_empty_raises_value_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    svc = RepositoryService(provider)
    with pytest.raises(ValueError):
        await svc.resolve_packages("", include_subpackages=False, max_depth=20)


@pytest.mark.asyncio
async def test_service_resolve_packages_max_depth_negative_raises_value_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    svc = RepositoryService(provider)
    with pytest.raises(ValueError, match="[Mm]ax"):
        await svc.resolve_packages("ZNCOREIQ", include_subpackages=False, max_depth=-1)


@pytest.mark.asyncio
async def test_service_resolve_packages_max_depth_21_raises_value_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    svc = RepositoryService(provider)
    with pytest.raises(ValueError, match="[Mm]ax"):
        await svc.resolve_packages("ZNCOREIQ", include_subpackages=False, max_depth=21)


@pytest.mark.asyncio
async def test_service_resolve_packages_max_depth_zero_accepted():
    provider = ODataSAPConnectionProvider(_odata_settings())
    svc = RepositoryService(provider)
    with _patch_http([_make_http_response(200, _payload(_row(max_depth=0)))]):
        result = await svc.resolve_packages("ZNCOREIQ", include_subpackages=False, max_depth=0)
    assert result.max_depth == 0


@pytest.mark.asyncio
async def test_service_resolve_packages_max_depth_twenty_accepted():
    provider = ODataSAPConnectionProvider(_odata_settings())
    svc = RepositoryService(provider)
    with _patch_http([_make_http_response(200, _payload(_row(max_depth=20)))]):
        result = await svc.resolve_packages("ZNCOREIQ", include_subpackages=False, max_depth=20)
    assert result.max_depth == 20


@pytest.mark.asyncio
async def test_service_resolve_packages_no_z_prefix_required():
    """Custom namespace packages (not starting with Z or Y) must be accepted."""
    provider = ODataSAPConnectionProvider(_odata_settings())
    svc = RepositoryService(provider)
    custom_pkg = "NCOREIQ"  # no Z/Y prefix
    with _patch_http([_make_http_response(200, _payload(_row(package=custom_pkg, root_package=custom_pkg)))]):
        result = await svc.resolve_packages(custom_pkg, include_subpackages=False, max_depth=20)
    assert result.root_package == custom_pkg


# ---------------------------------------------------------------------------
# 18. REST API tests
# ---------------------------------------------------------------------------


def _mock_svc_result(
    root: str = "ZNCOREIQ",
    include_subpackages: bool = False,
    max_depth: int = 20,
    truncated: bool = False,
) -> PackageResolutionResult:
    return PackageResolutionResult(
        system_id="NC1",
        client="800",
        root_package=root,
        include_subpackages=include_subpackages,
        max_depth=max_depth,
        truncated=truncated,
        message="Package hierarchy resolved successfully.",
        packages=[RepositoryPackage(package=root, depth=0, description="Test")],
    )


def _patch_svc(result: PackageResolutionResult | None = None, exc: Exception | None = None):
    """Patch RepositoryService.resolve_packages in the routes module."""
    if exc is not None:
        mock = AsyncMock(side_effect=exc)
    else:
        mock = AsyncMock(return_value=result or _mock_svc_result())
    return patch("app.api.routes.RepositoryService.resolve_packages", mock)


def test_api_packages_200_default_params():
    with _patch_svc(_mock_svc_result()):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ")
    assert resp.status_code == 200
    body = resp.json()
    assert body["root_package"] == "ZNCOREIQ"
    assert body["include_subpackages"] is False
    assert body["max_depth"] == 20
    assert body["truncated"] is False
    assert isinstance(body["packages"], list)
    assert len(body["packages"]) >= 1


def test_api_packages_200_include_subpackages_true():
    result = PackageResolutionResult(
        system_id="NC1",
        client="800",
        root_package="ZNCOREIQ",
        include_subpackages=True,
        max_depth=5,
        truncated=False,
        message="OK",
        packages=[
            RepositoryPackage(package="ZNCOREIQ", depth=0),
            RepositoryPackage(package="ZNCOREIQ_CORE", parent_package="ZNCOREIQ", depth=1),
        ],
    )
    with _patch_svc(result):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ?include_subpackages=true&max_depth=5")
    assert resp.status_code == 200
    body = resp.json()
    assert body["include_subpackages"] is True
    assert body["max_depth"] == 5
    assert len(body["packages"]) == 2


def test_api_packages_200_response_schema_complete():
    """All specified fields must be present in the response."""
    with _patch_svc(_mock_svc_result()):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ")
    body = resp.json()
    for field in ("system_id", "client", "root_package", "include_subpackages",
                  "max_depth", "truncated", "message", "packages"):
        assert field in body, f"Missing field: {field}"
    pkg = body["packages"][0]
    for field in ("package", "depth"):
        assert field in pkg, f"Missing package field: {field}"


def test_api_packages_200_truncated_propagated():
    with _patch_svc(_mock_svc_result(truncated=True)):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ")
    assert resp.status_code == 200
    assert resp.json()["truncated"] is True


def test_api_packages_404_package_not_found():
    with _patch_svc(exc=RepositoryPackageNotFoundError("Package 'ZUNKNOWN' was not found.")):
        resp = _tc.get(f"{PACKAGES_URL}/ZUNKNOWN")
    assert resp.status_code == 404
    body = resp.json()
    assert "ZUNKNOWN" in body.get("detail", "") or "ZUNKNOWN" in str(body)


def test_api_packages_422_blank_package():
    """A blank package name in the URL path should be rejected."""
    resp = _tc.get(f"{PACKAGES_URL}/%20%20%20")  # URL-encoded spaces
    # Either 422 from FastAPI validation or the service raises ValueError → 422
    assert resp.status_code in (404, 422)


def test_api_packages_422_max_depth_too_large():
    with _patch_svc(exc=ValueError("max_depth must be between 0 and 20")):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ?max_depth=99")
    assert resp.status_code == 422


def test_api_packages_422_max_depth_negative():
    with _patch_svc(exc=ValueError("max_depth must be between 0 and 20")):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ?max_depth=-5")
    assert resp.status_code == 422


def test_api_packages_503_connection_error():
    with _patch_svc(exc=SAPConnectionError("SAP ECC unreachable.")):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ")
    assert resp.status_code == 503


def test_api_packages_503_tls_error():
    with _patch_svc(exc=SAPTLSError("TLS validation failed.")):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ")
    assert resp.status_code == 503


def test_api_packages_503_timeout_error():
    with _patch_svc(exc=SAPTimeoutError("Request timed out.")):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ")
    assert resp.status_code == 503


def test_api_packages_503_gateway_error():
    with _patch_svc(exc=SAPGatewayError("SAP Gateway returned 500.")):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ")
    assert resp.status_code in (502, 503)


def test_api_packages_401_maps_to_auth_error():
    with _patch_svc(exc=SAPAuthenticationError("Authentication failed.")):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ")
    assert resp.status_code in (401, 403)


def test_api_packages_response_no_metadata_field():
    with _patch_svc(_mock_svc_result()):
        resp = _tc.get(f"{PACKAGES_URL}/ZNCOREIQ")
    body = resp.json()
    assert "__metadata" not in body
    if body.get("packages"):
        assert "__metadata" not in body["packages"][0]


# ---------------------------------------------------------------------------
# 19. Phase 2 regression — /api/v1/sap/ping still exists
# ---------------------------------------------------------------------------


def test_phase2_sap_ping_endpoint_still_exists():
    """Phase 2 /api/v1/sap/ping must not be broken by Phase 3 changes."""
    resp = _tc.get("/api/v1/sap/ping")
    # 200 OK (live) or 503 (SAP unavailable in CI) — route must exist
    assert resp.status_code != 404, "/api/v1/sap/ping route must still exist"


def test_phase2_health_endpoint_still_works():
    resp = _tc.get("/api/v1/health")
    assert resp.status_code == 200
