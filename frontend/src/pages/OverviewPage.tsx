import { useState, useEffect, useCallback } from "react";
import { Link } from "react-router-dom";
import { getSapPing, getHealth, getJobs } from "../api/client";
import type { ConnectionTestResult, HealthResult } from "../types";
import { LoadingSpinner } from "../components/common/LoadingSpinner";
import { ErrorAlert } from "../components/common/ErrorAlert";

interface OverviewData {
  health: HealthResult | null;
  ping: ConnectionTestResult | null;
  recentJobCount: number;
}

export function OverviewPage() {
  const [data, setData] = useState<OverviewData>({ health: null, ping: null, recentJobCount: 0 });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<unknown>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [health, ping, jobs] = await Promise.all([
        getHealth(),
        getSapPing().catch(() => null),
        getJobs(undefined, 10).catch(() => ({ items: [], total: 0, limit: 10, offset: 0 })),
      ]);
      setData({ health, ping, recentJobCount: jobs.total });
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  if (loading) return <LoadingSpinner message="Loading overview…" size="lg" />;

  return (
    <div>
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: "1.25rem" }}>
        <div>
          <h1 className="page-title">Overview</h1>
          <p className="page-subtitle">Operational status and system summary</p>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={refresh}>↻ Refresh</button>
      </div>

      {error && <ErrorAlert error={error} title="Overview load error" onRetry={refresh} />}

      <div className="card-grid" style={{ marginTop: "1rem" }}>
        {/* SAP Connectivity Card */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">SAP Connectivity</span>
            {data.ping && (
              <span className={`badge badge-${data.ping.status === "connected" ? "succeeded" : "failed"}`}>
                {data.ping.status === "connected" ? "Connected" : "Disconnected"}
              </span>
            )}
          </div>
          {data.ping ? (
            <div className="detail-grid">
              <span className="detail-label">System</span>
              <span className="detail-value">{data.ping.system_id ?? "—"}</span>
              <span className="detail-label">Client</span>
              <span className="detail-value">{data.ping.client ?? "—"}</span>
              <span className="detail-label">Release</span>
              <span className="detail-value">{data.ping.sap_release ?? "—"}</span>
              <span className="detail-label">Mode</span>
              <span className="detail-value">{data.ping.connection_mode}</span>
              <span className="detail-label">Ping Method</span>
              <span className="detail-value detail-value mono">{data.ping.ping_method}</span>
            </div>
          ) : (
            <div className="empty-state" style={{ padding: "1rem 0" }}>
              <div className="empty-state-msg">SAP system is not reachable.</div>
            </div>
          )}
        </div>

        {/* Application Status */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Application Status</span>
            {data.health && (
              <span className={`badge badge-${data.health.status === "ok" ? "succeeded" : "warning"}`}>
                {data.health.status?.toUpperCase() ?? "UNKNOWN"}
              </span>
            )}
          </div>
          {data.health ? (
            <div className="detail-grid">
              <span className="detail-label">Application</span>
              <span className="detail-value">{data.health.app}</span>
              <span className="detail-label">Version</span>
              <span className="detail-value">{data.health.version}</span>
              <span className="detail-label">Environment</span>
              <span className="detail-value">{data.health.environment}</span>
              {data.health.job_store != null && (
                <>
                  <span className="detail-label">Job Store</span>
                  <span className="detail-value">{data.health.job_store}</span>
                </>
              )}
              {data.health.worker_running != null && (
                <>
                  <span className="detail-label">Workers</span>
                  <span className="detail-value">{data.health.worker_running ? "Running" : "Stopped"}</span>
                </>
              )}
              {data.health.queued_jobs != null && (
                <>
                  <span className="detail-label">Queued Jobs</span>
                  <span className="detail-value">{data.health.queued_jobs}</span>
                </>
              )}
            </div>
          ) : (
            <div className="empty-state" style={{ padding: "1rem 0" }}>
              <div className="empty-state-msg">Health data unavailable.</div>
            </div>
          )}
        </div>

        {/* Repository Explorer Card */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Repository Explorer</span>
          </div>
          <p style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", marginBottom: "1rem" }}>
            Discover and inspect SAP ABAP repository objects from the configured ECC system.
            Search by package, name pattern, or object type.
          </p>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <Link to="/repository" className="btn btn-primary btn-sm">Open Explorer</Link>
          </div>
        </div>

        {/* Dependency Intelligence */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Dependency Intelligence</span>
          </div>
          <p style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", marginBottom: "1rem" }}>
            Analyse forward and reverse dependencies, traverse dependency graphs,
            and perform deterministic impact analysis.
          </p>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <Link to="/dependencies" className="btn btn-secondary btn-sm">Dependency View</Link>
          </div>
        </div>

        {/* Extraction Jobs */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Extraction Jobs</span>
            {data.recentJobCount > 0 && (
              <span className="badge badge-info">{data.recentJobCount} total</span>
            )}
          </div>
          <p style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", marginBottom: "1rem" }}>
            Monitor asynchronous extraction jobs. View progress, cancel running jobs,
            retry failed jobs, and download export artifacts.
          </p>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <Link to="/jobs" className="btn btn-secondary btn-sm">View Jobs</Link>
          </div>
        </div>

        {/* Exports */}
        <div className="card">
          <div className="card-header">
            <span className="card-title">Exports</span>
          </div>
          <p style={{ fontSize: "0.8125rem", color: "var(--color-text-secondary)", marginBottom: "1rem" }}>
            Download completed extraction artifacts in JSON or ZIP format.
            Artifacts are served securely through the N-CoreIQ backend.
          </p>
          <div style={{ display: "flex", gap: "0.5rem" }}>
            <Link to="/exports" className="btn btn-secondary btn-sm">View Exports</Link>
          </div>
        </div>
      </div>
    </div>
  );
}
