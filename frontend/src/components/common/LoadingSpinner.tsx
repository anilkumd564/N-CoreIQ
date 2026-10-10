interface LoadingSpinnerProps {
  message?: string;
  size?: "sm" | "lg";
}

export function LoadingSpinner({ message, size = "sm" }: LoadingSpinnerProps) {
  return (
    <div className="loading-block" role="status" aria-live="polite">
      <span className={`spinner${size === "lg" ? " spinner-lg" : ""}`} aria-hidden="true" />
      {message && <span>{message}</span>}
    </div>
  );
}

export function InlineSpinner() {
  return <span className="spinner" aria-hidden="true" style={{ verticalAlign: "middle" }} />;
}
