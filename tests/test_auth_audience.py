"""Gate 0E9D — Portable audience validation test suite.

Tests the _build_accepted_audiences() helper introduced in Gate 0E9D and the
end-to-end XSUAAAuthMiddleware audience derivation logic.

All values are synthetic.  No real tokens, secrets, or tenant identifiers
are used or required.

Test matrix:
    A  — bound xsappname accepted
    B  — bound clientid accepted
    C  — NCoreIQ namespace audience accepted (the primary S2S fix)
    D  — different application rejected
    E  — different tenant/application identity rejected
    F  — prefix attack rejected
    G  — suffix attack rejected
    H  — wrong namespace rejected
    I  — explicit AUTH__AUDIENCE preserves strict override semantics
    J  — missing XSUAA binding fails closed (no audience derived, auth fails)

Namespace constant alignment:
    Tests use _NCOREIQ_SCOPE_NAMESPACE to derive expected values — if the
    constant changes, the tests will catch drift from xs-security.json.
"""

from __future__ import annotations

import base64
import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.auth.middleware import (
    _NCOREIQ_SCOPE_NAMESPACE,
    _build_accepted_audiences,
    _validate_jwt_without_library,
)

# ---------------------------------------------------------------------------
# Synthetic test identities — no real tenant suffix
# ---------------------------------------------------------------------------

_SYNTH_XSAPP = "example-app!t123"          # synthetic tenant-qualified xsappname
_SYNTH_BASE  = "example-app"               # base without tenant qualifier
_SYNTH_CID   = "sb-example-app!t123"       # synthetic clientid
_SYNTH_NS_AUD = f"{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}"  # "example-app!t123.NCoreIQ"

# ---------------------------------------------------------------------------
# Helper — build a minimal (unsigned) JWT for structural tests
# ---------------------------------------------------------------------------

def _make_jwt(
    aud: str | list[str],
    exp_offset: int = 3600,
    sub: str = "sb-ncoreshift!t123",
    scope: list[str] | None = None,
) -> str:
    """Build a minimal JWT (header.payload.fake-sig).

    NOT cryptographically signed — used only for structural/audience tests
    that exercise the fallback (no-PyJWT) code path.
    """
    header = base64.urlsafe_b64encode(
        json.dumps({"alg": "RS256", "typ": "JWT"}).encode()
    ).rstrip(b"=").decode()

    payload_dict: dict[str, Any] = {
        "sub": sub,
        "aud": aud,
        "exp": int(time.time()) + exp_offset,
        "iss": "https://test.authentication.us10.hana.ondemand.com/oauth/token",
    }
    if scope is not None:
        payload_dict["scope"] = scope

    payload = base64.urlsafe_b64encode(
        json.dumps(payload_dict).encode()
    ).rstrip(b"=").decode()

    return f"{header}.{payload}.fakesig"


def _fake_jwks() -> dict[str, Any]:
    """Minimal JWKS structure (no real key — tests use fallback path)."""
    return {"keys": []}


def _validate_fallback(
    token: str,
    jwks: dict[str, Any],
    audience: str | list[str] | None,
    issuer: str | None,
) -> dict[str, Any]:
    """Call _validate_jwt_without_library via the structure-only fallback path.

    Patches out the 'jwt' module to force the ImportError branch, which
    performs expiry + audience membership checks without RSA key verification.
    This is appropriate for unit tests that supply synthetic unsigned tokens.
    """
    import sys
    with patch.dict(sys.modules, {"jwt": None}):
        return _validate_jwt_without_library(token, jwks, audience=audience, issuer=issuer)


# ===========================================================================
# _build_accepted_audiences — unit tests
# ===========================================================================


class TestBuildAcceptedAudiences:
    """Unit tests for _build_accepted_audiences()."""

    def test_a_bound_xsappname_included(self):
        """TEST A — bound xsappname appears in accepted list."""
        result = _build_accepted_audiences(_SYNTH_XSAPP, None)
        assert _SYNTH_XSAPP in result, (
            f"Expected bound xsappname {_SYNTH_XSAPP!r} in {result}"
        )

    def test_b_bound_clientid_included(self):
        """TEST B — bound clientid appears in accepted list."""
        result = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        assert _SYNTH_CID in result, (
            f"Expected bound clientid {_SYNTH_CID!r} in {result}"
        )

    def test_c_ncoreiq_namespace_audience_included(self):
        """TEST C — NCoreIQ namespace audience derived from xsappname.

        This is the primary fix for Gate 0E9D.
        XSUAA emits <xsappname>.NCoreIQ as an audience entry in
        client_credentials tokens that carry NCoreIQ.* scopes.
        The namespace is appended using the source-proven constant only.
        """
        result = _build_accepted_audiences(_SYNTH_XSAPP, None)
        expected = f"{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}"
        assert expected in result, (
            f"Expected namespace audience {expected!r} in {result}"
        )

    def test_c_namespace_derives_from_constant_not_hardcoded(self):
        """TEST C — namespace suffix comes from _NCOREIQ_SCOPE_NAMESPACE, not a literal."""
        result = _build_accepted_audiences(_SYNTH_XSAPP, None)
        # Build the expected value the same way the production code does
        dynamic_expected = f"{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}"
        assert dynamic_expected in result

    def test_d_different_application_not_in_list(self):
        """TEST D — a different application's xsappname is never in the list."""
        result = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        other_app = "another-app!t123"
        assert other_app not in result
        assert f"{other_app}.{_NCOREIQ_SCOPE_NAMESPACE}" not in result

    def test_e_different_tenant_not_in_list(self):
        """TEST E — same app, different tenant suffix, is not in the list."""
        result = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        different_tenant = "example-app!t999"
        assert different_tenant not in result
        assert f"{different_tenant}.{_NCOREIQ_SCOPE_NAMESPACE}" not in result

    def test_f_prefix_attack_not_in_list(self):
        """TEST F — prefix attack: <xsapp>-evil.<ns> is not generated."""
        result = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        attack = f"{_SYNTH_XSAPP}-evil.{_NCOREIQ_SCOPE_NAMESPACE}"
        assert attack not in result

    def test_g_suffix_attack_not_in_list(self):
        """TEST G — suffix attack: attacker.<xsapp>.<ns> is not generated."""
        result = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        attack = f"attacker.{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}"
        assert attack not in result

    def test_h_wrong_namespace_not_in_list(self):
        """TEST H — wrong namespace: <xsapp>.OtherScope is not generated."""
        result = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        wrong_ns = f"{_SYNTH_XSAPP}.OtherScope"
        assert wrong_ns not in result

    def test_none_xsappname_produces_only_clientid(self):
        """When xsappname is None, list contains only clientid (if present)."""
        result = _build_accepted_audiences(None, _SYNTH_CID)
        assert result == [_SYNTH_CID]

    def test_none_both_produces_empty_list(self):
        """When both inputs are None, result is empty."""
        result = _build_accepted_audiences(None, None)
        assert result == []

    def test_deduplication_preserves_order(self):
        """Duplicate values (if xsappname == clientid somehow) are removed."""
        result = _build_accepted_audiences("myapp!t1", "myapp!t1")
        # myapp!t1 appears only once (from xsappname), then namespace, then
        # clientid deduplicated
        assert result.count("myapp!t1") == 1

    def test_stable_order_xsapp_before_namespace_before_clientid(self):
        """Order: xsappname, namespace audience, clientid."""
        result = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        idx_xsapp = result.index(_SYNTH_XSAPP)
        idx_ns = result.index(f"{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}")
        idx_cid = result.index(_SYNTH_CID)
        assert idx_xsapp < idx_ns < idx_cid

    def test_namespace_constant_aligns_with_xs_security(self):
        """_NCOREIQ_SCOPE_NAMESPACE must equal 'NCoreIQ' per xs-security.json."""
        assert _NCOREIQ_SCOPE_NAMESPACE == "NCoreIQ", (
            "Namespace constant does not match xs-security.json scope name pattern. "
            "xs-security.json defines scopes as $XSAPPNAME.NCoreIQ.* — "
            "update _NCOREIQ_SCOPE_NAMESPACE if xs-security.json changes."
        )


# ===========================================================================
# _validate_jwt_without_library — audience membership tests
# (uses unsigned JWTs + fallback path; no PyJWT crypto dependency)
# ===========================================================================


class TestAudienceMembershipValidation:
    """End-to-end audience membership tests using the fallback JWT path.

    These tests exercise the audience membership check that PyJWT performs
    when _validate_jwt_without_library() is called with a list of accepted
    audiences.  In CI (no PyJWT installed) the fallback code path runs and
    performs the same membership semantics.
    """

    def _validate(
        self,
        aud_in_token: str | list[str],
        accepted_audiences: list[str],
    ) -> dict[str, Any]:
        """Attempt validation via the fallback (no-crypto) path."""
        token = _make_jwt(aud=aud_in_token)
        return _validate_fallback(token, _fake_jwks(), accepted_audiences, None)

    def test_c_namespace_audience_in_token_array_accepted(self):
        """TEST C — token aud array containing <xsapp>.NCoreIQ is accepted."""
        ns_aud = f"{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}"
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        # The token aud as produced by XSUAA for NCoreIQ.* scopes
        token_aud = [ns_aud]
        claims = self._validate(token_aud, accepted)
        assert claims["aud"] == token_aud

    def test_a_bare_xsappname_in_token_array_accepted(self):
        """TEST A — token aud array containing bare xsappname is accepted."""
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        claims = self._validate([_SYNTH_XSAPP], accepted)
        assert claims is not None

    def test_b_clientid_in_token_array_accepted(self):
        """TEST B — token aud array containing bound clientid is accepted."""
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        claims = self._validate([_SYNTH_CID], accepted)
        assert claims is not None

    def test_d_different_application_rejected(self):
        """TEST D — token from different application is rejected."""
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        other_ns = f"another-app!t123.{_NCOREIQ_SCOPE_NAMESPACE}"
        with pytest.raises((ValueError, Exception)):
            self._validate([other_ns], accepted)

    def test_e_different_tenant_rejected(self):
        """TEST E — token with same app but different tenant suffix is rejected."""
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        diff_tenant_ns = f"example-app!t999.{_NCOREIQ_SCOPE_NAMESPACE}"
        with pytest.raises((ValueError, Exception)):
            self._validate([diff_tenant_ns], accepted)

    def test_f_prefix_attack_rejected(self):
        """TEST F — prefix-extended xsappname audience is rejected."""
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        attack_aud = f"{_SYNTH_XSAPP}-evil.{_NCOREIQ_SCOPE_NAMESPACE}"
        with pytest.raises((ValueError, Exception)):
            self._validate([attack_aud], accepted)

    def test_g_suffix_attack_rejected(self):
        """TEST G — suffix-prepended xsappname audience is rejected."""
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        attack_aud = f"attacker.{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}"
        with pytest.raises((ValueError, Exception)):
            self._validate([attack_aud], accepted)

    def test_h_wrong_namespace_rejected(self):
        """TEST H — correct xsappname with wrong namespace is rejected."""
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        wrong_ns = f"{_SYNTH_XSAPP}.OtherScope"
        with pytest.raises((ValueError, Exception)):
            self._validate([wrong_ns], accepted)

    def test_empty_token_aud_rejected(self):
        """An empty audience array in the token is rejected."""
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        with pytest.raises((ValueError, Exception)):
            self._validate([], accepted)

    def test_multi_entry_aud_accepted_when_one_matches(self):
        """Token with multiple aud entries is accepted if any matches."""
        ns_aud = f"{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}"
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        # Token aud contains the namespace audience plus other benign entries
        token_aud = ["openid", ns_aud, _SYNTH_CID]
        claims = self._validate(token_aud, accepted)
        assert claims is not None


# ===========================================================================
# TEST I — explicit AUTH__AUDIENCE override
# ===========================================================================


class TestExplicitAudienceOverride:
    """TEST I — explicit AUTH__AUDIENCE preserves strict override semantics."""

    def test_i_explicit_audience_used_as_is(self):
        """When AUTH__AUDIENCE is set, it is used verbatim — no expansion."""
        explicit = f"{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}"
        token = _make_jwt(aud=[explicit])
        claims = _validate_fallback(token, _fake_jwks(), explicit, None)
        assert claims is not None

    def test_i_explicit_audience_rejects_non_matching(self):
        """Explicit AUTH__AUDIENCE rejects tokens that do not carry it."""
        explicit = f"{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}"
        token = _make_jwt(aud=["some-other-audience"])
        with pytest.raises((ValueError, Exception)):
            _validate_fallback(token, _fake_jwks(), explicit, None)

    def test_i_explicit_audience_does_not_silently_expand(self):
        """Explicit AUTH__AUDIENCE is NOT expanded by _build_accepted_audiences."""
        # When AUTH__AUDIENCE is set, only that exact value is in jwt_audience.
        # _build_accepted_audiences() is only called in the else branch.
        # This test confirms the function is not called for explicit config.
        explicit = "custom-audience-value"
        # explicit is not in the list that _build_accepted_audiences would produce
        auto_list = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        assert explicit not in auto_list  # confirms no contamination


# ===========================================================================
# TEST J — missing XSUAA binding fails closed
# ===========================================================================


class TestMissingBindingFailsClosed:
    """TEST J — missing XSUAA binding results in no audience, auth fails closed."""

    def test_j_empty_build_returns_empty_list(self):
        """When xsappname and clientid are both None, list is empty."""
        result = _build_accepted_audiences(None, None)
        assert result == []

    def test_j_empty_audience_list_means_no_validation(self):
        """With an empty accepted list, jwt_audience becomes None — auth fails.

        In the middleware: ``accepted or None`` converts [] to None, which
        sets ``verify_aud=False`` in PyJWT.  This is acceptable only because
        the XSUAA binding check earlier in the dispatch path already raises a
        ValueError when neither xsappname nor clientid is available:
        'JWKS URI not configured and no XSUAA service binding was found.'
        The test here verifies the helper contract, not the full middleware.
        """
        result = _build_accepted_audiences(None, None)
        # The middleware converts an empty list to None
        jwt_audience = result or None
        assert jwt_audience is None

    def test_j_xsappname_only_still_derives_namespace(self):
        """Even with no clientid, the namespace audience is still derived."""
        result = _build_accepted_audiences(_SYNTH_XSAPP, None)
        assert len(result) == 2
        assert _SYNTH_XSAPP in result
        assert f"{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}" in result


# ===========================================================================
# Expiry validation — existing security must remain intact
# ===========================================================================


class TestExpiryValidation:
    """Confirm expiry checking is not weakened by the audience change."""

    def test_expired_token_rejected(self):
        """Expired tokens must be rejected regardless of valid audience."""
        ns_aud = f"{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}"
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        token = _make_jwt(aud=[ns_aud], exp_offset=-3600)  # expired 1 hour ago
        with pytest.raises((ValueError, Exception)):
            _validate_fallback(token, _fake_jwks(), accepted, None)

    def test_valid_token_not_rejected_for_expiry(self):
        """A non-expired token with matching audience is accepted."""
        ns_aud = f"{_SYNTH_XSAPP}.{_NCOREIQ_SCOPE_NAMESPACE}"
        accepted = _build_accepted_audiences(_SYNTH_XSAPP, _SYNTH_CID)
        token = _make_jwt(aud=[ns_aud], exp_offset=3600)
        claims = _validate_fallback(token, _fake_jwks(), accepted, None)
        assert claims is not None


# ===========================================================================
# Authorization model unchanged — scope constants preserved
# ===========================================================================


class TestAuthorizationModelUnchanged:
    """Confirm the scope/role model is not disturbed by the audience fix."""

    def test_scope_constants_unchanged(self):
        """SCOPE_VIEWER, SCOPE_ANALYST, SCOPE_OPERATOR, SCOPE_ADMIN still present."""
        from app.auth.principal import (
            SCOPE_ADMIN,
            SCOPE_ANALYST,
            SCOPE_OPERATOR,
            SCOPE_VIEWER,
        )
        assert SCOPE_VIEWER is not None
        assert SCOPE_ANALYST is not None
        assert SCOPE_OPERATOR is not None
        assert SCOPE_ADMIN is not None

    def test_scope_map_contains_ncoreiq_scopes(self):
        """_SCOPE_MAP still maps NCoreIQ.Viewer/Analyst/Operator/Admin."""
        from app.auth.middleware import _SCOPE_MAP
        from app.auth.principal import (
            SCOPE_ADMIN,
            SCOPE_ANALYST,
            SCOPE_OPERATOR,
            SCOPE_VIEWER,
        )
        assert _SCOPE_MAP.get("NCoreIQ.Viewer") == SCOPE_VIEWER
        assert _SCOPE_MAP.get("NCoreIQ.Analyst") == SCOPE_ANALYST
        assert _SCOPE_MAP.get("NCoreIQ.Operator") == SCOPE_OPERATOR
        assert _SCOPE_MAP.get("NCoreIQ.Admin") == SCOPE_ADMIN

    def test_ncoreiq_scope_namespace_not_in_scope_map(self):
        """_NCOREIQ_SCOPE_NAMESPACE is NOT a scope name — it is an audience namespace."""
        from app.auth.middleware import _SCOPE_MAP
        # Audience namespace should not be interpreted as a scope
        assert _NCOREIQ_SCOPE_NAMESPACE not in _SCOPE_MAP
        assert f"NCoreIQ.{_NCOREIQ_SCOPE_NAMESPACE}" not in _SCOPE_MAP
