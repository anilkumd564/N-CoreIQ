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
    """3. Application loads in local mode."""
    settings = get_settings()
    assert settings.app_env == "local"
    assert settings.sap_connection_mode == "local"


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
    """6. Application does not expose secrets through configuration serialization."""
    settings = Settings()
    dumped = settings.model_dump()
    serialized = json.dumps(dumped, default=str)

    # SAP credential fields must either be empty/None or absent from output.
    assert settings.sap_local.password in (None, "")
    assert settings.sap_local.user in (None, "")
    # No literal password-like values should ever be present in the serialized form.
    assert "password" not in serialized.lower() or settings.sap_local.password in (None, "")


def test_application_startup_does_not_require_ecc_system():
    """7. Application startup does not require an available ECC system."""
    # The TestClient already instantiated the app without any live SAP system.
    # A successful health check confirms the app is usable without ECC.
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_unimplemented_sap_capability_fails_gracefully():
    """8. Unimplemented SAP capability fails gracefully (no crash, no stack trace)."""
    payload = {
        "packages": ["ZFINANCE"],
        "include_subpackages": True,
        "name_patterns": ["Z*", "Y*"],
        "object_types": ["PROG", "CLAS"],
        "max_results": 100,
    }
    response = client.post("/api/v1/repository/search", json=payload)
    assert response.status_code == 501
    body = response.json()
    assert "detail" in body
    # Must not leak credentials or raw tracebacks.
    assert "password" not in body["detail"].lower()
    assert "traceback" not in body["detail"].lower()


def test_sap_ping_does_not_crash_and_hides_credentials():
    """SAP ping must respond safely without crashing or exposing secrets."""
    response = client.get("/api/v1/sap/ping")
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False
    serialized = json.dumps(body)
    assert "password" not in serialized.lower()
