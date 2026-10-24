# claude code changed: new file — 2026-10-22, dev-only template boundary markers (bot/dev_template_markers.py).
import os
import re
from unittest import mock

from django.test import TestCase

from bot import dev_template_markers as dtm


class TemplateMarkersTests(TestCase):
    def tearDown(self):
        dtm.uninstall()

    def page(self):
        with mock.patch("bot.research.leverage_reality.load_historical", return_value=None):
            return self.client.get("/academy/leverage-reality/").content.decode()

    def test_off_by_default_nothing_changes(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(dtm.install_if_requested())
        html = self.page()
        self.assertNotIn("<!-- BEGIN", html)
        self.assertNotIn("<!-- PAGE", html)

    def test_on_marks_page_blocks_and_includes_with_file_and_line(self):
        with mock.patch.dict(os.environ, {dtm.ENV_FLAG: "1"}, clear=True):
            self.assertTrue(dtm.install_if_requested())
        html = self.page()
        self.assertIn("<!-- PAGE templates/academy/leverage_reality.html  (extends 'base.html')", html)
        self.assertRegex(html, r'<!-- BEGIN block "content" — templates/academy/leverage_reality\.html:\d+ -->')
        self.assertIn("<!-- BEGIN include templates/partials/_topbar.html -->", html)
        self.assertIn("<!-- END include templates/partials/_topbar.html -->", html)

    def test_markers_never_break_doctype_or_title(self):
        with mock.patch.dict(os.environ, {dtm.ENV_FLAG: "1"}, clear=True):
            dtm.install_if_requested()
        html = self.page()
        self.assertTrue(html.lstrip().lower().startswith("<!doctype"))          # nothing before the doctype
        title = re.search(r"<title>(.*?)</title>", html, re.S).group(1)
        self.assertNotIn("<!--", title)                                         # text-only block left untouched

    def test_refused_in_production_even_when_requested(self):
        with mock.patch.dict(os.environ, {dtm.ENV_FLAG: "1", "DATABASE_URL": "postgres://x"}, clear=True):
            self.assertFalse(dtm.install_if_requested())
        self.assertNotIn("<!-- PAGE", self.page())

    def test_uninstall_restores_normal_rendering(self):
        with mock.patch.dict(os.environ, {dtm.ENV_FLAG: "1"}, clear=True):
            dtm.install_if_requested()
        dtm.uninstall()
        self.assertNotIn("<!-- BEGIN", self.page())
