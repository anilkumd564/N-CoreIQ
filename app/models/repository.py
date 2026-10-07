from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field


class RepositoryCategory(StrEnum):
    ABAP_CODE = "ABAP_CODE"
    DATA_DICTIONARY = "DATA_DICTIONARY"
    ENHANCEMENT = "ENHANCEMENT"
    UI = "UI"
    FORM = "FORM"
    INTEGRATION = "INTEGRATION"
    OTHER = "OTHER"


class RepositoryMetadata(BaseModel):
    description: str | None = None
    author: str | None = None
    created_date: date | None = None
    changed_by: str | None = None
    changed_date: date | None = None
    active: bool | None = None
    original_system: str | None = None
    transport_layer: str | None = None


class RepositorySource(BaseModel):
    language: str | None = None
    content: str | None = None
    encoding: str = "utf-8"


class RepositoryRelationships(BaseModel):
    includes: list[str] = Field(default_factory=list)
    calls: list[str] = Field(default_factory=list)
    tables: list[str] = Field(default_factory=list)
    classes: list[str] = Field(default_factory=list)
    function_modules: list[str] = Field(default_factory=list)


class RepositoryObject(BaseModel):
    system: str
    object_type: str
    object_name: str
    package: str | None = None
    category: RepositoryCategory = RepositoryCategory.OTHER
    metadata: RepositoryMetadata = Field(default_factory=RepositoryMetadata)
    source: RepositorySource | None = None
    relationships: RepositoryRelationships = Field(default_factory=RepositoryRelationships)
    raw: dict[str, Any] = Field(default_factory=dict)
    extracted_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
