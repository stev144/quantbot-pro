# claude code changed: 2026-10-26 — new file: free trial + Stripe subscription tests (bot/billing/).
# No mocking: webhook payloads are signed with Stripe's own signing code and pass through the real verification;
# nothing here calls the Stripe API (Checkout/Portal need real keys — checkout_params is tested instead).

import json
import time
from datetime import timedelta

import stripe
from django.contrib.auth.models import AnonymousUser, User
from django.test import RequestFactory, TestCase, override_settings
from django.urls import resolve
from django.utils import timezone

from bot.billing.access import has_premium_access, is_premium_request, plan_state, start_trial, trial_days_left
from bot.billing.context_processors import billing_nav
from bot.billing.middleware import PremiumAccessMiddleware
from bot.billing.models import Subscription
from bot.billing.stripe_service import checkout_params

WEBHOOK_SECRET = "whsec_test_billing_suite"


def _request(path, user):
    req = RequestFactory().get(path)
    req.user = user
    req.resolver_match = resolve(path)
    return req


def _view(request):   # stand-in view: process_view either returns a response or lets this run
    return None


class TrialAndAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("trader", "t@example.com", "pw-123456789")

    def test_trial_is_30_days_and_created_once(self):
        now = timezone.now()
        sub = start_trial(self.user, now)
        self.assertEqual(sub.trial_ends_at - sub.trial_started_at, timedelta(days=30))
        later = start_trial(self.user, now + timedelta(days=5))
        self.assertEqual(later.pk, sub.pk)
        self.assertEqual(later.trial_ends_at, sub.trial_ends_at)   # a second call never restarts the trial

    def test_states(self):
        now = timezone.now()
        sub = start_trial(self.user, now)
        self.assertEqual(plan_state(sub, now), "trial")
        self.assertEqual(trial_days_left(sub, now), 30)
        self.assertEqual(plan_state(sub, now + timedelta(days=30, seconds=1)), "expired")
        self.assertEqual(trial_days_left(sub, now + timedelta(days=31)), 0)
        for status, expected in [("active", "subscribed"), ("trialing", "subscribed"), ("past_due", "subscribed"),
                                 ("unpaid", "expired"), ("canceled", "expired"), ("incomplete", "expired")]:
            sub.stripe_status = status
            self.assertEqual(plan_state(sub, now + timedelta(days=40)), expected, status)

    def test_access(self):
        self.assertFalse(has_premium_access(AnonymousUser()))
        self.assertTrue(has_premium_access(self.user))
        sub = start_trial(self.user)
        sub.trial_ends_at = timezone.now() - timedelta(seconds=1)
        sub.save()
        self.assertFalse(has_premium_access(self.user))
        staff = User.objects.create_user("owner", "o@example.com", "pw-123456789", is_staff=True)
        self.assertTrue(has_premium_access(staff))

    def test_which_pages_are_premium(self):
        premium = ["/research/", "/research/features/", "/research-lab/", "/forex/scanner/", "/market/",
                   "/portfolio/", "/execution/", "/execution/venues/", "/markets/crypto/BTC_USDT/"]
        free = ["/", "/markets/", "/markets/watchlist/", "/forex/", "/academy/", "/execution/paper-trading/",
                "/account/billing/"]
        for path in premium:
            self.assertTrue(is_premium_request(_request(path, self.user)), path)
        for path in free:
            self.assertFalse(is_premium_request(_request(path, self.user)), path)


class MiddlewareTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("trader", "t@example.com", "pw-123456789")
        self.mw = PremiumAccessMiddleware(lambda r: None)

    @override_settings(BILLING_ENFORCED=False)
    def test_not_enforced_leaves_everything_open(self):
        self.assertIsNone(self.mw.process_view(_request("/research/", AnonymousUser()), _view, (), {}))

    @override_settings(BILLING_ENFORCED=True)
    def test_enforced(self):
        resp = self.mw.process_view(_request("/research/", AnonymousUser()), _view, (), {})
        self.assertEqual(resp.status_code, 402)
        self.assertIn(b"Start 30-day free trial", resp.content)

        self.assertIsNone(self.mw.process_view(_request("/research/", self.user), _view, (), {}))     # in trial
        self.assertIsNone(self.mw.process_view(_request("/markets/", AnonymousUser()), _view, (), {}))  # free page

        sub = start_trial(self.user)
        sub.trial_ends_at = timezone.now() - timedelta(days=1)
        sub.save()
        resp = self.mw.process_view(_request("/research/", self.user), _view, (), {})
        self.assertEqual(resp.status_code, 402)
        self.assertIn(b"Your free trial has ended", resp.content)

        resp = self.mw.process_view(_request("/execution/venues/books.json", self.user), _view, (), {})
        self.assertEqual(resp.status_code, 402)
        self.assertEqual(json.loads(resp.content)["error"], "premium")

        sub.stripe_status = "active"
        sub.save()
        self.assertIsNone(self.mw.process_view(_request("/research/", self.user), _view, (), {}))


class SignupAndPagesTests(TestCase):
    def test_signup_starts_trial(self):
        resp = self.client.post("/signup/", {"username": "newbie", "email": "n@example.com",
                                             "password1": "Str0ng-pass-word!", "password2": "Str0ng-pass-word!"},
                                follow=True)
        user = User.objects.get(username="newbie")
        sub = Subscription.objects.get(user=user)
        self.assertEqual(trial_days_left(sub), 30)
        self.assertIn("30-day free trial has started", resp.content.decode())

    @override_settings(STRIPE_SECRET_KEY="", STRIPE_PRICE_ID="")
    def test_billing_page_and_unconfigured_checkout(self):
        user = User.objects.create_user("trader", "t@example.com", "pw-123456789")
        self.client.force_login(user)
        resp = self.client.get("/account/billing/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("Free trial · 30 days left", resp.content.decode())
        self.assertIn("Subscriptions open soon", resp.content.decode())
        resp = self.client.post("/account/billing/checkout/", follow=True)
        self.assertIn("Payments are not set up yet", resp.content.decode())

    def test_billing_page_needs_login(self):
        resp = self.client.get("/account/billing/")
        self.assertEqual(resp.status_code, 302)

    def test_nav_label(self):
        user = User.objects.create_user("trader", "t@example.com", "pw-123456789")
        req = RequestFactory().get("/")
        req.user = user
        self.assertEqual(billing_nav(req)["billing_nav_label"], "Trial · 30d")
        sub = Subscription.objects.get(user=user)
        sub.stripe_status = "active"
        sub.save()
        self.assertEqual(billing_nav(req)["billing_nav_label"], "Pro")
        req.user = AnonymousUser()
        self.assertEqual(billing_nav(req), {})


@override_settings(STRIPE_PRICE_ID="price_test_123")
class CheckoutParamsTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("trader", "t@example.com", "pw-123456789")

    def test_remaining_trial_carries_over(self):
        now = timezone.now()
        sub = start_trial(self.user, now)
        p = checkout_params(self.user, sub, "https://x/s", "https://x/c", now=now + timedelta(days=10))
        self.assertEqual(p["mode"], "subscription")
        self.assertEqual(p["line_items"], [{"price": "price_test_123", "quantity": 1}])
        self.assertEqual(p["subscription_data"]["trial_end"], int(sub.trial_ends_at.timestamp()))
        self.assertEqual(p["subscription_data"]["metadata"], {"user_id": str(self.user.pk)})
        self.assertEqual(p["client_reference_id"], str(self.user.pk))
        self.assertEqual(p["customer_email"], "t@example.com")
        self.assertNotIn("payment_method_types", p)   # Stripe's dynamic payment methods

    def test_no_trial_end_inside_48_hours(self):
        now = timezone.now()
        sub = start_trial(self.user, now)
        p = checkout_params(self.user, sub, "s", "c", now=sub.trial_ends_at - timedelta(hours=40))
        self.assertNotIn("trial_end", p["subscription_data"])
        sub.stripe_customer_id = "cus_123"
        p = checkout_params(self.user, sub, "s", "c", now=now)
        self.assertEqual(p["customer"], "cus_123")
        self.assertNotIn("customer_email", p)


@override_settings(STRIPE_WEBHOOK_SECRET=WEBHOOK_SECRET)
class WebhookTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("trader", "t@example.com", "pw-123456789")
        self.sub = start_trial(self.user)

    def _post(self, event, secret=WEBHOOK_SECRET):
        payload = json.dumps(event)
        header = stripe.WebhookSignature.generate_signature_header(payload, secret)
        return self.client.post("/account/billing/webhook/", payload, content_type="application/json",
                                headers={"Stripe-Signature": header})

    def _sub_event(self, etype, status, created, sub_id="sub_1", **extra):
        obj = {"id": sub_id, "object": "subscription", "customer": "cus_1", "status": status,
               "cancel_at_period_end": False, "metadata": {"user_id": str(self.user.pk)},
               "items": {"data": [{"current_period_end": 1798761600}]}, **extra}
        return {"id": f"evt_{created}", "type": etype, "created": created, "data": {"object": obj}}

    def test_bad_signature_rejected(self):
        self.assertEqual(self._post(self._sub_event("customer.subscription.updated", "active", 1),
                                    secret="whsec_wrong").status_code, 400)
        resp = self.client.post("/account/billing/webhook/", "{}", content_type="application/json")
        self.assertEqual(resp.status_code, 400)
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.stripe_status, "")

    @override_settings(STRIPE_WEBHOOK_SECRET="")
    def test_unconfigured_secret(self):
        resp = self.client.post("/account/billing/webhook/", "{}", content_type="application/json")
        self.assertEqual(resp.status_code, 503)

    def test_full_lifecycle(self):
        t = int(time.time())
        checkout = {"id": "evt_c", "type": "checkout.session.completed", "created": t,
                    "data": {"object": {"id": "cs_1", "mode": "subscription", "customer": "cus_1",
                                        "subscription": "sub_1", "client_reference_id": str(self.user.pk)}}}
        self.assertEqual(self._post(checkout).status_code, 200)
        self.sub.refresh_from_db()
        self.assertEqual((self.sub.stripe_customer_id, self.sub.stripe_subscription_id), ("cus_1", "sub_1"))

        self._post(self._sub_event("customer.subscription.updated", "active", t + 10))
        self.sub.refresh_from_db()
        self.assertEqual(plan_state(self.sub), "subscribed")
        self.assertEqual(int(self.sub.current_period_end.timestamp()), 1798761600)

        # an older event delivered late does not overwrite the newer state
        self._post(self._sub_event("customer.subscription.updated", "incomplete", t + 5))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.stripe_status, "active")

        # cancellation scheduled, then the subscription ends
        self._post(self._sub_event("customer.subscription.updated", "active", t + 20, cancel_at_period_end=True))
        self.sub.refresh_from_db()
        self.assertTrue(self.sub.cancel_at_period_end)
        self._post(self._sub_event("customer.subscription.deleted", "canceled", t + 30))
        self.sub.refresh_from_db()
        self.assertEqual(self.sub.stripe_status, "canceled")

    def test_subscription_event_before_checkout_links_by_metadata(self):
        self._post(self._sub_event("customer.subscription.created", "trialing", int(time.time())))
        self.sub.refresh_from_db()
        self.assertEqual((self.sub.stripe_subscription_id, self.sub.stripe_status), ("sub_1", "trialing"))

    def test_superseded_subscription_cannot_cancel_newer_one(self):
        t = int(time.time())
        self._post(self._sub_event("customer.subscription.updated", "active", t, sub_id="sub_new"))
        self._post(self._sub_event("customer.subscription.deleted", "canceled", t + 1, sub_id="sub_old"))
        self.sub.refresh_from_db()
        self.assertEqual((self.sub.stripe_subscription_id, self.sub.stripe_status), ("sub_new", "active"))

    def test_alert_and_unknown_events_are_acknowledged(self):
        t = int(time.time())
        for etype in ("charge.dispute.created", "invoice.payment_failed", "customer.created"):
            event = {"id": f"evt_{etype}", "type": etype, "created": t, "data": {"object": {"id": "x_1"}}}
            self.assertEqual(self._post(event).status_code, 200, etype)
