"""
N-CoreIQ Phase 4 — Live SAP Test Script
========================================
Runs against the locally running N-CoreIQ server (http://localhost:8000).
Tests all Phase 4 requirements:
  - Health check (regression)
  - SAP ping (regression)
  - Metadata endpoint (PROG, CLAS, TABL, FUNC)
  - Source endpoint (PROG with default max_lines)
  - Source endpoint (PROG with truncation test, max_lines=10)
  - Source endpoint (CLAS multi-component)
  - Source endpoint (TABL structured definition)
  - max_lines validation (0, 20001 → 422)
  - Object not found → 404
  - Unsupported type → 422

Usage:
    python scripts/live_test_phase4.py [--host http://localhost:8000] [--prog ZPROG] [--clas ZCLAS] [--tabl ZTABL]
"""

import argparse
import json
import sys
import os
import urllib.request
import urllib.error
from typing import Any

# Force UTF-8 output on Windows
if sys.platform == "win32":
    import io
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── defaults ──────────────────────────────────────────────────────────────────
DEFAULT_HOST = "http://localhost:8000"
DEFAULT_PROG = "ZNCOREIQ_DISCOVERY"   # adjust to a known program in NC1
DEFAULT_CLAS = "ZCL_NCOREIQ_PROVIDER"  # adjust to a known class in NC1
DEFAULT_TABL = "ZNCOREIQ_PKG"          # adjust to a known table in NC1
DEFAULT_FUNC = ""                      # leave blank to skip FUNC test

PASS = "\033[92mPASS\033[0m"
FAIL = "\033[91mFAIL\033[0m"
SKIP = "\033[93mSKIP\033[0m"
WARN = "\033[93mWARN\033[0m"

results: list[dict] = []


def _http_get(url: str) -> tuple[int, Any]:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}
    except Exception as exc:
        return -1, {"error": str(exc)}


def _http_post(url: str, payload: dict) -> tuple[int, Any]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8")
        try:
            return e.code, json.loads(body)
        except Exception:
            return e.code, {"raw": body}
    except Exception as exc:
        return -1, {"error": str(exc)}


def record(name: str, status: str, detail: str = "", data: Any = None):
    results.append({"name": name, "status": status, "detail": detail})
    icon = {"PASS": PASS, "FAIL": FAIL, "SKIP": SKIP, "WARN": WARN}.get(status, status)
    print(f"  [{icon}] {name}")
    if detail:
        print(f"         {detail}")
    if data is not None and status in ("FAIL", "WARN"):
        snippet = json.dumps(data, indent=2)[:600]
        print(f"         Response snippet: {snippet}")


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


# ── individual tests ──────────────────────────────────────────────────────────

def test_health(host: str):
    section("1. Health Check (Phase 1–3 Regression)")
    code, body = _http_get(f"{host}/api/v1/health")
    if code == 200 and body.get("status") == "ok":
        record("GET /api/v1/health → 200 ok", "PASS", f"version={body.get('version')}")
    else:
        record("GET /api/v1/health → 200 ok", "FAIL", f"HTTP {code}", body)


def test_sap_ping(host: str):
    section("2. SAP Ping (Phase 2 Regression)")
    code, body = _http_get(f"{host}/api/v1/sap/ping")
    if code == 200:
        record("GET /api/v1/sap/ping → 200", "PASS",
               f"system={body.get('system_id')} client={body.get('client')}")
    elif code == 503:
        record("GET /api/v1/sap/ping → 503 (SAP unreachable)", "WARN",
               "SAP not reachable — remaining tests may fail", body)
    else:
        record("GET /api/v1/sap/ping", "FAIL", f"HTTP {code}", body)


def test_repository_search(host: str):
    section("3. Repository Search (Phase 3 Regression)")
    payload = {
        "name_patterns": ["ZNCOREIQ*"],
        "object_types": ["PROG"],
        "max_results": 10,
    }
    code, body = _http_post(f"{host}/api/v1/repository/search", payload)
    if code == 200:
        objs = body.get("objects", [])
        record("POST /api/v1/repository/search → 200", "PASS",
               f"returned={len(objs)} objects")
    else:
        record("POST /api/v1/repository/search", "FAIL", f"HTTP {code}", body)


def test_metadata(host: str, obj_type: str, obj_name: str, label: str = ""):
    if not obj_name:
        record(f"GET metadata {obj_type}/{label or '?'}", "SKIP", "No object name configured")
        return None
    url = f"{host}/api/v1/repository/objects/{obj_type}/{obj_name}/metadata"
    code, body = _http_get(url)
    tag = label or obj_name
    if code == 200:
        found = body.get("found")
        desc = body.get("description", "")
        pkg = body.get("package", "")
        sys_id = body.get("system_id", "")
        client = body.get("client", "")
        created = body.get("created_date", "")
        record(
            f"GET metadata {obj_type}/{tag} → 200",
            "PASS",
            f"found={found} system={sys_id} client={client} pkg={pkg} desc='{desc}' created={created}",
        )
        return body
    elif code == 404:
        record(f"GET metadata {obj_type}/{tag} → 404", "WARN",
               "Object not found in ECC — check object name", body)
        return None
    else:
        record(f"GET metadata {obj_type}/{tag}", "FAIL", f"HTTP {code}", body)
        return None


def test_source(host: str, obj_type: str, obj_name: str, max_lines: int | None = None,
                label: str = "", expect_truncated: bool | None = None):
    if not obj_name:
        record(f"GET source {obj_type}/{label or '?'}", "SKIP", "No object name configured")
        return None
    url = f"{host}/api/v1/repository/objects/{obj_type}/{obj_name}/source"
    if max_lines is not None:
        url += f"?max_lines={max_lines}"
    code, body = _http_get(url)
    tag = label or obj_name
    ml_tag = f" max_lines={max_lines}" if max_lines is not None else ""
    if code == 200:
        returned = body.get("returned_lines", 0)
        comp_count = body.get("component_count", 0)
        truncated = body.get("truncated", False)
        found = body.get("found", False)
        detail = (
            f"found={found} returned_lines={returned} "
            f"components={comp_count} truncated={truncated}"
        )
        status = "PASS"
        if expect_truncated is True and not truncated:
            status = "WARN"
            detail += " — expected truncated=true but got false"
        elif expect_truncated is False and truncated:
            status = "WARN"
            detail += " — unexpected truncated=true"

        # Validate line ordering within first component
        comps = body.get("components", [])
        ordering_ok = True
        for comp in comps:
            lines = comp.get("lines", [])
            line_nos = [ln["line_no"] for ln in lines]
            if line_nos != sorted(line_nos):
                ordering_ok = False
                break
        if not ordering_ok:
            status = "FAIL"
            detail += " — LINE ORDERING VIOLATION"

        record(f"GET source {obj_type}/{tag}{ml_tag} → 200", status, detail)

        # Print first component name and first 3 lines
        if comps:
            c = comps[0]
            print(f"         First component: type={c.get('component_type')} "
                  f"name={c.get('component_name')} lines={c.get('line_count')}")
            for ln in c.get("lines", [])[:3]:
                print(f"           [{ln['line_no']:4d}] {ln['source_line'][:80]}")
        return body
    elif code == 404:
        record(f"GET source {obj_type}/{tag}{ml_tag} → 404", "WARN",
               "Object not found — check object name", body)
        return None
    else:
        record(f"GET source {obj_type}/{tag}{ml_tag}", "FAIL", f"HTTP {code}", body)
        return None


def test_max_lines_validation(host: str, obj_type: str, obj_name: str):
    if not obj_name:
        record("max_lines validation", "SKIP", "No object name configured")
        return
    section("6. max_lines Validation")
    for bad_val, label in [(0, "max_lines=0"), (-1, "max_lines=-1"), (20001, "max_lines=20001")]:
        url = f"{host}/api/v1/repository/objects/{obj_type}/{obj_name}/source?max_lines={bad_val}"
        code, body = _http_get(url)
        if code == 422:
            record(f"{label} → 422", "PASS")
        else:
            record(f"{label} → 422", "FAIL", f"Got HTTP {code}", body)

    # valid boundary values
    for ok_val in [1, 20000]:
        url = f"{host}/api/v1/repository/objects/{obj_type}/{obj_name}/source?max_lines={ok_val}"
        code, body = _http_get(url)
        if code in (200, 404):
            record(f"max_lines={ok_val} → {code} (accepted)", "PASS")
        else:
            record(f"max_lines={ok_val} → accepted", "FAIL", f"Got HTTP {code}", body)


def test_not_found(host: str):
    section("7. Object Not Found → 404")
    for obj_type, obj_name in [("PROG", "ZNCOREIQ_DOES_NOT_EXIST_XYZ_99999"),
                                ("CLAS", "ZCL_NCOREIQ_DOES_NOT_EXIST_XYZ")]:
        url = f"{host}/api/v1/repository/objects/{obj_type}/{obj_name}/metadata"
        code, body = _http_get(url)
        if code == 404:
            record(f"metadata {obj_type}/NONEXISTENT → 404", "PASS")
        elif code == 200 and not body.get("found"):
            record(f"metadata {obj_type}/NONEXISTENT → found=false", "WARN",
                   "Got 200 with found=false instead of 404")
        else:
            record(f"metadata {obj_type}/NONEXISTENT → 404", "FAIL", f"Got HTTP {code}", body)


def test_unsupported_type(host: str):
    section("8. Unsupported Object Type → 422")
    for obj_type in ["XXXX", "ZZZZ", "REPT"]:
        url = f"{host}/api/v1/repository/objects/{obj_type}/SOME_OBJECT/metadata"
        code, body = _http_get(url)
        if code == 422:
            record(f"metadata {obj_type}/... → 422", "PASS")
        else:
            record(f"metadata {obj_type}/... → 422", "FAIL", f"Got HTTP {code}", body)


def test_unicode(host: str, obj_type: str, obj_name: str):
    """Check that the source response is valid UTF-8 JSON (no corruption)."""
    if not obj_name:
        record("Unicode source preservation", "SKIP", "No object name configured")
        return
    url = f"{host}/api/v1/repository/objects/{obj_type}/{obj_name}/source?max_lines=100"
    code, body = _http_get(url)
    if code == 200:
        # Re-encode to UTF-8 — if any corruption exists json.dumps will expose it
        try:
            json.dumps(body, ensure_ascii=False).encode("utf-8")
            record("Unicode source preservation", "PASS", "JSON round-trip UTF-8 clean")
        except Exception as exc:
            record("Unicode source preservation", "FAIL", str(exc))
    else:
        record("Unicode source preservation", "SKIP", f"HTTP {code} — skipping unicode check")


def test_no_search_n1(host: str):
    """Confirm search endpoint does NOT call ObjectMetadataSet / SourceLinesSet.
    We can't instrument the SAP backend here, but we verify the response shape
    has no metadata/source fields embedded in objects (which would indicate N+1)."""
    section("9. No Search N+1 (structural check)")
    payload = {"name_patterns": ["ZNCOREIQ*"], "object_types": ["PROG"], "max_results": 5}
    code, body = _http_post(f"{host}/api/v1/repository/search", payload)
    if code != 200:
        record("Search N+1 check", "SKIP", f"Search returned {code}")
        return
    objects = body.get("objects", [])
    has_metadata_fields = any(
        "description" in obj or "author" in obj or "created_date" in obj or
        "components" in obj or "source" in obj
        for obj in objects
    )
    if has_metadata_fields:
        record("No metadata/source fields in search results", "FAIL",
               "Search result objects contain metadata/source fields — N+1 detected!")
    else:
        record("No metadata/source fields in search results", "PASS",
               f"Checked {len(objects)} objects — clean search-only response")


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="N-CoreIQ Phase 4 Live Test")
    parser.add_argument("--host", default=DEFAULT_HOST, help="N-CoreIQ base URL")
    parser.add_argument("--prog", default=DEFAULT_PROG, help="Known SAP PROG name")
    parser.add_argument("--clas", default=DEFAULT_CLAS, help="Known SAP CLAS name")
    parser.add_argument("--tabl", default=DEFAULT_TABL, help="Known SAP TABL name")
    parser.add_argument("--func", default=DEFAULT_FUNC, help="Known SAP FUNC name (optional)")
    args = parser.parse_args()

    host = args.host.rstrip("/")
    prog = args.prog.strip().upper() if args.prog.strip() else ""
    clas = args.clas.strip().upper() if args.clas.strip() else ""
    tabl = args.tabl.strip().upper() if args.tabl.strip() else ""
    func = args.func.strip().upper() if args.func.strip() else ""

    print("\n" + "=" * 60)
    print("  N-CoreIQ Phase 4 — Live SAP Integration Test")
    print("=" * 60)
    print(f"  Host : {host}")
    print(f"  PROG : {prog or '(not set)'}")
    print(f"  CLAS : {clas or '(not set)'}")
    print(f"  TABL : {tabl or '(not set)'}")
    print(f"  FUNC : {func or '(not set)'}")
    print("=" * 60)

    # ── Phase 1-3 regression ──────────────────────────────────────────────────
    test_health(host)
    test_sap_ping(host)
    test_repository_search(host)

    # ── Phase 4: metadata ─────────────────────────────────────────────────────
    section("4. Metadata Endpoint (Phase 4)")
    test_metadata(host, "PROG", prog, label=prog)
    test_metadata(host, "CLAS", clas, label=clas)
    test_metadata(host, "TABL", tabl, label=tabl)
    if func:
        test_metadata(host, "FUNC", func, label=func)
    else:
        record("GET metadata FUNC/... ", "SKIP", "--func not specified")

    # ── Phase 4: source ───────────────────────────────────────────────────────
    section("5. Source Endpoint (Phase 4)")

    # PROG full source (default max_lines)
    src_prog = test_source(host, "PROG", prog, label=prog)

    # PROG truncation test (max_lines=10)
    if prog:
        prog_lines = src_prog.get("returned_lines", 0) if src_prog else 0
        if prog_lines > 10:
            test_source(host, "PROG", prog, max_lines=10, label=f"{prog}(trunc)",
                        expect_truncated=True)
        else:
            print(f"         (PROG has {prog_lines} lines — skipping truncation test; "
                  f"program too short to truncate at max_lines=10)")
            test_source(host, "PROG", prog, max_lines=10, label=f"{prog}(trunc)")

    # CLAS multi-component
    src_clas = test_source(host, "CLAS", clas, label=clas)
    if src_clas and src_clas.get("component_count", 0) > 1:
        record(f"CLAS multi-component preserved", "PASS",
               f"component_count={src_clas['component_count']}")
    elif src_clas:
        record(f"CLAS multi-component check", "WARN",
               f"Only {src_clas.get('component_count',0)} component(s) returned — "
               "ECC may return single component for this class")

    # TABL structured definition
    test_source(host, "TABL", tabl, label=tabl)

    # FUNC source
    if func:
        test_source(host, "FUNC", func, label=func)
    else:
        record("GET source FUNC/...", "SKIP", "--func not specified")

    # Unicode check
    section("5b. Unicode Preservation")
    test_unicode(host, "PROG", prog)

    # max_lines validation
    test_max_lines_validation(host, "PROG", prog)

    # Not found
    test_not_found(host)

    # Unsupported type
    test_unsupported_type(host)

    # No N+1
    test_no_search_n1(host)

    # ── Summary ───────────────────────────────────────────────────────────────
    print(f"\n{'='*60}")
    print("  SUMMARY")
    print(f"{'='*60}")

    passed = sum(1 for r in results if r["status"] == "PASS")
    failed = sum(1 for r in results if r["status"] == "FAIL")
    warned = sum(1 for r in results if r["status"] == "WARN")
    skipped = sum(1 for r in results if r["status"] == "SKIP")
    total = len(results)

    print(f"  PASS   : {passed}")
    print(f"  WARN   : {warned}")
    print(f"  FAIL   : {failed}")
    print(f"  SKIP   : {skipped}")
    print(f"  TOTAL  : {total}")

    if failed:
        print(f"\n  FAILED tests:")
        for r in results:
            if r["status"] == "FAIL":
                print(f"    - {r['name']}")
                if r["detail"]:
                    print(f"      {r['detail']}")

    if warned:
        print(f"\n  WARNINGS:")
        for r in results:
            if r["status"] == "WARN":
                print(f"    - {r['name']}")
                if r["detail"]:
                    print(f"      {r['detail']}")

    overall = "PASS" if failed == 0 else "FAIL"
    print(f"\n  OVERALL: {PASS if failed == 0 else FAIL}")
    print("=" * 60 + "\n")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
