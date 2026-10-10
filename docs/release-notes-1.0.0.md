# N-CoreIQ — Version 1.0.0 Release Notes

**Release Date:** October 2026  
**Status:** General Availability  
**Predecessor:** 0.9.x (BTP Production Readiness Preview)

---

## Summary

N-CoreIQ Version 1.0.0 is the first General Availability release of the SAP ECC repository intelligence platform. This release consolidates Phases 1 through 10, delivering a production-ready, hardened platform for SAP ABAP repository discovery, source extraction, dependency analysis, and AI-assisted code intelligence via the Model Context Protocol (MCP).

---

## Major Capabilities

### SAP ECC Connectivity (Phase 1–2)
- OData V2 HTTPS connection to SAP ECC via `ZNCOREIQ_REPO_SRV`
- SAP NetWeaver RFC SDK connection (local/PyRFC mode)
- TLS certificate validation with corporate CA bundle support
- Configurable retry with exponential backoff
- Connection pool management
- SAP ECC read-only boundary enforced at all layers

### Repository Discovery (Phase 3)
- Package hierarchy resolution via `Z_MCP_REPO_PACKAGES`
- Custom object discovery via `Z_MCP_REPO_DISCOVER`
- Supported discovery types: PROG, CLAS, INTF, FUGR, TABL, DTEL, DOMA, VIEW, SHLP, ENQU, TRAN, MSAG
- Filter by package, name pattern, and object type
- Safety rules prevent unbounded full-system scans

### Metadata & Source Extraction (Phase 4)
- Object technical metadata via `ObjectMetadataSet`
- ABAP source code and DDIC technical definitions via `SourceLinesSet`
- Supported source types: PROG, CLAS, INTF, FUGR, FUNC, TABL, DTEL, DOMA, VIEW, SHLP, ENQU, TRAN, MSAG
- Source fidelity: line order, indentation, Unicode preserved
- Configurable line limits (1–5000 per object)

### Bulk Extraction & Export (Phase 5)
- Synchronous bulk extraction of up to 250 objects
- JSON and ZIP export formats
- Concurrent SAP retrieval with configurable parallelism
- Per-object error handling with `continue_on_error` support

### Async Extraction Jobs (Phase 6)
- Fully asynchronous background extraction jobs
- Job state machine: QUEUED → RUNNING → SUCCEEDED / FAILED / CANCELLED
- SQLite job persistence with WAL mode
- Worker lease mechanism prevents duplicate execution
- Automatic stale job recovery on restart
- Idempotency key support
- Job retry and cancellation
- Artifact storage with configurable retention

### MCP Server (Phase 7)
- 15 Model Context Protocol tools for AI assistant integration
- Compatible with Claude, Cursor, VS Code Copilot, and other MCP clients
- STDIO transport (default) and streamable HTTP transport
- Read-only annotations on all data-retrieval tools
- Conservative context limits (500 source lines, 100 search results by default)
- MCP tool catalog:
  - `sap_ping`, `list_packages`, `search_repository`
  - `get_object_metadata`, `get_object_source`
  - `start_extraction_job`, `get_extraction_job`, `list_extraction_jobs`
  - `cancel_extraction_job`, `retry_extraction_job`, `get_extraction_result`
  - `get_dependencies`, `traverse_dependencies`, `find_dependents`, `analyze_dependency_impact`

### Dependency & Relationship Intelligence (Phase 8)
- Static dependency extraction via `DependencyRelationsSet`
- 11 Version 1.0 relationship types: INCLUDES, CALLS_PROGRAM, CALLS_FUNCTION, USES_CLASS, IMPLEMENTS_INTERFACE, INHERITS_CLASS, READS_TABLE, WRITES_TABLE, USES_DATA_ELEMENT, USES_DOMAIN, TRANSACTION_TARGET
- Confidence levels: EXACT, HIGH, MEDIUM, LOW
- Evidence sources: DDIC, TSTC, SOURCE_STATIC, SOURCE_HEURISTIC, SOURCE_DYNAMIC
- Forward BFS traversal with cycle detection (depth 1–3)
- Reverse dependency lookup (bounded scope)
- Deterministic impact analysis

### Repository Explorer UI (Phase 8B)
- React-based web UI served at `/ui`
- Repository search and package browsing
- Object metadata and source viewer
- Dependency visualization
- Extraction job management

### Selective Source Download (Phase 8C)
- UI-driven source export to local folder (File System Access API)
- ZIP fallback for unsupported browsers
- Flat source ZIP format with manifest
- Path traversal protection and filename sanitization

### SAP BTP Production Readiness (Phase 9)
- XSUAA JWT authentication
- BTP Destination Service integration
- SAP Cloud Connector support
- Structured JSON logging
- Correlation ID propagation
- Security response headers
- CORS configuration
- Liveness and readiness health probes
- Fail-fast BTP startup validation
- Graceful shutdown

### Version 1.0 Hardening (Phase 10)
- Canonical error model with stable error codes
- Comprehensive exception-to-error-code mapping
- Complete SAP allow-list documentation and enforcement
- Dependency and confidence taxonomy frozen
- Job state machine certified
- Secret redaction verified
- Full Phase 1–9 regression: 942 tests passing
- Configuration reference documentation
- Installation, operations, and troubleshooting guides
- Compatibility matrix

---

## Architecture

```
Frontend (React)  /  MCP Server (FastMCP)  /  REST API (FastAPI)
                           ↓
              Application Services Layer
              (Discovery, Object, Bulk, Dependency, Job)
                           ↓
              Provider Abstractions (SapConnectionProvider)
                           ↓
              Infrastructure Implementations
              (OData HTTP / Local RFC / BTP Destination)
                           ↓
              SAP ECC (OData V2 Gateway) — READ ONLY
```

---

## Security Model

- **Authentication**: disabled (local) or XSUAA JWT (BTP production)
- **Authorization**: scope-based via XSUAA (`ncoreiq.read`, `ncoreiq.operate`, `ncoreiq.admin`)
- **SAP read-only boundary**: enforced at allow-list level — no RFC or OData write operation exists
- **Secret handling**: passwords and keys redacted from all logs, diagnostics, and API responses
- **Correlation IDs**: propagated through REST, MCP, SAP calls, jobs, and error responses
- **Security headers**: `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`

---

## Supported Object Types (Version 1.0)

| Type | Discovery | Metadata | Source | Dependencies |
|------|-----------|----------|--------|--------------|
| PROG | ✓ | ✓ | ✓ | ✓ |
| CLAS | ✓ | ✓ | ✓ | ✓ |
| INTF | ✓ | ✓ | ✓ | ✓ |
| FUGR | ✓ | ✓ | ✓ | ✓ |
| FUNC | — | ✓ | ✓ | ✓ |
| TABL | ✓ | ✓ | ✓ (DDIC def) | ✓ |
| DTEL | ✓ | ✓ | ✓ (DDIC def) | ✓ |
| DOMA | ✓ | ✓ | ✓ (DDIC def) | — |
| VIEW | ✓ | ✓ | ✓ (DDIC def) | ✓ |
| SHLP | ✓ | ✓ | ✓ (DDIC def) | — |
| ENQU | ✓ | ✓ | ✓ (DDIC def) | — |
| TRAN | ✓ | ✓ | ✓ (def) | ✓ |
| MSAG | ✓ | ✓ | ✓ (def) | — |

---

## Deployment Options

| Option | Status |
|--------|--------|
| Local (direct HTTPS) | ✓ Supported |
| BTP Cloud Foundry | ✓ Supported |
| Docker | ✓ Supported |
| Kubernetes | Not tested in 1.0 |
| On-premise server | ✓ Supported |

---

## Known Limitations

### Dependency Analysis
- Static analysis only — dynamic `CALL FUNCTION ... variable` not resolved
- Dynamic `SUBMIT` statements not resolved
- Dynamic SQL not resolved
- Multi-line macro calls may not be detected
- Generated source (GENERATE SUBROUTINE POOL) not analyzed
- BAdI/Enhancement framework dependencies not detected
- Workflow runtime dependencies not detected
- No full runtime call-graph accuracy claim

### Browser Compatibility
- Direct folder export (File System Access API) requires Chrome 86+ or Edge 86+
- Firefox and Safari use ZIP fallback automatically
- ZIP fallback is fully functional on all modern browsers

### Scale
- SQLite job store: suitable for single-instance / low concurrency
- Multi-instance BTP requires shared PostgreSQL (`NCOREIQ_DB_URL`)
- Reverse dependency search bounded by `max_candidates` (default 50)
- Dependency traversal bounded at depth 3, 200 nodes, 500 edges

### Features Not in Version 1.0
- Repository quality scoring
- SAP Clean Core recommendations
- S/4HANA migration intelligence
- Persistent knowledge graph
- LLM-based repository reasoning
- Advanced AI analysis features

---

## Upgrade Notes

### Upgrading from 0.9.x

1. **Database**: No migration required for SQLite. Schema is backward-compatible.
2. **Configuration**: `APP_VERSION` updated to `1.0.0` automatically.
3. **API**: REST API contracts are unchanged. All `/api/v1/` endpoints remain stable.
4. **MCP**: MCP tool names unchanged. Tool count remains 15.
5. **Frontend**: Rebuild frontend: `cd frontend && npm install && npm run build`
6. **SAP Transport**: No new transport required for 1.0.0 if 0.9.x transport is installed.

See [Upgrade Guide](upgrade-guide.md) for detailed steps.

---

## Compatibility

| Component | Tested Version |
|-----------|---------------|
| SAP ECC | 6.0 EHP7, EHP8 |
| SAP S/4HANA | 2020, 2021, 2022 |
| SAP NetWeaver Gateway | 7.50+ |
| Python | 3.11, 3.12, 3.14 |
| Node.js | 18, 20 |
| Chrome | 110+ |
| Firefox | 115+ (ZIP fallback) |
| Edge | 110+ |
| BTP CF Runtime | 2024+ |
| SQLite | 3.39+ |
