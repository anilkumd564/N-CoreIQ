"""Diagnostic script to capture the 422 response body for the idempotency test."""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

payload = {
    "extraction": {
        "objects": [{"object_type": "PROG", "object_name": "ZTEST1"}],
        "include_metadata": True,
        "include_source": True,
        "continue_on_error": True,
    },
    "export_format": "zip",
}

try:
    from fastapi.testclient import TestClient
    from app.main import app

    print("App imported successfully")
    with TestClient(app, raise_server_exceptions=False) as client:
        # First try WITHOUT header
        r0 = client.post("/api/v1/repository/jobs", json=payload)
        print(f"WITHOUT header: {r0.status_code}")

        # Then try WITH header
        r1 = client.post(
            "/api/v1/repository/jobs",
            json=payload,
            headers={"Idempotency-Key": "diag-idem-001"},
        )
        print(f"WITH header: {r1.status_code}")
        print(f"BODY: {r1.text[:2000]}")

except Exception as e:
    import traceback
    print(f"ERROR: {e}")
    traceback.print_exc()
