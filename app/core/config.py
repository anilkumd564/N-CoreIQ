"""N-CoreIQ — Centralized application configuration (Phase 9).

All configuration flows through this module.  Direct os.getenv() calls are
forbidden in business/service modules.

Runtime modes:
    local  — direct SAP HTTPS endpoint, SQLite job store, local artifact store
    btp    — BTP Destination/Connectivity, production DB, object-store artifacts
    test   — same as local; executor disabled; test isolation helpers active
"""
from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


# ---------------------------------------------------------------------------
# SAP sub-settings
# ---------------------------------------------------------------------------


class SapODataSettings(BaseModel):
    """OData Gateway connection settings for SAP ECC connectivity."""

    base_url: str | None = None
    service: str = "ZNCOREIQ_REPO_SRV"
    client: str = "800"
    user: str | None = None
    password: str | None = None
    timeout: int = 30
    ca_bundle: str | None = None
    skip_hostname_check: bool = False
    skip_tls_verify: bool = False

    # Phase 9 — retry configuration for transient SAP failures
    retry_attempts: int = Field(default=3, ge=0, le=5)
    retry_backoff_base: float = Field(default=1.0, ge=0.1, le=10.0)
    retry_backoff_max: float = Field(default=10.0, ge=1.0, le=60.0)

    # Phase 9 — connection pool / timeout settings
    http_connect_timeout: float = Field(default=10.0, ge=1.0)
    http_read_timeout: float = Field(default=60.0, ge=5.0)
    http_pool_connections: int = Field(default=10, ge=1, le=50)
    http_keepalive_expiry: float = Field(default=30.0, ge=5.0)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            "SapODataSettings("
            f"base_url={self.base_url!r}, service={self.service!r}, "
            f"client={self.client!r}, user={self.user!r}, "
            f"password={'***' if self.password else repr(self.password)}, "
            f"timeout={self.timeout!r}, skip_tls_verify={self.skip_tls_verify!r})"
        )

    __str__ = __repr__

    def safe_dict(self) -> dict[str, str | int | bool | None]:
        """Return a configuration dict with the password always redacted."""
        return {
            "base_url": self.base_url,
            "service": self.service,
            "client": self.client,
            "user": self.user,
            "password": "***" if self.password else self.password,
            "timeout": self.timeout,
            "ca_bundle": self.ca_bundle,
            "skip_hostname_check": self.skip_hostname_check,
            "skip_tls_verify": self.skip_tls_verify,
            "retry_attempts": self.retry_attempts,
        }


class SapLocalSettings(BaseModel):
    host: str | None = None
    system_number: str | None = None
    client: str | None = None
    user: str | None = None
    password: str | None = None
    language: str = "EN"

    def __repr__(self) -> str:  # pragma: no cover
        return (
            "SapLocalSettings("
            f"host={self.host!r}, system_number={self.system_number!r}, "
            f"client={self.client!r}, user={self.user!r}, "
            f"password={'***' if self.password else self.password!r}, "
            f"language={self.language!r})"
        )

    __str__ = __repr__

    def safe_dict(self) -> dict[str, str | None]:
        """Return a configuration dict with the password redacted."""
        return {
            "host": self.host,
            "system_number": self.system_number,
            "client": self.client,
            "user": self.user,
            "password": "***" if self.password else self.password,
            "language": self.language,
        }


# ---------------------------------------------------------------------------
# Phase 9 — BTP service binding settings
# ---------------------------------------------------------------------------


class BtpXsuaaSettings(BaseModel):
    """XSUAA service binding credentials. Never log or expose."""
    url: str | None = None
    clientid: str | None = None
    clientsecret: str | None = None
    xsappname: str | None = None
    uaadomain: str | None = None
    verificationkey: str | None = None

    def safe_dict(self) -> dict[str, str | None]:
        return {
            "url": self.url,
            "clientid": self.clientid,
            "clientsecret": "***" if self.clientsecret else None,
            "xsappname": self.xsappname,
            "uaadomain": self.uaadomain,
            "verificationkey": "***" if self.verificationkey else None,
        }


class BtpDestinationSettings(BaseModel):
    """Destination Service binding credentials."""
    uri: str | None = None
    clientid: str | None = None
    clientsecret: str | None = None
    url: str | None = None
    tokenserviceurl: str | None = None

    def safe_dict(self) -> dict[str, str | None]:
        return {
            "uri": self.uri,
            "clientid": self.clientid,
            "clientsecret": "***" if self.clientsecret else None,
            "url": self.url,
            "tokenserviceurl": self.tokenserviceurl,
        }


class BtpConnectivitySettings(BaseModel):
    """Connectivity Service binding credentials."""
    onpremise_proxy_host: str | None = None
    onpremise_proxy_port: int | None = None
    clientid: str | None = None
    clientsecret: str | None = None
    url: str | None = None
    tokenserviceurl: str | None = None

    def safe_dict(self) -> dict[str, str | int | None]:
        return {
            "onpremise_proxy_host": self.onpremise_proxy_host,
            "onpremise_proxy_port": self.onpremise_proxy_port,
            "clientid": self.clientid,
            "clientsecret": "***" if self.clientsecret else None,
            "url": self.url,
            "tokenserviceurl": self.tokenserviceurl,
        }


class BtpSettings(BaseModel):
    """Aggregated BTP service settings.

    In BTP Cloud Foundry populated from VCAP_SERVICES via BTPServiceBindingResolver.
    For local BTP simulation set via env vars with double-underscore nesting:
        BTP__XSUAA__URL=...
        BTP__DESTINATION__URI=...
    """
    xsuaa: BtpXsuaaSettings = Field(default_factory=BtpXsuaaSettings)
    destination: BtpDestinationSettings = Field(default_factory=BtpDestinationSettings)
    connectivity: BtpConnectivitySettings = Field(default_factory=BtpConnectivitySettings)
    sap_destination_name: str = "NCOREIQ_ECC"
    cloud_connector_location_id: str | None = None

    def safe_dict(self) -> dict:
        return {
            "xsuaa": self.xsuaa.safe_dict(),
            "destination": self.destination.safe_dict(),
            "connectivity": self.connectivity.safe_dict(),
            "sap_destination_name": self.sap_destination_name,
            "cloud_connector_location_id": self.cloud_connector_location_id,
        }


# ---------------------------------------------------------------------------
# Phase 9 — Authentication settings
# ---------------------------------------------------------------------------


class AuthSettings(BaseModel):
    """Authentication configuration.

    mode:
        disabled  — no authentication required (local/dev only)
        xsuaa     — XSUAA JWT validation (BTP production)
    """
    mode: Literal["disabled", "xsuaa"] = "disabled"
    jwks_uri: str | None = None
    audience: str | None = None
    issuer: str | None = None
    jwks_cache_ttl_seconds: int = Field(default=300, ge=60)
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    def safe_dict(self) -> dict:
        return {
            "mode": self.mode,
            "jwks_uri": self.jwks_uri,
            "audience": self.audience,
            "issuer": self.issuer,
            "cors_origins": self.cors_origins,
        }


# ---------------------------------------------------------------------------
# Phase 9 — Logging settings
# ---------------------------------------------------------------------------


class LoggingSettings(BaseModel):
    """Structured logging configuration."""
    level: str = "INFO"
    format: Literal["text", "json"] = "text"
    include_request_id: bool = True
    include_correlation_id: bool = True
    instance_id: str | None = None


# ---------------------------------------------------------------------------
# Main settings class
# ---------------------------------------------------------------------------


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    # ------------------------------------------------------------------
    # Application identity
    # ------------------------------------------------------------------
    app_name: str = "N-CoreIQ"
    app_version: str = "1.0.0"
    app_env: Literal["local", "btp", "test"] = "local"
    log_level: str = "INFO"

    # ------------------------------------------------------------------
    # SAP Connection
    # ------------------------------------------------------------------
    sap_connection_mode: Literal["local", "btp", "odata"] = "local"
    sap_destination_name: str = "ECC_DEV"
    sap_local: SapLocalSettings = Field(default_factory=SapLocalSettings)
    sap_odata: SapODataSettings = Field(default_factory=SapODataSettings)

    # ------------------------------------------------------------------
    # Phase 9 — BTP / Auth / Logging sub-settings
    # ------------------------------------------------------------------
    btp: BtpSettings = Field(default_factory=BtpSettings)
    auth: AuthSettings = Field(default_factory=AuthSettings)
    logging: LoggingSettings = Field(default_factory=LoggingSettings)

    # Legacy flat fields kept for backward-compat with existing .env files
    auth_mode: Literal["disabled", "xsuaa"] = "disabled"

    # ------------------------------------------------------------------
    # Export
    # ------------------------------------------------------------------
    export_mode: Literal["local", "zip"] = "zip"
    local_export_root: str = "./exports"

    # ------------------------------------------------------------------
    # Phase 9 — CORS
    # ------------------------------------------------------------------
    cors_allowed_origins: list[str] = Field(
        default_factory=lambda: ["http://localhost:5173", "http://localhost:3000"]
    )
    cors_allow_credentials: bool = True

    # ------------------------------------------------------------------
    # Phase 5 — Bulk Extraction settings
    # ------------------------------------------------------------------
    ncoreiq_bulk_max_objects: int = Field(default=250, ge=1)
    ncoreiq_bulk_concurrency: int = Field(default=3, ge=1, le=5)
    ncoreiq_bulk_max_total_source_lines: int = Field(default=250_000, ge=1)
    ncoreiq_bulk_export_dir: str | None = None

    # ------------------------------------------------------------------
    # Phase 6 — Extraction Job settings
    # ------------------------------------------------------------------
    ncoreiq_job_workers: int = Field(default=2, ge=1, le=10)
    ncoreiq_job_poll_interval_seconds: float = Field(default=2.0, ge=0.5)
    ncoreiq_job_lease_seconds: int = Field(default=120, ge=10)
    ncoreiq_job_heartbeat_seconds: int = Field(default=30, ge=5)
    ncoreiq_job_retention_days: int = Field(default=7, ge=1)
    ncoreiq_artifact_retention_days: int = Field(default=7, ge=1)
    ncoreiq_job_artifact_dir: str = Field(default="./job_artifacts")
    ncoreiq_job_db_path: str = Field(default="./ncoreiq_jobs.db")
    ncoreiq_job_max_list_limit: int = Field(default=200, ge=1)
    ncoreiq_disable_executor: bool = Field(default=False)

    # Phase 9 — concurrency limits
    ncoreiq_max_running_jobs_per_instance: int = Field(default=2, ge=1, le=10)
    ncoreiq_max_sap_concurrency_per_instance: int = Field(default=5, ge=1, le=20)

    # Phase 9 — Production database URL (empty = use SQLite)
    ncoreiq_db_url: str | None = None

    # ------------------------------------------------------------------
    # Phase 7 — MCP Server settings
    # ------------------------------------------------------------------
    ncoreiq_mcp_enabled: bool = Field(default=True)
    ncoreiq_mcp_server_name: str = Field(default="N-CoreIQ")
    ncoreiq_mcp_server_version: str = Field(default="1.0.0")
    ncoreiq_mcp_transport: str = Field(default="stdio")
    ncoreiq_mcp_max_source_lines: int = Field(default=500, ge=1, le=5000)
    ncoreiq_mcp_max_search_results: int = Field(default=100, ge=1, le=1000)
    ncoreiq_mcp_max_job_list_results: int = Field(default=20, ge=1, le=100)
    ncoreiq_mcp_tool_timeout_seconds: int = Field(default=120, ge=10)

    # ------------------------------------------------------------------
    # Phase 8 — Dependency Intelligence settings
    # ------------------------------------------------------------------
    ncoreiq_mcp_max_dep_results: int = Field(default=200, ge=1, le=1000)
    ncoreiq_mcp_dep_max_depth: int = Field(default=3, ge=1, le=3)
    ncoreiq_mcp_dep_max_nodes: int = Field(default=200, ge=1, le=200)
    ncoreiq_mcp_dep_max_edges: int = Field(default=500, ge=1, le=500)
    ncoreiq_mcp_dep_max_candidates: int = Field(default=50, ge=1, le=50)

    def model_dump(self, *args, **kwargs):  # type: ignore[override]
        """Serialize settings with SAP passwords and BTP secrets always redacted."""
        dumped = super().model_dump(*args, **kwargs)
        # Redact SAP local password
        sap_local = dumped.get("sap_local")
        if isinstance(sap_local, dict) and sap_local.get("password"):
            sap_local["password"] = "***"
        # Redact SAP OData password
        sap_odata = dumped.get("sap_odata")
        if isinstance(sap_odata, dict) and sap_odata.get("password"):
            sap_odata["password"] = "***"
        # Redact BTP secrets
        btp = dumped.get("btp", {})
        if isinstance(btp, dict):
            for svc in ("xsuaa", "destination", "connectivity"):
                svc_dict = btp.get(svc, {})
                if isinstance(svc_dict, dict):
                    for secret_key in ("clientsecret", "verificationkey", "password"):
                        if svc_dict.get(secret_key):
                            svc_dict[secret_key] = "***"
        return dumped


@lru_cache
def get_settings() -> Settings:
    return Settings()
