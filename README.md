# SAP Repository Intelligence Layer

Version 0.1 foundation for a BTP-ready, read-only SAP ECC custom repository discovery and extraction platform.

## Current capabilities

- FastAPI service with OpenAPI documentation
- BTP/local configuration abstraction
- SAP connection provider abstraction
- Canonical repository object model
- Package/name/object-type search contract
- Repository service boundary for future `Z_MCP_REPO_*` RFC APIs
- ZIP export service with manifest support
- MCP tool contract boundary
- Docker and SAP BTP Cloud Foundry scaffolding
- Initial XSUAA role model
- Unit tests for health/config request behavior

## Architecture principles

1. MCP and REST never contain SAP extraction logic.
2. SAP connectivity is injected through a provider interface.
3. Local and BTP runtime modes use the same repository services.
4. No SAP credentials are committed to source control.
5. Production on-premise access is designed for SAP BTP Destination + Connectivity Service + Cloud Connector.
6. The initial SAP integration is read-only.
7. Export storage is portable; a BTP-hosted instance must not depend on a Windows Desktop path.

## Run locally

```bash
python -m venv .venv
# Windows
.venv\\Scripts\\activate
# macOS/Linux
# source .venv/bin/activate

pip install -r requirements.txt
copy .env.example .env   # Windows cmd
# cp .env.example .env   # macOS/Linux

uvicorn app.main:app --reload --port 8080
```

Open:

- `http://localhost:8080/docs`
- `http://localhost:8080/api/v1/health`
- `http://localhost:8080/api/v1/sap/ping`

## Version 0.1 API contracts

### Search repository

`POST /api/v1/repository/search`

```json
{
  "packages": ["ZFINANCE"],
  "include_subpackages": true,
  "name_patterns": ["Z*", "Y*"],
  "object_types": ["PROG", "CLAS", "FUGR", "TABL"],
  "max_results": 5000
}
```

The endpoint currently returns HTTP 501 until the RFC adapter is activated.

## Planned ECC RFC contract

Phase 2 will establish:

- `Z_MCP_REPO_PING`
- `Z_MCP_REPO_PACKAGES`
- `Z_MCP_REPO_DISCOVER`
- `Z_MCP_REPO_GET_OBJECT`
- `Z_MCP_REPO_GET_SOURCE`
- `Z_MCP_REPO_GET_METADATA`

## Next implementation milestone

Implement `Z_MCP_REPO_PING` on ECC and activate the local RFC adapter. Once connectivity is proven, implement recursive package discovery and TADIR-based object discovery.
