# claude code changed: new file — Advanced Quant Research Capability
# Architecture. Tests the centralized entitlement service (section 18:
# free user denied Pro, active Pro user allowed operational Pro, expired
# subscription denied, unknown plan denied, backend enforcement works
# without UI). Section 10's four-concept separation gets its own class —
# visibility/entitlement/operational-readiness/policy are independent.

from datetime import timedelta

from django.contrib.auth.models import AnonymousUser, User
from django.test import TestCase
from django.utils import timezone

from bot.research_lab.entitlements import (
    ResearchEntitlementService, UNKNOWN_CAPABILITY, AUTHENTICATION_REQUIRED,
    ENGINE_NOT_READY, SUBSCRIPTION_REQUIRED, SUBSCRIPTION_EXPIRED, OK,
    ASSET_CLASS_NOT_SUPPORTED,
)
from bot.research_lab.models import ResearchSubscription


class GetUserTierTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="tieruser", password="x")

    def test_no_subscription_row_is_core(self):
        self.assertEqual(ResearchEntitlementService.get_user_tier(self.user), "CORE")

    def test_active_pro_no_expiry_is_pro(self):
        ResearchSubscription.objects.create(user=self.user, tier="PRO", status="ACTIVE", expires_at=None)
        self.assertEqual(ResearchEntitlementService.get_user_tier(self.user), "PRO")

    def test_active_pro_with_future_expiry_is_pro(self):
        ResearchSubscription.objects.create(user=self.user, tier="PRO", status="ACTIVE", expires_at=timezone.now() + timedelta(days=30))
        self.assertEqual(ResearchEntitlementService.get_user_tier(self.user), "PRO")

    def test_active_pro_with_past_expiry_is_core(self):
        """claude code changed: the exact 'expired subscription denied'
        case — a row that still says tier=PRO, status=ACTIVE but whose
        expires_at has passed must not grant PRO. The tier field alone is
        never trusted."""
        ResearchSubscription.objects.create(user=self.user, tier="PRO", status="ACTIVE", expires_at=timezone.now() - timedelta(days=1))
        self.assertEqual(ResearchEntitlementService.get_user_tier(self.user), "CORE")

    def test_canceled_pro_is_core(self):
        ResearchSubscription.objects.create(user=self.user, tier="PRO", status="CANCELED")
        self.assertEqual(ResearchEntitlementService.get_user_tier(self.user), "CORE")

    def test_expired_status_pro_is_core(self):
        ResearchSubscription.objects.create(user=self.user, tier="PRO", status="EXPIRED")
        self.assertEqual(ResearchEntitlementService.get_user_tier(self.user), "CORE")

    def test_anonymous_user_is_core(self):
        self.assertEqual(ResearchEntitlementService.get_user_tier(AnonymousUser()), "CORE")


class CanAccessTest(TestCase):

    def setUp(self):
        self.free_user = User.objects.create_user(username="free1", password="x")
        self.pro_user = User.objects.create_user(username="pro1", password="x")
        ResearchSubscription.objects.create(user=self.pro_user, tier="PRO", status="ACTIVE", expires_at=None)

    def test_unknown_capability_fails_closed(self):
        result = ResearchEntitlementService.can_access(self.pro_user, "not_a_real_capability")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, UNKNOWN_CAPABILITY)

    def test_anonymous_user_is_denied(self):
        result = ResearchEntitlementService.can_access(AnonymousUser(), "continuous_feature_research")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, AUTHENTICATION_REQUIRED)

    def test_free_user_allowed_a_core_capability(self):
        result = ResearchEntitlementService.can_access(self.free_user, "continuous_feature_research")
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason_code, OK)

    def test_free_user_denied_a_ready_pro_capability(self):
        result = ResearchEntitlementService.can_access(self.free_user, "cointegration_pairs_research")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, SUBSCRIPTION_REQUIRED)

    def test_active_pro_user_allowed_a_ready_pro_capability(self):
        result = ResearchEntitlementService.can_access(self.pro_user, "cointegration_pairs_research")
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason_code, OK)

    def test_pro_user_still_denied_an_unready_pro_capability(self):
        """claude code changed: the exact section 10/13 rule — 'a paywall
        must never become a mechanism for bypassing research-quality
        gates'. An active Pro subscriber must NOT be granted access to a
        capability whose engine isn't operationally ready, even though
        they've paid for the tier."""
        result = ResearchEntitlementService.can_access(self.pro_user, "kalman_dynamic_hedge_ratio")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, ENGINE_NOT_READY)

    def test_free_user_denied_an_unready_pro_capability_for_the_same_reason_not_subscription(self):
        """claude code changed: verifies the two axes are independent —
        a free user hitting an unready PRO capability is denied for
        ENGINE_NOT_READY, the same reason a Pro subscriber would be, not
        SUBSCRIPTION_REQUIRED (checked second, on purpose)."""
        result = ResearchEntitlementService.can_access(self.free_user, "kalman_dynamic_hedge_ratio")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, ENGINE_NOT_READY)

    def test_expired_pro_subscription_denied(self):
        expired_user = User.objects.create_user(username="expired1", password="x")
        ResearchSubscription.objects.create(user=expired_user, tier="PRO", status="ACTIVE", expires_at=timezone.now() - timedelta(days=1))
        result = ResearchEntitlementService.can_access(expired_user, "cointegration_pairs_research")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, SUBSCRIPTION_EXPIRED)

    def test_canceled_pro_subscription_denied(self):
        canceled_user = User.objects.create_user(username="canceled1", password="x")
        ResearchSubscription.objects.create(user=canceled_user, tier="PRO", status="CANCELED")
        result = ResearchEntitlementService.can_access(canceled_user, "cointegration_pairs_research")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, SUBSCRIPTION_EXPIRED)


class CapabilityUiStateTest(TestCase):
    """claude code changed: section 10/11's four-state badge, independent
    of can_access()'s single verdict — these prove BOTH axes are reported
    correctly, including the case section 10 explicitly calls out by
    example: Pro subscription + untested engine -> 'unavailable', not
    'locked'."""

    def setUp(self):
        self.free_user = User.objects.create_user(username="free2", password="x")
        self.pro_user = User.objects.create_user(username="pro2", password="x")
        ResearchSubscription.objects.create(user=self.pro_user, tier="PRO", status="ACTIVE", expires_at=None)

    def test_core_capability_is_available_for_any_user(self):
        self.assertEqual(ResearchEntitlementService.capability_ui_state(self.free_user, "continuous_feature_research")["badge"], "AVAILABLE")

    def test_not_implemented_capability_is_coming_soon_regardless_of_subscription(self):
        # claude code changed: was "cross_sectional_research" — that
        # capability was promoted to IMPLEMENTED_AND_READY earlier this
        # session (bot/research_lab/capability_registry.py) once
        # run_cross_sectional_oos.py made it real and tested; it correctly
        # shows LOCKED/AVAILABLE now, not COMING_SOON (see
        # test_free_user_sees_locked_for_a_ready_pro_capability's sibling
        # coverage of that transition). Swapped to feature_stability_research,
        # which is still genuinely NOT_IMPLEMENTED.
        self.assertEqual(ResearchEntitlementService.capability_ui_state(self.free_user, "feature_stability_research")["badge"], "COMING_SOON")
        self.assertEqual(ResearchEntitlementService.capability_ui_state(self.pro_user, "feature_stability_research")["badge"], "COMING_SOON")

    def test_pro_subscription_plus_unready_engine_is_unavailable_not_locked(self):
        """The exact section 10 example: 'Subscription: PRO but engine
        status: IMPLEMENTED_BUT_NEEDS_TESTS -> unavailable'."""
        state = ResearchEntitlementService.capability_ui_state(self.pro_user, "kalman_dynamic_hedge_ratio")
        self.assertEqual(state["badge"], "UNAVAILABLE")

    def test_free_user_sees_locked_for_a_ready_pro_capability(self):
        state = ResearchEntitlementService.capability_ui_state(self.free_user, "cointegration_pairs_research")
        self.assertEqual(state["badge"], "LOCKED")

    def test_pro_user_sees_available_for_a_ready_pro_capability(self):
        state = ResearchEntitlementService.capability_ui_state(self.pro_user, "cointegration_pairs_research")
        self.assertEqual(state["badge"], "AVAILABLE")


class AssetClassGatingTest(TestCase):
    # claude code changed: new — Forex Integration Forensic Verification,
    # Phase 10. Proves CRYPTO capability != automatically FOREX capability
    # (the mission's own explicit requirement): a capability is only
    # Forex-available when capability_registry.py's supported_asset_classes
    # explicitly says so, never inferred from "it works for crypto."
    # Covers: Forex-allowed capability, Forex-blocked capability,
    # crypto-only capability still working for CRYPTO, an unsupported
    # asset-class VALUE entirely (not just an unsupported capability), and
    # confirms omitting asset_class preserves every pre-Forex caller's
    # exact prior behavior (regression guard for the wiring fix in
    # orchestrator.py/formalize.py).

    def setUp(self):
        self.pro_user = User.objects.create_user(username="assetclasspro", password="x")
        ResearchSubscription.objects.create(user=self.pro_user, tier="PRO", status="ACTIVE", expires_at=None)

    def test_forex_allowed_capability(self):
        result = ResearchEntitlementService.can_access(self.pro_user, "cointegration_pairs_research", asset_class="FOREX")
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason_code, OK)

    def test_forex_blocked_capability(self):
        """cross_sectional_research is deliberately NOT marked FOREX-supported
        (its Research Lab tool has no asset-class parameter at all — see
        capability_registry.py's own comment on that entry) — this must be
        rejected at the entitlement layer, not silently allowed through."""
        result = ResearchEntitlementService.can_access(self.pro_user, "cross_sectional_research", asset_class="FOREX")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, ASSET_CLASS_NOT_SUPPORTED)

    def test_crypto_only_capability_still_works_for_crypto(self):
        result = ResearchEntitlementService.can_access(self.pro_user, "cross_sectional_research", asset_class="CRYPTO")
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason_code, OK)

    def test_unsupported_asset_class_value_is_rejected(self):
        """Not just an unsupported CAPABILITY — an asset_class value that
        isn't even a real registered asset class at all must still fail
        closed, never silently pass through."""
        result = ResearchEntitlementService.can_access(self.pro_user, "cointegration_pairs_research", asset_class="COMMODITIES")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, ASSET_CLASS_NOT_SUPPORTED)

    def test_omitting_asset_class_preserves_legacy_behavior(self):
        """Every caller that predates the Forex integration never passes
        asset_class — this must be completely unaffected."""
        result = ResearchEntitlementService.can_access(self.pro_user, "cointegration_pairs_research")
        self.assertTrue(result.allowed)
        self.assertEqual(result.reason_code, OK)

    def test_ui_state_reflects_the_same_asset_class_gate(self):
        """capability_ui_state() (the display-layer twin of can_access())
        must reach the same verdict, not a looser one — the exact
        'hiding a button is not authorization' principle applied to
        asset-class gating instead of subscription gating."""
        state = ResearchEntitlementService.capability_ui_state(self.pro_user, "cross_sectional_research", asset_class="FOREX")
        self.assertEqual(state["badge"], "UNAVAILABLE")
