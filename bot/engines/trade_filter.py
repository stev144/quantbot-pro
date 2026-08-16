def trade_quality_filter(signal):
    """
    Reject low quality trades BEFORE execution.
    
    Returns: (is_quality: bool, reason: str)
    """

    if not signal:
        return False, "signal_is_none"

    sig_type = signal.get("signal")
    if sig_type not in ["BUY", "SELL"]:
        return False, f"invalid_signal_type: {sig_type}"

    entry = signal.get("entry")
    sl = signal.get("sl")
    tp = signal.get("tp")

    if entry is None:
        return False, "missing_entry_price"
    if sl is None:
        return False, "missing_stop_loss"
    if tp is None:
        return False, "missing_take_profit"

    risk = abs(entry - sl)
    reward = abs(tp - entry)

    if risk <= 0:
        return False, "zero_or_negative_risk"

    rr_ratio = reward / risk if risk != 0 else 0

    if rr_ratio < 1.5:
        return False, f"rr_ratio_too_low: {rr_ratio:.2f}"

    if risk < entry * 0.001:
        return False, "risk_too_small"

    return True, "passed"