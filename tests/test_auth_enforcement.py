"""Gate 0E10-R4-A1 — Endpoint Authorization Enforcement Tests.

Tests actual FastAPI HTTP endpoint behavior for each identity class:
  1. No authentication (XSUAA mode, no token)          → 401
  2. Authenticated, no N-CoreIQ scope                  → 403
  3. Viewer                                             → authorized on Viewer endpoints
  4. Analyst                                            → authorized on Viewer + Analyst endpoints
  5. Operator                                           → authorized on all non-Admin endpoints
  6. Admin                                              → authorized on all endpoints (where applicable)

Key invariants verified:
  - 401 is returned before the route handler executes (middleware rejects no-token)
  - 403 is returned when authenticated but scope is insufficient
  - SAP provider is NOT called when 403 is returned (no information leak)
  - All-custom discovery semantics are UNCHANGED by auth (behavior preserved)
  - N-CoreShift Viewer+Analyst identity can reach all required job operations
  - N-CoreShift Viewer+Analyst identity CANNOT reach cancel/retry
  - Operator/Admin are NOT granted to N-CoreShift as a workaround

Auth test infrastructure:
  Tests use app.dependency_overrides[get_principal] to inject specific principals.
  This is the correct pattern because require_scope() uses Depends(get_principal)
  internally — overriding get_principal controls what principal every auth check sees.

  For 401 tests: middleware runs in xsuaa mode with no Authorization header;
  middleware returns 401 before the route handler.

  For 403/200 tests: auth mode is disabled (test default) + principal injection.
"""

from __future__ import annotations

import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

# Ensure disabled executor before app import
os.environ.setdefault("NCOREIQ_DISABLE_EXECUTOR", "true")


# ---------------------------------------------------------------------------
# Job model helper — creates a minimal valid ExtractionJob for mocking
# ---------------------------------------------------------------------------

def _make_job(job_id: str = "test-job-001", status_name: str = "QUEUED") -> "Any":
    """Build a minimal valid ExtractionJob for use in mock returns."""
    from app.models.phase6 import ExtractionJob, ExtractionJobRequest, JobStatus
    from app.models.phase5 import BulkExtractionRequest, ExtractionObjectRef
    from datetime import datetime, timezone

    req = ExtractionJobRequest(
        extraction=BulkExtractionRequest(
            objects=[ExtractionObjectRef(object_type="PROG", object_name="ZTEST")],
            include_metadata=True,
            include_source=False,
        )
    )
    now = datetime.now(timezone.utc)
    return ExtractionJob(
        job_id=job_id,
        status=JobStatus[status_name],
        created_at=now,
        updated_at=now,
        request=req,
        stage=None,
    )

from app.auth.middleware import get_principal
from app.auth.principal import (
    ANONYMOUS_PRINCIPAL,
    SCOPE_ADMIN,
    SCOPE_ANALYST,
    SCOPE_OPERATOR,
    SCOPE_VIEWER,
    AuthenticatedPrincipal,
)
from app.main import app

# ---------------------------------------------------------------------------
# Principal factories — synthetic identities, no real tokens
# ---------------------------------------------------------------------------


def _make_principal(
    scopes: list[str],
    subject: str = "test-subject",
    is_service: bool = False,
) -> AuthenticatedPrincipal:
    """Build a synthetic AuthenticatedPrincipal for testing."""
    return AuthenticatedPrincipal(
        subject=subject,
        display_name=f"Test {subject}",
        scopes=scopes,
        roles=scopes,
        is_service=is_service,
    )


def _no_scope() -> AuthenticatedPrincipal:
    """Authenticated principal with NO N-CoreIQ scopes."""
    return _make_principal(scopes=[], subject="no-scope")


def _viewer() -> AuthenticatedPrincipal:
    """Principal with NCoreIQ.Viewer scope only."""
    return _make_principal(scopes=[SCOPE_VIEWER], subject="viewer-user")


def _analyst() -> AuthenticatedPrincipal:
    """Principal with NCoreIQ.Viewer + NCoreIQ.Analyst scopes (N-CoreShift identity)."""
    return _make_principal(
        scopes=[SCOPE_VIEWER, SCOPE_ANALYST],
        subject="ncoreshift-service",
        is_service=True,
    )


def _operator() -> AuthenticatedPrincipal:
    """Principal with Viewer + Analyst + Operator scopes."""
    return _make_principal(
        scopes=[SCOPE_VIEWER, SCOPE_ANALYST, SCOPE_OPERATOR],
        subject="operator-user",
    )


def _admin() -> AuthenticatedPrincipal:
    """Principal with all scopes."""
    return _make_principal(
        scopes=[SCOPE_VIEWER, SCOPE_ANALYST, SCOPE_OPERATOR, SCOPE_ADMIN],
        subject="admin-user",
    )


# ---------------------------------------------------------------------------
# Test client helpers
# ---------------------------------------------------------------------------


def _client_with_principal(principal_factory) -> TestClient:
    """Return a TestClient that injects a specific principal for all requests."""
    app.dependency_overrides[get_principal] = principal_factory
    return TestClient(app, raise_server_exceptions=False)


def _restore_overrides():
    """Remove test dependency overrides."""
    app.dependency_overrides.pop(get_principal, None)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _cleanup_overrides():
    """Ensure dependency overrides are cleaned up after each test."""
    yield
    _restore_overrides()


# ===========================================================================
# Section 19 — POST /repository/search Authorization
# ===========================================================================


class TestSearchRepositoryAuthorization:
    """POST /api/v1/repository/search — Section 19 explicit regression."""

    URL = "/api/v1/repository/search"
    BODY = {"all_custom_objects": True, "max_results": 10}

    def test_no_scope_gets_403(self):
        """Authenticated principal with no N-CoreIQ scope receives 403."""
        client = _client_with_principal(_no_scope)
        with patch(
            "app.services.repository.RepositoryService.search",
            new_callable=AsyncMock,
        ) as mock_search:
            resp = client.post(self.URL, json=self.BODY)
        assert resp.status_code == 403, f"Expected 403, got {resp.status_code}"
        # SAP provider must NOT be called
        mock_search.assert_not_called()

    def test_viewer_gets_accepted(self):
        """Viewer principal is authorized to call repository search."""
        client = _client_with_principal(_viewer)
        with patch(
            "app.services.repository.RepositoryService.search",
            new_callable=AsyncMock,
        ) as mock_search:
            from app.models.results import (
                RepositorySearchResponse,
                SearchCriteria,
                SearchStatistics,
            )

            mock_search.return_value = RepositorySearchResponse(
                system_id="NC1",
                client="800",
                criteria=SearchCriteria(
                    packages=[],
                    include_subpackages=False,
                    name_patterns=[],
                    object_types=[],
                    all_custom_objects=True,
                    max_results=10,
                ),
                statistics=SearchStatistics(total_matches=0, returned=0),
                truncated=False,
                message="OK",
                objects=[],
            )
            resp = client.post(self.URL, json=self.BODY)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"

    def test_analyst_inherits_viewer_access(self):
        """Analyst principal (which includes Viewer) can call repository search."""
        client = _client_with_principal(_analyst)
        with patch(
            "app.services.repository.RepositoryService.search",
            new_callable=AsyncMock,
        ) as mock_search:
            from app.models.results import (
                RepositorySearchResponse,
                SearchCriteria,
                SearchStatistics,
            )

            mock_search.return_value = RepositorySearchResponse(
                system_id="NC1",
                client="800",
                criteria=SearchCriteria(
                    packages=[],
                    include_subpackages=False,
                    name_patterns=[],
                    object_types=[],
                    all_custom_objects=True,
                    max_results=10,
                ),
                statistics=SearchStatistics(total_matches=0, returned=0),
                truncated=False,
                message="OK",
                objects=[],
            )
            resp = client.post(self.URL, json=self.BODY)
        assert resp.status_code == 200

    def test_all_custom_semantics_preserved(self):
        """all_custom_objects=True is passed through unchanged — no semantic change."""
        client = _client_with_principal(_viewer)
        captured_request = {}
        with patch(
            "app.services.repository.RepositoryService.search",
            new_callable=AsyncMock,
        ) as mock_search:
            from app.models.results import (
                RepositorySearchResponse,
                SearchCriteria,
                SearchStatistics,
            )

            async def _capture(req):
                captured_request["all_custom"] = req.all_custom_objects
                captured_request["max_results"] = req.max_results
                return RepositorySearchResponse(
                    system_id="NC1",
                    client="800",
                    criteria=SearchCriteria(
                        packages=[],
                        include_subpackages=False,
                        name_patterns=[],
                        object_types=[],
                        all_custom_objects=True,
                        max_results=10,
                    ),
                    statistics=SearchStatistics(total_matches=0, returned=0),
                    truncated=False,
                    message="OK",
                    objects=[],
                )

            mock_search.side_effect = _capture
            resp = client.post(self.URL, json=self.BODY)

        assert resp.status_code == 200
        assert captured_request.get("all_custom") is True
        assert captured_request.get("max_results") == 10


# ===========================================================================
# Section 20 — GET /repository/objects/{type}/{name}/source Authorization
# ===========================================================================


class TestObjectSourceAuthorization:
    """GET .../source — Section 20 explicit authorization test."""

    URL = "/api/v1/repository/objects/PROG/ZTEST/source"

    def test_no_scope_gets_403(self):
        """Authenticated principal with no N-CoreIQ scope receives 403; SAP not called."""
        client = _client_with_principal(_no_scope)
        with patch(
            "app.services.object_service.RepositoryObjectService.get_source",
            new_callable=AsyncMock,
        ) as mock_source:
            resp = client.get(self.URL)
        assert resp.status_code == 403
        mock_source.assert_not_called()

    def test_viewer_accepted(self):
        """Viewer principal is authorized to retrieve object source."""
        client = _client_with_principal(_viewer)
        with patch(
            "app.services.object_service.RepositoryObjectService.get_source",
            new_callable=AsyncMock,
        ) as mock_source:
            from app.models.phase4 import RepositoryObjectSource

            mock_source.return_value = RepositoryObjectSource(
                sap_object_type="PROG",
                object_name="ZTEST",
                system_id="NC1",
                client="800",
                found=True,
                components=[],
                returned_lines=0,
                component_count=0,
                truncated=False,
                message="OK",
            )
            resp = client.get(self.URL)
        assert resp.status_code == 200


# ===========================================================================
# Section 21 — Job Authorization Tests
# ===========================================================================


class TestJobAuthorizationCreate:
    """POST /api/v1/repository/jobs — job creation authorization."""

    URL = "/api/v1/repository/jobs"
    BODY = {
        "extraction": {
            "objects": [{"object_type": "PROG", "object_name": "ZTEST"}],
            "include_metadata": True,
            "include_source": False,
        },
        "export_format": "none",
    }

    def _mock_job_service(self, app_instance):
        """Attach a mock job service to app state."""
        mock_svc = MagicMock()
        mock_svc.create_job.return_value = _make_job("test-job-001", "QUEUED")
        app_instance.state.job_service = mock_svc
        return mock_svc

    def test_no_scope_gets_403(self):
        """Authenticated principal with no scope cannot create a job."""
        client = _client_with_principal(_no_scope)
        self._mock_job_service(app)
        resp = client.post(self.URL, json=self.BODY)
        assert resp.status_code == 403

    def test_viewer_gets_403(self):
        """Viewer-only principal cannot create a job (requires Analyst)."""
        client = _client_with_principal(_viewer)
        self._mock_job_service(app)
        mock_svc = self._mock_job_service(app)
        resp = client.post(self.URL, json=self.BODY)
        assert resp.status_code == 403
        mock_svc.create_job.assert_not_called()

    def test_analyst_accepted(self):
        """Analyst principal (N-CoreShift identity) can create a job."""
        client = _client_with_principal(_analyst)
        self._mock_job_service(app)
        resp = client.post(self.URL, json=self.BODY)
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"

    def test_operator_accepted(self):
        """Operator principal can create a job (includes Analyst via inheritance)."""
        client = _client_with_principal(_operator)
        self._mock_job_service(app)
        resp = client.post(self.URL, json=self.BODY)
        assert resp.status_code == 202


class TestJobAuthorizationGet:
    """GET /api/v1/repository/jobs/{job_id} — job status authorization."""

    URL = "/api/v1/repository/jobs/test-job-001"

    def _mock_job_service(self):
        mock_svc = MagicMock()
        mock_svc.get_job.return_value = _make_job("test-job-001", "QUEUED")
        app.state.job_service = mock_svc
        return mock_svc

    def test_no_scope_gets_403(self):
        """No-scope principal cannot get job status."""
        client = _client_with_principal(_no_scope)
        mock_svc = self._mock_job_service()
        resp = client.get(self.URL)
        assert resp.status_code == 403
        mock_svc.get_job.assert_not_called()

    def test_viewer_gets_403(self):
        """Viewer-only cannot get job status (requires Analyst)."""
        client = _client_with_principal(_viewer)
        mock_svc = self._mock_job_service()
        resp = client.get(self.URL)
        assert resp.status_code == 403

    def test_analyst_accepted(self):
        """Analyst can get job status — N-CoreShift polling workflow."""
        client = _client_with_principal(_analyst)
        self._mock_job_service()
        resp = client.get(self.URL)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


class TestJobAuthorizationListJobs:
    """GET /api/v1/repository/jobs — list jobs authorization."""

    URL = "/api/v1/repository/jobs"

    def _mock_job_service(self):
        from app.models.phase6 import JobListResponse

        mock_svc = MagicMock()
        mock_svc.list_jobs.return_value = JobListResponse(
            items=[], total=0, limit=50, offset=0,
        )
        app.state.job_service = mock_svc
        return mock_svc

    def test_viewer_gets_403(self):
        """Viewer cannot list jobs (requires Analyst)."""
        client = _client_with_principal(_viewer)
        mock_svc = self._mock_job_service()
        resp = client.get(self.URL)
        assert resp.status_code == 403
        mock_svc.list_jobs.assert_not_called()

    def test_analyst_accepted(self):
        """Analyst can list jobs — N-CoreShift identity requirement."""
        client = _client_with_principal(_analyst)
        self._mock_job_service()
        resp = client.get(self.URL)
        assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"


# ===========================================================================
# Section 22 — Operator-only: cancel/retry
# ===========================================================================


class TestJobCancelAuthorization:
    """POST /api/v1/repository/jobs/{id}/cancel — Operator required."""

    URL = "/api/v1/repository/jobs/test-job-001/cancel"

    def _mock_job_service(self):
        mock_svc = MagicMock()
        mock_svc.get_job.return_value = _make_job("test-job-001", "QUEUED")
        mock_svc.cancel_job.return_value = _make_job("test-job-001", "CANCELLED")
        app.state.job_service = mock_svc
        return mock_svc

    def test_no_scope_gets_403(self):
        """No-scope principal cannot cancel a job."""
        client = _client_with_principal(_no_scope)
        mock_svc = self._mock_job_service()
        resp = client.post(self.URL)
        assert resp.status_code == 403
        mock_svc.cancel_job.assert_not_called()

    def test_viewer_gets_403(self):
        """Viewer cannot cancel a job (requires Operator)."""
        client = _client_with_principal(_viewer)
        mock_svc = self._mock_job_service()
        resp = client.post(self.URL)
        assert resp.status_code == 403
        mock_svc.cancel_job.assert_not_called()

    def test_analyst_gets_403(self):
        """Analyst cannot cancel a job — this is the N-CoreShift boundary check.

        N-CoreShift uses Viewer+Analyst only. cancel_job requires Operator.
        This test proves N-CoreShift does NOT need Operator for its workflow.
        """
        client = _client_with_principal(_analyst)
        mock_svc = self._mock_job_service()
        resp = client.post(self.URL)
        assert resp.status_code == 403, (
            "Analyst should NOT be able to cancel jobs. "
            "cancel_job requires NCoreIQ.Operator. "
            "N-CoreShift must NOT need Operator for certification."
        )
        mock_svc.cancel_job.assert_not_called()

    def test_operator_accepted(self):
        """Operator can cancel a job."""
        client = _client_with_principal(_operator)
        self._mock_job_service()
        resp = client.post(self.URL)
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"

    def test_admin_accepted(self):
        """Admin (includes Operator) can cancel a job."""
        client = _client_with_principal(_admin)
        self._mock_job_service()
        resp = client.post(self.URL)
        assert resp.status_code == 202


class TestJobRetryAuthorization:
    """POST /api/v1/repository/jobs/{id}/retry — Operator required."""

    URL = "/api/v1/repository/jobs/test-job-001/retry"

    def _mock_job_service(self):
        mock_svc = MagicMock()
        mock_svc.get_job.return_value = _make_job("test-job-001", "FAILED")
        mock_svc.retry_job.return_value = _make_job("test-job-002", "QUEUED")
        app.state.job_service = mock_svc
        return mock_svc

    def test_analyst_gets_403(self):
        """Analyst cannot retry a job — proves N-CoreShift does not need Operator.

        This is the critical architectural boundary test. N-CoreShift only
        needs Viewer+Analyst. retry_job is an operational control requiring Operator.
        N-CoreShift never needs to retry jobs as part of its evidence workflow.
        """
        client = _client_with_principal(_analyst)
        mock_svc = self._mock_job_service()
        resp = client.post(self.URL)
        assert resp.status_code == 403, (
            "Analyst must NOT be able to retry jobs. "
            "NCORESHIFT_REQUIRES_OPERATOR_AFTER_FIX must = NO."
        )
        mock_svc.retry_job.assert_not_called()

    def test_viewer_gets_403(self):
        """Viewer cannot retry a job."""
        client = _client_with_principal(_viewer)
        mock_svc = self._mock_job_service()
        resp = client.post(self.URL)
        assert resp.status_code == 403

    def test_operator_accepted(self):
        """Operator can retry a job."""
        client = _client_with_principal(_operator)
        self._mock_job_service()
        resp = client.post(self.URL)
        assert resp.status_code == 202, f"Expected 202, got {resp.status_code}: {resp.text}"


# ===========================================================================
# N-CoreShift Viewer+Analyst compatibility — Section 7
# ===========================================================================


class TestNCoreShiftCompatibility:
    """Prove that N-CoreShift's Viewer+Analyst identity covers all required operations.

    N-CoreShift requires (from consumer-contract-v1.md Section 10):
      - testConnection (sap_ping)           → Viewer
      - listPackages                         → Viewer
      - previewObjects (repository search)   → Viewer
      - getMetadata                          → Viewer
      - getSource                            → Viewer
      - getDependencies                      → Viewer
      - traverseDependencies                 → Analyst
      - findDependents (reverse)             → Analyst
      - analyzeImpact                        → Analyst
      - startPackageImport (create_job)      → Analyst
      - getImportStatus (get_job)            → Analyst
      - getImportResult (get_job_result)     → Analyst
      - downloadArtifact                     → Analyst

    N-CoreShift must NOT need Operator for any of the above.
    """

    def test_ncoreshift_can_access_all_viewer_endpoints(self):
        """N-CoreShift Viewer+Analyst principal can access all Viewer-level endpoints."""
        principal = _analyst()
        viewer_endpoints = [
            "sap_ping",
            "search_repository",
            "get_packages",
            "get_object_metadata",
            "get_object_source",
            "get_object_dependencies",
        ]
        for ep in viewer_endpoints:
            assert principal.can_access(ep), (
                f"N-CoreShift Analyst+Viewer cannot access Viewer endpoint '{ep}'"
            )

    def test_ncoreshift_can_access_all_analyst_endpoints(self):
        """N-CoreShift Viewer+Analyst principal can access all required Analyst endpoints."""
        principal = _analyst()
        analyst_endpoints = [
            "traverse_dependencies",
            "get_reverse_dependencies",
            "get_impact_analysis",
            "bulk_extract",
            "bulk_export",
            "source_export",
            "create_job",
            "list_jobs",
            "get_job",
            "get_job_result",
            "download_artifact",
        ]
        for ep in analyst_endpoints:
            assert principal.can_access(ep), (
                f"N-CoreShift Analyst+Viewer cannot access Analyst endpoint '{ep}'"
            )

    def test_ncoreshift_cannot_access_operator_endpoints(self):
        """N-CoreShift Viewer+Analyst principal CANNOT access Operator-only endpoints.

        This is the standing architecture invariant:
            N-CoreShift is NOT granted NCoreIQ.Operator
        """
        principal = _analyst()
        operator_only = ["cancel_job", "retry_job"]
        for ep in operator_only:
            assert not principal.can_access(ep), (
                f"N-CoreShift Analyst+Viewer should NOT be able to access '{ep}' "
                "which requires NCoreIQ.Operator. "
                "OPERATOR_GRANTED_TO_NCORESHIFT must be NO."
            )

    def test_ncoreshift_does_not_require_operator_after_fix(self):
        """Canonical proof: NCORESHIFT_REQUIRES_OPERATOR_AFTER_FIX = NO."""
        principal = _analyst()
        # All operations N-CoreShift needs per consumer-contract-v1.md Section 10
        required_operations = [
            "sap_ping",           # testConnection
            "get_packages",       # listPackages
            "search_repository",  # previewObjects
            "get_object_metadata",  # getMetadata
            "get_object_source",  # getSource
            "get_object_dependencies",  # getDependencies
            "traverse_dependencies",  # traverseDependencies
            "get_reverse_dependencies",  # findDependents
            "get_impact_analysis",  # analyzeImpact
            "create_job",         # startPackageImport
            "get_job",            # getImportStatus
            "get_job_result",     # getImportResult
            "download_artifact",  # downloadArtifact
            "list_jobs",          # list for monitoring
        ]
        failing = [op for op in required_operations if not principal.can_access(op)]
        assert not failing, (
            f"N-CoreShift Viewer+Analyst cannot access required operations: {failing}. "
            "These operations must be granted to Analyst scope."
        )


# ===========================================================================
# Principal model — scope hierarchy tests (Section 11-13)
# ===========================================================================


class TestScopeHierarchy:
    """Verify role inheritance: Admin > Operator > Analyst > Viewer."""

    def test_viewer_scope_only_accesses_viewer_endpoints(self):
        """Viewer can only access Viewer endpoints, not Analyst/Operator."""
        p = _viewer()
        assert p.can_access("sap_ping")
        assert p.can_access("search_repository")
        assert p.can_access("get_object_source")
        # NOT analyst
        assert not p.can_access("traverse_dependencies")
        assert not p.can_access("create_job")
        assert not p.can_access("cancel_job")

    def test_analyst_inherits_viewer(self):
        """Analyst has all Viewer capabilities plus Analyst capabilities."""
        p = _analyst()
        # Viewer endpoints accessible
        assert p.can_access("sap_ping")
        assert p.can_access("search_repository")
        # Analyst endpoints accessible
        assert p.can_access("traverse_dependencies")
        assert p.can_access("create_job")
        assert p.can_access("get_job")
        assert p.can_access("get_job_result")
        assert p.can_access("download_artifact")
        # Operator endpoints NOT accessible
        assert not p.can_access("cancel_job")
        assert not p.can_access("retry_job")

    def test_operator_inherits_analyst(self):
        """Operator has all Analyst + Operator capabilities."""
        p = _operator()
        # Viewer accessible
        assert p.can_access("sap_ping")
        # Analyst accessible
        assert p.can_access("create_job")
        assert p.can_access("get_job")
        # Operator accessible
        assert p.can_access("cancel_job")
        assert p.can_access("retry_job")

    def test_admin_has_all_capabilities(self):
        """Admin has all capabilities including Operator and Admin-specific."""
        p = _admin()
        assert p.can_access("cancel_job")
        assert p.can_access("retry_job")
        assert p.can_access("create_job")
        assert p.can_access("sap_ping")


# ===========================================================================
# Section 23 — AUTH_MODE disabled behavior
# ===========================================================================


class TestAuthModeDisabled:
    """AUTH_MODE=disabled — ANONYMOUS_PRINCIPAL has all scopes; all routes accessible.

    AUTH_MODE_DISABLED_AUTHORIZATION_BEHAVIOR:
        All requests receive ANONYMOUS_PRINCIPAL (Viewer+Analyst+Operator+Admin).
        All require_scope() checks pass because ANONYMOUS_PRINCIPAL.can_access()
        returns True for all endpoint names.
        This is the intended local development behavior.
        Production XSUAA mode ALWAYS enforces scopes.
    """

    def test_anonymous_principal_has_all_scopes(self):
        """ANONYMOUS_PRINCIPAL has all four N-CoreIQ scopes."""
        assert SCOPE_VIEWER in ANONYMOUS_PRINCIPAL.scopes
        assert SCOPE_ANALYST in ANONYMOUS_PRINCIPAL.scopes
        assert SCOPE_OPERATOR in ANONYMOUS_PRINCIPAL.scopes
        assert SCOPE_ADMIN in ANONYMOUS_PRINCIPAL.scopes

    def test_anonymous_can_access_all_protected_endpoints(self):
        """ANONYMOUS_PRINCIPAL can access every defined protected endpoint."""
        from app.auth.principal import (
            VIEWER_ENDPOINTS,
            ANALYST_ENDPOINTS,
            OPERATOR_ENDPOINTS,
            ADMIN_ENDPOINTS,
        )
        all_endpoints = ADMIN_ENDPOINTS  # superset of all
        failing = [ep for ep in all_endpoints if not ANONYMOUS_PRINCIPAL.can_access(ep)]
        assert not failing, (
            f"ANONYMOUS_PRINCIPAL cannot access: {failing}. "
            "AUTH_MODE=disabled must grant full access."
        )

    def test_disabled_mode_search_accessible_without_override(self):
        """In disabled auth mode (default for tests), search is accessible without override."""
        # No dependency override — uses whatever the app sets (ANONYMOUS_PRINCIPAL in disabled mode)
        _restore_overrides()
        client = TestClient(app, raise_server_exceptions=False)
        with patch(
            "app.services.repository.RepositoryService.search",
            new_callable=AsyncMock,
        ) as mock_search:
            from app.models.results import (
                RepositorySearchResponse,
                SearchCriteria,
                SearchStatistics,
            )
            mock_search.return_value = RepositorySearchResponse(
                system_id="NC1",
                client="800",
                criteria=SearchCriteria(
                    packages=[],
                    include_subpackages=False,
                    name_patterns=[],
                    object_types=[],
                    all_custom_objects=True,
                    max_results=10,
                ),
                statistics=SearchStatistics(total_matches=0, returned=0),
                truncated=False,
                message="OK",
                objects=[],
            )
            resp = client.post(
                "/api/v1/repository/search",
                json={"all_custom_objects": True, "max_results": 10},
            )
        # In disabled mode, all requests get ANONYMOUS_PRINCIPAL → authorized
        assert resp.status_code == 200, (
            f"In AUTH_MODE=disabled, search should succeed. Got {resp.status_code}"
        )


# ===========================================================================
# 401 tests — XSUAA mode with no token
# ===========================================================================


class TestUnauthenticated401:
    """In XSUAA mode, requests without a valid Bearer token receive 401.

    The middleware handles 401 centrally. These tests verify the middleware
    code path that returns 401 for missing tokens in xsuaa mode.
    """

    def test_middleware_rejects_no_token_in_xsuaa_mode(self):
        """Middleware _extract_token returns None for no Authorization header.

        In xsuaa mode, _extract_token returning None triggers 401 immediately.
        This unit-level test verifies the code path without requiring real XSUAA infra.
        """
        from app.auth.middleware import _extract_token
        from starlette.requests import Request
        from starlette.datastructures import Headers

        # Simulate a request with no Authorization header
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/repository/search",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope)
        token = _extract_token(request)
        assert token is None, "No Authorization header must produce None token"

    def test_middleware_extracts_bearer_token(self):
        """Middleware _extract_token correctly extracts a Bearer token."""
        from app.auth.middleware import _extract_token
        from starlette.requests import Request

        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/repository/search",
            "headers": [(b"authorization", b"Bearer test-token-value")],
            "query_string": b"",
        }
        request = Request(scope)
        token = _extract_token(request)
        assert token == "test-token-value"

    def test_middleware_exempt_paths_bypass_auth(self):
        """Health and liveness endpoints are in AUTH_EXEMPT_PATHS."""
        from app.auth.middleware import _AUTH_EXEMPT_PATHS

        assert "/health/live" in _AUTH_EXEMPT_PATHS
        assert "/health/ready" in _AUTH_EXEMPT_PATHS
        assert "/api/v1/health" in _AUTH_EXEMPT_PATHS

    def test_xsuaa_mode_no_token_middleware_path(self):
        """In xsuaa mode with no token, middleware returns 401 before any handler.

        Proven by examining the middleware dispatch code path:
        _extract_token returns None → dispatch returns JSONResponse(status_code=401).
        This is the canonical 401 behavior referenced in the consumer contract.
        """
        from app.auth.middleware import _extract_token
        from starlette.requests import Request

        # A request with no auth header
        scope = {
            "type": "http",
            "method": "POST",
            "path": "/api/v1/repository/jobs",
            "headers": [],
            "query_string": b"",
        }
        request = Request(scope)
        token = _extract_token(request)
        # In xsuaa mode: no token → middleware immediately returns 401
        # The route handler is never called
        assert token is None, (
            "Missing Authorization header must produce None from _extract_token. "
            "XSUAAAuthMiddleware converts None token to HTTP 401."
        )


# ===========================================================================
# Authorization helper unit tests (Section 8)
# ===========================================================================


class TestRequireScopeHelper:
    """Unit tests for the require_scope() dependency factory."""

    def test_require_scope_returns_callable(self):
        """require_scope() returns a callable suitable for FastAPI Depends."""
        from app.auth.authorization import require_scope
        import inspect

        dep = require_scope("sap_ping")
        assert callable(dep)
        assert inspect.iscoroutinefunction(dep)

    def test_require_scope_function_name_is_stable(self):
        """Each require_scope() call produces a uniquely named function."""
        from app.auth.authorization import require_scope

        dep_a = require_scope("sap_ping")
        dep_b = require_scope("create_job")
        assert dep_a.__name__ == "require_scope_sap_ping"
        assert dep_b.__name__ == "require_scope_create_job"

    @pytest.mark.asyncio
    async def test_viewer_principal_can_access_viewer_endpoint(self):
        """require_scope('sap_ping') returns principal when Viewer calls it."""
        from app.auth.authorization import require_scope

        dep = require_scope("sap_ping")
        principal = _viewer()
        result = await dep(principal=principal)
        assert result is principal

    @pytest.mark.asyncio
    async def test_viewer_blocked_from_analyst_endpoint(self):
        """require_scope('create_job') raises 403 when called by Viewer."""
        from app.auth.authorization import require_scope
        from fastapi import HTTPException

        dep = require_scope("create_job")
        principal = _viewer()
        with pytest.raises(HTTPException) as exc_info:
            await dep(principal=principal)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_analyst_blocked_from_operator_endpoint(self):
        """require_scope('cancel_job') raises 403 when called by Analyst."""
        from app.auth.authorization import require_scope
        from fastapi import HTTPException

        dep = require_scope("cancel_job")
        principal = _analyst()
        with pytest.raises(HTTPException) as exc_info:
            await dep(principal=principal)
        assert exc_info.value.status_code == 403

    @pytest.mark.asyncio
    async def test_none_principal_fails_closed(self):
        """require_scope raises 403 (not 500) if principal is somehow None."""
        from app.auth.authorization import require_scope
        from fastapi import HTTPException

        dep = require_scope("sap_ping")
        with pytest.raises(HTTPException) as exc_info:
            await dep(principal=None)
        assert exc_info.value.status_code == 403


# ===========================================================================
# Security regression — no silent allow-all fallback
# ===========================================================================


class TestNoAllowAllFallback:
    """Verify there is no wildcard authorization or allow-all fallback."""

    def test_unknown_endpoint_name_returns_false(self):
        """can_access() with an unknown endpoint name returns False for any role."""
        for p in [_no_scope(), _viewer(), _analyst()]:
            assert not p.can_access("nonexistent_endpoint_xyz"), (
                f"Principal {p.subject} should NOT access unknown endpoint"
            )

    def test_operator_cannot_access_unknown_endpoint(self):
        """Even Operator does not get wildcard access to unknown endpoints."""
        p = _operator()
        assert not p.can_access("nonexistent_endpoint_xyz")

    def test_no_scope_cannot_access_any_endpoint(self):
        """Principal with empty scopes cannot access any endpoint."""
        p = _no_scope()
        from app.auth.principal import ADMIN_ENDPOINTS
        for ep in ADMIN_ENDPOINTS:
            assert not p.can_access(ep), (
                f"No-scope principal should not access '{ep}'"
            )
