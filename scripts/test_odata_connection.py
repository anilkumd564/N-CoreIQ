"""Development-only standalone OData connectivity test.

Loads configuration through the N-CoreIQ settings mechanism, calls the
ZNCOREIQ_REPO_SRV / SystemInfoSet entity set, and prints a sanitised
summary.  Never prints passwords, Authorization headers, or full environment
dumps.

Usage (from the project root, with .env populated):

    python scripts/test_odata_connection.py

Prerequisites:
    pip install httpx python-dotenv pydantic-settings
"""

from __future__ import annotations

import asyncio
import sys

# Ensure project root is on the path when run directly.
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.core.config import get_settings
from app.sap.connection.factory import build_sap_connection_provider
from app.sap.exceptions import (
    SAPAuthenticationError,
    SAPCommunicationError,
    SAPConfigurationError,
    SAPConnectionError,
    SAPFunctionExecutionError,
)


async def main() -> None:
    settings = get_settings()

    print("=" * 60)
    print("N-CoreIQ — OData Connectivity Test")
    print("=" * 60)
    print(f"  Connection mode : {settings.sap_connection_mode}")

    if settings.sap_connection_mode != "odata":
        print(
            f"\n  [SKIP] SAP_CONNECTION_MODE={settings.sap_connection_mode!r}. "
            "Set SAP_CONNECTION_MODE=odata to run this script."
        )
        sys.exit(0)

    if settings.sap_odata:
        print(f"  OData base URL  : {settings.sap_odata.base_url}")
        print(f"  OData service   : {settings.sap_odata.service}")
        print(f"  SAP client      : {settings.sap_odata.client}")
        print(f"  Timeout         : {settings.sap_odata.timeout}s")
        # Intentionally NOT printing user or password.
    else:
        print("\n  [FAIL] SAP_ODATA settings block is missing.")
        sys.exit(1)

    print("\n  Calling SystemInfoSet …")

    provider = build_sap_connection_provider(settings)

    try:
        result = await provider.ping()
    except SAPConfigurationError as exc:
        print(f"\n  [FAIL] Configuration error: {exc}")
        sys.exit(1)
    except SAPAuthenticationError as exc:
        print(f"\n  [FAIL] Authentication error: {exc}")
        sys.exit(1)
    except SAPConnectionError as exc:
        print(f"\n  [FAIL] Connection error: {exc}")
        sys.exit(1)
    except SAPFunctionExecutionError as exc:
        print(f"\n  [FAIL] SAP execution error: {exc}")
        sys.exit(1)
    except SAPCommunicationError as exc:
        print(f"\n  [FAIL] Communication error: {exc}")
        sys.exit(1)

    print()
    print("=" * 60)
    print("  RESULT")
    print("=" * 60)
    print(f"  Status          : {result.status}")
    print(f"  Connection mode : {result.connection_mode}")
    print(f"  System ID       : {result.system_id}")
    print(f"  Client          : {result.client}")
    print(f"  SAP release     : {result.sap_release}")
    print(f"  Ping method     : {result.ping_method}")
    print(f"  Timestamp       : {result.timestamp}")
    print(f"  Message         : {result.message}")
    print("=" * 60)

    if result.status == "connected":
        print("\n  [PASS] SAP OData connectivity verified.\n")
    else:
        print(f"\n  [FAIL] SAP returned status={result.status!r}\n")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
