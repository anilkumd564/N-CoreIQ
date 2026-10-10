/**
 * N-CoreIQ component tests.
 * Uses React Testing Library + Vitest + jsdom.
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { BrowserRouter, MemoryRouter } from "react-router-dom";
import React from "react";

import { StatusBadge, JobStatusBadge, ConfidenceBadge, ObjectTypeBadge } from "../components/common/StatusBadge";
import { LoadingSpinner } from "../components/common/LoadingSpinner";
import { ErrorAlert, EmptyState } from "../components/common/ErrorAlert";
import type { JobStatus, DependencyConfidence } from "../types";

// -----------------------------------------------------------------------
// StatusBadge
// -----------------------------------------------------------------------

describe("StatusBadge", () => {
  it("renders with default variant", () => {
    render(<StatusBadge label="Test" />);
    expect(screen.getByText("Test")).toBeInTheDocument();
  });

  it("renders with success variant", () => {
    const { container } = render(<StatusBadge label="OK" variant="success" />);
    expect(container.firstChild).toHaveClass("badge-success");
  });

  it("renders with error variant", () => {
    const { container } = render(<StatusBadge label="Error" variant="error" />);
    expect(container.firstChild).toHaveClass("badge-error");
  });
});

describe("JobStatusBadge", () => {
  const statuses: JobStatus[] = ["QUEUED", "RUNNING", "CANCEL_REQUESTED", "CANCELLED", "SUCCEEDED", "FAILED"];

  statuses.forEach((status) => {
    it(`renders ${status} badge`, () => {
      render(<JobStatusBadge status={status} />);
      // Badge should contain the status text (with underscores replaced)
      expect(screen.getByText(status.replace("_", " "))).toBeInTheDocument();
    });
  });
});

describe("ConfidenceBadge", () => {
  const confidences: DependencyConfidence[] = ["EXACT", "HIGH", "MEDIUM", "LOW"];

  confidences.forEach((c) => {
    it(`renders ${c} confidence badge`, () => {
      render(<ConfidenceBadge confidence={c} />);
      expect(screen.getByText(c)).toBeInTheDocument();
    });
  });
});

describe("ObjectTypeBadge", () => {
  it("renders object type", () => {
    render(<ObjectTypeBadge type="PROG" />);
    expect(screen.getByText("PROG")).toBeInTheDocument();
  });

  it("renders TABL type", () => {
    render(<ObjectTypeBadge type="TABL" />);
    expect(screen.getByText("TABL")).toBeInTheDocument();
  });
});

// -----------------------------------------------------------------------
// LoadingSpinner
// -----------------------------------------------------------------------

describe("LoadingSpinner", () => {
  it("renders with message", () => {
    render(<LoadingSpinner message="Loading data…" />);
    expect(screen.getByText("Loading data…")).toBeInTheDocument();
  });

  it("renders without message", () => {
    const { container } = render(<LoadingSpinner />);
    expect(container.querySelector(".spinner")).toBeInTheDocument();
  });

  it("has aria role status", () => {
    render(<LoadingSpinner message="Loading…" />);
    expect(screen.getByRole("status")).toBeInTheDocument();
  });
});

// -----------------------------------------------------------------------
// ErrorAlert
// -----------------------------------------------------------------------

describe("ErrorAlert", () => {
  it("renders error message", () => {
    const err = { status: 503, detail: "SAP unavailable" };
    render(<ErrorAlert error={err} title="Connection Error" />);
    expect(screen.getByText("Connection Error")).toBeInTheDocument();
    expect(screen.getByText(/SAP unavailable/)).toBeInTheDocument();
  });

  it("renders network error message", () => {
    const err = { status: 0, detail: "Network error", isNetworkError: true };
    render(<ErrorAlert error={err} />);
    expect(screen.getByText(/unable to reach/i)).toBeInTheDocument();
  });

  it("renders auth error message", () => {
    const err = { status: 401, detail: "Unauthorized" };
    render(<ErrorAlert error={err} />);
    expect(screen.getByText(/Authentication or authorisation/i)).toBeInTheDocument();
  });

  it("renders retry button when onRetry provided", () => {
    const onRetry = vi.fn();
    render(<ErrorAlert error={{ status: 503, detail: "Error" }} onRetry={onRetry} />);
    const btn = screen.getByText("Retry");
    fireEvent.click(btn);
    expect(onRetry).toHaveBeenCalledOnce();
  });

  it("does not render retry button when no onRetry", () => {
    render(<ErrorAlert error={{ status: 503, detail: "Error" }} />);
    expect(screen.queryByText("Retry")).not.toBeInTheDocument();
  });
});

describe("EmptyState", () => {
  it("renders title and message", () => {
    render(<EmptyState title="No results" message="Try a different search." />);
    expect(screen.getByText("No results")).toBeInTheDocument();
    expect(screen.getByText("Try a different search.")).toBeInTheDocument();
  });

  it("renders custom icon", () => {
    render(<EmptyState icon="🔍" title="Empty" />);
    expect(screen.getByText("🔍")).toBeInTheDocument();
  });
});

// -----------------------------------------------------------------------
// App routing smoke test
// -----------------------------------------------------------------------

describe("App routing", () => {
  it("renders without crashing at /", async () => {
    // Mock fetch to prevent actual API calls
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("mocked")));
    const { default: App } = await import("../App");
    const { container } = render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>
    );
    expect(container).toBeTruthy();
    vi.restoreAllMocks();
  });
});

// -----------------------------------------------------------------------
// Type utilities
// -----------------------------------------------------------------------

describe("TECHNICAL_DEFINITION_TYPES", () => {
  it("contains expected DDIC types", async () => {
    const { TECHNICAL_DEFINITION_TYPES } = await import("../types");
    expect(TECHNICAL_DEFINITION_TYPES.has("TABL")).toBe(true);
    expect(TECHNICAL_DEFINITION_TYPES.has("DTEL")).toBe(true);
    expect(TECHNICAL_DEFINITION_TYPES.has("DOMA")).toBe(true);
    expect(TECHNICAL_DEFINITION_TYPES.has("PROG")).toBe(false);
    expect(TECHNICAL_DEFINITION_TYPES.has("CLAS")).toBe(false);
  });
});

describe("OBJECT_TYPE_LABELS", () => {
  it("has human-readable labels for all supported types", async () => {
    const { OBJECT_TYPE_LABELS } = await import("../types");
    expect(OBJECT_TYPE_LABELS["PROG"]).toContain("Program");
    expect(OBJECT_TYPE_LABELS["CLAS"]).toContain("Class");
    expect(OBJECT_TYPE_LABELS["TABL"]).toContain("Table");
  });
});

describe("TERMINAL_JOB_STATUSES", () => {
  it("contains terminal statuses", async () => {
    const { TERMINAL_JOB_STATUSES } = await import("../types");
    expect(TERMINAL_JOB_STATUSES.has("SUCCEEDED")).toBe(true);
    expect(TERMINAL_JOB_STATUSES.has("FAILED")).toBe(true);
    expect(TERMINAL_JOB_STATUSES.has("CANCELLED")).toBe(true);
    expect(TERMINAL_JOB_STATUSES.has("RUNNING")).toBe(false);
    expect(TERMINAL_JOB_STATUSES.has("QUEUED")).toBe(false);
  });
});
