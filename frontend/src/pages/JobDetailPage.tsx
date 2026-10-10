import { useState, useEffect, useCallback, useRef } from "react";
import { useParams, Link } from "react-router-dom";
import { getJob, getJobResult, cancelJob, retryJob, getArtifactUrl } from "../api/client";
import type { ExtractionJob } from "../types";
import { TERMINAL_JOB_STATUSES } from "../types";
import { LoadingSpinner } from "../components/common/LoadingSpinner";
import { ErrorAlert } from "../components/common/ErrorAlert";
import { JobStatusBadge } from "../components/common/StatusBadge";

export function JobDetailPage() {
  const { jobId } = useParams<{ jobId: string }>();
  const [job, setJob] = useState<ExtractionJob | null>(null);
  const [result, setResult] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [resultLoading, setResultLoading] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadJob = useCallback(async (quiet = false) => {
    if (!jobId) return;
    if (!quiet) setLoading(true);
    try {
      const j = await getJob(jobId);
      setJob(j);
    } catch (e) {
      setError(e);
    } finally {
      if (!quiet) setLoading(false);
    }
  }, [jobId]);

  useEffect(() => { loadJob(); }, [loadJob]);

  useEffect(() => {
    if (!job) return;
    const isActive = !TERMINAL_JOB_STATUSES.has(job.status);
    if (isActive) {
      pollRef.current = setInterval(() => loadJob(true), 3000);
    } else {
      if (pollRef.current) clearInterval(pollRef.current);
    }
    return () => { if (pollRef.current) clearInterval(pollRef.current); };
  }, [job, loadJob]);

  const loadResult = async () => {
    if (!jobId || !job) return;
    setResultLoading(true);
    try {
      const r = await getJobResult(jobId);
      setResult(r);
    } catch (e) {
      setActionError(`Failed to load result: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setResultLoading(false);
    }
  };

  const handleCancel = async () => {
    if (!jobId) return;
    setActionError(null);
    try { await cancelJob(jobId); loadJob(true); }
    catch (e) { setActionError(`Cancel failed: ${e instanceof Error ? e.message : String(e)}`); }
  };

  const handleRetry = async () => {
    if (!jobId) return;
    setActionError(null);
    try { await retryJob(jobId); loadJob(true); }
    catch (e) { setActionError(`Retry failed: ${e instanceof Error ? e.message : String(e)}`); }
  };

  const fmt = (iso: string | null | undefined) => {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  };

  if (loading) return <LoadingSpinner message="Loading job…" size="lg" />;
  if (error) return <ErrorAlert error={error} title="Failed to load job" onRetry={() => loadJob()} />;
  if (!job) return null;

  const isTerminal = TERMINAL_JOB_STATUSES.has(job.status);
  const canCancel = job.status === "QUEUED" || job.status === "RUNNING";
  const canRetry = job.status === "FAILED" || job.status === "CANCELLED";
  const hasArtifact = job.artifact != null && isTerminal;

  return (
    <div>
      <div style={{ marginBottom: "1.25rem" }}>
        <Link to="/jobs" style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)" }}>← Extraction Jobs</Link>
        <h1 className="page-title" style={{ marginTop: "0.5rem" }}>
          Job Detail
          <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.875rem", fontWeight: 400, marginLeft: "0.75rem", color: "var(--color-text-secondary)" }}>
            {job.job_id}
          </span>
        </h1>
      </div>

      {actionError && (
        <div className="alert alert-error" style={{ marginBottom: "0.75rem" }}>
          <span className="alert-icon">⚠</span>
          <div className="alert-body">{actionError}</div>
        </div>
      )}

      <div className="card-grid-2" style={{ marginBottom: "1rem" }}>
        {/* Status Card */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Status</span>
            <JobStatusBadge status={job.status} />
          </div>
          <div className="detail-grid">
            <span className="detail-label">Stage</span><span className="detail-value">{job.stage ?? "—"}</span>
            <span className="detail-label">Created</span><span className="detail-value">{fmt(job.created_at)}</span>
            <span className="detail-label">Started</span><span className="detail-value">{fmt(job.started_at)}</span>
            <span className="detail-label">Completed</span><span className="detail-value">{fmt(job.completed_at)}</span>
            <span className="detail-label">Attempt</span><span className="detail-value">{job.attempt}</span>
            {job.retry_of_job_id && (
              <><span className="detail-label">Retry Of</span>
              <span className="detail-value">
                <Link to={`/jobs/${job.retry_of_job_id}`} style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>
                  {job.retry_of_job_id.substring(0, 8)}…
                </Link>
              </span></>
            )}
            {job.result_status && <><span className="detail-label">Result</span><span className="detail-value">{job.result_status}</span></>}
          </div>
          <div className="btn-group" style={{ marginTop: "1rem", flexWrap: "wrap" }}>
            {!isTerminal && <span style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", display: "flex", alignItems: "center", gap: "0.375rem" }}><span className="spinner" /> Polling…</span>}
            {canCancel && <button className="btn btn-danger btn-sm" onClick={handleCancel}>Cancel</button>}
            {canRetry && <button className="btn btn-secondary btn-sm" onClick={handleRetry}>Retry</button>}
            {isTerminal && !result && (
              <button className="btn btn-secondary btn-sm" onClick={loadResult} disabled={resultLoading}>
                {resultLoading ? <><span className="spinner" /> Loading…</> : "View Result"}
              </button>
            )}
            {hasArtifact && (
              <a href={getArtifactUrl(job.job_id)} className="btn btn-primary btn-sm" download={job.artifact?.filename}>
                ⬇ Download Artifact
              </a>
            )}
          </div>
        </div>

        {/* Progress Card */}
        <div className="card">
          <div className="card-header"><span className="card-title">Progress</span></div>
          <div style={{ marginBottom: "0.75rem" }}>
            <div className="progress-bar-wrap" style={{ height: 10, marginBottom: "0.5rem" }}>
              <div
                className={`progress-bar-fill${job.status === "SUCCEEDED" ? " succeeded" : job.status === "FAILED" ? " failed" : ""}`}
                style={{ width: `${job.progress.percent_complete}%` }}
              />
            </div>
            <div style={{ fontSize: "0.875rem", fontWeight: 600 }}>{Math.round(job.progress.percent_complete)}%</div>
          </div>
          <div className="detail-grid">
            <span className="detail-label">Total</span><span className="detail-value">{job.progress.total_objects}</span>
            <span className="detail-label">Processed</span><span className="detail-value">{job.progress.processed_objects}</span>
            <span className="detail-label">Successful</span><span className="detail-value text-success">{job.progress.successful_objects}</span>
            <span className="detail-label">Partial</span><span className="detail-value">{job.progress.partial_objects}</span>
            <span className="detail-label">Failed</span><span className="detail-value text-error">{job.progress.failed_objects}</span>
            {job.progress.current_object_name && (
              <><span className="detail-label">Current</span>
              <span className="detail-value mono">{job.progress.current_object_type}/{job.progress.current_object_name}</span></>
            )}
            {job.progress.message && <><span className="detail-label">Message</span><span className="detail-value">{job.progress.message}</span></>}
          </div>
        </div>
      </div>

      {/* Error */}
      {job.error && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <div className="card-header"><span className="card-title text-error">Job Error</span></div>
          <div className="detail-grid">
            <span className="detail-label">Code</span><span className="detail-value mono">{job.error.code}</span>
            <span className="detail-label">Message</span><span className="detail-value">{job.error.message}</span>
            {job.error.stage && <><span className="detail-label">Stage</span><span className="detail-value">{job.error.stage}</span></>}
            <span className="detail-label">Retryable</span><span className="detail-value">{job.error.retryable ? "Yes" : "No"}</span>
          </div>
        </div>
      )}

      {/* Artifact */}
      {job.artifact && (
        <div className="card" style={{ marginBottom: "1rem" }}>
          <div className="card-header"><span className="card-title">Artifact</span></div>
          <div className="detail-grid">
            <span className="detail-label">Format</span><span className="detail-value">{job.artifact.format.toUpperCase()}</span>
            <span className="detail-label">Filename</span><span className="detail-value mono">{job.artifact.filename}</span>
            <span className="detail-label">Size</span><span className="detail-value">{(job.artifact.size_bytes / 1024).toFixed(1)} KB</span>
            <span className="detail-label">Created</span><span className="detail-value">{fmt(job.artifact.created_at)}</span>
            {job.artifact.expires_at && <><span className="detail-label">Expires</span><span className="detail-value">{fmt(job.artifact.expires_at)}</span></>}
          </div>
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="card">
          <div className="card-header"><span className="card-title">Extraction Result</span></div>
          <pre style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem", background: "#f4f5f7", padding: "0.875rem", borderRadius: "4px", overflow: "auto", maxHeight: 400 }}>
            {JSON.stringify(result, null, 2)}
          </pre>
        </div>
      )}
    </div>
  );
}
