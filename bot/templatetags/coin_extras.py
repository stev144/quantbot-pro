# claude code changed: new file — 2026-10-22 (owner: "include the coin's logo on all the crypto pages").
# {% load coin_extras %}{% coin_logo "BTC_USDT" %} → the coin's logo from static/img/coins/ (bundled locally — no
# external request), or a lettered badge when no logo exists. Accepts BTC_USDT, BTC/USDT, BTCUSDT or BTC.
import re
from functools import lru_cache
from pathlib import Path

from django import template
from django.conf import settings
from django.templatetags.static import static
from django.utils.html import format_html

register = template.Library()

LOGO_DIR = Path(settings.BASE_DIR) / "static" / "img" / "coins"
_QUOTES = ("USDT", "USDC", "FDUSD", "BUSD", "USD", "EUR", "BTC", "ETH", "BNB")


@lru_cache(maxsize=1)
def available_logos() -> frozenset:
    return frozenset(p.stem.upper() for p in LOGO_DIR.glob("*.svg"))


def base_asset(symbol) -> str:
    """'BTC_USDT' / 'BTC/USDT' / 'BTCUSDT' / 'btc' → 'BTC'."""
    s = str(symbol or "").strip().upper()
    for sep in ("_", "/", "-"):
        if sep in s:
            return s.split(sep, 1)[0]
    for q in _QUOTES:
        if s.endswith(q) and len(s) > len(q) and s[: -len(q)] in available_logos():
            return s[: -len(q)]
    return s


@register.simple_tag
def coin_logo(symbol, size=24):
    base = base_asset(symbol)
    try:
        px = max(12, min(int(size), 96))
    except (TypeError, ValueError):
        px = 24
    if not re.fullmatch(r"[A-Z0-9]{1,15}", base or ""):
        return ""
    if base in available_logos():
        return format_html('<img class="coin-logo" src="{}" alt="{} logo" width="{}" height="{}" loading="lazy" decoding="async">',
                           static(f"img/coins/{base.lower()}.svg"), base, px, px)
    letters = re.sub(r"^\d+", "", base)[:2] or base[:2]
    return format_html('<span class="coin-logo coin-logo-fallback" style="width:{}px;height:{}px;font-size:{}px" '
                       'title="{} (no logo available)" aria-hidden="true">{}</span>', px, px, max(8, px * 0.4), base, letters)


# ── forex pairs (claude code changed: 2026-10-22) ─────────────────────────────────────────────────────────────────
# {% fx_pair "EUR_USD" %} → a small container: two overlapping flags + "Euro / US Dollar". Flags are bundled locally
# in static/img/flags/ (flag-icons, MIT); gold/silver use project-drawn badges. A currency without a flag falls back
# to its code in a lettered circle — never a guessed flag.
FLAG_DIR = Path(settings.BASE_DIR) / "static" / "img" / "flags"
CURRENCIES = {   # code → (flag file, full name)
    "USD": ("us", "US Dollar"), "EUR": ("eu", "Euro"), "GBP": ("gb", "British Pound"), "JPY": ("jp", "Japanese Yen"),
    "CHF": ("ch", "Swiss Franc"), "AUD": ("au", "Australian Dollar"), "NZD": ("nz", "New Zealand Dollar"),
    "CAD": ("ca", "Canadian Dollar"), "SGD": ("sg", "Singapore Dollar"), "SEK": ("se", "Swedish Krona"),
    "NOK": ("no", "Norwegian Krone"), "DKK": ("dk", "Danish Krone"), "ZAR": ("za", "South African Rand"),
    "PLN": ("pl", "Polish Zloty"), "HKD": ("hk", "Hong Kong Dollar"), "THB": ("th", "Thai Baht"),
    "MXN": ("mx", "Mexican Peso"), "CZK": ("cz", "Czech Koruna"), "CNH": ("cn", "Chinese Yuan (offshore)"),
    "XAU": ("xau", "Gold"), "XAG": ("xag", "Silver"),
}


def split_pair(symbol):
    """'EUR_USD' / 'EUR/USD' / 'EURUSD' → ('EUR', 'USD'); None when it is not a 3+3 currency pair."""
    s = re.sub(r"[^A-Z]", "", str(symbol or "").upper())
    return (s[:3], s[3:6]) if len(s) == 6 else None


def _flag(code, px):
    entry = CURRENCIES.get(code)
    if entry and (FLAG_DIR / f"{entry[0]}.svg").exists():
        return format_html('<img class="fx-flag" src="{}" alt="{} flag" width="{}" height="{}" loading="lazy" decoding="async">',
                           static(f"img/flags/{entry[0]}.svg"), code, px, px)
    return format_html('<span class="fx-flag coin-logo-fallback" style="width:{}px;height:{}px;font-size:{}px">{}</span>',
                       px, px, max(7, px * 0.36), code[:3])


@register.simple_tag
def fx_flags(symbol, size=22):
    """Two overlapping flags only (no names)."""
    pair = split_pair(symbol)
    if not pair:
        return ""
    try:
        px = max(12, min(int(size), 64))
    except (TypeError, ValueError):
        px = 22
    return format_html('<span class="fx-flags" title="{} / {}">{}{}</span>', *(CURRENCIES.get(c, (None, c))[1] for c in pair),
                       _flag(pair[0], px), _flag(pair[1], px))


@register.simple_tag
def fx_pair(symbol, size=18):
    """Small container: overlapping flags + both full currency names."""
    pair = split_pair(symbol)
    if not pair:
        return ""
    names = [CURRENCIES.get(c, (None, c))[1] for c in pair]
    return format_html('<span class="fx-pair">{}<span class="fx-names">{} <span class="fx-sep">/</span> {}</span></span>',
                       fx_flags(symbol, size), names[0], names[1])
