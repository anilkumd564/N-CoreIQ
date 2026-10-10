import { useState, useCallback, useEffect, useRef } from "react";
import { useParams, useNavigate } from "react-router-dom";
import { searchRepository, getPackages, createJob } from "../api/client";
import type {
  RepositoryObject,
  RepositorySearchResponse,
  RepositoryPackage,
  ExtractionJobRequest,
} from "../types";
import { OBJECT_TYPE_LABELS } from "../types";
import { LoadingSpinner } from "../components/common/LoadingSpinner";
import { ErrorAlert } from "../components/common/ErrorAlert";
import { ObjectTypeBadge } from "../components/common/StatusBadge";
import { ObjectDetailDrawer } from "../components/ObjectDetail/ObjectDetailDrawer";

const ALL_TYPES = Object.keys(OBJECT_TYPE_LABELS);

interface SearchForm {
  packages: string;
  includeSubpackages: boolean;
  namePattern: string;
  objectType: string;
  maxResults: number;
  allCustomObjects: boolean;
}

const DEFAULT_FORM: SearchForm = {
  packages: "",
  includeSubpackages: false,
  namePattern: "",
  objectType: "",
  maxResults: 100,
  allCustomObjects: false,
};

export function RepositoryExplorerPage() {
  const params = useParams<{ objectType?: string; objectName?: string; tab?: string }>();
  const navigate = useNavigate();

  const [form, setForm] = useState<SearchForm>(DEFAULT_FORM);
  const [searching, setSearching] = useState(false);
  const [results, setResults] = useState<RepositorySearchResponse | null>(null);
  const [searchError, setSearchError] = useState<unknown>(null);
  const [selectedObj, setSelectedObj] = useState<RepositoryObject | null>(null);
  const [selectedRows, setSelectedRows] = useState<Set<string>>(new Set());
  const [extracting, setExtracting] = useState(false);
  const [extractError, setExtractError] = useState<unknown>(null);
  const [extractSuccess, setExtractSuccess] = useState<string | null>(null);

  // Package tree state
  const [pkgInput, setPkgInput] = useState("");
  const [pkgResult, setPkgResult] = useState<RepositoryPackage[] | null>(null);
  const [pkgLoading, setPkgLoading] = useState(false);
  const [pkgError, setPkgError] = useState<unknown>(null);

  const abortRef = useRef<AbortController | null>(null);

  // Open drawer from URL params
  useEffect(() => {
    if (params.objectType && params.objectName && results) {
      const obj = results.objects.find(
        (o) => o.sap_object_type === params.objectType?.toUpperCase() &&
               o.object_name === params.objectName?.toUpperCase()
      );
      if (obj) setSelectedObj(obj);
    }
  }, [params.objectType, params.objectName, results]);

  const handleSearch = useCallback(async () => {
    abortRef.current?.abort();
    abortRef.current = new AbortController();
    setSearching(true);
    setSearchError(null);
    setResults(null);

    const pkgs = form.packages
      .split(/[\s,]+/)
      .map((p) => p.trim().toUpperCase())
      .filter(Boolean);

    const namePatterns = form.namePattern
      .split(/[\s,]+/)
      .map((p) => p.trim().toUpperCase())
      .filter(Boolean);

    try {
      const resp = await searchRepository(
        {
          packages: pkgs.length > 0 ? pkgs : undefined,
          include_subpackages: form.includeSubpackages || undefined,
          name_patterns: namePatterns.length > 0 ? namePatterns : undefined,
          object_types: form.objectType ? [form.objectType] : undefined,
          all_custom_objects: form.allCustomObjects || undefined,
          max_results: form.maxResults,
        },
        abortRef.current.signal
      );
      setResults(resp);
    } catch (e) {
      setSearchError(e);
    } finally {
      setSearching(false);
    }
  }, [form]);

  const handleReset = () => {
    setForm(DEFAULT_FORM);
    setResults(null);
    setSearchError(null);
    setSelectedRows(new Set());
  };

  const handleRowClick = (obj: RepositoryObject) => {
    setSelectedObj(obj);
    navigate(`/repository/${obj.sap_object_type}/${obj.object_name}`, { replace: true });
  };

  const handleCloseDrawer = () => {
    setSelectedObj(null);
    navigate("/repository", { replace: true });
  };

  const toggleRowSelect = (key: string) => {
    setSelectedRows((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const selectAll = () => {
    if (!results) return;
    setSelectedRows(new Set(results.objects.map((o) => `${o.sap_object_type}::${o.object_name}`)));
  };

  const clearSelection = () => setSelectedRows(new Set());

  const handleExtract = async () => {
    if (selectedRows.size === 0 || !results) return;
    setExtracting(true);
    setExtractError(null);
    setExtractSuccess(null);
    const objects = results.objects
      .filter((o) => selectedRows.has(`${o.sap_object_type}::${o.object_name}`))
      .map((o) => ({ object_type: o.sap_object_type, object_name: o.object_name }));

    const req: ExtractionJobRequest = {
      extraction: {
        objects,
        include_metadata: true,
        include_source: true,
        source_max_lines: 500,
        continue_on_error: true,
      },
      export_format: "zip",
    };
    try {
      const job = await createJob(req);
      setExtractSuccess(job.job_id);
      clearSelection();
    } catch (e) {
      setExtractError(e);
    } finally {
      setExtracting(false);
    }
  };

  const lookupPackage = async () => {
    if (!pkgInput.trim()) return;
    setPkgLoading(true);
    setPkgError(null);
    try {
      const r = await getPackages(pkgInput.trim().toUpperCase(), true, 5);
      setPkgResult(r.packages);
      if (r.packages.length > 0) {
        setForm((f) => ({ ...f, packages: r.packages[0].package }));
      }
    } catch (e) {
      setPkgError(e);
    } finally {
      setPkgLoading(false);
    }
  };

  return (
    <div>
      <div style={{ marginBottom: "1.25rem" }}>
        <h1 className="page-title">Repository Explorer</h1>
        <p className="page-subtitle">Search SAP ABAP repository objects from the configured ECC system</p>
      </div>

      {/* Search Controls */}
      <div className="card" style={{ marginBottom: "1rem" }}>
        <div className="card-header">
          <span className="card-title">Search</span>
        </div>

        <div className="form-grid" style={{ marginBottom: "1rem" }}>
          <div className="form-field">
            <label htmlFor="pkg-input">Package</label>
            <div style={{ display: "flex", gap: "0.375rem" }}>
              <input
                id="pkg-input"
                className="input"
                placeholder="e.g. ZNCOREIQ"
                value={form.packages}
                onChange={(e) => setForm((f) => ({ ...f, packages: e.target.value }))}
                onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              />
            </div>
          </div>

          <div className="form-field">
            <label htmlFor="pattern-input">Name Pattern</label>
            <input
              id="pattern-input"
              className="input"
              placeholder="e.g. ZFI*"
              value={form.namePattern}
              onChange={(e) => setForm((f) => ({ ...f, namePattern: e.target.value }))}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
            />
          </div>

          <div className="form-field">
            <label htmlFor="type-select">Object Type</label>
            <select
              id="type-select"
              className="select"
              value={form.objectType}
              onChange={(e) => setForm((f) => ({ ...f, objectType: e.target.value }))}
            >
              <option value="">All Types</option>
              {ALL_TYPES.map((t) => (
                <option key={t} value={t}>{OBJECT_TYPE_LABELS[t]}</option>
              ))}
            </select>
          </div>

          <div className="form-field">
            <label htmlFor="maxresults">Max Results</label>
            <select
              id="maxresults"
              className="select"
              value={form.maxResults}
              onChange={(e) => setForm((f) => ({ ...f, maxResults: Number(e.target.value) }))}
            >
              {[50, 100, 200, 500].map((n) => (
                <option key={n} value={n}>{n}</option>
              ))}
            </select>
          </div>
        </div>

        <div style={{ display: "flex", gap: "1rem", alignItems: "center", flexWrap: "wrap" }}>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={form.includeSubpackages}
              onChange={(e) => setForm((f) => ({ ...f, includeSubpackages: e.target.checked }))}
            />
            Include Subpackages
          </label>
          <label className="checkbox-field">
            <input
              type="checkbox"
              checked={form.allCustomObjects}
              onChange={(e) => setForm((f) => ({ ...f, allCustomObjects: e.target.checked }))}
            />
            All Custom Objects (Z*/Y*)
          </label>
        </div>

        <div style={{ marginTop: "1rem", display: "flex", gap: "0.5rem" }}>
          <button className="btn btn-primary" onClick={handleSearch} disabled={searching}>
            {searching ? <><span className="spinner" /> Searching…</> : "Search"}
          </button>
          <button className="btn btn-secondary" onClick={handleReset} disabled={searching}>Reset</button>
        </div>
      </div>

      {/* Package Lookup Helper */}
      <div className="card" style={{ marginBottom: "1rem" }}>
        <div className="card-header">
          <span className="card-title">Package Lookup</span>
        </div>
        <div style={{ display: "flex", gap: "0.5rem", alignItems: "flex-end" }}>
          <div className="form-field" style={{ flex: 1 }}>
            <label>Package Name</label>
            <input
              className="input"
              placeholder="ZNCOREIQ"
              value={pkgInput}
              onChange={(e) => setPkgInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && lookupPackage()}
            />
          </div>
          <button className="btn btn-secondary" onClick={lookupPackage} disabled={pkgLoading}>
            {pkgLoading ? <span className="spinner" /> : "Look Up"}
          </button>
        </div>
        {pkgError && <ErrorAlert error={pkgError} title="Package lookup failed" />}
        {pkgResult && pkgResult.length > 0 && (
          <div style={{ marginTop: "0.75rem" }}>
            <div style={{ fontSize: "0.75rem", fontWeight: 600, color: "var(--color-text-secondary)", marginBottom: "0.375rem" }}>
              {pkgResult.length} packages found
            </div>
            <div className="package-tree">
              {pkgResult.slice(0, 20).map((p) => (
                <div
                  key={p.package}
                  className="package-tree-item"
                  style={{ paddingLeft: `${p.depth * 1.25}rem` }}
                  onClick={() => setForm((f) => ({ ...f, packages: p.package }))}
                >
                  📦 <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>{p.package}</span>
                  {p.description && <span style={{ color: "var(--color-text-secondary)", fontSize: "0.75rem" }}> — {p.description}</span>}
                </div>
              ))}
            </div>
          </div>
        )}
      </div>

      {/* Results */}
      {searchError && <ErrorAlert error={searchError} title="Search failed" onRetry={handleSearch} />}

      {extractSuccess && (
        <div className="alert alert-success" style={{ marginBottom: "0.75rem" }}>
          <span className="alert-icon">✓</span>
          <div className="alert-body">
            Extraction job created: <span style={{ fontFamily: "var(--font-mono)", fontSize: "0.75rem" }}>{extractSuccess}</span>.{" "}
            <a href="/jobs" style={{ color: "inherit", textDecoration: "underline" }}>View Jobs →</a>
          </div>
        </div>
      )}
      {extractError && <ErrorAlert error={extractError} title="Extraction failed" />}

      {results && (
        <div className="card">
          <div className="card-header">
            <div>
              <span className="card-title">
                Results
                <span className="badge badge-info" style={{ marginLeft: "0.5rem" }}>
                  {results.objects.length}
                </span>
                {results.statistics && (
                  <span style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginLeft: "0.5rem" }}>
                    of {results.statistics.total_matches} total
                  </span>
                )}
              </span>
              {results.truncated && (
                <div className="truncated-warning" style={{ marginTop: "0.25rem" }}>
                  ⚠ Results truncated — refine your search or increase max results
                </div>
              )}
            </div>
            <div className="btn-group">
              {selectedRows.size > 0 && (
                <>
                  <span className="selection-count">{selectedRows.size} selected</span>
                  <button className="btn btn-secondary btn-sm" onClick={clearSelection}>Clear</button>
                  <button className="btn btn-primary btn-sm" onClick={handleExtract} disabled={extracting}>
                    {extracting ? <><span className="spinner" /> Extracting…</> : "Extract Selected"}
                  </button>
                </>
              )}
              <button className="btn btn-ghost btn-sm" onClick={selectAll}>Select All Visible</button>
            </div>
          </div>

          {results.objects.length === 0 ? (
            <div className="empty-state">
              <div className="empty-state-icon">🔍</div>
              <div className="empty-state-title">No objects matched</div>
              <div className="empty-state-msg">Try broadening your search criteria or checking the package name.</div>
            </div>
          ) : (
            <div className="table-wrapper">
              <table>
                <thead>
                  <tr>
                    <th style={{ width: 28 }}><span className="sr-only">Select</span></th>
                    <th>Type</th>
                    <th>Name</th>
                    <th>Package</th>
                    <th>Description</th>
                    <th>Author</th>
                    <th>Match</th>
                  </tr>
                </thead>
                <tbody>
                  {results.objects.map((obj) => {
                    const key = `${obj.sap_object_type}::${obj.object_name}`;
                    const isSelected = selectedRows.has(key);
                    return (
                      <tr
                        key={key}
                        className={`clickable${isSelected ? " selected" : ""}`}
                        onClick={() => handleRowClick(obj)}
                      >
                        <td onClick={(e) => { e.stopPropagation(); toggleRowSelect(key); }}>
                          <input
                            type="checkbox"
                            checked={isSelected}
                            onChange={() => toggleRowSelect(key)}
                            aria-label={`Select ${obj.object_name}`}
                          />
                        </td>
                        <td className="nowrap"><ObjectTypeBadge type={obj.sap_object_type} /></td>
                        <td className="mono">{obj.object_name}</td>
                        <td className="mono">{obj.package ?? "—"}</td>
                        <td>{obj.description ?? "—"}</td>
                        <td className="mono">{obj.author ?? "—"}</td>
                        <td>{obj.match_reason ?? "—"}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}

      {/* Object Detail Drawer */}
      {selectedObj && (
        <ObjectDetailDrawer
          object={selectedObj}
          initialTab={params.tab as "summary" | "metadata" | "source" | "dependencies" | "impact" | undefined}
          onClose={handleCloseDrawer}
        />
      )}
    </div>
  );
}
