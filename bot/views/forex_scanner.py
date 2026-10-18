# bot/views/forex_scanner.py  # claude code changed: new — Forex Market Scanner page
"""
Forex Market Scanner — live regime/volatility/z-score table for all 1h FX pairs.

Reads the latest candles from data/forex/*_1h.csv (excludes 5m/15m), computes
regime, ADX, ATR%, z-score (20-bar), BB width, and 24h price change for every
pair, then renders a sortable table. Ranging pairs float to the top.

Cache: 30 minutes (data is at most 30 min stale vs. the underlying CSVs).
"""

import json
import logging
from pathlib import Path

import numpy as np
import pandas as pd
from django.core.cache import cache
from django.shortcuts import render

logger = logging.getLogger(__name__)

_ROOT      = Path(__file__).resolve().parents[2]
_FOREX_DIR = _ROOT / "data" / "forex"
_CACHE_KEY = "forex_scanner_v1"
_CACHE_TTL = 1800   # 30 minutes
_CANDLES   = 200    # candles loaded per pair for indicator computation
_ZSCORE_W  = 20     # z-score lookback window
_ATR_W     = 14
_ADX_W     = 14
_BB_W      = 20

# Regime display config
_REGIME_ORDER = {"RANGING": 0, "HIGH_VOLATILITY": 1, "TRENDING_UP": 2, "TRENDING_DOWN": 3}
_REGIME_CLASS = {
    "RANGING":        "pos",
    "HIGH_VOLATILITY":"warn",
    "TRENDING_UP":    "accent",
    "TRENDING_DOWN":  "neg",
}


def _load_pairs() -> list[Path]:
    """Return all 1h CSV paths, excluding 5m/15m files."""  # claude code changed: new
    return sorted(p for p in _FOREX_DIR.glob("*_1h.csv")
                  if "5m" not in p.name and "15m" not in p.name)


def _pair_name(path: Path) -> str:
    """Convert AUD_CAD_1h.csv → AUD/CAD, EURUSD_1h.csv → EUR/USD."""  # claude code changed: new
    stem = path.stem.replace("_1h", "")
    parts = stem.split("_")
    if len(parts) == 2:
        return f"{parts[0]}/{parts[1]}"
    # run-together format e.g. EURUSD — split at 3 chars
    if len(stem) == 6:
        return f"{stem[:3]}/{stem[3:]}"
    return stem


def _atr_series(high, low, close, w):
    """Wilder rolling ATR, returns array same length as input."""  # claude code changed: new
    n = len(close)
    tr = np.maximum(high - low,
         np.maximum(np.abs(high - np.roll(close, 1)),
                    np.abs(low  - np.roll(close, 1))))
    tr[0] = high[0] - low[0]
    atr = np.full(n, np.nan)
    if n < w:
        return atr
    atr[w - 1] = tr[:w].mean()
    k = 1.0 / w
    for i in range(w, n):
        atr[i] = atr[i - 1] + k * (tr[i] - atr[i - 1])
    return atr


def _adx_series(high, low, close, w):
    """Wilder ADX(w), returns array same length as input."""  # claude code changed: new
    n = len(close)
    result = np.full(n, np.nan)
    if n < 2 * w:
        return result
    prev_h = np.concatenate([[high[0]],  high[:-1]])
    prev_l = np.concatenate([[low[0]],   low[:-1]])
    prev_c = np.concatenate([[close[0]], close[:-1]])
    tr = np.maximum(high - low, np.maximum(np.abs(high - prev_c), np.abs(low - prev_c)))
    up   = high - prev_h
    dn   = prev_l - low
    pdm  = np.where((up > dn) & (up > 0), up, 0.0)
    mdm  = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr_s = np.zeros(n); pd_s = np.zeros(n); md_s = np.zeros(n)
    tr_s[w-1] = tr[:w].sum(); pd_s[w-1] = pdm[:w].sum(); md_s[w-1] = mdm[:w].sum()
    for i in range(w, n):
        tr_s[i] = tr_s[i-1] - tr_s[i-1]/w + tr[i]
        pd_s[i] = pd_s[i-1] - pd_s[i-1]/w + pdm[i]
        md_s[i] = md_s[i-1] - md_s[i-1]/w + mdm[i]
    with np.errstate(divide="ignore", invalid="ignore"):
        pdi = np.where(tr_s > 0, 100.0 * pd_s / tr_s, 0.0)
        mdi = np.where(tr_s > 0, 100.0 * md_s / tr_s, 0.0)
        sdi = pdi + mdi
        dx  = np.where(sdi > 0, 100.0 * np.abs(pdi - mdi) / sdi, 0.0)
    start = 2 * w - 2
    if start >= n:
        return result
    result[start] = dx[w-1:start+1].mean()
    for i in range(start + 1, n):
        result[i] = (result[i-1] * (w - 1) + dx[i]) / w
    return result


def _compute_pair_metrics(csv_path: Path) -> dict | None:
    """Load last _CANDLES rows and compute all scanner metrics."""  # claude code changed: new
    try:
        # read only the tail we need — skip all but last _CANDLES rows
        total = sum(1 for _ in open(csv_path)) - 1  # row count excl header
        skip  = max(0, total - _CANDLES)
        df = pd.read_csv(csv_path, skiprows=range(1, skip + 1) if skip else None,
                         parse_dates=["timestamp"])
        if len(df) < 60:
            return None

        h = df["high"].to_numpy(dtype=float)
        l = df["low"].to_numpy(dtype=float)
        c = df["close"].to_numpy(dtype=float)
        price = c[-1]
        if price <= 0:
            return None

        # ── ADX ──────────────────────────────────────────────────────────────
        adx_arr = _adx_series(h, l, c, _ADX_W)
        adx = float(adx_arr[-1]) if not np.isnan(adx_arr[-1]) else None

        # ── ATR % of price ────────────────────────────────────────────────────
        atr_arr = _atr_series(h, l, c, _ATR_W)
        atr_val = float(atr_arr[-1]) if not np.isnan(atr_arr[-1]) else None
        atr_pct = round(atr_val / price * 100, 4) if atr_val else None

        # ── ATR ratio (current vs 50-bar average) ─────────────────────────────
        atr_50 = _atr_series(h, l, c, 50)
        if not np.isnan(atr_arr[-1]) and not np.isnan(atr_50[-1]) and atr_50[-1] > 0:
            atr_ratio = round(float(atr_arr[-1] / atr_50[-1]), 2)
        else:
            atr_ratio = None

        # ── Z-score (20-bar close) ─────────────────────────────────────────────
        if len(c) >= _ZSCORE_W:
            window = c[-_ZSCORE_W:]
            mu, sigma = window.mean(), window.std()
            zscore = round(float((c[-1] - mu) / sigma), 2) if sigma > 0 else 0.0
        else:
            zscore = None

        # ── Bollinger Band width % ────────────────────────────────────────────
        if len(c) >= _BB_W:
            w = c[-_BB_W:]
            upper = w.mean() + 2 * w.std()
            lower = w.mean() - 2 * w.std()
            bb_width_pct = round((upper - lower) / price * 100, 3)
            bb_pct_b = round((c[-1] - lower) / (upper - lower), 3) if (upper - lower) > 0 else 0.5
        else:
            bb_width_pct = None
            bb_pct_b = None

        # ── 24h change % ──────────────────────────────────────────────────────
        change_24h = round((c[-1] - c[-25]) / c[-25] * 100, 3) if len(c) >= 25 else None

        # ── EMA spread % ──────────────────────────────────────────────────────
        ema20 = float(pd.Series(c).ewm(span=20, adjust=False).mean().iloc[-1])
        ema50 = float(pd.Series(c).ewm(span=50, adjust=False).mean().iloc[-1])
        ema_spread_pct = round(abs(ema20 - ema50) / price * 100, 4)

        # ── Regime classification ─────────────────────────────────────────────
        # FX-calibrated thresholds: EMA spread 0.02%, BB narrow < 0.3%
        regime = _classify_regime(
            adx=adx,
            atr_ratio=atr_ratio,
            ema_spread_pct=ema_spread_pct,
            bb_width_pct=bb_width_pct,
        )

        # Latest timestamp
        last_ts = str(df["timestamp"].iloc[-1])[:10]

        return {
            "pair":          _pair_name(csv_path),
            "price":         round(price, 5),
            "regime":        regime,
            "regime_label":  regime.replace("_", " ").lower(),  # claude code changed: pre-formatted for template
            "regime_cls":    _REGIME_CLASS.get(regime, ""),
            "adx":           round(adx, 1) if adx is not None else None,
            "atr_pct":       atr_pct,
            "atr_ratio":     atr_ratio,
            "zscore":        zscore,
            "bb_width_pct":  bb_width_pct,
            "bb_pct_b":      bb_pct_b,
            "ema_spread_pct":ema_spread_pct,
            "change_24h":    change_24h,
            "last_ts":       last_ts,
            "regime_order":  _REGIME_ORDER.get(regime, 9),
        }
    except Exception as exc:
        logger.warning("forex_scanner: error computing %s: %s", csv_path.name, exc)
        return None


def _classify_regime(adx, atr_ratio, ema_spread_pct, bb_width_pct) -> str:
    """
    FX-calibrated regime classification.

    Thresholds tuned for FX 1h data (EMA spread in FX is ~100× smaller
    than crypto — use 0.02% instead of 0.5%).
    """  # claude code changed: new — FX-calibrated thresholds
    if adx is None:
        return "RANGING"

    # HIGH_VOLATILITY: ATR ratio > 1.7 (abnormal vol)
    if atr_ratio is not None and atr_ratio > 1.7:
        return "HIGH_VOLATILITY"

    # TRENDING: ADX > 25 AND EMA spread expanding
    if adx > 25:
        if ema_spread_pct is not None and ema_spread_pct > 0.02:
            # Determine direction from EMA relationship
            return "TRENDING_UP"   # direction resolved in caller if needed
        return "TRENDING_UP"       # ADX alone sufficient for trending label

    # RANGING: ADX < 20 or BB compressed
    if adx < 20:
        return "RANGING"
    if bb_width_pct is not None and bb_width_pct < 0.3:
        return "RANGING"

    # 20-25 ADX grey zone — call it RANGING unless something says otherwise
    return "RANGING"


def _build_scanner_data() -> list[dict]:
    """Compute metrics for all 1h FX pairs, sorted ranging-first."""  # claude code changed: new
    paths = _load_pairs()
    rows = []
    for p in paths:
        m = _compute_pair_metrics(p)
        if m:
            rows.append(m)
    # Sort: ranging first, then by ADX ascending within each regime group
    rows.sort(key=lambda r: (r["regime_order"], r["adx"] if r["adx"] is not None else 99))
    return rows


# ── view ──────────────────────────────────────────────────────────────────────

def forex_scanner(request):  # claude code changed: new — Forex Market Scanner view
    rows = cache.get(_CACHE_KEY)
    if rows is None:
        rows = _build_scanner_data()
        cache.set(_CACHE_KEY, rows, _CACHE_TTL)

    # Count by regime for summary badges
    regime_counts = {}
    for r in rows:
        regime_counts[r["regime"]] = regime_counts.get(r["regime"], 0) + 1

    context = {
        "rows":          rows,
        "rows_json":     json.dumps(rows),
        "regime_counts": regime_counts,
        "total_pairs":   len(rows),
    }
    return render(request, "forex_scanner.html", context)
