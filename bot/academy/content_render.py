# ============================================================
# bot/academy/content_render.py
# claude code changed: new file — renders a Lesson's Markdown body to
# HTML for display. Sanitizes with bleach even though today's content is
# entirely first-party-authored (loaded from bot/academy/content/ via
# load_academy_content) — defense in depth against a future content
# source (an editor UI, a CMS import) that isn't fully trusted, and
# cheap insurance against a Markdown extension accidentally emitting
# something unsafe. Never render Lesson.body with Django's |safe filter
# directly on raw Markdown output — always through render_lesson_body().
# ============================================================

import re

import bleach
import markdown

# claude code changed: new — bleach.clean(strip=True) removes a
# disallowed tag but NOT necessarily its text content (confirmed by a
# failing test: <script>alert('xss')</script> came out as the tag gone
# but the literal text "alert('xss')" left behind, inert but leaked).
# Not executable — no <script> tag survives — but genuinely dangerous
# element content (script/style) should be removed outright, not just
# untagged. Stripped before bleach ever sees it, case-insensitively,
# across multi-line blocks.
_DANGEROUS_BLOCK_RE = re.compile(r"<(script|style)\b[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)

ALLOWED_TAGS = [
    "p", "br", "hr",
    "h1", "h2", "h3", "h4",
    "strong", "em", "code", "pre",
    "ul", "ol", "li",
    "blockquote",
    "table", "thead", "tbody", "tr", "th", "td",
    "a",
]

ALLOWED_ATTRIBUTES = {
    "a": ["href", "title", "rel"],
}


def render_lesson_body(markdown_text: str) -> str:
    """
    Markdown -> sanitized HTML. The only path Lesson.body should ever be
    rendered through.
    """
    html = markdown.markdown(
        markdown_text or "",
        extensions=["tables", "fenced_code"],
    )
    html = _DANGEROUS_BLOCK_RE.sub("", html)
    return bleach.clean(html, tags=ALLOWED_TAGS, attributes=ALLOWED_ATTRIBUTES, strip=True)
