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
    status: str
    connection_mode: str
    system_id: str | None = None
    client: str | None = None
    sap_release: str | None = None
    timestamp: str | None = None
    ping_method: str | None = None
    message: str

    @property
    def connected(self) -> bool:
        """Backward-compatible convenience flag for callers/tests."""
        return self.status == "connected"


class SearchResult(BaseModel):
    count: int
    objects: list[RepositoryObject]


class ExportResult(BaseModel):
    export_id: str
    object_count: int
    artifact_path: str | None = None
    artifact_name: str | None = None
    errors: list[str] = Field(default_factory=list)
