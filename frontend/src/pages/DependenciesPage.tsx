import { useState, useCallback } from "react";
import { traverseDependencies } from "../api/client";
import type { DependencyGraph, DependencyTraversalRequest } from "../types";
import { OBJECT_TYPE_LABELS } from "../types";
import { LoadingSpinner } from "../components/common/LoadingSpinner";
import { ErrorAlert } from "../components/common/ErrorAlert";
import { ObjectTypeBadge, ConfidenceBadge, RelationshipBadge } from "../components/common/StatusBadge";

const ALL_TYPES = Object.keys(OBJECT_TYPE_LABELS);

export function DependenciesPage() {
  const [objectType, setObjectType] = useState("PROG");
  const [objectName, setObjectName] = useState("");
  const [maxDepth, setMaxDepth] = useState(1);
  const [maxNodes, setMaxNodes] = useState(100);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<DependencyGraph | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [selectedNode, setSelectedNode] = useState<string | null>(null);

  const handleTraverse = useCallback(async () => {
    if (!objectName.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    const req: DependencyTraversalRequest = {
      root_object: { sap_object_type: objectType, object_name: objectName.trim().toUpperCase() },
      max_depth: maxDepth,
      max_nodes: maxNodes,
      max_edges: 250,
      include_dynamic: true,
    };
    try {
      const graph = await traverseDependencies(req);
      setResult(graph);
    } catch (e) {
      setError(e);
    } finally {
      setLoading(false);
    }
  }, [objectType, objectName, maxDepth, maxNodes]);

  return (
    <div>
      <div style={{ marginBottom: "1.25rem" }}>
        <h1 className="page-title">Dependency Graph</h1>
        <p className="page-subtitle">Forward dependency traversal — bounded BFS from a root object</p>
      </div>

      <div className="card" style={{ marginBottom: "1rem" }}>
        <div className="card-header"><span className="card-title">Traversal Parameters</span></div>
        <div className="form-grid" style={{ marginBottom: "1rem" }}>
          <div className="form-field">
            <label>Object Type</label>
            <select className="select" value={objectType} onChange={(e) => setObjectType(e.target.value)}>
              {ALL_TYPES.map((t) => <option key={t} value={t}>{OBJECT_TYPE_LABELS[t]}</option>)}
            </select>
          </div>
          <div className="form-field">
            <label>Object Name</label>
            <input
              className="input"
              placeholder="e.g. ZFI_REPORT"
              value={objectName}
              onChange={(e) => setObjectName(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleTraverse()}
            />
          </div>
          <div className="form-field">
            <label>Depth (1–5)</label>
            <select className="select" value={maxDepth} onChange={(e) => setMaxDepth(Number(e.target.value))}>
              {[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
          <div className="form-field">
            <label>Max Nodes</label>
            <select className="select" value={maxNodes} onChange={(e) => setMaxNodes(Number(e.target.value))}>
              {[50, 100, 200, 500].map((n) => <option key={n} value={n}>{n}</option>)}
            </select>
          </div>
        </div>
        <div className="alert alert-warning" style={{ marginBottom: "0.75rem" }}>
          <span className="alert-icon">⚠</span>
          <div className="alert-body">Start with depth 1. Large graphs may be slow to load and render.</div>
        </div>
        <button className="btn btn-primary" onClick={handleTraverse} disabled={loading || !objectName.trim()}>
          {loading ? <><span className="spinner" /> Traversing…</> : "Traverse Dependencies"}
        </button>
      </div>

      {error && <ErrorAlert error={error} title="Traversal failed" onRetry={handleTraverse} />}

      {result && (
        <div>
          <div className="card" style={{ marginBottom: "1rem" }}>
            <div className="card-header">
              <span className="card-title">
                Graph Result
                <span className="badge badge-info" style={{ marginLeft: "0.5rem" }}>{result.statistics.node_count} nodes</span>
                <span className="badge badge-default" style={{ marginLeft: "0.25rem" }}>{result.statistics.edge_count} edges</span>
              </span>
              {result.truncated && <span className="badge badge-warning">Truncated</span>}
            </div>
            <div className="detail-grid" style={{ marginBottom: "0.75rem" }}>
              <span className="detail-label">Root</span>
              <span className="detail-value mono">{result.root.sap_object_type} / {result.root.object_name}</span>
              <span className="detail-label">Depth Reached</span>
              <span className="detail-value">{result.depth_reached} / {result.requested_max_depth}</span>
              <span className="detail-label">Dynamic Edges</span>
              <span className="detail-value">{result.statistics.dynamic_edge_count}</span>
              {result.cycles_detected && <><span className="detail-label">Cycles</span><span className="detail-value text-error">{result.statistics.cycle_count} detected</span></>}
            </div>
            {result.warnings.map((w, i) => (
              <div key={i} className="alert alert-warning" style={{ marginBottom: "0.5rem" }}>
                <span className="alert-icon">⚠</span>
                <div className="alert-body">{w}</div>
              </div>
            ))}
          </div>

          {/* Node list */}
          <div className="card" style={{ marginBottom: "1rem" }}>
            <div className="card-header"><span className="card-title">Nodes ({result.nodes.length})</span></div>
            <div className="table-wrapper">
              <table>
                <thead>
                  <tr><th>Type</th><th>Name</th><th>Depth</th></tr>
                </thead>
                <tbody>
                  {result.nodes.map((n, i) => (
                    <tr
                      key={i}
                      className={`clickable${selectedNode === `${n.sap_object_type}::${n.object_name}` ? " selected" : ""}`}
                      onClick={() => setSelectedNode(`${n.sap_object_type}::${n.object_name}`)}
                    >
                      <td><ObjectTypeBadge type={n.sap_object_type} /></td>
                      <td className="mono">{n.object_name}</td>
                      <td>{n.depth === 0 ? <span className="badge badge-info">root</span> : n.depth}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          {/* Edge list */}
          <div className="card">
            <div className="card-header"><span className="card-title">Edges ({result.edges.length})</span></div>
            <div className="table-wrapper">
              <table>
                <thead>
                  <tr><th>From</th><th>Relationship</th><th>To</th><th>Confidence</th><th>Dynamic</th><th>Cycle</th></tr>
                </thead>
                <tbody>
                  {result.edges.map((e, i) => (
                    <tr key={i}>
                      <td className="mono">{e.source.object_name}</td>
                      <td><RelationshipBadge rel={e.relationship_type} /></td>
                      <td className="mono">{e.target.object_name}</td>
                      <td><ConfidenceBadge confidence={e.confidence} /></td>
                      <td>{e.dynamic ? "⚡" : "—"}</td>
                      <td>{e.creates_cycle ? <span className="text-error">⟳ Cycle</span> : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>
      )}

      {!result && !loading && !error && (
        <div className="empty-state">
          <div className="empty-state-icon">🕸</div>
          <div className="empty-state-title">No graph loaded</div>
          <div className="empty-state-msg">Enter an object type and name, then click Traverse Dependencies.</div>
        </div>
      )}
    </div>
  );
}
