import type {
  DependencyConfidence,
  JobStatus,
} from "../../types";

interface StatusBadgeProps {
  label: string;
  variant?:
    | "default"
    | "info"
    | "success"
    | "warning"
    | "error"
    | "exact"
    | "high"
    | "medium"
    | "low"
    | "queued"
    | "running"
    | "cancel-requested"
    | "cancelled"
    | "succeeded"
    | "failed"
    | "rel"
    | "obj-type";
  dot?: boolean;
}

export function StatusBadge({ label, variant = "default", dot }: StatusBadgeProps) {
  return (
    <span className={`badge badge-${variant}`}>
      {dot && <span style={{ width: 6, height: 6, borderRadius: "50%", background: "currentColor", display: "inline-block" }} />}
      {label}
    </span>
  );
}

export function ConfidenceBadge({ confidence }: { confidence: DependencyConfidence }) {
  const variantMap: Record<DependencyConfidence, StatusBadgeProps["variant"]> = {
    EXACT: "exact",
    HIGH: "high",
    MEDIUM: "medium",
    LOW: "low",
  };
  return <StatusBadge label={confidence} variant={variantMap[confidence]} />;
}

export function JobStatusBadge({ status }: { status: JobStatus }) {
  const variantMap: Record<JobStatus, StatusBadgeProps["variant"]> = {
    QUEUED: "queued",
    RUNNING: "running",
    CANCEL_REQUESTED: "cancel-requested",
    CANCELLED: "cancelled",
    SUCCEEDED: "succeeded",
    FAILED: "failed",
  };
  return <StatusBadge label={status.replace("_", " ")} variant={variantMap[status]} />;
}

export function ObjectTypeBadge({ type }: { type: string }) {
  return <span className="badge-obj-type">{type}</span>;
}

export function RelationshipBadge({ rel }: { rel: string }) {
  return (
    <span className="badge badge-rel">
      {rel.replace(/_/g, " ")}
    </span>
  );
}
