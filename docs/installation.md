# N-CoreIQ — Installation Guide (Version 1.0.0)

## Overview

N-CoreIQ provides two deployment modes:

| Mode | Description |
|------|-------------|
| **Local** | Direct HTTPS to SAP Gateway, SQLite job store, local artifact storage |
| **BTP** | SAP BTP Cloud Foundry, Destination/Connectivity/Cloud Connector, managed DB |

---

## 1. Prerequisites

### System Requirements

| Component | Minimum | Recommended |
|-----------|---------|-------------|
| Python | 3.11 | 3.12 |
| Node.js | 18 | 20 LTS |
| OS | Windows 10 / Linux / macOS | Linux (production) |
| RAM | 512 MB | 1 GB |
| Disk | 500 MB | 2 GB |

### SAP Prerequisites

- SAP ECC 6.0 EHP7+ (or S/4HANA)
- SAP Gateway (ICM) enabled with HTTPS
- SAP NetWeaver Gateway OData V2 service active
- ABAP development key or transport system access
- Technical user with `Z_NCOREIQ_ROLE` authorization

---

## 2. SAP Installation

### 2.1 Transport Import

Import the N-CoreIQ transport into your SAP system. The transport contains:

- **Function Group**: `Z_MCP_REPO` with function modules:
  - `Z_MCP_REPO_PING` — connectivity test
  - `Z_MCP_REPO_PACKAGES` — package hierarchy resolution
  - `Z_MCP_REPO_DISCOVER` — repository object discovery
  - `Z_MCP_REPO_GET_METADATA` — object metadata retrieval
  - `Z_MCP_REPO_GET_SOURCE` — object source retrieval
  - `Z_MCP_REPO_DEPENDENCIES` — dependency extraction

- **Authorization Role**: `Z_NCOREIQ_ROLE` — grants read-only access to:
  - `S_RFC` for `Z_MCP_REPO_*` and `RFC_SYSTEM_INFO`
  - `S_TABU_DIS` for TADIR, repository tables (display only)

- **DDIC Structures** (for dependency intelligence):
  - `ZST_NCOREIQ_DEP_REL` — dependency relation structure
  - `ZST_NCOREIQ_DEP_HDR` — dependency header structure

### 2.2 OData Service Activation

```
Transaction: /n/IWFND/MAINT_SERVICE
Service: ZNCOREIQ_REPO_SRV
Version: 1
System Alias: LOCAL (or appropriate gateway alias)
```

1. Navigate to `/n/IWFND/MAINT_SERVICE`
2. Click **Add Service**
3. Enter Service ID: `ZNCOREIQ_REPO_SRV`
4. Select System Alias
5. Click **Add Selected Services**
6. Verify the service appears with status **Active**

### 2.3 SICF Activation

```
Transaction: SICF
Path: /sap/opu/odata/sap/ZNCOREIQ_REPO_SRV/
```

Activate the SICF node if not automatically activated.

### 2.4 Gateway Test

Test the service in browser or Postman:
```
GET https://<sap-host>:<port>/sap/opu/odata/sap/ZNCOREIQ_REPO_SRV/SystemInfoSet
Authorization: Basic <base64(user:password)>
```

Expected response: JSON with `SapRelease`, `SystemId`, `Client`.

### 2.5 Technical User

Create a dedicated SAP technical user (type `Service`):

```
Transaction: SU01
User Type: Service
Authorization Profile: Z_NCOREIQ_ROLE
```

**Never use a dialog user or an admin account.**

---

## 3. Local Developer Setup

### 3.1 Clone and Install

```bash
git clone https://github.com/anilkumd564/N-CoreIQ.git
cd N-CoreIQ
pip install -r requirements.txt
```

### 3.2 Configure Environment

```bash
cp .env.example .env
```

Edit `.env`:

```env
# Application
APP_ENV=local
APP_VERSION=1.0.0
LOG_LEVEL=INFO

# SAP Connection (OData mode — recommended for local dev)
SAP_CONNECTION_MODE=odata
SAP_ODATA__BASE_URL=https://your-sap-host:44300
SAP_ODATA__USER=NCOREIQ_SVC
SAP_ODATA__PASSWORD=your_password
SAP_ODATA__CLIENT=100
SAP_ODATA__CA_BUNDLE=/path/to/ca-bundle.pem

# Jobs
NCOREIQ_JOB_DB_PATH=./ncoreiq_jobs.db
NCOREIQ_JOB_ARTIFACT_DIR=./job_artifacts
NCOREIQ_DISABLE_EXECUTOR=false
```

### 3.3 Start Backend

```bash
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

### 3.4 Build and Start Frontend

```bash
cd frontend
npm install
npm run dev
```

Frontend accessible at: `http://localhost:5173`

### 3.5 Start MCP Server

```bash
python -m app.mcp.server
```

---

## 4. Frontend Build (Production)

```bash
cd frontend
npm install
npm run build
```

The built assets are placed in `frontend/dist/`. When present, the backend serves the UI at `/ui`.

---

## 5. BTP Deployment

### 5.1 Prerequisites

- SAP BTP subaccount with Cloud Foundry environment
- BTP Connectivity service instance (for Cloud Connector)
- BTP Destination service instance
- BTP XSUAA service instance
- Cloud Connector configured with Virtual Host for SAP ECC

### 5.2 Service Instances

```bash
# Create XSUAA instance
cf create-service xsuaa application ncoreiq-xsuaa -c deployment/btp/xs-security.json

# Create Connectivity instance
cf create-service connectivity lite ncoreiq-connectivity

# Create Destination instance
cf create-service destination lite ncoreiq-destination
```

### 5.3 Configure Destination

In BTP Destination service, create a destination named `NCOREIQ_ECC`:

```
Name: NCOREIQ_ECC
Type: HTTP
URL: https://virtual-host:virtual-port
ProxyType: OnPremise
Authentication: BasicAuthentication
User: NCOREIQ_SVC
Password: <password>
```

### 5.4 Deploy

```bash
cf push -f deployment/btp/manifest.yml
```

Or using MTA:

```bash
mbt build
cf deploy mta_archives/ncoreiq_1.0.0.mtar
```

### 5.5 Configure Environment Variables

```bash
cf set-env ncoreiq APP_ENV=btp
cf set-env ncoreiq SAP_CONNECTION_MODE=btp
cf set-env ncoreiq BTP__XSUAA__URL=<xsuaa-url>
cf set-env ncoreiq AUTH_MODE=xsuaa
cf restage ncoreiq
```

---

## 6. Health Check

After deployment, verify:

```
GET /health          → {"status": "ok", "version": "1.0.0", ...}
GET /health/live     → {"status": "alive"}
GET /health/ready    → {"status": "ready"}
GET /api/v1/sap/ping → {"status": "connected", "system_id": "...", ...}
```

---

## 7. Verify Installation

```bash
# Test SAP connectivity
curl -s http://localhost:8000/api/v1/sap/ping | python -m json.tool

# Test package discovery
curl -s -X GET "http://localhost:8000/api/v1/repository/packages/ZNCOREIQ" | python -m json.tool
