# ============================================================
# bot/templatetags/research_extras.py
# claude code changed: new file — template filter mapping the Research
# Lab's six-state verdict vocabulary (see bot/views/research_lab_data.py
# module docstring) onto the badge CSS classes already defined in
# static/css/terminal.css, so every panel renders verdicts consistently
# instead of repeating the same if/elif chain in every template block.
# ============================================================

from django import template

register = template.Library()

VERDICT_CLASS_MAP = {
    "STATISTICALLY SUPPORTED": "pos",
    "PROMISING": "neu",
    "TESTED": "dim",
    "UNSTABLE": "warn",
    "REQUIRES FURTHER RESEARCH": "warn",
    "REJECTED": "neg",
    # claude code changed: new — bot/research/validated_feature_registry.py's
    # own 5-state taxonomy (SUPPORTED/CONDITIONAL/WEAK/REJECTED/UNTESTED) is
    # distinct from the markdown research log's 6-state vocabulary above.
    # REJECTED already maps identically; the rest didn't exist as keys here
    # and were silently falling through to "dim" (gray) regardless of verdict.
    "SUPPORTED": "pos",
    "CONDITIONAL": "neu",
    # claude code changed: owner decision D-7 — verdict ladder (OBSERVATION is unconfirmed → neutral)
    "OBSERVATION": "neu",
    "STATISTICAL": "pos",
    "ECONOMIC": "pos",
    "TRADEABLE": "pos",
    "CANONICAL": "pos",
    "WEAK": "warn",
    "UNTESTED": "dim",
}


@register.filter
def verdict_class(verdict):
    return VERDICT_CLASS_MAP.get(str(verdict).upper(), "dim")


@register.filter
def as_pct(value):   # claude code changed: 2026-10-26 — fractions (0.02645) shown as percent (2.645); None/'' stay ''
    """Multiply a stored fraction by 100 for display as a percentage. Non-numbers return ''."""
    try:
        return float(value) * 100
    except (TypeError, ValueError):
        return ""


@register.filter
def pvalue(value):   # claude code changed: 2026-10-26 — "0.0000267…" shown as "< 0.0001"; 4 decimals otherwise
    """Display a p-value: '< 0.0001' below that, else 4 decimals. None/'' → 'not evaluated'."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return "not evaluated"
    return "< 0.0001" if v < 0.0001 else f"{v:.4f}"



# claude code changed: 2026-10-27 — plain-English words for the report's engine-step table (one per registered tool)
TOOL_WORDS = {
    "inspect_dataset": "Loaded the price data and checked it: which asset, which dates, which columns, how many candles.",
    "calculate_feature": "Calculated the indicator your idea uses (for example RSI) from the prices.",
    "run_statistical_test": "Measured how well the indicator lined up with what the price did next (Information Coefficient), "
                            "then ran the shuffle test to see whether chance could explain it.",
    "run_fdr_correction": "Adjusted the p-values for the number of related ideas tested, so lucky results are not counted as real.",
    "run_conditional_test": "Compared what the price did next when your condition was true with when it was false, "
                            "then ran the significance tests.",
    "run_cointegration_test": "Tested whether the two prices are tied together over time, and how fast their gap closes.",
    "run_kalman_pairs_test": "Tested the pair with a hedge ratio that adjusts itself over time (a Kalman filter).",
    "run_backtest": "Replayed the strategy on past prices, trade by trade, with costs.",
    "run_parameter_sensitivity": "Re-ran with nearby settings to check the result does not depend on one lucky setting.",
    "run_cross_sectional_ranking_test": "Ranked many assets by the indicator and tested whether the ranking predicted "
                                        "returns on data it had not seen.",
    "run_regime_conditional_test": "Tested the indicator separately in each market regime (trending, ranging, volatile).",
    "run_cross_sectional_regime_conditional_test": "Ran the ranking test separately in each market regime, on unseen data.",
    "generate_research_report": "Wrote up this report.",
}


@register.filter
def tool_words(name):   # claude code changed: 2026-10-27
    """Plain-English description of one research-engine tool; the raw name when it is not in TOOL_WORDS."""
    return TOOL_WORDS.get(name, name)


@register.filter
def p_hint(value):   # claude code changed: 2026-10-27 — the report's "what it means" column
    """'unlikely to be chance alone' below 0.05, 'could easily be chance' otherwise; '' when not evaluated."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    return "Here: below 0.05 — unlikely to be chance alone." if v < 0.05 else "Here: 0.05 or above — could easily be chance."


# claude code changed: 2026-10-27 — ONE source of plain-English explanations for every Research Lab number (owner: "a
# beginner doesn't necessarily know Mann-Whitney, FDR ... make sure the explanation applies to all our research lab
# results"). Used by the report's "What it means" column and the results page's glossary (research_lab/_metric_glossary.html).
P_VALUE_WORDS = ("A p-value is the chance of seeing a result at least this strong if your idea were wrong and only luck "
                 "were at work. The smaller it is, the less likely luck explains it. This platform uses 0.05 (5%) as the line.")

METRIC_WORDS = {
    # conditional event test
    "observations": "How many candles (time periods) were checked in total.",
    "condition_true": "Candles where your rule held — the cases your idea is about.",
    "condition_false": "All the other candles: the \"normal\" group your cases are compared against.",
    "episodes": "Matching candles in a row count as one episode, because they are really one event. This is the honest "
                "count of separate events, so it is smaller than the candle count.",
    "mean_true": "The average price change over the next few candles (the horizon you chose) after your rule held.",
    "mean_baseline": "The same average when your rule did not hold. The gap between the two averages is the effect being tested.",
    "median_true": "The middle value: half the moves were bigger, half smaller. A few huge moves cannot pull it around the "
                   "way they pull the average.",
    "median_baseline": "The middle value when your rule did not hold.",
    "direction": "What your idea predicted (up or down) against what the data showed. If they differ, the data went the "
                 "opposite way to your idea.",
    "mannwhitney": "A quick test of whether the two groups' moves look different. It assumes every candle is independent, "
                   "which is not true here (each forward return overlaps the next, and matching candles come in runs), so it "
                   "often looks far more convincing than it should. Shown for reference; the verdict does not use it.",
    "block_perm": "The stricter test, and the one the verdict uses. It shuffles whole blocks of time many times and counts "
                  "how often luck alone produces a difference this big.",
    "fdr_p": "FDR = false discovery rate. Test enough ideas and some pass by luck, so each p-value is raised to allow for how "
             "many were tested. It only applies when the idea belongs to a registered group of related tests.",
    "passes_fdr": "Whether the result still holds after that adjustment. \"Not applied\" means no adjustment was made, not "
                  "that it passed.",
    # pairs / cointegration
    "coint_p": "Tests whether the two prices are tied together over time — they wander, but never drift far apart. Below "
               "0.05 = evidence they are tied.",
    "adf_p": "A related check on the gap between the two prices using the ordinary tables. Shown for reference only — it is "
             "too lenient for a fitted gap, so the cointegration p-value is the one that counts.",
    "hedge_ratio": "How many units of the second asset balance one unit of the first, so the two together move as one "
                   "\"spread\" (β, beta).",
    "half_life": "How long the gap between the two prices typically takes to close halfway. Shorter means it snaps back faster.",
    "is_coint": "The yes/no answer from the cointegration test alone, before any practical checks.",
    "tradability": "Whether the half-life and other practical limits make the pair realistic to trade, not just "
                   "statistically interesting.",
    # strategy backtest
    "data_window": "The stretch of price history the strategy was replayed on.",
    "trades": "How many trades the strategy made in the replay. Results are measured in R: profit in units of the amount "
              "risked on a trade (+1R = won what was risked).",
    "mean_r": "The average result per trade after costs, in R, and how much single trades varied around it (sd = standard deviation).",
    "t_p": "Tests whether the average R is above zero. A p below 0.05 means a positive average is unlikely to be luck.",
    "bootstrap_ci": "A range the true average R probably lies in (95% confidence), found by re-drawing the trades thousands "
                    "of times. If the range includes 0, a real edge has not been shown.",
    "autocorr": "Whether one trade's result tends to look like the next one's. Near 0 = independent trades; far from 0 means "
                "the tests above may be too optimistic.",
    "win_rate": "The share of trades that made money. A high win rate alone says nothing if the losing trades are large.",
    "profit_factor": "Total profits ÷ total losses. Above 1 = the strategy made more than it lost.",
    "balance": "The account's starting and ending balance in the replay.",
    "max_dd": "The biggest fall from a high point to a later low — the worst losing stretch you would have had to sit through.",
    # information coefficient (feature tests)
    "ic": "How well the indicator's ranking matched what happened next, from −1 to +1. 0 = no link; the further from 0, the "
          "stronger the link (negative = the opposite direction).",
    "ic_block_perm": "Shuffles whole blocks of time many times and counts how often luck alone gives a link this strong.",
    "sample_size": "How many observations the calculation used.",
    # reproducibility
    "research_id": "This experiment's unique name.",
    "code_version": "The exact version of the software that produced these numbers (a git commit).",
    "seed": "The starting number for the random shuffles, so a rerun gives exactly the same results.",
    "created": "When the idea was submitted.",
    "started": "When the engine began testing it.",
    "completed": "When the results were finished.",
    "rerun_of": "The earlier experiment this one repeats.",
}


@register.filter
def means(key):   # claude code changed: 2026-10-27
    """Plain-English explanation of one Research Lab number (METRIC_WORDS); '' for an unknown key."""
    return METRIC_WORDS.get(key, "")


@register.simple_tag
def p_value_words():   # claude code changed: 2026-10-27
    return P_VALUE_WORDS
