import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { getJobs, getArtifactUrl } from "../api/client";
import type { ExtractionJob } from "../types";
import { LoadingSpinner } from "../components/common/LoadingSpinner";
import { ErrorAlert } from "../components/common/ErrorAlert";
import { JobStatusBadge } from "../components/common/StatusBadge";

export function ExportsPage() {
  const [jobs, setJobs] = useState<ExtractionJob[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const resp = await getJobs("SUCCEEDED", 100);
      setJobs(resp.items.filter((j) => j.artifact != null));
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { load(); }, [load]);

  const fmt = (iso: string | null | undefined) => {
    if (!iso) return "—";
    try { return new Date(iso).toLocaleString(); } catch { return iso; }
  };

  if (loading) return <LoadingSpinner message="Loading exports…" size="lg" />;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.25rem" }}>
        <div>
          <h1 className="page-title">Exports</h1>
          <p className="page-subtitle">Download completed extraction artifacts</p>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={load}>↻ Refresh</button>
      </div>

      <div className="alert alert-info" style={{ marginBottom: "1rem" }}>
        <span className="alert-icon">ℹ</span>
        <div className="alert-body">
          Artifacts are served securely through the N-CoreIQ backend.
          The browser never accesses SAP or server filesystem paths directly.
          Artifacts expire after the configured retention period.
        </div>
      </div>

      {error && <ErrorAlert error={error} title="Failed to load exports" onRetry={load} />}

      {jobs.length === 0 ? (
        <div className="empty-state">
          <div className="empty-state-icon">📦</div>
          <div className="empty-state-title">No export artifacts available</div>
          <div className="empty-state-msg">
            Run an extraction job with export format JSON or ZIP from the{" "}
            <Link to="/repository">Repository Explorer</Link>.
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
                  <th>Filename</th>
                  <th>Format</th>
                  <th>Size</th>
                  <th>Created</th>
                  <th>Expires</th>
                  <th>Actions</th>
                </tr>
              </thead>
              <tbody>
                {jobs.map((job) => {
                  const art = job.artifact!;
                  return (
                    <tr key={job.job_id}>
                      <td>
                        <Link to={`/jobs/${job.job_id}`} style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>
                          {job.job_id.substring(0, 8)}…
                        </Link>
                      </td>
                      <td><JobStatusBadge status={job.status} /></td>
                      <td style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>{art.filename}</td>
                      <td><span className="badge badge-default">{art.format.toUpperCase()}</span></td>
                      <td style={{ fontSize: "0.75rem" }}>{(art.size_bytes / 1024).toFixed(1)} KB</td>
                      <td style={{ fontSize: "0.75rem" }}>{fmt(art.created_at)}</td>
                      <td style={{ fontSize: "0.75rem" }}>{fmt(art.expires_at)}</td>
                      <td>
                        <a
                          href={getArtifactUrl(job.job_id)}
                          className="btn btn-primary btn-xs"
                          download={art.filename}
                        >
                          ⬇ Download
                        </a>
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
