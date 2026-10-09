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
    app_version: str = "0.4.0"
    app_env: Literal["local", "btp", "test"] = "local"
    log_level: str = "INFO"

    sap_connection_mode: Literal["local", "btp", "odata"] = "local"
    sap_destination_name: str = "ECC_DEV"
    sap_local: SapLocalSettings = Field(default_factory=SapLocalSettings)
    sap_odata: SapODataSettings = Field(default_factory=SapODataSettings)

    export_mode: Literal["local", "zip"] = "zip"
    local_export_root: str = "./exports"

    auth_mode: Literal["disabled", "xsuaa"] = "disabled"

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
