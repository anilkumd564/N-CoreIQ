"""Phase 3 — API endpoint tests for POST /api/v1/repository/search.

Tests the HTTP layer by patching RepositoryService.search so no
real SAP connection is required.

Coverage:
  - HTTP 200 success with full response schema
  - HTTP 200 with zero results
  - HTTP 200 truncated result propagated
  - HTTP 422 from Pydantic validation (empty request)
  - HTTP 422 unsupported object type
  - HTTP 422 no discovery selector
  - HTTP 503 SAP connection error
  - HTTP 503 SAP communication error
  - HTTP 503 RFC execution error
  - HTTP 501 RFC runtime unavailable
  - Response schema fields present (statistics, criteria, truncated, objects)
  - Friendly type in object response
  - Match reason in object response
  - Phase 1 / Phase 2 regression (health + ping unchanged)
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.repository import MatchReason, RepositoryObject
from app.models.results import (
    RepositorySearchResponse,
    SearchCriteria,
    SearchStatistics,
)
from app.sap.exceptions import (
    SAPCommunicationError,
    SAPConnectionError,
    SAPFunctionExecutionError,
    SAPRFCRuntimeUnavailableError,
)

client = TestClient(app)

SEARCH_URL = "/api/v1/repository/search"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_response(
    objects: list[RepositoryObject] | None = None,
    total_matches: int = 0,
    truncated: bool = False,
    message: str = "Discovery completed successfully.",
    system_id: str = "DEV",
    client_id: str = "100",
    packages: list[str] | None = None,
    object_types: list[str] | None = None,
) -> RepositorySearchResponse:
    objs = objects or []
    return RepositorySearchResponse(
        system_id=system_id,
        client=client_id,
        criteria=SearchCriteria(
            packages=packages or [],
            include_subpackages=False,
            name_patterns=[],
            object_types=object_types or [],
            all_custom_objects=False,
            max_results=1000,
        ),
        statistics=SearchStatistics(
            total_matches=total_matches or len(objs),
            returned=len(objs),
        ),
        truncated=truncated,
        message=message,
        objects=objs,
    )


def _make_prog_object(name: str = "ZFI_REPORT", package: str = "ZFINANCE") -> RepositoryObject:
    return RepositoryObject(
        sap_object_type="PROG",
        friendly_type="PROGRAM",
        object_name=name,
        package=package,
        match_reason=MatchReason.PACKAGE,
        system_id="DEV",
        client="100",
    )


# ---------------------------------------------------------------------------
# Success scenarios
# ---------------------------------------------------------------------------


def test_search_returns_200_with_objects():
    mock_resp = _make_response(
        objects=[_make_prog_object()],
        total_matches=1,
        packages=["ZFINANCE"],
    )
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        resp = client.post(
            SEARCH_URL,
            json={"packages": ["ZFINANCE"], "object_types": ["PROG"]},
        )
    assert resp.status_code == 200
    data = resp.json()
    assert data["statistics"]["returned"] == 1
    assert data["statistics"]["total_matches"] == 1
    assert len(data["objects"]) == 1
    assert data["objects"][0]["sap_object_type"] == "PROG"
    assert data["objects"][0]["friendly_type"] == "PROGRAM"
    assert data["objects"][0]["object_name"] == "ZFI_REPORT"
    assert data["objects"][0]["match_reason"] == "PACKAGE"


def test_search_200_zero_results():
    mock_resp = _make_response(
        objects=[],
        total_matches=0,
        message="No matching repository objects found.",
        packages=["ZUNKNOWN"],
    )
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        resp = client.post(SEARCH_URL, json={"packages": ["ZUNKNOWN"]})
    assert resp.status_code == 200
    data = resp.json()
    assert data["objects"] == []
    assert data["statistics"]["returned"] == 0
    assert data["truncated"] is False


def test_search_200_truncated_result():
    mock_resp = _make_response(
        objects=[_make_prog_object()],
        total_matches=5000,
        truncated=True,
        message="Result truncated at 1 of 5000.",
        packages=["ZFINANCE"],
    )
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        resp = client.post(SEARCH_URL, json={"packages": ["ZFINANCE"], "max_results": 1})
    assert resp.status_code == 200
    data = resp.json()
    assert data["truncated"] is True
    assert data["statistics"]["total_matches"] == 5000
    assert data["statistics"]["returned"] == 1


def test_search_response_contains_system_id_and_client():
    mock_resp = _make_response(packages=["ZFINANCE"])
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        resp = client.post(SEARCH_URL, json={"packages": ["ZFINANCE"]})
    data = resp.json()
    assert data["system_id"] == "DEV"
    assert data["client"] == "100"


def test_search_response_criteria_echo():
    mock_resp = _make_response(packages=["ZFINANCE"], object_types=["PROG"])
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        resp = client.post(
            SEARCH_URL,
            json={"packages": ["ZFINANCE"], "object_types": ["PROG"]},
        )
    data = resp.json()
    assert "criteria" in data
    assert "statistics" in data
    assert "truncated" in data
    assert "objects" in data
    assert "message" in data


def test_match_reason_name_pattern_in_response():
    obj = _make_prog_object()
    obj.match_reason = MatchReason.NAME_PATTERN
    mock_resp = _make_response(objects=[obj], total_matches=1)
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        resp = client.post(SEARCH_URL, json={"name_patterns": ["Z*"]})
    data = resp.json()
    assert data["objects"][0]["match_reason"] == "NAME_PATTERN"


def test_match_reason_both_in_response():
    obj = _make_prog_object()
    obj.match_reason = MatchReason.BOTH
    mock_resp = _make_response(objects=[obj], total_matches=1)
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        resp = client.post(
            SEARCH_URL,
            json={"packages": ["ZFINANCE"], "name_patterns": ["Z*"]},
        )
    data = resp.json()
    assert data["objects"][0]["match_reason"] == "BOTH"


# ---------------------------------------------------------------------------
# Validation failures — HTTP 422
# ---------------------------------------------------------------------------


def test_empty_request_returns_422():
    resp = client.post(SEARCH_URL, json={})
    assert resp.status_code == 422


def test_no_selector_returns_422():
    resp = client.post(
        SEARCH_URL,
        json={"packages": [], "name_patterns": [], "object_types": []},
    )
    assert resp.status_code == 422


def test_unsupported_object_type_returns_422():
    resp = client.post(
        SEARCH_URL,
        json={"packages": ["ZTEST"], "object_types": ["UNKNOWNTYPE"]},
    )
    assert resp.status_code == 422


def test_max_results_above_limit_returns_422():
    resp = client.post(
        SEARCH_URL,
        json={"packages": ["ZTEST"], "max_results": 99999},
    )
    assert resp.status_code == 422


def test_invalid_name_pattern_returns_422():
    resp = client.post(
        SEARCH_URL,
        json={"name_patterns": ["Z'; DROP TABLE TADIR --"]},
    )
    assert resp.status_code == 422


# ---------------------------------------------------------------------------
# SAP error scenarios
# ---------------------------------------------------------------------------


def test_sap_connection_error_returns_503():
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        side_effect=SAPConnectionError("ECC not reachable"),
    ):
        resp = client.post(SEARCH_URL, json={"packages": ["ZFINANCE"]})
    assert resp.status_code == 503


def test_sap_communication_error_returns_503():
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        side_effect=SAPCommunicationError("RFC timeout"),
    ):
        resp = client.post(SEARCH_URL, json={"packages": ["ZFINANCE"]})
    assert resp.status_code == 503


def test_sap_function_execution_error_returns_503():
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        side_effect=SAPFunctionExecutionError("Z_MCP_REPO_DISCOVER failed"),
    ):
        resp = client.post(SEARCH_URL, json={"packages": ["ZFINANCE"]})
    assert resp.status_code == 503


def test_rfc_runtime_unavailable_returns_501():
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        side_effect=SAPRFCRuntimeUnavailableError("PyRFC not installed"),
    ):
        resp = client.post(SEARCH_URL, json={"packages": ["ZFINANCE"]})
    assert resp.status_code == 501


def test_error_response_does_not_expose_stack_trace():
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        side_effect=SAPConnectionError("ECC not reachable"),
    ):
        resp = client.post(SEARCH_URL, json={"packages": ["ZFINANCE"]})
    body = resp.text
    assert "Traceback" not in body
    assert "pyrfc" not in body.lower()
    assert "password" not in body.lower()


# ---------------------------------------------------------------------------
# all_custom_objects=true
# ---------------------------------------------------------------------------


def test_all_custom_objects_true_accepted():
    mock_resp = _make_response(
        objects=[_make_prog_object()],
        total_matches=1,
    )
    with patch(
        "app.services.repository.RepositoryService.search",
        new_callable=AsyncMock,
        return_value=mock_resp,
    ):
        resp = client.post(SEARCH_URL, json={"all_custom_objects": True})
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Phase 1 / Phase 2 regression
# ---------------------------------------------------------------------------


def test_health_endpoint_still_returns_200():
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "0.3.0"


def test_health_response_has_expected_fields():
    resp = client.get("/api/v1/health")
    data = resp.json()
    assert "status" in data
    assert "app" in data
    assert "version" in data
    assert "environment" in data


def test_sap_ping_endpoint_still_exists():
    """Ping must not return 404; 200 or 503 are both acceptable without ECC."""
    resp = client.get("/api/v1/sap/ping")
    assert resp.status_code in (200, 503, 501)


def test_docs_endpoint_reachable():
    """OpenAPI /docs must be reachable (Phase 3 must not break docs)."""
    resp = client.get("/docs")
    assert resp.status_code == 200
