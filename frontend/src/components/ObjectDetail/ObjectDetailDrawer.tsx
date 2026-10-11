import { useState, useCallback } from "react";
import { useNavigate } from "react-router-dom";
import { getMetadata, getSource, getDependencies, analyzeImpact, findDependents } from "../../api/client";
import type {
  RepositoryObject,
  RepositoryObjectMetadata,
  RepositoryObjectSource,
  DirectDependencyResult,
  DependencyImpactResult,
  ReverseDependencyResult,
} from "../../types";
import { TECHNICAL_DEFINITION_TYPES } from "../../types";
import { LoadingSpinner } from "../common/LoadingSpinner";
import { ErrorAlert } from "../common/ErrorAlert";
import { ObjectTypeBadge, ConfidenceBadge, RelationshipBadge } from "../common/StatusBadge";

type Tab = "summary" | "metadata" | "source" | "dependencies" | "impact";

interface Props {
  object: RepositoryObject;
  initialTab?: Tab;
  onClose: () => void;
}

export function ObjectDetailDrawer({ object, initialTab = "summary", onClose }: Props) {
  const [activeTab, setActiveTab] = useState<Tab>(initialTab);
  const [metadata, setMetadata] = useState<RepositoryObjectMetadata | null>(null);
  const [metaLoading, setMetaLoading] = useState(false);
  const [metaError, setMetaError] = useState<unknown>(null);

  const [source, setSource] = useState<RepositoryObjectSource | null>(null);
  const [sourceLoading, setSourceLoading] = useState(false);
  const [sourceError, setSourceError] = useState<unknown>(null);
  const [selectedComponent, setSelectedComponent] = useState(0);

  const [deps, setDeps] = useState<DirectDependencyResult | null>(null);
  const [depsLoading, setDepsLoading] = useState(false);
  const [depsError, setDepsError] = useState<unknown>(null);

  const [impact, setImpact] = useState<DependencyImpactResult | null>(null);
  const [impactLoading, setImpactLoading] = useState(false);
  const [impactError, setImpactError] = useState<unknown>(null);
  const [impactPackage, setImpactPackage] = useState(object.package ?? "");

  const [reverseResult, setReverseResult] = useState<ReverseDependencyResult | null>(null);
  const [reverseLoading, setReverseLoading] = useState(false);
  const [reverseError, setReverseError] = useState<unknown>(null);

  const isTechDef = TECHNICAL_DEFINITION_TYPES.has(object.sap_object_type);

  const loadMetadata = useCallback(async () => {
    if (metadata || metaLoading) return;
    setMetaLoading(true);
    setMetaError(null);
    try {
      const m = await getMetadata(object.sap_object_type, object.object_name);
      setMetadata(m);
    } catch (e) {
      setMetaError(e);
    } finally {
      setMetaLoading(false);
    }
  }, [object, metadata, metaLoading]);

  const loadSource = useCallback(async () => {
    setSourceLoading(true);
    setSourceError(null);
    try {
      // No maxLines argument → complete source retrieval (no truncation)
      const s = await getSource(object.sap_object_type, object.object_name);
      setSource(s);
      setSelectedComponent(0);
    } catch (e) {
      setSourceError(e);
    } finally {
      setSourceLoading(false);
    }
  }, [object]);

  const loadDeps = useCallback(async () => {
    if (deps || depsLoading) return;
    setDepsLoading(true);
    setDepsError(null);
    try {
      const d = await getDependencies(object.sap_object_type, object.object_name, undefined, 200);
      setDeps(d);
    } catch (e) {
      setDepsError(e);
    } finally {
      setDepsLoading(false);
    }
  }, [object, deps, depsLoading]);

  const loadImpact = useCallback(async () => {
    if (!impactPackage.trim()) return;
    setImpactLoading(true);
    setImpactError(null);
    try {
      const r = await analyzeImpact({
        object: { sap_object_type: object.sap_object_type, object_name: object.object_name },
        scope: { packages: [impactPackage.trim().toUpperCase()], include_subpackages: true },
        max_depth: 2,
      });
      setImpact(r);
    } catch (e) {
      setImpactError(e);
    } finally {
      setImpactLoading(false);
    }
  }, [object, impactPackage]);

  const loadReverse = useCallback(async () => {
    if (!impactPackage.trim()) return;
    setReverseLoading(true);
    setReverseError(null);
    try {
      const r = await findDependents({
        target_object: { sap_object_type: object.sap_object_type, object_name: object.object_name },
        scope: { packages: [impactPackage.trim().toUpperCase()], include_subpackages: true, max_results: 100 },
      });
      setReverseResult(r);
    } catch (e) {
      setReverseError(e);
    } finally {
      setReverseLoading(false);
    }
  }, [object, impactPackage]);

  const handleTabChange = (tab: Tab) => {
    setActiveTab(tab);
    if (tab === "metadata" && !metadata) loadMetadata();
    if (tab === "source" && !source) loadSource();
    if (tab === "dependencies" && !deps) loadDeps();
  };

  const copySource = () => {
    if (!source) return;
    const comp = source.components[selectedComponent];
    if (!comp) return;
    // All returned lines — no truncation
    const text = comp.lines.map((l) => l.source_line).join("\n");
    navigator.clipboard.writeText(text).catch(() => {});
  };

  const downloadSource = () => {
    if (!source) return;
    const comp = source.components[selectedComponent];
    if (!comp) return;
    // Download contains every returned source line
    const text = comp.lines.map((l) => l.source_line).join("\n");
    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${comp.component_name || object.object_name}.abap`;
    a.click();
    URL.revokeObjectURL(url);
  };

  return (
    <>
      <div className="drawer-overlay" onClick={onClose} aria-hidden="true" />
      <div className="drawer" role="dialog" aria-modal="true" aria-label={`${object.sap_object_type} ${object.object_name}`}>
        <div className="drawer-header">
          <div className="drawer-title">
            <h2>
              <ObjectTypeBadge type={object.sap_object_type} /> {object.object_name}
            </h2>
            {object.description && (
              <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginTop: "0.125rem" }}>
                {object.description}
              </div>
            )}
          </div>
          <button className="drawer-close" onClick={onClose} aria-label="Close">✕</button>
        </div>

        <div className="tabs" style={{ padding: "0 1.25rem", marginBottom: 0 }}>
          {(["summary", "metadata", "source", "dependencies", "impact"] as Tab[]).map((tab) => (
            <button
              key={tab}
              className={`tab-btn${activeTab === tab ? " active" : ""}`}
              onClick={() => handleTabChange(tab)}
            >
              {tab === "source" && isTechDef ? "Technical Def." : tab.charAt(0).toUpperCase() + tab.slice(1)}
            </button>
          ))}
        </div>

        <div className="drawer-body">
          {/* SUMMARY TAB */}
          {activeTab === "summary" && (
            <div>
              <div className="detail-grid" style={{ marginBottom: "1.25rem" }}>
                <span className="detail-label">Object Type</span>
                <span className="detail-value"><ObjectTypeBadge type={object.sap_object_type} /></span>
                <span className="detail-label">Name</span>
                <span className="detail-value mono">{object.object_name}</span>
                {object.package && <><span className="detail-label">Package</span><span className="detail-value mono">{object.package}</span></>}
                {object.description && <><span className="detail-label">Description</span><span className="detail-value">{object.description}</span></>}
                {object.author && <><span className="detail-label">Author</span><span className="detail-value mono">{object.author}</span></>}
                {object.match_reason && <><span className="detail-label">Match Reason</span><span className="detail-value">{object.match_reason}</span></>}
                {object.system_id && <><span className="detail-label">System</span><span className="detail-value">{object.system_id}</span></>}
                {object.client && <><span className="detail-label">Client</span><span className="detail-value">{object.client}</span></>}
              </div>
              <div className="btn-group" style={{ flexWrap: "wrap" }}>
                <button className="btn btn-secondary btn-sm" onClick={() => handleTabChange("metadata")}>View Metadata</button>
                <button className="btn btn-secondary btn-sm" onClick={() => handleTabChange("source")}>
                  {isTechDef ? "View Technical Definition" : "View Source"}
                </button>
                <button className="btn btn-secondary btn-sm" onClick={() => handleTabChange("dependencies")}>View Dependencies</button>
                <button className="btn btn-secondary btn-sm" onClick={() => handleTabChange("impact")}>Analyse Impact</button>
              </div>
            </div>
          )}

          {/* METADATA TAB */}
          {activeTab === "metadata" && (
            <div>
              {metaLoading && <LoadingSpinner message="Loading metadata…" />}
              {metaError && <ErrorAlert error={metaError} title="Metadata load failed" onRetry={loadMetadata} />}
              {metadata && (
                <div className="detail-grid">
                  <span className="detail-label">Object Type</span><span className="detail-value"><ObjectTypeBadge type={metadata.sap_object_type} /></span>
                  <span className="detail-label">Name</span><span className="detail-value mono">{metadata.object_name}</span>
                  {metadata.package && <><span className="detail-label">Package</span><span className="detail-value mono">{metadata.package}</span></>}
                  {metadata.description && <><span className="detail-label">Description</span><span className="detail-value">{metadata.description}</span></>}
                  {metadata.author && <><span className="detail-label">Author</span><span className="detail-value mono">{metadata.author}</span></>}
                  {metadata.created_date && <><span className="detail-label">Created</span><span className="detail-value">{metadata.created_date}</span></>}
                  {metadata.changed_by && <><span className="detail-label">Changed By</span><span className="detail-value mono">{metadata.changed_by}</span></>}
                  {metadata.changed_date && <><span className="detail-label">Changed Date</span><span className="detail-value">{metadata.changed_date}</span></>}
                  {metadata.master_language && <><span className="detail-label">Language</span><span className="detail-value">{metadata.master_language}</span></>}
                  {metadata.active_status && <><span className="detail-label">Status</span><span className="detail-value">{metadata.active_status}</span></>}
                  {metadata.object_subtype && <><span className="detail-label">Subtype</span><span className="detail-value">{metadata.object_subtype}</span></>}
                  {metadata.program_name && <><span className="detail-label">Program</span><span className="detail-value mono">{metadata.program_name}</span></>}
                  {metadata.function_group && <><span className="detail-label">Function Group</span><span className="detail-value mono">{metadata.function_group}</span></>}
                  {metadata.table_class && <><span className="detail-label">Table Class</span><span className="detail-value">{metadata.table_class}</span></>}
                  {metadata.system_id && <><span className="detail-label">System</span><span className="detail-value">{metadata.system_id}</span></>}
                  {metadata.client && <><span className="detail-label">Client</span><span className="detail-value">{metadata.client}</span></>}
                  {metadata.warning && <><span className="detail-label">Warning</span><span className="detail-value text-error">{metadata.warning}</span></>}
                </div>
              )}
            </div>
          )}

          {/* SOURCE TAB */}
          {activeTab === "source" && (
            <div>
              {!source && !sourceLoading && (
                <div style={{ marginBottom: "1rem" }}>
                  <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.75rem" }}>
                    <button className="btn btn-primary btn-sm" onClick={() => loadSource()}>
                      Load {isTechDef ? "Technical Definition" : "Source"}
                    </button>
                    <span style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>
                      Retrieves complete source — no line limit applied
                    </span>
                  </div>
                </div>
              )}
              {sourceLoading && <LoadingSpinner message={`Loading ${isTechDef ? "technical definition" : "source"}…`} />}
              {sourceError && <ErrorAlert error={sourceError} title="Source load failed" onRetry={() => loadSource()} />}
              {source && (
                <div>
                  <div style={{ display: "flex", gap: "0.5rem", alignItems: "center", marginBottom: "0.75rem", flexWrap: "wrap" }}>
                    <span style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)" }}>
                      Lines: {source.returned_lines.toLocaleString()} · {source.component_count} component(s)
                      {source.truncated && <span className="truncated-warning" style={{ display: "inline-flex", marginLeft: "0.5rem" }}>⚠ Truncated</span>}
                    </span>
                    {source.components.length > 1 && (
                      <select
                        className="select"
                        value={selectedComponent}
                        onChange={(e) => setSelectedComponent(Number(e.target.value))}
                        style={{ width: "auto" }}
                      >
                        {source.components.map((c, i) => (
                          <option key={i} value={i}>{c.component_name} ({c.line_count?.toLocaleString()} lines)</option>
                        ))}
                      </select>
                    )}
                    <button className="btn btn-ghost btn-sm" onClick={copySource} title="Copy to clipboard">⎘ Copy</button>
                    <button className="btn btn-ghost btn-sm" onClick={downloadSource} title="Download as .abap file">⬇ Download</button>
                  </div>
                  {source.components[selectedComponent] && (
                    <div className="source-viewer">
                      <div className="source-viewer-toolbar">
                        <span style={{ fontSize: "0.6875rem", color: "rgba(255,255,255,0.5)", fontFamily: "var(--font-mono)" }}>
                          {source.components[selectedComponent].component_type}
                        </span>
                        <span style={{ fontSize: "0.6875rem", color: "rgba(255,255,255,0.7)", fontFamily: "var(--font-mono)" }}>
                          {source.components[selectedComponent].component_name}
                        </span>
                        <span className="source-viewer-meta">
                          {isTechDef ? "Technical Definition" : "ABAP Source"} · {source.components[selectedComponent].line_count} lines
                        </span>
                      </div>
                      <div className="source-viewer-content">
                        <div className="source-code">
                          {source.components[selectedComponent].lines.map((line) => (
                            <div key={line.line_no} className="source-line">
                              <span className="source-line-num">{line.line_no}</span>
                              <span className="source-line-text">{line.source_line}</span>
                            </div>
                          ))}
                        </div>
                      </div>
                    </div>
                  )}
                </div>
              )}
            </div>
          )}

          {/* DEPENDENCIES TAB */}
          {activeTab === "dependencies" && (
            <div>
              {depsLoading && <LoadingSpinner message="Loading dependencies…" />}
              {depsError && <ErrorAlert error={depsError} title="Dependencies load failed" onRetry={loadDeps} />}
              {deps && deps.relationships.length === 0 && (
                <div className="empty-state">
                  <div className="empty-state-icon">🔗</div>
                  <div className="empty-state-title">No dependencies detected</div>
                  <div className="empty-state-msg">No dependency relationships were found for this object.</div>
                </div>
              )}
              {deps && deps.relationships.length > 0 && (
                <div>
                  <div style={{ fontSize: "0.75rem", color: "var(--color-text-secondary)", marginBottom: "0.75rem" }}>
                    {deps.returned_relations} relationships
                    {deps.truncated && <span className="truncated-warning" style={{ display: "inline-flex", marginLeft: "0.5rem" }}>⚠ Truncated</span>}
                  </div>
                  <div className="table-wrapper">
                    <table>
                      <thead>
                        <tr>
                          <th>Relationship</th>
                          <th>Target Type</th>
                          <th>Target Object</th>
                          <th>Confidence</th>
                          <th>Component</th>
                          <th>Line</th>
                          <th>Dynamic</th>
                        </tr>
                      </thead>
                      <tbody>
                        {deps.relationships.map((r, i) => (
                          <tr key={i}>
                            <td><RelationshipBadge rel={r.relationship_type} /></td>
                            <td><ObjectTypeBadge type={r.target_object_type} /></td>
                            <td className="mono">{r.target_object_name}</td>
                            <td><ConfidenceBadge confidence={r.confidence} /></td>
                            <td className="mono">{r.source_component ?? "—"}</td>
                            <td>{r.line_no ?? "—"}</td>
                            <td>{r.dynamic ? "⚡ Dynamic" : "—"}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* IMPACT TAB */}
          {activeTab === "impact" && (
            <div>
              <div style={{ display: "flex", gap: "0.5rem", alignItems: "flex-end", marginBottom: "1rem", flexWrap: "wrap" }}>
                <div className="form-field" style={{ flex: 1, minWidth: 160 }}>
                  <label>Scope Package</label>
                  <input
                    className="input"
                    placeholder="Package to search within"
                    value={impactPackage}
                    onChange={(e) => setImpactPackage(e.target.value)}
                  />
                </div>
                <button className="btn btn-primary btn-sm" onClick={loadImpact} disabled={impactLoading || !impactPackage.trim()}>
                  {impactLoading ? <><span className="spinner" /> Analysing…</> : "Analyse Impact"}
                </button>
                <button className="btn btn-secondary btn-sm" onClick={loadReverse} disabled={reverseLoading || !impactPackage.trim()}>
                  {reverseLoading ? <><span className="spinner" /> Searching…</> : "Find Who Uses This"}
                </button>
              </div>

              {impactError && <ErrorAlert error={impactError} title="Impact analysis failed" />}
              {reverseError && <ErrorAlert error={reverseError} title="Reverse lookup failed" />}

              {impact && (
                <div style={{ marginBottom: "1.25rem" }}>
                  <div className="section-title">Impact Analysis</div>
                  <div style={{ display: "flex", gap: "1.5rem", flexWrap: "wrap", marginBottom: "0.75rem" }}>
                    <div><div className="stat-value">{impact.direct_dependent_count}</div><div className="stat-label">Direct dependents</div></div>
                    <div><div className="stat-value">{impact.transitive_dependent_count}</div><div className="stat-label">Transitive dependents</div></div>
                    <div><div className="stat-value">{impact.dynamic_reference_count}</div><div className="stat-label">Dynamic references</div></div>
                  </div>
                  {impact.truncated && <div className="truncated-warning">⚠ Analysis truncated — scope may be too large</div>}
                  {impact.impacted_objects.length > 0 && (
                    <div className="table-wrapper" style={{ marginTop: "0.75rem" }}>
                      <table>
                        <thead><tr><th>Type</th><th>Object</th><th>Relationships</th><th>Confidence</th><th>Dynamic</th></tr></thead>
                        <tbody>
                          {impact.impacted_objects.map((obj, i) => (
                            <tr key={i}>
                              <td><ObjectTypeBadge type={obj.sap_object_type} /></td>
                              <td className="mono">{obj.object_name}</td>
                              <td>{obj.relationship_types.map((rt, j) => <RelationshipBadge key={j} rel={rt} />)}</td>
                              <td><ConfidenceBadge confidence={obj.confidence} /></td>
                              <td>{obj.dynamic ? "⚡" : "—"}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {reverseResult && (
                <div>
                  <div className="section-title">Reverse Dependencies ({reverseResult.matched_dependents} dependents)</div>
                  {reverseResult.truncated && <div className="truncated-warning">⚠ Result truncated</div>}
                  {reverseResult.dependents.length === 0 ? (
                    <div className="empty-state">
                      <div className="empty-state-title">No reverse dependencies found</div>
                      <div className="empty-state-msg">No objects in the selected scope depend on this object.</div>
                    </div>
                  ) : (
                    <div className="table-wrapper">
                      <table>
                        <thead><tr><th>Type</th><th>Object</th><th>Relationships</th><th>Confidence</th></tr></thead>
                        <tbody>
                          {reverseResult.dependents.map((dep, i) => (
                            <tr key={i}>
                              <td><ObjectTypeBadge type={dep.sap_object_type} /></td>
                              <td className="mono">{dep.object_name}</td>
                              <td>{dep.relationship_types.map((rt, j) => <RelationshipBadge key={j} rel={rt} />)}</td>
                              <td><ConfidenceBadge confidence={dep.confidence} /></td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  )}
                </div>
              )}

              {!impact && !reverseResult && !impactLoading && !reverseLoading && (
                <div className="empty-state">
                  <div className="empty-state-icon">📊</div>
                  <div className="empty-state-title">Impact analysis</div>
                  <div className="empty-state-msg">Enter a package scope and click Analyse Impact or Find Who Uses This to run a bounded impact analysis.</div>
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </>
  );
}
