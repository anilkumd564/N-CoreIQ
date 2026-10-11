/**
 * N-CoreIQ API client unit tests.
 * All backend calls are mocked — no real SAP or N-CoreIQ connections.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import * as client from "../api/client";

// -----------------------------------------------------------------------
// Global fetch mock helper
// -----------------------------------------------------------------------

function mockFetch(status: number, body: unknown, ok = status < 400) {
  return vi.fn().mockResolvedValue({
    ok,
    status,
    statusText: status === 200 ? "OK" : "Error",
    json: async () => body,
  } as Response);
}

beforeEach(() => {
  vi.stubGlobal("fetch", mockFetch(200, {}));
});

afterEach(() => {
  vi.restoreAllMocks();
});

// -----------------------------------------------------------------------
// Health
// -----------------------------------------------------------------------

describe("getHealth", () => {
  it("returns health result on 200", async () => {
    const payload = { status: "ok", app: "N-CoreIQ", version: "0.8.5", environment: "local" };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.getHealth();
    expect(result.status).toBe("ok");
    expect(result.version).toBe("0.8.5");
  });

  it("throws ApiError on 503", async () => {
    vi.stubGlobal("fetch", mockFetch(503, { detail: "SAP down" }, false));
    await expect(client.getHealth()).rejects.toMatchObject({ status: 503, detail: "SAP down" });
  });

  it("throws network error when fetch rejects", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(client.getHealth()).rejects.toMatchObject({ isNetworkError: true });
  });
});

// -----------------------------------------------------------------------
// SAP Ping
// -----------------------------------------------------------------------

describe("getSapPing", () => {
  it("returns ping result on 200", async () => {
    const payload = {
      status: "connected",
      connection_mode: "odata",
      system_id: "NC1",
      client: "800",
      sap_release: "740",
      ping_method: "SystemInfoSet",
      message: "Connected",
    };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.getSapPing();
    expect(result.status).toBe("connected");
    expect(result.system_id).toBe("NC1");
  });

  it("throws ApiError on 503", async () => {
    vi.stubGlobal("fetch", mockFetch(503, { detail: "TLS handshake failed" }, false));
    await expect(client.getSapPing()).rejects.toMatchObject({ status: 503 });
  });
});

// -----------------------------------------------------------------------
// Package resolution
// -----------------------------------------------------------------------

describe("getPackages", () => {
  it("returns package result on 200", async () => {
    const payload = {
      root_package: "ZNCOREIQ",
      include_subpackages: true,
      truncated: false,
      packages: [{ package: "ZNCOREIQ", depth: 0 }],
    };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.getPackages("ZNCOREIQ", true);
    expect(result.root_package).toBe("ZNCOREIQ");
    expect(result.packages).toHaveLength(1);
  });

  it("throws 404 when package not found", async () => {
    vi.stubGlobal("fetch", mockFetch(404, { detail: "Package ZNOTEXIST not found" }, false));
    await expect(client.getPackages("ZNOTEXIST")).rejects.toMatchObject({ status: 404 });
  });
});

// -----------------------------------------------------------------------
// Repository search
// -----------------------------------------------------------------------

describe("searchRepository", () => {
  it("returns search response on 200", async () => {
    const payload = {
      system_id: "NC1",
      client: "800",
      truncated: false,
      objects: [{ sap_object_type: "PROG", object_name: "ZMY_PROG", metadata: {}, source_components: [], relationships: [] }],
      statistics: { total_matches: 1, returned: 1 },
    };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.searchRepository({ packages: ["ZNCOREIQ"], max_results: 100 });
    expect(result.objects).toHaveLength(1);
    expect(result.objects[0].sap_object_type).toBe("PROG");
  });

  it("throws 422 on empty scope", async () => {
    vi.stubGlobal("fetch", mockFetch(422, { detail: "No search scope provided" }, false));
    await expect(client.searchRepository({ max_results: 100 })).rejects.toMatchObject({ status: 422 });
  });

  it("handles truncated response", async () => {
    const payload = {
      truncated: true,
      objects: Array.from({ length: 100 }, (_, i) => ({
        sap_object_type: "PROG",
        object_name: `ZPROG_${i}`,
        metadata: {},
        source_components: [],
        relationships: [],
      })),
      statistics: { total_matches: 500, returned: 100 },
    };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.searchRepository({ packages: ["ZNCOREIQ"], max_results: 100 });
    expect(result.truncated).toBe(true);
    expect(result.objects).toHaveLength(100);
  });
});

// -----------------------------------------------------------------------
// Object metadata
// -----------------------------------------------------------------------

describe("getMetadata", () => {
  it("returns metadata for PROG", async () => {
    const payload = { sap_object_type: "PROG", object_name: "ZMY_PROG", found: true };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.getMetadata("PROG", "ZMY_PROG");
    expect(result.found).toBe(true);
    expect(result.sap_object_type).toBe("PROG");
  });

  it("throws 404 when object not found", async () => {
    vi.stubGlobal("fetch", mockFetch(404, { detail: "Object not found" }, false));
    await expect(client.getMetadata("PROG", "ZNOTEXIST")).rejects.toMatchObject({ status: 404 });
  });

  it("throws 422 for unsupported type", async () => {
    vi.stubGlobal("fetch", mockFetch(422, { detail: "Unsupported type XXXX" }, false));
    await expect(client.getMetadata("XXXX", "ZTEST")).rejects.toMatchObject({ status: 422 });
  });
});

// -----------------------------------------------------------------------
// Object source
// -----------------------------------------------------------------------

describe("getSource", () => {
  it("returns source for PROG with explicit maxLines", async () => {
    const payload = {
      sap_object_type: "PROG",
      object_name: "ZMY_PROG",
      max_lines: 500,
      returned_lines: 10,
      component_count: 1,
      found: true,
      truncated: false,
      components: [{ component_type: "PROGRAM", component_name: "ZMY_PROG", line_count: 10, lines: [] }],
    };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.getSource("PROG", "ZMY_PROG", 500);
    expect(result.found).toBe(true);
    expect(result.component_count).toBe(1);
  });

  it("returns technical definition label for TABL", async () => {
    const payload = {
      sap_object_type: "TABL",
      object_name: "ZFIN_TABLE",
      max_lines: null,
      returned_lines: 5,
      component_count: 1,
      found: true,
      truncated: false,
      components: [{ component_type: "TABLE_DEF", component_name: "ZFIN_TABLE", line_count: 5, lines: [] }],
    };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.getSource("TABL", "ZFIN_TABLE");
    expect(result.sap_object_type).toBe("TABL");
  });

  it("does NOT send max_lines when called without maxLines (complete retrieval)", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({
        sap_object_type: "PROG",
        object_name: "ZMY_PROG",
        max_lines: null,
        returned_lines: 1250,
        component_count: 1,
        found: true,
        truncated: false,
        components: [],
      }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    const result = await client.getSource("PROG", "ZMY_PROG");
    const calledUrl = (fetchMock.mock.calls[0] as [string, RequestInit])[0];
    expect(calledUrl).not.toContain("max_lines");
    expect(result.returned_lines).toBe(1250);
  });

  it("sends max_lines=500 when explicitly requested", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      json: async () => ({
        sap_object_type: "PROG",
        object_name: "ZMY_PROG",
        max_lines: 500,
        returned_lines: 500,
        component_count: 1,
        found: true,
        truncated: true,
        components: [],
      }),
    } as Response);
    vi.stubGlobal("fetch", fetchMock);
    await client.getSource("PROG", "ZMY_PROG", 500);
    const calledUrl = (fetchMock.mock.calls[0] as [string, RequestInit])[0];
    expect(calledUrl).toContain("max_lines=500");
  });

  it("returns 1250 lines when complete source requested (no maxLines)", async () => {
    const lines = Array.from({ length: 1250 }, (_, i) => ({
      line_no: i + 1,
      source_line: `* line ${i + 1}`,
      component_type: "PROGRAM",
      component_name: "ZMY_PROG",
    }));
    const payload = {
      sap_object_type: "PROG",
      object_name: "ZMY_PROG",
      max_lines: null,
      returned_lines: 1250,
      component_count: 1,
      found: true,
      truncated: false,
      components: [{ component_type: "PROGRAM", component_name: "ZMY_PROG", line_count: 1250, lines }],
    };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.getSource("PROG", "ZMY_PROG");
    expect(result.returned_lines).toBe(1250);
    expect(result.truncated).toBe(false);
    expect(result.components[0].lines).toHaveLength(1250);
  });

  it("download uses all returned lines (not truncated)", async () => {
    // Verify that source components contain all lines available for download
    const lines = Array.from({ length: 3842 }, (_, i) => ({
      line_no: i + 1,
      source_line: `* line ${i + 1}`,
      component_type: "PROGRAM",
      component_name: "ZBIG_PROG",
    }));
    const payload = {
      sap_object_type: "PROG",
      object_name: "ZBIG_PROG",
      max_lines: null,
      returned_lines: 3842,
      component_count: 1,
      found: true,
      truncated: false,
      components: [{ component_type: "PROGRAM", component_name: "ZBIG_PROG", line_count: 3842, lines }],
    };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.getSource("PROG", "ZBIG_PROG");
    // All lines available for download
    const downloadText = result.components[0].lines.map((l) => l.source_line).join("\n");
    expect(downloadText.split("\n")).toHaveLength(3842);
  });
});

// -----------------------------------------------------------------------
// Dependencies
// -----------------------------------------------------------------------

describe("getDependencies", () => {
  it("returns direct dependencies", async () => {
    const payload = {
      source_object_type: "PROG",
      source_object_name: "ZMY_PROG",
      total_relations: 2,
      returned_relations: 2,
      truncated: false,
      relationships: [
        { source_object_type: "PROG", source_object_name: "ZMY_PROG", target_object_type: "FUNC", target_object_name: "ZFI_GET", relationship_type: "CALLS_FUNCTION", confidence: "HIGH", dynamic: false },
        { source_object_type: "PROG", source_object_name: "ZMY_PROG", target_object_type: "TABL", target_object_name: "ZFIN_ITEMS", relationship_type: "READS_TABLE", confidence: "EXACT", dynamic: false },
      ],
    };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.getDependencies("PROG", "ZMY_PROG");
    expect(result.relationships).toHaveLength(2);
    expect(result.relationships[0].relationship_type).toBe("CALLS_FUNCTION");
  });

  it("returns empty list when no dependencies", async () => {
    const payload = {
      source_object_type: "PROG",
      source_object_name: "ZISOLATED",
      total_relations: 0,
      returned_relations: 0,
      truncated: false,
      relationships: [],
    };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.getDependencies("PROG", "ZISOLATED");
    expect(result.relationships).toHaveLength(0);
  });
});

// -----------------------------------------------------------------------
// Jobs
// -----------------------------------------------------------------------

describe("getJobs", () => {
  it("returns job list on 200", async () => {
    const payload = {
      items: [
        {
          job_id: "abc123",
          status: "SUCCEEDED",
          created_at: new Date().toISOString(),
          updated_at: new Date().toISOString(),
          attempt: 1,
          request: {},
          progress: { total_objects: 1, processed_objects: 1, successful_objects: 1, partial_objects: 0, failed_objects: 0, skipped_objects: 0, truncated_objects: 0, percent_complete: 100 },
        },
      ],
      total: 1,
      limit: 50,
      offset: 0,
    };
    vi.stubGlobal("fetch", mockFetch(200, payload));
    const result = await client.getJobs();
    expect(result.items).toHaveLength(1);
    expect(result.items[0].status).toBe("SUCCEEDED");
  });
});

describe("createJob", () => {
  it("returns created job on 202", async () => {
    const payload = { job_id: "newjob123", status: "QUEUED", status_url: "/api/v1/repository/jobs/newjob123" };
    vi.stubGlobal("fetch", mockFetch(202, payload));
    const result = await client.createJob({
      extraction: {
        objects: [{ object_type: "PROG", object_name: "ZMY_PROG" }],
        include_metadata: true,
        include_source: true,
      },
      export_format: "zip",
    });
    expect(result.job_id).toBe("newjob123");
    expect(result.status).toBe("QUEUED");
  });

  it("throws 422 on invalid request", async () => {
    vi.stubGlobal("fetch", mockFetch(422, { detail: "Missing extraction objects" }, false));
    await expect(client.createJob({ extraction: {} })).rejects.toMatchObject({ status: 422 });
  });
});

describe("cancelJob", () => {
  it("cancels a job", async () => {
    vi.stubGlobal("fetch", mockFetch(202, { job_id: "abc", status: "CANCEL_REQUESTED", message: "Cancellation accepted." }));
    const result = await client.cancelJob("abc");
    expect(result).toBeDefined();
  });
});

describe("retryJob", () => {
  it("retries a job", async () => {
    const payload = { job_id: "newjob456", status: "QUEUED", status_url: "/api/v1/repository/jobs/newjob456" };
    vi.stubGlobal("fetch", mockFetch(202, payload));
    const result = await client.retryJob("original123");
    expect(result.job_id).toBe("newjob456");
  });
});

// -----------------------------------------------------------------------
// Error handling
// -----------------------------------------------------------------------

// -----------------------------------------------------------------------
// Phase 8C — exportSourceZip
// -----------------------------------------------------------------------

describe("exportSourceZip", () => {
  it("returns buffer and filename on 200", async () => {
    const buf = new ArrayBuffer(16);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      statusText: "OK",
      headers: {
        get: (name: string) => name === "Content-Disposition"
          ? 'attachment; filename="N-CoreIQ-Source-20261009_230000.zip"'
          : null,
      },
      arrayBuffer: async () => buf,
      json: async () => ({}),
    } as unknown as Response));
    const { buffer, filename } = await client.exportSourceZip([
      { object_type: "PROG", object_name: "ZREPORT1" },
    ]);
    expect(filename).toBe("N-CoreIQ-Source-20261009_230000.zip");
    expect(buffer).toBe(buf);
  });

  it("uppercases object names in request", async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => null },
      arrayBuffer: async () => new ArrayBuffer(0),
      json: async () => ({}),
    } as unknown as Response);
    vi.stubGlobal("fetch", fetchMock);
    await client.exportSourceZip([{ object_type: "prog", object_name: "zreport1" }]);
    const callInit = (fetchMock.mock.calls[0] as [string, RequestInit])[1];
    const body = JSON.parse(callInit.body as string) as { objects: Array<{ object_type: string; object_name: string }> };
    expect(body.objects[0].object_type).toBe("PROG");
    expect(body.objects[0].object_name).toBe("ZREPORT1");
  });

  it("throws ApiError on 503", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 503,
      statusText: "Service Unavailable",
      json: async () => ({ detail: "SAP unreachable" }),
      arrayBuffer: async () => new ArrayBuffer(0),
    } as unknown as Response));
    await expect(
      client.exportSourceZip([{ object_type: "PROG", object_name: "ZTEST" }])
    ).rejects.toMatchObject({ status: 503 });
  });

  it("throws network error on fetch failure", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    await expect(
      client.exportSourceZip([{ object_type: "PROG", object_name: "ZTEST" }])
    ).rejects.toMatchObject({ isNetworkError: true });
  });

  it("uses fallback filename when Content-Disposition absent", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      headers: { get: () => null },
      arrayBuffer: async () => new ArrayBuffer(0),
      json: async () => ({}),
    } as unknown as Response));
    const { filename } = await client.exportSourceZip([
      { object_type: "PROG", object_name: "ZTEST" },
    ]);
    expect(filename).toBe("N-CoreIQ-Source-export.zip");
  });

  it("throws on abort signal", async () => {
    const ctrl = new AbortController();
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(
      Object.assign(new DOMException("Aborted", "AbortError"), {})
    ));
    ctrl.abort();
    await expect(
      client.exportSourceZip([{ object_type: "PROG", object_name: "ZTEST" }], ctrl.signal)
    ).rejects.toMatchObject({ status: 0, isNetworkError: true });
  });
});

describe("isApiError", () => {
  it("identifies ApiError shape", () => {
    const err = { status: 404, detail: "Not found" };
    expect(client.isApiError(err)).toBe(true);
  });

  it("rejects non-ApiError", () => {
    expect(client.isApiError(new Error("test"))).toBe(false);
    expect(client.isApiError("string")).toBe(false);
    expect(client.isApiError(null)).toBe(false);
  });
});

describe("malformed response handling", () => {
  it("handles non-JSON error response gracefully", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({
      ok: false,
      status: 500,
      statusText: "Internal Server Error",
      json: async () => { throw new Error("not json"); },
    } as unknown as Response));
    await expect(client.getHealth()).rejects.toMatchObject({ status: 500 });
  });
});
