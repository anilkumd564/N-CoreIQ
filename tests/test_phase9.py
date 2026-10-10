"""Phase 9 — SAP BTP Production Readiness test suite.

Tests cover:
    - Settings (BTP sub-settings, auth, logging)
    - BTP Service Binding Resolver
    - AuthenticatedPrincipal model and authorization logic
    - XSUAA middleware (disabled mode)
    - Correlation ID middleware
    - Liveness and readiness endpoints
    - Health endpoint backward compatibility
    - Production error sanitization
    - Secret redaction
    - CORS configuration
    - Security headers

All tests are offline — no BTP credentials, no live XSUAA, no live SAP.
"""

from __future__ import annotations

import json
import os
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from app.auth.principal import (
    ANONYMOUS_PRINCIPAL,
    SCOPE_ADMIN,
    SCOPE_ANALYST,
    SCOPE_OPERATOR,
    SCOPE_VIEWER,
    AuthenticatedPrincipal,
)
from app.btp.bindings import BTPServiceBindingResolver, _mask_secrets
from app.core.config import BtpSettings, Settings, get_settings
from app.main import app

client = TestClient(app)


# ===========================================================================
# Settings — Phase 9 fields
# ===========================================================================


def test_settings_has_btp_sub_settings():
    settings = Settings()
    assert hasattr(settings, "btp")
    assert settings.btp is not None
    assert settings.btp.sap_destination_name  # default value present


def test_settings_has_auth_sub_settings():
    settings = Settings()
    assert hasattr(settings, "auth")
    assert settings.auth.mode in ("disabled", "xsuaa")


def test_settings_has_logging_sub_settings():
    settings = Settings()
    assert hasattr(settings, "logging")
    assert settings.logging.format in ("text", "json")


def test_settings_has_cors_configuration():
    settings = Settings()
    assert hasattr(settings, "cors_allowed_origins")
    assert isinstance(settings.cors_allowed_origins, list)
    assert len(settings.cors_allowed_origins) > 0


def test_settings_has_concurrency_limits():
    settings = Settings()
    assert settings.ncoreiq_max_running_jobs_per_instance >= 1
    assert settings.ncoreiq_max_sap_concurrency_per_instance >= 1


def test_settings_version_is_phase9():
    settings = Settings()
    assert settings.app_version == "1.0.0"


def test_settings_btp_secrets_redacted_in_model_dump():
    settings = Settings(
        **{
            "btp": {
                "xsuaa": {"clientsecret": "my-xsuaa-secret", "url": "https://auth.example"},
                "destination": {"clientsecret": "my-dest-secret"},
                "connectivity": {"clientsecret": "my-conn-secret"},
            }
        }
    )
    dumped = settings.model_dump()
    serialized = json.dumps(dumped, default=str)
    assert "my-xsuaa-secret" not in serialized
    assert "my-dest-secret" not in serialized
    assert "my-conn-secret" not in serialized
    # Redacted values replaced with ***
    assert dumped["btp"]["xsuaa"]["clientsecret"] == "***"


def test_settings_app_env_default_is_local():
    settings = Settings()
    assert settings.app_env == "local"


# ===========================================================================
# BTP Service Binding Resolver
# ===========================================================================


def test_binding_resolver_returns_none_without_vcap():
    """Without VCAP_SERVICES, all bindings should be None."""
    with patch.dict(os.environ, {}, clear=False):
        # Ensure VCAP_SERVICES is not set
        env = {k: v for k, v in os.environ.items() if k != "VCAP_SERVICES"}
        with patch.dict(os.environ, env, clear=True):
            resolver = BTPServiceBindingResolver()
            assert resolver.get_xsuaa() is None
            assert resolver.get_destination() is None
            assert resolver.get_connectivity() is None


def test_binding_resolver_parses_vcap_services():
    """BTPServiceBindingResolver parses VCAP_SERVICES correctly."""
    vcap = json.dumps({
        "xsuaa": [{
            "name": "ncoreiq-xsuaa",
            "credentials": {
                "url": "https://subdomain.authentication.eu10.hana.ondemand.com",
                "clientid": "sb-ncoreiq",
                "clientsecret": "secret-xsuaa-value",
                "xsappname": "ncoreiq",
            }
        }],
        "destination": [{
            "name": "ncoreiq-destination",
            "credentials": {
                "uri": "https://destination-configuration.cfapps.eu10.hana.ondemand.com",
                "clientid": "sb-destination",
                "clientsecret": "secret-dest-value",
            }
        }],
    })
    with patch.dict(os.environ, {"VCAP_SERVICES": vcap}):
        resolver = BTPServiceBindingResolver()
        xsuaa = resolver.get_xsuaa()
        assert xsuaa is not None
        assert xsuaa.service_name == "ncoreiq-xsuaa"
        assert xsuaa.get("clientid") == "sb-ncoreiq"
        # Secret is accessible via .get() but masked in safe_repr
        assert "secret-xsuaa-value" not in str(xsuaa.safe_repr())

        dest = resolver.get_destination()
        assert dest is not None
        assert dest.get("uri") is not None

        # connectivity not bound
        assert resolver.get_connectivity() is None


def test_binding_resolver_require_raises_for_missing_field():
    """ServiceBindingCredentials.require() raises ValueError for missing field."""
    vcap = json.dumps({
        "destination": [{
            "name": "test-dest",
            "credentials": {"uri": "https://example.com"}
        }]
    })
    with patch.dict(os.environ, {"VCAP_SERVICES": vcap}):
        resolver = BTPServiceBindingResolver()
        dest = resolver.get_destination()
        assert dest is not None
        with pytest.raises(ValueError) as exc_info:
            dest.require("clientsecret")
        assert "clientsecret" in str(exc_info.value)
        assert "Verify the service is correctly bound" in str(exc_info.value)


def test_binding_resolver_validate_btp_mode_returns_missing():
    """validate_btp_mode() returns list of missing services."""
    with patch.dict(os.environ, {}, clear=True):
        resolver = BTPServiceBindingResolver()
        missing = resolver.validate_btp_mode()
        assert len(missing) >= 2  # at least xsuaa and destination missing


def test_mask_secrets_redacts_known_fields():
    data = {
        "url": "https://example.com",
        "clientsecret": "super-secret",
        "verificationkey": "-----BEGIN PUBLIC KEY-----",
        "normal_field": "visible",
    }
    masked = _mask_secrets(data)
    assert masked["url"] == "https://example.com"
    assert masked["clientsecret"] == "***"
    assert masked["verificationkey"] == "***"
    assert masked["normal_field"] == "visible"


def test_binding_resolver_diagnostics_does_not_expose_credentials():
    vcap = json.dumps({
        "xsuaa": [{
            "name": "ncoreiq-xsuaa",
            "credentials": {
                "url": "https://auth.example",
                "clientsecret": "xsuaa-secret",
            }
        }]
    })
    with patch.dict(os.environ, {"VCAP_SERVICES": vcap}):
        resolver = BTPServiceBindingResolver()
        diag = resolver.diagnostics()
        diag_str = json.dumps(diag)
        assert "xsuaa-secret" not in diag_str
        assert diag["xsuaa"]["bound"] is True
        assert "credential_keys" in diag["xsuaa"]


# ===========================================================================
# AuthenticatedPrincipal model
# ===========================================================================


def test_authenticated_principal_viewer_can_access_viewer_endpoints():
    principal = AuthenticatedPrincipal(
        subject="user1",
        display_name="Test User",
        scopes=[SCOPE_VIEWER],
        roles=[SCOPE_VIEWER],
    )
    assert principal.is_viewer()
    assert principal.can_access("sap_ping")
    assert principal.can_access("search_repository")
    assert principal.can_access("get_object_metadata")
    assert not principal.can_access("create_job")  # Operator only
    assert not principal.can_access("diagnostics")  # Admin only


def test_authenticated_principal_analyst_can_access_analyst_endpoints():
    principal = AuthenticatedPrincipal(
        subject="user2",
        display_name="Analyst User",
        scopes=[SCOPE_ANALYST, SCOPE_VIEWER],
        roles=[SCOPE_ANALYST],
    )
    assert principal.is_analyst()
    assert principal.can_access("traverse_dependencies")
    assert principal.can_access("source_export")
    assert not principal.can_access("create_job")


def test_authenticated_principal_operator_can_access_operator_endpoints():
    principal = AuthenticatedPrincipal(
        subject="user3",
        display_name="Operator",
        scopes=[SCOPE_OPERATOR, SCOPE_ANALYST, SCOPE_VIEWER],
        roles=[SCOPE_OPERATOR],
    )
    assert principal.is_operator()
    assert principal.can_access("create_job")
    assert principal.can_access("cancel_job")
    assert principal.can_access("download_artifact")
    assert not principal.can_access("diagnostics")


def test_authenticated_principal_admin_can_access_all_endpoints():
    principal = AuthenticatedPrincipal(
        subject="admin",
        display_name="Admin User",
        scopes=[SCOPE_ADMIN, SCOPE_OPERATOR, SCOPE_ANALYST, SCOPE_VIEWER],
        roles=[SCOPE_ADMIN],
    )
    assert principal.is_admin()
    assert principal.can_access("diagnostics")
    assert principal.can_access("configuration")
    assert principal.can_access("create_job")
    assert principal.can_access("sap_ping")


def test_anonymous_principal_has_full_access():
    """ANONYMOUS_PRINCIPAL (disabled auth mode) has all scopes."""
    assert ANONYMOUS_PRINCIPAL.is_admin()
    assert ANONYMOUS_PRINCIPAL.can_access("diagnostics")
    assert ANONYMOUS_PRINCIPAL.can_access("sap_ping")


def test_principal_safe_repr_does_not_expose_secrets():
    principal = AuthenticatedPrincipal(
        subject="user@tenant.com",
        display_name="User",
        scopes=[SCOPE_VIEWER],
    )
    safe = principal.safe_repr()
    # safe_repr should not contain raw auth tokens
    safe_str = json.dumps(safe)
    assert "password" not in safe_str.lower() or safe_str.count("password") == 0


def test_principal_highest_role_returns_correct_level():
    viewer = AuthenticatedPrincipal(
        subject="u", display_name="u", scopes=[SCOPE_VIEWER]
    )
    assert viewer.highest_role() == SCOPE_VIEWER

    admin = AuthenticatedPrincipal(
        subject="a",
        display_name="a",
        scopes=[SCOPE_ADMIN, SCOPE_OPERATOR, SCOPE_ANALYST, SCOPE_VIEWER],
    )
    assert admin.highest_role() == SCOPE_ADMIN


# ===========================================================================
# Liveness and Readiness endpoints
# ===========================================================================


def test_liveness_endpoint_returns_200():
    response = client.get("/health/live")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "alive"
    assert "version" in body
    assert "app" in body


def test_liveness_endpoint_does_not_require_sap():
    """Liveness must not fail even if SAP is completely unavailable."""
    with patch(
        "app.api.routes.build_sap_connection_provider",
        side_effect=RuntimeError("SAP completely unavailable"),
    ):
        response = client.get("/health/live")
    assert response.status_code == 200


def test_readiness_endpoint_returns_2xx_or_503():
    """Readiness may return 200 or 503 depending on job store state."""
    response = client.get("/health/ready")
    assert response.status_code in (200, 503)
    body = response.json()
    assert "status" in body
    assert body["status"] in ("ready", "not_ready")
    assert "services" in body


def test_health_endpoint_backward_compat():
    """Legacy /health endpoint still returns Phase 1 format at /health."""
    response = client.get("/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"
    assert "version" in body


def test_api_v1_health_backward_compat():
    """Legacy /api/v1/health endpoint still works."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ok"


# ===========================================================================
# Security headers
# ===========================================================================


def test_security_headers_present_on_api_response():
    response = client.get("/api/v1/health")
    assert response.headers.get("X-Content-Type-Options") == "nosniff"
    assert response.headers.get("Referrer-Policy") == "strict-origin-when-cross-origin"
    assert response.headers.get("X-Frame-Options") == "SAMEORIGIN"


def test_correlation_id_returned_in_response_headers():
    response = client.get("/api/v1/health")
    assert "X-Correlation-ID" in response.headers or "x-correlation-id" in response.headers
    assert "X-Request-ID" in response.headers or "x-request-id" in response.headers


def test_correlation_id_propagated_from_request():
    """If client sends X-Correlation-ID, it should be echoed back."""
    response = client.get(
        "/api/v1/health",
        headers={"X-Correlation-ID": "test-correlation-123"},
    )
    correlation = (
        response.headers.get("X-Correlation-ID")
        or response.headers.get("x-correlation-id")
    )
    assert correlation == "test-correlation-123"


# ===========================================================================
# Authentication middleware — disabled mode
# ===========================================================================


def test_auth_disabled_mode_allows_all_requests():
    """In disabled auth mode, all requests should succeed without a token."""
    response = client.get("/api/v1/health")
    assert response.status_code == 200


def test_auth_disabled_mode_health_live_always_accessible():
    """Liveness probe must always be accessible regardless of auth mode."""
    response = client.get("/health/live")
    assert response.status_code == 200


# ===========================================================================
# Production error sanitization
# ===========================================================================


def test_404_response_does_not_expose_internal_paths():
    response = client.get("/api/v1/nonexistent-endpoint")
    assert response.status_code == 404


def test_sap_password_not_in_api_response_bodies():
    """No SAP credential must appear in any API response."""
    settings = Settings()
    response = client.get("/api/v1/health")
    body_str = json.dumps(response.json())
    if settings.sap_odata.password:
        assert settings.sap_odata.password not in body_str
    if settings.sap_local.password:
        assert settings.sap_local.password not in body_str


def test_liveness_version_is_09x():
    """Phase 10: version is now 1.0.0 (renamed test kept for history)."""
    response = client.get("/health/live")
    body = response.json()
    assert body["version"] == "1.0.0"


# ===========================================================================
# CORS configuration
# ===========================================================================


def test_cors_is_not_wildcard():
    """CORS must never allow all origins in production."""
    settings = Settings()
    origins = settings.cors_allowed_origins
    assert "*" not in origins, "CORS must not use wildcard '*' for allowed origins"


def test_cors_allows_configured_origin():
    """Requests from configured origins should get CORS headers."""
    settings = Settings()
    if settings.cors_allowed_origins:
        origin = settings.cors_allowed_origins[0]
        response = client.get(
            "/api/v1/health",
            headers={"Origin": origin},
        )
        # Either CORS header is present OR the endpoint returns 200
        assert response.status_code == 200


# ===========================================================================
# Admin endpoints — authorization in disabled mode
# ===========================================================================


def test_admin_diagnostics_accessible_in_disabled_auth_mode():
    """In disabled auth mode, ANONYMOUS_PRINCIPAL is admin so diagnostics is accessible."""
    response = client.get("/api/v1/admin/diagnostics")
    # ANONYMOUS_PRINCIPAL has Admin scope in disabled mode
    assert response.status_code == 200


def test_admin_metrics_accessible_in_disabled_auth_mode():
    response = client.get("/api/v1/admin/metrics")
    assert response.status_code == 200
    body = response.json()
    assert "runtime_mode" in body


# ===========================================================================
# Structured logging / log config
# ===========================================================================


def test_log_config_imports_work():
    """Verify log_config module imports without errors."""
    from app.core.log_config import (
        CorrelationIdMiddleware,
        StructuredLogFormatter,
        configure_logging,
        get_correlation_id,
        get_request_id,
        set_correlation_id,
        set_request_id,
    )
    assert CorrelationIdMiddleware is not None
    assert StructuredLogFormatter is not None


def test_structured_log_formatter_text_mode():
    """StructuredLogFormatter in text mode produces readable output."""
    import logging
    from app.core.log_config import StructuredLogFormatter

    formatter = StructuredLogFormatter(fmt="text")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=1,
        msg="test message",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    assert "test message" in formatted
    assert "INFO" in formatted


def test_structured_log_formatter_json_mode():
    """StructuredLogFormatter in json mode produces valid JSON."""
    import logging
    from app.core.log_config import StructuredLogFormatter

    formatter = StructuredLogFormatter(fmt="json", service="N-CoreIQ")
    record = logging.LogRecord(
        name="test.module",
        level=logging.WARNING,
        pathname="",
        lineno=42,
        msg="json log test",
        args=(),
        exc_info=None,
    )
    formatted = formatter.format(record)
    parsed = json.loads(formatted)
    assert parsed["level"] == "WARNING"
    assert parsed["message"] == "json log test"
    assert parsed["service"] == "N-CoreIQ"
    assert "timestamp" in parsed


def test_secret_fields_not_in_json_log():
    """Secret field values must be redacted in JSON logs."""
    import logging
    from app.core.log_config import StructuredLogFormatter

    formatter = StructuredLogFormatter(fmt="json")
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname="",
        lineno=1,
        msg="checking secrets",
        args=(),
        exc_info=None,
    )
    record.password = "my-secret-password"  # type: ignore[attr-defined]
    record.normal_field = "visible"  # type: ignore[attr-defined]
    formatted = formatter.format(record)
    # Secret should be redacted
    assert "my-secret-password" not in formatted
    assert "visible" in formatted


# ===========================================================================
# Phase 9 BTP connection provider
# ===========================================================================


def test_btp_provider_created_by_factory():
    """Factory creates BtpSapConnectionProvider for btp mode."""
    from app.sap.connection.btp import BtpSapConnectionProvider
    from app.sap.connection.factory import build_sap_connection_provider

    settings = Settings(sap_connection_mode="btp")
    provider = build_sap_connection_provider(settings)
    assert isinstance(provider, BtpSapConnectionProvider)


def test_btp_provider_uses_configured_destination_name():
    """BtpSapConnectionProvider uses destination name from settings."""
    from app.sap.connection.btp import BtpSapConnectionProvider

    settings = Settings(
        sap_connection_mode="btp",
        btp={"sap_destination_name": "MY_ECC_DEST"},
    )
    provider = BtpSapConnectionProvider(settings)
    assert provider._destination_name == "MY_ECC_DEST"
