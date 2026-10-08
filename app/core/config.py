from functools import lru_cache
from typing import Literal

from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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
    app_version: str = "0.2.0"
    app_env: Literal["local", "btp", "test"] = "local"
    log_level: str = "INFO"

    sap_connection_mode: Literal["local", "btp"] = "local"
    sap_destination_name: str = "ECC_DEV"
    sap_local: SapLocalSettings = Field(default_factory=SapLocalSettings)

    export_mode: Literal["local", "zip"] = "zip"
    local_export_root: str = "./exports"

    auth_mode: Literal["disabled", "xsuaa"] = "disabled"

    def model_dump(self, *args, **kwargs):  # type: ignore[override]
        """Serialize settings with the SAP password always redacted.

        The in-memory `sap_local.password` value is required for live RFC
        calls and is never cleared, but any configuration dump (diagnostics,
        logs, API responses) must never expose the raw credential.
        """
        dumped = super().model_dump(*args, **kwargs)
        sap_local = dumped.get("sap_local")
        if isinstance(sap_local, dict) and sap_local.get("password"):
            sap_local["password"] = "***"
        return dumped


@lru_cache
def get_settings() -> Settings:
    return Settings()
