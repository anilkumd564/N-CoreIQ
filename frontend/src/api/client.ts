/**
 * N-CoreIQ Centralized API Client
 *
 * All backend calls go through this module.
 * The browser NEVER calls SAP directly.
 * SAP credentials are NEVER present in this client.
 */

import type {
  ConnectionTestResult,
  CreateJobResponse,
  DependencyGraph,
  DependencyImpactRequest,
  DependencyImpactResult,
  DependencyTraversalRequest,
  DirectDependencyResult,
  ExtractionJob,
  ExtractionJobRequest,
  HealthResult,
  JobListResponse,
  PackageResolutionResult,
  RepositoryObjectMetadata,
  RepositoryObjectSource,
  RepositorySearchRequest,
  RepositorySearchResponse,
  ReverseDependencyRequest,
  ReverseDependencyResult,
} from "../types";

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------

const API_BASE = (import.meta as unknown as { env: Record<string, string> }).env
  .VITE_API_BASE_URL ?? "";

const API_V1 = `${API_BASE}/api/v1`;

// ---------------------------------------------------------------------------
// Error types
// ---------------------------------------------------------------------------

export interface ApiError {
  status: number;
  detail: string;
  isNetworkError?: boolean;
  isTimeout?: boolean;
}

function isApiError(e: unknown): e is ApiError {
  return (
    typeof e === "object" &&
    e !== null &&
    "status" in e &&
    "detail" in e
  );
}

export { isApiError };

// ---------------------------------------------------------------------------
// Core fetch wrapper
// ---------------------------------------------------------------------------

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
  signal?: AbortSignal
): Promise<T> {
  const url = `${API_V1}${path}`;
  const headers: Record<string, string> = {
    Accept: "application/json",
  };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(url, {
      method,
      headers,
      body: body !== undefined ? JSON.stringify(body) : undefined,
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw { status: 0, detail: "Request cancelled.", isNetworkError: true } as ApiError;
    }
    throw {
      status: 0,
      detail: "Network error: unable to reach the N-CoreIQ server.",
      isNetworkError: true,
    } as ApiError;
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const json = await response.json();
      if (json && typeof json.detail === "string") {
        detail = json.detail;
      } else if (json && json.detail) {
        detail = JSON.stringify(json.detail);
      }
    } catch {
      // Ignore JSON parse errors on error responses
    }
    throw { status: response.status, detail } as ApiError;
  }

  // For 204 No Content
  if (response.status === 204) {
    return undefined as unknown as T;
  }

  return response.json() as Promise<T>;
}

async function get<T>(path: string, signal?: AbortSignal): Promise<T> {
  return request<T>("GET", path, undefined, signal);
}

async function post<T>(
  path: string,
  body: unknown,
  signal?: AbortSignal
): Promise<T> {
  return request<T>("POST", path, body, signal);
}

// ---------------------------------------------------------------------------
// Health & Connectivity
// ---------------------------------------------------------------------------

export async function getHealth(signal?: AbortSignal): Promise<HealthResult> {
  return get<HealthResult>("/health", signal);
}

export async function getSapPing(
  signal?: AbortSignal
): Promise<ConnectionTestResult> {
  return get<ConnectionTestResult>("/sap/ping", signal);
}

// ---------------------------------------------------------------------------
// Package Resolution (Phase 3)
// ---------------------------------------------------------------------------

export async function getPackages(
  packageName: string,
  includeSubpackages = false,
  maxDepth = 20,
  signal?: AbortSignal
): Promise<PackageResolutionResult> {
  const params = new URLSearchParams({
    include_subpackages: String(includeSubpackages),
    max_depth: String(maxDepth),
  });
  return get<PackageResolutionResult>(
    `/repository/packages/${encodeURIComponent(packageName.toUpperCase())}?${params}`,
    signal
  );
}

// ---------------------------------------------------------------------------
// Repository Search (Phase 3)
// ---------------------------------------------------------------------------

export async function searchRepository(
  req: RepositorySearchRequest,
  signal?: AbortSignal
): Promise<RepositorySearchResponse> {
  return post<RepositorySearchResponse>("/repository/search", req, signal);
}

// ---------------------------------------------------------------------------
// Object Metadata (Phase 4)
// ---------------------------------------------------------------------------

export async function getMetadata(
  objectType: string,
  objectName: string,
  signal?: AbortSignal
): Promise<RepositoryObjectMetadata> {
  return get<RepositoryObjectMetadata>(
    `/repository/objects/${encodeURIComponent(objectType.toUpperCase())}/${encodeURIComponent(objectName.toUpperCase())}/metadata`,
    signal
  );
}

// ---------------------------------------------------------------------------
// Object Source (Phase 4)
// ---------------------------------------------------------------------------

/**
 * Retrieve ABAP source or technical definition for one SAP repository object.
 *
 * @param objectType  SAP object type (e.g. "PROG", "CLAS").
 * @param objectName  SAP object name.
 * @param maxLines    Optional line limit for a bounded preview.
 *                    **Omit (or pass `undefined`) for complete source retrieval.**
 *                    Passing a number sends `?max_lines=<n>` to the backend.
 * @param signal      Optional AbortSignal for cancellation.
 */
export async function getSource(
  objectType: string,
  objectName: string,
  maxLines?: number,
  signal?: AbortSignal
): Promise<RepositoryObjectSource> {
  const base = `/repository/objects/${encodeURIComponent(objectType.toUpperCase())}/${encodeURIComponent(objectName.toUpperCase())}/source`;
  const url = maxLines !== undefined ? `${base}?max_lines=${maxLines}` : base;
  return get<RepositoryObjectSource>(url, signal);
}

// ---------------------------------------------------------------------------
// Dependencies (Phase 8)
// ---------------------------------------------------------------------------

export async function getDependencies(
  objectType: string,
  objectName: string,
  minConfidence?: string,
  maxResults = 200,
  signal?: AbortSignal
): Promise<DirectDependencyResult> {
  const params = new URLSearchParams({ max_results: String(maxResults) });
  if (minConfidence) params.set("min_confidence", minConfidence);
  return get<DirectDependencyResult>(
    `/repository/objects/${encodeURIComponent(objectType.toUpperCase())}/${encodeURIComponent(objectName.toUpperCase())}/dependencies?${params}`,
    signal
  );
}

export async function traverseDependencies(
  req: DependencyTraversalRequest,
  signal?: AbortSignal
): Promise<DependencyGraph> {
  return post<DependencyGraph>(
    "/repository/dependencies/traverse",
    req,
    signal
  );
}

export async function findDependents(
  req: ReverseDependencyRequest,
  signal?: AbortSignal
): Promise<ReverseDependencyResult> {
  return post<ReverseDependencyResult>(
    "/repository/dependencies/reverse",
    req,
    signal
  );
}

export async function analyzeImpact(
  req: DependencyImpactRequest,
  signal?: AbortSignal
): Promise<DependencyImpactResult> {
  return post<DependencyImpactResult>(
    "/repository/dependencies/impact",
    req,
    signal
  );
}

// ---------------------------------------------------------------------------
// Extraction Jobs (Phase 6)
// ---------------------------------------------------------------------------

export async function createJob(
  req: ExtractionJobRequest,
  idempotencyKey?: string
): Promise<CreateJobResponse> {
  const headers: Record<string, string> = {};
  if (idempotencyKey) headers["Idempotency-Key"] = idempotencyKey;
  // Use request() directly to pass custom headers
  const url = `${API_V1}/repository/jobs`;
  const response = await fetch(url, {
    method: "POST",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      ...headers,
    },
    body: JSON.stringify(req),
  });
  if (!response.ok) {
    let detail = `HTTP ${response.status}`;
    try {
      const json = await response.json();
      if (json?.detail) detail = String(json.detail);
    } catch { /* ignore */ }
    throw { status: response.status, detail } as ApiError;
  }
  return response.json() as Promise<CreateJobResponse>;
}

export async function getJobs(
  status?: string,
  limit = 50,
  offset = 0,
  signal?: AbortSignal
): Promise<JobListResponse> {
  const params = new URLSearchParams({ limit: String(limit), offset: String(offset) });
  if (status) params.set("status", status);
  return get<JobListResponse>(`/repository/jobs?${params}`, signal);
}

export async function getJob(
  jobId: string,
  signal?: AbortSignal
): Promise<ExtractionJob> {
  return get<ExtractionJob>(
    `/repository/jobs/${encodeURIComponent(jobId)}`,
    signal
  );
}

export async function cancelJob(jobId: string): Promise<unknown> {
  return post<unknown>(
    `/repository/jobs/${encodeURIComponent(jobId)}/cancel`,
    {}
  );
}

export async function retryJob(jobId: string): Promise<CreateJobResponse> {
  return post<CreateJobResponse>(
    `/repository/jobs/${encodeURIComponent(jobId)}/retry`,
    {}
  );
}

export async function getJobResult(jobId: string): Promise<unknown> {
  return get<unknown>(`/repository/jobs/${encodeURIComponent(jobId)}/result`);
}

export function getArtifactUrl(jobId: string): string {
  return `${API_V1}/repository/jobs/${encodeURIComponent(jobId)}/artifact`;
}

// ---------------------------------------------------------------------------
// Phase 8C — Flat Source Export
// ---------------------------------------------------------------------------

export interface SourceExportObject {
  object_type: string;
  object_name: string;
}

/**
 * Fetch the Phase 8C flat source ZIP from the backend.
 * Returns an ArrayBuffer so the caller can either save via FSA or trigger a
 * browser download.
 *
 * @param objects   List of objects to export.
 * @param signal    Optional AbortSignal for cancellation.
 * @returns         { buffer: ArrayBuffer; filename: string }
 */
export async function exportSourceZip(
  objects: SourceExportObject[],
  signal?: AbortSignal
): Promise<{ buffer: ArrayBuffer; filename: string }> {
  const url = `${API_V1}/repository/source-export`;
  const body = {
    objects: objects.map((o) => ({
      object_type: o.object_type.toUpperCase(),
      object_name: o.object_name.toUpperCase(),
    })),
    include_metadata: false,
    include_source: true,
  };

  let response: Response;
  try {
    response = await fetch(url, {
      method: "POST",
      headers: {
        Accept: "application/zip",
        "Content-Type": "application/json",
      },
      body: JSON.stringify(body),
      signal,
    });
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw { status: 0, detail: "Export cancelled.", isNetworkError: true } as ApiError;
    }
    throw {
      status: 0,
      detail: "Network error: unable to reach the N-CoreIQ server.",
      isNetworkError: true,
    } as ApiError;
  }

  if (!response.ok) {
    let detail = `HTTP ${response.status}: ${response.statusText}`;
    try {
      const json = await response.json();
      if (json?.detail) detail = String(json.detail);
    } catch { /* ignore */ }
    throw { status: response.status, detail } as ApiError;
  }

  // Extract filename from Content-Disposition header
  const disposition = response.headers.get("Content-Disposition") ?? "";
  const match = disposition.match(/filename="([^"]+)"/);
  const filename = match ? match[1] : "N-CoreIQ-Source-export.zip";

  const buffer = await response.arrayBuffer();
  return { buffer, filename };
}
