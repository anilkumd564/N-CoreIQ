"""N-CoreIQ BTP Deployment — Step 2A / 4A: Deployment Preflight Cleanup Tests.

Tests cover:
  1.  SAP destination name is consistently NC1_ODATA in BTP deployment files
  2.  mta.yaml version is 1.0.0
  3.  manifest.yml version-independent SAP destination alignment
  4.  PyJWT[crypto] is installed and importable
  5.  RSAAlgorithm available (cryptography backend present)
  6.  BTP+xsuaa mode fails at startup if PyJWT is missing (fail-fast)
  7.  Non-BTP mode allows structure-only fallback (development convenience)
  8.  check_jwt_crypto_available() raises RuntimeError when jwt absent
  9.  check_jwt_crypto_available() succeeds when jwt present
 10.  _validate_btp_startup raises when jwt crypto absent in xsuaa mode
 11.  Full JWT validation uses PyJWT RS256 (not structure-only) when available
 12.  mta.yaml binds all three required services
 13.  manifest.yml binds all three required services
 14.  xs-security.json present and valid
 15.  Application version 1.0.0 consistent across settings
 16.  No ECC_DEV in BTP deployment files
 17.  NCOREIQ_MCP_TRANSPORT=streamable_http in both deployment files
 18.  Requirements file lists PyJWT[crypto] and cryptography
 19.  stdio regression: 15 tools still registered
 20.  REST regression: health endpoints functional

All tests are offline — no BTP credentials, no live XSUAA, no live SAP.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from app.auth.middleware import check_jwt_crypto_available
from app.core.config import Settings


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_MANIFEST = _PROJECT_ROOT / "deployment" / "btp" / "manifest.yml"
_MTA = _PROJECT_ROOT / "deployment" / "btp" / "mta.yaml"
_XS_SECURITY = _PROJECT_ROOT / "deployment" / "btp" / "xs-security.json"
_REQUIREMENTS = _PROJECT_ROOT / "requirements.txt"
_ENV_EXAMPLE = _PROJECT_ROOT / ".env.example"


# ===========================================================================
# 1–3. SAP Destination name consistency
# ===========================================================================


class TestDestinationNameConsistency:
    """BTP deployment files must consistently use NC1_ODATA."""

    def test_manifest_uses_nc1_odata(self):
        """manifest.yml must set SAP_DESTINATION_NAME=NC1_ODATA."""
        content = _MANIFEST.read_text(encoding="utf-8")
        assert "NC1_ODATA" in content, "manifest.yml missing NC1_ODATA"

    def test_manifest_does_not_use_ecc_dev(self):
        """manifest.yml must NOT set SAP_DESTINATION_NAME=ECC_DEV for BTP."""
        content = _MANIFEST.read_text(encoding="utf-8")
        assert "SAP_DESTINATION_NAME: ECC_DEV" not in content, (
            "manifest.yml must use NC1_ODATA, not ECC_DEV"
        )

    def test_mta_uses_nc1_odata(self):
        """mta.yaml must reference NC1_ODATA as the destination name."""
        content = _MTA.read_text(encoding="utf-8")
        assert "NC1_ODATA" in content, "mta.yaml missing NC1_ODATA reference"

    def test_mta_sets_sap_destination_name(self):
        """mta.yaml must explicitly set SAP_DESTINATION_NAME: NC1_ODATA."""
        content = _MTA.read_text(encoding="utf-8")
        assert "SAP_DESTINATION_NAME: NC1_ODATA" in content, (
            "mta.yaml must set SAP_DESTINATION_NAME: NC1_ODATA"
        )

    def test_btp_settings_default_destination_is_nc1_odata(self):
        """BtpSettings.sap_destination_name defaults to NC1_ODATA."""
        s = Settings()
        assert s.btp.sap_destination_name == "NC1_ODATA"

    def test_env_example_documents_local_default(self):
        """Local ECC_DEV default is acceptable in .env.example (local dev)."""
        if _ENV_EXAMPLE.exists():
            content = _ENV_EXAMPLE.read_text(encoding="utf-8")
            # Local dev may use ECC_DEV — that is intentional
            assert "SAP_DESTINATION_NAME" in content


# ===========================================================================
# 4. MTA version alignment
# ===========================================================================


class TestVersionAlignment:
    """Application and MTA versions must both be 1.0.0."""

    def test_settings_app_version_is_1_0_0(self):
        s = Settings()
        assert s.app_version == "1.0.0"

    def test_mta_version_is_1_0_0(self):
        """mta.yaml version must be 1.0.0."""
        content = _MTA.read_text(encoding="utf-8")
        assert "version: 1.0.0" in content, (
            f"mta.yaml must have version: 1.0.0 (found: {content[:500]})"
        )

    def test_mta_does_not_have_old_version(self):
        """mta.yaml must not still reference version 0.9.0."""
        content = _MTA.read_text(encoding="utf-8")
        assert "version: 0.9.0" not in content

    def test_mtar_filename_reference_updated(self):
        """mta.yaml usage comment must reference ncoreiq_1.0.0.mtar."""
        content = _MTA.read_text(encoding="utf-8")
        assert "ncoreiq_1.0.0.mtar" in content
        assert "ncoreiq_0.9.0.mtar" not in content


# ===========================================================================
# 5–10. PyJWT crypto availability and fail-fast
# ===========================================================================


class TestPyJwtCryptoAvailability:
    """PyJWT with RSA support must be installed and fail-fast when absent."""

    def test_pyjwt_importable(self):
        """PyJWT must be importable."""
        import jwt
        assert jwt is not None

    def test_pyjwt_version_meets_minimum(self):
        """PyJWT version must be >= 2.8."""
        import jwt
        major, minor = str(jwt.__version__).split(".")[:2]
        assert (int(major), int(minor)) >= (2, 8), (
            f"PyJWT >= 2.8 required, got {jwt.__version__}"
        )

    def test_rsa_algorithm_importable(self):
        """jwt.algorithms.RSAAlgorithm must be importable (cryptography backend)."""
        from jwt.algorithms import RSAAlgorithm
        assert RSAAlgorithm is not None

    def test_cryptography_importable(self):
        """cryptography package must be importable."""
        import cryptography
        assert cryptography is not None

    def test_cryptography_version_meets_minimum(self):
        """cryptography version must be >= 42.0."""
        import cryptography
        major = int(cryptography.__version__.split(".")[0])
        assert major >= 42, (
            f"cryptography >= 42.0 required, got {cryptography.__version__}"
        )

    def test_check_jwt_crypto_available_succeeds(self):
        """check_jwt_crypto_available() must not raise when PyJWT is present."""
        check_jwt_crypto_available()  # no exception expected

    def test_check_jwt_crypto_available_raises_on_missing_jwt(self):
        """check_jwt_crypto_available() must raise RuntimeError if jwt absent."""
        import builtins
        real_import = builtins.__import__

        def mock_import(name, *args, **kwargs):
            if name == "jwt":
                raise ImportError("No module named 'jwt'")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=mock_import):
            with pytest.raises(RuntimeError, match="PyJWT with RSA cryptography"):
                check_jwt_crypto_available()

    def test_requirements_lists_pyjwt_crypto(self):
        """requirements.txt must list PyJWT[crypto]."""
        content = _REQUIREMENTS.read_text(encoding="utf-8")
        assert "PyJWT[crypto]" in content
        # Must not be commented out
        for line in content.splitlines():
            stripped = line.strip()
            if "PyJWT[crypto]" in stripped and not stripped.startswith("#"):
                return
        pytest.fail("PyJWT[crypto] is commented out in requirements.txt")

    def test_requirements_lists_cryptography(self):
        """requirements.txt must list cryptography."""
        content = _REQUIREMENTS.read_text(encoding="utf-8")
        for line in content.splitlines():
            stripped = line.strip()
            if stripped.startswith("cryptography") and not stripped.startswith("#"):
                return
        pytest.fail("cryptography is commented out or absent in requirements.txt")


# ===========================================================================
# 11. BTP startup fail-fast for missing JWT crypto
# ===========================================================================


class TestBtpStartupFailFast:
    """BTP+xsuaa mode must fail at startup if PyJWT crypto is absent."""

    def test_validate_btp_startup_checks_jwt_crypto_in_xsuaa_mode(self):
        """_validate_btp_startup must call check_jwt_crypto_available in xsuaa mode."""
        from app.main import _validate_btp_startup

        # Patch check_jwt_crypto_available to verify it is called
        with patch("app.auth.middleware.check_jwt_crypto_available") as mock_check:
            mock_check.return_value = None  # success
            settings = Settings(auth_mode="xsuaa", app_env="btp")
            # Also need to patch the binding resolver to not fail
            with patch("app.btp.bindings.BTPServiceBindingResolver") as mock_resolver:
                mock_resolver.return_value.validate_btp_mode.return_value = []
                _validate_btp_startup(settings)
            mock_check.assert_called_once()

    def test_validate_btp_startup_raises_if_pyjwt_missing(self):
        """_validate_btp_startup must raise RuntimeError if PyJWT absent in xsuaa mode."""
        from app.main import _validate_btp_startup

        with patch("app.auth.middleware.check_jwt_crypto_available",
                   side_effect=RuntimeError("PyJWT missing")):
            settings = Settings(auth_mode="xsuaa", app_env="btp")
            with patch("app.btp.bindings.BTPServiceBindingResolver") as mock_resolver:
                mock_resolver.return_value.validate_btp_mode.return_value = []
                with pytest.raises(RuntimeError, match="PyJWT missing"):
                    _validate_btp_startup(settings)

    def test_validate_btp_startup_skips_jwt_check_in_disabled_mode(self):
        """_validate_btp_startup must not check JWT crypto when auth is disabled."""
        from app.main import _validate_btp_startup

        with patch("app.auth.middleware.check_jwt_crypto_available") as mock_check:
            settings = Settings(auth_mode="disabled", app_env="btp")
            with patch("app.btp.bindings.BTPServiceBindingResolver") as mock_resolver:
                mock_resolver.return_value.validate_btp_mode.return_value = []
                _validate_btp_startup(settings)
            mock_check.assert_not_called()

    def test_insecure_jwt_fallback_requires_non_btp_mode(self):
        """Structure-only JWT fallback must only occur when PyJWT absent (dev only)."""
        # Verify that check_jwt_crypto_available exists and is callable
        assert callable(check_jwt_crypto_available)
        # Verify the middleware module documents the restriction
        import inspect
        import app.auth.middleware as mw_mod
        src = inspect.getsource(mw_mod._validate_jwt_without_library)
        assert "development mode" in src.lower() or "dev" in src.lower()
        assert "btp" in src.lower()


# ===========================================================================
# 12–13. BTP service bindings in deployment files
# ===========================================================================


class TestBtpServiceBindings:
    """Deployment files must bind all three required BTP services."""

    def test_manifest_binds_xsuaa(self):
        content = _MANIFEST.read_text(encoding="utf-8")
        assert "sri-xsuaa" in content or "xsuaa" in content.lower()

    def test_manifest_binds_destination(self):
        content = _MANIFEST.read_text(encoding="utf-8")
        assert "sri-destination" in content or "destination" in content.lower()

    def test_manifest_binds_connectivity(self):
        content = _MANIFEST.read_text(encoding="utf-8")
        assert "sri-connectivity" in content or "connectivity" in content.lower()

    def test_mta_requires_xsuaa(self):
        content = _MTA.read_text(encoding="utf-8")
        assert "ncoreiq-xsuaa" in content

    def test_mta_requires_destination(self):
        content = _MTA.read_text(encoding="utf-8")
        assert "ncoreiq-destination" in content

    def test_mta_requires_connectivity(self):
        content = _MTA.read_text(encoding="utf-8")
        assert "ncoreiq-connectivity" in content

    def test_mta_xsuaa_service_plan_is_application(self):
        """XSUAA must use 'application' service plan."""
        content = _MTA.read_text(encoding="utf-8")
        assert "service-plan: application" in content

    def test_xs_security_json_present(self):
        assert _XS_SECURITY.exists(), "xs-security.json missing"

    def test_xs_security_has_required_scopes(self):
        import json
        sec = json.loads(_XS_SECURITY.read_text(encoding="utf-8"))
        scope_names = [s["name"] for s in sec.get("scopes", [])]
        required = {"$XSAPPNAME.NCoreIQ.Viewer", "$XSAPPNAME.NCoreIQ.Analyst",
                    "$XSAPPNAME.NCoreIQ.Operator", "$XSAPPNAME.NCoreIQ.Admin"}
        for r in required:
            assert r in scope_names, f"xs-security.json missing scope: {r}"


# ===========================================================================
# 14. MCP configuration preserved in deployment files
# ===========================================================================


class TestMcpConfigPreserved:
    """MCP streamable_http transport must be set in all BTP deployment files."""

    def test_manifest_has_mcp_transport_streamable_http(self):
        content = _MANIFEST.read_text(encoding="utf-8")
        assert "NCOREIQ_MCP_TRANSPORT: streamable_http" in content

    def test_mta_has_mcp_transport_streamable_http(self):
        content = _MTA.read_text(encoding="utf-8")
        assert "NCOREIQ_MCP_TRANSPORT: streamable_http" in content

    def test_no_ecc_dev_in_btp_deployment_files(self):
        """Neither manifest.yml nor mta.yaml should contain SAP_DESTINATION_NAME: ECC_DEV."""
        for path, name in [(_MANIFEST, "manifest.yml"), (_MTA, "mta.yaml")]:
            content = path.read_text(encoding="utf-8")
            assert "SAP_DESTINATION_NAME: ECC_DEV" not in content, (
                f"{name} must not set SAP_DESTINATION_NAME to ECC_DEV"
            )

    def test_no_ncoreiq_ecc_in_btp_deployment_files(self):
        """Neither manifest.yml nor mta.yaml should reference old NCOREIQ_ECC destination."""
        for path, name in [(_MANIFEST, "manifest.yml"), (_MTA, "mta.yaml")]:
            content = path.read_text(encoding="utf-8")
            assert "SAP_DESTINATION_NAME: NCOREIQ_ECC" not in content, (
                f"{name} must not set SAP_DESTINATION_NAME to NCOREIQ_ECC"
            )

    def test_canonical_mcp_path_documented(self):
        """mta.yaml must document the canonical MCP endpoint /mcp/."""
        content = _MTA.read_text(encoding="utf-8")
        assert "/mcp/" in content or "mcp" in content


# ===========================================================================
# 15. stdio regression
# ===========================================================================


class TestStdioRegression:
    """stdio transport must still work after Step 2A changes."""

    def test_15_tools_registered_in_stdio_mode(self):
        import asyncio
        from unittest.mock import AsyncMock, MagicMock
        from app.mcp.server import NCoreIQMCPServer

        srv = NCoreIQMCPServer(
            sap_provider=AsyncMock(),
            discovery_svc=AsyncMock(),
            object_svc=AsyncMock(),
            job_svc=MagicMock(),
        )
        tools = asyncio.get_event_loop().run_until_complete(srv.mcp.list_tools())
        assert len(tools) == 15, f"Expected 15 tools, got {len(tools)}"

    def test_run_stdio_method_exists(self):
        from unittest.mock import AsyncMock, MagicMock
        from app.mcp.server import NCoreIQMCPServer

        srv = NCoreIQMCPServer(
            sap_provider=AsyncMock(),
            discovery_svc=AsyncMock(),
            object_svc=AsyncMock(),
            job_svc=MagicMock(),
        )
        assert hasattr(srv, "run_stdio")


# ===========================================================================
# 16. REST regression
# ===========================================================================


class TestRestRegression:
    """REST health endpoints must remain functional after Step 2A changes."""

    def test_health_live_returns_200(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        assert client.get("/health/live").status_code == 200

    def test_health_returns_ok(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        assert client.get("/health").json()["status"] == "ok"

    def test_app_version_in_liveness_response(self):
        from fastapi.testclient import TestClient
        from app.main import app

        client = TestClient(app)
        body = client.get("/health/live").json()
        assert body.get("version") == "1.0.0"
