# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/forex_data_validation.py
# Workstream D — Session-aware forex data validation
#
# claude code changed: new file — 2026-10-16. Reuses the existing bot.quant_core.market_profile
# calendar and gap infrastructure — does NOT invent a second FX calendar. The
# fx_new_york_close_calendar() (OD-FX-1, owner decision 2026-10-06) is the single source of
# the FX session schedule; this module wraps it for DCA-research use cases.
#
# Design decisions:
#   - is_expected_gap(): checks whether a timestamp gap falls within the known FX closure schedule
#     by delegating to SessionCalendar.is_trading(). Never invents a heuristic — uses the owner's
#     decision (calendar from OD-FX-1).
#   - validate_ohlc_candle(): pure arithmetic validation; no market data fetching. Returns a list
#     of error strings so callers can decide what to do (filter, log, raise) rather than deciding
#     for them.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

from typing import List, Optional

import pandas as pd

from bot.quant_core.market_profile import FOREX_PROFILE, fx_new_york_close_calendar  # claude code changed: reuse single-source FX calendar


def is_expected_gap(
    prev_ts: pd.Timestamp,
    curr_ts: pd.Timestamp,
    calendar=None,
) -> bool:
    """
    Returns True when ALL hours between prev_ts and curr_ts (exclusive) fall within
    scheduled market closures on the FX calendar — i.e. the gap is FULLY explained
    by expected closures (weekend, holiday).

    Returns False when ANY hour in the gap falls inside a trading session — meaning
    the gap contains genuinely missing data.

    calendar: a SessionCalendar or ContinuousCalendar instance. Defaults to
    fx_new_york_close_calendar() (OD-FX-1, the owner's FX schedule). Pass explicitly
    for tests or alternate calendars.

    NOTE: this function does NOT distinguish between different gap sources (weekend,
    holiday, broker outage). It only answers "is this gap fully accounted for by
    the known closure schedule?" For gap-source attribution, use FOREX_PROFILE's
    calendar directly.

    Raises ValueError if prev_ts >= curr_ts.
    """  # claude code changed: new — Workstream D; reuses OD-FX-1 calendar, no second calendar
    if not isinstance(prev_ts, pd.Timestamp):
        prev_ts = pd.Timestamp(prev_ts)
    if not isinstance(curr_ts, pd.Timestamp):
        curr_ts = pd.Timestamp(curr_ts)
    if prev_ts.tzinfo is None:
        prev_ts = prev_ts.tz_localize("UTC")
    if curr_ts.tzinfo is None:
        curr_ts = curr_ts.tz_localize("UTC")

    if prev_ts >= curr_ts:
        raise ValueError(f"prev_ts ({prev_ts}) must be < curr_ts ({curr_ts})")

    cal = calendar if calendar is not None else fx_new_york_close_calendar()  # claude code changed: default = OD-FX-1

    # Build the set of hours strictly between prev_ts and curr_ts (not including curr_ts itself,
    # since curr_ts is the NEXT observed candle — the gap is the missing hours in between).
    # prev_ts + 1h is the first "expected" candle that's missing; curr_ts is the first found.
    missing_hours = pd.date_range(
        prev_ts + pd.Timedelta(hours=1),
        curr_ts - pd.Timedelta(hours=1),
        freq="h",
    )

    if len(missing_hours) == 0:
        # Consecutive candles (gap of exactly 1 hour) — no missing hours, not a gap at all
        return True

    trading_flags = cal.is_trading(missing_hours)
    # If ANY missing hour is a trading hour, the gap is NOT fully explained by closures
    return not bool(trading_flags.any())


def validate_ohlc_candle(
    row: dict,
    spec=None,  # ForexInstrumentSpec, Optional — for future price-precision checks
) -> List[str]:
    """
    Validate one OHLC candle row. Returns a list of error strings;
    an empty list means the candle is valid.

    Checks (all pure arithmetic — no market data, no broker connection):
    1. All required columns present: open, high, low, close.
    2. All prices strictly positive.
    3. high >= low.
    4. high >= max(open, close).
    5. low <= min(open, close).
    6. If volume present: volume >= 0.
    7. If timestamp present: it is a valid, non-NaT timestamp.

    spec is unused currently (reserved for future price-precision range checks
    once real broker specs are confirmed — not guessing precision today).
    """  # claude code changed: new — Workstream D; pure validation, no market data
    errors: List[str] = []

    for col in ("open", "high", "low", "close"):
        if col not in row:
            errors.append(f"missing required column: {col!r}")

    # If any required column is missing, further checks are not meaningful
    if errors:
        return errors

    try:
        o = float(row["open"])
        h = float(row["high"])
        lo = float(row["low"])
        c = float(row["close"])
    except (TypeError, ValueError) as e:
        errors.append(f"non-numeric price value: {e}")
        return errors

    if o <= 0:
        errors.append(f"open must be > 0, got {o}")
    if h <= 0:
        errors.append(f"high must be > 0, got {h}")
    if lo <= 0:
        errors.append(f"low must be > 0, got {lo}")
    if c <= 0:
        errors.append(f"close must be > 0, got {c}")

    if errors:
        return errors  # don't run relational checks with invalid prices

    if h < lo:
        errors.append(f"high ({h}) < low ({lo}): impossible OHLC")
    if h < o:
        errors.append(f"high ({h}) < open ({o}): open outside high-low range")
    if h < c:
        errors.append(f"high ({h}) < close ({c}): close outside high-low range")
    if lo > o:
        errors.append(f"low ({lo}) > open ({o}): open outside high-low range")
    if lo > c:
        errors.append(f"low ({lo}) > close ({c}): close outside high-low range")

    if "volume" in row:
        try:
            v = float(row["volume"])
            if v < 0:
                errors.append(f"volume ({v}) is negative")
        except (TypeError, ValueError) as e:
            errors.append(f"non-numeric volume: {e}")

    if "timestamp" in row:
        ts = row["timestamp"]
        if pd.isnull(ts):
            errors.append("timestamp is NaT")

    return errors
