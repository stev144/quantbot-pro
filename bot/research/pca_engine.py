# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/research/pca_engine.py
#
# PCA-BASED STATISTICAL ARBITRAGE ENGINE
#
# claude code changed: new file — 2026-09-25. Built against a user-supplied
# spec that assumed several modules/files that do NOT exist in this
# codebase (feature_validator_v2_institutional.py, a consolidated config.py
# with PAIR_CONFIGS/WalkForwardConfig, cross_section_engine._align_universe)
# and a factual claim ("17 cointegrated pairs, ALL failed permutation")
# that is FALSE — verified directly against research_data/cointegration_
# pairs.csv and the live pilot: 21 pairs currently pass cointegration+OOS,
# and 2 of them (DODO_USDT/FIDA_USDT, MINA_USDT/ONG_USDT) pass permutation
# AND walk-forward too — they are this project's live pilot pairs right
# now. This file is built against the REAL architecture, corrected:
#   - universe: bot.research.cross_section_engine.UNIVERSE (real, derived
#     from bot.instruments.symbols_for_asset_class — same universe
#     cross-sectional ranking already uses)
#   - price loading: bot.instruments.resolve_ohlcv_path() (the real,
#     existing canonical path resolver — not a hand-built "data/{SYM}_1h.csv"
#     string, which risks drifting from fetch_all_symbols.py's actual
#     naming convention)
#   - PCA math: plain numpy eigendecomposition of the return CORRELATION
#     matrix (standardized returns — required for the Kaiser eigenvalue>1
#     criterion to mean anything; see run_pca()'s docstring for the real
#     covariance-vs-correlation bug this caught on the first live run).
#     scikit-learn is NOT an installed dependency anywhere in this
#     project (confirmed: ModuleNotFoundError on import) and PCA is exactly
#     an eigendecomposition — adding a new heavy dependency for something
#     numpy already does natively would be inconsistent with this
#     project's existing numpy/pandas/scipy/statsmodels footprint.
#   - rolling z-score: reuses bot.pairs.kalman_online.RollingZScore
#     verbatim (same 504-window/168-min-periods/±5 winsor convention the
#     live pilot's Kalman signal engine already uses) rather than
#     reimplementing rolling z-score logic a second time — "inspect
#     before duplicating."
#   - significance testing: reuses bot.research.feature_validator's real,
#     already-audited FeatureValidator.validate_all_features_raw() +
#     apply_family_wide_correction() (block-permutation, n=100 — this
#     project's real, existing default, not an invented 500) rather than
#     reimplementing block-permutation testing a second time.
#   - config: this module owns its own PCAConfig/PCA_CONFIG constants,
#     matching how cointegration_engine.py and kalman_filter_engine.py
#     each already own their own module-level config — there is no
#     shared bot/research/config.py to extend, so inventing one here
#     would be new, unrequested architecture.
#
# WHAT THIS ENGINE ACTUALLY TESTS (the real hypothesis, not the false one)
#
# The real, honest motivation for this engine — the one that survives
# fact-checking — is NOT "our pairs all failed, so try PCA instead."
# It IS: (1) PCA discovers shared market factors automatically instead of
# a pairwise search across C(n,2) combinations, (2) it explicitly removes
# the common crypto-market factor before computing a tradeable signal
# (rather than relying on a hand-picked second asset to net it out), and
# (3) it produces a usable residual for every symbol in the universe at
# once, not just the handful that happen to form a cointegrated pair. See
# STEP 8's comparison report for how this stacks up against the 2 real,
# fully-validated pairs this project already has.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from bot.instruments import resolve_ohlcv_path
from bot.research.cross_section_engine import UNIVERSE  # real, live universe — same one cross-sectional ranking uses
from bot.research.feature_validator import FeatureValidator, apply_family_wide_correction
from bot.pairs.kalman_online import RollingZScore  # reused verbatim — see module docstring

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(name)s | %(levelname)s | %(message)s")

OUTPUT_DIR = Path("research_data")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# STEP 7 (config first — every other step reads from this)
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class PCAConfig:
    """Every tunable constant this engine uses, in one place — matches
    the project's existing per-engine config pattern (cointegration_
    engine.py/kalman_filter_engine.py each own their own constants; there
    is no shared config.py in this codebase to extend instead)."""

    # None = auto-select via choose_n_components() (Kaiser criterion ∩
    # 80%-variance threshold, whichever gives FEWER components — see
    # that function's docstring for why "fewer" is the deliberate choice).
    n_components: Optional[int] = None
    variance_threshold: float = 0.80

    # claude code changed: matches bot/research/kalman_filter_engine.py's
    # ZSCORE_WINDOW/ZSCORE_MIN_PERIODS exactly (504 candles / 168 candles)
    # — the same rolling-window convention the live pilot's own Kalman
    # signal engine uses, via the SAME RollingZScore class, not a second
    # independently-tuned window.
    zscore_window: int = 504
    zscore_min_periods: int = 168

    entry_threshold: float = 2.0
    require_lag_confirm: bool = True

    forward_horizons: Dict[str, int] = field(default_factory=lambda: {
        "forward_return_1h": 1,
        "forward_return_4h": 4,
        "forward_return_24h": 24,
    })

    # claude code changed: real correction — the warmup period must be at
    # LEAST zscore_window candles (the rolling z-score is not meaningful
    # before then; RollingZScore itself already returns None until
    # zscore_min_periods candles have accumulated, but the FULL window
    # convention this project uses elsewhere — see kalman_filter_engine.py's
    # own WARMUP_CANDLES — masks the full zscore_window, not just
    # min_periods, before treating output as trustworthy). Kept as an
    # explicit, separate field (not silently `= zscore_window`) so a
    # future caller can widen it without it looking like a bug.
    warmup_candles: int = 504

    ic_alpha: float = 0.05
    ic_forward_return_col: str = "forward_return_4h"  # which horizon IC validation (STEP 5) is scored against


PCA_CONFIG = PCAConfig()


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# STEP 1: PCA DECOMPOSITION
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def load_close_prices(universe: List[str]) -> Dict[str, pd.Series]:
    """Loads each symbol's close-price series from data/*.csv via
    bot.instruments.resolve_ohlcv_path() — the project's real, existing
    canonical path resolver (used by cointegration_engine.py/cross_
    section_engine.py's own MarketDataProvider-backed loaders), NOT a
    hand-built "data/{SYMBOL}_1h.csv" string. Raises FileNotFoundError
    with clear instructions if a symbol's CSV is missing, per the spec's
    explicit requirement — never silently skips a symbol.

    universe here is in underscore form ("BTC_USDT", matching cross_
    section_engine.UNIVERSE's own convention); resolve_ohlcv_path expects
    the canonical slash form ("BTC/USDT"), so this function converts.
    """
    prices: Dict[str, pd.Series] = {}
    for symbol in universe:
        canonical = symbol.replace("_", "/")
        path = resolve_ohlcv_path(canonical)
        if not path.exists():
            raise FileNotFoundError(
                f"No price data for {symbol} at {path}. "
                f"Run `python -m bot.fetch_all_symbols` to download the tracked universe first."
            )
        df = pd.read_csv(path, parse_dates=["timestamp"])
        df = df.set_index("timestamp").sort_index()
        prices[symbol] = df["close"]
        logger.info(f"  Loaded {symbol}: {len(df):,} candles ({df.index.min()} -> {df.index.max()})")
    return prices


def align_universe(prices: Dict[str, pd.Series]) -> pd.DataFrame:
    """Aligns every symbol's price series onto one shared UTC
    DatetimeIndex via an INNER join (every row has every symbol's price,
    no NaN).

    claude code changed: deliberately INNER, not the OUTER join cross_
    section_engine.py's _build_returns_matrix() uses (that module ranks
    assets independently at each timestamp and can tolerate a symbol
    being NaN at a given row; PCA's eigendecomposition of the covariance
    matrix cannot — a single NaN anywhere breaks it). This is the real,
    corrected join strategy for THIS specific use case, not a copy of
    cross_section_engine's method (which does not exist under the name
    the original spec assumed, and would be the wrong join even if it did).
    """
    price_df = pd.concat(prices, axis=1, join="inner")
    price_df.columns = list(prices.keys())
    logger.info(f"  Aligned universe: {len(price_df.columns)} symbols, {len(price_df):,} shared timestamps "
                f"({price_df.index.min()} -> {price_df.index.max()})")
    return price_df


def compute_log_returns(price_df: pd.DataFrame) -> pd.DataFrame:
    """log(price_t / price_{t-1}) per symbol — the standard return
    representation for PCA (additive across time, roughly normally
    distributed, the same convention kalman_filter_engine.py's own
    log-price spread formula uses)."""
    log_prices = np.log(price_df)
    returns = log_prices.diff().dropna(how="any")
    return returns


def choose_n_components(eigenvalues: np.ndarray, variance_threshold: float) -> int:
    """Auto-selects how many principal components to retain.

    Two independent rules, take whichever gives FEWER components:
      1. Kaiser criterion: keep every component whose eigenvalue > 1.0
         (a component with eigenvalue <= 1 explains less variance than a
         single original asset would on average — not worth keeping).
      2. Cumulative variance: keep just enough components, in descending
         eigenvalue order, to reach variance_threshold (default 80%) of
         total variance explained.

    Taking the MINIMUM of the two (not the max, not an average) is a
    deliberate, conservative choice: it means whichever rule is more
    parsimonious wins, so this engine never keeps a component that BOTH
    rules would have discarded on their own. Eigenvalues must already be
    sorted descending (run_pca() guarantees this).
    """
    kaiser_n = int(np.sum(eigenvalues > 1.0))
    total_variance = eigenvalues.sum()
    cumulative = np.cumsum(eigenvalues) / total_variance
    variance_n = int(np.searchsorted(cumulative, variance_threshold) + 1)
    n = max(1, min(kaiser_n, variance_n))  # never 0 — always keep at least PC1
    logger.info(f"  Kaiser criterion (eigenvalue > 1.0): {kaiser_n} components")
    logger.info(f"  {variance_threshold*100:.0f}% variance threshold: {variance_n} components")
    logger.info(f"  Selected (fewer wins): {n} components")
    return n


def run_pca(returns: pd.DataFrame, config: PCAConfig) -> Tuple[np.ndarray, np.ndarray, np.ndarray, int, np.ndarray, np.ndarray]:
    """Runs PCA via eigendecomposition of the return CORRELATION matrix
    (no sklearn — see module docstring for why).

    claude code changed: real bug caught by actually running this against
    the live 144-symbol universe and inspecting the output, not assumed
    correct up front (this project's own "verify, don't trust" discipline
    applied to my own new code). The Kaiser criterion (eigenvalue > 1.0)
    is only meaningful on a CORRELATION matrix, where eigenvalues average
    to exactly 1.0 by construction (trace = n = sum of unit diagonal
    entries) — "a component explaining more than one asset's average
    share of variance" is the whole idea behind the > 1.0 cutoff. Applied
    to a COVARIANCE matrix of hourly log-returns instead (typical
    per-candle variance ~1e-4 to 1e-6), literally every eigenvalue is
    tiny regardless of real structure, so Kaiser degenerates to ~0
    components — confirmed live: first real run showed "Kaiser: 0
    components" vs. "80% variance: 46 components," and the (otherwise
    correctly conservative) "fewer wins" rule then kept only 1 real
    component purely from this scale artifact, not genuine parsimony.
    Standardizing each symbol's returns first (correlation matrix, not
    covariance) is the standard fix and also has a second benefit: no
    single high-volatility symbol can dominate the factor loadings just
    because its RAW return variance happens to be large.

    Returns
    -------
    eigenvalues : np.ndarray, shape (n_symbols,) — descending order, of
        the CORRELATION matrix (not covariance)
    eigenvectors : np.ndarray, shape (n_symbols, n_symbols) — column i is
        the loading vector for component i, same order as eigenvalues
    loadings : np.ndarray, shape (n_symbols, n_retained) — eigenvectors
        for just the retained components
    n_retained : int
    mean : np.ndarray, shape (n_symbols,) — per-symbol return mean, used
        by compute_residuals() to standardize/un-standardize consistently
    std : np.ndarray, shape (n_symbols,) — per-symbol return std dev,
        used to rescale the standardized residual back into raw
        log-return units (so forward-return labels stay economically
        interpretable, not left in unitless z-score space)
    """
    mean = returns.values.mean(axis=0)
    std = returns.values.std(axis=0, ddof=1)
    standardized = (returns.values - mean) / std

    corr = np.corrcoef(standardized, rowvar=False)  # rowvar=False: columns are symbols, rows are timestamps
    # eigh (not eig) because a correlation matrix is symmetric — eigh is
    # both faster and numerically guaranteed to return real eigenvalues,
    # unlike eig which can return complex values for a matrix that's
    # symmetric only up to floating-point error.
    eigenvalues, eigenvectors = np.linalg.eigh(corr)
    # eigh returns ASCENDING order — reverse to descending (PC1 = most variance) to match every downstream assumption
    order = np.argsort(eigenvalues)[::-1]
    eigenvalues = eigenvalues[order]
    eigenvectors = eigenvectors[:, order]

    n_retained = config.n_components or choose_n_components(eigenvalues, config.variance_threshold)
    loadings = eigenvectors[:, :n_retained]

    variance_explained = eigenvalues / eigenvalues.sum()
    logger.info("\nVariance explained:")
    for i in range(min(n_retained + 2, len(eigenvalues))):  # show a couple beyond the cutoff for context
        marker = " (retained)" if i < n_retained else " (dropped)"
        logger.info(f"  PC{i+1}: {variance_explained[i]*100:5.1f}% variance explained{marker}")
    logger.info(f"  Total retained: {n_retained} components explaining {variance_explained[:n_retained].sum()*100:.1f}% of variance")

    return eigenvalues, eigenvectors, loadings, n_retained, mean, std


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# STEP 2: RESIDUAL CALCULATION
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def compute_residuals(
    returns: pd.DataFrame, loadings: np.ndarray, mean: np.ndarray, std: np.ndarray, config: PCAConfig
) -> pd.DataFrame:
    """For each symbol, projects its STANDARDIZED return onto the
    retained principal components to get the "factor-explained" return,
    subtracts it to get the idiosyncratic (common-factor-free) residual,
    then rescales the residual back into raw log-return units.

    claude code changed: must standardize/rescale with the SAME mean/std
    run_pca() used to build the correlation matrix loadings were fit
    against — projecting raw (non-standardized) returns onto correlation-
    matrix eigenvectors would silently mix two incompatible scales.
    Rescaling the final residual by each symbol's own std (not re-adding
    mean — a residual is a deviation, already ~0-mean) keeps forward-
    return labels (STEP 4) in real log-return units instead of unitless
    z-scores, which matters for economic-significance interpretation even
    though the IC test itself (STEP 5, Spearman-based) is scale-invariant
    either way.

    Math: standardized = (returns - mean) / std; factor scores F (T x k)
    = standardized @ loadings (n x k); factor-explained = F @ loadings.T
    (T x n); standardized_residual = standardized - explained;
    raw_residual = standardized_residual * std.

    This residual is this engine's equivalent of a cointegration pair's
    "spread": the part of an asset's movement that is NOT explained by
    what everything else in the universe is doing.
    """
    standardized = (returns.values - mean) / std
    scores = standardized @ loadings              # (T, k) — how much each timestamp "activates" each component
    explained = scores @ loadings.T                # (T, n) — project back into standardized asset space
    standardized_residuals = standardized - explained
    raw_residuals = standardized_residuals * std   # back to real log-return units
    residual_df = pd.DataFrame(raw_residuals, index=returns.index, columns=returns.columns)
    return residual_df


def compute_residual_zscores(residual_df: pd.DataFrame, config: PCAConfig) -> pd.DataFrame:
    """Rolling z-score of each symbol's residual, via the SAME
    RollingZScore class bot/pairs/kalman_online.py already uses for the
    live pilot's spread z-score (504-window, 168-min-periods, ±5.0
    winsor) — reused, not reimplemented, per this project's "inspect
    before duplicating" convention.

    Column naming: residual_zscore_{SYMBOL}, per the spec's explicit
    naming requirement.
    """
    zscore_df = pd.DataFrame(index=residual_df.index)
    for symbol in residual_df.columns:
        roller = RollingZScore(window=config.zscore_window, min_periods=config.zscore_min_periods)
        z_values = [roller.update(v) for v in residual_df[symbol].values]
        zscore_df[f"residual_zscore_{symbol}"] = z_values
    return zscore_df


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# STEP 3: SIGNAL GENERATION
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def generate_signals(zscore_df: pd.DataFrame, config: PCAConfig) -> pd.DataFrame:
    """Builds the tradeable decision columns per symbol:
      - pair_signal_pca_{SYMBOL} = -residual_zscore_{SYMBOL} (same sign
        convention as kalman_filter_engine.py's pair_signal_dynamic —
        confirmed by direct read of that module's own comment: "results
        ['pair_signal_dynamic'] = -z" — so a high positive residual
        z-score (expensive relative to the market factors) produces a
        NEGATIVE signal, i.e. lean short, and vice versa).
      - residual_zscore_lag1_{SYMBOL}: previous candle's z-score, for the
        lag-confirmation filter (matches entry_exit_engine.py's
        REQUIRE_LAG_CONFIRMATION convention — an entry only confirms if
        the current and previous candle's z-score agree in sign, guarding
        against acting on a single noisy print).
    """
    signal_df = pd.DataFrame(index=zscore_df.index)
    symbols = [c.replace("residual_zscore_", "") for c in zscore_df.columns]
    for symbol in symbols:
        z_col = f"residual_zscore_{symbol}"
        signal_df[f"pair_signal_pca_{symbol}"] = -zscore_df[z_col]
        signal_df[f"residual_zscore_lag1_{symbol}"] = zscore_df[z_col].shift(1)
        if config.require_lag_confirm:
            current = zscore_df[z_col]
            lag1 = signal_df[f"residual_zscore_lag1_{symbol}"]
            # claude code changed: lag confirmation = current and previous
            # z-score must have the SAME sign (both stretched the same
            # direction) — matches entry_exit_engine.py's REQUIRE_LAG_
            # CONFIRMATION spirit (don't act on a single-candle spike).
            confirmed = (np.sign(current) == np.sign(lag1)) & current.notna() & lag1.notna()
            signal_df[f"lag_confirmed_{symbol}"] = confirmed
    return signal_df


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# STEP 4: FORWARD RETURN LABELS
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def add_forward_returns(residual_df: pd.DataFrame, config: PCAConfig) -> pd.DataFrame:
    """Adds forward_return_{1h,4h,24h}_{SYMBOL} columns computed on each
    symbol's IDIOSYNCRATIC (residual) return series — NOT the raw price
    return. This is the deliberate, spec-required label: the hypothesis
    being tested is "does the residual z-score predict the FUTURE
    residual," the PCA equivalent of cointegration_engine.py's
    spread_forward_{h} columns (which label future SPREAD movement, not
    future raw price movement)."""
    label_df = pd.DataFrame(index=residual_df.index)
    for col_name, horizon in config.forward_horizons.items():
        for symbol in residual_df.columns:
            # Forward-looking sum of residual returns over `horizon` candles — matches
            # cointegration_engine.py's own forward-return construction (cumulative, not single-candle).
            label_df[f"{col_name}_{symbol}"] = (
                residual_df[symbol].rolling(window=horizon).sum().shift(-horizon)
            )
    return label_df


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# STEP 5: IC VALIDATION — reuses the REAL feature_validator.py machinery
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def validate_ic_per_symbol(
    signal_df: pd.DataFrame,
    label_df: pd.DataFrame,
    symbols: List[str],
    config: PCAConfig,
) -> pd.DataFrame:
    """Runs Spearman IC + block-permutation significance (n=100, this
    project's real, existing default — see feature_validator.py's
    BLOCK_PERMUTATION_N) for every symbol's pair_signal_pca against its
    own forward_return_4h (config.ic_forward_return_col), THEN pools all
    symbols into one hypothesis family for Bonferroni+FDR correction via
    apply_family_wide_correction() — the same real function this
    project's cross-sectional/regime-conditional research already uses,
    not a reimplementation.

    Returns one row per symbol: STRONG_KEEP / KEEP / REVIEW / DELETE,
    matching feature_validator.py's own recommendation vocabulary (the
    spec's requested verdict labels are exactly this vocabulary — no
    translation needed).
    """
    raw_results_by_symbol: Dict[str, pd.DataFrame] = {}
    forward_col = config.ic_forward_return_col

    for symbol in symbols:
        sig_col = f"pair_signal_pca_{symbol}"
        lbl_col = f"{forward_col}_{symbol}"
        if sig_col not in signal_df.columns or lbl_col not in label_df.columns:
            logger.warning(f"  {symbol}: missing signal or label column — skipping")
            continue

        df = pd.DataFrame({
            "timestamp": signal_df.index,
            "pair_signal_pca": signal_df[sig_col].values,
            forward_col: label_df[lbl_col].values,
        }).dropna()

        if len(df) < 30:
            logger.warning(f"  {symbol}: only {len(df)} usable rows after dropna — skipping (min 30)")
            continue

        validator = FeatureValidator(min_observations=30, alpha=config.ic_alpha)
        try:
            raw = validator.validate_all_features_raw(df, forward_return_col=forward_col)
        except Exception as e:
            logger.warning(f"  {symbol}: validation failed ({e}) — skipping")
            continue
        raw_results_by_symbol[symbol] = raw

    if not raw_results_by_symbol:
        logger.error("  No symbol produced a usable validation result — returning empty DataFrame")
        return pd.DataFrame()

    # claude code changed: THIS is the real block-permutation + Bonferroni
    # + FDR correction — pooled across every symbol as one hypothesis
    # family, exactly matching this project's own multiple-testing
    # discipline elsewhere (cointegration_engine.py's cross-pair FDR
    # correction, feature_validator.py's own family-wide correction).
    corrected_by_symbol, pooled_summary = apply_family_wide_correction(raw_results_by_symbol, alpha=config.ic_alpha)

    # claude code changed: real column names verified directly against
    # feature_validator.py's _results_to_dataframe()/_apply_multiple_
    # testing_correction() — 'ic_overall' (not 'ic'), 'p_fdr' (not
    # 'p_value_fdr') are the actual DataFrame columns produced by
    # apply_family_wide_correction(). Caught before running, not after a
    # KeyError.
    rows = []
    for symbol, df in corrected_by_symbol.items():
        row = df[df["feature"] == "pair_signal_pca"]
        if row.empty:
            continue
        r = row.iloc[0]
        rows.append({
            "symbol": symbol,
            "ic": r.get("ic_overall"),
            "p_value_block_permutation": r.get("p_value_block_permutation"),
            "p_value_fdr": r.get("p_fdr", r.get("p_value_block_permutation")),
            "recommendation": r.get("recommendation"),
        })
    return pd.DataFrame(rows).sort_values("ic", key=lambda s: s.abs(), ascending=False).reset_index(drop=True)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# STEP 6: OUTPUT FILES
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def save_outputs(
    residual_zscore_df: pd.DataFrame,
    eigenvalues: np.ndarray,
    loadings: np.ndarray,
    symbols: List[str],
    n_retained: int,
    ic_results: pd.DataFrame,
    output_dir: Path = OUTPUT_DIR,
    output_prefix: str = "",
) -> None:
    """Saves the 4 output files the spec requests, matching this
    project's existing research_data/ naming convention.

    claude code changed: added output_prefix (default "" — byte-identical
    filenames/behavior for the original crypto-universe run) so a second
    run against a DIFFERENT universe (e.g. "forex_") writes to its own
    files instead of silently overwriting the crypto run's saved results —
    the user explicitly asked to keep the crypto output before adding
    the forex run.
    """
    output_dir.mkdir(exist_ok=True)

    residual_zscore_df.reset_index().to_csv(output_dir / f"{output_prefix}pca_residuals.csv", index=False)
    logger.info(f"  Saved: {output_dir / f'{output_prefix}pca_residuals.csv'}")

    loadings_df = pd.DataFrame(
        loadings, index=symbols, columns=[f"PC{i+1}" for i in range(n_retained)],
    )
    loadings_df.to_csv(output_dir / f"{output_prefix}pca_components.csv")
    logger.info(f"  Saved: {output_dir / f'{output_prefix}pca_components.csv'}")

    variance_explained = eigenvalues / eigenvalues.sum()
    scree_df = pd.DataFrame({
        "component": [f"PC{i+1}" for i in range(len(eigenvalues))],
        "eigenvalue": eigenvalues,
        "variance_explained": variance_explained,
        "cumulative_variance": np.cumsum(variance_explained),
        "retained": [i < n_retained for i in range(len(eigenvalues))],
    })
    scree_df.to_csv(output_dir / f"{output_prefix}pca_variance_explained.csv", index=False)
    logger.info(f"  Saved: {output_dir / f'{output_prefix}pca_variance_explained.csv'}")

    ic_results.to_csv(output_dir / f"{output_prefix}pca_ic_validation.csv", index=False)
    logger.info(f"  Saved: {output_dir / f'{output_prefix}pca_ic_validation.csv'}")


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# STEP 8: COMPARISON REPORT
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def print_comparison_report(ic_results: pd.DataFrame) -> None:
    """Prints the side-by-side comparison the spec requests — using REAL,
    verified numbers for the existing methods, not invented placeholders.

    claude code changed: the original spec's comparison table assumed
    "Pairwise cointegration: IC=0.52, 0/17 passing FDR, fails permutation"
    — false. Verified directly against research_data/cointegration_
    pairs.csv and this project's real, fresh (today's) permutation
    results: 21 pairs pass cointegration+OOS; of those, DODO_USDT/FIDA_USDT
    (entry_ic=0.3336) and MINA_USDT/ONG_USDT (entry_ic=0.2771) pass
    permutation AND walk-forward too — they are the live pilot's pairs
    right now. "Cross-section ranking" has no single directly-comparable
    IC figure verified today for plain (non-regime-conditional) crypto
    ranking, so it's reported as not-yet-measured here rather than
    invented.
    """
    logger.info("\n" + "=" * 100)
    logger.info("METHOD COMPARISON")
    logger.info("=" * 100)
    logger.info(f"{'METHOD':<28}| {'BEST IC':>8} | {'PASSING FDR':>14} | STATUS")
    logger.info("-" * 100)
    logger.info(f"{'Pairwise cointegration':<28}| {'0.33':>8} | {'2/21':>14} | DODO/FIDA + MINA/ONG pass cointegration+OOS+permutation+walk-forward (live pilot)")
    logger.info(f"{'Cross-section ranking':<28}| {'n/a':>8} | {'n/a':>14} | Not directly measured for plain crypto ranking as of this run")

    if len(ic_results) == 0:
        logger.info(f"{'PCA residual stat-arb':<28}| {'n/a':>8} | {'0/0':>14} | No symbol produced a usable result")
        return

    # claude code changed: real recommendation strings have a space
    # ("STRONG KEEP"), not an underscore — verified against
    # FeatureValidator._determine_recommendation()'s actual return values.
    passing = ic_results[ic_results["recommendation"].isin(["STRONG KEEP", "KEEP"])]
    best_ic = ic_results["ic"].abs().max()
    logger.info(f"{'PCA residual stat-arb':<28}| {best_ic:>8.4f} | {f'{len(passing)}/{len(ic_results)}':>14} | "
                f"{'Some symbols pass' if len(passing) > 0 else 'No symbol clears STRONG_KEEP/KEEP'}")

    logger.info("\nTop 3 symbols by |IC|:")
    for _, row in ic_results.head(3).iterrows():
        logger.info(f"  {row['symbol']:15} IC={row['ic']:+.4f} | p_fdr={row['p_value_fdr']:.4f} | {row['recommendation']}")

    # claude code changed: the single most important output, per the
    # spec's explicit instruction — shown LAST, unambiguously.
    logger.info("\n" + "=" * 100)
    best_row = ic_results.iloc[0]
    if best_row["recommendation"] in ("STRONG KEEP", "KEEP") and best_row["p_value_fdr"] < 0.05:
        logger.info(f"RESULT: {best_row['symbol']} PASSES the FDR-corrected permutation test "
                    f"(IC={best_row['ic']:+.4f}, p_fdr={best_row['p_value_fdr']:.4f}, {best_row['recommendation']}).")
    else:
        logger.info(f"RESULT: NO symbol passes the FDR-corrected permutation test. "
                    f"Best candidate ({best_row['symbol']}): IC={best_row['ic']:+.4f}, "
                    f"p_fdr={best_row['p_value_fdr']:.4f}, recommendation={best_row['recommendation']}.")
    logger.info("=" * 100)


# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════

def run_pca_engine(
    universe: Optional[List[str]] = None,
    config: PCAConfig = PCA_CONFIG,
    output_prefix: str = "",
    universe_label: str = "bot.research.cross_section_engine.UNIVERSE",
) -> pd.DataFrame:
    """Runs the full 8-step pipeline end to end. Returns the IC
    validation results DataFrame (also saved to
    research_data/{output_prefix}pca_ic_validation.csv).

    claude code changed: added output_prefix/universe_label so this same,
    unmodified pipeline can be pointed at a different asset-class universe
    (e.g. FOREX) without overwriting the original crypto run's saved
    files or mislabeling its log output — see save_outputs()'s docstring.
    """
    universe = universe or UNIVERSE

    logger.info("=" * 100)
    logger.info("PCA ENGINE — STANDALONE RUN")
    logger.info("=" * 100)
    logger.info(f"  Universe: {len(universe)} symbols ({universe_label})")

    prices = load_close_prices(universe)
    price_df = align_universe(prices)
    returns = compute_log_returns(price_df)

    eigenvalues, eigenvectors, loadings, n_retained, ret_mean, ret_std = run_pca(returns, config)

    residual_df = compute_residuals(returns, loadings, ret_mean, ret_std, config)
    residual_df = residual_df.iloc[config.warmup_candles:]  # remove warmup — spec's explicit requirement

    zscore_df = compute_residual_zscores(residual_df, config)
    signal_df = generate_signals(zscore_df, config)
    label_df = add_forward_returns(residual_df, config)

    ic_results = validate_ic_per_symbol(signal_df, label_df, list(returns.columns), config)

    save_outputs(zscore_df, eigenvalues, loadings, list(returns.columns), n_retained, ic_results, output_prefix=output_prefix)
    print_comparison_report(ic_results)

    return ic_results


if __name__ == "__main__":
    run_pca_engine()
