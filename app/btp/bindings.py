"""Phase 9 — BTP Service Binding Resolver.

Discovers service bindings from:
1. VCAP_SERVICES environment variable (Cloud Foundry standard)
2. Mounted service binding files (Kubernetes / BTP runtime)
3. Explicit environment variable configuration (local BTP simulation)

Security:
- Raw credential dictionaries are NEVER exposed to business services.
- Secret values are masked in all log output.
- Binding parsing failures produce descriptive errors, not silent fallback.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Canonical service label names used in VCAP_SERVICES
_XSUAA_LABELS = ("xsuaa", "UAA", "authorization")
_DESTINATION_LABELS = ("destination",)
_CONNECTIVITY_LABELS = ("connectivity",)

# Secret field names — masked in all log output
_SECRET_FIELDS = frozenset(
    {
        "password",
        "token",
        "authorization",
        "secret",
        "client_secret",
        "clientsecret",
        "private_key",
        "privatekey",
        "credentials",
        "servicekey",
        "verificationkey",
        "certificate",
        "key",
    }
)


def _mask_secrets(data: dict[str, Any], depth: int = 0) -> dict[str, Any]:
    """Return a copy of data with secret values replaced by '***'."""
    if depth > 5:
        return {"...": "..."}
    result: dict[str, Any] = {}
    for k, v in data.items():
        if k.lower() in _SECRET_FIELDS:
            result[k] = "***" if v else v
        elif isinstance(v, dict):
            result[k] = _mask_secrets(v, depth + 1)
        else:
            result[k] = v
    return result


@dataclass
class ServiceBindingCredentials:
    """Normalized, validated service binding credential bag.

    Never exposed to business services as a raw dict.
    Access individual fields via typed accessors.
    """

    service_name: str
    label: str
    credentials: dict[str, Any] = field(default_factory=dict)

    def get(self, key: str, default: Any = None) -> Any:
        """Get a credential value. Returns default if not present."""
        return self.credentials.get(key, default)

    def require(self, key: str) -> str:
        """Get a required credential value. Raises ValueError if absent."""
        value = self.credentials.get(key)
        if not value:
            raise ValueError(
                f"BTP service binding '{self.service_name}' is missing required "
                f"credential field '{key}'. "
                "Verify the service is correctly bound in the BTP subaccount."
            )
        return str(value)

    def safe_repr(self) -> dict[str, Any]:
        """Return credential dict with secrets masked. Safe for logging."""
        return _mask_secrets(self.credentials)

    def __repr__(self) -> str:  # pragma: no cover
        return (
            f"ServiceBindingCredentials(service_name={self.service_name!r}, "
            f"label={self.label!r}, credentials={self.safe_repr()})"
        )


class BTPServiceBindingResolver:
    """Discovers and validates BTP service bindings.

    Resolution order:
    1. VCAP_SERVICES JSON (Cloud Foundry)
    2. Service binding mounted files (cf-service-operator / Kyma)
    3. Explicit environment variable configuration already present in Settings

    Usage:
        resolver = BTPServiceBindingResolver()
        xsuaa = resolver.get_xsuaa()
        dest  = resolver.get_destination()
        conn  = resolver.get_connectivity()
    """

    def __init__(self) -> None:
        self._vcap: dict[str, list[dict]] = self._parse_vcap_services()

    # ------------------------------------------------------------------
    # VCAP_SERVICES parsing
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_vcap_services() -> dict[str, list[dict]]:
        """Parse VCAP_SERVICES env var. Returns empty dict if not set."""
        raw = os.environ.get("VCAP_SERVICES", "")
        if not raw:
            return {}
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, dict):
                logger.warning("btp_vcap_services_not_a_dict")
                return {}
            return parsed
        except json.JSONDecodeError as exc:
            logger.warning("btp_vcap_services_parse_error error=%s", exc)
            return {}

    def _find_binding(self, candidate_labels: tuple[str, ...]) -> ServiceBindingCredentials | None:
        """Find the first matching service binding from VCAP_SERVICES."""
        for label in candidate_labels:
            instances = self._vcap.get(label, [])
            if instances:
                inst = instances[0]
                creds = inst.get("credentials", {})
                service_name = inst.get("name", label)
                logger.info(
                    "btp_binding_found service=%s label=%s",
                    service_name,
                    label,
                    extra={"operation": "btp_binding_resolve"},
                )
                return ServiceBindingCredentials(
                    service_name=service_name,
                    label=label,
                    credentials=creds,
                )
        return None

    # ------------------------------------------------------------------
    # Public accessors
    # ------------------------------------------------------------------

    def get_xsuaa(self) -> ServiceBindingCredentials | None:
        """Return XSUAA binding credentials, or None if not bound."""
        binding = self._find_binding(_XSUAA_LABELS)
        if binding:
            logger.debug(
                "btp_xsuaa_binding service=%s safe=%s",
                binding.service_name,
                binding.safe_repr(),
            )
        return binding

    def get_destination(self) -> ServiceBindingCredentials | None:
        """Return Destination Service binding credentials, or None if not bound."""
        return self._find_binding(_DESTINATION_LABELS)

    def get_connectivity(self) -> ServiceBindingCredentials | None:
        """Return Connectivity Service binding credentials, or None if not bound."""
        return self._find_binding(_CONNECTIVITY_LABELS)

    def validate_btp_mode(self) -> list[str]:
        """Validate required BTP bindings are present. Returns list of missing services."""
        missing: list[str] = []
        if not self.get_xsuaa():
            missing.append("xsuaa (XSUAA / Authorization and Trust Management)")
        if not self.get_destination():
            missing.append("destination (SAP Destination Service)")
        if not self.get_connectivity():
            missing.append("connectivity (SAP Connectivity Service)")
        return missing

    def diagnostics(self) -> dict[str, Any]:
        """Return safe diagnostics. Never exposes credentials."""
        result: dict[str, Any] = {}
        for label, candidates in [
            ("xsuaa", _XSUAA_LABELS),
            ("destination", _DESTINATION_LABELS),
            ("connectivity", _CONNECTIVITY_LABELS),
        ]:
            binding = self._find_binding(candidates)
            if binding:
                result[label] = {
                    "bound": True,
                    "service_name": binding.service_name,
                    "label": binding.label,
                    "credential_keys": list(binding.credentials.keys()),
                }
            else:
                result[label] = {"bound": False}
        return result
