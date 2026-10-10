import { useState, useEffect, useCallback, useRef } from "react";
import { Link } from "react-router-dom";
import { getJobs, cancelJob, retryJob } from "../api/client";
import type { ExtractionJob, JobStatus } from "../types";
import { TERMINAL_JOB_STATUSES } from "../types";
import { LoadingSpinner } from "../components/common/LoadingSpinner";
import { ErrorAlert } from "../components/common/ErrorAlert";
import { JobStatusBadge } from "../components/common/StatusBadge";

const STATUS_OPTIONS: Array<{ value: string; label: string }> = [
  { value: "", label: "All Statuses" },
  { value: "QUEUED", label: "Queued" },
  { value: "RUNNING", label: "Running" },
  { value: "SUCCEEDED", label: "Succeeded" },
  { value: "FAILED", label: "Failed" },
  { value: "CANCELLED", label: "Cancelled" },
];

export function JobsPage() {
  const [jobs, setJobs] = useState<ExtractionJob[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [statusFilter, setStatusFilter] = useState("");
  const [actionError, setActionError] = useState<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const load = useCallback(async (quiet = false) => {
    if (!quiet) setLoading(true);
    setError(null);
    try {
      const resp = await getJobs(statusFilter || undefined, 50);
      setJobs(resp.items);
      setTotal(resp.total);
    } catch (e) {
      setError(e);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [statusFilter]);

  useEffect(() => {
    load();
  }, [load]);

  // Poll while any active job exists
  useEffect(() => {
    const hasActive = jobs.some((j) => !TERMINAL_JOB_STATUSES.has(j.status));
    if (hasActive) {
      pollRef.current = setInterval(() => load(true), 3000);
    } else {
      if (pollRef.current) clearInterval(pollRef.current);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [jobs, load]);

  const handleCancel = async (jobId: string) => {
    setActionError(null);
    try {
      await cancelJob(jobId);
      load(true);
    } catch (e) {
      setActionError(`Cancel failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const handleRetry = async (jobId: string) => {
    setActionError(null);
    try {
      await retryJob(jobId);
      load(true);
    } catch (e) {
      setActionError(`Retry failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  };

  const formatDate = (iso: string | null | undefined) => {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  };

  if (loading) return <LoadingSpinner message="Loading jobs…" size="lg" />;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.25rem" }}>
        <div>
          <h1 className="page-title">Extraction Jobs</h1>
          <p className="page-subtitle">Monitor and manage asynchronous extraction jobs</p>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={() => load()}>↻ Refresh</button>
      </div>

      {error && <ErrorAlert error={error} title="Failed to load jobs" onRetry={() => load()} />}
      {actionError && (
        <div className="alert alert-error" style={{ marginBottom: "0.75rem" }}>
          <span className="alert-icon">⚠</span>
          <div className="alert-body">{actionError}</div>
        </div>
      )}

      <div style={{ display: "flex", gap: "0.75rem", marginBottom: "1rem", alignItems: "center" }}>
        <select className="select" style={{ width: "auto" }} value={statusFilter} onChange={(e) => setStatusFilter(e.target.value)}>
          {STATUS_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <span style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>{total} jobs total</span>
      </div>

      {jobs.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">📋</div>
          <div className="empty-state-title">No extraction jobs yet</div>
          <div className="empty-state-msg">
            Create a job from the <Link to="/repository">Repository Explorer</Link> by selecting objects and clicking "Extract Selected".
          </div>
        </div>
      ) : (
        <div className="card" style={{ padding: 0 }}>
          <div className="table-wrapper">
            <table>
              <thead>
                <tr>
                  <th>Job ID</th>
                  <th>Status</th>
                  <th>Stage</th>
                  <th>Progress</th>
                  <th>Created</th>
                  <th>Completed</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const isTerminal = TERMINAL_JOB_STATUSES.has(job.status);
                  const canCancel = job.status === "QUEUED" || job.status === "RUNNING";
                  const canRetry = job.status === "FAILED" || job.status === "CANCELLED";
                  const hasArtifact = job.artifact != null && isTerminal;
                  return (
                    <tr key={job.job_id}>
                      <td>
                        <Link to={`/jobs/${job.job_id}`} style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>
                          {job.job_id.substring(0, 8)}…
                        </Link>
                      </td>
                      <td><JobStatusBadge status={job.status} /></td>
                      <td>{job.stage ?? "—"}</td>
                      <td>
                        <div style={{ minWidth: 120 }}>
                          <div className="progress-bar-wrap" style={{ marginBottom: "0.25rem" }}>
                            <div
                              className={`progress-bar-fill${job.status === "SUCCEEDED" ? " succeeded" : job.status === "FAILED" ? " failed" : ""}`}
                              style={{ width: `${job.progress.percent_complete}%` }}
                            />
                          </div>
                          <div style={{ fontSize: "0.6875rem", color: "var(--color-text-secondary)" }}>
                            {job.progress.processed_objects}/{job.progress.total_objects} ({Math.round(job.progress.percent_complete)}%)
                          </div>
                        </div>
                      </td>
                      <td style={{ fontSize: "0.75rem" }}>{formatDate(job.created_at)}</td>
                      <td style={{ fontSize: "0.75rem" }}>{formatDate(job.completed_at)}</td>
                      <td>
                        <div className="btn-group">
                          <Link to={`/jobs/${job.job_id}`} className="btn btn-ghost btn-xs">View</Link>
                          {canCancel && (
                            <button className="btn btn-danger btn-xs" onClick={() => handleCancel(job.job_id)}>Cancel</button>
                          )}
                          {canRetry && (
                            <button className="btn btn-secondary btn-xs" onClick={() => handleRetry(job.job_id)}>Retry</button>
                          )}
                          {hasArtifact && (
                            <a
                              href={`/api/v1/repository/jobs/${job.job_id}/artifact`}
                              className="btn btn-secondary btn-xs"
                              download={job.artifact?.filename}
                            >
                              ⬇ Download
                            </a>
                          )}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  );
}
