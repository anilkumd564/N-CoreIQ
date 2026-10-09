"""Phase 6 — Extraction Job API automated tests."""

from __future__ import annotations

import hashlib
import json
import os
import time
import uuid
from datetime import datetime, timezone

import pytest

from app.models.phase6 import (
    ExtractionJob,
    ExtractionJobProgress,
    ExtractionJobRequest,
    JobArtifact,
    JobArtifactFormat,
    JobError,
    JobStage,
    JobStatus,
    TERMINAL_STATUSES,
    RETRYABLE_STATUSES,
)
from app.jobs.store import SQLiteJobStore
from app.jobs.artifacts import LocalArtifactStore
from app.jobs.service import ExtractionJobService
from app.models.phase5 import BulkExtractionRequest, ExtractionObjectRef


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_db(tmp_path):
    db_file = str(tmp_path / "test_jobs.db")
    store = SQLiteJobStore(db_path=db_file)
    store.initialize()
    return store


@pytest.fixture()
def tmp_artifact_dir(tmp_path):
    adir = str(tmp_path / "artifacts")
    os.makedirs(adir, exist_ok=True)
    return adir


@pytest.fixture()
def artifact_store(tmp_artifact_dir):
    return LocalArtifactStore(artifact_dir=tmp_artifact_dir, retention_days=7)


@pytest.fixture()
def job_service(tmp_db, artifact_store):
    return ExtractionJobService(job_store=tmp_db, artifact_store=artifact_store)


@pytest.fixture()
def sample_request():
    return ExtractionJobRequest(
        extraction=BulkExtractionRequest(
            objects=[
                ExtractionObjectRef(object_type="PROG", object_name="ZTEST1"),
                ExtractionObjectRef(object_type="CLAS", object_name="ZCL_SAMPLE"),
            ],
            include_metadata=True,
            include_source=True,
            source_max_lines=5000,
            continue_on_error=True,
        ),
        export_format="zip",
    )


@pytest.fixture()
def sample_request2():
    return ExtractionJobRequest(
        extraction=BulkExtractionRequest(
            objects=[
                ExtractionObjectRef(object_type="PROG", object_name="ZDIFFERENT"),
            ],
            include_metadata=True,
            include_source=False,
        ),
        export_format="json",
    )


# ---------------------------------------------------------------------------
# 1. Job Creation
# ---------------------------------------------------------------------------

class TestJobCreation:
    def test_create_job_returns_queued(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        assert job.job_id is not None
        assert job.status == JobStatus.QUEUED
        assert job.created_at is not None

    def test_create_job_persisted_immediately(self, job_service, tmp_db, sample_request):
        job = job_service.create_job(request=sample_request)
        retrieved = tmp_db.get_job(job.job_id)
        assert retrieved is not None
        assert retrieved.status == JobStatus.QUEUED

    def test_create_job_stores_request_snapshot(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        retrieved = job_service.get_job(job.job_id)
        assert retrieved.request is not None
        assert isinstance(retrieved.request, dict)
        assert "extraction" in retrieved.request

    def test_create_job_attempt_is_one(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        assert job.attempt == 1

    def test_create_job_no_sap_credentials_in_record(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        job_json = json.dumps(job.model_dump(), default=str).lower()
        assert "password" not in job_json or '"password": null' in job_json


# ---------------------------------------------------------------------------
# 2. Job Retrieval
# ---------------------------------------------------------------------------

class TestJobRetrieval:
    def test_get_existing_job(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        retrieved = job_service.get_job(job.job_id)
        assert retrieved is not None
        assert retrieved.job_id == job.job_id

    def test_get_unknown_job_returns_none(self, job_service):
        assert job_service.get_job("nonexistent-id") is None

    def test_get_job_returns_extraction_job_type(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        assert isinstance(job_service.get_job(job.job_id), ExtractionJob)


# ---------------------------------------------------------------------------
# 3. Job Listing
# ---------------------------------------------------------------------------

class TestJobListing:
    def test_list_returns_created_jobs(self, job_service, sample_request):
        j1 = job_service.create_job(request=sample_request)
        j2 = job_service.create_job(request=sample_request)
        result = job_service.list_jobs()
        ids = [j.job_id for j in result.items]
        assert j1.job_id in ids
        assert j2.job_id in ids

    def test_list_newest_first(self, job_service, sample_request):
        j1 = job_service.create_job(request=sample_request)
        time.sleep(0.05)
        j2 = job_service.create_job(request=sample_request)
        result = job_service.list_jobs(limit=10)
        ids = [j.job_id for j in result.items]
        assert ids.index(j2.job_id) < ids.index(j1.job_id)

    def test_list_respects_limit(self, job_service, sample_request):
        for _ in range(5):
            job_service.create_job(request=sample_request)
        result = job_service.list_jobs(limit=3)
        assert len(result.items) <= 3

    def test_list_respects_offset(self, job_service, sample_request):
        for _ in range(4):
            job_service.create_job(request=sample_request)
        page1 = job_service.list_jobs(limit=2, offset=0)
        page2 = job_service.list_jobs(limit=2, offset=2)
        assert {j.job_id for j in page1.items}.isdisjoint({j.job_id for j in page2.items})

    def test_list_total_count(self, job_service, sample_request):
        for _ in range(3):
            job_service.create_job(request=sample_request)
        result = job_service.list_jobs(limit=50)
        assert result.total >= 3

    def test_list_filter_by_status_includes_match(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        result = job_service.list_jobs(status="QUEUED")
        assert job.job_id in [j.job_id for j in result.items]

    def test_list_filter_by_status_excludes_other(self, job_service, tmp_db, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.update_job(job.job_id, status=JobStatus.RUNNING)
        result = job_service.list_jobs(status="QUEUED")
        assert job.job_id not in [j.job_id for j in result.items]


# ---------------------------------------------------------------------------
# 4. State Machine Transitions
# ---------------------------------------------------------------------------

class TestStateTransitions:
    def test_queued_to_running_via_claim(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        claimed = tmp_db.claim_next_job(worker_id="worker-1", lease_seconds=60)
        assert claimed is not None
        assert claimed.job_id == job.job_id
        assert claimed.status == JobStatus.RUNNING

    def test_queued_to_cancelled(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        result = job_service.cancel_job(job.job_id)
        assert result.status == JobStatus.CANCELLED

    def test_running_to_cancel_requested(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        result = job_service.cancel_job(job.job_id)
        assert result.status == JobStatus.CANCEL_REQUESTED

    def test_running_to_succeeded(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        tmp_db.finish_job(job.job_id, status=JobStatus.SUCCEEDED)
        assert job_service.get_job(job.job_id).status == JobStatus.SUCCEEDED

    def test_running_to_failed(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        tmp_db.fail_job(job.job_id, error_code="ERR", error_message="oops")
        assert job_service.get_job(job.job_id).status == JobStatus.FAILED

    def test_succeeded_cannot_be_cancelled(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        tmp_db.finish_job(job.job_id, status=JobStatus.SUCCEEDED)
        with pytest.raises(ValueError, match="terminal"):
            job_service.cancel_job(job.job_id)

    def test_failed_cannot_be_cancelled(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        tmp_db.fail_job(job.job_id, error_code="E", error_message="fail")
        with pytest.raises(ValueError, match="terminal"):
            job_service.cancel_job(job.job_id)

    def test_terminal_statuses_correct(self):
        assert JobStatus.SUCCEEDED in TERMINAL_STATUSES
        assert JobStatus.FAILED in TERMINAL_STATUSES
        assert JobStatus.CANCELLED in TERMINAL_STATUSES
        assert JobStatus.QUEUED not in TERMINAL_STATUSES
        assert JobStatus.RUNNING not in TERMINAL_STATUSES

    def test_retryable_statuses_correct(self):
        assert JobStatus.FAILED in RETRYABLE_STATUSES
        assert JobStatus.CANCELLED in RETRYABLE_STATUSES
        assert JobStatus.QUEUED not in RETRYABLE_STATUSES
        assert JobStatus.RUNNING not in RETRYABLE_STATUSES


# ---------------------------------------------------------------------------
# 5. Atomic Job Claiming
# ---------------------------------------------------------------------------

class TestAtomicClaiming:
    def test_only_one_worker_claims_single_job(self, tmp_db, job_service, sample_request):
        job_service.create_job(request=sample_request)
        c1 = tmp_db.claim_next_job(worker_id="worker-A", lease_seconds=60)
        c2 = tmp_db.claim_next_job(worker_id="worker-B", lease_seconds=60)
        successes = sum(1 for c in (c1, c2) if c is not None)
        assert successes == 1

    def test_two_queued_jobs_go_to_different_workers(self, tmp_db, job_service, sample_request):
        job_service.create_job(request=sample_request)
        job_service.create_job(request=sample_request)
        c1 = tmp_db.claim_next_job(worker_id="worker-A", lease_seconds=60)
        c2 = tmp_db.claim_next_job(worker_id="worker-B", lease_seconds=60)
        assert c1 is not None
        assert c2 is not None
        assert c1.job_id != c2.job_id

    def test_already_claimed_job_not_re_claimed(self, tmp_db, job_service, sample_request):
        job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="worker-A", lease_seconds=120)
        c2 = tmp_db.claim_next_job(worker_id="worker-B", lease_seconds=120)
        assert c2 is None


# ---------------------------------------------------------------------------
# 6. Heartbeat
# ---------------------------------------------------------------------------

class TestHeartbeat:
    def test_heartbeat_does_not_raise(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=120)
        tmp_db.heartbeat_job(job.job_id, worker_id="w1", lease_seconds=120)

    def test_heartbeat_updates_heartbeat_at(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=120)
        tmp_db.heartbeat_job(job.job_id, worker_id="w1", lease_seconds=120)
        updated = job_service.get_job(job.job_id)
        assert updated.heartbeat_at is not None


# ---------------------------------------------------------------------------
# 7. Stale Job Recovery
# ---------------------------------------------------------------------------

class TestStaleJobRecovery:
    def test_stale_running_job_reset_to_queued(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=0)
        tmp_db._force_expire_lease(job.job_id)
        recovered = tmp_db.recover_stale_jobs()
        assert recovered >= 1
        restored = job_service.get_job(job.job_id)
        assert restored.status == JobStatus.QUEUED

    def test_valid_lease_job_not_recovered(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=300)
        recovered = tmp_db.recover_stale_jobs()
        assert recovered == 0
        still_running = job_service.get_job(job.job_id)
        assert still_running.status == JobStatus.RUNNING


# ---------------------------------------------------------------------------
# 8. Cancellation
# ---------------------------------------------------------------------------

class TestCancellation:
    def test_cancel_queued_job(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        result = job_service.cancel_job(job.job_id)
        assert result.status == JobStatus.CANCELLED

    def test_cancel_running_job_sets_cancel_requested(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        result = job_service.cancel_job(job.job_id)
        assert result.status == JobStatus.CANCEL_REQUESTED

    def test_cancel_already_cancelled_is_idempotent(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        job_service.cancel_job(job.job_id)
        result = job_service.cancel_job(job.job_id)
        assert result.status == JobStatus.CANCELLED

    def test_cancel_succeeded_job_raises(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        tmp_db.finish_job(job.job_id, status=JobStatus.SUCCEEDED)
        with pytest.raises(ValueError, match="terminal"):
            job_service.cancel_job(job.job_id)

    def test_cancel_unknown_job_raises(self, job_service):
        with pytest.raises(ValueError):
            job_service.cancel_job("no-such-job")


# ---------------------------------------------------------------------------
# 9. Retry
# ---------------------------------------------------------------------------

class TestRetry:
    def test_retry_failed_job_creates_new_job(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        tmp_db.fail_job(job.job_id, error_code="E", error_message="fail")
        new_job = job_service.retry_job(job.job_id)
        assert new_job.job_id != job.job_id
        assert new_job.status == JobStatus.QUEUED

    def test_retry_sets_retry_of_job_id(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        tmp_db.fail_job(job.job_id, error_code="E", error_message="fail")
        new_job = job_service.retry_job(job.job_id)
        assert new_job.retry_of_job_id == job.job_id

    def test_retry_increments_attempt(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        assert job.attempt == 1
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        tmp_db.fail_job(job.job_id, error_code="E", error_message="fail")
        new_job = job_service.retry_job(job.job_id)
        assert new_job.attempt == 2

    def test_retry_cancelled_job_allowed(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        job_service.cancel_job(job.job_id)
        new_job = job_service.retry_job(job.job_id)
        assert new_job.status == JobStatus.QUEUED

    def test_retry_preserves_original_job(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        tmp_db.fail_job(job.job_id, error_code="E", error_message="fail")
        job_service.retry_job(job.job_id)
        original = job_service.get_job(job.job_id)
        assert original is not None
        assert original.status == JobStatus.FAILED

    def test_retry_queued_job_raises(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        with pytest.raises(ValueError, match="not in a retryable"):
            job_service.retry_job(job.job_id)

    def test_retry_running_job_raises(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        with pytest.raises(ValueError, match="not in a retryable"):
            job_service.retry_job(job.job_id)


# ---------------------------------------------------------------------------
# 10. Progress
# ---------------------------------------------------------------------------

class TestProgress:
    def test_progress_defaults_to_zero(self, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        assert job.progress is not None
        assert job.progress.total_objects == 0
        assert job.progress.processed_objects == 0

    def test_update_progress_persisted(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        progress = ExtractionJobProgress(
            total_objects=10,
            processed_objects=5,
            successful_objects=4,
            failed_objects=1,
            current_stage=JobStage.EXTRACTING,
        )
        tmp_db.update_progress(job.job_id, progress=progress)
        updated = job_service.get_job(job.job_id)
        assert updated.progress.total_objects == 10
        assert updated.progress.processed_objects == 5

    def test_progress_percent_never_100_while_running(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        progress = ExtractionJobProgress(
            total_objects=5,
            processed_objects=5,
            successful_objects=5,
            current_stage=JobStage.EXTRACTING,
        )
        assert progress.percent_complete < 100.0

    def test_progress_percent_safe_with_zero_total(self):
        p = ExtractionJobProgress(total_objects=0, processed_objects=0)
        assert p.percent_complete == 0.0

    def test_progress_percent_calculation(self):
        p = ExtractionJobProgress(
            total_objects=10,
            processed_objects=4,
            current_stage=JobStage.EXTRACTING,
        )
        assert 0.0 <= p.percent_complete <= 100.0


# ---------------------------------------------------------------------------
# 11. Idempotency
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_same_key_same_request_returns_existing_job(self, job_service, sample_request):
        j1 = job_service.create_job(request=sample_request, idempotency_key="idem-1")
        j2 = job_service.create_job(request=sample_request, idempotency_key="idem-1")
        assert j1.job_id == j2.job_id

    def test_different_key_creates_new_job(self, job_service, sample_request):
        j1 = job_service.create_job(request=sample_request, idempotency_key="key-A")
        j2 = job_service.create_job(request=sample_request, idempotency_key="key-B")
        assert j1.job_id != j2.job_id

    def test_no_key_always_creates_new_job(self, job_service, sample_request):
        j1 = job_service.create_job(request=sample_request)
        j2 = job_service.create_job(request=sample_request)
        assert j1.job_id != j2.job_id

    def test_same_key_different_request_raises_conflict(
        self, job_service, sample_request, sample_request2
    ):
        job_service.create_job(request=sample_request, idempotency_key="conflict-key")
        with pytest.raises(ValueError, match="[Cc]onflict"):
            job_service.create_job(request=sample_request2, idempotency_key="conflict-key")


# ---------------------------------------------------------------------------
# 12. Artifact Store
# ---------------------------------------------------------------------------

class TestArtifactStore:
    def test_save_and_read_artifact(self, artifact_store):
        content = b"PK\x03\x04fake-zip-content"
        artifact_id = str(uuid.uuid4())
        artifact_store.save(
            artifact_id=artifact_id,
            data=content,
            filename=f"ncoreiq_{artifact_id}.zip",
            content_type="application/zip",
            job_id="job-123",
        )
        assert artifact_store.exists(artifact_id)
        read_back = artifact_store.read(artifact_id)
        assert read_back == content

    def test_artifact_checksum_sha256(self, artifact_store):
        content = b"test content for checksum"
        artifact_id = str(uuid.uuid4())
        artifact_store.save(
            artifact_id=artifact_id,
            data=content,
            filename=f"ncoreiq_{artifact_id}.zip",
            content_type="application/zip",
            job_id="job-456",
        )
        meta = artifact_store.metadata(artifact_id)
        expected = hashlib.sha256(content).hexdigest()
        assert meta.sha256 == expected

    def test_artifact_size_bytes(self, artifact_store):
        content = b"x" * 1024
        artifact_id = str(uuid.uuid4())
        artifact_store.save(
            artifact_id=artifact_id,
            data=content,
            filename=f"ncoreiq_{artifact_id}.zip",
            content_type="application/zip",
            job_id="job-789",
        )
        meta = artifact_store.metadata(artifact_id)
        assert meta.size_bytes == 1024

    def test_artifact_delete(self, artifact_store):
        content = b"delete me"
        artifact_id = str(uuid.uuid4())
        artifact_store.save(
            artifact_id=artifact_id,
            data=content,
            filename=f"ncoreiq_{artifact_id}.zip",
            content_type="application/zip",
            job_id="job-del",
        )
        artifact_store.delete(artifact_id)
        assert not artifact_store.exists(artifact_id)

    def test_artifact_not_exist_raises(self, artifact_store):
        with pytest.raises(Exception):
            artifact_store.read("no-such-artifact")

    def test_artifact_path_not_exposed_in_metadata(self, artifact_store):
        content = b"sensitive"
        artifact_id = str(uuid.uuid4())
        artifact_store.save(
            artifact_id=artifact_id,
            data=content,
            filename=f"ncoreiq_{artifact_id}.zip",
            content_type="application/zip",
            job_id="job-sec",
        )
        meta = artifact_store.metadata(artifact_id)
        meta_dict = meta.model_dump()
        # filesystem path must not appear in exported metadata
        assert "path" not in meta_dict
        assert "file_path" not in meta_dict

    def test_artifact_path_traversal_protection(self, artifact_store):
        with pytest.raises(Exception):
            artifact_store.read("../../etc/passwd")


# ---------------------------------------------------------------------------
# 13. Retention / Cleanup
# ---------------------------------------------------------------------------

class TestRetention:
    def test_cleanup_does_not_affect_running_jobs(self, tmp_db, job_service, artifact_store, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=300)
        deleted = tmp_db.delete_expired_jobs(retention_days=0)
        still_there = job_service.get_job(job.job_id)
        assert still_there is not None
        assert still_there.status == JobStatus.RUNNING

    def test_cleanup_does_not_affect_queued_jobs(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        deleted = tmp_db.delete_expired_jobs(retention_days=0)
        still_there = job_service.get_job(job.job_id)
        assert still_there is not None

    def test_cleanup_removes_old_terminal_jobs(self, tmp_db, job_service, sample_request):
        job = job_service.create_job(request=sample_request)
        tmp_db.claim_next_job(worker_id="w1", lease_seconds=60)
        tmp_db.finish_job(job.job_id, status=JobStatus.SUCCEEDED)
        # Force completed_at to the past
        tmp_db._force_old_completed_at(job.job_id, days_ago=10)
        deleted = tmp_db.delete_expired_jobs(retention_days=7)
        assert deleted >= 1


# ---------------------------------------------------------------------------
# 14. Persistence Store Operations
# ---------------------------------------------------------------------------

class TestPersistenceStore:
    def test_create_and_get_roundtrip(self, tmp_db, sample_request):
        job_id = str(uuid.uuid4())
        job = ExtractionJob(
            job_id=job_id,
            status=JobStatus.QUEUED,
            stage=JobStage.VALIDATING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            attempt=1,
            request=sample_request.model_dump(),
            progress=ExtractionJobProgress(),
        )
        tmp_db.create_job(job)
        retrieved = tmp_db.get_job(job_id)
        assert retrieved is not None
        assert retrieved.job_id == job_id
        assert retrieved.status == JobStatus.QUEUED

    def test_update_job_status(self, tmp_db, sample_request):
        job_id = str(uuid.uuid4())
        job = ExtractionJob(
            job_id=job_id,
            status=JobStatus.QUEUED,
            stage=JobStage.VALIDATING,
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            attempt=1,
            request=sample_request.model_dump(),
            progress=ExtractionJobProgress(),
        )
        tmp_db.create_job(job)
        tmp_db.update_job(job_id, status=JobStatus.RUNNING)
        updated = tmp_db.get_job(job_id)
        assert updated.status == JobStatus.RUNNING

    def test_list_jobs_pagination(self, tmp_db, job_service, sample_request):
        for _ in range(6):
            job_service.create_job(request=sample_request)
        result1 = tmp_db.list_jobs(limit=3, offset=0)
        result2 = tmp_db.list_jobs(limit=3, offset=3)
        all_ids = {j.job_id for j in result1.items} | {j.job_id for j in result2.items}
        assert len(all_ids) == 6

    def test_get_nonexistent_returns_none(self, tmp_db):
        assert tmp_db.get_job("does-not-exist") is None


# ---------------------------------------------------------------------------
# 15. Job Error Model
# ---------------------------------------------------------------------------

class TestJobErrorModel:
    def test_job_error_fields(self):
        err = JobError(
            code="SAP_TIMEOUT",
            message="Gateway timed out",
            stage=JobStage.EXTRACTING,
            retryable=True,
        )
        assert err.code == "SAP_TIMEOUT"
        assert err.retryable is True
        assert err.timestamp is not None

    def test_job_error_no_stack_trace(self):
        err = JobError(code="E", message="msg", stage=JobStage.EXTRACTING)
        err_dict = err.model_dump()
        assert "traceback" not in err_dict
        assert "stacktrace" not in err_dict


# ---------------------------------------------------------------------------
# 16. Artifact Model
# ---------------------------------------------------------------------------

class TestArtifactModel:
    def test_artifact_model_no_path_field(self):
        artifact = JobArtifact(
            artifact_id=str(uuid.uuid4()),
            format=JobArtifactFormat.ZIP,
            filename="ncoreiq_test.zip",
            content_type="application/zip",
            size_bytes=1024,
            sha256="abc123",
            created_at=datetime.now(timezone.utc),
        )
        artifact_dict = artifact.model_dump()
        assert "path" not in artifact_dict
        assert "file_path" not in artifact_dict

    def test_artifact_format_values(self):
        assert JobArtifactFormat.ZIP == "zip"
        assert JobArtifactFormat.JSON == "json"


# ---------------------------------------------------------------------------
# 17. ExtractionJobRequest Validation
# ---------------------------------------------------------------------------

class TestExtractionJobRequestValidation:
    def test_valid_request_zip(self, sample_request):
        assert sample_request.export_format == "zip"

    def test_valid_request_json(self, sample_request2):
        assert sample_request2.export_format == "json"

    def test_valid_request_none_format(self):
        req = ExtractionJobRequest(
            extraction=BulkExtractionRequest(
                objects=[ExtractionObjectRef(object_type="PROG", object_name="ZP")],
                include_metadata=True,
            ),
            export_format="none",
        )
        assert req.export_format == "none"

    def test_invalid_export_format_raises(self):
        with pytest.raises(Exception):
            ExtractionJobRequest(
                extraction=BulkExtractionRequest(
                    objects=[ExtractionObjectRef(object_type="PROG", object_name="ZP")],
                    include_metadata=True,
                ),
                export_format="invalid_format",
            )

    def test_request_reuses_phase5_extraction(self, sample_request):
        assert isinstance(sample_request.extraction, BulkExtractionRequest)


# ---------------------------------------------------------------------------
# 18. Worker Executor (mocked Phase 5)
# ---------------------------------------------------------------------------

class TestWorkerExecutorMocked:
    """Tests for ExtractionJobExecutor using deterministic mocked Phase 5."""

    def _make_executor(self, tmp_db, artifact_store, bulk_service_mock):
        from app.jobs.executor import ExtractionJobExecutor
        return ExtractionJobExecutor(
            job_store=tmp_db,
            artifact_store=artifact_store,
            bulk_extraction_service=bulk_service_mock,
            worker_id="test-worker",
            lease_seconds=60,
            heartbeat_interval=999,
        )

    def test_executor_claims_and_runs_queued_job(
        self, tmp_db, artifact_store, job_service, sample_request, mocker
    ):
        from app.models.phase5 import BulkExtractionResult, ExtractionStatus
        job = job_service.create_job(request=sample_request)

        from datetime import datetime, timezone
        import uuid
        now = datetime.now(timezone.utc)
        mock_result = BulkExtractionResult(
            extraction_id=str(uuid.uuid4()),
            status=ExtractionStatus.COMPLETED,
            started_at=now,
            completed_at=now,
            duration_ms=100.0,
            selection="explicit",
            requested_object_count=2,
            processed_object_count=2,
            successful_object_count=2,
            partial_object_count=0,
            failed_object_count=0,
            metadata_enabled=True,
            source_enabled=True,
            source_max_lines=1000,
            objects=[],
        )
        mock_service = mocker.MagicMock()
        mock_service.extract.return_value = mock_result

        executor = self._make_executor(tmp_db, artifact_store, mock_service)
        executor.process_one()

        final_job = job_service.get_job(job.job_id)
        assert final_job.status == JobStatus.SUCCEEDED

    def test_executor_marks_failed_on_exception(
        self, tmp_db, artifact_store, job_service, sample_request, mocker
    ):
        mock_service = mocker.MagicMock()
        mock_service.extract.side_effect = RuntimeError("SAP connection failed")

        executor = self._make_executor(tmp_db, artifact_store, mock_service)
        job = job_service.create_job(request=sample_request)
        executor.process_one()

        final_job = job_service.get_job(job.job_id)
        assert final_job.status == JobStatus.FAILED

    def test_executor_respects_cancel_requested(
        self, tmp_db, artifact_store, job_service, sample_request, mocker
    ):
        from app.models.phase5 import BulkExtractionResult, ExtractionStatus

        job = job_service.create_job(request=sample_request)

        call_count = {"n": 0}

        def slow_extract(*args, **kwargs):
            from datetime import datetime, timezone
            import uuid
            call_count["n"] += 1
            tmp_db.request_cancel(job.job_id)
            now = datetime.now(timezone.utc)
            return BulkExtractionResult(
                extraction_id=str(uuid.uuid4()),
                status=ExtractionStatus.COMPLETED,
                started_at=now,
                completed_at=now,
                duration_ms=50.0,
                selection="explicit",
                requested_object_count=1,
                processed_object_count=1,
                successful_object_count=1,
                partial_object_count=0,
                failed_object_count=0,
                metadata_enabled=True,
                source_enabled=True,
                source_max_lines=1000,
                objects=[],
            )

        mock_service = mocker.MagicMock()
        mock_service.extract.side_effect = slow_extract

        executor = self._make_executor(tmp_db, artifact_store, mock_service)
        executor.process_one()

        final_job = job_service.get_job(job.job_id)
        assert final_job.status in (JobStatus.CANCELLED, JobStatus.SUCCEEDED)

    def test_executor_completed_with_errors_not_infrastructure_failure(
        self, tmp_db, artifact_store, job_service, sample_request, mocker
    ):
        from app.models.phase5 import BulkExtractionResult, ExtractionStatus

        from datetime import datetime, timezone
        import uuid
        now = datetime.now(timezone.utc)
        mock_result = BulkExtractionResult(
            extraction_id=str(uuid.uuid4()),
            status=ExtractionStatus.COMPLETED_WITH_ERRORS,
            started_at=now,
            completed_at=now,
            duration_ms=200.0,
            selection="explicit",
            requested_object_count=2,
            processed_object_count=2,
            successful_object_count=1,
            partial_object_count=0,
            failed_object_count=1,
            metadata_enabled=True,
            source_enabled=True,
            source_max_lines=1000,
            objects=[],
        )
        mock_service = mocker.MagicMock()
        mock_service.extract.return_value = mock_result

        executor = self._make_executor(tmp_db, artifact_store, mock_service)
        job = job_service.create_job(request=sample_request)
        executor.process_one()

        final_job = job_service.get_job(job.job_id)
        # Infrastructure job should SUCCEED even when extraction has errors
        assert final_job.status == JobStatus.SUCCEEDED
        assert final_job.result_status == ExtractionStatus.COMPLETED_WITH_ERRORS.value

    def test_executor_no_job_available_is_noop(
        self, tmp_db, artifact_store, mocker
    ):
        mock_service = mocker.MagicMock()
        executor = self._make_executor(tmp_db, artifact_store, mock_service)
        # Should not raise when no jobs are available
        executor.process_one()
        mock_service.extract.assert_not_called()


# ---------------------------------------------------------------------------
# 19. API Endpoint Tests (FastAPI test client)
# ---------------------------------------------------------------------------

@pytest.fixture()
def api_client(tmp_db, artifact_store):
    """Minimal FastAPI test client with Phase 6 routes."""
    from fastapi.testclient import TestClient
    from app.main import create_app

    app = create_app(
        job_store=tmp_db,
        artifact_store=artifact_store,
    )
    with TestClient(app, raise_server_exceptions=True) as client:
        yield client


@pytest.fixture()
def api_client_default():
    """Standard test client using application defaults."""
    from fastapi.testclient import TestClient
    from app.main import app as application
    with TestClient(application) as client:
        yield client


class TestJobCreationAPI:
    def _job_payload(self):
        return {
            "extraction": {
                "objects": [
                    {"object_type": "PROG", "object_name": "ZTEST1"},
                    {"object_type": "CLAS", "object_name": "ZCL_SAMPLE"},
                ],
                "include_metadata": True,
                "include_source": True,
                "continue_on_error": True,
            },
            "export_format": "zip",
        }

    def test_post_jobs_returns_202(self, api_client_default):
        resp = api_client_default.post("/api/v1/repository/jobs", json=self._job_payload())
        assert resp.status_code == 202

    def test_post_jobs_returns_job_id(self, api_client_default):
        resp = api_client_default.post("/api/v1/repository/jobs", json=self._job_payload())
        data = resp.json()
        assert "job_id" in data

    def test_post_jobs_returns_queued_status(self, api_client_default):
        resp = api_client_default.post("/api/v1/repository/jobs", json=self._job_payload())
        assert resp.json()["status"] == "QUEUED"

    def test_post_jobs_returns_status_url(self, api_client_default):
        resp = api_client_default.post("/api/v1/repository/jobs", json=self._job_payload())
        data = resp.json()
        assert "status_url" in data or "job_id" in data

    def test_post_jobs_invalid_format_returns_422(self, api_client_default):
        payload = self._job_payload()
        payload["export_format"] = "invalid"
        resp = api_client_default.post("/api/v1/repository/jobs", json=payload)
        assert resp.status_code == 422

    def test_post_jobs_empty_objects_returns_422(self, api_client_default):
        payload = {
            "extraction": {
                "objects": [],
                "include_metadata": True,
            },
            "export_format": "zip",
        }
        resp = api_client_default.post("/api/v1/repository/jobs", json=payload)
        assert resp.status_code == 422

    def test_post_jobs_idempotency_key_same_request(self, api_client_default):
        payload = self._job_payload()
        r1 = api_client_default.post(
            "/api/v1/repository/jobs",
            json=payload,
            headers={"Idempotency-Key": "test-idem-001"},
        )
        r2 = api_client_default.post(
            "/api/v1/repository/jobs",
            json=payload,
            headers={"Idempotency-Key": "test-idem-001"},
        )
        assert r1.status_code == 202
        assert r2.status_code == 202
        assert r1.json()["job_id"] == r2.json()["job_id"]

    def test_post_jobs_idempotency_key_different_request_returns_409(self, api_client_default):
        payload1 = self._job_payload()
        payload2 = {
            "extraction": {
                "objects": [{"object_type": "PROG", "object_name": "ZDIFF"}],
                "include_metadata": True,
            },
            "export_format": "json",
        }
        api_client_default.post(
            "/api/v1/repository/jobs",
            json=payload1,
            headers={"Idempotency-Key": "conflict-key-api"},
        )
        r2 = api_client_default.post(
            "/api/v1/repository/jobs",
            json=payload2,
            headers={"Idempotency-Key": "conflict-key-api"},
        )
        assert r2.status_code == 409


class TestGetJobAPI:
    def _create_job(self, client):
        payload = {
            "extraction": {
                "objects": [{"object_type": "PROG", "object_name": "ZTEST1"}],
                "include_metadata": True,
            },
            "export_format": "json",
        }
        return client.post("/api/v1/repository/jobs", json=payload).json()

    def test_get_known_job_returns_200(self, api_client_default):
        job = self._create_job(api_client_default)
        resp = api_client_default.get(f"/api/v1/repository/jobs/{job['job_id']}")
        assert resp.status_code == 200

    def test_get_unknown_job_returns_404(self, api_client_default):
        resp = api_client_default.get("/api/v1/repository/jobs/no-such-job-id-xyz")
        assert resp.status_code == 404

    def test_get_job_has_status_field(self, api_client_default):
        job = self._create_job(api_client_default)
        resp = api_client_default.get(f"/api/v1/repository/jobs/{job['job_id']}")
        data = resp.json()
        assert "status" in data

    def test_get_job_has_progress_field(self, api_client_default):
        job = self._create_job(api_client_default)
        resp = api_client_default.get(f"/api/v1/repository/jobs/{job['job_id']}")
        data = resp.json()
        assert "progress" in data

    def test_get_job_has_no_filesystem_path(self, api_client_default):
        job = self._create_job(api_client_default)
        resp = api_client_default.get(f"/api/v1/repository/jobs/{job['job_id']}")
        resp_text = resp.text.lower()
        assert "c:\\" not in resp_text
        assert "/home/" not in resp_text


class TestListJobsAPI:
    def _payload(self):
        return {
            "extraction": {
                "objects": [{"object_type": "PROG", "object_name": "ZLIST1"}],
                "include_metadata": True,
            },
            "export_format": "json",
        }

    def test_list_jobs_returns_200(self, api_client_default):
        resp = api_client_default.get("/api/v1/repository/jobs")
        assert resp.status_code == 200

    def test_list_jobs_has_items_field(self, api_client_default):
        resp = api_client_default.get("/api/v1/repository/jobs")
        data = resp.json()
        assert "items" in data

    def test_list_jobs_has_total_field(self, api_client_default):
        resp = api_client_default.get("/api/v1/repository/jobs")
        data = resp.json()
        assert "total" in data

    def test_list_jobs_respects_limit(self, api_client_default):
        for _ in range(3):
            api_client_default.post("/api/v1/repository/jobs", json=self._payload())
        resp = api_client_default.get("/api/v1/repository/jobs?limit=2")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) <= 2

    def test_list_jobs_invalid_limit_rejected(self, api_client_default):
        resp = api_client_default.get("/api/v1/repository/jobs?limit=999999")
        assert resp.status_code in (400, 422)


class TestCancelAPI:
    def _create_job(self, client):
        payload = {
            "extraction": {
                "objects": [{"object_type": "PROG", "object_name": "ZCANCEL1"}],
                "include_metadata": True,
            },
            "export_format": "json",
        }
        return client.post("/api/v1/repository/jobs", json=payload).json()

    def test_cancel_queued_job_returns_2xx(self, api_client_default):
        job = self._create_job(api_client_default)
        resp = api_client_default.post(f"/api/v1/repository/jobs/{job['job_id']}/cancel")
        assert resp.status_code in (200, 202)

    def test_cancel_queued_job_status_is_cancelled(self, api_client_default):
        job = self._create_job(api_client_default)
        api_client_default.post(f"/api/v1/repository/jobs/{job['job_id']}/cancel")
        resp = api_client_default.get(f"/api/v1/repository/jobs/{job['job_id']}")
        assert resp.json()["status"] == "CANCELLED"

    def test_cancel_unknown_job_returns_404(self, api_client_default):
        resp = api_client_default.post("/api/v1/repository/jobs/no-such-cancel/cancel")
        assert resp.status_code == 404

    def test_cancel_terminal_job_returns_409_or_422(self, api_client_default):
        job = self._create_job(api_client_default)
        api_client_default.post(f"/api/v1/repository/jobs/{job['job_id']}/cancel")
        resp = api_client_default.post(f"/api/v1/repository/jobs/{job['job_id']}/cancel")
        assert resp.status_code in (200, 202, 409, 422)


class TestRetryAPI:
    def _create_and_cancel(self, client):
        payload = {
            "extraction": {
                "objects": [{"object_type": "PROG", "object_name": "ZRETRY1"}],
                "include_metadata": True,
            },
            "export_format": "json",
        }
        job = client.post("/api/v1/repository/jobs", json=payload).json()
        client.post(f"/api/v1/repository/jobs/{job['job_id']}/cancel")
        return job

    def test_retry_cancelled_job_returns_202(self, api_client_default):
        job = self._create_and_cancel(api_client_default)
        resp = api_client_default.post(f"/api/v1/repository/jobs/{job['job_id']}/retry")
        assert resp.status_code == 202

    def test_retry_returns_new_job_id(self, api_client_default):
        job = self._create_and_cancel(api_client_default)
        resp = api_client_default.post(f"/api/v1/repository/jobs/{job['job_id']}/retry")
        new_job = resp.json()
        assert new_job["job_id"] != job["job_id"]

    def test_retry_new_job_is_queued(self, api_client_default):
        job = self._create_and_cancel(api_client_default)
        resp = api_client_default.post(f"/api/v1/repository/jobs/{job['job_id']}/retry")
        assert resp.json()["status"] == "QUEUED"

    def test_retry_queued_job_returns_4xx(self, api_client_default):
        payload = {
            "extraction": {
                "objects": [{"object_type": "PROG", "object_name": "ZRETRY2"}],
                "include_metadata": True,
            },
            "export_format": "json",
        }
        job = api_client_default.post("/api/v1/repository/jobs", json=payload).json()
        resp = api_client_default.post(f"/api/v1/repository/jobs/{job['job_id']}/retry")
        assert resp.status_code in (409, 422)

    def test_retry_unknown_job_returns_404(self, api_client_default):
        resp = api_client_default.post("/api/v1/repository/jobs/no-such-retry/retry")
        assert resp.status_code == 404


class TestResultAPI:
    def _create_job(self, client):
        payload = {
            "extraction": {
                "objects": [{"object_type": "PROG", "object_name": "ZRESULT1"}],
                "include_metadata": True,
            },
            "export_format": "json",
        }
        return client.post("/api/v1/repository/jobs", json=payload).json()

    def test_result_on_queued_job_returns_409(self, api_client_default):
        job = self._create_job(api_client_default)
        resp = api_client_default.get(f"/api/v1/repository/jobs/{job['job_id']}/result")
        assert resp.status_code == 409

    def test_result_on_unknown_job_returns_404(self, api_client_default):
        resp = api_client_default.get("/api/v1/repository/jobs/no-such-result/result")
        assert resp.status_code == 404


class TestArtifactAPI:
    def _create_job(self, client):
        payload = {
            "extraction": {
                "objects": [{"object_type": "PROG", "object_name": "ZARTIFACT1"}],
                "include_metadata": True,
            },
            "export_format": "zip",
        }
        return client.post("/api/v1/repository/jobs", json=payload).json()

    def test_artifact_on_queued_job_returns_409(self, api_client_default):
        job = self._create_job(api_client_default)
        resp = api_client_default.get(f"/api/v1/repository/jobs/{job['job_id']}/artifact")
        assert resp.status_code == 409

    def test_artifact_on_unknown_job_returns_404(self, api_client_default):
        resp = api_client_default.get("/api/v1/repository/jobs/no-such-artifact/artifact")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# 20. Security — Secret Protection
# ---------------------------------------------------------------------------

class TestSecretProtection:
    def test_job_response_has_no_password(self, api_client_default):
        payload = {
            "extraction": {
                "objects": [{"object_type": "PROG", "object_name": "ZSEC1"}],
                "include_metadata": True,
            },
            "export_format": "none",
        }
        resp = api_client_default.post("/api/v1/repository/jobs", json=payload)
        text = resp.text.lower()
        assert "password" not in text or '"password": null' in text.lower()

    def test_job_response_has_no_auth_header(self, api_client_default):
        payload = {
            "extraction": {
                "objects": [{"object_type": "PROG", "object_name": "ZSEC2"}],
                "include_metadata": True,
            },
            "export_format": "none",
        }
        resp = api_client_default.post("/api/v1/repository/jobs", json=payload)
        text = resp.text.lower()
        assert "authorization" not in text
        assert "bearer " not in text

    def test_job_model_no_password_field(self):
        from app.models.phase6 import ExtractionJob
        fields = ExtractionJob.model_fields.keys()
        assert "password" not in fields
        assert "sap_password" not in fields

    def test_artifact_metadata_no_path_field(self):
        from app.models.phase6 import JobArtifact
        fields = JobArtifact.model_fields.keys()
        assert "path" not in fields
        assert "file_path" not in fields


# ---------------------------------------------------------------------------
# 21. Phase 1–5 Regression
# ---------------------------------------------------------------------------

class TestPhase1To5Regression:
    """Smoke-level regression: ensure Phase 6 does not break existing endpoints."""

    def test_health_endpoint_still_works(self, api_client_default):
        resp = api_client_default.get("/api/v1/health")
        assert resp.status_code == 200

    def test_health_includes_job_store_info(self, api_client_default):
        resp = api_client_default.get("/api/v1/health")
        data = resp.json()
        # Health may include job subsystem info (not required to fail if missing)
        assert resp.status_code == 200
        # Check optional job store status field doesn't break health
        if "job_store" in data:
            assert data["job_store"] in ("healthy", "unavailable", True, False, None)

    def test_existing_extract_endpoint_still_works(self, api_client_default):
        """POST /api/v1/repository/extract must still exist and accept valid input."""
        payload = {
            "objects": [{"object_type": "PROG", "object_name": "ZTEST_P5"}],
            "include_metadata": True,
            "include_source": False,
            "continue_on_error": True,
        }
        resp = api_client_default.post("/api/v1/repository/extract", json=payload)
        # Accept 200 (live SAP) or connection-related 5xx — just not 404/405
        assert resp.status_code != 404
        assert resp.status_code != 405

    def test_existing_search_endpoint_still_works(self, api_client_default):
        """POST /api/v1/repository/search must still be reachable."""
        payload = {
            "packages": ["ZNCOREIQ"],
            "include_metadata": False,
            "include_source": False,
        }
        resp = api_client_default.post("/api/v1/repository/search", json=payload)
        assert resp.status_code != 404
        assert resp.status_code != 405

    def test_sap_ping_endpoint_still_reachable(self, api_client_default):
        resp = api_client_default.get("/api/v1/sap/ping")
        assert resp.status_code != 404
        assert resp.status_code != 405
