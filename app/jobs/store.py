"""Phase 6 — SQLiteJobStore: durable persistence for extraction jobs."""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Generator, List, Optional

from app.models.phase6 import (
    ExtractionJob,
    ExtractionJobProgress,
    JobArtifact,
    JobArtifactFormat,
    JobError,
    JobStage,
    JobStatus,
    TERMINAL_STATUSES,
)

logger = logging.getLogger(__name__)
_UTC = timezone.utc


def _now() -> datetime:
    return datetime.now(_UTC)


def _ts(dt: Optional[datetime]) -> Optional[str]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=_UTC)
    return dt.isoformat()


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_UTC)
        return dt
    except (ValueError, TypeError):
        return None


@dataclass
class JobListResult:
    items: List[ExtractionJob]
    total: int
    limit: int
    offset: int


class SQLiteJobStore:
    """SQLite-backed job store.

    KNOWN LIMITATION: Suitable for local/single-instance deployment.
    Replace with a managed relational backend for BTP multi-instance.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS extraction_jobs (
        job_id           TEXT PRIMARY KEY,
        status           TEXT NOT NULL,
        stage            TEXT,
        request_json     TEXT,
        created_at       TEXT NOT NULL,
        updated_at       TEXT NOT NULL,
        started_at       TEXT,
        completed_at     TEXT,
        progress_json    TEXT,
        result_summary_json TEXT,
        result_status    TEXT,
        error_json       TEXT,
        artifact_id      TEXT,
        artifact_format  TEXT,
        artifact_filename TEXT,
        artifact_size    INTEGER,
        artifact_sha256  TEXT,
        artifact_created_at TEXT,
        retry_of_job_id  TEXT,
        attempt          INTEGER NOT NULL DEFAULT 1,
        worker_id        TEXT,
        heartbeat_at     TEXT,
        lease_expires_at TEXT,
        created_by       TEXT,
        idempotency_key  TEXT,
        request_hash     TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_jobs_status ON extraction_jobs(status);
    CREATE INDEX IF NOT EXISTS idx_jobs_created_at ON extraction_jobs(created_at);
    CREATE INDEX IF NOT EXISTS idx_jobs_idempotency_key ON extraction_jobs(idempotency_key);
    """

    def __init__(self, db_path: str = "ncoreiq_jobs.db") -> None:
        self._db_path = db_path
        self._lock = threading.Lock()

    def initialize(self) -> None:
        with self._connect() as conn:
            conn.executescript(self._SCHEMA)
            conn.commit()
        logger.info("job_store=initialized db=%s", self._db_path)

    @contextmanager
    def _connect(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(self._db_path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            yield conn
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Core CRUD
    # ------------------------------------------------------------------

    def create_job(self, job: ExtractionJob) -> ExtractionJob:
        row = self._job_to_row(job)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO extraction_jobs (
                    job_id, status, stage, request_json,
                    created_at, updated_at, started_at, completed_at,
                    progress_json, result_summary_json, result_status, error_json,
                    artifact_id, artifact_format, artifact_filename,
                    artifact_size, artifact_sha256, artifact_created_at,
                    retry_of_job_id, attempt, worker_id,
                    heartbeat_at, lease_expires_at, created_by,
                    idempotency_key, request_hash
                ) VALUES (
                    :job_id, :status, :stage, :request_json,
                    :created_at, :updated_at, :started_at, :completed_at,
                    :progress_json, :result_summary_json, :result_status, :error_json,
                    :artifact_id, :artifact_format, :artifact_filename,
                    :artifact_size, :artifact_sha256, :artifact_created_at,
                    :retry_of_job_id, :attempt, :worker_id,
                    :heartbeat_at, :lease_expires_at, :created_by,
                    :idempotency_key, :request_hash
                )
                """,
                row,
            )
        logger.info("event=job_created job_id=%s", job.job_id)
        return job

    def get_job(self, job_id: str) -> Optional[ExtractionJob]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM extraction_jobs WHERE job_id = ?", (job_id,)
            ).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_jobs(
        self,
        status: Optional[str] = None,
        created_after: Optional[datetime] = None,
        created_before: Optional[datetime] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> JobListResult:
        limit = min(int(limit), 200)
        offset = max(int(offset), 0)

        clauses: list[str] = []
        params: list[Any] = []

        if status:
            clauses.append("status = ?")
            params.append(status)
        if created_after:
            clauses.append("created_at >= ?")
            params.append(_ts(created_after))
        if created_before:
            clauses.append("created_at <= ?")
            params.append(_ts(created_before))

        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

        with self._connect() as conn:
            total = conn.execute(
                f"SELECT COUNT(*) FROM extraction_jobs {where}", params
            ).fetchone()[0]
            rows = conn.execute(
                f"SELECT * FROM extraction_jobs {where} ORDER BY created_at DESC LIMIT ? OFFSET ?",
                params + [limit, offset],
            ).fetchall()

        items = [self._row_to_job(r) for r in rows]
        return JobListResult(items=items, total=total, limit=limit, offset=offset)

    def update_job(self, job_id: str, status: Optional[JobStatus] = None, **fields: Any) -> None:
        """Update job fields by job_id using keyword arguments.

        Special handling:
        - ``progress`` (ExtractionJobProgress) → serialised to ``progress_json``
        - ``JobStatus`` enum values → their string `.value`
        - ``JobStage`` enum values → their string `.value`
        - ``datetime`` values → ISO-format UTC string
        """
        updates: dict[str, Any] = {}
        if status is not None:
            updates["status"] = status.value if isinstance(status, JobStatus) else status

        for k, v in fields.items():
            if k == "progress" and isinstance(v, ExtractionJobProgress):
                # Serialize progress object to the correct column name
                p_dict = v.model_dump()
                pct = p_dict.get("percent_complete", 0.0)
                stage_val = p_dict.get("current_stage")
                if stage_val not in (JobStage.COMPLETED.value, "COMPLETED") and pct >= 100.0:
                    p_dict["percent_complete"] = 99.0
                updates["progress_json"] = json.dumps(p_dict, default=str)
            elif isinstance(v, JobStatus):
                updates[k] = v.value
            elif isinstance(v, JobStage):
                updates[k] = v.value
            elif isinstance(v, datetime):
                updates[k] = _ts(v)
            else:
                updates[k] = v

        if not updates:
            return
        updates["updated_at"] = _ts(_now())
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["_job_id"] = job_id
        with self._connect() as conn:
            conn.execute(
                f"UPDATE extraction_jobs SET {set_clause} WHERE job_id = :_job_id",
                updates,
            )

    def update_progress(self, job_id: str, progress: ExtractionJobProgress) -> None:
        """Persist progress update. Prevents premature 100% reporting."""
        p_dict = progress.model_dump()
        pct = p_dict.get("percent_complete", 0.0)
        stage_val = p_dict.get("current_stage")
        if stage_val not in (JobStage.COMPLETED.value, "COMPLETED") and pct >= 100.0:
            p_dict["percent_complete"] = 99.0
        with self._connect() as conn:
            conn.execute(
                "UPDATE extraction_jobs SET progress_json = ?, updated_at = ? WHERE job_id = ?",
                (json.dumps(p_dict, default=str), _ts(_now()), job_id),
            )

    def claim_next_job(self, worker_id: str, lease_seconds: int = 120) -> Optional[ExtractionJob]:
        """Atomically claim the next QUEUED job. Returns claimed job or None."""
        expires = _now() + timedelta(seconds=lease_seconds)
        now_ts = _ts(_now())
        expires_ts = _ts(expires)
        with self._lock:
            with self._connect() as conn:
                conn.execute("BEGIN EXCLUSIVE")
                row = conn.execute(
                    "SELECT * FROM extraction_jobs WHERE status = 'QUEUED' ORDER BY created_at ASC LIMIT 1"
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                job_id = row["job_id"]
                conn.execute(
                    """
                    UPDATE extraction_jobs
                    SET status = 'RUNNING', worker_id = ?, started_at = ?,
                        heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                    WHERE job_id = ? AND status = 'QUEUED'
                    """,
                    (worker_id, now_ts, now_ts, expires_ts, now_ts, job_id),
                )
                conn.execute("COMMIT")
                updated = conn.execute(
                    "SELECT * FROM extraction_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
        if updated and updated["status"] == "RUNNING":
            logger.info("event=job_claimed job_id=%s worker_id=%s", job_id, worker_id)
            return self._row_to_job(updated)
        return None

    def heartbeat_job(self, job_id: str, worker_id: str, lease_seconds: int = 120) -> None:
        """Renew the worker lease for a running job."""
        expires = _now() + timedelta(seconds=lease_seconds)
        now_ts = _ts(_now())
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE extraction_jobs
                SET heartbeat_at = ?, lease_expires_at = ?, updated_at = ?
                WHERE job_id = ? AND worker_id = ?
                """,
                (now_ts, _ts(expires), now_ts, job_id, worker_id),
            )

    def request_cancel(self, job_id: str) -> bool:
        """Request cancellation of a RUNNING job (sets CANCEL_REQUESTED)."""
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE extraction_jobs
                SET status = 'CANCEL_REQUESTED', updated_at = ?
                WHERE job_id = ? AND status = 'RUNNING'
                """,
                (_ts(_now()), job_id),
            )
            return cursor.rowcount > 0

    def finish_job(
        self,
        job_id: str,
        status: JobStatus = JobStatus.SUCCEEDED,
        stage: Optional[Any] = None,
        result_status: Optional[str] = None,
        result_summary: Optional[dict] = None,
        progress: Optional[ExtractionJobProgress] = None,
        artifact: Optional[JobArtifact] = None,
    ) -> None:
        """Mark a job as finished (SUCCEEDED or other terminal state).

        ``stage`` is accepted but ignored — the stage is always set to COMPLETED.
        """
        now_ts = _ts(_now())
        updates: dict[str, Any] = {
            "status": status.value if isinstance(status, JobStatus) else status,
            "stage": JobStage.COMPLETED.value,
            "completed_at": now_ts,
            "updated_at": now_ts,
        }
        if result_status is not None:
            updates["result_status"] = result_status
        if result_summary is not None:
            updates["result_summary_json"] = json.dumps(result_summary, default=str)
        if progress is not None:
            updates["progress_json"] = json.dumps(progress.model_dump(), default=str)
        if artifact is not None:
            updates["artifact_id"] = artifact.artifact_id
            updates["artifact_format"] = artifact.format
            updates["artifact_filename"] = artifact.filename
            updates["artifact_size"] = artifact.size_bytes
            updates["artifact_sha256"] = artifact.sha256
            updates["artifact_created_at"] = _ts(artifact.created_at)
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["_job_id"] = job_id
        with self._connect() as conn:
            conn.execute(
                f"UPDATE extraction_jobs SET {set_clause} WHERE job_id = :_job_id",
                updates,
            )
        logger.info("event=job_finished job_id=%s status=%s", job_id, updates["status"])

    def fail_job(
        self,
        job_id: str,
        error_code: Optional[str] = None,
        error_message: Optional[str] = None,
        error: Optional[JobError] = None,
        stage: Optional[str] = None,
        progress: Optional[ExtractionJobProgress] = None,
    ) -> None:
        """Mark a job as FAILED."""
        now_ts = _ts(_now())
        if error is None and error_code is not None:
            error = JobError(
                code=error_code,
                message=error_message or "Job failed",
                stage=stage or JobStage.EXTRACTING.value,
                retryable=False,
            )
        error_json = json.dumps(error.model_dump(), default=str) if error else None
        updates: dict[str, Any] = {
            "status": "FAILED",
            "completed_at": now_ts,
            "updated_at": now_ts,
            "error_json": error_json,
        }
        if stage:
            updates["stage"] = stage
        if progress is not None:
            updates["progress_json"] = json.dumps(progress.model_dump(), default=str)
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["_job_id"] = job_id
        with self._connect() as conn:
            conn.execute(
                f"UPDATE extraction_jobs SET {set_clause} WHERE job_id = :_job_id",
                updates,
            )
        logger.info(
            "event=job_failed job_id=%s code=%s",
            job_id,
            error_code or (error.code if error else "unknown"),
        )

    def retry_job(self, original_job_id: str) -> ExtractionJob:
        """Create a new QUEUED job that is a retry of the original."""
        original = self.get_job(original_job_id)
        if original is None:
            raise ValueError(f"Job not found: {original_job_id}")
        if original.status not in TERMINAL_STATUSES:
            raise ValueError(
                f"Job {original_job_id} is not in a retryable terminal state: {original.status}"
            )
        new_job = ExtractionJob(
            job_id=str(uuid.uuid4()),
            status=JobStatus.QUEUED,
            stage=JobStage.VALIDATING,
            created_at=_now(),
            updated_at=_now(),
            request=original.request,
            retry_of_job_id=original_job_id,
            attempt=(original.attempt or 1) + 1,
            progress=ExtractionJobProgress(),
        )
        self.create_job(new_job)
        logger.info(
            "event=job_retried original_job_id=%s new_job_id=%s attempt=%d",
            original_job_id,
            new_job.job_id,
            new_job.attempt,
        )
        return new_job

    def cancel_queued_job(self, job_id: str) -> Optional[ExtractionJob]:
        """Directly cancel a QUEUED job without going through CANCEL_REQUESTED.

        Returns the updated ExtractionJob if the cancel was applied, or None if
        the job was no longer QUEUED (e.g. already claimed by a worker).
        """
        now_ts = _ts(_now())
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE extraction_jobs
                SET status = 'CANCELLED', updated_at = ?, completed_at = ?
                WHERE job_id = ? AND status = 'QUEUED'
                """,
                (now_ts, now_ts, job_id),
            )
            if cursor.rowcount > 0:
                logger.info("event=job_cancelled job_id=%s (was QUEUED)", job_id)
                row = conn.execute(
                    "SELECT * FROM extraction_jobs WHERE job_id = ?", (job_id,)
                ).fetchone()
                return self._row_to_job(row) if row else None
        return None

    def finalize_cancellation(
        self, job_id: str, progress: Optional[ExtractionJobProgress] = None
    ) -> None:
        """Move a CANCEL_REQUESTED job to CANCELLED."""
        now = _ts(_now())
        updates: dict[str, Any] = {
            "status": "CANCELLED",
            "completed_at": now,
            "updated_at": now,
        }
        if progress is not None:
            updates["progress_json"] = json.dumps(progress.model_dump(), default=str)
        set_clause = ", ".join(f"{k} = :{k}" for k in updates)
        updates["_job_id"] = job_id
        with self._connect() as conn:
            conn.execute(
                f"UPDATE extraction_jobs SET {set_clause} WHERE job_id = :_job_id",
                updates,
            )
        logger.info("event=job_cancelled job_id=%s (was CANCEL_REQUESTED)", job_id)

    def recover_stale_jobs(self, lease_seconds: int = 120) -> int:
        """Move RUNNING/CANCEL_REQUESTED jobs with expired leases back to QUEUED."""
        now_ts = _ts(_now())
        with self._connect() as conn:
            cursor = conn.execute(
                """
                UPDATE extraction_jobs
                SET status = 'QUEUED', worker_id = NULL,
                    started_at = NULL, heartbeat_at = NULL,
                    lease_expires_at = NULL, updated_at = ?
                WHERE status IN ('RUNNING', 'CANCEL_REQUESTED')
                  AND lease_expires_at IS NOT NULL
                  AND lease_expires_at < ?
                """,
                (now_ts, now_ts),
            )
            count = cursor.rowcount
        if count > 0:
            logger.warning("event=stale_job_recovered count=%d", count)
        return count

    def delete_expired_jobs(self, retention_days: int = 7) -> int:
        """Delete terminal jobs older than retention_days. Returns count deleted."""
        if retention_days <= 0:
            cutoff = _now()
        else:
            cutoff = _now() - timedelta(days=retention_days)
        cutoff_ts = _ts(cutoff)
        terminal = "', '".join(s.value for s in TERMINAL_STATUSES)
        with self._connect() as conn:
            cursor = conn.execute(
                f"""
                DELETE FROM extraction_jobs
                WHERE status IN ('{terminal}')
                  AND completed_at IS NOT NULL
                  AND completed_at < ?
                """,
                (cutoff_ts,),
            )
            count = cursor.rowcount
        if count > 0:
            logger.info(
                "event=jobs_expired count=%d retention_days=%d", count, retention_days
            )
        return count

    def find_by_idempotency_key(self, key: str) -> Optional[ExtractionJob]:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM extraction_jobs WHERE idempotency_key = ? ORDER BY created_at DESC LIMIT 1",
                (key,),
            ).fetchone()
        return self._row_to_job(row) if row else None

    def count_by_status(self) -> dict[str, int]:
        """Return {status: count} mapping for health/diagnostics."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as cnt FROM extraction_jobs GROUP BY status"
            ).fetchall()
        return {r["status"]: r["cnt"] for r in rows}

    # ------------------------------------------------------------------
    # Test helpers (clearly internal)
    # ------------------------------------------------------------------

    def _force_expire_lease(self, job_id: str) -> None:
        """Force lease_expires_at to the past. Test helper only."""
        past = _ts(_now() - timedelta(seconds=3600))
        with self._connect() as conn:
            conn.execute(
                "UPDATE extraction_jobs SET lease_expires_at = ?, heartbeat_at = ? WHERE job_id = ?",
                (past, past, job_id),
            )

    def _force_old_completed_at(self, job_id: str, days_ago: int = 10) -> None:
        """Force completed_at into the past. Test helper only."""
        past = _ts(_now() - timedelta(days=days_ago))
        with self._connect() as conn:
            conn.execute(
                "UPDATE extraction_jobs SET completed_at = ?, updated_at = ? WHERE job_id = ?",
                (past, past, job_id),
            )

    # ------------------------------------------------------------------
    # Serialisation helpers
    # ------------------------------------------------------------------

    def _job_to_row(self, job: ExtractionJob) -> dict:
        request_val = job.request
        if hasattr(request_val, "model_dump"):
            request_json = json.dumps(request_val.model_dump(), default=str)
        elif isinstance(request_val, dict):
            request_json = json.dumps(request_val, default=str)
        elif request_val is not None:
            request_json = json.dumps(request_val, default=str)
        else:
            request_json = None

        artifact = job.artifact
        progress = job.progress

        return {
            "job_id": job.job_id,
            "status": job.status.value if isinstance(job.status, JobStatus) else job.status,
            "stage": job.stage.value if isinstance(job.stage, JobStage) else (job.stage or None),
            "request_json": request_json,
            "created_at": _ts(job.created_at),
            "updated_at": _ts(job.updated_at),
            "started_at": _ts(job.started_at),
            "completed_at": _ts(job.completed_at),
            "progress_json": json.dumps(progress.model_dump(), default=str) if progress else None,
            "result_summary_json": None,
            "result_status": job.result_status,
            "error_json": (
                json.dumps(job.error.model_dump(), default=str) if job.error else None
            ),
            "artifact_id": artifact.artifact_id if artifact else None,
            "artifact_format": artifact.format if artifact else None,
            "artifact_filename": artifact.filename if artifact else None,
            "artifact_size": artifact.size_bytes if artifact else None,
            "artifact_sha256": artifact.sha256 if artifact else None,
            "artifact_created_at": _ts(artifact.created_at) if artifact else None,
            "retry_of_job_id": job.retry_of_job_id,
            "attempt": job.attempt or 1,
            "worker_id": job.worker_id,
            "heartbeat_at": _ts(job.heartbeat_at),
            "lease_expires_at": _ts(job.lease_expires_at),
            "created_by": job.created_by,
            "idempotency_key": getattr(job, "idempotency_key", None),
            "request_hash": getattr(job, "request_hash", None),
        }

    def _row_to_job(self, row: sqlite3.Row) -> ExtractionJob:
        r = dict(row)

        request = None
        if r.get("request_json"):
            try:
                request = json.loads(r["request_json"])
            except (json.JSONDecodeError, TypeError):
                request = None

        progress = ExtractionJobProgress()
        if r.get("progress_json"):
            try:
                pdata = json.loads(r["progress_json"])
                progress = ExtractionJobProgress(**pdata)
            except Exception:
                pass

        error = None
        if r.get("error_json"):
            try:
                edata = json.loads(r["error_json"])
                error = JobError(**edata)
            except Exception:
                pass

        artifact = None
        if r.get("artifact_id"):
            try:
                artifact = JobArtifact(
                    artifact_id=r["artifact_id"],
                    format=r.get("artifact_format", "json"),
                    filename=r.get("artifact_filename", ""),
                    content_type="application/json",
                    size_bytes=r.get("artifact_size", 0),
                    sha256=r.get("artifact_sha256", ""),
                    created_at=_parse_dt(r.get("artifact_created_at")) or _now(),
                )
            except Exception:
                pass

        result_summary = None
        if r.get("result_summary_json"):
            try:
                result_summary = json.loads(r["result_summary_json"])
            except Exception:
                pass

        return ExtractionJob(
            job_id=r["job_id"],
            status=r["status"],
            stage=r.get("stage"),
            request=request,
            created_at=_parse_dt(r["created_at"]),
            updated_at=_parse_dt(r["updated_at"]),
            started_at=_parse_dt(r.get("started_at")),
            completed_at=_parse_dt(r.get("completed_at")),
            progress=progress,
            result_status=r.get("result_status"),
            error=error,
            artifact=artifact,
            retry_of_job_id=r.get("retry_of_job_id"),
            attempt=r.get("attempt", 1),
            worker_id=r.get("worker_id"),
            heartbeat_at=_parse_dt(r.get("heartbeat_at")),
            lease_expires_at=_parse_dt(r.get("lease_expires_at")),
            created_by=r.get("created_by"),
            idempotency_key=r.get("idempotency_key"),
            request_hash=r.get("request_hash"),
        )


# Public alias — service code imports ``JobStore``; the concrete
# implementation is ``SQLiteJobStore``.  Replace this alias when a
# managed relational backend is introduced for BTP.
JobStore = SQLiteJobStore
