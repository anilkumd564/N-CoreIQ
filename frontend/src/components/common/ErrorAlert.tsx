import type { ApiError } from "../../api/client";
import { isApiError } from "../../api/client";

interface ErrorAlertProps {
  error: unknown;
  title?: string;
  onRetry?: () => void;
}

function getErrorMessage(error: unknown): string {
  if (isApiError(error)) {
    if (error.isNetworkError) {
      return "Unable to reach the N-CoreIQ server. Check that the backend is running.";
    }
    if (error.status === 401 || error.status === 403) {
      return "Authentication or authorisation failure. Check your credentials.";
    }
    if (error.status === 404) {
      return `Not found: ${error.detail}`;
    }
    if (error.status === 422) {
      return `Invalid request: ${error.detail}`;
    }
    if (error.status === 503) {
      return `SAP system unavailable: ${error.detail}`;
    }
    return error.detail;
  }
  if (error instanceof Error) return error.message;
  return String(error);
}

export function ErrorAlert({ error, title = "Error", onRetry }: ErrorAlertProps) {
  const message = getErrorMessage(error);
  return (
    <div className="alert alert-error" role="alert">
      <span className="alert-icon">⚠</span>
      <div className="alert-body">
        <div className="alert-title">{title}</div>
        <div>{message}</div>
        {onRetry && (
          <button className="btn btn-secondary btn-sm" style={{ marginTop: 8 }} onClick={onRetry}>
            Retry
          </button>
        )}
      </div>
    </div>
  );
}

export function EmptyState({
  icon = "📭",
  title,
  message,
}: {
  icon?: string;
  title: string;
  message?: string;
}) {
  return (
    <div className="empty-state">
      <div className="empty-state-icon">{icon}</div>
      <div className="empty-state-title">{title}</div>
      {message && <div className="empty-state-msg">{message}</div>}
    </div>
  );
}
