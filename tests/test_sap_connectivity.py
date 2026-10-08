"""Phase 2 — SAP ECC Connectivity test suite.

All tests here are fully offline: no live ECC, no real SAP NetWeaver RFC SDK,
and no real credentials. The PyRFC adapter is mocked at the
app.sap.connection.local module boundary so LocalSapConnectionProvider logic
(allow-list enforcement, error mapping, result normalization) is exercised
without requiring the proprietary SDK to be installed.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.core.config import Settings, SapLocalSettings
from app.main import app
from app.sap.allowlist import ensure_allowed
from app.sap.connection.base import SapConnectionProvider
from app.sap.connection.btp import BtpSapConnectionProvider
from app.sap.connection.factory import build_sap_connection_provider
from app.sap.connection.local import LocalSapConnectionProvider
from app.sap.exceptions import (
    SAPAuthenticationError,
    SAPConfigurationError,
    SAPFunctionExecutionError,
    SAPProviderNotImplementedError,
    SAPRFCNotAllowedError,
    SAPRFCRuntimeUnavailableError,
)
from app.sap.rfc_runtime import RFCRuntimeStatus

client = TestClient(app)

VALID_LOCAL = SapLocalSettings(
    host="ecc-dev.example.corp",
    system_number="00",
    client="100",
    user="RFC_USER",
    password="super-secret-password",
    language="EN",
)


def _settings(sap_local: SapLocalSettings, mode: str = "local") -> Settings:
    return Settings(sap_connection_mode=mode, sap_local=sap_local)


class FakeConnectionSuccess:
    """Fake PyRFC connection that returns a successful Z_MCP_REPO_PING payload."""

    def __init__(self, **kwargs):
        self._kwargs = kwargs
        self.closed = False

    def call(self, function_name: str, **params):
        if function_name == "Z_MCP_REPO_PING":
            return {
                "EV_STATUS": "S",
                "EV_SYSID": "DEV",
                "EV_CLIENT": "100",
                "EV_SAP_RELEASE": "757",
                "EV_TIMESTAMP": "20260101120000",
                "EV_MESSAGE": "N-CoreIQ RFC connectivity successful.",
            }
        if function_name == "RFC_SYSTEM_INFO":
            return {"RFCSI_EXPORT": {"RFCSYSID": "DEV"}}
        raise AssertionError(f"unexpected function call: {function_name}")

    def close(self):
        self.closed = True


class FakeConnectionApplicationError:
    """Fake PyRFC connection whose call() raises a SAP application error."""

    def __init__(self, **kwargs):
        self.closed = False

    def call(self, function_name: str, **params):
        raise RuntimeError("Z_MCP_REPO_PING failed: object locked")

    def close(self):
        self.closed = True


class FakeConnectionAuthFailure:
    """Fake PyRFC connection whose construction raises a logon failure."""

    def __init__(self, **kwargs):
        raise RuntimeError("Logon failed: incorrect password for user RFC_USER")


class FakeConnectionCommunicationFailure:
    """Fake PyRFC connection whose construction raises a comms failure."""

    def __init__(self, **kwargs):
        raise RuntimeError("CommunicationError: partner not reachable")


class FakePyrfcModule:
    def __init__(self, connection_cls):
        self.Connection = connection_cls


def _patch_runtime_available(connection_cls):
    """Context manager helper patching the local provider's RFC runtime hooks."""
    return (
        patch(
            "app.sap.connection.local.get_rfc_runtime_status",
            return_value=RFCRuntimeStatus(available=True, detail="PyRFC runtime loaded."),
        ),
        patch(
            "app.sap.connection.local.get_pyrfc_module",
            return_value=FakePyrfcModule(connection_cls),
        ),
    )


# ---------------------------------------------------------------------------
# 1. Valid local configuration
# ---------------------------------------------------------------------------
def test_valid_local_configuration_passes_validation():
    provider = LocalSapConnectionProvider(_settings(VALID_LOCAL))
    # Should not raise.
    provider._require_configuration()


# ---------------------------------------------------------------------------
# 2-5. Missing required local configuration fields
# ---------------------------------------------------------------------------
@pytest.mark.parametrize(
    "field,missing_label",
    [
        ("host", "host"),
        ("client", "client"),
        ("user", "user"),
        ("password", "password"),
    ],
)
def test_missing_required_local_field_raises_configuration_error(field, missing_label):
    data = VALID_LOCAL.model_dump()
    data[field] = None
    incomplete = SapLocalSettings(**data)
    provider = LocalSapConnectionProvider(_settings(incomplete))

    with pytest.raises(SAPConfigurationError) as exc_info:
        provider._require_configuration()

    assert missing_label in str(exc_info.value)
    assert "super-secret-password" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 6. Missing RFC runtime
# ---------------------------------------------------------------------------
def test_missing_rfc_runtime_raises_controlled_error():
    provider = LocalSapConnectionProvider(_settings(VALID_LOCAL))
    with patch(
        "app.sap.connection.local.get_rfc_runtime_status",
        return_value=RFCRuntimeStatus(
            available=False, detail="SAP NetWeaver RFC SDK / PyRFC runtime is not available."
        ),
    ):
        with pytest.raises(SAPRFCRuntimeUnavailableError):
            provider._require_runtime()


# ---------------------------------------------------------------------------
# 7. Provider factory selects local provider
# ---------------------------------------------------------------------------
def test_factory_selects_local_provider_by_default():
    provider = build_sap_connection_provider(_settings(VALID_LOCAL, mode="local"))
    assert isinstance(provider, LocalSapConnectionProvider)
    assert isinstance(provider, SapConnectionProvider)


# ---------------------------------------------------------------------------
# 8. Provider factory handles BTP mode safely (no silent fallback to local)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_factory_handles_btp_mode_without_silent_fallback():
    provider = build_sap_connection_provider(_settings(VALID_LOCAL, mode="btp"))
    assert isinstance(provider, BtpSapConnectionProvider)
    assert not isinstance(provider, LocalSapConnectionProvider)

    with pytest.raises(SAPProviderNotImplementedError):
        await provider.ping()


# ---------------------------------------------------------------------------
# 9. Successful mocked Z_MCP_REPO_PING
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_successful_mocked_z_mcp_repo_ping():
    provider = LocalSapConnectionProvider(_settings(VALID_LOCAL))
    status_patch, module_patch = _patch_runtime_available(FakeConnectionSuccess)
    with status_patch, module_patch:
        result = await provider.ping()

    assert result.status == "connected"
    assert result.connection_mode == "local"
    assert result.system_id == "DEV"
    assert result.client == "100"
    assert result.ping_method == "Z_MCP_REPO_PING"
    assert "N-CoreIQ" in result.message


# ---------------------------------------------------------------------------
# 10. Failed mocked Z_MCP_REPO_PING (SAP application error)
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_failed_mocked_z_mcp_repo_ping_raises_function_execution_error():
    provider = LocalSapConnectionProvider(_settings(VALID_LOCAL))
    status_patch, module_patch = _patch_runtime_available(FakeConnectionApplicationError)
    with status_patch, module_patch:
        with pytest.raises(SAPFunctionExecutionError):
            await provider.ping()


# ---------------------------------------------------------------------------
# 11. Authentication / communication failure
# ---------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_authentication_failure_is_mapped_to_sap_authentication_error():
    provider = LocalSapConnectionProvider(_settings(VALID_LOCAL))
    status_patch, module_patch = _patch_runtime_available(FakeConnectionAuthFailure)
    with status_patch, module_patch:
        with pytest.raises(SAPAuthenticationError) as exc_info:
            await provider.ping()

    assert "super-secret-password" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_communication_failure_is_mapped_to_controlled_error():
    provider = LocalSapConnectionProvider(_settings(VALID_LOCAL))
    status_patch, module_patch = _patch_runtime_available(FakeConnectionCommunicationFailure)
    with status_patch, module_patch:
        with pytest.raises(Exception) as exc_info:
            await provider.ping()

    assert "super-secret-password" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 12-14. RFC allow-list enforcement
# ---------------------------------------------------------------------------
def test_allowlist_permits_z_mcp_repo_ping():
    ensure_allowed("Z_MCP_REPO_PING")  # must not raise


def test_allowlist_permits_rfc_system_info():
    ensure_allowed("RFC_SYSTEM_INFO")  # must not raise


def test_allowlist_rejects_unapproved_function():
    with pytest.raises(SAPRFCNotAllowedError):
        ensure_allowed("Z_SOME_RANDOM_FUNCTION")


@pytest.mark.asyncio
async def test_call_allowed_function_rejects_unapproved_name_before_sap_call():
    provider = LocalSapConnectionProvider(_settings(VALID_LOCAL))
    status_patch, module_patch = _patch_runtime_available(FakeConnectionSuccess)
    with status_patch, module_patch:
        with pytest.raises(SAPRFCNotAllowedError):
            await provider.call_allowed_function("Z_UNAPPROVED_FUNCTION")


# ---------------------------------------------------------------------------
# 15. Password never appears in exception text
# ---------------------------------------------------------------------------
def test_password_never_appears_in_configuration_exception_text():
    data = VALID_LOCAL.model_dump()
    data["password"] = None
    incomplete = SapLocalSettings(**data)
    provider = LocalSapConnectionProvider(_settings(incomplete))

    with pytest.raises(SAPConfigurationError) as exc_info:
        provider._require_configuration()

    assert "super-secret-password" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# 16. Password never appears in API response
# ---------------------------------------------------------------------------
def test_password_never_appears_in_sap_ping_api_response(monkeypatch):
    async def fake_build_provider_ping():
        from app.models.results import ConnectionTestResult

        return ConnectionTestResult(
            status="connected",
            connection_mode="local",
            system_id="DEV",
            client="100",
            sap_release="757",
            timestamp="20260101120000",
            ping_method="Z_MCP_REPO_PING",
            message="N-CoreIQ RFC connectivity successful.",
        )

    class FakeProvider:
        async def ping(self):
            return await fake_build_provider_ping()

    with patch(
        "app.api.routes.build_sap_connection_provider",
        return_value=FakeProvider(),
    ):
        response = client.get("/api/v1/sap/ping")

    assert response.status_code == 200
    body_text = json.dumps(response.json())
    assert "super-secret-password" not in body_text


# ---------------------------------------------------------------------------
# 17. /api/v1/health remains healthy without SAP
# ---------------------------------------------------------------------------
def test_health_endpoint_independent_of_sap():
    with patch(
        "app.api.routes.build_sap_connection_provider",
        side_effect=AssertionError("SAP provider must not be constructed for /health"),
    ):
        response = client.get("/api/v1/health")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


# ---------------------------------------------------------------------------
# 18. /api/v1/sap/ping returns correct normalized model
# ---------------------------------------------------------------------------
def test_sap_ping_endpoint_returns_normalized_model():
    from app.models.results import ConnectionTestResult

    class FakeProvider:
        async def ping(self):
            return ConnectionTestResult(
                status="connected",
                connection_mode="local",
                system_id="DEV",
                client="100",
                sap_release="757",
                timestamp="20260101120000",
                ping_method="Z_MCP_REPO_PING",
                message="N-CoreIQ RFC connectivity successful.",
            )

    with patch(
        "app.api.routes.build_sap_connection_provider",
        return_value=FakeProvider(),
    ):
        response = client.get("/api/v1/sap/ping")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "connected"
    assert body["connection_mode"] == "local"
    assert body["system_id"] == "DEV"
    assert body["client"] == "100"
    assert body["ping_method"] == "Z_MCP_REPO_PING"
    assert body["message"] == "N-CoreIQ RFC connectivity successful."


def test_sap_ping_endpoint_maps_configuration_error_to_422():
    class FakeProvider:
        async def ping(self):
            raise SAPConfigurationError("SAP local configuration incomplete: missing host.")

    with patch(
        "app.api.routes.build_sap_connection_provider",
        return_value=FakeProvider(),
    ):
        response = client.get("/api/v1/sap/ping")

    assert response.status_code == 422


def test_sap_ping_endpoint_maps_runtime_unavailable_to_501():
    class FakeProvider:
        async def ping(self):
            raise SAPRFCRuntimeUnavailableError("PyRFC / SAP NetWeaver RFC SDK not available.")

    with patch(
        "app.api.routes.build_sap_connection_provider",
        return_value=FakeProvider(),
    ):
        response = client.get("/api/v1/sap/ping")

    assert response.status_code == 501
