# ============================================================
# bot/dev_template_markers.py
# claude code changed: new file — 2026-10-22 (owner request). DEVELOPMENT-ONLY switch that makes every rendered page
# show, in the browser's Elements panel, which template file (and line) produced each part:
#
#   <!-- PAGE templates/venue_comparison.html  (extends base.html) -->        right after <!DOCTYPE …>
#   <!-- BEGIN block "content" — templates/venue_comparison.html:46 -->  …  <!-- END block "content" -->
#   <!-- BEGIN include templates/partials/_topbar.html -->               …  <!-- END include … -->
#
# ON only when the environment variable SQT_TEMPLATE_MARKERS=1 (true/yes) is set when the server starts, and NEVER
# when DATABASE_URL is set (the Railway/production gate this project already uses for DB and SSL settings).
# Off by default: nothing changes in normal runs. Safety rules for the markers themselves:
#   - nothing is ever placed before <!DOCTYPE> (that would switch the browser to quirks mode);
#   - text-only output (e.g. {% block title %}, attribute values) is never wrapped — a comment there would
#     show up as literal text;
#   - the patch is installed once, can be removed (uninstall), and touches rendering only, never data.
# ============================================================

import logging
import os
import re
from pathlib import Path

from django.conf import settings

logger = logging.getLogger(__name__)

ENV_FLAG = "SQT_TEMPLATE_MARKERS"
_HAS_TAG = re.compile(r"<[A-Za-z!/]")
_DOCTYPE = re.compile(r"\A(\s*<!DOCTYPE[^>]*>)", re.IGNORECASE)
_originals = {}


def requested() -> bool:
    return os.getenv(ENV_FLAG, "").strip().lower() in ("1", "true", "yes")


def allowed() -> bool:
    """Markers are refused in production (DATABASE_URL present), whatever the flag says."""
    return not os.getenv("DATABASE_URL", "")


def _short(origin) -> str:
    name = getattr(origin, "name", None) or "?"
    try:
        return Path(name).resolve().relative_to(Path(settings.BASE_DIR).resolve()).as_posix()
    except (ValueError, TypeError, OSError):
        return str(name).replace("\\", "/")


def _wrap(begin: str, end: str, html: str) -> str:
    if not _HAS_TAG.search(html or ""):
        return html                       # text-only output: a comment would be visible text
    from django.utils.safestring import SafeString, mark_safe
    out = f"<!-- BEGIN {begin} -->{html}<!-- END {end} -->"
    return mark_safe(out) if isinstance(html, SafeString) else out


def install() -> bool:
    """Patch Template.render and BlockNode.render. Returns True when markers are now active."""
    if _originals:
        return True
    from django.template.base import Template
    from django.template.loader_tags import BLOCK_CONTEXT_KEY, BlockNode, ExtendsNode

    orig_template_render, orig_block_render = Template.render, BlockNode.render

    def template_render(self, context):
        top_level = context.template is None
        html = orig_template_render(self, context)
        if top_level:
            parents = [n.parent_name.token for n in self.nodelist.get_nodes_by_type(ExtendsNode)]
            note = f"<!-- PAGE {_short(self.origin)}{'  (extends ' + ', '.join(parents) + ')' if parents else ''}"
            note += f"  · template markers ON ({ENV_FLAG}) -->"
            m = _DOCTYPE.match(html)
            return (html[:m.end()] + note + html[m.end():]) if m else (note + html)
        return _wrap(f"include {_short(self.origin)}", f"include {_short(self.origin)}", html)

    def block_render(self, context):
        bc = context.render_context.get(BLOCK_CONTEXT_KEY)
        source = (bc.get_block(self.name) if bc is not None else None) or self   # the block that will actually render
        line = getattr(getattr(source, "token", None), "lineno", None)
        where = _short(getattr(source, "origin", None)) + (f":{line}" if line else "")
        html = orig_block_render(self, context)
        return _wrap(f'block "{self.name}" — {where}', f'block "{self.name}"', html)

    _originals.update(template=orig_template_render, block=orig_block_render)
    Template.render = template_render
    BlockNode.render = block_render
    logger.warning(f"[dev_template_markers] template boundary comments are ON ({ENV_FLAG}) — development only")
    return True


def uninstall() -> None:
    if not _originals:
        return
    from django.template.base import Template
    from django.template.loader_tags import BlockNode
    Template.render = _originals.pop("template")
    BlockNode.render = _originals.pop("block")


def install_if_requested() -> bool:
    if not requested():
        return False
    if not allowed():
        logger.error(f"[dev_template_markers] {ENV_FLAG} is set but DATABASE_URL is present (production) — refused")
        return False
    return install()
