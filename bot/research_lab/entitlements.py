# ============================================================
# bot/research_lab/entitlements.py
# Research Lab — the centralized Research Entitlement Service (Advanced
# Quant Research Capability Architecture, section 9).
#
# claude code changed: new file. THE single choke point every capability
# access decision passes through — no view, orchestrator function, or tool
# call is permitted to scatter its own ad-hoc subscription check (section
# 9's explicit instruction). The research engines themselves import
# NOTHING from this module — subscription status can influence whether a
# tool call happens, never what a tool call computes (section 15's
# absolute rule, re-verified by test_research_lab_entitlements.py).
# ============================================================

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from django.utils import timezone

from bot.research_lab.capability_registry import RESEARCH_CAPABILITIES, ResearchCapability

# claude code changed: new — reason codes are for programmatic branching
# (tests assert on these, not on message text); `message` is the
# user-facing string. Keeping both means the UI copy can be edited freely
# without breaking a test that only cares about the reason.
UNKNOWN_CAPABILITY = "UNKNOWN_CAPABILITY"
AUTHENTICATION_REQUIRED = "AUTHENTICATION_REQUIRED"
ENGINE_NOT_READY = "ENGINE_NOT_READY"
SUBSCRIPTION_REQUIRED = "SUBSCRIPTION_REQUIRED"
SUBSCRIPTION_EXPIRED = "SUBSCRIPTION_EXPIRED"
ASSET_CLASS_NOT_SUPPORTED = "ASSET_CLASS_NOT_SUPPORTED"   # claude code changed: new — Forex Multi-Asset Integration
OK = "OK"


@dataclass(frozen=True)
class EntitlementResult:
    allowed: bool
    reason_code: str
    message: str

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "reason_code": self.reason_code, "message": self.message}


class ResearchEntitlementService:
    """
    claude code changed: new. Stateless — every method takes `user`
    explicitly rather than reading request-global state, so it can be
    called identically from a Django view, from plan_experiment()
    (defense in depth, same "re-verify at every layer" convention this
    package already established for is_tool_allowed()), or from a future
    AI tool-discovery call (section 16) without any of them sharing a
    request object.
    """

    @staticmethod
    def get_user_tier(user) -> str:
        """
        claude code changed: new. CORE unless a ResearchSubscription row
        says otherwise. A user with no row, an EXPIRED/CANCELED row, or a
        row whose expires_at has passed is CORE — there is no "trust the
        tier field even if status/expiry say otherwise" path.
        """
        if not getattr(user, "is_authenticated", False):
            return "CORE"  # claude code changed: a real tier value even for an anonymous caller — can_access() below still denies via AUTHENTICATION_REQUIRED before this would ever matter
        subscription = getattr(user, "research_subscription", None)
        if subscription is None:
            return "CORE"
        if subscription.status != "ACTIVE":
            return "CORE"
        if subscription.expires_at is not None and subscription.expires_at <= timezone.now():
            return "CORE"
        return subscription.tier

    @staticmethod
    def can_access(user, capability_id: str, asset_class: Optional[str] = None) -> EntitlementResult:
        """
        claude code changed: new. The single hard backend gate (section 12
        — "the backend must reject unauthorized capability requests").
        Checked in this fixed order:

          1. capability must exist (fail closed on a typo/unknown id —
             section 18 "unsupported capabilities fail closed")
          2. user must be authenticated
          3. the capability's ENGINE must be operationally ready —
             checked before subscription tier on purpose: "a paywall must
             never become a mechanism for bypassing research-quality
             gates" (section 6) — an unready engine is denied to a PRO
             subscriber exactly as it is to a CORE user, never treated as
             "paid for, therefore approved"
          4. (Forex Multi-Asset Integration) if the caller names a specific
             `asset_class`, it must be in the capability's own
             supported_asset_classes — same "structural gate before
             paywall gate" placement as step 3, for the same reason: which
             asset classes a capability actually supports is an
             engineering fact, not something a subscription should bypass
          5. the user's subscription tier must cover the capability's
             required_tier

        `asset_class` defaults to None (skips step 4 entirely) — every
        existing caller that doesn't pass it keeps its exact prior
        behavior.

        Returns a single verdict for backend enforcement. See
        capability_ui_state() below for the richer, always-informative
        display state section 10/11 wants (which independently reports
        BOTH axes rather than only the first one that failed).
        """
        capability = RESEARCH_CAPABILITIES.get(capability_id)
        if capability is None:
            return EntitlementResult(False, UNKNOWN_CAPABILITY, f"'{capability_id}' is not a recognized research capability.")

        if not getattr(user, "is_authenticated", False):
            return EntitlementResult(False, AUTHENTICATION_REQUIRED, "Sign in to access Research Lab capabilities.")

        if not capability.operationally_ready:
            note = capability.status_note or "This capability is not yet operational."
            return EntitlementResult(False, ENGINE_NOT_READY, note)

        if asset_class is not None and asset_class not in capability.supported_asset_classes:
            return EntitlementResult(False, ASSET_CLASS_NOT_SUPPORTED, f"{capability.name} does not yet support {asset_class}.")

        if capability.required_tier == "CORE":
            return EntitlementResult(True, OK, "Available.")

        # required_tier == PRO from here
        subscription = getattr(user, "research_subscription", None)
        user_tier = ResearchEntitlementService.get_user_tier(user)
        if user_tier == "PRO":
            return EntitlementResult(True, OK, "Available.")

        if subscription is not None and subscription.tier == "PRO" and subscription.status != "ACTIVE":
            return EntitlementResult(False, SUBSCRIPTION_EXPIRED, f"Your Pro subscription is {subscription.status.lower()}. Renew to regain access to {capability.name}.")
        if subscription is not None and subscription.tier == "PRO" and subscription.status == "ACTIVE" and subscription.expires_at is not None and subscription.expires_at <= timezone.now():
            return EntitlementResult(False, SUBSCRIPTION_EXPIRED, f"Your Pro subscription expired. Renew to regain access to {capability.name}.")

        return EntitlementResult(False, SUBSCRIPTION_REQUIRED, f"{capability.name} is included in Pro Research. Upgrade to unlock it.")

    @staticmethod
    def capability_ui_state(user, capability_id: str, asset_class: Optional[str] = None) -> dict:
        """
        claude code changed: section 10/11's four-state catalog badge,
        computed independently along BOTH axes (unlike can_access(), which
        only needs one final verdict). Returns:

            {"badge": "AVAILABLE" | "LOCKED" | "COMING_SOON" | "UNAVAILABLE",
             "message": str}

        AVAILABLE   — engine ready AND user entitled
        LOCKED      — engine ready, but user's subscription doesn't cover it (🔒)
        COMING_SOON — engine not implemented at all yet (🧪), regardless of subscription
        UNAVAILABLE — engine implemented but not yet approved for use (⚠️), regardless of subscription — this is the exact "Pro subscription + untested engine" case section 10's own example describes; ALSO now the badge for "implemented, but not for this asset_class" (Forex Multi-Asset Integration) — reported the same way, since both are "not offered here," not a subscription problem.

        `asset_class` defaults to None (skips the asset-class check
        entirely) — every existing caller that doesn't pass it keeps its
        exact prior behavior.
        """
        capability = RESEARCH_CAPABILITIES.get(capability_id)
        if capability is None:
            return {"badge": "UNAVAILABLE", "message": "Unknown capability."}

        if not capability.operationally_ready:
            if capability.engine_status == "NOT_IMPLEMENTED":
                return {"badge": "COMING_SOON", "message": capability.status_note or "Coming soon."}
            return {"badge": "UNAVAILABLE", "message": capability.status_note or "Temporarily unavailable."}

        if asset_class is not None and asset_class not in capability.supported_asset_classes:
            return {"badge": "UNAVAILABLE", "message": f"{capability.name} does not yet support {asset_class}."}

        if capability.required_tier == "CORE":
            return {"badge": "AVAILABLE", "message": "Available."}

        user_tier = ResearchEntitlementService.get_user_tier(user) if getattr(user, "is_authenticated", False) else "CORE"
        if user_tier == "PRO":
            return {"badge": "AVAILABLE", "message": "Available."}
        return {"badge": "LOCKED", "message": f"{capability.name} is included in Pro Research."}
