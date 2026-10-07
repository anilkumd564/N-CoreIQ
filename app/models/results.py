from datetime import UTC, datetime

from pydantic import BaseModel, Field

from app.models.repository import RepositoryObject


class HealthResult(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ConnectionTestResult(BaseModel):
    connected: bool
    mode: str
    system: str | None = None
    message: str


class SearchResult(BaseModel):
    count: int
    objects: list[RepositoryObject]


class ExportResult(BaseModel):
    export_id: str
    object_count: int
    artifact_path: str | None = None
    artifact_name: str | None = None
    errors: list[str] = Field(default_factory=list)
