# claude code changed: new file — app-wide navigation UX fixes requested
# mid-Phase-2D: (1) a back-navigation arrow on every page (not just
# Research Lab, which Phase 2C already covered with its own precise,
# per-page-audited _back_nav.html), and (2) the topbar's "Research" /
# "Risk & Execution" nav dropdowns (native HTML <details>/<summary>, which
# has NO built-in "close on outside click" behavior) now close when the
# user clicks anywhere else on the page, not only by re-clicking the exact
# toggle.
#
# Unlike Research Lab's tight, audited wizard flow (one real known parent
# per page), this app's ~20 other pages are all independently reachable
# from the same shared topbar nav from many different starting points —
# there is no single correct "known parent" for e.g. Backtesting or
# Portfolio & Risk. The generic arrow therefore prefers browser history
# (via JS) with a real <a href> to the main dashboard as the always-works
# fallback (JS disabled, or no history entry e.g. a bookmarked link opened
# fresh) — added once to templates/partials/_topbar.html (included by
# every page) rather than duplicated per-template.

from django.contrib.auth.models import User
from django.test import TestCase


class TopbarBackArrowTest(TestCase):

    def test_dashboard_root_has_no_back_arrow(self):
        # claude code changed: the main dashboard is the app's own root —
        # nothing to go back to, same reasoning Research Lab's dashboard
        # already uses.
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertNotIn('term-back-arrow', resp.content.decode())

    def test_ordinary_page_has_the_generic_back_arrow(self):
        resp = self.client.get('/research/backtests/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertIn('term-back-arrow', html)
        self.assertIn('window.history.back()', html)   # claude code changed: prefers browser history
        self.assertIn('href="/"', html.split('term-back-arrow')[0][-200:] or html)  # claude code changed: loose sanity check that SOME dashboard href exists nearby as the fallback

    def test_research_lab_page_keeps_only_its_own_precise_back_link(self):
        # claude code changed: research_lab_mvp pages already carry Phase
        # 2C's own precise, per-page-audited back link — the generic
        # topbar arrow must not ALSO appear there (two back arrows on one
        # small mobile screen is exactly the confusion this feature exists
        # to avoid).
        User.objects.create_user(username='navcheck', password='x')
        self.client.login(username='navcheck', password='x')
        resp = self.client.get('/research-lab/capabilities/')
        self.assertEqual(resp.status_code, 200)
        html = resp.content.decode()
        self.assertNotIn('term-back-arrow', html)
        self.assertIn('&larr; Back', html)


class CryptoForexNavLinksTest(TestCase):
    # claude code changed: new — Crypto/Forex Navigation Discoverability.
    # Real Django test client against real pages, matching this file's
    # own established convention (no mocking of the templates/routes
    # themselves).

    def test_crypto_and_forex_links_present_on_dashboard(self):
        resp = self.client.get('/')
        html = resp.content.decode()
        self.assertIn('>Crypto</a>', html)
        self.assertIn('>Forex</a>', html)

    def test_topbar_comment_text_never_leaks_into_the_rendered_page(self):
        # claude code changed: new — real bug, found live in production
        # (reported directly by the user): the long explanatory comment
        # for the Crypto/Forex nav links was originally written with the
        # {# #} single-line delimiter, which does not support multi-line
        # content — the exact same mistake this file's own back-arrow
        # block already had fixed once before, for the same reason. It
        # rendered as literal visible text on every page. Every other
        # test in this file only checks for PRESENCE of expected content,
        # which is exactly why none of them caught this — the leaked
        # comment text sat right next to the real links without breaking
        # any "is Crypto/Forex present" assertion. This test asserts
        # ABSENCE of the marker string every one of this codebase's
        # inline authorship comments starts with, across two different
        # real pages, so a future multi-line {# #} mistake anywhere in
        # this shared partial fails loudly instead of shipping silently.
        #
        # claude code changed: was a blanket assertNotIn("claude code
        # changed", html) — too broad, since that exact phrase also
        # appears legitimately inside real JavaScript `//` comments in
        # base.html's own <script> block (which SHOULD be visible in page
        # source — that's just how JS comments work, nothing to hide).
        # Checks for a phrase unique to this partial's own Django
        # {% comment %} block instead (confirmed absent from every real
        # .js file), so this only fires on an actual server-side template
        # comment leaking, never a legitimate client-side JS comment.
        for url in ('/', '/market/'):  # both public, no login required, both render this shared topbar partial
            html = self.client.get(url).content.decode()
            self.assertNotIn('Navigation Discoverability', html, f"a template comment leaked into the rendered HTML of {url}")

    def test_crypto_link_points_at_the_real_dashboard_route(self):
        resp = self.client.get('/')
        html = resp.content.decode()
        # claude code changed: the exact <a> tag, not just the word
        # "Crypto" appearing somewhere — proves it's a real link to '/',
        # not text that happens to be on the page for another reason.
        self.assertIn('href="/">Crypto</a>', html)

    def test_forex_link_points_at_the_forex_dashboard(self):
        # claude code changed: Forex Research Dashboard mission — Forex
        # used to point straight at Research Lab's hypothesis-entry page
        # (?asset_class=FOREX hint and all); now it opens a real,
        # first-class Forex market/research dashboard (bot/views/
        # forex_dashboard.py), with the hypothesis workflow embedded
        # inside that page rather than being the link's sole destination.
        from django.urls import reverse
        resp = self.client.get('/')
        html = resp.content.decode()
        self.assertIn(f'href="{reverse("forex_dashboard")}">Forex</a>', html)

    def test_crypto_link_highlights_on_the_dashboard_page(self):
        # claude code changed: the <a> tag's class/href span two lines in
        # the template (matching every other nav link's own formatting),
        # so assert on the class attribute and the href/label separately
        # rather than one exact single-line string.
        # claude code changed: dashboard hardening added a data-slow-nav
        # attribute on this same <a> tag (see templates/base.html's
        # loading-overlay handler) between the class and href lines — this
        # now matches on the Crypto link's own <a> block as a whole
        # (isolated by splitting on ">Crypto</a>") rather than one exact,
        # now-stale three-line string.
        resp = self.client.get('/')
        html = resp.content.decode()
        crypto_tag = html.split(">Crypto</a>")[0].rsplit("<a class=", 1)[-1]
        self.assertIn('"term-nav-link active"', crypto_tag)
        self.assertIn('href="/"', crypto_tag)

    def test_forex_link_present_and_not_highlighted_on_an_unrelated_page(self):
        from django.urls import reverse
        resp = self.client.get('/research/backtests/')
        html = resp.content.decode()
        self.assertIn('>Forex</a>', html)
        self.assertNotIn(f'term-nav-link active"\n           href="{reverse("forex_dashboard")}">Forex</a>', html)

    def test_crypto_and_forex_appear_in_the_wrapped_mobile_nav_too(self):
        # claude code changed: this app has no separate hamburger-menu
        # markup — .term-nav wraps onto its own row below 860px via a CSS
        # media query (static/css/terminal.css), reusing the exact same
        # <a> elements. Proving the links exist in the one shared markup
        # IS proving they appear on mobile — there's no separate DOM to
        # check.
        resp = self.client.get('/')
        html = resp.content.decode()
        self.assertIn('class="term-nav"', html)
        nav_section = html.split('class="term-nav"', 1)[1].split('</nav>', 1)[0]
        self.assertIn('>Crypto</a>', nav_section)
        self.assertIn('>Forex</a>', nav_section)


class DropdownCloseOnOutsideClickTest(TestCase):

    def test_close_script_present_on_every_page_type_checked(self):
        for url in ('/', '/research/backtests/'):
            resp = self.client.get(url)
            html = resp.content.decode()
            self.assertIn('.term-nav-group[open]', html)
            self.assertIn('removeAttribute("open")', html)

    def test_close_logic_only_closes_groups_the_click_was_outside_of(self):
        # claude code changed: proves the actual JS predicate, not just its
        # presence — details.contains(event.target) is the correct check
        # (closes only groups the click landed OUTSIDE of; a click on the
        # group's own summary/toggle, or one of its own dropdown links,
        # must never be immediately un-done by this same handler).
        resp = self.client.get('/')
        html = resp.content.decode()
        self.assertIn('!details.contains(event.target)', html)
