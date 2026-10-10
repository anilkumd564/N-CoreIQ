from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class SapODataSettings(BaseModel):
    """OData Gateway connection settings for SAP ECC connectivity.

    Environment variables use double-underscore nesting consistent with
    the existing SAP_LOCAL__ convention:

        SAP_ODATA__BASE_URL=https://host:44300
        SAP_ODATA__SERVICE=ZNCOREIQ_REPO_SRV
        SAP_ODATA__CLIENT=800
        SAP_ODATA__USER=<sap_user>
        SAP_ODATA__PASSWORD=<secret>
        SAP_ODATA__TIMEOUT=30
        SAP_ODATA__CA_BUNDLE=   (optional path to corporate CA bundle)
    """

    base_url: str | None = None
    service: str = "ZNCOREIQ_REPO_SRV"
    client: str = "800"
    user: str | None = None
    password: str | None = None
    timeout: int = 30
    ca_bundle: str | None = None
    skip_hostname_check: bool = False
    skip_tls_verify: bool = False
    """Development-only: disable TLS certificate verification entirely.

    USE ONLY when the SAP Gateway uses a certificate signed by an internal CA
    (e.g. SAP's SDC_CA) that cannot be added to the trust store and the CA
    cert is unavailable (no AIA extension, self-signed chain, etc.).

    This MUST remain False in production / BTP deployments.
    Set via SAP_ODATA__SKIP_TLS_VERIFY=true in .env for local PoC only.
    """

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            "SapODataSettings("
            f"base_url={self.base_url!r}, service={self.service!r}, "
            f"client={self.client!r}, user={self.user!r}, "
            f"password={'***' if self.password else repr(self.password)}, "
            f"timeout={self.timeout!r}, ca_bundle={self.ca_bundle!r}, "
            f"skip_hostname_check={self.skip_hostname_check!r}, "
            f"skip_tls_verify={self.skip_tls_verify!r})"
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
        }


class SapLocalSettings(BaseModel):
    host: str | None = None
    system_number: str | None = None
    client: str | None = None
    user: str | None = None
    password: str | None = None
    language: str = "EN"

    def __repr__(self) -> str:  # pragma: no cover - trivial
        return (
            "SapLocalSettings("
            f"host={self.host!r}, system_number={self.system_number!r}, "
            f"client={self.client!r}, user={self.user!r}, "
            f"password={'***' if self.password else self.password!r}, "
            f"language={self.language!r})"
        )

    __str__ = __repr__

    def safe_dict(self) -> dict[str, str | None]:
        """Return a configuration dict with the password redacted.

        Used anywhere SAP configuration needs to be surfaced (diagnostics,
        logs, API responses) without ever exposing the real credential.
        """
        return {
            "host": self.host,
            "system_number": self.system_number,
            "client": self.client,
            "user": self.user,
            "password": "***" if self.password else self.password,
            "language": self.language,
        }


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "SAP Repository Intelligence Layer"
    app_version: str = "0.8.0"
    app_env: Literal["local", "btp", "test"] = "local"
    log_level: str = "INFO"

    sap_connection_mode: Literal["local", "btp", "odata"] = "local"
    sap_destination_name: str = "ECC_DEV"
    sap_local: SapLocalSettings = Field(default_factory=SapLocalSettings)
    sap_odata: SapODataSettings = Field(default_factory=SapODataSettings)

    export_mode: Literal["local", "zip"] = "zip"
    local_export_root: str = "./exports"

    auth_mode: Literal["disabled", "xsuaa"] = "disabled"

    # ------------------------------------------------------------------
    # Phase 5 — Bulk Extraction settings
    # ------------------------------------------------------------------
    #: Maximum number of objects allowed in a single synchronous bulk extraction.
    #: Override via NCOREIQ_BULK_MAX_OBJECTS env var.
    ncoreiq_bulk_max_objects: int = Field(default=250, ge=1)

    #: Number of concurrent SAP requests during bulk extraction.
    #: Override via NCOREIQ_BULK_CONCURRENCY env var.
    ncoreiq_bulk_concurrency: int = Field(default=3, ge=1, le=5)

    #: Hard ceiling on aggregate source lines across the whole extraction.
    #: Override via NCOREIQ_BULK_MAX_TOTAL_SOURCE_LINES env var.
    ncoreiq_bulk_max_total_source_lines: int = Field(default=250_000, ge=1)

    #: Temporary export directory (used if in-memory streaming is insufficient).
    #: Override via NCOREIQ_BULK_EXPORT_DIR env var.
    ncoreiq_bulk_export_dir: str | None = None

    # ------------------------------------------------------------------
    # Phase 6 — Extraction Job settings
    # ------------------------------------------------------------------
    #: Number of background job worker tasks.
    #: Override via NCOREIQ_JOB_WORKERS env var.
    ncoreiq_job_workers: int = Field(default=2, ge=1, le=10)

    #: Seconds between worker poll cycles when idle.
    #: Override via NCOREIQ_JOB_POLL_INTERVAL_SECONDS env var.
    ncoreiq_job_poll_interval_seconds: float = Field(default=2.0, ge=0.5)

    #: Seconds a worker lease is valid without renewal.
    #: Override via NCOREIQ_JOB_LEASE_SECONDS env var.
    ncoreiq_job_lease_seconds: int = Field(default=120, ge=10)

    #: Seconds between heartbeat renewals while RUNNING.
    #: Override via NCOREIQ_JOB_HEARTBEAT_SECONDS env var.
    ncoreiq_job_heartbeat_seconds: int = Field(default=30, ge=5)

    #: Days to retain job metadata after terminal state.
    #: Override via NCOREIQ_JOB_RETENTION_DAYS env var.
    ncoreiq_job_retention_days: int = Field(default=7, ge=1)

    #: Days to retain job artifacts.
    #: Override via NCOREIQ_ARTIFACT_RETENTION_DAYS env var.
    ncoreiq_artifact_retention_days: int = Field(default=7, ge=1)

    #: Directory where job export artifacts are stored.
    #: Override via NCOREIQ_JOB_ARTIFACT_DIR env var.
    ncoreiq_job_artifact_dir: str = Field(default="./job_artifacts")

    #: SQLite database file path for the job store.
    #: Override via NCOREIQ_JOB_DB_PATH env var.
    ncoreiq_job_db_path: str = Field(default="./ncoreiq_jobs.db")

    #: Maximum number of jobs returned by list endpoint.
    #: Override via NCOREIQ_JOB_MAX_LIST_LIMIT env var.
    ncoreiq_job_max_list_limit: int = Field(default=200, ge=1)

    #: Disable background executor workers (set true during automated tests
    #: to prevent live SAP connection attempts from background workers).
    #: Override via NCOREIQ_DISABLE_EXECUTOR env var.
    ncoreiq_disable_executor: bool = Field(default=False)

    # ------------------------------------------------------------------
    # Phase 7 — MCP Server settings
    # ------------------------------------------------------------------
    #: Enable/disable the MCP server entry point.
    ncoreiq_mcp_enabled: bool = Field(default=True)

    #: MCP server name exposed in the protocol handshake.
    ncoreiq_mcp_server_name: str = Field(default="N-CoreIQ")

    #: MCP server version (shown to clients).
    ncoreiq_mcp_server_version: str = Field(default="0.7.0")

    #: Default transport: stdio | streamable_http
    ncoreiq_mcp_transport: str = Field(default="stdio")

    #: Maximum source lines returned by get_object_source MCP tool.
    ncoreiq_mcp_max_source_lines: int = Field(default=500, ge=1, le=5000)

    #: Maximum search results returned by search_repository MCP tool.
    ncoreiq_mcp_max_search_results: int = Field(default=100, ge=1, le=1000)

    #: Maximum job list results returned by list_extraction_jobs MCP tool.
    ncoreiq_mcp_max_job_list_results: int = Field(default=20, ge=1, le=100)

    #: Tool-level timeout in seconds (advisory — individual tools may enforce shorter).
    ncoreiq_mcp_tool_timeout_seconds: int = Field(default=120, ge=10)

    # ------------------------------------------------------------------
    # Phase 8 — Dependency Intelligence settings
    # ------------------------------------------------------------------
    #: Default max_results for MCP get_dependencies tool.
    ncoreiq_mcp_max_dep_results: int = Field(default=200, ge=1, le=1000)

    #: MCP traversal hard limits (tighter than REST).
    ncoreiq_mcp_dep_max_depth: int = Field(default=3, ge=1, le=3)
    ncoreiq_mcp_dep_max_nodes: int = Field(default=200, ge=1, le=200)
    ncoreiq_mcp_dep_max_edges: int = Field(default=500, ge=1, le=500)

    #: MCP reverse lookup candidate limit.
    ncoreiq_mcp_dep_max_candidates: int = Field(default=50, ge=1, le=50)

    def model_dump(self, *args, **kwargs):  # type: ignore[override]
        """Serialize settings with the SAP passwords always redacted.

        The in-memory credential values are required for live connectivity
        and are never cleared, but any configuration dump (diagnostics,
        logs, API responses) must never expose raw credentials.
        """
        dumped = super().model_dump(*args, **kwargs)
        sap_local = dumped.get("sap_local")
        if isinstance(sap_local, dict) and sap_local.get("password"):
            sap_local["password"] = "***"
        sap_odata = dumped.get("sap_odata")
        if isinstance(sap_odata, dict) and sap_odata.get("password"):
            sap_odata["password"] = "***"
        return dumped


@lru_cache
def get_settings() -> Settings:
    return Settings()
