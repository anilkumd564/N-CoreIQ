"""Phase 9 — AuthenticatedPrincipal model.

Application services consume this typed model — they NEVER receive raw
JWT claim dictionaries or XSUAA token objects.

Roles / Scopes defined for N-CoreIQ:
    NCoreIQ.Viewer    — read-only: ping, packages, search, metadata, source, dependencies
    NCoreIQ.Analyst   — Viewer + traversal, reverse lookup, impact analysis, source export,
                        extraction job creation, job status polling, result retrieval, artifact
                        download (the full N-CoreShift evidence workflow)
    NCoreIQ.Operator  — Analyst + job cancellation and retry (operational control only)
    NCoreIQ.Admin     — Operator + diagnostics, configuration visibility, admin operations

Canonical job-scope decision (Gate 0E10-R4-A1):
    xs-security.json NCoreIQ.Analyst description: "all Viewer capabilities plus dependency
    traversal, reverse analysis, impact analysis, source export, job management"

    consumer-contract-v1.md Section 4 text: "NCoreIQ.Analyst — additionally required for
    traversal, reverse analysis, impact analysis, and extraction jobs"

    consumer-contract-v1.md Section 11: "Assign 'NCoreIQ Analyst' role collection to the
    N-CoreShift service instance"

    Therefore: create_job, list_jobs, get_job, get_job_result, download_artifact → ANALYST
               cancel_job, retry_job → OPERATOR (operational control, not required by N-CoreShift)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Canonical scope / role constants
# ---------------------------------------------------------------------------

SCOPE_VIEWER = "NCoreIQ.Viewer"
SCOPE_ANALYST = "NCoreIQ.Analyst"
SCOPE_OPERATOR = "NCoreIQ.Operator"
SCOPE_ADMIN = "NCoreIQ.Admin"

# Ordered from least to most privileged
_ROLE_HIERARCHY = [SCOPE_VIEWER, SCOPE_ANALYST, SCOPE_OPERATOR, SCOPE_ADMIN]

# Viewer permissions (all Viewer capabilities are available to higher roles too)
VIEWER_ENDPOINTS = frozenset(
    {
        "sap_ping",
        "health",
        "health_live",
        "health_ready",
        "search_repository",
        "get_packages",
        "get_object_metadata",
        "get_object_source",
        "get_object_dependencies",
    }
)

# Analyst = Viewer + analysis operations + the full N-CoreShift job workflow
# (create job, poll status, retrieve result, download artifact, list jobs)
ANALYST_ENDPOINTS = VIEWER_ENDPOINTS | frozenset(
    {
        "traverse_dependencies",
        "get_reverse_dependencies",
        "get_impact_analysis",
        "bulk_extract",
        "bulk_export",
        "source_export",
        # Extraction job workflow — required by N-CoreShift (Viewer + Analyst identity)
        "create_job",
        "list_jobs",
        "get_job",
        "get_job_result",
        "download_artifact",
    }
)

# Operator = Analyst + operational job control (NOT required by N-CoreShift)
OPERATOR_ENDPOINTS = ANALYST_ENDPOINTS | frozenset(
    {
        "cancel_job",
        "retry_job",
    }
)

ADMIN_ENDPOINTS = OPERATOR_ENDPOINTS | frozenset(
    {
        "diagnostics",
        "configuration",
        "health_diagnostics",
    }
)

_SCOPE_ENDPOINTS: dict[str, frozenset[str]] = {
    SCOPE_VIEWER: VIEWER_ENDPOINTS,
    SCOPE_ANALYST: ANALYST_ENDPOINTS,
    SCOPE_OPERATOR: OPERATOR_ENDPOINTS,
    SCOPE_ADMIN: ADMIN_ENDPOINTS,
}


# ---------------------------------------------------------------------------
# Principal model
# ---------------------------------------------------------------------------


@dataclass
class AuthenticatedPrincipal:
    """Represents an authenticated and authorized N-CoreIQ user.

    Populated from a validated XSUAA JWT (or synthesized for local/disabled mode).
    Application services receive this object, NEVER the raw JWT.

    Fields:
        subject       — JWT 'sub' claim (user ID or client ID for technical users)
        display_name  — human-friendly display name (from 'user_name' or 'sub')
        email         — optional email address
        scopes        — list of granted scopes from the JWT
        roles         — mapped N-CoreIQ role names (derived from scopes)
        tenant        — BTP tenant ID / subaccount ID
        is_service    — True when authenticated as a technical service (no user_name)
    """

    subject: str
    display_name: str
    email: str | None = None
    scopes: list[str] = field(default_factory=list)
    roles: list[str] = field(default_factory=list)
    tenant: str | None = None
    is_service: bool = False

    # ------------------------------------------------------------------
    # Authorization helpers
    # ------------------------------------------------------------------

    def has_scope(self, scope: str) -> bool:
        """Return True if the principal has the specified scope."""
        return scope in self.scopes

    def has_any_scope(self, *scopes: str) -> bool:
        """Return True if the principal has at least one of the specified scopes."""
        return any(s in self.scopes for s in scopes)

    def has_all_scopes(self, *scopes: str) -> bool:
        """Return True if the principal has all of the specified scopes."""
        return all(s in self.scopes for s in scopes)

    def can_access(self, endpoint_name: str) -> bool:
        """Return True if the principal is authorized to access the endpoint."""
        for scope, allowed in _SCOPE_ENDPOINTS.items():
            if scope in self.scopes and endpoint_name in allowed:
                return True
        return False

    def highest_role(self) -> str | None:
        """Return the highest N-CoreIQ role the principal has."""
        for role in reversed(_ROLE_HIERARCHY):
            if role in self.scopes:
                return role
        return None

    def is_viewer(self) -> bool:
        return self.has_any_scope(SCOPE_VIEWER, SCOPE_ANALYST, SCOPE_OPERATOR, SCOPE_ADMIN)

    def is_analyst(self) -> bool:
        return self.has_any_scope(SCOPE_ANALYST, SCOPE_OPERATOR, SCOPE_ADMIN)

    def is_operator(self) -> bool:
        return self.has_any_scope(SCOPE_OPERATOR, SCOPE_ADMIN)

    def is_admin(self) -> bool:
        return self.has_scope(SCOPE_ADMIN)

    def safe_repr(self) -> dict[str, Any]:
        """Safe representation for logging. NEVER includes credentials."""
        return {
            "subject": self.subject,
            "display_name": self.display_name,
            "email": self.email,
            "scopes": self.scopes,
            "roles": self.roles,
            "tenant": self.tenant,
            "is_service": self.is_service,
        }


# ---------------------------------------------------------------------------
# Anonymous principal (used in disabled auth mode)
# ---------------------------------------------------------------------------

ANONYMOUS_PRINCIPAL = AuthenticatedPrincipal(
    subject="anonymous",
    display_name="Anonymous",
    scopes=[SCOPE_VIEWER, SCOPE_ANALYST, SCOPE_OPERATOR, SCOPE_ADMIN],
    roles=[SCOPE_VIEWER, SCOPE_ANALYST, SCOPE_OPERATOR, SCOPE_ADMIN],
    is_service=False,
)


# ---------------------------------------------------------------------------
# TEST/DEMO ONLY — MCP anonymous principal.
#
# Used exclusively by XSUAAAuthMiddleware when NCOREIQ_MCP_AUTH_MODE=none
# and the request path is a valid MCP path (/mcp, /mcp/, /mcp/*).
#
# Deliberately scoped to Viewer + Analyst only — sufficient for all 15
# registered MCP tools (search, metadata, source, dependency intelligence,
# and the full extraction-job workflow: create/list/get/result/download).
# Does NOT include Operator (cancel/retry job) or Admin (diagnostics,
# configuration) capabilities, which are not part of the intended
# repository-intelligence MCP use case and must not be silently exposed
# through an anonymous MCP endpoint.
# ---------------------------------------------------------------------------

MCP_ANONYMOUS_PRINCIPAL = AuthenticatedPrincipal(
    subject="mcp-anonymous",
    display_name="MCP Anonymous (TEST/DEMO)",
    scopes=[SCOPE_VIEWER, SCOPE_ANALYST],
    roles=[SCOPE_VIEWER, SCOPE_ANALYST],
    is_service=True,
)
