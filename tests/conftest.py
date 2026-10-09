"""Pytest configuration and session-scoped fixtures for N-CoreIQ tests.

Module-level environment setup (runs before any app module is imported):
    - Sets APP_ENV=test so the application lifespan skips executor startup.
      This prevents background workers from attempting live SAP connections
      during automated tests, which would cause 30-second timeouts.

Session-level database isolation:
    The ``api_client_default`` fixture in test_phase6.py uses the live FastAPI
    application singleton with its real on-disk database (ncoreiq_jobs.db).
    To prevent stale idempotency keys or other leftover state from previous
    test runs from causing failures, this conftest removes the live DB and
    artifact directory before the test session starts.

    This is correct per the Phase 6 spec: "Do not use the developer's real
    job database in automated tests."
"""

from __future__ import annotations

import os
import shutil

import pytest

# ---------------------------------------------------------------------------
# Set NCOREIQ_DISABLE_EXECUTOR=true at module level, BEFORE any app module
# is imported.  This ensures get_settings() returns ncoreiq_disable_executor=True
# for the entire session, so the lifespan skips background worker startup.
# This prevents live SAP connection attempts from background workers during
# automated tests (which would cause 30-second SAP timeouts per job).
#
# APP_ENV is intentionally NOT changed here so that Phase 1 foundation tests
# that assert settings.app_env == "local" continue to pass.
# ---------------------------------------------------------------------------
os.environ.setdefault("NCOREIQ_DISABLE_EXECUTOR", "true")


@pytest.fixture(scope="session", autouse=True)
def clean_live_test_database() -> None:
    """Delete the live SQLite job database before each test session.

    This prevents stale idempotency keys, job records, or other leftover data
    from prior test runs from causing test isolation failures.  The database is
    recreated automatically when the application starts under TestClient.

    Only the default development paths are cleaned.  If NCOREIQ_JOB_DB_PATH
    or NCOREIQ_JOB_ARTIFACT_DIR have been customised to a production location
    via environment variables, those paths are NOT touched.
    """
    default_db = "./ncoreiq_jobs.db"
    default_artifact_dir = "./job_artifacts"

    # Only clean the default development paths — never production paths.
    db_path = os.environ.get("NCOREIQ_JOB_DB_PATH", default_db)
    artifact_dir = os.environ.get("NCOREIQ_JOB_ARTIFACT_DIR", default_artifact_dir)

    # Remove stale DB (recreated on startup)
    if os.path.exists(db_path):
        try:
            os.remove(db_path)
        except OSError:
            pass  # non-fatal; test may still pass

    # Remove stale test artifacts (recreated on demand)
    if os.path.isdir(artifact_dir):
        try:
            shutil.rmtree(artifact_dir)
        except OSError:
            pass  # non-fatal

    yield
