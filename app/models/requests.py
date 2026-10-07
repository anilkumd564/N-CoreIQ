from datetime import date

from pydantic import BaseModel, Field, field_validator


class RepositorySearchRequest(BaseModel):
    packages: list[str] = Field(default_factory=list)
    include_subpackages: bool = True
    name_patterns: list[str] = Field(default_factory=lambda: ["Z*", "Y*"])
    object_types: list[str] = Field(default_factory=list)
    changed_after: date | None = None
    changed_before: date | None = None
    changed_by: str | None = None
    max_results: int = Field(default=5000, ge=1, le=50000)

    @field_validator("packages", "name_patterns", "object_types")
    @classmethod
    def normalize_values(cls, values: list[str]) -> list[str]:
        return [value.strip().upper() for value in values if value and value.strip()]


class ObjectKey(BaseModel):
    object_type: str
    object_name: str


class ExportRequest(RepositorySearchRequest):
    export_name: str = "ecc_custom_repository"
    include_source: bool = True
    include_metadata: bool = True
