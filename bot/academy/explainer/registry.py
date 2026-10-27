# claude code changed: 2026-10-26 — new file. The Research Explainer's approved concept definitions: the ONLY source
# the AI may explain from. Every implementation reference below was read in the code on 2026-10-26 and is checked by
# bot/tests/test_explainer.py (the module and attribute must import), so a stale reference fails the suite.
# Fields not genuinely known are left empty — never filled in to look complete.
#
# Bump REGISTRY_VERSION whenever any definition changes: it is part of the answer-cache key, so cached AI answers built
# from an older definition are never served again.

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple

REGISTRY_VERSION = "2026-10-26.4"


@dataclass(frozen=True)
class Concept:
    id: str
    name: str
    summary: str                                   # one plain-English sentence
    definition: str
    formula: str
    units: str = ""
    inputs: str = ""
    window: str = ""
    data_source: str = ""
    implementation: Tuple[Tuple[str, str], ...] = ()   # (module path, attribute path) — shown locally, never sent to the model
    tests: Tuple[str, ...] = ()                    # test files that exercise it (existing ≠ passed)
    methodology_version: str = ""
    limitations: Tuple[str, ...] = ()
    intended_use: str = ""
    evidence_patterns: Tuple[str, ...] = ()        # research_data/ artifact globs, classified by the evidence registry
    related: Tuple[str, ...] = ()
    suggested_questions: Tuple[str, ...] = ()
    academy_lessons: Tuple[Tuple[str, str, str], ...] = ()   # (course_slug, lesson_slug, title) of approved Academy lessons
    prerequisites: Tuple[str, ...] = ()            # claude code changed: 2026-10-26 — concepts a beginner needs first


CONCEPTS: Dict[str, Concept] = {c.id: c for c in [
    # claude code changed: 2026-10-26 — building blocks taught before cointegration (owner: beginners need these first)
    Concept(
        id="correlation",
        name="Correlation",
        summary="How closely two things tend to move up and down together, on a scale from −1 to +1.",
        definition=("Correlation measures how consistently two series move in the same direction at the same time. "
                    "+1 means they always move together in a straight-line way, −1 means always opposite, 0 means no "
                    "straight-line relationship. In markets it is measured on returns (the percentage changes), not on "
                    "the price levels, because two unrelated trending prices can look strongly 'correlated' by accident."),
        formula="ρ(X, Y) = cov(X, Y) / (sd(X) · sd(Y));  sample version: Σ(xᵢ − x̄)(yᵢ − ȳ) / √(Σ(xᵢ − x̄)² · Σ(yᵢ − ȳ)²).",
        units="Dimensionless, from −1 to +1.",
        inputs="Two return series on the same timestamps.",
        implementation=(("bot.views.home_intelligence", "correlation_summary"),
                        ("bot.views.feature_intelligence_data", "get_feature_correlation_matrix")),
        tests=("bot/tests/test_home_intelligence.py",),
        limitations=(
            "Correlation is not causation: two assets can move together because of a shared third cause.",
            "It only captures straight-line relationships, and it changes over time (it often jumps towards 1 in crashes).",
            "High correlation of returns does not mean the prices stay close together — that is a different property "
            "(cointegration).",
        ),
        intended_use="Describing co-movement and diversification.",
        related=("cointegration", "spread"),
        suggested_questions=("What is correlation?", "What is the difference between correlation and cointegration?"),
    ),
    Concept(
        id="spread",
        name="Spread (between two prices)",
        summary="The gap between two prices after scaling one of them — the quantity pairs research watches.",
        definition=("A spread measures how far apart two prices are. The simplest spread is A − B. When the two assets "
                    "have different sizes or sensitivities, one is scaled first: in our pairs research the spread is the "
                    "log price of A minus a fitted amount (β) of the log price of B, minus a constant. If the spread "
                    "keeps returning to its usual level, the pair is a candidate for further research; if it wanders "
                    "off and never comes back, it is not."),
        formula="spread_t = log(A_t) − α̂ − β̂ · log(B_t), with α̂ and β̂ fitted by OLS on the training window.",
        units="Log-price units (approximately a percentage gap for small values).",
        inputs="Two aligned close-price series and the fitted hedge ratio.",
        implementation=(("bot.research.cointegration_engine", "CointegrationEngine._calculate_spread"),),
        tests=("bot/tests/test_cointegration_engine.py",),
        limitations=(
            "A spread that has stayed in a band in the past can break out permanently later.",
            "The hedge ratio is estimated, so the spread carries estimation error.",
        ),
        intended_use="The series whose behaviour cointegration and half-life describe.",
        related=("cointegration", "ols_hedge_ratio", "stationarity"),
        prerequisites=("correlation",),
        suggested_questions=("What is a spread between two assets?",),
    ),
    Concept(
        id="stationarity",
        name="Stationarity",
        summary="A series is stationary when its typical level and wobble stay the same over time instead of drifting.",
        definition=("A stationary series keeps returning to the same average and varies by a similar amount in every "
                    "period, like the temperature of a room with a thermostat. A non-stationary series, like most "
                    "prices, can drift anywhere and has no level it must come back to. Many statistical tools assume "
                    "stationarity, and cointegration is exactly the question of whether a combination of two "
                    "non-stationary prices is stationary."),
        formula=("Weak stationarity: E[y_t] = μ and Var(y_t) = σ² for all t, and Cov(y_t, y_{t−k}) depends only on k. "
                 "A random walk y_t = y_{t−1} + ε_t is the standard non-stationary example (its variance grows with t)."),
        units="A property of a series (tested, e.g. with the ADF test).",
        implementation=(("bot.quant_core.cointegration", "persistence_adf"),),
        tests=("bot/tests/test_quant_core.py",),
        limitations=(
            "No finite sample can prove stationarity; tests can only find evidence against a unit root.",
            "A series can look stationary in one period and break in the next (structural breaks).",
        ),
        intended_use="Deciding which statistical tools are valid, and the core idea behind cointegration.",
        related=("adf_test", "spread", "cointegration"),
        suggested_questions=("What does stationary mean?",),
    ),
    Concept(
        id="z_score",
        name="Z-score (rolling, single asset)",
        summary="How many standard deviations the latest price sits above or below its own recent average.",
        definition=("A standardised distance: the latest close minus its average over the last 20 bars, divided by the "
                    "standard deviation of those 20 closes. 0 means 'at its recent average'; +2 means two standard "
                    "deviations above it."),
        formula="z_t = (P_t − mean(P_{t−19..t})) / sd(P_{t−19..t});  sd is the sample standard deviation (n − 1); "
                "z is set to missing when sd = 0 and clipped to the range [−5, +5].",
        units="Standard deviations (dimensionless).",
        inputs="Close price P.",
        window="20 bars, including the current bar.",
        data_source="Hourly OHLC files (data/*.csv, data/forex/*.csv).",
        implementation=(("bot.research.feature_calculator", "FeatureCalculator._calculate_z_score"),),
        tests=("bot/tests/test_feature_calculator.py",),
        limitations=(
            "It is computed on prices, which trend; a high z can persist for a long time in a trend.",
            "An extreme value does not predict a reversal — it only says the price is far from its recent average.",
            "'95% of values lie within ±2' holds only for normally distributed, independent data; market prices have fat "
            "tails and strongly autocorrelated rolling windows, so that rule of thumb is approximate at best.",
            "Because the window includes the current bar, |z| can never exceed (n − 1)/√n = 19/√20 ≈ 4.25 "
            "(Samuelson's inequality), so the ±5 clip never actually binds and a 20-bar z-score cannot express "
            "moves larger than about 4.25 standard deviations.",   # claude code changed: 2026-10-26 — corrected (Stage F)
        ),
        intended_use="Descriptive input (for example the forex scanner's ranging-entry check); not a trading signal.",
        evidence_patterns=("*_validated_features.csv",),
        related=("cross_sectional_zscore", "information_coefficient"),
        suggested_questions=("What is a Z-score?", "Does a Z-score of +3 mean the price will fall?"),
    ),
    Concept(
        id="cross_sectional_zscore",
        prerequisites=("z_score",),   # claude code changed: 2026-10-26
        name="Cross-sectional Z-score",
        summary="How unusual one asset's return is compared with all the other assets over the same period.",
        definition=("At one moment, take every asset's return, subtract the average return across assets and divide by "
                    "the standard deviation across assets. It compares assets with each other at the same time, not "
                    "one asset with its own history."),
        formula="z_{i,t} = (r_{i,t} − mean_j(r_{j,t})) / sd_j(r_{j,t});  sample sd (n − 1); at least 4 assets; "
                "zero variance → undefined.",
        units="Standard deviations across assets (dimensionless).",
        inputs=("Research pipeline: 1-hour returns, causally winsorised (each row clipped at the 1st/99th percentile of "
                "its own past, after 1,000 observations). Home page: Binance's rolling 24-hour change, not winsorised."),
        window="One timestamp (research: one hour; home page: the last 24 hours).",
        data_source="Research: data/*_1h.csv via the cross-section engine. Home page: Binance 24 h ticker (live).",
        implementation=(("bot.research.cross_section_engine", "CrossSectionEngine._calculate_cross_sectional_features"),
                        ("bot.views.home_intelligence", "cross_section")),
        tests=("bot/tests/test_cross_section_engine.py", "bot/tests/test_home_intelligence.py"),
        limitations=(
            "Relative, not absolute: in a market-wide crash the 'strongest' coin can still be falling.",
            "One extreme outlier widens the standard deviation and shrinks every other asset's |z|.",
            "A large |z| is not a mispricing, a reversal forecast or evidence of alpha.",
            "Cross-sectional strategy research on this universe found no edge that survived transaction costs.",
        ),
        intended_use="Describing relative performance and choosing what to research; not an entry signal.",
        evidence_patterns=("*_cross_section.csv", "cross_sectional_checkpoints/*", "forex_cross_sectional_family_verdict.json"),
        related=("cross_sectional_rank", "dispersion", "z_score"),
        suggested_questions=("What does a cross-sectional Z-score of −3 mean?",),
    ),
    Concept(
        id="cross_sectional_rank",
        name="Cross-sectional ranking",
        summary="Ordering assets by their return at the same moment, from weakest to strongest (or the reverse).",
        definition=("Each asset receives its position in the sorted list of returns at one timestamp. The research "
                    "pipeline also rescales the rank to −1…+1 and to a 0–100 percentile."),
        formula=("Research: cs_rank = rank of r_i ascending (1 = lowest return), ties get the average rank; "
                 "cs_rank_norm = (rank − (N+1)/2) / ((N+1)/2); cs_percentile = percentile rank. "
                 "Home page: rank 1 = highest 24 h return, ties ordered alphabetically by symbol."),
        units="Position (1…N), or −1…+1, or 0–100.",
        inputs="Same returns as the cross-sectional Z-score.",
        window="One timestamp.",
        implementation=(("bot.research.cross_section_engine", "CrossSectionEngine._calculate_cross_sectional_features"),
                        ("bot.views.home_intelligence", "cross_section")),
        tests=("bot/tests/test_cross_section_engine.py", "bot/tests/test_home_intelligence.py"),
        limitations=(
            "The two places use opposite directions: in the research files rank 1 is the WEAKEST asset, on the home page "
            "rank 1 is the STRONGEST. Always check which convention a table states.",
            "A rank ignores distance: the gap between rank 1 and 2 may be huge or tiny.",
        ),
        intended_use="Description and research selection.",
        evidence_patterns=("*_cross_section.csv",),
        related=("cross_sectional_zscore",),
        suggested_questions=("What does cross-sectional ranking mean?",),
    ),
    Concept(
        id="dispersion",
        prerequisites=("cross_sectional_zscore",),   # claude code changed: 2026-10-26
        name="Cross-sectional dispersion",
        summary="How spread out the assets' returns are at the same moment.",
        definition=("The standard deviation of the assets' returns at one moment. High dispersion means assets are "
                    "behaving differently from each other; low dispersion means they are moving together."),
        formula=("Home page: sd_j(r_{j}) of the eligible coins' 24 h returns (sample sd), in percentage points, plus the "
                 "inter-quartile range. Market Intelligence (research): sd of the latest cs_zscore values at the most "
                 "common latest timestamp."),
        units="Percentage points of return (home page); standard deviations of z (research reading).",
        window="Home page: last 24 h. Research reading: one hour.",
        data_source="Home page: Binance 24 h ticker. Research: research_data/*_cross_section.csv.",
        implementation=(("bot.views.home_intelligence", "cross_section"),
                        ("bot.views.terminal_data", "cross_sectional_dispersion_at_common_timestamp")),
        tests=("bot/tests/test_home_intelligence.py",),
        limitations=(
            "The research reading is the standard deviation of values that were already scaled to a standard deviation "
            "of 1 across the same cross-section, so it is close to 1 by construction (measured 1.016 on the 2026-09-24 "
            "data, 119 symbols) and carries almost no information. The home page's version uses raw returns and does "
            "not have this problem.",
            "High dispersion does not mean profitable opportunities exist.",
            "Not winsorised on the home page: one extreme coin can dominate it.",
        ),
        intended_use="Describing market structure.",
        related=("cross_sectional_zscore",),
        suggested_questions=("How does our system calculate dispersion?",),
    ),
    Concept(
        id="ols_hedge_ratio",
        prerequisites=("correlation",),   # claude code changed: 2026-10-26
        name="OLS hedge ratio (pairs)",
        summary="The straight-line relationship between two assets' log prices, estimated by least squares.",
        definition=("Ordinary least squares fits a straight line through the log price of asset A against the log price "
                    "of asset B on the training window only. The slope β is the hedge ratio; what is left over is the "
                    "spread that the cointegration test then examines."),
        formula="log(A_t) = α + β·log(B_t) + ε_t;  (α̂, β̂) minimise Σ ε_t²;  spread_t = log(A_t) − α̂ − β̂·log(B_t).",
        units="β: dimensionless (log-price elasticity).",
        inputs="Close prices of the two assets, aligned on the same timestamps, logged.",
        window="The engine's training window only (no data after it), to avoid look-ahead.",
        data_source="data/*_1h.csv.",
        implementation=(("bot.quant_core.cointegration", "ols_hedge"),
                        ("bot.research.cointegration_engine", "CointegrationEngine._test_pair")),
        tests=("bot/tests/test_quant_core.py", "bot/tests/test_cointegration_engine.py"),
        limitations=(
            "β is an estimate: the spread inherits its estimation error, which is why ordinary ADF critical values do not "
            "apply to it.",
            "Regressing A on B gives a different β from B on A.",
            "Assumes β is constant over the window; real relationships drift.",
            "A good fit is not evidence that the two assets are economically linked or that the spread will mean-revert.",
        ),
        intended_use="First step of the Engle–Granger procedure.",
        evidence_patterns=("cointegration_pairs.csv", "*_cointegration.csv", "forex_cointegration_pairs*.csv"),
        related=("cointegration", "adf_test"),
        suggested_questions=("What is the purpose of OLS in pairs research?",),
    ),
    Concept(
        id="adf_test",
        prerequisites=("stationarity",),   # claude code changed: 2026-10-26
        name="Augmented Dickey–Fuller (ADF) test",
        summary="A test of whether a series wanders without returning (a unit root) or tends to revert.",
        definition=("The null hypothesis is that the series has a unit root (it is non-stationary). The test regresses "
                    "the change in the series on its previous level plus lagged changes; a strongly negative "
                    "coefficient on the level is evidence against the null."),
        formula="Δy_t = c + γ·y_{t−1} + Σ_{j=1..p} φ_j·Δy_{t−j} + e_t;  H0: γ = 0 (unit root);  statistic = t-ratio of γ̂; "
                "our calls use a constant, up to 20 lags chosen by AIC.",
        units="Test statistic and p-value.",
        inputs="A single series (here: a pair's spread).",
        implementation=(("bot.quant_core.cointegration", "persistence_adf"),
                        ("bot.research.cointegration_engine", "CointegrationEngine._test_pair")),
        tests=("bot/tests/test_quant_core.py", "bot/tests/test_cointegration_engine.py"),
        limitations=(
            "Applied to a residual from an estimated regression, ordinary ADF critical values are wrong (too lenient); "
            "our engine therefore records the plain ADF p-value only as a diagnostic and selects pairs with the "
            "Engle–Granger test, which uses MacKinnon's residual-based critical values.",
            "Failing to reject the null does not prove a unit root; the test has low power in short samples.",
            "Sensitive to the number of lags and to structural breaks.",
        ),
        intended_use=("Diagnostic on the training residual; persistence check on the validation-window residual built "
                      "with the frozen training coefficients."),
        related=("cointegration", "ols_hedge_ratio"),
        suggested_questions=("What is the Augmented Dickey-Fuller test?",),
    ),
    Concept(
        id="cointegration",
        prerequisites=("correlation", "spread", "stationarity", "ols_hedge_ratio"),   # claude code changed: 2026-10-26
        name="Cointegration (Engle–Granger procedure)",
        summary="Two non-stationary prices whose particular combination stays stationary — they drift together.",
        definition=("Two series that each wander (are integrated of order one) are cointegrated if some linear "
                    "combination of them is stationary. Our engine tests this with the Engle–Granger two-step "
                    "procedure on log prices, on the training window only."),
        formula=("1) OLS: log A = α + β·log B + ε.  2) Unit-root test on the estimated residuals with MacKinnon residual-based critical values "
                 "(statsmodels coint, constant term, lag order by AIC).  Selection: Engle–Granger p-value below the "
                 "threshold after Benjamini–Hochberg false-discovery-rate correction across every pair tested; a pair "
                 "whose test cannot be computed is UNTESTABLE (counted with p = 1). Then a half-life filter and an "
                 "out-of-sample persistence ADF with frozen coefficients."),
        units="p-values; hedge ratio β; half-life in candles.",
        inputs="Aligned log close prices of two assets.",
        window="Training window for selection; a later validation window for the persistence check.",
        data_source="data/*_1h.csv (crypto), data/forex/*.csv (forex).",
        implementation=(("bot.quant_core.cointegration", "engle_granger"),
                        ("bot.research.cointegration_engine", "CointegrationEngine._test_pair"),
                        ("bot.research.cointegration_engine", "CointegrationEngine._apply_fdr_correction")),
        tests=("bot/tests/test_cointegration_engine.py", "bot/tests/test_quant_core.py",
               "bot/tests/test_coint_selection_zone.py"),
        limitations=(
            "Cointegration is not correlation: two highly correlated prices need not be cointegrated, and vice versa.",
            "It says a combination is stationary over the sample; it does not guarantee convergence at a tradable "
            "horizon, nor any profit after costs.",
            "Relationships break; a pair selected on one period often fails later.",
            "Testing many pairs produces false discoveries; FDR correction reduces but does not remove this.",
            "This platform's earlier pairs results are classified INVALIDATED or SUPERSEDED in the evidence registry "
            "(a P&L-measurement artefact and a flawed permutation null); none is current evidence.",
        ),
        intended_use="Selecting candidate pairs for further research; never on its own a reason to trade.",
        evidence_patterns=("cointegration_pairs.csv", "*_cointegration.csv", "forex_cointegration_pairs*.csv",
                           "permutation_test/*", "walk_forward/*", "crrp_retest_2026-10-02/*"),
        related=("ols_hedge_ratio", "adf_test", "ou_half_life"),
        suggested_questions=("What is cointegration?", "How did our Engle-Granger procedure work?",
                             "Did any pair actually pass our validation tests?"),
    ),
    Concept(
        id="ou_half_life",
        prerequisites=("spread", "stationarity"),   # claude code changed: 2026-10-26
        name="Ornstein–Uhlenbeck half-life",
        summary="How many candles a spread takes, on average, to close half of its distance to equilibrium.",
        definition=("A mean-reverting spread is modelled as an Ornstein–Uhlenbeck process. In discrete time this is an "
                    "AR(1) regression of the spread's change on its previous level; the half-life follows from the "
                    "estimated reversion speed."),
        formula="dS = λ(μ − S)dt + σdW;  discrete: ΔS_t = α + λ·S_{t−1} + ε_t (OLS);  HL = −ln 2 / λ̂ candles "
                "(infinite when λ̂ ≥ 0 or the fit fails); converted to hours/days with the candle timeframe.",
        units="Candles (then wall-clock time via the timeframe).",
        inputs="The training-window spread.",
        window="Training window.",
        implementation=(("bot.research.cointegration_engine", "CointegrationEngine._estimate_half_life"),),
        tests=("bot/tests/test_cointegration_engine.py",),
        limitations=(
            "λ̂ is noisy; in finite samples the AR(1) coefficient is biased downward, which tends to make the half-life "
            "look shorter than it is.",
            "It is an average decay of the expected deviation, not a promise about when a trade will be in profit.",
            "Assumes a constant reversion speed.",
        ),
        intended_use="Filtering pairs whose reversion is too slow or implausibly fast.",
        evidence_patterns=("*_kalman_half_life.csv", "*_cointegration.csv"),
        related=("cointegration",),
        suggested_questions=("How do we estimate Ornstein-Uhlenbeck half-life?",),
    ),
    Concept(
        id="information_coefficient",
        prerequisites=("correlation",),   # claude code changed: 2026-10-26
        name="Information Coefficient (IC)",
        summary="The rank correlation between a feature now and the return that follows.",
        definition=("The Spearman rank correlation between a feature's value at time t and the forward return label. "
                    "The platform's thresholds are |IC| > 0.05 acceptable, > 0.10 good, > 0.15 excellent."),
        formula="IC = Spearman ρ(feature_t, forward_return_t);  canonical label (decision D-1): "
                "forward_return_t = open(t+1+N) / open(t+1) − 1 (enter at the next bar's open), default N = 4 candles.",
        units="Correlation, −1 to +1.",
        inputs="A feature column and the forward-return label.",
        window="The whole sample per symbol (overall IC), plus stability across sub-periods.",
        data_source="research_data/*_validated_features.csv (feature validator output).",
        implementation=(("bot.research.feature_validator", "FeatureValidator._validate_single_feature_institutional"),
                        ("bot.quant_core.timing", "research_forward_return")),
        tests=("bot/tests/test_feature_validator.py",),
        limitations=(
            "4-candle labels on 1-hour data overlap, so neighbouring observations are not independent and naive "
            "p-values are too optimistic.",
            "Many features × many symbols are tested: some will look significant by chance (multiple testing).",
            "A real IC can be economically worthless after costs (the platform's RSI result: statistically significant "
            "on 20/20 symbols, yet far below the 0.5% economic-significance threshold).",
            "The saved feature tables are classified LEGACY_DESCRIPTIVE in the evidence registry.",
        ),
        intended_use="Screening features for further research.",
        evidence_patterns=("*_validated_features.csv", "validated_features_institutional.csv"),
        related=("significance_vs_costs", "in_sample_vs_oos"),
        suggested_questions=("What is Information Coefficient?",),
        academy_lessons=(("information-coefficient", "what-is-ic", "What Is the Information Coefficient?"),
                         ("information-coefficient", "ic-alone-isnt-enough", "Why IC Alone Isn't Enough"),
                         ("information-coefficient", "multiple-testing-and-autocorrelation",
                          "Multiple Testing and Autocorrelation")),
    ),
    Concept(
        id="in_sample_vs_oos",
        name="In-sample vs out-of-sample",
        summary="Results on the data a method was tuned on vs. results on data it never saw.",
        definition=("In-sample results come from the data used to choose or fit a method, so they are optimistic. "
                    "Out-of-sample results come from later data the method never touched. This platform splits history "
                    "into fixed research zones and keeps a sealed forward holdout."),
        formula=("Zones (QBP-ZONES-1): SELECTION 2021-11-07 → 2024-03-31; EMBARGO 504 candles; VALIDATION 2024-04-22 → "
                 "2026-09-24; HOLDOUT from 2026-10-04 00:00 UTC, sealed, evaluated once at 2027-04-04 (extended while "
                 "fewer than 30 trades)."),
        units="Time periods.",
        implementation=(("bot.quant_core.research_zones", "ResearchZones"),),
        tests=("bot/tests/test_research_zones.py",),
        limitations=(
            "Every candle before the holdout has been looked at, so all history before 2026-10-04 is a 'pseudo-holdout' "
            "and not claim-eligible.",
            "Re-using an out-of-sample period to pick between methods turns it into in-sample data.",
        ),
        intended_use="Deciding which results may be cited as evidence.",
        related=("information_coefficient", "significance_vs_costs"),
        suggested_questions=("What is the difference between in-sample and out-of-sample performance?",),
    ),
    Concept(
        id="significance_vs_costs",
        name="Statistical significance vs. profit after costs",
        summary="A real, statistically significant effect can still lose money once trading costs are paid.",
        definition=("A significance test asks whether an effect is distinguishable from zero. With enough data, tiny "
                    "effects become 'significant'. Trading pays fees, spread and slippage on every entry and exit, so "
                    "the effect must be larger than the round-trip cost to make money."),
        formula=("Net expectancy per trade ≈ gross expected return − (2 × taker fee + spread + slippage). "
                 "Modelled Binance cost: 0.10% taker fee per side and 0.05% slippage per side."),
        units="Return per trade.",
        implementation=(("bot.config.execution_costs", "FEE_RATE"), ("bot.config.execution_costs", "SLIPPAGE_RATE")),
        limitations=(
            "Fees differ by venue and tier (Kraken's published Tier 1 taker fee is 0.80%).",
            "Real slippage grows with order size and in fast markets.",
        ),
        intended_use="Judging whether a statistical finding could matter economically.",
        related=("information_coefficient", "in_sample_vs_oos"),
        suggested_questions=("Why can a statistically significant result still lose money after transaction costs?",),
    ),
]}


def get_concept(concept_id: str) -> Optional[Concept]:
    return CONCEPTS.get((concept_id or "").strip())


def all_concepts() -> List[Concept]:
    return sorted(CONCEPTS.values(), key=lambda c: c.name.lower())


def model_context(concept: Concept) -> str:
    """The text sent to the model for one concept: definitions only — no file paths, symbols, data-file names or test
    names (owner decision 2026-10-26: code references are shown locally, never sent). data_source is therefore left
    out: it names files."""
    parts = [f"Concept: {concept.name}", f"Summary: {concept.summary}", f"Definition: {concept.definition}",
             f"Formula: {concept.formula}"]
    for label, value in (("Units", concept.units), ("Inputs", concept.inputs), ("Window", concept.window),
                         ("Intended use", concept.intended_use)):
        if value:
            parts.append(f"{label}: {value}")
    if concept.limitations:
        parts.append("Known limitations:\n" + "\n".join(f"- {x}" for x in concept.limitations))
    pre = [CONCEPTS[c] for c in concept.prerequisites if c in CONCEPTS]   # claude code changed: 2026-10-26
    if pre:
        parts.append("Building blocks a beginner needs first (approved one-line definitions):\n"
                     + "\n".join(f"- {c.name}: {c.summary}" for c in pre))
    return "\n".join(parts)
