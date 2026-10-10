// ============================================================
// N-CoreIQ Frontend Type Definitions
// Mirrors the backend Pydantic models exactly.
// ============================================================

// -------------------------------------------------------
// Health & Connectivity
// -------------------------------------------------------

export interface HealthResult {
  status: string;
  app: string;
  version: string;
  environment: string;
  job_store?: string | null;
  worker_running?: boolean | null;
  queued_jobs?: number | null;
  running_jobs?: number | null;
}

export interface ConnectionTestResult {
  status: string;
  connection_mode: string;
  system_id?: string | null;
  client?: string | null;
  sap_release?: string | null;
  timestamp?: string | null;
  ping_method: string;
  message: string;
}

// -------------------------------------------------------
// Repository Objects (Phase 3)
// -------------------------------------------------------

export type MatchReason = "PACKAGE" | "NAME_PATTERN" | "BOTH";

export interface RepositoryObject {
  system_id?: string | null;
  client?: string | null;
  pgmid?: string | null;
  sap_object_type: string;
  friendly_type?: string | null;
  object_name: string;
  package?: string | null;
  author?: string | null;
  description?: string | null;
  created_date?: string | null;
  changed_by?: string | null;
  changed_date?: string | null;
  active_status?: string | null;
  match_reason?: MatchReason | null;
  warning?: string | null;
  metadata: Record<string, unknown>;
  source_components: unknown[];
  relationships: unknown[];
}

export interface SearchCriteria {
  packages: string[];
  include_subpackages: boolean;
  name_patterns: string[];
  object_types: string[];
  all_custom_objects: boolean;
  max_results: number;
}

export interface SearchStatistics {
  total_matches: number;
  returned: number;
}

export interface RepositorySearchResponse {
  system_id?: string | null;
  client?: string | null;
  criteria?: SearchCriteria | null;
  statistics?: SearchStatistics | null;
  truncated: boolean;
  message?: string | null;
  objects: RepositoryObject[];
}

export interface RepositorySearchRequest {
  packages?: string[];
  include_subpackages?: boolean;
  name_patterns?: string[];
  object_types?: string[];
  all_custom_objects?: boolean;
  max_results?: number;
}

// -------------------------------------------------------
// Package Resolution
// -------------------------------------------------------

export interface RepositoryPackage {
  package: string;
  parent_package?: string | null;
  description?: string | null;
  depth: number;
}

export interface PackageResolutionResult {
  system_id?: string | null;
  client?: string | null;
  root_package: string;
  include_subpackages: boolean;
  max_depth: number;
  truncated: boolean;
  message?: string | null;
  packages: RepositoryPackage[];
}

// -------------------------------------------------------
// Object Metadata (Phase 4)
// -------------------------------------------------------

export interface RepositoryObjectMetadata {
  sap_object_type: string;
  object_name: string;
  system_id?: string | null;
  client?: string | null;
  pgmid?: string | null;
  package?: string | null;
  object_subtype?: string | null;
  program_name?: string | null;
  function_group?: string | null;
  table_class?: string | null;
  author?: string | null;
  master_language?: string | null;
  source_system?: string | null;
  created_date?: string | null;
  changed_by?: string | null;
  changed_date?: string | null;
  description?: string | null;
  active_status?: string | null;
  found: boolean;
  warning?: string | null;
  message?: string | null;
}

// -------------------------------------------------------
// Object Source (Phase 4)
// -------------------------------------------------------

export interface RepositorySourceLine {
  component_type: string;
  component_name: string;
  line_no: number;
  source_line: string;
}

export interface RepositorySourceComponent {
  component_type: string;
  component_name: string;
  line_count?: number | null;
  lines: RepositorySourceLine[];
  source_text?: string | null;
}

export interface RepositoryObjectSource {
  sap_object_type: string;
  object_name: string;
  system_id?: string | null;
  client?: string | null;
  max_lines: number;
  returned_lines: number;
  component_count: number;
  found: boolean;
  truncated: boolean;
  message?: string | null;
  components: RepositorySourceComponent[];
}

// -------------------------------------------------------
// Dependencies (Phase 8)
// -------------------------------------------------------

export type DependencyRelationshipType =
  | "INCLUDES"
  | "CALLS_PROGRAM"
  | "CALLS_FUNCTION"
  | "USES_CLASS"
  | "IMPLEMENTS_INTERFACE"
  | "INHERITS_CLASS"
  | "READS_TABLE"
  | "WRITES_TABLE"
  | "USES_DATA_ELEMENT"
  | "USES_DOMAIN"
  | "TRANSACTION_TARGET";

export type DependencyConfidence = "EXACT" | "HIGH" | "MEDIUM" | "LOW";

export interface DependencyEvidence {
  evidence_source?: string | null;
  source_component?: string | null;
  line_no?: number | null;
  detail?: string | null;
  confidence: DependencyConfidence;
  dynamic: boolean;
}

export interface RepositoryDependency {
  relation_id?: number | null;
  system_id?: string | null;
  client?: string | null;
  source_object_type: string;
  source_object_name: string;
  target_object_type: string;
  target_object_name: string;
  relationship_type: DependencyRelationshipType;
  evidence_source?: string | null;
  confidence: DependencyConfidence;
  source_component?: string | null;
  line_no?: number | null;
  dynamic: boolean;
  detail?: string | null;
}

export interface DirectDependencyResult {
  system_id?: string | null;
  client?: string | null;
  source_object_type: string;
  source_object_name: string;
  total_relations: number;
  returned_relations: number;
  truncated: boolean;
  message?: string | null;
  relationships: RepositoryDependency[];
}

export interface RepositoryObjectReference {
  sap_object_type: string;
  object_name: string;
  package?: string | null;
  description?: string | null;
}

export interface DependencyEdge {
  source: RepositoryObjectReference;
  target: RepositoryObjectReference;
  relationship_type: DependencyRelationshipType;
  confidence: DependencyConfidence;
  dynamic: boolean;
  evidence: DependencyEvidence[];
}

export interface DependencyNode {
  sap_object_type: string;
  object_name: string;
  depth: number;
}

export interface DependencyGraphEdge {
  source: RepositoryObjectReference;
  target: RepositoryObjectReference;
  relationship_type: DependencyRelationshipType;
  confidence: DependencyConfidence;
  dynamic: boolean;
  evidence: DependencyEvidence[];
  depth: number;
  creates_cycle: boolean;
}

export interface GraphStatistics {
  node_count: number;
  edge_count: number;
  relationship_type_counts: Record<string, number>;
  confidence_counts: Record<string, number>;
  dynamic_edge_count: number;
  cycle_count: number;
  sap_calls: number;
}

export interface DependencyGraph {
  root: RepositoryObjectReference;
  direction: "FORWARD" | "REVERSE";
  requested_max_depth: number;
  depth_reached: number;
  truncated: boolean;
  nodes: DependencyNode[];
  edges: DependencyGraphEdge[];
  cycles_detected: boolean;
  warnings: string[];
  statistics: GraphStatistics;
}

export interface DependencyTraversalRequest {
  root_object: RepositoryObjectReference;
  direction?: "FORWARD" | "REVERSE";
  max_depth?: number;
  relationship_types?: string[] | null;
  min_confidence?: string | null;
  max_nodes?: number;
  max_edges?: number;
  include_dynamic?: boolean;
}

export interface ReverseScopeSpec {
  packages?: string[];
  include_subpackages?: boolean;
  name_patterns?: string[];
  object_types?: string[];
  all_custom_objects?: boolean;
  max_results?: number;
}

export interface ReverseDependencyRequest {
  target_object: RepositoryObjectReference;
  scope: ReverseScopeSpec;
  relationship_types?: string[] | null;
  min_confidence?: string | null;
  include_dynamic?: boolean;
  max_candidates?: number;
}

export interface ReverseDependent {
  sap_object_type: string;
  object_name: string;
  relationship_types: string[];
  confidence: DependencyConfidence;
  dynamic: boolean;
  evidence: DependencyEvidence[];
}

export interface ReverseDependencyResult {
  target_object: RepositoryObjectReference;
  scope_summary: Record<string, unknown>;
  candidate_count: number;
  matched_dependents: number;
  truncated: boolean;
  warnings: string[];
  relationships: DependencyEdge[];
  dependents: ReverseDependent[];
  statistics: GraphStatistics;
}

export interface DependencyImpactRequest {
  object: RepositoryObjectReference;
  scope: ReverseScopeSpec;
  max_depth?: number;
  relationship_types?: string[] | null;
  min_confidence?: string | null;
  max_candidates?: number | null;
  max_nodes?: number | null;
  max_edges?: number | null;
}

export interface DependencyImpactResult {
  target_object: RepositoryObjectReference;
  direct_dependent_count: number;
  transitive_dependent_count: number;
  total_impacted_objects: number;
  relationship_type_counts: Record<string, number>;
  confidence_counts: Record<string, number>;
  dynamic_reference_count: number;
  max_depth_reached: number;
  truncated: boolean;
  warnings: string[];
  impacted_objects: ReverseDependent[];
  edges: DependencyEdge[];
}

// -------------------------------------------------------
// Bulk Extraction (Phase 5)
// -------------------------------------------------------

export interface ObjectKey {
  object_type: string;
  object_name: string;
}

export interface BulkExtractionRequest {
  objects?: ObjectKey[] | null;
  search?: RepositorySearchRequest | null;
  include_metadata?: boolean;
  include_source?: boolean;
  source_max_lines?: number;
  continue_on_error?: boolean;
}

// -------------------------------------------------------
// Extraction Jobs (Phase 6)
// -------------------------------------------------------

export type JobStatus =
  | "QUEUED"
  | "RUNNING"
  | "CANCEL_REQUESTED"
  | "CANCELLED"
  | "SUCCEEDED"
  | "FAILED";

export type JobStage =
  | "VALIDATING"
  | "DISCOVERY"
  | "EXTRACTING"
  | "EXPORTING"
  | "FINALIZING"
  | "COMPLETED";

export type ExportJobFormat = "json" | "zip" | "none";

export interface ExtractionJobProgress {
  total_objects: number;
  processed_objects: number;
  successful_objects: number;
  partial_objects: number;
  failed_objects: number;
  skipped_objects: number;
  truncated_objects: number;
  percent_complete: number;
  current_object_type?: string | null;
  current_object_name?: string | null;
  current_stage?: string | null;
  message?: string | null;
}

export interface JobError {
  code: string;
  message: string;
  stage?: string | null;
  retryable: boolean;
  timestamp: string;
}

export interface JobArtifact {
  artifact_id: string;
  format: ExportJobFormat;
  filename: string;
  content_type: string;
  size_bytes: number;
  sha256: string;
  created_at: string;
  expires_at?: string | null;
}

export interface ExtractionJob {
  job_id: string;
  status: JobStatus;
  stage?: JobStage | null;
  created_at: string;
  started_at?: string | null;
  completed_at?: string | null;
  updated_at: string;
  created_by?: string | null;
  request: unknown;
  progress: ExtractionJobProgress;
  result_status?: string | null;
  error?: JobError | null;
  artifact?: JobArtifact | null;
  retry_of_job_id?: string | null;
  attempt: number;
}

export interface JobListResponse {
  items: ExtractionJob[];
  total: number;
  limit: number;
  offset: number;
}

export interface ExtractionJobRequest {
  extraction: BulkExtractionRequest;
  export_format?: ExportJobFormat;
  idempotency_key?: string | null;
}

export interface CreateJobResponse {
  job_id: string;
  status: JobStatus;
  stage?: string | null;
  created_at?: string | null;
  status_url: string;
}

// -------------------------------------------------------
// UI-specific helper types
// -------------------------------------------------------

export const OBJECT_TYPE_LABELS: Record<string, string> = {
  PROG: "Program (PROG)",
  CLAS: "Class (CLAS)",
  INTF: "Interface (INTF)",
  FUGR: "Function Group (FUGR)",
  FUNC: "Function Module (FUNC)",
  TABL: "Table (TABL)",
  DTEL: "Data Element (DTEL)",
  DOMA: "Domain (DOMA)",
  VIEW: "View (VIEW)",
  SHLP: "Search Help (SHLP)",
  ENQU: "Lock Object (ENQU)",
  TRAN: "Transaction (TRAN)",
  MSAG: "Message Class (MSAG)",
};

export const TECHNICAL_DEFINITION_TYPES = new Set([
  "TABL",
  "DTEL",
  "DOMA",
  "VIEW",
  "SHLP",
  "ENQU",
  "TRAN",
  "MSAG",
]);

export const TERMINAL_JOB_STATUSES: Set<JobStatus> = new Set([
  "CANCELLED",
  "SUCCEEDED",
  "FAILED",
]);

export const CONFIDENCE_COLORS: Record<DependencyConfidence, string> = {
  EXACT: "badge-exact",
  HIGH: "badge-high",
  MEDIUM: "badge-medium",
  LOW: "badge-low",
};

export const JOB_STATUS_COLORS: Record<JobStatus, string> = {
  QUEUED: "badge-queued",
  RUNNING: "badge-running",
  CANCEL_REQUESTED: "badge-cancel-requested",
  CANCELLED: "badge-cancelled",
  SUCCEEDED: "badge-succeeded",
  FAILED: "badge-failed",
};
