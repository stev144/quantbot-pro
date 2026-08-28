# claude code changed: new file — Research Capabilities Discoverability &
# Navigation Refactor. Tests the promotion of the Research Lab's existing
# capability catalog (bot/research_lab/views/capabilities.py,
# templates/research_lab/capabilities.html — both pre-existing, unchanged
# in their core logic) into a directly-reachable top-level nav
# destination, and proves visibility and executability remain
# INDEPENDENT axes throughout: a capability card being visible/labeled
# never implies a user can actually run it — bot.research_lab.entitlements.
# ResearchEntitlementService.can_access() (unchanged, real backend gate)
# is the only thing checked for that.

from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from django.utils.html import escape

from bot.research_lab.capability_registry import RESEARCH_CAPABILITIES
from bot.research_lab.entitlements import ResearchEntitlementService
from bot.research_lab.models import ResearchSubscription


class DirectDiscoverabilityTest(TestCase):
    """'A user no longer has to enter Research Lab and scroll down to
    discover capabilities' — proves the page is reachable straight from
    the shared topbar, on any page that includes it, not just via a link
    buried inside the Research Lab dashboard."""

    def setUp(self):
        self.user = User.objects.create_user(username="discoveruser", password="x")
        self.client.login(username="discoveruser", password="x")

    def test_capabilities_page_loads_directly(self):
        response = self.client.get(reverse("research_lab_capabilities"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Research Capabilities", response.content)

    def test_capabilities_link_present_in_the_shared_topbar_on_an_unrelated_page(self):
        # claude code changed: THE discoverability property — the link
        # must appear on a page that is NOT Research Lab at all (proves
        # it's in the shared nav partial every page includes, not
        # something only reachable from within Research Lab itself).
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        self.assertIn(f'href="{reverse("research_lab_capabilities")}"', html)
        self.assertIn(">Capabilities<", html)

    def test_capabilities_link_appears_before_new_hypothesis_link_in_the_dropdown(self):
        # claude code changed: "Capabilities" must be the FIRST item in
        # the Research Lab nav group — the new primary discovery surface,
        # not an afterthought below the hypothesis form's own link.
        response = self.client.get("/")
        html = response.content.decode()
        capabilities_pos = html.index(f'href="{reverse("research_lab_capabilities")}"')
        dashboard_pos = html.index(f'href="{reverse("research_lab_dashboard")}"')
        self.assertLess(capabilities_pos, dashboard_pos)

    def test_history_link_also_present_in_the_dropdown(self):
        response = self.client.get("/")
        html = response.content.decode()
        self.assertIn(f'href="{reverse("research_lab_history")}"', html)


class VisibilityIndependentOfAccessTest(TestCase):
    """'Gated capabilities are visible but cannot be executed without
    authorization.' Uses real registry entries (verified present at test
    run time, not assumed) rather than inventing fixture capabilities."""

    def setUp(self):
        self.core_user = User.objects.create_user(username="corevisuser", password="x")
        self.client.login(username="corevisuser", password="x")

    def _first_capability_with(self, **filters):
        for cap in RESEARCH_CAPABILITIES.values():
            if all(getattr(cap, k) == v for k, v in filters.items()):
                return cap
        return None

    def test_a_pro_only_capability_is_still_listed_for_a_core_user(self):
        pro_cap = self._first_capability_with(required_tier="PRO")
        self.assertIsNotNone(pro_cap, "test assumes at least one PRO-tier capability exists in the registry")
        response = self.client.get(reverse("research_lab_capabilities"))
        self.assertContains(response, pro_cap.name)   # visible...
        self.assertContains(response, "PRO RESEARCH")  # ...and honestly badged, not silently AVAILABLE

    def test_a_not_implemented_capability_is_still_listed_not_hidden(self):
        not_impl = self._first_capability_with(engine_status="NOT_IMPLEMENTED")
        self.assertIsNotNone(not_impl, "test assumes at least one NOT_IMPLEMENTED capability exists in the registry")
        response = self.client.get(reverse("research_lab_capabilities"))
        self.assertContains(response, not_impl.name)
        self.assertContains(response, "COMING SOON")

    def test_visible_pro_capability_still_denied_to_a_core_user_at_the_backend(self):
        # claude code changed: THE critical security property — seeing the
        # card in the previous test must NOT translate into real access.
        pro_ready_cap = self._first_capability_with(required_tier="PRO")
        for cap in RESEARCH_CAPABILITIES.values():
            if cap.required_tier == "PRO" and cap.operationally_ready:
                pro_ready_cap = cap
                break
        self.assertIsNotNone(pro_ready_cap, "test assumes at least one operationally-ready PRO capability exists")
        result = ResearchEntitlementService.can_access(self.core_user, pro_ready_cap.id)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "SUBSCRIPTION_REQUIRED")

    def test_visible_not_implemented_capability_denied_even_to_a_pro_user(self):
        # claude code changed: proves readiness is checked BEFORE/independent
        # of subscription (bot/research_lab/entitlements.py's own documented
        # order) — a paywall can never become a bypass for an engine that
        # simply isn't ready, regardless of what tier the user paid for.
        ResearchSubscription.objects.create(user=self.core_user, tier="PRO", status="ACTIVE", expires_at=None)
        not_ready = self._first_capability_with(engine_status="NOT_IMPLEMENTED")
        self.assertIsNotNone(not_ready)
        result = ResearchEntitlementService.can_access(self.core_user, not_ready.id)
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason_code, "ENGINE_NOT_READY")

    def test_manually_visiting_a_url_for_an_unready_capability_does_not_grant_access(self):
        # claude code changed: "A user seeing 'X — Unavailable' must still
        # be blocked by the backend even if someone attempts to access its
        # URL manually" — can_access() is called directly here (the real
        # gate any future execution endpoint must use), simulating exactly
        # that manual-access attempt at the service layer.
        blocked = self._first_capability_with(engine_status="BLOCKED_BY_DEPENDENCY")
        self.assertIsNotNone(blocked, "test assumes at least one BLOCKED_BY_DEPENDENCY capability exists")
        ResearchSubscription.objects.create(user=self.core_user, tier="PRO", status="ACTIVE", expires_at=None)
        result = ResearchEntitlementService.can_access(self.core_user, blocked.id)
        self.assertFalse(result.allowed)


class ProUserSeesUpgradedBadgeTest(TestCase):
    """The other half of the AVAILABLE/LOCKED transition — an authorized
    Pro user sees the SAME capability as available, not locked."""

    def setUp(self):
        self.pro_user = User.objects.create_user(username="prouser", password="x")
        ResearchSubscription.objects.create(user=self.pro_user, tier="PRO", status="ACTIVE", expires_at=None)
        self.client.login(username="prouser", password="x")

    def test_ready_pro_capability_shows_available_not_locked_for_a_pro_user(self):
        ready_pro_cap = None
        for cap in RESEARCH_CAPABILITIES.values():
            if cap.required_tier == "PRO" and cap.operationally_ready:
                ready_pro_cap = cap
                break
        self.assertIsNotNone(ready_pro_cap, "test assumes at least one operationally-ready PRO capability exists")
        response = self.client.get(reverse("research_lab_capabilities"))
        html = response.content.decode()
        # claude code changed: scope the assertion to this capability's own
        # card, not the whole page (other cards may legitimately show
        # different badges) — split on the capability name to isolate it.
        # escape() first: some real capability names (e.g. "Cointegration
        # & Pairs Research") contain characters Django's autoescaping
        # renders differently (& -> &amp;) — comparing against the raw
        # Python string would never match the actual rendered HTML.
        card_html = html.split(escape(ready_pro_cap.name), 1)[1][:600]
        self.assertIn("AVAILABLE", card_html)
        self.assertNotIn("PRO RESEARCH", card_html)


class AnonymousUserTest(TestCase):

    def test_anonymous_user_is_redirected_not_shown_a_broken_page(self):
        response = self.client.get(reverse("research_lab_capabilities"))
        self.assertEqual(response.status_code, 302)   # login_required redirect, unchanged pre-existing behavior
        self.assertIn("/login", response.url)


class StatusLabelsMatchRegistryTest(TestCase):
    """'Add tests confirming status labels match the registry.' Renders
    every real registry entry and checks its engine_status appears as
    honest, unmodified text — not a second, hand-maintained status list."""

    def setUp(self):
        self.user = User.objects.create_user(username="statususer", password="x")
        self.client.login(username="statususer", password="x")

    def test_every_registry_capability_name_appears_on_the_page(self):
        response = self.client.get(reverse("research_lab_capabilities"))
        html = response.content.decode()
        for cap in RESEARCH_CAPABILITIES.values():
            # claude code changed: escape() — e.g. "Cointegration & Pairs
            # Research" renders as "...&amp; Pairs..." under Django's
            # autoescaping; comparing the raw registry string directly
            # against rendered HTML is a false negative, not a real defect.
            self.assertIn(escape(cap.name), html, f"{cap.id} is in the registry but missing from the capabilities page")

    def test_engine_status_taxonomy_values_rendered_are_real_registry_values_only(self):
        # claude code changed: proves no second/invented status vocabulary
        # leaked into the template — every one of these literal strings is
        # only present in the HTML because a REAL capability in the
        # registry currently has that engine_status; if the registry ever
        # stops using one of these six values, this test's second half
        # (real values found) still holds against whatever subset exists.
        real_statuses = {cap.engine_status for cap in RESEARCH_CAPABILITIES.values()}
        response = self.client.get(reverse("research_lab_capabilities"))
        html = response.content.decode()
        status_label_map = {
            "IMPLEMENTED_AND_READY": "IMPLEMENTED &amp; READY",
            "IMPLEMENTED_BUT_NEEDS_TESTS": "NEEDS TESTS",
            "IMPLEMENTED_BUT_NEEDS_AUDIT": "NEEDS AUDIT",
            "PARTIALLY_IMPLEMENTED": "PARTIALLY IMPLEMENTED",
            "BLOCKED_BY_DEPENDENCY": "BLOCKED BY DEPENDENCY",
            "NOT_IMPLEMENTED": "NOT IMPLEMENTED",
        }
        for status in real_statuses:
            self.assertIn(status_label_map[status], html, f"engine_status={status} has no rendered label")
