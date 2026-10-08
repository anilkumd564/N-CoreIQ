"""Foundation tests for Phase 1 (Version 0.1) stability.

These tests validate the application foundation independent of any live
SAP ECC system. They must remain fast, deterministic, and offline-safe.
"""

import json

from fastapi.testclient import TestClient

from app.core.config import Settings, get_settings
from app.main import app
from app.models.requests import RepositorySearchRequest

client = TestClient(app)


def test_health_endpoint_returns_200():
    """1. Health endpoint returns HTTP 200."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_health_response_contains_expected_fields():
    """2. Health response contains expected application fields."""
    response = client.get("/api/v1/health")
    body = response.json()
    assert body["status"] == "ok"
    assert body["app"] == "SAP Repository Intelligence Layer"
    assert "version" in body
    assert body["environment"] == "local"


def test_application_loads_in_local_mode():
    """3. Application loads with a recognized connection mode (local or odata).

    OData is now the preferred connection mode for ECC connectivity via SAP
    Gateway.  The .env file may legitimately set SAP_CONNECTION_MODE=odata,
    so this test accepts both 'local' and 'odata' as valid startup modes.
    """
    settings = get_settings()
    assert settings.app_env == "local"
    assert settings.sap_connection_mode in ("local", "odata")


def test_repository_search_request_accepts_valid_input():
    """4. Repository search request model accepts valid input."""
    request = RepositorySearchRequest(
        packages=["ZFINANCE"],
        include_subpackages=True,
        name_patterns=["Z*", "Y*"],
        object_types=["PROG", "CLAS"],
        max_results=100,
    )
    assert request.packages == ["ZFINANCE"]
    assert request.max_results == 100


def test_repository_search_request_rejects_invalid_input():
    """5. Repository search request rejects structurally invalid input."""
    import pytest
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        RepositorySearchRequest(max_results=0)

    with pytest.raises(ValidationError):
        RepositorySearchRequest(max_results=100000)


def test_configuration_serialization_does_not_expose_secrets():
    """6. Application does not expose secrets through configuration serialization.

    The live `Settings` object may legitimately hold a real SAP password in
    memory (required to perform RFC calls), but any serialization of that
    configuration - model_dump(), repr(), str() - must redact it. This test
    verifies redaction holds even when a real credential is configured.
    """
    settings = Settings()
    real_password = settings.sap_local.password

    dumped = settings.model_dump()
    serialized = json.dumps(dumped, default=str)

    if real_password:
        # The real credential must never appear in any serialized form.
        assert real_password not in serialized
        assert real_password not in repr(settings.sap_local)
        assert real_password not in str(settings.sap_local)
        assert dumped["sap_local"]["password"] == "***"
    else:
        assert dumped["sap_local"]["password"] in (None, "")


def test_application_startup_does_not_require_ecc_system():
    """7. Application startup does not require an available ECC system."""
    # The TestClient already instantiated the app without any live SAP system.
    # A successful health check confirms the app is usable without ECC.
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_unimplemented_sap_capability_fails_gracefully():
    """8. Phase 3 repository search responds with a controlled status code.

    Phase 3 fully implements POST /api/v1/repository/search, so a 501
    (Not Implemented) is no longer the expected response.  Depending on
    runtime configuration the endpoint will return one of:

      200 -- SAP is reachable; objects found or zero results.
      422 -- Service-layer validation error (e.g. unsupported type, budget
             exceeded, or SAP returned a structured error mapped to ValueError).
      503 -- SAP is configured but unreachable (network, TLS, timeout, auth).

    The invariants that must ALWAYS hold regardless of SAP availability:
      - No raw 500 with an unhandled stack trace.
      - No actual credential value appears in the response body.
    """
    payload = {
        "packages": ["ZFINANCE"],
        "include_subpackages": True,
        "name_patterns": ["Z*", "Y*"],
        "object_types": ["PROG", "CLAS"],
        "max_results": 100,
    }
    response = client.post("/api/v1/repository/search", json=payload)
    # Phase 3 is implemented -- 501 is no longer a valid response for this input.
    assert response.status_code in (200, 422, 503), (
        f"Unexpected status {response.status_code}: {response.text}"
    )
    body = response.json()
    serialized = json.dumps(body)
    # Must not leak credentials or raw tracebacks.
    assert "traceback" not in serialized.lower()
    settings = Settings()
    if settings.sap_odata.password:
        assert settings.sap_odata.password not in serialized
    if settings.sap_local.password:
        assert settings.sap_local.password not in serialized


def test_sap_ping_does_not_crash_and_hides_credentials():
    """SAP ping must respond safely without crashing or exposing secrets.

    Accepted outcomes depending on runtime configuration:
      200 -- OData/RFC credentials are configured and SAP is reachable.
      422 -- Required configuration is missing or invalid.
      501 -- Connection mode not yet implemented (BTP placeholder).
      503 -- SAP is configured but unreachable from this environment
             (network error, TLS failure, timeout, OData connectivity
             failure).

    All outcomes are considered *controlled* responses.  The invariants are:
      - No unhandled 500 with a raw stack trace.
      - No actual credential value appears in the response body.
    """
    response = client.get("/api/v1/sap/ping")
    assert response.status_code in (200, 422, 501, 503), (
        f"Unexpected status {response.status_code}: {response.text}"
    )
    body = response.json()
    serialized = json.dumps(body)
    assert "traceback" not in serialized.lower()
    # No actual secret value may ever appear - only field names are allowed.
    settings = Settings()
    if settings.sap_local.password:
        assert settings.sap_local.password not in serialized
    if settings.sap_odata.password:
        assert settings.sap_odata.password not in serialized
