"""Phase 3 — Request validation tests.

Covers all RepositorySearchRequest validation rules:
  - empty request rejected (safety rule)
  - package-only request allowed
  - pattern-only request allowed
  - all_custom_objects=true allowed
  - multiple packages normalised
  - duplicate packages removed
  - lowercase package converted to uppercase
  - duplicate name patterns removed
  - invalid/suspicious name patterns rejected
  - unsupported object type rejected
  - empty object_types accepted
  - max_results validation
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.models.requests import RepositorySearchRequest


# ---------------------------------------------------------------------------
# Safety rule – blank request must be rejected
# ---------------------------------------------------------------------------


def test_empty_request_rejected():
    """No packages, no patterns, no all_custom_objects → 422-equivalent ValueError."""
    with pytest.raises(ValidationError) as exc_info:
        RepositorySearchRequest(packages=[], name_patterns=[], object_types=[])
    errors = exc_info.value.errors()
    messages = " ".join(str(e["msg"]) for e in errors)
    assert "at least one package" in messages.lower() or "all_custom_objects" in messages.lower()


def test_all_fields_empty_and_all_custom_objects_false_rejected():
    """Explicit all_custom_objects=false with no other selectors is rejected."""
    with pytest.raises(ValidationError):
        RepositorySearchRequest(
            packages=[],
            name_patterns=[],
            object_types=[],
            all_custom_objects=False,
        )


# ---------------------------------------------------------------------------
# Valid selector combinations
# ---------------------------------------------------------------------------


def test_package_only_request_allowed():
    req = RepositorySearchRequest(packages=["ZFINANCE"])
    assert req.packages == ["ZFINANCE"]
    assert req.name_patterns == []
    assert not req.all_custom_objects


def test_pattern_only_request_allowed():
    req = RepositorySearchRequest(name_patterns=["Z*"])
    assert req.name_patterns == ["Z*"]
    assert req.packages == []


def test_all_custom_objects_true_allowed_with_no_other_selectors():
    req = RepositorySearchRequest(all_custom_objects=True)
    assert req.all_custom_objects is True
    assert req.packages == []
    assert req.name_patterns == []


def test_package_and_pattern_combined_allowed():
    req = RepositorySearchRequest(packages=["ZFINANCE"], name_patterns=["ZFI*"])
    assert "ZFINANCE" in req.packages
    assert "ZFI*" in req.name_patterns


# ---------------------------------------------------------------------------
# Package normalisation
# ---------------------------------------------------------------------------


def test_multiple_packages_normalised():
    req = RepositorySearchRequest(
        packages=["zfinance", "ZFINANCE", " ZFI_UTIL "]
    )
    assert req.packages == ["ZFINANCE", "ZFI_UTIL"]


def test_duplicate_packages_removed():
    req = RepositorySearchRequest(packages=["ZPAY", "ZPAY", "ZPAY"])
    assert req.packages == ["ZPAY"]


def test_lowercase_package_converted_to_uppercase():
    req = RepositorySearchRequest(packages=["zpayments"])
    assert req.packages == ["ZPAYMENTS"]


def test_blank_package_entries_dropped():
    req = RepositorySearchRequest(packages=["ZFINANCE", "  ", ""])
    assert req.packages == ["ZFINANCE"]


def test_all_blank_packages_treated_as_empty():
    """All-blank package list → empty after normalisation → safety rule fires."""
    with pytest.raises(ValidationError):
        RepositorySearchRequest(packages=["  ", ""], name_patterns=[])


# ---------------------------------------------------------------------------
# Name-pattern normalisation
# ---------------------------------------------------------------------------


def test_name_patterns_uppercased():
    req = RepositorySearchRequest(name_patterns=["z*", "y*"])
    assert req.name_patterns == ["Z*", "Y*"]


def test_duplicate_name_patterns_removed():
    req = RepositorySearchRequest(name_patterns=["Z*", "z*", "Z*"])
    assert req.name_patterns == ["Z*"]


def test_specific_patterns_normalised():
    req = RepositorySearchRequest(name_patterns=["zfi*", "ZCL_*", "y*"])
    assert req.name_patterns == ["ZFI*", "ZCL_*", "Y*"]


def test_invalid_pattern_semicolon_rejected():
    with pytest.raises(ValidationError) as exc_info:
        RepositorySearchRequest(name_patterns=["Z*; DROP TABLE TADIR --"])
    messages = " ".join(str(e["msg"]) for e in exc_info.value.errors())
    assert "unsupported" in messages.lower() or "invalid" in messages.lower()


def test_invalid_pattern_sql_select_rejected():
    with pytest.raises(ValidationError):
        RepositorySearchRequest(name_patterns=["SELECT * FROM TADIR"])


def test_invalid_pattern_single_quote_rejected():
    with pytest.raises(ValidationError):
        RepositorySearchRequest(name_patterns=["Z' OR '1'='1"])


def test_valid_sap_wildcard_plus_accepted():
    req = RepositorySearchRequest(name_patterns=["Z+FI*"])
    assert "Z+FI*" in req.name_patterns


# ---------------------------------------------------------------------------
# Object-type validation
# ---------------------------------------------------------------------------


def test_supported_object_types_accepted():
    req = RepositorySearchRequest(
        packages=["ZTEST"],
        object_types=["PROG", "CLAS", "INTF", "FUGR", "TABL"],
    )
    assert req.object_types == ["PROG", "CLAS", "INTF", "FUGR", "TABL"]


def test_object_types_uppercased():
    req = RepositorySearchRequest(packages=["ZTEST"], object_types=["prog", "clas"])
    assert req.object_types == ["PROG", "CLAS"]


def test_duplicate_object_types_removed():
    req = RepositorySearchRequest(packages=["ZTEST"], object_types=["PROG", "prog", "PROG"])
    assert req.object_types == ["PROG"]


def test_unsupported_object_type_rejected():
    with pytest.raises(ValidationError) as exc_info:
        RepositorySearchRequest(packages=["ZTEST"], object_types=["ZZZUNK"])
    messages = " ".join(str(e["msg"]) for e in exc_info.value.errors())
    assert "unsupported" in messages.lower() or "object type" in messages.lower()


def test_empty_object_types_accepted():
    """Empty object_types is valid – the service will use all Phase 3 types."""
    req = RepositorySearchRequest(packages=["ZTEST"], object_types=[])
    assert req.object_types == []


def test_all_phase3_object_types_accepted():
    from app.models.object_types import PHASE3_OBJECT_TYPES

    req = RepositorySearchRequest(
        packages=["ZTEST"],
        object_types=sorted(PHASE3_OBJECT_TYPES),
    )
    assert set(req.object_types) == PHASE3_OBJECT_TYPES


# ---------------------------------------------------------------------------
# max_results validation
# ---------------------------------------------------------------------------


def test_default_max_results_is_1000():
    req = RepositorySearchRequest(packages=["ZTEST"])
    assert req.max_results == 1000


def test_max_results_minimum_is_1():
    req = RepositorySearchRequest(packages=["ZTEST"], max_results=1)
    assert req.max_results == 1


def test_max_results_at_hard_limit():
    req = RepositorySearchRequest(packages=["ZTEST"], max_results=10_000)
    assert req.max_results == 10_000


def test_max_results_above_hard_limit_rejected():
    with pytest.raises(ValidationError):
        RepositorySearchRequest(packages=["ZTEST"], max_results=10_001)


def test_max_results_zero_rejected():
    with pytest.raises(ValidationError):
        RepositorySearchRequest(packages=["ZTEST"], max_results=0)


# ---------------------------------------------------------------------------
# all_custom_objects semantics
# ---------------------------------------------------------------------------


def test_all_custom_objects_and_packages_can_coexist():
    req = RepositorySearchRequest(packages=["ZFINANCE"], all_custom_objects=True)
    assert req.all_custom_objects is True
    assert req.packages == ["ZFINANCE"]


def test_all_custom_objects_and_patterns_can_coexist():
    req = RepositorySearchRequest(name_patterns=["ZFI*"], all_custom_objects=True)
    assert req.all_custom_objects is True
    assert "ZFI*" in req.name_patterns


# ---------------------------------------------------------------------------
# include_subpackages default
# ---------------------------------------------------------------------------


def test_include_subpackages_defaults_to_false():
    req = RepositorySearchRequest(packages=["ZTEST"])
    assert req.include_subpackages is False


def test_include_subpackages_can_be_set_true():
    req = RepositorySearchRequest(packages=["ZTEST"], include_subpackages=True)
    assert req.include_subpackages is True
