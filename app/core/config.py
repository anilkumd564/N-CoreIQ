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


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )

    app_name: str = "SAP Repository Intelligence Layer"
    app_version: str = "0.1.0"
    app_env: Literal["local", "btp", "test"] = "local"
    log_level: str = "INFO"

    sap_connection_mode: Literal["local", "btp"] = "local"
    sap_destination_name: str = "ECC_DEV"
    sap_local: SapLocalSettings = Field(default_factory=SapLocalSettings)

    export_mode: Literal["local", "zip"] = "zip"
    local_export_root: str = "./exports"

    auth_mode: Literal["disabled", "xsuaa"] = "disabled"


@lru_cache
def get_settings() -> Settings:
    return Settings()
