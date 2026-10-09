"""Phase 6 — ArtifactStore: abstraction for job export artifact storage.

Security notes:
- Internal filesystem paths are NEVER exposed to callers.
- Artifact IDs are validated to prevent path traversal.
- Artifacts are finalised via atomic rename (tmp → final).
- SHA-256 is computed and stored with each artifact.

Known limitation:
    LocalArtifactStore uses the local filesystem and is suitable for
    single-instance development deployments only.  Replace with an
    object-store implementation (e.g. BTP Document Management) for
    production multi-instance deployments.
"""

from __future__ import annotations

import hashlib
import io
import logging
import os
import re
import shutil
import tempfile
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import IO, Iterator

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# Only allow safe artifact IDs: UUID-like alphanum + hyphens/underscores
_SAFE_ARTIFACT_ID = re.compile(r"^[a-zA-Z0-9_\-]{1,128}$")


def _validate_artifact_id(artifact_id: str) -> None:
    """Raise ValueError if artifact_id is not safe for use as a filename."""
    if not _SAFE_ARTIFACT_ID.match(artifact_id):
        raise ValueError(
            f"Artifact ID '{artifact_id}' contains unsafe characters. "
            "Only alphanumeric, hyphen, and underscore are allowed."
        )


class ArtifactMetadata(BaseModel):
    """Metadata returned by ArtifactStore.metadata().

    The internal filesystem path is intentionally NEVER included.
    """
    artifact_id: str
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    created_at: datetime
    job_id: str | None = None
    expires_at: datetime | None = None


class ArtifactStore(ABC):
    """Abstract artifact persistence interface.

    Implementations must be interchangeable without changes to JobService
    or ExtractionJobExecutor.
    """

    @abstractmethod
    def save(
        self,
        artifact_id: str,
        data: bytes,
        filename: str,
        content_type: str,
        job_id: str | None = None,
    ) -> ArtifactMetadata:
        """Persist artifact bytes atomically.

        Returns ArtifactMetadata (without filesystem path).
        Raises ValueError if artifact_id is unsafe.
        """
        ...

    @abstractmethod
    def exists(self, artifact_id: str) -> bool:
        """Return True if an artifact with this ID is stored and accessible."""
        ...

    @abstractmethod
    def read(self, artifact_id: str) -> bytes:
        """Read and return all bytes for the artifact.

        Raises FileNotFoundError if not found.
        Raises ValueError if artifact_id is unsafe.
        """
        ...

    @abstractmethod
    def open(self, artifact_id: str) -> IO[bytes]:
        """Return a readable binary stream for the artifact.

        Raises FileNotFoundError if not found.
        Raises ValueError if artifact_id is unsafe.
        The caller is responsible for closing the stream.
        """
        ...

    @abstractmethod
    def metadata(self, artifact_id: str) -> ArtifactMetadata:
        """Return stored metadata for the artifact.

        Raises FileNotFoundError if not found.
        Raises ValueError if artifact_id is unsafe.
        Does NOT include filesystem path.
        """
        ...

    @abstractmethod
    def delete(self, artifact_id: str) -> None:
        """Delete the artifact.  Idempotent (no error if not found).

        Raises ValueError if artifact_id is unsafe.
        """
        ...

    @abstractmethod
    def list_expired(self, before: datetime) -> list[str]:
        """Return artifact IDs whose expires_at is before the given datetime."""
        ...

    def cleanup_expired(self, retention_days: int) -> int:
        """Delete all artifacts older than retention_days.  Returns count deleted.

        Uses list_expired + delete so subclasses do not need to override.
        Safe to call at startup or periodically.
        """
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        expired = self.list_expired(before=cutoff)
        deleted = 0
        for artifact_id in expired:
            try:
                self.delete(artifact_id)
                deleted += 1
                logger.info(
                    "event=artifact_expired_deleted artifact_id=%s", artifact_id
                )
            except Exception as exc:
                logger.warning(
                    "event=artifact_delete_failed artifact_id=%s error=%s",
                    artifact_id, exc,
                )
        if deleted:
            logger.info("event=artifact_cleanup_complete deleted=%d", deleted)
        return deleted


# ---------------------------------------------------------------------------
# Local filesystem implementation
# ---------------------------------------------------------------------------


class LocalArtifactStore(ArtifactStore):
    """Local filesystem artifact store.

    Suitable for single-instance development deployments.
    NOT suitable for multi-instance production use.

    Files are stored as:
        <base_dir>/<artifact_id>          — artifact bytes
        <base_dir>/<artifact_id>.meta     — JSON metadata (no path)

    Atomicity: bytes are written to a temp file then renamed.
    """

    def __init__(
        self,
        base_dir: str | Path | None = None,
        retention_days: int = 7,
        *,
        artifact_dir: str | Path | None = None,
    ) -> None:
        # Accept either base_dir (positional) or artifact_dir (keyword alias)
        resolved_dir = base_dir if base_dir is not None else artifact_dir
        if resolved_dir is None:
            raise TypeError(
                "LocalArtifactStore requires either 'base_dir' or 'artifact_dir'."
            )
        self._base = Path(resolved_dir).resolve()
        self._base.mkdir(parents=True, exist_ok=True)
        self._retention_days = retention_days
        logger.info("event=artifact_store_init base_dir=%s", self._base)

    def _artifact_path(self, artifact_id: str) -> Path:
        """Return the data file path.  artifact_id already validated."""
        return self._base / artifact_id

    def _meta_path(self, artifact_id: str) -> Path:
        return self._base / f"{artifact_id}.meta"

    def _write_metadata(self, meta: ArtifactMetadata) -> None:
        path = self._meta_path(meta.artifact_id)
        tmp = path.parent / (path.name + ".tmp")
        tmp.write_text(meta.model_dump_json(), encoding="utf-8")
        tmp.replace(path)

    def _read_metadata(self, artifact_id: str) -> ArtifactMetadata:
        path = self._meta_path(artifact_id)
        if not path.exists():
            raise FileNotFoundError(
                f"Artifact metadata not found for ID: {artifact_id}"
            )
        return ArtifactMetadata.model_validate_json(path.read_text(encoding="utf-8"))

    # ------------------------------------------------------------------

    def save(
        self,
        artifact_id: str,
        data: bytes,
        filename: str,
        content_type: str,
        job_id: str | None = None,
    ) -> ArtifactMetadata:
        _validate_artifact_id(artifact_id)
        dest = self._artifact_path(artifact_id)

        # Write bytes atomically
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self._base, prefix=f"{artifact_id}.tmp")
        try:
            with os.fdopen(tmp_fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                os.fsync(fh.fileno())
        except Exception:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise
        os.replace(tmp_path, dest)

        # Compute SHA-256
        sha256 = hashlib.sha256(data).hexdigest()
        now = datetime.now(timezone.utc)
        meta = ArtifactMetadata(
            artifact_id=artifact_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
            sha256=sha256,
            created_at=now,
            job_id=job_id,
        )
        self._write_metadata(meta)
        logger.info(
            "event=artifact_saved artifact_id=%s size_bytes=%d sha256=%.12s",
            artifact_id, len(data), sha256,
        )
        return meta

    def exists(self, artifact_id: str) -> bool:
        try:
            _validate_artifact_id(artifact_id)
        except ValueError:
            return False
        return self._artifact_path(artifact_id).exists()

    def read(self, artifact_id: str) -> bytes:
        _validate_artifact_id(artifact_id)
        path = self._artifact_path(artifact_id)
        if not path.exists():
            raise FileNotFoundError(f"Artifact not found: {artifact_id}")
        return path.read_bytes()

    def open(self, artifact_id: str) -> IO[bytes]:
        _validate_artifact_id(artifact_id)
        path = self._artifact_path(artifact_id)
        if not path.exists():
            raise FileNotFoundError(f"Artifact not found: {artifact_id}")
        return open(path, "rb")  # noqa: SIM115  caller must close

    def metadata(self, artifact_id: str) -> ArtifactMetadata:
        _validate_artifact_id(artifact_id)
        return self._read_metadata(artifact_id)

    def delete(self, artifact_id: str) -> None:
        _validate_artifact_id(artifact_id)
        for path in (self._artifact_path(artifact_id), self._meta_path(artifact_id)):
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning(
                    "event=artifact_delete_error artifact_id=%s path=%s error=%s",
                    artifact_id, path, exc,
                )

    def list_expired(self, before: datetime) -> list[str]:
        expired: list[str] = []
        for meta_file in self._base.glob("*.meta"):
            try:
                meta = ArtifactMetadata.model_validate_json(
                    meta_file.read_text(encoding="utf-8")
                )
                if meta.expires_at and meta.expires_at < before:
                    expired.append(meta.artifact_id)
            except Exception as exc:
                logger.warning(
                    "event=artifact_meta_read_error path=%s error=%s", meta_file, exc
                )
        return expired


# ---------------------------------------------------------------------------
# In-memory implementation for testing
# ---------------------------------------------------------------------------


class InMemoryArtifactStore(ArtifactStore):
    """Non-persistent in-memory artifact store for unit tests."""

    def __init__(self, retention_days: int = 7) -> None:
        self._data: dict[str, bytes] = {}
        self._meta: dict[str, ArtifactMetadata] = {}
        self._retention_days = retention_days

    def save(
        self,
        artifact_id: str,
        data: bytes,
        filename: str,
        content_type: str,
        job_id: str | None = None,
    ) -> ArtifactMetadata:
        _validate_artifact_id(artifact_id)
        sha256 = hashlib.sha256(data).hexdigest()
        meta = ArtifactMetadata(
            artifact_id=artifact_id,
            filename=filename,
            content_type=content_type,
            size_bytes=len(data),
            sha256=sha256,
            created_at=datetime.now(timezone.utc),
            job_id=job_id,
        )
        self._data[artifact_id] = data
        self._meta[artifact_id] = meta
        return meta

    def exists(self, artifact_id: str) -> bool:
        try:
            _validate_artifact_id(artifact_id)
        except ValueError:
            return False
        return artifact_id in self._data

    def read(self, artifact_id: str) -> bytes:
        _validate_artifact_id(artifact_id)
        if artifact_id not in self._data:
            raise FileNotFoundError(f"Artifact not found: {artifact_id}")
        return self._data[artifact_id]

    def open(self, artifact_id: str) -> IO[bytes]:
        return io.BytesIO(self.read(artifact_id))

    def metadata(self, artifact_id: str) -> ArtifactMetadata:
        _validate_artifact_id(artifact_id)
        if artifact_id not in self._meta:
            raise FileNotFoundError(f"Artifact metadata not found: {artifact_id}")
        return self._meta[artifact_id]

    def delete(self, artifact_id: str) -> None:
        _validate_artifact_id(artifact_id)
        self._data.pop(artifact_id, None)
        self._meta.pop(artifact_id, None)

    def list_expired(self, before: datetime) -> list[str]:
        expired = []
        for artifact_id, meta in self._meta.items():
            if meta.expires_at and meta.expires_at < before:
                expired.append(artifact_id)
        return expired
