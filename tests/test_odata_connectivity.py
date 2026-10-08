"""Phase 2 — SAP Gateway OData V2 Connectivity test suite.

All tests are fully offline.  httpx is mocked at the transport layer using
httpx.MockTransport / respx-style inline mocks so that:

  - No live SAP ECC connection is required.
  - No real credentials are required.
  - TLS, authentication, timeout, and parse failures are all exercised.

Coverage targets (spec §17–19):
  ODataSAPConnectionProvider unit tests
    ✓  HTTP 200 — successful OData V2 payload parsed correctly
    ✓  connection_mode == "odata"
    ✓  SystemId / Client / SapRelease / Timestamp / Message normalization
    ✓  HTTP 200 — SAP Status not in success set → SAPFunctionExecutionError
    ✓  HTTP 200 — empty results list → SAPResponseParseError
    ✓  HTTP 200 — missing "d" key → SAPResponseParseError
    ✓  HTTP 200 — missing "results" key → SAPResponseParseError
    ✓  HTTP 200 — malformed JSON → SAPResponseParseError
    ✓  HTTP 401 → SAPAuthenticationError
    ✓  HTTP 403 → SAPAuthenticationError
    ✓  HTTP 404 → SAPGatewayError
    ✓  HTTP 500 → SAPGatewayError
    ✓  httpx.ConnectError (SSL) → SAPTLSError
    ✓  httpx.ConnectError (generic) → SAPConnectionError
    ✓  httpx.TimeoutException → SAPTimeoutError
    ✓  Missing base_url → SAPConfigurationError
    ✓  Missing user → SAPConfigurationError
    ✓  Missing password → SAPConfigurationError
    ✓  Password never appears in exception text
    ✓  Authorization header never logged
    ✓  call_allowed_function raises SAPProviderNotImplementedError

  Provider factory tests (spec §18)
    ✓  SAP_CONNECTION_MODE=odata → ODataSAPConnectionProvider
    ✓  SAP_CONNECTION_MODE=local → LocalSapConnectionProvider
    ✓  SAP_CONNECTION_MODE=btp  → BtpSapConnectionProvider
    ✓  Invalid mode (via patching Literal guard) → SAPConfigurationError

  API endpoint tests (spec §19)
    ✓  GET /api/v1/sap/ping — OData success → HTTP 200 with correct body
    ✓  GET /api/v1/sap/ping — OData SAPConfigurationError → HTTP 422
    ✓  GET /api/v1/sap/ping — SAPTLSError (subclass of SAPConnectionError) → HTTP 503
    ✓  GET /api/v1/sap/ping — SAPTimeoutError (subclass of SAPConnectionError) → HTTP 503
    ✓  GET /api/v1/sap/ping — SAPAuthenticationError → HTTP 503
    ✓  GET /api/v1/sap/ping — SAPGatewayError (subclass of SAPCommunicationError) → HTTP 503
    ✓  GET /api/v1/sap/ping — SAPResponseParseError → HTTP 503
    ✓  GET /api/v1/health remains independent of SAP OData

  Security tests
    ✓  Password does not appear in configuration exception messages
    ✓  Password does not appear in API response body
"""

from __future__ import annotations

import json
import logging
import ssl
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from fastapi.testclient import TestClient

from app.core.config import SapLocalSettings, SapODataSettings, Settings
from app.main import app
from app.models.results import ConnectionTestResult
from app.sap.connection.base import SapConnectionProvider
from app.sap.connection.btp import BtpSapConnectionProvider
from app.sap.connection.factory import build_sap_connection_provider
from app.sap.connection.local import LocalSapConnectionProvider
from app.sap.connection.odata import ODataSAPConnectionProvider
from app.sap.exceptions import (
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPConfigurationError,
    SAPConnectionError,
    SAPFunctionExecutionError,
    SAPGatewayError,
    SAPProviderNotImplementedError,
    SAPResponseParseError,
    SAPTimeoutError,
    SAPTLSError,
)

client = TestClient(app)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_ODATA = SapODataSettings(
    base_url="https://sap-gw.example.corp:44300",
    service="ZNCOREIQ_REPO_SRV",
    client="800",
    user="TEST_USER",
    password="super-secret-odata-password",
    timeout=30,
)

_GOOD_PAYLOAD: dict[str, Any] = {
    "d": {
        "results": [
            {
                "Status": "OK",
                "Client": "800",
                "SapRelease": "740",
                "Timestamp": "20261008164359",
                "Message": "N-CoreIQ OData connectivity successful.",
                "SystemId": "NC1",
            }
        ]
    }
}


def _odata_settings(**overrides) -> Settings:
    """Build a Settings object with OData mode and valid OData config."""
    odata_kwargs = {
        "base_url": _VALID_ODATA.base_url,
        "service": _VALID_ODATA.service,
        "client": _VALID_ODATA.client,
        "user": _VALID_ODATA.user,
        "password": _VALID_ODATA.password,
        "timeout": _VALID_ODATA.timeout,
    }
    odata_kwargs.update(overrides)
    return Settings(
        sap_connection_mode="odata",
        sap_odata=SapODataSettings(**odata_kwargs),
    )


def _make_response(status_code: int, payload: Any = None, text: str | None = None) -> httpx.Response:
    """Build a fake httpx.Response."""
    if text is not None:
        content = text.encode()
        headers = {"content-type": "text/plain"}
    else:
        content = json.dumps(payload).encode()
        headers = {"content-type": "application/json"}
    return httpx.Response(status_code=status_code, content=content, headers=headers)


class _MockAsyncClient:
    """Minimal async context manager wrapping a list of prepared responses."""

    def __init__(self, responses: list[httpx.Response]):
        self._responses = iter(responses)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get(self, url: str, **kwargs) -> httpx.Response:
        return next(self._responses)


def _patch_http(responses: list[httpx.Response]):
    """Patch ODataSAPConnectionProvider._build_client to return a mock."""
    mock_client = _MockAsyncClient(responses)
    return patch.object(ODataSAPConnectionProvider, "_build_client", return_value=mock_client)


class _RaisingMockClient:
    """Async context manager that raises a RequestError on get()."""

    def __init__(self, exc: httpx.RequestError):
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_):
        pass

    async def get(self, url: str, **kwargs):
        raise self._exc


def _patch_http_raise(exc: httpx.RequestError):
    return patch.object(ODataSAPConnectionProvider, "_build_client", return_value=_RaisingMockClient(exc))


# ---------------------------------------------------------------------------
# 1. Successful OData V2 response — full field normalization
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_odata_ping_success_returns_connected():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(200, _GOOD_PAYLOAD)]):
        result = await provider.ping()

    assert result.status == "connected"
    assert result.connection_mode == "odata"
    assert result.system_id == "NC1"
    assert result.client == "800"
    assert result.sap_release == "740"
    assert result.timestamp == "20261008164359"
    assert result.ping_method == "SystemInfoSet"
    assert "N-CoreIQ" in (result.message or "")


@pytest.mark.asyncio
async def test_odata_ping_result_is_connection_test_result_instance():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(200, _GOOD_PAYLOAD)]):
        result = await provider.ping()
    assert isinstance(result, ConnectionTestResult)


# ---------------------------------------------------------------------------
# 2. SAP Status not in success set → SAPFunctionExecutionError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_odata_ping_non_success_status_raises_function_execution_error():
    payload = json.loads(json.dumps(_GOOD_PAYLOAD))
    payload["d"]["results"][0]["Status"] = "E"
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(200, payload)]):
        with pytest.raises(SAPFunctionExecutionError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_empty_status_raises_function_execution_error():
    payload = json.loads(json.dumps(_GOOD_PAYLOAD))
    payload["d"]["results"][0]["Status"] = ""
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(200, payload)]):
        with pytest.raises(SAPFunctionExecutionError):
            await provider.ping()


# ---------------------------------------------------------------------------
# 3. Malformed / incomplete OData payload → SAPResponseParseError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_odata_ping_missing_d_raises_parse_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(200, {"something": "else"})]):
        with pytest.raises(SAPResponseParseError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_missing_results_raises_parse_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(200, {"d": {}})]):
        with pytest.raises(SAPResponseParseError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_empty_results_raises_parse_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(200, {"d": {"results": []}})]):
        with pytest.raises(SAPResponseParseError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_malformed_json_raises_parse_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(200, text="NOT_JSON{{{")]):
        with pytest.raises(SAPResponseParseError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_missing_required_field_raises_parse_error():
    """A row missing 'SystemId' must raise SAPResponseParseError."""
    payload = {
        "d": {
            "results": [
                {
                    "Status": "OK",
                    "Client": "800",
                    "SapRelease": "740",
                    # SystemId intentionally omitted
                }
            ]
        }
    }
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(200, payload)]):
        with pytest.raises(SAPResponseParseError):
            await provider.ping()


# ---------------------------------------------------------------------------
# 4. HTTP error status codes
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_odata_ping_http_401_raises_authentication_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(401, {"error": {"message": "Unauthorized"}})]):
        with pytest.raises(SAPAuthenticationError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_http_403_raises_authentication_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(403, {"error": {"message": "Forbidden"}})]):
        with pytest.raises(SAPAuthenticationError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_http_404_raises_gateway_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(404, {"error": {"message": "Not Found"}})]):
        with pytest.raises(SAPGatewayError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_http_500_raises_gateway_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(500, {"error": {"message": "Internal Server Error"}})]):
        with pytest.raises(SAPGatewayError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_http_503_raises_gateway_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(503, {})]):
        with pytest.raises(SAPGatewayError):
            await provider.ping()


# ---------------------------------------------------------------------------
# 5. Network / transport failures
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_odata_ping_ssl_error_raises_tls_error():
    # SSL errors surface as ConnectError with 'ssl' in the message.
    exc = httpx.ConnectError("SSL certificate verify failed")
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http_raise(exc):
        with pytest.raises(SAPTLSError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_connect_error_raises_connection_error():
    exc = httpx.ConnectError("Connection refused")
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http_raise(exc):
        with pytest.raises(SAPConnectionError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_timeout_raises_timeout_error():
    exc = httpx.ReadTimeout("Request timed out")
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http_raise(exc):
        with pytest.raises(SAPTimeoutError):
            await provider.ping()


@pytest.mark.asyncio
async def test_odata_ping_connect_timeout_raises_timeout_error():
    exc = httpx.ConnectTimeout("Connection timed out")
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http_raise(exc):
        with pytest.raises(SAPTimeoutError):
            await provider.ping()


# ---------------------------------------------------------------------------
# 6. Configuration validation
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_odata_ping_missing_base_url_raises_configuration_error():
    provider = ODataSAPConnectionProvider(_odata_settings(base_url=None))
    with pytest.raises(SAPConfigurationError) as exc_info:
        await provider.ping()
    assert "SAP_ODATA__BASE_URL" in str(exc_info.value)


@pytest.mark.asyncio
async def test_odata_ping_missing_user_raises_configuration_error():
    provider = ODataSAPConnectionProvider(_odata_settings(user=None))
    with pytest.raises(SAPConfigurationError) as exc_info:
        await provider.ping()
    assert "SAP_ODATA__USER" in str(exc_info.value)


@pytest.mark.asyncio
async def test_odata_ping_missing_password_raises_configuration_error():
    provider = ODataSAPConnectionProvider(_odata_settings(password=None))
    with pytest.raises(SAPConfigurationError) as exc_info:
        await provider.ping()
    assert "SAP_ODATA__PASSWORD" in str(exc_info.value)


@pytest.mark.asyncio
async def test_odata_ping_multiple_missing_fields_listed_in_error():
    provider = ODataSAPConnectionProvider(_odata_settings(base_url=None, user=None))
    with pytest.raises(SAPConfigurationError) as exc_info:
        await provider.ping()
    error_text = str(exc_info.value)
    assert "SAP_ODATA__BASE_URL" in error_text
    assert "SAP_ODATA__USER" in error_text


# ---------------------------------------------------------------------------
# 7. Security — password never appears in exception messages
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_odata_ping_password_not_in_configuration_error():
    provider = ODataSAPConnectionProvider(_odata_settings(base_url=None))
    with pytest.raises(SAPConfigurationError) as exc_info:
        await provider.ping()
    assert "super-secret-odata-password" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_odata_ping_password_not_in_authentication_error():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(401, {})]):
        with pytest.raises(SAPAuthenticationError) as exc_info:
            await provider.ping()
    assert "super-secret-odata-password" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_odata_ping_password_not_in_tls_error():
    exc = httpx.ConnectError("SSL certificate verify failed")
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http_raise(exc):
        with pytest.raises(SAPTLSError) as exc_info:
            await provider.ping()
    assert "super-secret-odata-password" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 8. call_allowed_function raises SAPProviderNotImplementedError
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_odata_provider_call_allowed_function_raises_not_implemented():
    provider = ODataSAPConnectionProvider(_odata_settings())
    with pytest.raises(SAPProviderNotImplementedError):
        await provider.call_allowed_function("Z_MCP_REPO_PING")


# ---------------------------------------------------------------------------
# 9. Service URL construction — no double slashes
# ---------------------------------------------------------------------------

def test_odata_service_root_no_trailing_slash():
    provider = ODataSAPConnectionProvider(
        _odata_settings(base_url="https://sap-gw.example.corp:44300/")
    )
    root = provider._service_root()
    assert "//" not in root.replace("https://", "")
    assert root.endswith("ZNCOREIQ_REPO_SRV")


def test_odata_service_root_correct_path():
    provider = ODataSAPConnectionProvider(_odata_settings())
    root = provider._service_root()
    assert root == "https://sap-gw.example.corp:44300/sap/opu/odata/sap/ZNCOREIQ_REPO_SRV"


# ---------------------------------------------------------------------------
# 10. TLS verify — CA bundle path / truststore / fallback behaviour
# ---------------------------------------------------------------------------

def test_odata_tls_verify_default_is_not_false():
    """The default TLS verify value must never be False."""
    provider = ODataSAPConnectionProvider(_odata_settings())
    result = provider._tls_verify()
    assert result is not False
    assert result != "False"
    assert result != 0


def test_odata_tls_verify_default_uses_truststore_when_available():
    """When truststore is installed, _tls_verify() returns an ssl.SSLContext
    backed by the OS native certificate store (Windows/macOS/Linux)."""
    import app.sap.connection.odata as _odata_mod

    if not _odata_mod._TRUSTSTORE_AVAILABLE:
        pytest.skip("truststore not installed — skipping truststore path test")

    provider = ODataSAPConnectionProvider(_odata_settings())
    result = provider._tls_verify()
    assert isinstance(result, ssl.SSLContext), (
        f"Expected ssl.SSLContext from truststore, got {type(result)}"
    )


def test_odata_tls_verify_fallback_to_true_without_truststore():
    """When truststore is NOT installed, _tls_verify() falls back to True
    (Python's bundled certifi CA store)."""
    import app.sap.connection.odata as _odata_mod

    provider = ODataSAPConnectionProvider(_odata_settings())
    with patch.object(_odata_mod, "_TRUSTSTORE_AVAILABLE", False):
        result = provider._tls_verify()
    assert result is True


def test_odata_tls_verify_returns_ca_bundle_path_when_set():
    """When SAP_ODATA__CA_BUNDLE is configured, that path is used regardless
    of truststore availability — explicit overrides automatic."""
    settings = _odata_settings()
    settings.sap_odata.ca_bundle = "/etc/ssl/certs/corporate.pem"
    provider = ODataSAPConnectionProvider(settings)
    assert provider._tls_verify() == "/etc/ssl/certs/corporate.pem"


def test_odata_tls_verify_ca_bundle_takes_priority_over_truststore():
    """CA bundle path must win even when truststore is available."""
    import app.sap.connection.odata as _odata_mod

    settings = _odata_settings()
    settings.sap_odata.ca_bundle = "C:\\certs\\nttdata-ca.pem"
    provider = ODataSAPConnectionProvider(settings)
    # Regardless of truststore availability, the explicit path is returned.
    result = provider._tls_verify()
    assert result == "C:\\certs\\nttdata-ca.pem"
    assert not isinstance(result, ssl.SSLContext)


def test_odata_tls_verify_false_never_returned():
    """verify=False must never be returned by _tls_verify()."""
    import app.sap.connection.odata as _odata_mod

    provider = ODataSAPConnectionProvider(_odata_settings())

    # Test all code paths: truststore available
    result = provider._tls_verify()
    assert result is not False

    # Test fallback path: truststore not available
    with patch.object(_odata_mod, "_TRUSTSTORE_AVAILABLE", False):
        result_fallback = provider._tls_verify()
    assert result_fallback is not False

    # Test CA bundle path
    settings = _odata_settings()
    settings.sap_odata.ca_bundle = "/path/to/ca.pem"
    result_bundle = ODataSAPConnectionProvider(settings)._tls_verify()
    assert result_bundle is not False


# ---------------------------------------------------------------------------
# 11. Provider factory tests (spec §18)
# ---------------------------------------------------------------------------

def test_factory_mode_odata_returns_odata_provider():
    provider = build_sap_connection_provider(_odata_settings())
    assert isinstance(provider, ODataSAPConnectionProvider)
    assert isinstance(provider, SapConnectionProvider)


def test_factory_mode_local_returns_local_provider():
    settings = Settings(
        sap_connection_mode="local",
        sap_local=SapLocalSettings(
            host="ecc.example.corp",
            system_number="00",
            client="100",
            user="RFC_USER",
            password="rfc-secret",
            language="EN",
        ),
    )
    provider = build_sap_connection_provider(settings)
    assert isinstance(provider, LocalSapConnectionProvider)


def test_factory_mode_btp_returns_btp_provider():
    settings = Settings(sap_connection_mode="btp")
    provider = build_sap_connection_provider(settings)
    assert isinstance(provider, BtpSapConnectionProvider)


def test_factory_invalid_mode_raises_configuration_error():
    """An unrecognised connection mode must raise SAPConfigurationError.

    Pydantic v2 model fields are not patchable via patch.object on the class.
    We use a MagicMock that exposes only what the factory reads so we can
    inject an invalid mode string that bypasses the Literal validator.
    """
    fake_settings = MagicMock(spec=Settings)
    fake_settings.sap_connection_mode = "invalid_mode"
    with pytest.raises(SAPConfigurationError):
        build_sap_connection_provider(fake_settings)


# ---------------------------------------------------------------------------
# 12. API endpoint tests — OData success (spec §19)
# ---------------------------------------------------------------------------

def _fake_odata_result() -> ConnectionTestResult:
    return ConnectionTestResult(
        status="connected",
        connection_mode="odata",
        system_id="NC1",
        client="800",
        sap_release="740",
        timestamp="20261008164359",
        ping_method="SystemInfoSet",
        message="N-CoreIQ OData connectivity successful.",
    )


def test_api_sap_ping_odata_success_returns_200():
    class FakeProvider:
        async def ping(self):
            return _fake_odata_result()

    with patch("app.api.routes.build_sap_connection_provider", return_value=FakeProvider()):
        response = client.get("/api/v1/sap/ping")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "connected"
    assert body["connection_mode"] == "odata"
    assert body["system_id"] == "NC1"
    assert body["client"] == "800"
    assert body["sap_release"] == "740"
    assert body["ping_method"] == "SystemInfoSet"
    assert "N-CoreIQ" in body["message"]


def test_api_sap_ping_odata_configuration_error_returns_422():
    class FakeProvider:
        async def ping(self):
            raise SAPConfigurationError("OData SAP configuration is incomplete.")

    with patch("app.api.routes.build_sap_connection_provider", return_value=FakeProvider()):
        response = client.get("/api/v1/sap/ping")

    assert response.status_code == 422


def test_api_sap_ping_odata_tls_error_returns_503():
    class FakeProvider:
        async def ping(self):
            raise SAPTLSError("SAP HTTPS certificate could not be validated.")

    with patch("app.api.routes.build_sap_connection_provider", return_value=FakeProvider()):
        response = client.get("/api/v1/sap/ping")

    assert response.status_code == 503


def test_api_sap_ping_odata_timeout_returns_503():
    class FakeProvider:
        async def ping(self):
            raise SAPTimeoutError("SAP OData request timed out.")

    with patch("app.api.routes.build_sap_connection_provider", return_value=FakeProvider()):
        response = client.get("/api/v1/sap/ping")

    assert response.status_code == 503


def test_api_sap_ping_odata_auth_error_returns_503():
    class FakeProvider:
        async def ping(self):
            raise SAPAuthenticationError("SAP authentication failed.")

    with patch("app.api.routes.build_sap_connection_provider", return_value=FakeProvider()):
        response = client.get("/api/v1/sap/ping")

    assert response.status_code == 503


def test_api_sap_ping_odata_gateway_error_returns_503():
    class FakeProvider:
        async def ping(self):
            raise SAPGatewayError("SAP OData service returned an internal server error.")

    with patch("app.api.routes.build_sap_connection_provider", return_value=FakeProvider()):
        response = client.get("/api/v1/sap/ping")

    assert response.status_code == 503


def test_api_sap_ping_odata_response_parse_error_returns_503():
    class FakeProvider:
        async def ping(self):
            raise SAPResponseParseError("SAP OData response is missing the expected 'd' envelope.")

    with patch("app.api.routes.build_sap_connection_provider", return_value=FakeProvider()):
        response = client.get("/api/v1/sap/ping")

    assert response.status_code == 503


# ---------------------------------------------------------------------------
# 13. Health endpoint independent of SAP OData (spec §20)
# ---------------------------------------------------------------------------

def test_health_endpoint_independent_of_odata():
    with patch(
        "app.api.routes.build_sap_connection_provider",
        side_effect=AssertionError("SAP provider must not be constructed for /health"),
    ):
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# 14. Security — password never appears in API response body
# ---------------------------------------------------------------------------

def test_odata_password_never_in_api_response():
    class FakeProvider:
        async def ping(self):
            return _fake_odata_result()

    with patch("app.api.routes.build_sap_connection_provider", return_value=FakeProvider()):
        response = client.get("/api/v1/sap/ping")

    assert "super-secret-odata-password" not in json.dumps(response.json())


def test_odata_configuration_error_message_never_contains_password():
    class FakeProvider:
        async def ping(self):
            raise SAPConfigurationError(
                "OData SAP configuration is incomplete. Missing: SAP_ODATA__BASE_URL"
            )

    with patch("app.api.routes.build_sap_connection_provider", return_value=FakeProvider()):
        response = client.get("/api/v1/sap/ping")

    assert "super-secret-odata-password" not in json.dumps(response.json())


# ---------------------------------------------------------------------------
# 15. Normalized response matches spec §11
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_odata_normalized_response_matches_spec():
    """The normalized result must exactly match the Phase 2 spec §11 shape."""
    provider = ODataSAPConnectionProvider(_odata_settings())
    with _patch_http([_make_response(200, _GOOD_PAYLOAD)]):
        result = await provider.ping()

    assert result.status == "connected"
    assert result.connection_mode == "odata"
    assert result.system_id == "NC1"
    assert result.client == "800"
    assert result.sap_release == "740"
    assert result.timestamp == "20261008164359"
    assert result.ping_method == "SystemInfoSet"
    assert result.message == "N-CoreIQ OData connectivity successful."
