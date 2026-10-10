"""Phase 7 — N-CoreIQ MCP Server (FastMCP 1.30+).

Architecture: MCP Tool → N-CoreIQ Service → SAP Provider → SAP OData
All SAP operations are strictly read-only.

Start: python -m app.mcp.server

Client config example:
    {"mcpServers": {"ncoreiq": {"command": "python", "args": ["-m", "app.mcp.server"]}}}
SAP credentials remain in .env — never in MCP config.
"""
from __future__ import annotations

import logging
from typing import Annotated, Optional

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from app.mcp.errors import MCPToolError, map_exception

logger = logging.getLogger(__name__)

MCP_SCHEMA_VERSION = "1"
_DEFAULT_SEARCH = 100
_DEFAULT_SOURCE = 500
_DEFAULT_JOBS = 20
_HARD_SEARCH = 1000
_HARD_SOURCE = 5000
_HARD_JOBS = 100

_RO = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True)
_JM = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)


class NCoreIQMCPServer:
    """N-CoreIQ MCP server — wraps FastMCP with 15 registered tools.

    Services injected at construction for testability:
      sap_provider  — ping
      discovery_svc — packages, search
      object_svc    — metadata, source
      job_svc       — all job operations
      dep_svc       — dependency intelligence (Phase 8)
    """

    def __init__(self, sap_provider, discovery_svc, object_svc, job_svc, settings=None, dep_svc=None):
        self._provider = sap_provider
        self._discovery = discovery_svc
        self._object = object_svc
        self._jobs = job_svc
        self._dep = dep_svc
        self._settings = settings

        if settings is not None:
            self._max_search = min(getattr(settings, "ncoreiq_mcp_max_search_results", _DEFAULT_SEARCH), _HARD_SEARCH)
            self._max_source = min(getattr(settings, "ncoreiq_mcp_max_source_lines", _DEFAULT_SOURCE), _HARD_SOURCE)
            self._max_jobs = min(getattr(settings, "ncoreiq_mcp_max_job_list_results", _DEFAULT_JOBS), _HARD_JOBS)
            self._max_dep_results = min(getattr(settings, "ncoreiq_mcp_max_dep_results", 200), 1000)
            self._dep_max_depth = min(getattr(settings, "ncoreiq_mcp_dep_max_depth", 3), 3)
            self._dep_max_nodes = min(getattr(settings, "ncoreiq_mcp_dep_max_nodes", 200), 200)
            self._dep_max_edges = min(getattr(settings, "ncoreiq_mcp_dep_max_edges", 500), 500)
            self._dep_max_candidates = min(getattr(settings, "ncoreiq_mcp_dep_max_candidates", 50), 50)
            name = getattr(settings, "ncoreiq_mcp_server_name", "N-CoreIQ")
            version = getattr(settings, "app_version", "1.0.0")
        else:
            self._max_search = _DEFAULT_SEARCH
            self._max_source = _DEFAULT_SOURCE
            self._max_jobs = _DEFAULT_JOBS
            self._max_dep_results = 200
            self._dep_max_depth = 3
            self._dep_max_nodes = 200
            self._dep_max_edges = 500
            self._dep_max_candidates = 50
            name = "N-CoreIQ"
            version = "1.0.0"

        self._version = version
        self.mcp = FastMCP(
            name=name,
            instructions=(
                "SAP Repository Intelligence — controlled read-only access to SAP ECC repository. "
                f"Version: {version}. "
                "Workflow: search_repository → get_object_metadata → get_object_source. "
                "Dependencies: get_dependencies → traverse_dependencies | find_dependents | analyze_dependency_impact. "
                "Bulk extraction: start_extraction_job → poll get_extraction_job → get_extraction_result."
            ),
        )
        self._register_tools()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def _register_tools(self):
        m = self.mcp
        s = self

        # 1. sap_ping
        async def sap_ping() -> dict:
            """Test SAP connectivity. Returns system_id, client, sap_release. Read-only."""
            return await s._ping()
        m.add_tool(sap_ping, name="sap_ping",
                   description="Test N-CoreIQ → SAP ECC connectivity. Returns system_id, client, release. Read-only.",
                   annotations=_RO)

        # 2. list_packages
        async def list_packages(
            root_package: Annotated[str, "Root SAP package name (e.g. ZNCOREIQ)."],
            include_subpackages: Annotated[bool, "Recursively include child packages."] = False,
            max_depth: Annotated[int, "Recursion depth 0-20."] = 20,
        ) -> dict:
            """Resolve SAP package hierarchy. Read-only."""
            return await s._list_packages(root_package, include_subpackages, max_depth)
        m.add_tool(list_packages, name="list_packages",
                   description="Resolve SAP development package hierarchy. Use before search_repository. Read-only.",
                   annotations=_RO)

        # 3. search_repository
        async def search_repository(
            packages: Annotated[Optional[list[str]], "SAP package names to search."] = None,
            name_patterns: Annotated[Optional[list[str]], "Object name wildcards e.g. ['Z*']."] = None,
            object_types: Annotated[Optional[list[str]], "SAP types e.g. ['PROG','CLAS']."] = None,
            include_subpackages: Annotated[bool, "Include sub-package objects."] = False,
            all_custom_objects: Annotated[bool, "Search all Z*/Y* objects (use cautiously)."] = False,
            max_results: Annotated[int, "Max results 1-1000. Default 100."] = 100,
        ) -> dict:
            """Search SAP repository for custom objects. At least one selector required. Read-only."""
            return await s._search_repository(packages, name_patterns, object_types,
                                               include_subpackages, all_custom_objects, max_results)
        m.add_tool(search_repository, name="search_repository",
                   description=(
                       "Search the SAP repository for custom objects using package, name pattern, "
                       "and object-type filters. Use before get_object_metadata or get_object_source. Read-only."
                   ), annotations=_RO)

        # 4. get_object_metadata
        async def get_object_metadata(
            object_type: Annotated[str, "SAP object type: PROG, CLAS, TABL, FUNC, INTF, FUGR, etc."],
            object_name: Annotated[str, "Exact SAP object name (auto-uppercased)."],
        ) -> dict:
            """Get technical metadata for one SAP object. Read-only."""
            return await s._get_metadata(object_type, object_name)
        m.add_tool(get_object_metadata, name="get_object_metadata",
                   description="Get technical metadata for one SAP repository object. Use search_repository first if unsure of name. Read-only.",
                   annotations=_RO)

        # 5. get_object_source
        async def get_object_source(
            object_type: Annotated[str, "SAP object type: PROG, CLAS, TABL, FUNC, etc."],
            object_name: Annotated[str, "Exact SAP object name."],
            max_lines: Annotated[int, f"Max lines 1-{_HARD_SOURCE}. Default {_DEFAULT_SOURCE}."] = _DEFAULT_SOURCE,
        ) -> dict:
            """Get ABAP source or DDIC technical definition. May be truncated. Read-only."""
            return await s._get_source(object_type, object_name, max_lines)
        m.add_tool(get_object_source, name="get_object_source",
                   description=(
                       f"Get source code or technical definition for one SAP object. "
                       f"Max {_HARD_SOURCE} lines. Truncated results include truncated=true. Read-only."
                   ), annotations=_RO)

        # 6. start_extraction_job
        async def start_extraction_job(
            objects: Annotated[Optional[list[dict]], "Explicit list: [{object_type, object_name}, ...]."] = None,
            packages: Annotated[Optional[list[str]], "Search-based: package names."] = None,
            name_patterns: Annotated[Optional[list[str]], "Search-based: name patterns."] = None,
            object_types: Annotated[Optional[list[str]], "Search-based: type filter."] = None,
            include_metadata: Annotated[bool, "Extract metadata."] = True,
            include_source: Annotated[bool, "Extract source."] = True,
            source_max_lines: Annotated[int, "Max source lines per object."] = 5000,
            continue_on_error: Annotated[bool, "Continue when objects fail."] = True,
            export_format: Annotated[str, "Export: none, json, or zip."] = "none",
        ) -> dict:
            """Start async extraction job. Returns job_id immediately. Poll with get_extraction_job."""
            return await s._start_job(objects, packages, name_patterns, object_types,
                                       include_metadata, include_source, source_max_lines,
                                       continue_on_error, export_format)
        m.add_tool(start_extraction_job, name="start_extraction_job",
                   description="Start async bulk extraction job. Returns job_id. SAP is not modified.",
                   annotations=_JM)

        # 7. get_extraction_job
        async def get_extraction_job(
            job_id: Annotated[str, "Job ID from start_extraction_job."],
        ) -> dict:
            """Get job status and progress. Poll until SUCCEEDED/FAILED/CANCELLED."""
            return await s._get_job(job_id)
        m.add_tool(get_extraction_job, name="get_extraction_job",
                   description="Get status and progress of an extraction job. Poll until terminal state.",
                   annotations=_RO)

        # 8. list_extraction_jobs
        async def list_extraction_jobs(
            status: Annotated[Optional[str], "Filter: QUEUED, RUNNING, SUCCEEDED, FAILED, CANCELLED."] = None,
            limit: Annotated[int, "Max results 1-100. Default 20."] = 20,
            offset: Annotated[int, "Pagination offset."] = 0,
        ) -> dict:
            """List recent extraction jobs newest-first."""
            return await s._list_jobs(status, limit, offset)
        m.add_tool(list_extraction_jobs, name="list_extraction_jobs",
                   description="List recent extraction jobs newest-first. Filter by status, paginate with limit/offset.",
                   annotations=_RO)

        # 9. cancel_extraction_job
        async def cancel_extraction_job(
            job_id: Annotated[str, "Job ID to cancel."],
        ) -> dict:
            """Cancel a QUEUED or RUNNING job cooperatively."""
            return await s._cancel_job(job_id)
        m.add_tool(cancel_extraction_job, name="cancel_extraction_job",
                   description="Cancel a queued or running extraction job. Running jobs stop at next safe checkpoint.",
                   annotations=_JM)

        # 10. retry_extraction_job
        async def retry_extraction_job(
            job_id: Annotated[str, "Job ID of FAILED or CANCELLED job to retry."],
        ) -> dict:
            """Create new job as retry of failed/cancelled job. Original preserved."""
            return await s._retry_job(job_id)
        m.add_tool(retry_extraction_job, name="retry_extraction_job",
                   description="Retry a FAILED or CANCELLED job. Creates new job with same request. Original preserved.",
                   annotations=_JM)

        # 11. get_extraction_result
        async def get_extraction_result(
            job_id: Annotated[str, "Job ID of completed job."],
        ) -> dict:
            """Get extraction result summary. Job must be in terminal state."""
            return await s._get_result(job_id)
        m.add_tool(get_extraction_result, name="get_extraction_result",
                   description="Get extraction result summary for a completed job. Large artifacts not returned inline.",
                   annotations=_RO)

        # 12. get_dependencies (Phase 8)
        async def get_dependencies(
            object_type: Annotated[str, "SAP object type: PROG, CLAS, INTF, FUGR, FUNC, TABL, VIEW, DTEL, TRAN."],
            object_name: Annotated[str, "Exact SAP object name (auto-uppercased)."],
            relationship_types: Annotated[Optional[list[str]], "Optional relationship type filter (e.g. CALLS_FUNCTION)."] = None,
            min_confidence: Annotated[Optional[str], "Minimum confidence: EXACT, HIGH, MEDIUM, LOW."] = None,
            max_results: Annotated[int, "Maximum dependency rows (1-1000, default 200)."] = 200,
        ) -> dict:
            """Get direct SAP dependency relationships for one object. Read-only."""
            return await s._get_dependencies(object_type, object_name, relationship_types, min_confidence, max_results)
        m.add_tool(get_dependencies, name="get_dependencies",
                   description=(
                       "Get direct SAP dependency relationships for one repository object. "
                       "Returns relationship types, confidence levels, evidence, and dynamic flags. Read-only."
                   ), annotations=_RO)

        # 13. traverse_dependencies (Phase 8)
        async def traverse_dependencies(
            object_type: Annotated[str, "SAP object type of root object."],
            object_name: Annotated[str, "SAP object name of root object."],
            max_depth: Annotated[int, "Maximum traversal depth (1-3 for MCP)."] = 2,
            relationship_types: Annotated[Optional[list[str]], "Optional relationship type filter."] = None,
            min_confidence: Annotated[Optional[str], "Minimum confidence filter."] = None,
            max_nodes: Annotated[int, "Maximum graph nodes (1-200 for MCP)."] = 100,
            max_edges: Annotated[int, "Maximum graph edges (1-500 for MCP)."] = 300,
        ) -> dict:
            """Bounded forward BFS traversal of SAP dependency graph. Read-only."""
            return await s._traverse_dependencies(
                object_type, object_name, max_depth, relationship_types,
                min_confidence, max_nodes, max_edges
            )
        m.add_tool(traverse_dependencies, name="traverse_dependencies",
                   description=(
                       "Bounded BFS forward traversal of SAP dependency relationships. "
                       "Detects cycles, handles dynamic targets, uses request-local cache. Read-only."
                   ), annotations=_RO)

        # 14. find_dependents (Phase 8)
        async def find_dependents(
            object_type: Annotated[str, "SAP object type of target."],
            object_name: Annotated[str, "SAP object name to find dependents of."],
            packages: Annotated[Optional[list[str]], "Package scope for candidate search."] = None,
            name_patterns: Annotated[Optional[list[str]], "Name pattern scope (e.g. Z*)."] = None,
            all_custom_objects: Annotated[bool, "Search all Z*/Y* (explicit)."] = False,
            relationship_types: Annotated[Optional[list[str]], "Optional relationship type filter."] = None,
            min_confidence: Annotated[Optional[str], "Minimum confidence filter."] = None,
            max_candidates: Annotated[int, "Maximum candidate objects to inspect (1-50 for MCP)."] = 50,
        ) -> dict:
            """Find SAP objects that depend on the specified target. Bounded scope required. Read-only."""
            return await s._find_dependents(
                object_type, object_name, packages, name_patterns,
                all_custom_objects, relationship_types, min_confidence, max_candidates
            )
        m.add_tool(find_dependents, name="find_dependents",
                   description=(
                       "Find SAP repository objects that depend on the specified target object. "
                       "Requires a bounded scope (packages or patterns). Read-only."
                   ), annotations=_RO)

        # 15. analyze_dependency_impact (Phase 8)
        async def analyze_dependency_impact(
            object_type: Annotated[str, "SAP object type of the object to analyse."],
            object_name: Annotated[str, "SAP object name."],
            packages: Annotated[Optional[list[str]], "Package scope for impact analysis."] = None,
            name_patterns: Annotated[Optional[list[str]], "Name pattern scope."] = None,
            all_custom_objects: Annotated[bool, "Search all Z*/Y* (explicit)."] = False,
            relationship_types: Annotated[Optional[list[str]], "Optional relationship type filter."] = None,
            min_confidence: Annotated[Optional[str], "Minimum confidence filter."] = None,
        ) -> dict:
            """Deterministic technical impact analysis. No AI. Read-only."""
            return await s._analyze_dependency_impact(
                object_type, object_name, packages, name_patterns,
                all_custom_objects, relationship_types, min_confidence
            )
        m.add_tool(analyze_dependency_impact, name="analyze_dependency_impact",
                   description=(
                       "Deterministic technical impact analysis for a SAP object. "
                       "Returns dependent counts, relationship distributions, and confidence stats. No AI/LLM. Read-only."
                   ), annotations=_RO)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _ping(self) -> dict:
        try:
            r = await self._provider.ping()
            return {
                "success": True, "operation": "sap_ping",
                "connected": r.status == "connected",
                "connection_mode": r.connection_mode,
                "system_id": r.system_id, "client": r.client,
                "sap_release": r.sap_release, "message": r.message,
            }
        except Exception as exc:
            raise map_exception(exc, "sap_ping") from exc

    async def _list_packages(self, root_package: str, include_subpackages: bool, max_depth: int) -> dict:
        if not (root_package or "").strip():
            raise MCPToolError("INVALID_ARGUMENT", "root_package is required.")
        if not (0 <= max_depth <= 20):
            raise MCPToolError("INVALID_ARGUMENT", "max_depth must be 0–20.")
        try:
            r = await self._discovery.resolve_packages(
                package=root_package.strip().upper(),
                include_subpackages=include_subpackages,
                max_depth=max_depth,
            )
            pkgs = []
            for p in getattr(r, "packages", []):
                pkgs.append({
                    "package": getattr(p, "package", getattr(p, "package_name", str(p))),
                    "parent_package": getattr(p, "parent_package", None),
                    "description": getattr(p, "description", None),
                    "depth": getattr(p, "depth", 0),
                })
            return {
                "success": True, "operation": "list_packages",
                "system_id": getattr(r, "system_id", None), "client": getattr(r, "client", None),
                "root_package": root_package.strip().upper(),
                "truncated": getattr(r, "truncated", False),
                "package_count": len(pkgs), "packages": pkgs,
            }
        except MCPToolError:
            raise
        except Exception as exc:
            raise map_exception(exc, "list_packages") from exc

    async def _search_repository(self, packages, name_patterns, object_types,
                                   include_subpackages, all_custom_objects, max_results) -> dict:
        from app.models.requests import RepositorySearchRequest
        effective = min(int(max_results), self._max_search)
        try:
            req = RepositorySearchRequest(
                packages=packages or [], name_patterns=name_patterns or [],
                object_types=object_types or [], include_subpackages=include_subpackages,
                all_custom_objects=all_custom_objects, max_results=effective,
            )
        except Exception as exc:
            raise MCPToolError("INVALID_ARGUMENT", str(exc)) from exc
        try:
            r = await self._discovery.search(req)
        except Exception as exc:
            raise map_exception(exc, "search_repository") from exc

        raw = getattr(r, "objects", [])
        truncated = getattr(r, "truncated", False)
        if len(raw) >= effective:
            truncated = True
        objs = []
        for o in raw:
            objs.append({
                "sap_object_type": getattr(o, "sap_object_type", None),
                "friendly_type": getattr(o, "friendly_type", None),
                "object_name": getattr(o, "object_name", None),
                "package": getattr(o, "package", None),
                "description": getattr(o, "description", None),
                "author": getattr(o, "author", None),
                "match_reason": getattr(o, "match_reason", None),
            })
        stats = getattr(r, "statistics", None)
        return {
            "success": True, "operation": "search_repository",
            "system_id": getattr(r, "system_id", None), "client": getattr(r, "client", None),
            "returned_count": len(objs),
            "total_matches": getattr(stats, "total_matches", len(objs)) if stats else len(objs),
            "truncated": truncated,
            "message": (f"Truncated at {effective}. Narrow criteria." if truncated else getattr(r, "message", None)),
            "objects": objs,
        }

    async def _get_metadata(self, object_type: str, object_name: str) -> dict:
        if not (object_type or "").strip():
            raise MCPToolError("INVALID_ARGUMENT", "object_type is required.")
        if not (object_name or "").strip():
            raise MCPToolError("INVALID_ARGUMENT", "object_name is required.")
        try:
            meta = await self._object.get_metadata(
                object_type=object_type.strip().upper(),
                object_name=object_name.strip().upper(),
            )
            data = meta.model_dump(mode="json") if hasattr(meta, "model_dump") else dict(meta)
            return {"success": True, "operation": "get_object_metadata", "metadata": data}
        except MCPToolError:
            raise
        except Exception as exc:
            raise map_exception(exc, "get_object_metadata") from exc

    async def _get_source(self, object_type: str, object_name: str, max_lines: int) -> dict:
        if not (object_type or "").strip():
            raise MCPToolError("INVALID_ARGUMENT", "object_type is required.")
        if not (object_name or "").strip():
            raise MCPToolError("INVALID_ARGUMENT", "object_name is required.")
        effective = min(int(max_lines), self._max_source)
        try:
            src = await self._object.get_source(
                object_type=object_type.strip().upper(),
                object_name=object_name.strip().upper(),
                max_lines=effective,
            )
            comps = []
            for c in getattr(src, "components", []):
                lines = [{"line_no": getattr(ln, "line_no", 0), "source_line": getattr(ln, "source_line", "")}
                         for ln in sorted(getattr(c, "lines", []), key=lambda l: getattr(l, "line_no", 0))]
                comps.append({
                    "component_type": getattr(c, "component_type", None),
                    "component_name": getattr(c, "component_name", None),
                    "line_count": len(lines), "lines": lines,
                })
            trunc = getattr(src, "truncated", False)
            return {
                "success": True, "operation": "get_object_source",
                "system_id": getattr(src, "system_id", None), "client": getattr(src, "client", None),
                "sap_object_type": getattr(src, "sap_object_type", object_type.upper()),
                "object_name": getattr(src, "object_name", object_name.upper()),
                "returned_lines": getattr(src, "returned_lines", sum(len(c["lines"]) for c in comps)),
                "component_count": len(comps), "truncated": trunc,
                "message": f"Source truncated at {effective} lines." if trunc else None,
                "components": comps,
            }
        except MCPToolError:
            raise
        except Exception as exc:
            raise map_exception(exc, "get_object_source") from exc

    async def _start_job(self, objects, packages, name_patterns, object_types,
                          include_metadata, include_source, source_max_lines,
                          continue_on_error, export_format) -> dict:
        from app.models.phase5 import BulkExtractionRequest, RepositoryObjectIdentifier
        from app.models.phase6 import ExtractionJobRequest, ExportJobFormat
        from app.models.requests import RepositorySearchRequest

        if objects is not None:
            try:
                obj_list = [RepositoryObjectIdentifier(
                    object_type=str(o.get("object_type", "")).strip().upper(),
                    object_name=str(o.get("object_name", "")).strip().upper(),
                ) for o in objects]
            except Exception as exc:
                raise MCPToolError("INVALID_ARGUMENT", f"Invalid objects: {exc}") from exc
            try:
                extraction = BulkExtractionRequest(
                    objects=obj_list, include_metadata=include_metadata,
                    include_source=include_source, source_max_lines=source_max_lines,
                    continue_on_error=continue_on_error,
                )
            except Exception as exc:
                raise MCPToolError("INVALID_ARGUMENT", str(exc)) from exc
        else:
            try:
                search = RepositorySearchRequest(
                    packages=packages or [], name_patterns=name_patterns or [],
                    object_types=object_types or [],
                )
                extraction = BulkExtractionRequest(
                    search=search, include_metadata=include_metadata,
                    include_source=include_source, source_max_lines=source_max_lines,
                    continue_on_error=continue_on_error,
                )
            except Exception as exc:
                raise MCPToolError("INVALID_ARGUMENT", str(exc)) from exc

        fmt_map = {"none": ExportJobFormat.NONE, "json": ExportJobFormat.JSON, "zip": ExportJobFormat.ZIP}
        p6_fmt = fmt_map.get(str(export_format).lower(), ExportJobFormat.NONE)
        try:
            job_request = ExtractionJobRequest(extraction=extraction, export_format=p6_fmt)
            job = self._jobs.create_job(request=job_request)
        except Exception as exc:
            raise map_exception(exc, "start_extraction_job") from exc

        logger.info("event=mcp_job_created job_id=%s", job.job_id)
        return {
            "success": True, "operation": "start_extraction_job",
            "job_id": job.job_id, "status": job.status.value,
            "stage": job.stage.value if job.stage else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "message": f"Job {job.job_id} queued.",
        }

    async def _get_job(self, job_id: str) -> dict:
        if not (job_id or "").strip():
            raise MCPToolError("INVALID_ARGUMENT", "job_id is required.")
        job = self._jobs.get_job(str(job_id).strip())
        if job is None:
            raise MCPToolError("JOB_NOT_FOUND", f"Job '{job_id}' not found.")
        p = job.progress
        art = job.artifact
        err = job.error
        return {
            "success": True, "operation": "get_extraction_job",
            "job_id": job.job_id,
            "status": job.status.value,
            "stage": job.stage.value if job.stage else None,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "started_at": job.started_at.isoformat() if job.started_at else None,
            "completed_at": job.completed_at.isoformat() if job.completed_at else None,
            "attempt": job.attempt, "retry_of_job_id": job.retry_of_job_id,
            "progress": {
                "total_objects": p.total_objects, "processed_objects": p.processed_objects,
                "successful_objects": p.successful_objects, "failed_objects": p.failed_objects,
                "percent_complete": p.percent_complete, "current_stage": p.current_stage,
                "current_object_type": p.current_object_type, "current_object_name": p.current_object_name,
            } if p else None,
            "result_status": job.result_status,
            "artifact": {
                "artifact_id": art.artifact_id,
                "format": art.format.value if hasattr(art.format, "value") else art.format,
                "filename": art.filename, "size_bytes": art.size_bytes, "sha256": art.sha256,
            } if art else None,
            "error": {"code": err.code, "message": err.message, "retryable": err.retryable} if err else None,
        }

    async def _list_jobs(self, status, limit, offset) -> dict:
        effective = min(int(limit), self._max_jobs)
        result = self._jobs.list_jobs(status=status, limit=effective, offset=int(offset))
        items = []
        for j in result.items:
            items.append({
                "job_id": j.job_id,
                "status": j.status.value if hasattr(j.status, "value") else str(j.status),
                "stage": j.stage.value if j.stage and hasattr(j.stage, "value") else str(j.stage) if j.stage else None,
                "created_at": j.created_at.isoformat() if j.created_at else None,
                "completed_at": j.completed_at.isoformat() if j.completed_at else None,
                "attempt": j.attempt,
                "percent_complete": j.progress.percent_complete if j.progress else 0,
                "result_status": j.result_status,
            })
        return {
            "success": True, "operation": "list_extraction_jobs",
            "total": result.total, "limit": effective, "offset": offset,
            "count": len(items), "jobs": items,
        }

    async def _cancel_job(self, job_id: str) -> dict:
        if not (job_id or "").strip():
            raise MCPToolError("INVALID_ARGUMENT", "job_id is required.")
        try:
            updated = self._jobs.cancel_job(str(job_id).strip())
        except ValueError as exc:
            raise MCPToolError("JOB_NOT_CANCELLABLE", str(exc)) from exc
        except KeyError as exc:
            raise MCPToolError("JOB_NOT_FOUND", f"Job '{job_id}' not found.") from exc
        return {
            "success": True, "operation": "cancel_extraction_job",
            "job_id": updated.job_id,
            "status": updated.status.value,
            "message": f"Cancellation accepted. Status is now {updated.status.value}.",
        }

    async def _retry_job(self, job_id: str) -> dict:
        if not (job_id or "").strip():
            raise MCPToolError("INVALID_ARGUMENT", "job_id is required.")
        try:
            new_job = self._jobs.retry_job(str(job_id).strip())
        except ValueError as exc:
            raise MCPToolError("JOB_NOT_RETRYABLE", str(exc)) from exc
        except KeyError as exc:
            raise MCPToolError("JOB_NOT_FOUND", f"Job '{job_id}' not found.") from exc
        logger.info("event=mcp_job_retried original=%s new=%s", job_id, new_job.job_id)
        return {
            "success": True, "operation": "retry_extraction_job",
            "new_job_id": new_job.job_id, "retry_of_job_id": new_job.retry_of_job_id,
            "attempt": new_job.attempt, "status": new_job.status.value,
            "created_at": new_job.created_at.isoformat() if new_job.created_at else None,
        }

    async def _get_result(self, job_id: str) -> dict:
        if not (job_id or "").strip():
            raise MCPToolError("INVALID_ARGUMENT", "job_id is required.")
        try:
            result = self._jobs.get_result(str(job_id).strip())
        except KeyError as exc:
            raise MCPToolError("JOB_NOT_FOUND", f"Job '{job_id}' not found.") from exc
        except ValueError as exc:
            raise MCPToolError("RESULT_NOT_AVAILABLE", str(exc)) from exc
        return {"success": True, "operation": "get_extraction_result", **result}

    # ------------------------------------------------------------------
    # Phase 8 — Dependency handlers
    # ------------------------------------------------------------------

    def _require_dep_service(self) -> None:
        if self._dep is None:
            raise MCPToolError(
                "SERVICE_UNAVAILABLE",
                "Dependency service is not configured in this MCP server instance.",
            )

    async def _get_dependencies(
        self,
        object_type: str,
        object_name: str,
        relationship_types: list[str] | None,
        min_confidence: str | None,
        max_results: int,
    ) -> dict:
        self._require_dep_service()
        if not (object_type or "").strip():
            raise MCPToolError("INVALID_ARGUMENT", "object_type is required.")
        if not (object_name or "").strip():
            raise MCPToolError("INVALID_ARGUMENT", "object_name is required.")
        effective = min(int(max_results), self._max_dep_results)
        try:
            result = await self._dep.get_direct_dependencies(
                object_type=object_type.strip().upper(),
                object_name=object_name.strip().upper(),
                relationship_types=relationship_types,
                min_confidence=min_confidence,
                max_results=effective,
            )
            rels = []
            for r in result.relationships:
                rels.append({
                    "source_object_type": r.source_object_type,
                    "source_object_name": r.source_object_name,
                    "target_object_type": r.target_object_type,
                    "target_object_name": r.target_object_name,
                    "relationship_type": r.relationship_type.value,
                    "confidence": r.confidence.value,
                    "evidence_source": r.evidence_source,
                    "source_component": r.source_component,
                    "line_no": r.line_no,
                    "dynamic": r.dynamic,
                    "detail": r.detail,
                })
            return {
                "success": True,
                "operation": "get_dependencies",
                "system_id": result.system_id,
                "client": result.client,
                "source_object_type": result.source_object_type,
                "source_object_name": result.source_object_name,
                "total_relations": result.total_relations,
                "returned_relations": result.returned_relations,
                "truncated": result.truncated,
                "message": result.message,
                "relationships": rels,
            }
        except MCPToolError:
            raise
        except Exception as exc:
            raise map_exception(exc, "get_dependencies") from exc

    async def _traverse_dependencies(
        self,
        object_type: str,
        object_name: str,
        max_depth: int,
        relationship_types: list[str] | None,
        min_confidence: str | None,
        max_nodes: int,
        max_edges: int,
    ) -> dict:
        self._require_dep_service()
        from app.models.phase8 import DependencyTraversalRequest, RepositoryObjectReference

        # Enforce MCP-specific limits
        safe_depth = min(int(max_depth), self._dep_max_depth)
        safe_nodes = min(int(max_nodes), self._dep_max_nodes)
        safe_edges = min(int(max_edges), self._dep_max_edges)

        try:
            req = DependencyTraversalRequest(
                root_object=RepositoryObjectReference(
                    sap_object_type=object_type.strip().upper(),
                    object_name=object_name.strip().upper(),
                ),
                max_depth=safe_depth,
                relationship_types=relationship_types,
                min_confidence=min_confidence,
                max_nodes=safe_nodes,
                max_edges=safe_edges,
            )
        except Exception as exc:
            raise MCPToolError("INVALID_ARGUMENT", str(exc)) from exc

        try:
            graph = await self._dep.traverse_dependencies(req)
            nodes = [{"sap_object_type": n.sap_object_type, "object_name": n.object_name, "depth": n.depth}
                     for n in graph.nodes]
            edges = []
            for e in graph.edges:
                edges.append({
                    "source_type": e.source.sap_object_type, "source_name": e.source.object_name,
                    "target_type": e.target.sap_object_type, "target_name": e.target.object_name,
                    "relationship_type": e.relationship_type.value,
                    "confidence": e.confidence.value,
                    "dynamic": e.dynamic, "depth": e.depth, "creates_cycle": e.creates_cycle,
                })
            return {
                "success": True, "operation": "traverse_dependencies",
                "root_type": graph.root.sap_object_type, "root_name": graph.root.object_name,
                "requested_max_depth": graph.requested_max_depth,
                "depth_reached": graph.depth_reached,
                "truncated": graph.truncated,
                "cycles_detected": graph.cycles_detected,
                "warnings": graph.warnings,
                "node_count": graph.statistics.node_count,
                "edge_count": graph.statistics.edge_count,
                "sap_calls": graph.statistics.sap_calls,
                "nodes": nodes,
                "edges": edges,
            }
        except MCPToolError:
            raise
        except Exception as exc:
            raise map_exception(exc, "traverse_dependencies") from exc

    async def _find_dependents(
        self,
        object_type: str,
        object_name: str,
        packages: list[str] | None,
        name_patterns: list[str] | None,
        all_custom_objects: bool,
        relationship_types: list[str] | None,
        min_confidence: str | None,
        max_candidates: int,
    ) -> dict:
        self._require_dep_service()
        from app.models.phase8 import ReverseDependencyRequest, ReverseScopeSpec, RepositoryObjectReference

        safe_candidates = min(int(max_candidates), self._dep_max_candidates)

        try:
            scope = ReverseScopeSpec(
                packages=packages or [],
                name_patterns=name_patterns or [],
                all_custom_objects=all_custom_objects,
                max_results=safe_candidates,
            )
            req = ReverseDependencyRequest(
                target_object=RepositoryObjectReference(
                    sap_object_type=object_type.strip().upper(),
                    object_name=object_name.strip().upper(),
                ),
                scope=scope,
                relationship_types=relationship_types,
                min_confidence=min_confidence,
                max_candidates=safe_candidates,
            )
        except Exception as exc:
            raise MCPToolError("INVALID_ARGUMENT", str(exc)) from exc

        try:
            result = await self._dep.get_reverse_dependencies(req)
            dependents = [
                {
                    "sap_object_type": d.sap_object_type,
                    "object_name": d.object_name,
                    "relationship_types": d.relationship_types,
                    "confidence": d.confidence.value,
                    "dynamic": d.dynamic,
                }
                for d in result.dependents
            ]
            return {
                "success": True, "operation": "find_dependents",
                "target_type": result.target_object.sap_object_type,
                "target_name": result.target_object.object_name,
                "candidate_count": result.candidate_count,
                "matched_dependents": result.matched_dependents,
                "truncated": result.truncated,
                "warnings": result.warnings,
                "sap_calls": result.statistics.sap_calls,
                "dependents": dependents,
            }
        except MCPToolError:
            raise
        except Exception as exc:
            raise map_exception(exc, "find_dependents") from exc

    async def _analyze_dependency_impact(
        self,
        object_type: str,
        object_name: str,
        packages: list[str] | None,
        name_patterns: list[str] | None,
        all_custom_objects: bool,
        relationship_types: list[str] | None,
        min_confidence: str | None,
    ) -> dict:
        self._require_dep_service()
        from app.models.phase8 import DependencyImpactRequest, ReverseScopeSpec, RepositoryObjectReference

        try:
            scope = ReverseScopeSpec(
                packages=packages or [],
                name_patterns=name_patterns or [],
                all_custom_objects=all_custom_objects,
                max_results=self._dep_max_candidates,
            )
            req = DependencyImpactRequest(
                object=RepositoryObjectReference(
                    sap_object_type=object_type.strip().upper(),
                    object_name=object_name.strip().upper(),
                ),
                scope=scope,
                relationship_types=relationship_types,
                min_confidence=min_confidence,
                max_candidates=self._dep_max_candidates,
            )
        except Exception as exc:
            raise MCPToolError("INVALID_ARGUMENT", str(exc)) from exc

        try:
            result = await self._dep.get_impact_analysis(req)
            impacted = [
                {
                    "sap_object_type": o.sap_object_type,
                    "object_name": o.object_name,
                    "relationship_types": o.relationship_types,
                    "confidence": o.confidence.value,
                    "dynamic": o.dynamic,
                }
                for o in result.impacted_objects
            ]
            return {
                "success": True, "operation": "analyze_dependency_impact",
                "target_type": result.target_object.sap_object_type,
                "target_name": result.target_object.object_name,
                "direct_dependent_count": result.direct_dependent_count,
                "total_impacted_objects": result.total_impacted_objects,
                "relationship_type_counts": result.relationship_type_counts,
                "confidence_counts": result.confidence_counts,
                "dynamic_reference_count": result.dynamic_reference_count,
                "truncated": result.truncated,
                "warnings": result.warnings,
                "impacted_objects": impacted,
            }
        except MCPToolError:
            raise
        except Exception as exc:
            raise map_exception(exc, "analyze_dependency_impact") from exc

    # ------------------------------------------------------------------
    # Lifecycle helpers
    # ------------------------------------------------------------------

    async def run_stdio(self) -> None:
        """Run the MCP server with STDIO transport (default for local/IDE use)."""
        await self.mcp.run_stdio_async()

    async def run_streamable_http(self) -> None:
        """Run the MCP server with streamable HTTP transport (BTP/network deployments)."""
        await self.mcp.run_streamable_http_async()


# ------------------------------------------------------------------
# Factory and entry point
# ------------------------------------------------------------------

def create_server(settings=None) -> NCoreIQMCPServer:
    """Create a fully-wired NCoreIQMCPServer from application settings."""
    if settings is None:
        from app.core.config import get_settings
        settings = get_settings()

    from app.sap.connection.factory import build_sap_connection_provider
    from app.services.repository import RepositoryService
    from app.services.discovery import RepositoryDiscoveryService
    from app.services.dependency import RepositoryDependencyService
    from app.services.object_service import RepositoryObjectService
    from app.jobs.store import SQLiteJobStore
    from app.jobs.artifacts import LocalArtifactStore
    from app.jobs.service import ExtractionJobService
    import os

    sap_provider = build_sap_connection_provider(settings)
    discovery_svc = RepositoryService(sap_provider)
    discovery_raw = RepositoryDiscoveryService(sap_provider)
    object_svc = RepositoryObjectService(sap_provider)
    dep_svc = RepositoryDependencyService(provider=sap_provider, discovery_service=discovery_raw)

    artifact_dir = settings.ncoreiq_job_artifact_dir
    os.makedirs(artifact_dir, exist_ok=True)
    job_store = SQLiteJobStore(db_path=settings.ncoreiq_job_db_path)
    job_store.initialize()
    artifact_store = LocalArtifactStore(
        artifact_dir=artifact_dir,
        retention_days=settings.ncoreiq_artifact_retention_days,
    )
    job_svc = ExtractionJobService(job_store=job_store, artifact_store=artifact_store)

    return NCoreIQMCPServer(
        sap_provider=sap_provider,
        discovery_svc=discovery_svc,
        object_svc=object_svc,
        job_svc=job_svc,
        settings=settings,
        dep_svc=dep_svc,
    )


if __name__ == "__main__":
    import asyncio
    import logging as _logging
    _logging.basicConfig(level="INFO")
    server = create_server()
    asyncio.run(server.run_stdio())
