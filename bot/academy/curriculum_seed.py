# ============================================================
# bot/academy/curriculum_seed.py
# claude code changed: new file — the Academy's curriculum as data, not
# hardcoded template markup (section 22's requirement). Loaded into the DB
# by bot/management/commands/load_academy_content.py, following this
# project's existing convention of file-based inputs driving a loader
# script (data/*.csv -> fetch_all_symbols.py, research_data/*.csv ->
# run_research_all.py) rather than editing the database by hand.
#
# Every path/course from the approved architecture spec is represented
# here so the full catalog is real and browsable — most courses are
# stubs (is_published=False, no modules yet) until content is authored;
# "Information Coefficient" (Quantitative Research path) is the one
# course fully built out end-to-end, using this project's own real
# research findings as the worked example (section 12).
# ============================================================

# Each course tuple: (slug, title, difficulty, short_description)
_BEGINNER = "beginner"
_INTERMEDIATE = "intermediate"
_ADVANCED = "advanced"


LEARNING_PATHS = [
    {
        "slug": "quant-foundations",
        "title": "Quant Foundations",
        "order": 1,
        "description": "Where systematic trading actually starts: markets, data, and the difference between risk and return.",
        "courses": [
            ("intro-systematic-trading", "Introduction to Systematic Trading", _BEGINNER,
             "What makes a trading approach systematic, and why that discipline matters before any code is written."),
            ("markets-and-market-structure", "Markets and Market Structure", _BEGINNER,
             "How exchanges, order books, and market participants actually work."),
            ("trading-data-fundamentals", "Trading Data Fundamentals", _BEGINNER,
             "OHLCV data, candles, timeframes, and the data-quality problems that quietly break research."),
            ("risk-and-return", "Risk and Return", _BEGINNER,
             "Why return without risk context is meaningless, and how quants actually measure both."),
        ],
    },
    {
        "slug": "python-for-quant-trading",
        "title": "Python for Quant Trading",
        "order": 2,
        "description": "The specific Python toolkit this platform's own research pipeline runs on.",
        "courses": [
            ("python-fundamentals", "Python Fundamentals", _BEGINNER, "Core Python for people who will spend most of their time in pandas."),
            ("numpy-for-quants", "NumPy", _BEGINNER, "Vectorized numerical computing — the foundation everything else here is built on."),
            ("pandas-for-quants", "Pandas", _BEGINNER, "DataFrames, indexing, and the operations this platform's research code uses constantly."),
            ("time-series-data", "Time-Series Data", _INTERMEDIATE, "Resampling, rolling windows, and why time order changes the rules."),
            ("data-cleaning", "Data Cleaning", _INTERMEDIATE, "Missing candles, duplicate timestamps, and outliers — before they become bad research."),
            ("feature-engineering-python", "Feature Engineering", _INTERMEDIATE, "Turning raw price data into candidate research features."),
            ("apis-and-websockets", "APIs and WebSockets", _INTERMEDIATE, "REST vs. streaming data, and how live systems actually get their data."),
        ],
    },
    {
        "slug": "quantitative-research",
        "title": "Quantitative Research",
        "order": 3,
        "description": "How to turn a hunch into evidence — and how to tell the difference between the two.",
        "courses": [
            ("hypothesis-formation", "Hypothesis Formation", _BEGINNER, "Indicators are hypotheses, not evidence — starting a research process the right way."),
            ("predictive-features", "Predictive Features", _INTERMEDIATE, "What makes a feature a genuine candidate versus wishful thinking."),
            ("information-coefficient", "Information Coefficient", _INTERMEDIATE,
             "How to measure predictive power properly, and why a positive result is only the beginning."),
            ("statistical-significance", "Statistical Significance", _INTERMEDIATE, "What a p-value actually tells you, and what it doesn't."),
            ("multiple-testing", "Multiple Testing", _ADVANCED, "Why testing many features at once quietly inflates your false-discovery rate."),
            ("fdr-and-bonferroni", "FDR and Bonferroni", _ADVANCED, "Two corrections, two philosophies, and when to use which."),
            ("autocorrelation", "Autocorrelation", _ADVANCED, "Why overlapping forward-return labels break standard significance tests."),
            ("permutation-testing", "Permutation Testing", _ADVANCED, "Testing significance by shuffling the data itself, not assuming a distribution."),
            ("feature-stability", "Feature Stability", _INTERMEDIATE, "Does it work, or did it just work once?"),
            ("feature-decay", "Feature Decay", _INTERMEDIATE, "Why yesterday's edge quietly becomes tomorrow's noise."),
        ],
    },
    {
        "slug": "strategy-engineering",
        "title": "Strategy Engineering",
        "order": 4,
        "description": "Turning validated research into strategy candidates — still not production, yet.",
        "courses": [
            ("momentum", "Momentum", _INTERMEDIATE, "Trend-following strategy logic and its failure modes."),
            ("mean-reversion", "Mean Reversion", _INTERMEDIATE, "Range-bound strategy logic and its failure modes."),
            ("cross-sectional-strategies", "Cross-Sectional Strategies", _ADVANCED, "Ranking assets against each other instead of trading them in isolation."),
            ("cointegration", "Cointegration", _ADVANCED, "When two assets' prices are statistically tied together over time."),
            ("pairs-trading", "Pairs Trading", _ADVANCED, "Trading the spread between cointegrated assets."),
            ("kalman-filtering", "Kalman Filtering", _ADVANCED, "Tracking a relationship that changes slowly over time, without look-ahead."),
            ("statistical-arbitrage", "Statistical Arbitrage", _ADVANCED, "Where cointegration, filtering, and execution costs meet."),
            ("market-microstructure", "Market Microstructure", _ADVANCED, "Order books, spreads, and depth — the mechanics beneath every fill."),
            ("order-flow-research", "Order-Flow Research", _ADVANCED, "What the order book itself can (and can't) tell you."),
            ("cross-venue-research", "Cross-Venue Research", _ADVANCED, "Comparing the same asset across exchanges without assuming arbitrage."),
        ],
    },
    {
        "slug": "backtesting-and-robustness",
        "title": "Backtesting & Robustness",
        "order": 5,
        "description": "A backtest is not proof of an edge. This path is about proving that to yourself, rigorously.",
        "courses": [
            ("backtesting-fundamentals", "Backtesting Fundamentals", _BEGINNER, "What a backtest can and cannot tell you."),
            ("look-ahead-bias", "Look-Ahead Bias", _INTERMEDIATE, "The most common way research accidentally cheats."),
            ("train-test-separation", "Train/Test Separation", _INTERMEDIATE, "Why testing on the data you tuned on tells you nothing."),
            ("walk-forward-testing", "Walk-Forward Testing", _ADVANCED, "Simulating how a strategy would have been re-validated over time."),
            ("permutation-testing-backtests", "Permutation Testing", _ADVANCED, "Applying shuffled-data significance testing to full strategy backtests."),
            ("monte-carlo-testing", "Monte Carlo Testing", _ADVANCED, "Stress-testing a strategy's trade sequence, not just its average result."),
            ("transaction-costs", "Transaction Costs", _INTERMEDIATE, "Why an edge that ignores costs isn't an edge."),
            ("slippage", "Slippage", _INTERMEDIATE, "The gap between the price you wanted and the price you got."),
            ("parameter-stability", "Parameter Stability", _ADVANCED, "If a small parameter change ruins the result, the result wasn't real."),
            ("regime-robustness", "Regime Robustness", _ADVANCED, "Does the strategy survive market conditions it wasn't tuned on?"),
        ],
    },
    {
        "slug": "execution-engineering",
        "title": "Execution Engineering",
        "order": 6,
        "description": "Research becomes real the moment an order is sent — and everything gets harder.",
        "courses": [
            ("exchange-apis", "Exchange APIs", _INTERMEDIATE, "How trading systems actually talk to exchanges."),
            ("rest-apis", "REST", _BEGINNER, "Request/response trading APIs."),
            ("websockets-execution", "WebSockets", _INTERMEDIATE, "Streaming market data and order updates."),
            ("order-books", "Order Books", _INTERMEDIATE, "Reading and reasoning about live liquidity."),
            ("order-management", "Order Management", _ADVANCED, "Placing, tracking, and reconciling real orders safely."),
            ("execution-algorithms", "Execution Algorithms", _ADVANCED, "How large or sensitive orders actually get worked."),
            ("idempotency", "Idempotency", _ADVANCED, "Why the same request must never accidentally happen twice."),
            ("position-reconciliation", "Position Reconciliation", _ADVANCED, "Making sure your system's belief about a position matches reality."),
            ("venue-comparison", "Venue Comparison", _INTERMEDIATE, "Comparing execution quality across exchanges honestly."),
            ("paper-trading", "Paper Trading", _INTERMEDIATE, "The step between backtesting and risking real capital."),
        ],
    },
    {
        "slug": "risk-engineering",
        "title": "Risk Engineering",
        "order": 7,
        "description": "Risk management isn't a bolt-on — it's part of the strategy itself.",
        "courses": [
            ("position-sizing", "Position Sizing", _BEGINNER, "How much to risk on any single trade, and why."),
            ("portfolio-risk", "Portfolio Risk", _INTERMEDIATE, "Risk across many positions at once, not just one."),
            ("drawdown", "Drawdown", _INTERMEDIATE, "Measuring and surviving losing streaks."),
            ("correlation-risk", "Correlation", _INTERMEDIATE, "Why ten uncorrelated-looking bets can secretly be one big bet."),
            ("exposure", "Exposure", _INTERMEDIATE, "Tracking what you actually have at risk, in real time."),
            ("risk-of-ruin", "Risk of Ruin", _ADVANCED, "The mathematics of how a strategy can wipe out an account."),
            ("kill-switches", "Kill Switches", _ADVANCED, "Automated circuit breakers for when something goes wrong."),
            ("cross-venue-risk", "Cross-Venue Risk", _ADVANCED, "Risk that spans exchanges, not just symbols."),
            ("multi-position-risk", "Multi-Position Risk", _ADVANCED, "What happens when many positions can be open at once."),
        ],
    },
    {
        "slug": "multi-asset-quant-research",
        "title": "Multi-Asset Quant Research",
        "order": 8,
        "description": "Crypto, forex, and equities don't just have different tickers — they have different rules.",
        "courses": [
            ("crypto-market-research", "Crypto Markets", _INTERMEDIATE, "Market structure, liquidity, and research methodology specific to crypto."),
            ("forex-market-research", "Forex Markets", _INTERMEDIATE, "Trading hours, leverage, and settlement conventions specific to FX."),
            ("equities-market-research", "U.S. Equities Markets", _INTERMEDIATE, "Shorting rules, settlement, and data conventions specific to equities."),
        ],
    },
    {
        "slug": "production-quant-systems",
        "title": "Production Quant Systems",
        "order": 9,
        "description": "The gap between a working backtest and a system you can trust with real capital.",
        "courses": [
            ("research-to-production-architecture", "Research-to-Production Architecture", _ADVANCED, "The full chain this platform is built around, end to end."),
            ("strategy-gating", "Strategy Gating", _ADVANCED, "Why a strategy shouldn't trade just because it exists in code."),
            ("paper-trading-production", "Paper Trading", _INTERMEDIATE, "Validating execution behavior before risking capital."),
            ("execution-monitoring", "Execution Monitoring", _ADVANCED, "Knowing what a live system is actually doing right now."),
            ("reconciliation", "Reconciliation", _ADVANCED, "Catching the gap between what you think happened and what did."),
            ("observability", "Observability", _INTERMEDIATE, "Making a running system's internal state legible to a human."),
            ("deployment", "Deployment", _INTERMEDIATE, "Shipping changes to a system that's already trading."),
            ("failure-recovery", "Failure Recovery", _ADVANCED, "What a system should do when it crashes mid-trade."),
            ("production-security", "Security", _INTERMEDIATE, "Protecting credentials, secrets, and the production execution path."),
            ("production-readiness", "Production Readiness", _ADVANCED, "How to honestly assess whether a system is actually ready."),
        ],
    },
]


# claude code changed: new — the one fully-authored course, referenced by
# (path_slug, course_slug) so the loader can attach it after creating the
# stub Course row from LEARNING_PATHS above.
FULLY_AUTHORED_COURSE = {
    "path_slug": "quantitative-research",
    "course_slug": "information-coefficient",
    "modules": [
        {
            "title": "Understanding IC",
            "order": 1,
            "lessons": [
                {"slug": "what-is-ic", "title": "What Is the Information Coefficient?", "order": 1,
                 "content_file": "quantitative_research/information_coefficient/01_what_is_ic.md"},
                {"slug": "ic-alone-isnt-enough", "title": "Why IC Alone Isn't Enough", "order": 2,
                 "content_file": "quantitative_research/information_coefficient/02_ic_alone_isnt_enough.md"},
                {"slug": "multiple-testing-and-autocorrelation", "title": "Multiple Testing and Autocorrelation", "order": 3,
                 "content_file": "quantitative_research/information_coefficient/03_multiple_testing_and_autocorrelation.md"},
                {"slug": "from-ic-to-verdict", "title": "From IC to a Research Verdict", "order": 4,
                 "content_file": "quantitative_research/information_coefficient/04_from_ic_to_verdict.md"},
            ],
        },
    ],
    "quiz": {
        "title": "Information Coefficient — Course Quiz",
        "passing_score": 70,
        "questions": [
            {
                "question_type": "multiple_choice",
                "prompt": "An IC of 0.06 on real trading data most likely represents:",
                "payload": {
                    "choices": [
                        "No relationship at all — too small to matter",
                        "A small but potentially real, tradable relationship worth further validation",
                        "Proof of a strong, immediately tradable edge",
                        "A calculation error, since IC should always be near 1.0 for real features",
                    ],
                    "correct_index": 1,
                },
                "explanation": "Real, useful ICs in liquid markets are often small in absolute terms. Small does not mean useless — it means the investigation isn't over yet.",
            },
            {
                "question_type": "true_false",
                "prompt": "A feature with a p-value below 0.05 automatically proves it has a tradable edge.",
                "payload": {"correct": False},
                "explanation": "Statistical significance alone says nothing about economic significance, multiple-testing exposure, or whether the significance test's independence assumption even holds.",
            },
            {
                "question_type": "true_false",
                "prompt": "Applying a multiple-testing correction separately to each symbol's features, instead of across every symbol and feature tested, is a stricter (more conservative) correction.",
                "payload": {"correct": False},
                "explanation": "It's the opposite — correcting within small, separate groups is LESS strict than correcting across the true, larger hypothesis family. This is a real bug this platform's own pipeline had and fixed.",
            },
            {
                "question_type": "research_interpretation",
                "prompt": (
                    "A feature has IC = 0.02 with p < 0.01 across 40,000 observations, but its quartile return "
                    "spread is 0.0008 against a 0.5% (0.005) economic-significance threshold. Is this feature "
                    "production eligible?"
                ),
                "payload": {"correct_verdict": "no"},
                "explanation": (
                    "No — statistically real but economically negligible, exactly like RSI in this course's "
                    "worked example. Statistical significance and economic significance are separate questions, "
                    "and both must pass."
                ),
            },
            {
                "question_type": "research_decision",
                "prompt": (
                    "A feature passes IC testing, survives family-wide multiple-testing correction, and clears "
                    "the economic-significance bar — but has never been checked for temporal stability. "
                    "What is the correct verdict?"
                ),
                "payload": {"correct_verdict": "REVIEW"},
                "explanation": (
                    "REVIEW, not KEEP — missing evidence is treated as insufficient, not as a pass. A feature "
                    "that has never been stability-tested might be decaying or regime-specific; the platform's "
                    "own gating logic downgrades exactly this case rather than assuming it's fine."
                ),
            },
        ],
    },
}
