import { useState, useEffect, useCallback } from "react";
import { NavLink, Outlet } from "react-router-dom";
import { getSapPing, getHealth } from "../../api/client";
import type { ConnectionTestResult, HealthResult } from "../../types";

function navClass({ isActive }: { isActive: boolean }) {
  return `app-nav-item${isActive ? " active" : ""}`;
}

export function AppShell() {
  const [ping, setPing] = useState<ConnectionTestResult | null>(null);
  const [health, setHealth] = useState<HealthResult | null>(null);
  const [pingState, setPingState] = useState<"checking" | "connected" | "disconnected">("checking");

  const refreshStatus = useCallback(async () => {
    setPingState("checking");

    const [pingResult, healthResult] = await Promise.allSettled([
      getSapPing(),
      getHealth(),
    ]);

    if (healthResult.status === "fulfilled") {
      setHealth(healthResult.value);
    } else {
      setHealth(null);
    }

    if (pingResult.status === "fulfilled") {
      setPing(pingResult.value);
      setPingState(
        pingResult.value.status === "connected"
          ? "connected"
          : "disconnected"
      );
    } else {
      setPing(null);
      setPingState("disconnected");
    }
  }, []);  

  useEffect(() => {
    refreshStatus();
    const iv = setInterval(refreshStatus, 60_000);
    return () => clearInterval(iv);
  }, [refreshStatus]);

  const sysId = ping?.system_id ?? "—";
  const client = ping?.client ?? "—";
  const env = health?.environment ?? "";
  const version = health?.version ?? "—";

  return (
    <div className="app-shell">
      {/* Header */}
      <header className="app-header">
        <NavLink to="/" className="app-header-brand">
          <span className="app-header-brand-name">N-CoreIQ</span>
          {env && env !== "local" && (
            <span className="app-header-brand-tag">{env.toUpperCase()}</span>
          )}
        </NavLink>

        <div className="app-header-spacer" />

        <div className="app-header-status">
          <div className="app-header-status-item">
            <span className={`conn-dot ${pingState}`} title={`SAP ${pingState}`} />
            {pingState === "connected" ? "Connected" : pingState === "checking" ? "Checking…" : "Disconnected"}
          </div>
          {sysId !== "—" && (
            <div className="app-header-status-item" title="System / Client">
              {sysId} / {client}
            </div>
          )}
          {ping?.sap_release && (
            <div className="app-header-status-item" title="SAP Release">
              SAP {ping.sap_release}
            </div>
          )}
          <div className="app-header-version">v{version}</div>
        </div>
      </header>

      {/* Body */}
      <div className="app-body">
        {/* Navigation */}
        <nav className="app-nav" aria-label="Main navigation">
          <div className="app-nav-section">
            <NavLink to="/" end className={navClass}>
              <svg className="app-nav-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                <path d="M8 1L1 6v9h5V9h4v6h5V6L8 1z" />
              </svg>
              Overview
            </NavLink>
          </div>

          <div className="app-nav-section">
            <div className="app-nav-section-label">Repository</div>
            <NavLink to="/repository" className={navClass}>
              <svg className="app-nav-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                <path d="M2 2h12v2H2V2zm0 3h12v2H2V5zm0 3h8v2H2V8zm0 3h6v2H2v-2z" />
              </svg>
              Explorer
            </NavLink>
            <NavLink to="/dependencies" className={navClass}>
              <svg className="app-nav-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                <circle cx="3" cy="8" r="2" />
                <circle cx="13" cy="3" r="2" />
                <circle cx="13" cy="13" r="2" />
                <line x1="5" y1="7.2" x2="11" y2="4" stroke="currentColor" strokeWidth="1.5" />
                <line x1="5" y1="8.8" x2="11" y2="12" stroke="currentColor" strokeWidth="1.5" />
              </svg>
              Dependencies
            </NavLink>
          </div>

          <div className="app-nav-section">
            <div className="app-nav-section-label">Operations</div>
            <NavLink to="/jobs" className={navClass}>
              <svg className="app-nav-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                <rect x="1" y="3" width="14" height="10" rx="1" fillOpacity="0.2" stroke="currentColor" strokeWidth="1.5" fill="none" />
                <path d="M5 7h6M5 10h4" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" />
              </svg>
              Extraction Jobs
            </NavLink>
            <NavLink to="/exports" className={navClass}>
              <svg className="app-nav-icon" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
                <path d="M8 1v9M4 6l4 4 4-4M2 13h12" stroke="currentColor" strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" fill="none" />
              </svg>
              Exports
            </NavLink>
          </div>
        </nav>

        {/* Main content */}
        <main className="app-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
