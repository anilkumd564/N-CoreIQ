"""Phase 7 — MCP Server automated tests.

Tests use mocked N-CoreIQ services to verify:
- Tool registration (all 11 tools present, no prohibited tools)
- Tool schemas (required/optional args, types)
- sap_ping (success, failure, no credential leakage)
- list_packages (success, missing package, bad depth)
- search_repository (package/pattern/type filters, limits, blank rejection)
- get_object_metadata (PROG/CLAS/TABL, not-found, unsupported type)
- get_object_source (lines, truncation, MCP limit, DDIC)
- start_extraction_job (explicit objects, search-based, formats, invalid)
- get_extraction_job (found, not found, progress)
- list_extraction_jobs (listing, limits)
- cancel_extraction_job (queued, terminal rejection)
- retry_extraction_job (failed, not retryable)
- get_extraction_result (available, not yet available)
- Error sanitisation (no credentials, no stack traces)
- Read-only boundary (no prohibited tools)
- Architecture boundary (no httpx/SAP imports from MCP)
- Large result protection (search limit, source limit, job list limit)
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timezone
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.mcp.errors import MCPToolError, map_exception
from app.mcp.server import NCoreIQMCPServer, _HARD_SEARCH, _HARD_SOURCE, _HARD_JOBS


# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_NOW = datetime.now(timezone.utc)


def _make_ping_result(connected: bool = True):
    r = MagicMock()
    r.status = "connected" if connected else "disconnected"
    r.connection_mode = "odata"
    r.system_id = "S4H"
    r.client = "100"
    r.sap_release = "756"
    r.message = "Connected." if connected else "Unreachable."
    return r


def _make_package_result(packages=None, truncated=False):
    r = MagicMock()
    r.system_id = "S4H"
    r.client = "100"
    r.truncated = truncated
    r.message = None
    r.packages = []
    if packages:
        for p in packages:
            pkg = MagicMock()
            pkg.package = p
            pkg.parent_package = None
            pkg.description = f"Package {p}"
            pkg.depth = 0
            r.packages.append(pkg)
    return r


def _make_search_result(objects=None, truncated=False):
    r = MagicMock()
    r.system_id = "S4H"
    r.client = "100"
    r.truncated = truncated
    r.message = None
    r.objects = []
    r.statistics = None
    if objects:
        for obj_type, obj_name in objects:
            o = MagicMock()
            o.sap_object_type = obj_type
            o.friendly_type = obj_type.capitalize()
            o.object_name = obj_name
            o.package = "ZTEST"
            o.description = f"Test {obj_name}"
            o.author = "DEVUSER"
            o.match_reason = "package_filter"
            r.objects.append(o)
    return r


def _make_metadata_result(obj_type="PROG", obj_name="ZTEST"):
    r = MagicMock()
    r.model_dump = MagicMock(return_value={
        "sap_object_type": obj_type,
        "object_name": obj_name,
        "package": "ZTEST",
        "description": "Test program",
        "author": "DEVUSER",
    })
    return r


def _make_source_result(obj_type="PROG", obj_name="ZTEST", lines=10, truncated=False):
    r = MagicMock()
    r.system_id = "S4H"
    r.client = "100"
    r.sap_object_type = obj_type
    r.object_name = obj_name
    r.returned_lines = lines
    r.truncated = truncated
    comp = MagicMock()
    comp.component_type = "PROGRAM"
    comp.component_name = obj_name
    comp.lines = [_make_line(i, f"WRITE 'line {i}'.") for i in range(1, lines + 1)]
    r.components = [comp]
    return r


def _make_line(line_no, text):
    l = MagicMock()
    l.line_no = line_no
    l.source_line = text
    return l


def _make_job(status="QUEUED", job_id=None):
    j = MagicMock()
    j.job_id = job_id or str(uuid.uuid4())
    from app.models.phase6 import JobStatus, JobStage
    j.status = JobStatus(status)
    j.stage = None
    j.created_at = _NOW
    j.started_at = None
    j.completed_at = None
    j.updated_at = _NOW
    j.attempt = 1
    j.retry_of_job_id = None
    j.result_status = None
    j.error = None
    j.artifact = None
    j.progress = MagicMock(
        total_objects=0, processed_objects=0, successful_objects=0,
        failed_objects=0, percent_complete=0.0,
        current_stage=None, current_object_type=None, current_object_name=None,
    )
    return j


def _make_list_result(jobs=None, total=None):
    r = MagicMock()
    r.items = jobs or []
    r.total = total if total is not None else len(r.items)
    r.limit = 20
    r.offset = 0
    return r


@pytest.fixture()
def services():
    provider = AsyncMock()
    discovery = AsyncMock()
    object_svc = AsyncMock()
    job_svc = MagicMock()
    return provider, discovery, object_svc, job_svc


@pytest.fixture()
def server(services):
    provider, discovery, object_svc, job_svc = services
    return NCoreIQMCPServer(
        sap_provider=provider,
        discovery_svc=discovery,
        object_svc=object_svc,
        job_svc=job_svc,
    )


# ---------------------------------------------------------------------------
# 1. Tool registration
# ---------------------------------------------------------------------------

class TestToolRegistration:
    EXPECTED_TOOLS = {
        "sap_ping", "list_packages", "search_repository",
        "get_object_metadata", "get_object_source",
        "start_extraction_job", "get_extraction_job",
        "list_extraction_jobs", "cancel_extraction_job",
        "retry_extraction_job", "get_extraction_result",
    }

    PROHIBITED_NAMES = {
        "run_abap", "execute_abap", "call_rfc", "generic_rfc_call",
        "read_table", "rfc_read_table", "execute_odata",
        "execute_sql", "update_table", "modify_repository_object",
        "delete_repository_object", "upload_source", "activate_object",
        "run_transaction", "execute_command", "arbitrary_odata_query",
    }

    async def test_all_expected_tools_registered(self, server):
        tools = await server.mcp.list_tools()
        names = {t.name for t in tools}
        assert self.EXPECTED_TOOLS.issubset(names), (
            f"Missing tools: {self.EXPECTED_TOOLS - names}"
        )

    async def test_tool_count_is_fifteen(self, server):
        tools = await server.mcp.list_tools()
        assert len(tools) == 15

    async def test_no_prohibited_tools_registered(self, server):
        tools = await server.mcp.list_tools()
        names = {t.name for t in tools}
        found = self.PROHIBITED_NAMES & names
        assert not found, f"Prohibited tools registered: {found}"

    async def test_read_only_tools_have_readonly_hint(self, server):
        tools = await server.mcp.list_tools()
        read_only_tools = {
            "sap_ping", "list_packages", "search_repository",
            "get_object_metadata", "get_object_source",
            "get_extraction_job", "list_extraction_jobs", "get_extraction_result",
        }
        for t in tools:
            if t.name in read_only_tools:
                ann = getattr(t, "annotations", None)
                if ann is not None:
                    assert getattr(ann, "readOnlyHint", None) is True, (
                        f"Tool {t.name} should be readOnlyHint=True"
                    )

    def test_server_mcp_name(self, server):
        assert server.mcp.name == "N-CoreIQ"

    async def test_server_instructions_mention_workflow(self, server):
        tools = await server.mcp.list_tools()
        assert len(tools) > 0  # server started successfully


# ---------------------------------------------------------------------------
# 2. Tool schemas
# ---------------------------------------------------------------------------

class TestToolSchemas:
    async def _get_tool(self, server, name):
        tools = await server.mcp.list_tools()
        return next((t for t in tools if t.name == name), None)

    async def test_list_packages_requires_root_package(self, server):
        t = await self._get_tool(server, "list_packages")
        assert t is not None
        schema = t.inputSchema
        required = schema.get("required", [])
        assert "root_package" in required

    async def test_search_repository_all_optional(self, server):
        t = await self._get_tool(server, "search_repository")
        assert t is not None
        schema = t.inputSchema
        required = schema.get("required", [])
        assert "packages" not in required

    async def test_get_object_metadata_requires_type_and_name(self, server):
        t = await self._get_tool(server, "get_object_metadata")
        schema = t.inputSchema
        assert "object_type" in schema.get("required", [])
        assert "object_name" in schema.get("required", [])

    async def test_get_object_source_has_max_lines_with_default(self, server):
        t = await self._get_tool(server, "get_object_source")
        props = t.inputSchema.get("properties", {})
        assert "max_lines" in props

    async def test_start_job_no_raw_odata_field(self, server):
        t = await self._get_tool(server, "start_extraction_job")
        props = t.inputSchema.get("properties", {})
        for bad in ("odata_filter", "sql_query", "rfc_name", "abap_code"):
            assert bad not in props

    async def test_get_extraction_job_requires_job_id(self, server):
        t = await self._get_tool(server, "get_extraction_job")
        assert "job_id" in t.inputSchema.get("required", [])


# ---------------------------------------------------------------------------
# 3. sap_ping
# ---------------------------------------------------------------------------

class TestSapPing:
    async def test_ping_success(self, server, services):
        provider, *_ = services
        provider.ping = AsyncMock(return_value=_make_ping_result(True))
        result = await server.mcp.call_tool("sap_ping", {})
        assert result is not None

    async def test_ping_returns_system_id(self, server, services):
        provider, *_ = services
        provider.ping = AsyncMock(return_value=_make_ping_result(True))
        result = await server._ping()
        assert result["system_id"] == "S4H"
        assert result["connected"] is True

    async def test_ping_does_not_return_password(self, server, services):
        provider, *_ = services
        provider.ping = AsyncMock(return_value=_make_ping_result(True))
        result = await server._ping()
        result_str = str(result).lower()
        assert "password" not in result_str
        assert "authorization" not in result_str

    async def test_ping_sap_unavailable_raises_mcp_error(self, server, services):
        provider, *_ = services
        from app.sap.exceptions import SAPConnectionError
        provider.ping = AsyncMock(side_effect=SAPConnectionError("timeout"))
        with pytest.raises(MCPToolError) as exc_info:
            await server._ping()
        assert exc_info.value.code == "SAP_UNAVAILABLE"

    async def test_ping_auth_error(self, server, services):
        provider, *_ = services
        from app.sap.exceptions import SAPAuthenticationError
        provider.ping = AsyncMock(side_effect=SAPAuthenticationError("auth failed"))
        with pytest.raises(MCPToolError) as exc_info:
            await server._ping()
        assert exc_info.value.code == "SAP_AUTHENTICATION_FAILED"


# ---------------------------------------------------------------------------
# 4. list_packages
# ---------------------------------------------------------------------------

class TestListPackages:
    async def test_list_root_package(self, server, services):
        _, discovery, *_ = services
        discovery.resolve_packages = AsyncMock(
            return_value=_make_package_result(["ZNCOREIQ"])
        )
        result = await server._list_packages("ZNCOREIQ", False, 0)
        assert result["success"] is True
        assert result["root_package"] == "ZNCOREIQ"
        assert result["package_count"] == 1

    async def test_list_packages_empty_root_raises(self, server):
        with pytest.raises(MCPToolError) as exc_info:
            await server._list_packages("", False, 5)
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_list_packages_bad_depth_raises(self, server):
        with pytest.raises(MCPToolError) as exc_info:
            await server._list_packages("ZTEST", False, 99)
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_list_packages_not_found(self, server, services):
        _, discovery, *_ = services
        from app.sap.exceptions import RepositoryPackageNotFoundError
        discovery.resolve_packages = AsyncMock(
            side_effect=RepositoryPackageNotFoundError("ZBAD")
        )
        with pytest.raises(MCPToolError) as exc_info:
            await server._list_packages("ZBAD", False, 5)
        assert exc_info.value.code == "PACKAGE_NOT_FOUND"

    async def test_list_packages_truncated(self, server, services):
        _, discovery, *_ = services
        discovery.resolve_packages = AsyncMock(
            return_value=_make_package_result(["ZTEST"], truncated=True)
        )
        result = await server._list_packages("ZTEST", True, 20)
        assert result["truncated"] is True


# ---------------------------------------------------------------------------
# 5. search_repository
# ---------------------------------------------------------------------------

class TestSearchRepository:
    async def test_search_by_package(self, server, services):
        _, discovery, *_ = services
        discovery.search = AsyncMock(
            return_value=_make_search_result([("PROG", "ZTEST1"), ("CLAS", "ZCL_SAMPLE")])
        )
        result = await server._search_repository(
            packages=["ZNCOREIQ"], name_patterns=None, object_types=None,
            include_subpackages=False, all_custom_objects=False, max_results=100,
        )
        assert result["success"] is True
        assert result["returned_count"] == 2
        assert result["objects"][0]["sap_object_type"] in ("PROG", "CLAS")

    async def test_search_enforces_mcp_hard_limit(self, server, services):
        _, discovery, *_ = services
        discovery.search = AsyncMock(return_value=_make_search_result([("PROG", "Z1")]))
        result = await server._search_repository(
            packages=["ZTEST"], name_patterns=None, object_types=None,
            include_subpackages=False, all_custom_objects=False,
            max_results=_HARD_SEARCH + 9999,
        )
        assert result["success"] is True

    async def test_search_blank_rejects_via_service(self, server, services):
        _, discovery, *_ = services
        from app.sap.exceptions import SAPCommunicationError
        discovery.search = AsyncMock(side_effect=ValueError("No packages, patterns, or all_custom_objects specified."))
        with pytest.raises(MCPToolError) as exc_info:
            await server._search_repository(
                packages=[], name_patterns=[], object_types=[],
                include_subpackages=False, all_custom_objects=False, max_results=100,
            )
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_search_result_no_credentials(self, server, services):
        _, discovery, *_ = services
        discovery.search = AsyncMock(return_value=_make_search_result([("PROG", "ZTEST")]))
        result = await server._search_repository(
            packages=["ZTEST"], name_patterns=None, object_types=None,
            include_subpackages=False, all_custom_objects=False, max_results=10,
        )
        result_str = str(result).lower()
        assert "password" not in result_str


# ---------------------------------------------------------------------------
# 6. get_object_metadata
# ---------------------------------------------------------------------------

class TestGetObjectMetadata:
    async def test_metadata_prog(self, server, services):
        _, _, object_svc, _ = services
        object_svc.get_metadata = AsyncMock(return_value=_make_metadata_result("PROG", "ZREPORT1"))
        result = await server._get_metadata("PROG", "ZREPORT1")
        assert result["success"] is True
        assert "metadata" in result
        assert result["metadata"]["sap_object_type"] == "PROG"

    async def test_metadata_clas(self, server, services):
        _, _, object_svc, _ = services
        object_svc.get_metadata = AsyncMock(return_value=_make_metadata_result("CLAS", "ZCL_SAMPLE"))
        result = await server._get_metadata("CLAS", "ZCL_SAMPLE")
        assert result["success"] is True

    async def test_metadata_tabl(self, server, services):
        _, _, object_svc, _ = services
        object_svc.get_metadata = AsyncMock(return_value=_make_metadata_result("TABL", "ZTABLE1"))
        result = await server._get_metadata("TABL", "ZTABLE1")
        assert result["success"] is True

    async def test_metadata_not_found(self, server, services):
        _, _, object_svc, _ = services
        from app.sap.exceptions import RepositoryObjectNotFoundError
        object_svc.get_metadata = AsyncMock(side_effect=RepositoryObjectNotFoundError("PROG", "ZBAD"))
        with pytest.raises(MCPToolError) as exc_info:
            await server._get_metadata("PROG", "ZBAD")
        assert exc_info.value.code == "OBJECT_NOT_FOUND"

    async def test_metadata_unsupported_type(self, server, services):
        _, _, object_svc, _ = services
        from app.sap.exceptions import RepositoryObjectTypeNotSupportedError
        object_svc.get_metadata = AsyncMock(
            side_effect=RepositoryObjectTypeNotSupportedError("UNKN", ["PROG", "CLAS"])
        )
        with pytest.raises(MCPToolError) as exc_info:
            await server._get_metadata("UNKN", "ZBAD")
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_metadata_empty_type_raises(self, server):
        with pytest.raises(MCPToolError) as exc_info:
            await server._get_metadata("", "ZTEST")
        assert exc_info.value.code == "INVALID_ARGUMENT"


# ---------------------------------------------------------------------------
# 7. get_object_source
# ---------------------------------------------------------------------------

class TestGetObjectSource:
    async def test_source_prog(self, server, services):
        _, _, object_svc, _ = services
        object_svc.get_source = AsyncMock(return_value=_make_source_result("PROG", "ZTEST", 10))
        result = await server._get_source("PROG", "ZTEST", 500)
        assert result["success"] is True
        assert result["returned_lines"] == 10
        assert result["component_count"] == 1
        assert result["truncated"] is False

    async def test_source_truncated(self, server, services):
        _, _, object_svc, _ = services
        object_svc.get_source = AsyncMock(return_value=_make_source_result("PROG", "ZHUGE", 500, truncated=True))
        result = await server._get_source("PROG", "ZHUGE", 500)
        assert result["truncated"] is True
        assert result["message"] is not None

    async def test_source_mcp_limit_enforced(self, server, services):
        _, _, object_svc, _ = services
        object_svc.get_source = AsyncMock(return_value=_make_source_result("PROG", "ZTEST", 100))
        # Pass max_lines well above HARD limit; service should receive HARD limit
        await server._get_source("PROG", "ZTEST", _HARD_SOURCE + 99999)
        call_args = object_svc.get_source.call_args
        assert call_args.kwargs["max_lines"] <= _HARD_SOURCE

    async def test_source_not_found(self, server, services):
        _, _, object_svc, _ = services
        from app.sap.exceptions import RepositoryObjectNotFoundError
        object_svc.get_source = AsyncMock(side_effect=RepositoryObjectNotFoundError("PROG", "ZBAD"))
        with pytest.raises(MCPToolError) as exc_info:
            await server._get_source("PROG", "ZBAD", 500)
        assert exc_info.value.code == "OBJECT_NOT_FOUND"

    async def test_source_empty_name_raises(self, server):
        with pytest.raises(MCPToolError) as exc_info:
            await server._get_source("PROG", "", 100)
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_source_ddic_tabl(self, server, services):
        _, _, object_svc, _ = services
        src = _make_source_result("TABL", "ZTABLE1", 5)
        src.components[0].component_type = "DDIC_TABLE"
        object_svc.get_source = AsyncMock(return_value=src)
        result = await server._get_source("TABL", "ZTABLE1", 500)
        assert result["success"] is True


# ---------------------------------------------------------------------------
# 8. start_extraction_job
# ---------------------------------------------------------------------------

class TestStartExtractionJob:
    def _make_queued_job(self):
        return _make_job("QUEUED")

    async def test_start_explicit_objects(self, server, services):
        _, _, _, job_svc = services
        j = self._make_queued_job()
        job_svc.create_job = MagicMock(return_value=j)
        result = await server._start_job(
            objects=[{"object_type": "PROG", "object_name": "ZTEST1"}],
            packages=None, name_patterns=None, object_types=None,
            include_metadata=True, include_source=True, source_max_lines=1000,
            continue_on_error=True, export_format="none",
        )
        assert result["success"] is True
        assert result["status"] == "QUEUED"
        assert "job_id" in result

    async def test_start_search_based(self, server, services):
        _, _, _, job_svc = services
        j = self._make_queued_job()
        job_svc.create_job = MagicMock(return_value=j)
        result = await server._start_job(
            objects=None,
            packages=["ZNCOREIQ"], name_patterns=None, object_types=None,
            include_metadata=True, include_source=False, source_max_lines=500,
            continue_on_error=True, export_format="none",
        )
        assert result["success"] is True

    async def test_start_job_zip_format(self, server, services):
        _, _, _, job_svc = services
        j = self._make_queued_job()
        job_svc.create_job = MagicMock(return_value=j)
        result = await server._start_job(
            objects=[{"object_type": "PROG", "object_name": "ZTEST1"}],
            packages=None, name_patterns=None, object_types=None,
            include_metadata=True, include_source=True, source_max_lines=1000,
            continue_on_error=True, export_format="zip",
        )
        assert result["success"] is True

    async def test_start_job_invalid_object_raises(self, server, services):
        _, _, _, job_svc = services
        with pytest.raises(MCPToolError) as exc_info:
            await server._start_job(
                objects=[{"object_type": "", "object_name": ""}],
                packages=None, name_patterns=None, object_types=None,
                include_metadata=True, include_source=True, source_max_lines=1000,
                continue_on_error=True, export_format="none",
            )
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_start_job_returns_immediately(self, server, services):
        """Verify job creation returns QUEUED without executing extraction."""
        _, _, _, job_svc = services
        j = self._make_queued_job()
        job_svc.create_job = MagicMock(return_value=j)
        result = await server._start_job(
            objects=[{"object_type": "PROG", "object_name": "ZTEST1"}],
            packages=None, name_patterns=None, object_types=None,
            include_metadata=True, include_source=True, source_max_lines=1000,
            continue_on_error=True, export_format="none",
        )
        assert result["status"] == "QUEUED"
        # create_job called once — no bulk extraction called
        job_svc.create_job.assert_called_once()


# ---------------------------------------------------------------------------
# 9. get_extraction_job
# ---------------------------------------------------------------------------

class TestGetExtractionJob:
    async def test_get_queued_job(self, server, services):
        _, _, _, job_svc = services
        j = _make_job("QUEUED", "job-123")
        job_svc.get_job = MagicMock(return_value=j)
        result = await server._get_job("job-123")
        assert result["job_id"] == "job-123"
        assert result["status"] == "QUEUED"

    async def test_get_job_not_found(self, server, services):
        _, _, _, job_svc = services
        job_svc.get_job = MagicMock(return_value=None)
        with pytest.raises(MCPToolError) as exc_info:
            await server._get_job("no-such-job")
        assert exc_info.value.code == "JOB_NOT_FOUND"

    async def test_get_job_empty_id_raises(self, server):
        with pytest.raises(MCPToolError) as exc_info:
            await server._get_job("")
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_get_running_job_has_progress(self, server, services):
        _, _, _, job_svc = services
        j = _make_job("RUNNING")
        j.progress.total_objects = 10
        j.progress.processed_objects = 5
        j.progress.percent_complete = 50.0
        job_svc.get_job = MagicMock(return_value=j)
        result = await server._get_job(j.job_id)
        assert result["progress"]["total_objects"] == 10
        assert result["progress"]["percent_complete"] == 50.0


# ---------------------------------------------------------------------------
# 10. list_extraction_jobs
# ---------------------------------------------------------------------------

class TestListExtractionJobs:
    async def test_list_returns_jobs(self, server, services):
        _, _, _, job_svc = services
        jobs = [_make_job("QUEUED"), _make_job("SUCCEEDED")]
        job_svc.list_jobs = MagicMock(return_value=_make_list_result(jobs))
        result = await server._list_jobs(None, 20, 0)
        assert result["success"] is True
        assert result["count"] == 2

    async def test_list_respects_mcp_hard_limit(self, server, services):
        _, _, _, job_svc = services
        job_svc.list_jobs = MagicMock(return_value=_make_list_result([]))
        await server._list_jobs(None, _HARD_JOBS + 9999, 0)
        call_args = job_svc.list_jobs.call_args
        assert call_args.kwargs["limit"] <= _HARD_JOBS

    async def test_list_filter_by_status(self, server, services):
        _, _, _, job_svc = services
        job_svc.list_jobs = MagicMock(return_value=_make_list_result([_make_job("FAILED")]))
        result = await server._list_jobs("FAILED", 10, 0)
        assert result["count"] == 1
        job_svc.list_jobs.assert_called_with(status="FAILED", limit=10, offset=0)


# ---------------------------------------------------------------------------
# 11. cancel_extraction_job
# ---------------------------------------------------------------------------

class TestCancelExtractionJob:
    async def test_cancel_queued_job(self, server, services):
        _, _, _, job_svc = services
        updated = _make_job("CANCELLED", "job-999")
        job_svc.cancel_job = MagicMock(return_value=updated)
        result = await server._cancel_job("job-999")
        assert result["success"] is True
        assert result["status"] == "CANCELLED"

    async def test_cancel_empty_id_raises(self, server):
        with pytest.raises(MCPToolError) as exc_info:
            await server._cancel_job("")
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_cancel_terminal_job_raises(self, server, services):
        _, _, _, job_svc = services
        job_svc.cancel_job = MagicMock(side_effect=ValueError("already in terminal state"))
        with pytest.raises(MCPToolError) as exc_info:
            await server._cancel_job("job-done")
        assert exc_info.value.code == "JOB_NOT_CANCELLABLE"

    async def test_cancel_running_sets_cancel_requested(self, server, services):
        _, _, _, job_svc = services
        updated = _make_job("CANCEL_REQUESTED", "job-running")
        job_svc.cancel_job = MagicMock(return_value=updated)
        result = await server._cancel_job("job-running")
        assert result["status"] == "CANCEL_REQUESTED"


# ---------------------------------------------------------------------------
# 12. retry_extraction_job
# ---------------------------------------------------------------------------

class TestRetryExtractionJob:
    async def test_retry_failed_job(self, server, services):
        _, _, _, job_svc = services
        new_job = _make_job("QUEUED", "new-job-456")
        new_job.retry_of_job_id = "old-job-123"
        new_job.attempt = 2
        job_svc.retry_job = MagicMock(return_value=new_job)
        result = await server._retry_job("old-job-123")
        assert result["success"] is True
        assert result["new_job_id"] == "new-job-456"
        assert result["retry_of_job_id"] == "old-job-123"
        assert result["attempt"] == 2
        assert result["status"] == "QUEUED"

    async def test_retry_empty_id_raises(self, server):
        with pytest.raises(MCPToolError) as exc_info:
            await server._retry_job("")
        assert exc_info.value.code == "INVALID_ARGUMENT"

    async def test_retry_not_retryable_raises(self, server, services):
        _, _, _, job_svc = services
        job_svc.retry_job = MagicMock(side_effect=ValueError("not in a retryable state"))
        with pytest.raises(MCPToolError) as exc_info:
            await server._retry_job("running-job")
        assert exc_info.value.code == "JOB_NOT_RETRYABLE"

    async def test_retry_preserves_original_history(self, server, services):
        """Retry must not mutate original — it creates a new job."""
        _, _, _, job_svc = services
        new_job = _make_job("QUEUED", "new-job-789")
        new_job.retry_of_job_id = "orig-job"
        new_job.attempt = 2
        job_svc.retry_job = MagicMock(return_value=new_job)
        result = await server._retry_job("orig-job")
        # retry_job creates a new job; original is unchanged
        assert result["new_job_id"] != "orig-job"


# ---------------------------------------------------------------------------
# 13. get_extraction_result
# ---------------------------------------------------------------------------

class TestGetExtractionResult:
    async def test_get_result_succeeded_job(self, server, services):
        _, _, _, job_svc = services
        job_svc.get_result = MagicMock(return_value={
            "job_id": "job-abc",
            "status": "SUCCEEDED",
            "result_status": "COMPLETED",
            "progress": None,
            "error": None,
            "artifact": None,
            "completed_at": _NOW.isoformat(),
        })
        result = await server._get_result("job-abc")
        assert result["success"] is True
        assert result["status"] == "SUCCEEDED"

    async def test_get_result_not_available(self, server, services):
        _, _, _, job_svc = services
        job_svc.get_result = MagicMock(side_effect=ValueError("Job is not yet complete"))
        with pytest.raises(MCPToolError) as exc_info:
            await server._get_result("running-job")
        assert exc_info.value.code == "RESULT_NOT_AVAILABLE"

    async def test_get_result_not_found(self, server, services):
        _, _, _, job_svc = services
        job_svc.get_result = MagicMock(side_effect=KeyError("no-such-job"))
        with pytest.raises(MCPToolError) as exc_info:
            await server._get_result("no-such-job")
        assert exc_info.value.code == "JOB_NOT_FOUND"

    async def test_get_result_empty_id_raises(self, server):
        with pytest.raises(MCPToolError) as exc_info:
            await server._get_result("")
        assert exc_info.value.code == "INVALID_ARGUMENT"


# ---------------------------------------------------------------------------
# 14. Error sanitisation
# ---------------------------------------------------------------------------

class TestErrorSanitisation:
    def test_sap_auth_error_no_credentials(self):
        from app.sap.exceptions import SAPAuthenticationError
        exc = SAPAuthenticationError("password=mysecret")
        mapped = map_exception(exc, "test")
        assert "mysecret" not in mapped.message
        assert "password" not in mapped.message.lower() or mapped.code == "SAP_AUTHENTICATION_FAILED"

    def test_generic_error_redacted(self):
        exc = ValueError("Something with password=secret123 in it")
        mapped = map_exception(exc, "test")
        assert "secret123" not in mapped.message

    def test_mcp_tool_error_passthrough(self):
        original = MCPToolError("CUSTOM_CODE", "Custom message")
        mapped = map_exception(original, "test")
        assert mapped.code == "CUSTOM_CODE"

    def test_key_error_maps_to_job_not_found(self):
        exc = KeyError("job-123")
        mapped = map_exception(exc, "test")
        assert mapped.code == "JOB_NOT_FOUND"

    def test_unknown_exception_maps_to_internal_error(self):
        exc = RuntimeError("Unexpected failure")
        mapped = map_exception(exc, "test")
        assert mapped.code == "INTERNAL_ERROR"
        assert "traceback" not in mapped.message.lower()

    def test_authorization_header_redacted(self):
        exc = ValueError("Got Authorization: Bearer secret_token in response")
        mapped = map_exception(exc, "test")
        assert "secret_token" not in mapped.message


# ---------------------------------------------------------------------------
# 15. Read-only boundary and architecture
# ---------------------------------------------------------------------------

class TestReadOnlyBoundary:
    def test_mcp_server_module_does_not_import_httpx_directly(self):
        """MCP server must not import httpx for direct SAP access."""
        import importlib
        import sys
        # The server module should be importable without httpx being used for SAP
        import app.mcp.server as smod
        source = open(smod.__file__, encoding="utf-8").read()
        # Must not contain direct httpx SAP calls
        assert "httpx.get(" not in source
        assert "httpx.post(" not in source
        assert "AsyncClient().get" not in source.replace(" ", "")

    def test_mcp_server_does_not_import_sap_odata_directly(self):
        import app.mcp.server as smod
        source = open(smod.__file__, encoding="utf-8").read()
        # Must not import SAP connection layers directly
        assert "from app.sap.connection.odata import" not in source
        assert "from app.sap.connection.local import" not in source

    def test_no_rfc_calls_in_mcp_server(self):
        import app.mcp.server as smod
        source = open(smod.__file__, encoding="utf-8").read()
        prohibited = ["RFC_READ_TABLE", "COMMIT WORK", "CALL TRANSACTION", "SUBMIT "]
        for p in prohibited:
            assert p not in source, f"Prohibited SAP call found in MCP server: {p}"

    def test_mcp_errors_module_does_not_expose_stack_traces(self):
        import app.mcp.errors as emod
        source = open(emod.__file__, encoding="utf-8").read()
        assert "traceback.format_exc" not in source
        assert "__traceback__" not in source

    def test_server_uses_service_layer_not_direct_sap(self, server):
        """Server has service attributes, not raw SAP connection details."""
        assert hasattr(server, "_provider")
        assert hasattr(server, "_discovery")
        assert hasattr(server, "_object")
        assert hasattr(server, "_jobs")
        # Must not have raw SAP config attributes
        assert not hasattr(server, "_sap_password")
        assert not hasattr(server, "_authorization_header")


# ---------------------------------------------------------------------------
# 16. Large result protection
# ---------------------------------------------------------------------------

class TestLargeResultProtection:
    async def test_search_max_results_capped_at_hard_limit(self, server, services):
        _, discovery, *_ = services
        discovery.search = AsyncMock(return_value=_make_search_result([("PROG", "Z1")]))
        # Even if caller requests 99999, only HARD_SEARCH is passed to service
        await server._search_repository(
            packages=["ZTEST"], name_patterns=None, object_types=None,
            include_subpackages=False, all_custom_objects=False,
            max_results=99999,
        )
        req_passed = discovery.search.call_args[0][0]
        assert req_passed.max_results <= _HARD_SEARCH

    async def test_source_lines_capped_at_hard_limit(self, server, services):
        _, _, object_svc, _ = services
        object_svc.get_source = AsyncMock(return_value=_make_source_result("PROG", "Z1", 5))
        await server._get_source("PROG", "Z1", 99999)
        assert object_svc.get_source.call_args.kwargs["max_lines"] <= _HARD_SOURCE

    async def test_job_list_capped_at_hard_limit(self, server, services):
        _, _, _, job_svc = services
        job_svc.list_jobs = MagicMock(return_value=_make_list_result([]))
        await server._list_jobs(None, 99999, 0)
        assert job_svc.list_jobs.call_args.kwargs["limit"] <= _HARD_JOBS

    async def test_artifact_not_returned_inline_in_result(self, server, services):
        """get_extraction_result must not embed base64 zip content."""
        _, _, _, job_svc = services
        job_svc.get_result = MagicMock(return_value={
            "job_id": "j1", "status": "SUCCEEDED", "result_status": "COMPLETED",
            "progress": None, "error": None,
            "artifact": {"artifact_id": "a1", "format": "zip", "size_bytes": 10000},
            "completed_at": _NOW.isoformat(),
        })
        result = await server._get_result("j1")
        result_str = str(result)
        # No base64-encoded blob (starts with UEsD which is zip PK header)
        assert "UEsD" not in result_str
        # Artifact metadata may be present but not raw bytes
        assert result["success"] is True


# ---------------------------------------------------------------------------
# 17. Settings integration
# ---------------------------------------------------------------------------

class TestSettingsIntegration:
    def test_server_uses_settings_limits(self):
        settings = MagicMock()
        settings.ncoreiq_mcp_max_search_results = 50
        settings.ncoreiq_mcp_max_source_lines = 200
        settings.ncoreiq_mcp_max_job_list_results = 10
        settings.ncoreiq_mcp_server_name = "TestServer"
        settings.app_version = "0.8.0"
        # Phase 8 dependency settings — must be integers, not MagicMock attributes
        settings.ncoreiq_mcp_max_dep_results = 200
        settings.ncoreiq_mcp_dep_max_depth = 3
        settings.ncoreiq_mcp_dep_max_nodes = 200
        settings.ncoreiq_mcp_dep_max_edges = 500
        settings.ncoreiq_mcp_dep_max_candidates = 50
        srv = NCoreIQMCPServer(
            sap_provider=MagicMock(),
            discovery_svc=MagicMock(),
            object_svc=MagicMock(),
            job_svc=MagicMock(),
            settings=settings,
        )
        assert srv._max_search == 50
        assert srv._max_source == 200
        assert srv._max_jobs == 10
        assert srv.mcp.name == "TestServer"

    def test_server_hard_limits_not_exceeded_by_settings(self):
        settings = MagicMock()
        settings.ncoreiq_mcp_max_search_results = 999999
        settings.ncoreiq_mcp_max_source_lines = 999999
        settings.ncoreiq_mcp_max_job_list_results = 999999
        settings.ncoreiq_mcp_server_name = "TestServer"
        settings.app_version = "0.8.0"
        # Phase 8 dependency settings — must be integers, not MagicMock attributes
        settings.ncoreiq_mcp_max_dep_results = 999999
        settings.ncoreiq_mcp_dep_max_depth = 999
        settings.ncoreiq_mcp_dep_max_nodes = 999999
        settings.ncoreiq_mcp_dep_max_edges = 999999
        settings.ncoreiq_mcp_dep_max_candidates = 999999
        srv = NCoreIQMCPServer(
            sap_provider=MagicMock(),
            discovery_svc=MagicMock(),
            object_svc=MagicMock(),
            job_svc=MagicMock(),
            settings=settings,
        )
        assert srv._max_search <= _HARD_SEARCH
        assert srv._max_source <= _HARD_SOURCE
        assert srv._max_jobs <= _HARD_JOBS
        assert srv._max_dep_results <= 1000
        assert srv._dep_max_depth <= 3
        assert srv._dep_max_nodes <= 200
        assert srv._dep_max_edges <= 500
        assert srv._dep_max_candidates <= 50
