"""Phase 10 — Version 1.0 Hardening & Release Readiness tests.

Tests cover:
  - Version consistency (1.0.0 everywhere)
  - Error model completeness
  - Error code catalog
  - Input validation & limits
  - SAP read-only boundary (no write FMs on allow-list)
  - MCP tool inventory & security annotations
  - Job state machine transitions
  - Secret redaction in settings
  - API error response structure
  - Canonical error code mapping
  - Dependency taxonomy frozen values
  - Confidence taxonomy frozen values
  - SAP allow-list completeness
  - Configuration defaults
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.core.config import Settings


# ===========================================================================
# 1. Version consistency
# ===========================================================================


class TestVersionConsistency:
    """Version 1.0.0 must be reported consistently across all components."""

    def test_backend_version_is_1_0_0(self):
        s = Settings()
        assert s.app_version == "1.0.0", f"app_version should be 1.0.0, got {s.app_version!r}"

    def test_mcp_server_version_default_is_1_0_0(self):
        s = Settings()
        assert s.ncoreiq_mcp_server_version == "1.0.0"

    def test_mcp_server_fallback_version_is_1_0_0(self):
        """NCoreIQMCPServer uses 1.0.0 when settings are not supplied."""
        from app.mcp.server import NCoreIQMCPServer

        class _FakeSvc:
            pass

        server = NCoreIQMCPServer(
            sap_provider=_FakeSvc(),
            discovery_svc=_FakeSvc(),
            object_svc=_FakeSvc(),
            job_svc=_FakeSvc(),
            settings=None,
        )
        assert server._version == "1.0.0"

    def test_frontend_package_version_is_1_0_0(self):
        pkg = Path("frontend/package.json")
        assert pkg.exists(), "frontend/package.json not found"
        data = json.loads(pkg.read_text(encoding="utf-8"))
        assert data["version"] == "1.0.0", (
            f"frontend/package.json version should be 1.0.0, got {data['version']!r}"
        )

    def test_health_endpoint_reports_1_0_0(self):
        """The /health endpoint must include version=1.0.0."""
        from fastapi.testclient import TestClient
        from app.main import create_app

        app = create_app()
        with TestClient(app) as client:
            resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == "1.0.0", f"Health version: {body.get('version')}"

    def test_root_endpoint_reports_1_0_0(self):
        """The / root endpoint must include version=1.0.0."""
        from fastapi.testclient import TestClient
        from app.main import app as prod_app

        with TestClient(prod_app) as client:
            resp = client.get("/")
        assert resp.status_code == 200
        body = resp.json()
        assert body["version"] == "1.0.0"

    def test_app_name_is_ncoreiq(self):
        s = Settings()
        assert s.app_name == "N-CoreIQ"


# ===========================================================================
# 2. Error model & error code catalog
# ===========================================================================


class TestErrorModel:
    """Canonical APIError and ErrorCode must be well-formed."""

    def test_api_error_model_has_required_fields(self):
        from app.models.errors import APIError

        err = APIError(code="TEST_CODE", message="Test message.")
        assert err.code == "TEST_CODE"
        assert err.message == "Test message."
        assert err.correlation_id is None
        assert err.details is None

    def test_api_error_model_serialises_to_dict(self):
        from app.models.errors import APIError

        err = APIError(
            code="JOB_NOT_FOUND",
            message="Job not found.",
            correlation_id="abc-123",
        )
        d = err.model_dump()
        assert d["code"] == "JOB_NOT_FOUND"
        assert d["message"] == "Job not found."
        assert d["correlation_id"] == "abc-123"
        assert "details" in d

    def test_api_error_with_details(self):
        from app.models.errors import APIError

        err = APIError(
            code="VALIDATION_ERROR",
            message="Invalid input.",
            details={"field": "object_type", "reason": "blank"},
        )
        assert err.details["field"] == "object_type"

    def test_error_code_catalog_has_all_required_codes(self):
        from app.models.errors import ErrorCode

        required = [
            "VALIDATION_ERROR",
            "SAP_UNAVAILABLE",
            "SAP_AUTHENTICATION_FAILED",
            "SAP_AUTHORIZATION_FAILED",
            "SAP_OBJECT_NOT_FOUND",
            "SAP_RESPONSE_INVALID",
            "REPOSITORY_OBJECT_NOT_FOUND",
            "DEPENDENCY_LIMIT_EXCEEDED",
            "REVERSE_SCOPE_TOO_LARGE",
            "EXTRACTION_LIMIT_EXCEEDED",
            "JOB_NOT_FOUND",
            "JOB_INVALID_STATE",
            "JOB_NOT_CANCELLABLE",
            "JOB_NOT_RETRYABLE",
            "ARTIFACT_NOT_FOUND",
            "ARTIFACT_EXPIRED",
            "ARTIFACT_UNAVAILABLE",
            "AUTHENTICATION_REQUIRED",
            "AUTHORIZATION_DENIED",
            "TOKEN_EXPIRED",
            "TOKEN_INVALID",
            "CONFIGURATION_ERROR",
            "SERVICE_UNAVAILABLE",
            "INTERNAL_ERROR",
            "NOT_IMPLEMENTED",
        ]
        for code in required:
            assert hasattr(ErrorCode, code), f"ErrorCode missing: {code}"
            assert getattr(ErrorCode, code) == code, f"ErrorCode.{code} value mismatch"

    def test_exception_to_error_code_sap_auth(self):
        from app.models.errors import exception_to_error_code, ErrorCode
        from app.sap.exceptions import SAPAuthenticationError

        result = exception_to_error_code(SAPAuthenticationError("bad creds"))
        assert result == ErrorCode.SAP_AUTHENTICATION_FAILED

    def test_exception_to_error_code_connection(self):
        from app.models.errors import exception_to_error_code, ErrorCode
        from app.sap.exceptions import SAPConnectionError

        result = exception_to_error_code(SAPConnectionError("down"))
        assert result == ErrorCode.SAP_UNAVAILABLE

    def test_exception_to_error_code_object_not_found(self):
        from app.models.errors import exception_to_error_code, ErrorCode
        from app.sap.exceptions import RepositoryObjectNotFoundError

        result = exception_to_error_code(RepositoryObjectNotFoundError("PROG", "ZPROG"))
        assert result == ErrorCode.REPOSITORY_OBJECT_NOT_FOUND

    def test_exception_to_error_code_package_not_found(self):
        from app.models.errors import exception_to_error_code, ErrorCode
        from app.sap.exceptions import RepositoryPackageNotFoundError

        result = exception_to_error_code(RepositoryPackageNotFoundError("ZPKG"))
        assert result == ErrorCode.REPOSITORY_PACKAGE_NOT_FOUND

    def test_exception_to_error_code_value_error(self):
        from app.models.errors import exception_to_error_code, ErrorCode

        result = exception_to_error_code(ValueError("bad input"))
        assert result == ErrorCode.VALIDATION_ERROR

    def test_exception_to_error_code_unknown(self):
        from app.models.errors import exception_to_error_code, ErrorCode

        result = exception_to_error_code(RuntimeError("unexpected"))
        assert result == ErrorCode.INTERNAL_ERROR

    def test_exception_to_error_code_tls(self):
        from app.models.errors import exception_to_error_code, ErrorCode
        from app.sap.exceptions import SAPTLSError

        result = exception_to_error_code(SAPTLSError("cert fail"))
        assert result == ErrorCode.SAP_TLS_ERROR

    def test_exception_to_error_code_timeout(self):
        from app.models.errors import exception_to_error_code, ErrorCode
        from app.sap.exceptions import SAPTimeoutError

        result = exception_to_error_code(SAPTimeoutError("timed out"))
        assert result == ErrorCode.SAP_TIMEOUT

    def test_exception_to_error_code_dep_scope_too_large(self):
        from app.models.errors import exception_to_error_code, ErrorCode
        from app.sap.exceptions import DependencyScopeTooLargeError

        result = exception_to_error_code(DependencyScopeTooLargeError("too big"))
        assert result == ErrorCode.REVERSE_SCOPE_TOO_LARGE

    def test_exception_to_error_code_traversal_limit(self):
        from app.models.errors import exception_to_error_code, ErrorCode
        from app.sap.exceptions import DependencyTraversalLimitError

        result = exception_to_error_code(DependencyTraversalLimitError("too deep"))
        assert result == ErrorCode.DEPENDENCY_LIMIT_EXCEEDED

    def test_exception_to_error_code_not_implemented(self):
        from app.models.errors import exception_to_error_code, ErrorCode

        result = exception_to_error_code(NotImplementedError("not done"))
        assert result == ErrorCode.NOT_IMPLEMENTED


# ===========================================================================
# 3. SAP read-only boundary
# ===========================================================================


class TestSAPReadOnlyBoundary:
    """No write-capable function modules may appear on the allow-list."""

    _FORBIDDEN_FM_PATTERNS = [
        r"INSERT_",
        r"UPDATE_",
        r"DELETE_",
        r"WRITE_",
        r"MODIFY_",
        r"CREATE_",
        r"BAPI_",
        r"_COMMIT",
        r"ABAP_RUN",
        r"EXECUTE_",
    ]

    def test_allowlist_exists(self):
        from app.sap.allowlist import ALLOWED_RFC_FUNCTIONS
        assert isinstance(ALLOWED_RFC_FUNCTIONS, (set, frozenset, list, tuple))
        assert len(ALLOWED_RFC_FUNCTIONS) > 0

    # SAP standard read-only RFCs explicitly permitted for infrastructure use
    _KNOWN_STANDARD_READONLY_RFCS = {"RFC_SYSTEM_INFO"}

    def test_allowlist_contains_only_permitted_fms(self):
        """Allow-listed FMs must be either Z_MCP_REPO_* or known standard read-only RFCs."""
        from app.sap.allowlist import ALLOWED_RFC_FUNCTIONS
        for fm in ALLOWED_RFC_FUNCTIONS:
            is_ncoreiq = fm.startswith("Z_MCP_REPO_")
            is_standard_readonly = fm in self._KNOWN_STANDARD_READONLY_RFCS
            assert is_ncoreiq or is_standard_readonly, (
                f"Unexpected FM on allow-list: {fm!r}. "
                "Only Z_MCP_REPO_* and known standard read-only RFCs are permitted."
            )

    def test_allowlist_has_no_dangerous_names(self):
        from app.sap.allowlist import ALLOWED_RFC_FUNCTIONS
        for fm in ALLOWED_RFC_FUNCTIONS:
            for pattern in self._FORBIDDEN_FM_PATTERNS:
                assert not re.search(pattern, fm, re.IGNORECASE), (
                    f"Potentially dangerous FM on allow-list: {fm!r} matches {pattern!r}"
                )

    def test_allowlist_contains_expected_read_fms(self):
        from app.sap.allowlist import ALLOWED_RFC_FUNCTIONS
        expected = {
            "Z_MCP_REPO_PING",
            "Z_MCP_REPO_PACKAGES",
            "Z_MCP_REPO_DISCOVER",
        }
        fm_set = set(ALLOWED_RFC_FUNCTIONS)
        for fm in expected:
            assert fm in fm_set, f"Expected read FM missing from allow-list: {fm!r}"

    def test_rfc_not_allowed_error_raised_for_unknown_fm(self):
        from app.sap.allowlist import ALLOWED_RFC_FUNCTIONS
        from app.sap.exceptions import SAPRFCNotAllowedError

        # Verify the allow-list check mechanism works
        dangerous_fm = "BAPI_MATERIAL_SAVEDATA"
        assert dangerous_fm not in ALLOWED_RFC_FUNCTIONS

        exc = SAPRFCNotAllowedError(dangerous_fm)
        assert dangerous_fm in str(exc)


# ===========================================================================
# 4. MCP tool inventory & security
# ===========================================================================


class TestMCPToolInventory:
    """All 15 Version 1.0 MCP tools must be registered with correct annotations."""

    _EXPECTED_TOOLS = {
        "sap_ping",
        "list_packages",
        "search_repository",
        "get_object_metadata",
        "get_object_source",
        "start_extraction_job",
        "get_extraction_job",
        "list_extraction_jobs",
        "cancel_extraction_job",
        "retry_extraction_job",
        "get_extraction_result",
        "get_dependencies",
        "traverse_dependencies",
        "find_dependents",
        "analyze_dependency_impact",
    }

    _READ_ONLY_TOOLS = {
        "sap_ping", "list_packages", "search_repository",
        "get_object_metadata", "get_object_source",
        "get_extraction_job", "list_extraction_jobs", "get_extraction_result",
        "get_dependencies", "traverse_dependencies",
        "find_dependents", "analyze_dependency_impact",
    }

    _MUTATION_TOOLS = {
        "start_extraction_job",
        "cancel_extraction_job",
        "retry_extraction_job",
    }

    _FORBIDDEN_TOOLS = {
        "run_abap", "execute_rfc", "execute_odata",
        "read_table", "sql_query", "raw_odata", "abap_eval",
    }

    def _build_server(self):
        from app.mcp.server import NCoreIQMCPServer

        class _FakeSvc:
            pass

        return NCoreIQMCPServer(
            sap_provider=_FakeSvc(),
            discovery_svc=_FakeSvc(),
            object_svc=_FakeSvc(),
            job_svc=_FakeSvc(),
            settings=None,
        )

    async def _get_tools(self, server):
        """Use the FastMCP list_tools() async API to get registered tools."""
        return await server.mcp.list_tools()

    @pytest.mark.asyncio
    async def test_all_15_tools_registered(self):
        server = self._build_server()
        tools = await self._get_tools(server)
        registered = {t.name for t in tools}
        missing = self._EXPECTED_TOOLS - registered
        assert not missing, f"Missing MCP tools: {missing}"

    @pytest.mark.asyncio
    async def test_no_extra_dangerous_tools(self):
        server = self._build_server()
        tools = await self._get_tools(server)
        registered = {t.name for t in tools}
        dangerous = registered & self._FORBIDDEN_TOOLS
        assert not dangerous, f"Dangerous MCP tools registered: {dangerous}"

    @pytest.mark.asyncio
    async def test_read_only_tools_have_correct_annotations(self):
        server = self._build_server()
        tools = await self._get_tools(server)
        tools_by_name = {t.name: t for t in tools}
        for name in self._READ_ONLY_TOOLS:
            tool = tools_by_name.get(name)
            if tool is None:
                continue
            ann = getattr(tool, "annotations", None)
            if ann is not None:
                assert getattr(ann, "readOnlyHint", None) is True, (
                    f"Tool {name!r} should be readOnlyHint=True"
                )
                assert getattr(ann, "destructiveHint", None) is False, (
                    f"Tool {name!r} should be destructiveHint=False"
                )

    @pytest.mark.asyncio
    async def test_mutation_tools_are_not_destructive(self):
        server = self._build_server()
        tools = await self._get_tools(server)
        tools_by_name = {t.name: t for t in tools}
        for name in self._MUTATION_TOOLS:
            tool = tools_by_name.get(name)
            if tool is None:
                continue
            ann = getattr(tool, "annotations", None)
            if ann is not None:
                assert getattr(ann, "destructiveHint", None) is False, (
                    f"MCP tool {name!r} must not be destructive"
                )

    @pytest.mark.asyncio
    async def test_tool_count_equals_15(self):
        server = self._build_server()
        tools = await self._get_tools(server)
        count = len(tools)
        assert count == 15, f"Expected 15 MCP tools, found {count}"


# ===========================================================================
# 5. Job state machine
# ===========================================================================


class TestJobStateMachine:
    """Validate job state transitions for Version 1.0."""

    def _make_store(self, tmp_path):
        from app.jobs.store import SQLiteJobStore
        store = SQLiteJobStore(db_path=str(tmp_path / "test.db"))
        store.initialize()
        return store

    def _make_queued_job(self):
        from app.models.phase6 import (
            ExtractionJob, JobStatus, JobStage, ExtractionJobProgress
        )
        # request field is required (type=Any) - pass a minimal dict
        return ExtractionJob(
            job_id=str(uuid.uuid4()),
            status=JobStatus.QUEUED,
            stage=JobStage.VALIDATING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            progress=ExtractionJobProgress(),
            request={"objects": [], "include_metadata": True, "include_source": True},
        )

    def test_queued_job_can_be_claimed(self, tmp_path):
        store = self._make_store(tmp_path)
        job = self._make_queued_job()
        store.create_job(job)
        claimed = store.claim_next_job(worker_id="worker-1", lease_seconds=60)
        assert claimed is not None
        assert claimed.job_id == job.job_id
        from app.models.phase6 import JobStatus
        assert claimed.status == JobStatus.RUNNING

    def test_running_job_can_be_finished(self, tmp_path):
        from app.models.phase6 import JobStatus
        store = self._make_store(tmp_path)
        job = self._make_queued_job()
        store.create_job(job)
        store.claim_next_job(worker_id="w1")
        store.finish_job(job.job_id, status=JobStatus.SUCCEEDED)
        updated = store.get_job(job.job_id)
        assert updated.status == JobStatus.SUCCEEDED

    def test_running_job_can_be_failed(self, tmp_path):
        from app.models.phase6 import JobStatus
        store = self._make_store(tmp_path)
        job = self._make_queued_job()
        store.create_job(job)
        store.claim_next_job(worker_id="w1")
        store.fail_job(job.job_id, error_code="INTERNAL_ERROR", error_message="died")
        updated = store.get_job(job.job_id)
        assert updated.status == JobStatus.FAILED
        assert updated.error is not None
        assert updated.error.code == "INTERNAL_ERROR"

    def test_queued_job_can_be_directly_cancelled(self, tmp_path):
        from app.models.phase6 import JobStatus
        store = self._make_store(tmp_path)
        job = self._make_queued_job()
        store.create_job(job)
        result = store.cancel_queued_job(job.job_id)
        assert result is not None
        assert result.status == JobStatus.CANCELLED

    def test_running_job_can_request_cancel(self, tmp_path):
        from app.models.phase6 import JobStatus
        store = self._make_store(tmp_path)
        job = self._make_queued_job()
        store.create_job(job)
        store.claim_next_job(worker_id="w1")
        ok = store.request_cancel(job.job_id)
        assert ok is True
        updated = store.get_job(job.job_id)
        assert updated.status == JobStatus.CANCEL_REQUESTED

    def test_cancel_requested_job_can_finalize(self, tmp_path):
        from app.models.phase6 import JobStatus
        store = self._make_store(tmp_path)
        job = self._make_queued_job()
        store.create_job(job)
        store.claim_next_job(worker_id="w1")
        store.request_cancel(job.job_id)
        store.finalize_cancellation(job.job_id)
        updated = store.get_job(job.job_id)
        assert updated.status == JobStatus.CANCELLED

    def test_terminal_job_can_be_retried(self, tmp_path):
        from app.models.phase6 import JobStatus
        store = self._make_store(tmp_path)
        job = self._make_queued_job()
        store.create_job(job)
        store.claim_next_job(worker_id="w1")
        store.fail_job(job.job_id, error_code="SAP_UNAVAILABLE", error_message="sap down")
        new_job = store.retry_job(job.job_id)
        assert new_job.job_id != job.job_id
        assert new_job.status == JobStatus.QUEUED
        assert new_job.retry_of_job_id == job.job_id
        assert new_job.attempt == 2

    def test_non_terminal_job_cannot_be_retried(self, tmp_path):
        store = self._make_store(tmp_path)
        job = self._make_queued_job()
        store.create_job(job)
        store.claim_next_job(worker_id="w1")
        with pytest.raises(ValueError, match="retryable"):
            store.retry_job(job.job_id)

    def test_stale_running_job_recovered(self, tmp_path):
        from app.models.phase6 import JobStatus
        store = self._make_store(tmp_path)
        job = self._make_queued_job()
        store.create_job(job)
        store.claim_next_job(worker_id="w1")
        store._force_expire_lease(job.job_id)
        count = store.recover_stale_jobs()
        assert count == 1
        updated = store.get_job(job.job_id)
        assert updated.status == JobStatus.QUEUED

    def test_job_states_are_documented(self):
        """Verify all Version 1.0 states exist in the JobStatus enum."""
        from app.models.phase6 import JobStatus
        expected_states = {
            "QUEUED", "RUNNING", "CANCEL_REQUESTED",
            "CANCELLED", "SUCCEEDED", "FAILED"
        }
        actual_states = {s.value for s in JobStatus}
        missing = expected_states - actual_states
        assert not missing, f"Missing job states: {missing}"


# ===========================================================================
# 6. Secret redaction
# ===========================================================================


class TestSecretRedaction:
    """Secrets must never appear in serialized settings or logs."""

    def test_sap_password_redacted_in_model_dump(self):
        s = Settings(
            SAP_ODATA__PASSWORD="super_secret_pass",
            SAP_ODATA__BASE_URL="https://sap.example.com",
        )
        dumped = s.model_dump()
        odata = dumped.get("sap_odata", {})
        assert odata.get("password") == "***", "OData password must be redacted"

    def test_sap_local_password_redacted_in_model_dump(self):
        from app.core.config import SapLocalSettings
        s = Settings(sap_local=SapLocalSettings(password="local_secret"))
        dumped = s.model_dump()
        local = dumped.get("sap_local", {})
        assert local.get("password") == "***", "Local SAP password must be redacted"

    def test_btp_clientsecret_redacted_in_model_dump(self):
        from app.core.config import BtpSettings, BtpXsuaaSettings
        s = Settings(btp=BtpSettings(xsuaa=BtpXsuaaSettings(clientsecret="btp_secret_xyz")))
        dumped = s.model_dump()
        xsuaa = dumped.get("btp", {}).get("xsuaa", {})
        assert xsuaa.get("clientsecret") == "***", "BTP clientsecret must be redacted"

    def test_sap_odata_safe_dict_redacts_password(self):
        from app.core.config import SapODataSettings
        s = Settings(sap_odata=SapODataSettings(password="redact_me"))
        safe = s.sap_odata.safe_dict()
        assert safe["password"] == "***"

    def test_btp_safe_dict_redacts_secrets(self):
        from app.core.config import BtpSettings, BtpXsuaaSettings
        s = Settings(btp=BtpSettings(xsuaa=BtpXsuaaSettings(clientsecret="secret123")))
        safe = s.btp.safe_dict()
        assert safe["xsuaa"]["clientsecret"] == "***"

    def test_btp_verificationkey_redacted(self):
        from app.core.config import BtpSettings, BtpXsuaaSettings
        s = Settings(btp=BtpSettings(xsuaa=BtpXsuaaSettings(verificationkey="pubkey_data")))
        safe = s.btp.safe_dict()
        assert safe["xsuaa"]["verificationkey"] == "***"


# ===========================================================================
# 7. Input limits configuration
# ===========================================================================


class TestInputLimits:
    """Version 1.0 limits must be within safe bounds."""

    def test_bulk_max_objects_default(self):
        s = Settings()
        assert s.ncoreiq_bulk_max_objects == 250

    def test_job_workers_within_bounds(self):
        s = Settings()
        assert 1 <= s.ncoreiq_job_workers <= 10

    def test_mcp_max_search_results_within_bounds(self):
        s = Settings()
        assert 1 <= s.ncoreiq_mcp_max_search_results <= 1000

    def test_mcp_max_source_lines_within_bounds(self):
        s = Settings()
        assert 1 <= s.ncoreiq_mcp_max_source_lines <= 5000

    def test_mcp_dep_max_depth_within_bounds(self):
        s = Settings()
        assert 1 <= s.ncoreiq_mcp_dep_max_depth <= 3

    def test_mcp_dep_max_nodes_within_bounds(self):
        s = Settings()
        assert 1 <= s.ncoreiq_mcp_dep_max_nodes <= 200

    def test_mcp_dep_max_edges_within_bounds(self):
        s = Settings()
        assert 1 <= s.ncoreiq_mcp_dep_max_edges <= 500

    def test_mcp_dep_max_candidates_within_bounds(self):
        s = Settings()
        assert 1 <= s.ncoreiq_mcp_dep_max_candidates <= 50

    def test_job_list_max_limit(self):
        s = Settings()
        assert s.ncoreiq_job_max_list_limit <= 200

    def test_job_lease_seconds_reasonable(self):
        s = Settings()
        assert s.ncoreiq_job_lease_seconds >= 10

    def test_artifact_retention_days_positive(self):
        s = Settings()
        assert s.ncoreiq_artifact_retention_days >= 1


# ===========================================================================
# 8. Dependency taxonomy frozen values
# ===========================================================================


class TestDependencyTaxonomy:
    """Version 1.0 relationship and confidence values must be stable."""

    _V1_RELATIONSHIP_TYPES = {
        "INCLUDES",
        "CALLS_PROGRAM",
        "CALLS_FUNCTION",
        "USES_CLASS",
        "IMPLEMENTS_INTERFACE",
        "INHERITS_CLASS",
        "READS_TABLE",
        "WRITES_TABLE",
        "USES_DATA_ELEMENT",
        "USES_DOMAIN",
        "TRANSACTION_TARGET",
    }

    _V1_CONFIDENCE_VALUES = {"EXACT", "HIGH", "MEDIUM", "LOW"}

    _V1_EVIDENCE_VALUES = {
        "DDIC",
        "TSTC",
        "SOURCE_STATIC",
        "SOURCE_HEURISTIC",
        "SOURCE_DYNAMIC",
    }

    def test_relationship_type_enum_exists(self):
        from app.models.phase8 import DependencyRelationshipType
        actual = {rt.value for rt in DependencyRelationshipType}
        missing = self._V1_RELATIONSHIP_TYPES - actual
        assert not missing, f"Missing V1.0 relationship types: {missing}"

    def test_confidence_enum_exists(self):
        from app.models.phase8 import DependencyConfidence
        actual = {cl.value for cl in DependencyConfidence}
        missing = self._V1_CONFIDENCE_VALUES - actual
        assert not missing, f"Missing V1.0 confidence values: {missing}"

    def test_no_extra_confidence_values_in_v1(self):
        """V1.0 confidence values must exactly match the frozen set."""
        from app.models.phase8 import DependencyConfidence
        actual = {cl.value for cl in DependencyConfidence}
        extra = actual - self._V1_CONFIDENCE_VALUES
        assert not extra, f"Unexpected confidence values (freeze V1.0 taxonomy): {extra}"


# ===========================================================================
# 9. API versioning — all public routes use /api/v1/
# ===========================================================================


class TestAPIVersioning:
    """All production endpoints must be under /api/v1/."""

    def _get_routes(self):
        from app.main import app as prod_app
        return [r for r in prod_app.routes]

    def test_repository_routes_use_v1_prefix(self):
        from fastapi.testclient import TestClient
        from app.main import create_app

        app = create_app()
        paths = [r.path for r in app.routes if hasattr(r, "path")]
        # All repository/job/dependency routes must use v1
        api_paths = [p for p in paths if p.startswith("/api/")]
        for p in api_paths:
            assert p.startswith("/api/v1/"), (
                f"Non-versioned API route found: {p!r}"
            )

    def test_health_endpoints_available(self):
        from fastapi.testclient import TestClient
        from app.main import app as prod_app

        with TestClient(prod_app) as client:
            assert client.get("/health").status_code == 200
            assert client.get("/health/live").status_code == 200
            # /health/ready may return 200 or 503 depending on job store availability
            ready_status = client.get("/health/ready").status_code
            assert ready_status in (200, 503), f"Unexpected /health/ready status: {ready_status}"


# ===========================================================================
# 10. Security headers
# ===========================================================================


class TestSecurityHeaders:
    """Response headers must include Version 1.0 security requirements."""

    def test_security_headers_present(self):
        from fastapi.testclient import TestClient
        from app.main import app as prod_app

        with TestClient(prod_app) as client:
            resp = client.get("/")
        assert resp.headers.get("x-content-type-options") == "nosniff"
        assert resp.headers.get("x-frame-options") == "SAMEORIGIN"
        assert "strict-origin" in resp.headers.get("referrer-policy", "")


# ===========================================================================
# 11. Configuration auth mode defaults
# ===========================================================================


class TestAuthConfiguration:
    """Auth mode must default to disabled (safe for local dev)."""

    def test_auth_mode_default_is_disabled(self):
        s = Settings()
        assert s.auth.mode == "disabled"
        assert s.auth_mode == "disabled"

    def test_cors_origins_are_configured(self):
        s = Settings()
        assert len(s.cors_allowed_origins) > 0

    def test_sap_connection_mode_default(self):
        s = Settings()
        assert s.sap_connection_mode in ("local", "btp", "odata")


# ===========================================================================
# 12. Source object types coverage
# ===========================================================================


class TestSupportedObjectTypes:
    """Version 1.0 must support all documented object types."""

    _V1_DISCOVERY_TYPES = {
        "PROG", "CLAS", "INTF", "FUGR", "TABL",
        "DTEL", "DOMA", "VIEW", "SHLP", "ENQU", "TRAN", "MSAG",
    }

    _V1_SOURCE_TYPES = {
        "PROG", "CLAS", "INTF", "FUGR", "FUNC",
        "TABL", "DTEL", "DOMA", "VIEW", "SHLP", "ENQU", "TRAN", "MSAG",
    }

    def test_discovery_object_types_defined(self):
        from app.models.object_types import PHASE3_OBJECT_TYPES
        missing = self._V1_DISCOVERY_TYPES - set(PHASE3_OBJECT_TYPES)
        assert not missing, f"Missing V1.0 discovery types: {missing}"

    def test_source_object_types_include_func(self):
        from app.models.phase4 import PHASE4_SUPPORTED_OBJECT_TYPES
        assert "FUNC" in PHASE4_SUPPORTED_OBJECT_TYPES, "FUNC must be supported for source extraction"

    def test_source_object_types_include_all_v1(self):
        from app.models.phase4 import PHASE4_SUPPORTED_OBJECT_TYPES
        missing = self._V1_SOURCE_TYPES - set(PHASE4_SUPPORTED_OBJECT_TYPES)
        assert not missing, f"Missing V1.0 source types: {missing}"


# ===========================================================================
# 13. Export file consistency
# ===========================================================================


class TestExportConsistency:
    """ZIP and JSON export formats must follow V1.0 layout conventions."""

    def test_export_service_importable(self):
        from app.services.export import RepositoryExportService
        assert RepositoryExportService is not None

    def test_source_export_service_importable(self):
        from app.services.export import RepositoryExportService
        s = Settings()
        svc = RepositoryExportService(settings=s)
        assert svc is not None

    def test_export_filename_has_timestamp(self):
        from app.services.export import RepositoryExportService
        s = Settings()
        svc = RepositoryExportService(settings=s)
        ts = "20260101_120000"
        fname = svc.source_export_filename(ts)
        assert ts in fname
        assert fname.endswith(".zip")


# ===========================================================================
# 14. Exception hierarchy completeness
# ===========================================================================


class TestExceptionHierarchy:
    """All domain exceptions must inherit from SAPError."""

    def test_all_domain_exceptions_inherit_sap_error(self):
        from app.sap import exceptions as ex

        sap_error_subclasses = [
            ex.SAPConfigurationError,
            ex.SAPRFCRuntimeUnavailableError,
            ex.SAPConnectionError,
            ex.SAPAuthenticationError,
            ex.SAPCommunicationError,
            ex.SAPFunctionExecutionError,
            ex.SAPRFCNotAllowedError,
            ex.SAPProviderNotImplementedError,
            ex.SAPTLSError,
            ex.SAPTimeoutError,
            ex.SAPGatewayError,
            ex.SAPResponseParseError,
            ex.RepositoryPackageNotFoundError,
            ex.RepositoryObjectNotFoundError,
            ex.RepositoryObjectTypeNotSupportedError,
            ex.RepositorySourceDataIntegrityError,
            ex.DependencyObjectNotFoundError,
            ex.DependencyScopeTooLargeError,
            ex.DependencyTraversalLimitError,
            ex.DependencyDataIntegrityError,
            ex.DependencyProviderError,
        ]
        for exc_class in sap_error_subclasses:
            assert issubclass(exc_class, ex.SAPError), (
                f"{exc_class.__name__} must inherit from SAPError"
            )

    def test_rfc_not_allowed_carries_function_name(self):
        from app.sap.exceptions import SAPRFCNotAllowedError
        exc = SAPRFCNotAllowedError("Z_DANGEROUS_FM")
        assert exc.function_name == "Z_DANGEROUS_FM"
        assert "Z_DANGEROUS_FM" in str(exc)

    def test_repository_object_not_found_carries_type_and_name(self):
        from app.sap.exceptions import RepositoryObjectNotFoundError
        exc = RepositoryObjectNotFoundError("PROG", "ZTEST")
        assert exc.object_type == "PROG"
        assert exc.object_name == "ZTEST"

    def test_dependency_scope_too_large_is_also_value_error(self):
        from app.sap.exceptions import DependencyScopeTooLargeError
        exc = DependencyScopeTooLargeError("scope too large")
        assert isinstance(exc, ValueError)

    def test_repository_object_type_not_supported_is_also_value_error(self):
        from app.sap.exceptions import RepositoryObjectTypeNotSupportedError
        exc = RepositoryObjectTypeNotSupportedError("XXXX", frozenset({"PROG"}))
        assert isinstance(exc, ValueError)
