# ═══════════════════════════════════════════════════════════════════════════════
# bot/research/permutation_test_engine.py
#
# PERMUTATION (BLOCK-SHUFFLE) FALSIFICATION TEST
# QuantiBot Pro Research Pipeline — Missing Piece 4.5 of 6
#
# Version  : 1.0 (Research Grade)
# Author   : QuantiBot Pro Research Pipeline
#
# ─────────────────────────────────────────────────────────────────────────────
# WHY THIS MODULE EXISTS
# ─────────────────────────────────────────────────────────────────────────────
#
# AVAX/ATOM's entry_exit_engine.py results show a pattern that should worry
# anyone who's seen it before: a very high win rate (90%+), an absurd
# Sharpe (15-25), trades closing in hours instead of the validated ~120h
# half-life, and — most damning — an Entry IC that is NEGATIVE or
# statistically indistinguishable from zero in every fold tested so far.
#
# There is a specific, well-known failure mode that produces EXACTLY this
# signature: trading a BOUNDED, SELF-REFERENCING oscillator (a z-score
# computed against its own rolling mean/std, clipped at ±5) rather than a
# genuine slow economic relationship between two assets. A bounded
# statistic mechanically snaps back toward its own rolling mean — that's
# what it's built to do, by construction, almost regardless of what the
# underlying prices are actually doing. A strategy that buys the extreme
# and sells the mean of such a statistic will look profitable on nearly
# ANY sequence of numbers with that shape, including numbers that have
# nothing to do with real AVAX/ATOM cointegration.
#
# This module answers one falsifiable question: IS THE APPARENT EDGE
# COMING FROM SOMETHING REAL IN THE AVAX/ATOM RELATIONSHIP, OR FROM THE
# MECHANICAL SHAPE OF THE INDICATOR ITSELF?
#
# ─────────────────────────────────────────────────────────────────────────────
# THE METHOD — BLOCK PERMUTATION TESTING
# ─────────────────────────────────────────────────────────────────────────────
#
# This is a standard, assumption-light technique from empirical finance
# (the same family as White's Reality Check / Sullivan-Timmerman-White
# data-snooping tests): instead of inventing a synthetic model of what
# "should" happen, take the REAL data and destroy only the one thing
# we're trying to test the importance of — the SPECIFIC TEMPORAL ALIGNMENT
# between the signal and what happened next — while preserving everything
# else about the data's statistical texture.
#
# Concretely: chop the real Kalman CSV into contiguous BLOCKS (default: one
# week of hourly candles each), and reassemble those blocks in a randomly
# shuffled order. Within each block, short-term structure — volatility
# clustering, the z-score's own autocorrelation, its bounded shape — is
# completely untouched. What's destroyed is the LONG-RANGE alignment: week
# 47's z-score readings might now sit where week 12's used to be. If the
# entry/exit rules make money because they're reading something genuinely
# predictive about AVAX/ATOM's relationship, shuffling which week follows
# which should hurt performance. If the entry/exit rules make money purely
# because "extreme values of a bounded statistic revert toward zero" is
# true almost everywhere, performance on shuffled data should look about
# the same as on the real data — because the very reason to expect
# reversion never actually left.
#
# Run this many times (each with a different random shuffle), and you get
# a whole DISTRIBUTION of "what a bounded-oscillator-chasing strategy
# produces on scrambled history." The real, unshuffled result gets ranked
# against that distribution. If it's not meaningfully better than what
# shuffled noise already produces, the apparent edge is not statistically
# distinguishable from the mechanical artifact — which is the honest,
# falsifiable answer to "is this real."
#
# ─────────────────────────────────────────────────────────────────────────────
# WHERE THIS SITS IN THE PIPELINE
# ─────────────────────────────────────────────────────────────────────────────
#
# FEEDS FROM: entry_exit_engine.py — reused unchanged, exactly as
#             walk_forward_engine.py reuses it. This module does not
#             reimplement entry/exit decision logic; it only controls
#             what data that logic is allowed to see.
#
# RUNS: after Missing Piece 4 passes, and BEFORE trusting Missing Piece 5's
#       walk-forward result — a walk-forward pass on a fake edge is still
#       a fake edge, just tested on more scrambled sub-periods of the same
#       fake edge. This is the right place to catch it.
#
# OUTPUT FILES (research_data/permutation_test/<PAIR>/):
#   permutation_results.csv   → one row per shuffle: its win rate, Sharpe,
#                                profit factor, entry IC
#   permutation_verdict.csv   → the real result's percentile rank and
#                                empirical p-value against the shuffled distribution
#
# ═══════════════════════════════════════════════════════════════════════════════


from __future__ import annotations              # Modern type hints in Python 3.8+

import logging                                  # Structured logging — same format as every other module
import shutil                                   # Clean up per-permutation scratch directories
from pathlib import Path                        # Cross-platform file paths
from typing import Dict, List, Optional         # Type annotations

import numpy as np                              # Numerical operations, random permutation generator
import pandas as pd                             # DataFrame operations

# Reused unchanged — this module's only job is to control what data
# EntryExitEngine sees, never to reimplement its decision logic.
from bot.research.entry_exit_engine import EntryExitEngine, STRATEGY_CAPITAL_USDT

logger = logging.getLogger(__name__)            # Module-level logger, same format as the rest of the project
if not logger.handlers:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

# ── Configuration ───────────────────────────────────────────────────────────────
PERMUTATION_OUTPUT_DIR: str = "research_data/permutation_test"   # Root output directory
DEFAULT_N_PERMUTATIONS: int = 100                                 # Must be high enough that p < 0.05 is even
                                                                    # achievable — the smallest possible empirical
                                                                    # p-value is 1/(N+1), so N=15 can NEVER report
                                                                    # significance no matter how dominant the real
                                                                    # result is. N=100 gives a floor of ~0.0099.
                                                                    # Raise further for a tighter estimate once
                                                                    # you trust the pair enough to spend the compute.
# ── Block size ─────────────────────────────────────────────────────────────────
# THIS IS THE MOST IMPORTANT PARAMETER IN THE WHOLE MODULE, AND IT MUST BE
# SHORTER THAN A TYPICAL TRADE'S HOLDING PERIOD. If the block is longer
# than most trades' entry-to-exit lifespan, shuffling block order just
# relocates whole, INTACT trades to a different calendar period without
# touching which trades won or lost — the shuffled distribution ends up
# nearly identical to the real one, and the test has no power to detect
# anything at all. This isn't a hypothetical: it happened during
# development against a KNOWN-real synthetic OU pair and produced a false
# "no edge" verdict, purely because the fixed 168h default block was far
# longer than that pair's ~30h average hold time. Never trust a fixed
# block size across pairs with different trade durations — always derive
# it from the real run's own avg_hold_hours (see ADAPTIVE_BLOCK_DIVISOR).
ADAPTIVE_BLOCK_DIVISOR: float = 3.0          # Block size = avg_hold_hours / this — small enough that most
                                              # trades' lifecycle gets split across a shuffled block boundary
MIN_BLOCK_SIZE_CANDLES: int = 1               # Floor — as low as a single candle (full IID shuffle) is now safe,
                                               # because kalman_zscore_lag1 is recomputed AFTER shuffling (see
                                               # _block_shuffle) rather than independently reshuffled — there is
                                               # no longer a boundary-consistency reason to keep this artificially high
MAX_BLOCK_SIZE_CANDLES: int = 48                # Ceiling — never let a very long average hold time push the
                                                 # block size back up toward the same failure mode this exists to avoid
REQUIRED_KALMAN_COLUMNS = [                                           # Same seven columns EntryExitEngine requires —
    "kalman_zscore", "kalman_zscore_lag1", "pair_signal_dynamic",       # shuffled together, in lockstep, so a given
    "kalman_beta", "kalman_spread", "prediction_error", "beta_uncertainty",  # relocated candle's columns stay internally
]                                                                          # consistent with each other

# A result is only called "statistically real" if the actual, unshuffled
# run beats at least this fraction of shuffled replicas. 0.95 is the
# conventional two-sided-adjacent one-sided threshold (p < 0.05).
SIGNIFICANCE_PERCENTILE: float = 0.95


class PermutationTestEngine:
    """
    Runs entry_exit_engine.py's exact decision logic against the real data
    once, and against many block-shuffled replicas of the same data, to
    test whether the real result's performance is statistically
    distinguishable from what a bounded-oscillator-chasing strategy
    produces on scrambled history.
    """

    def __init__(
        self,
        n_permutations:    int   = DEFAULT_N_PERMUTATIONS,
        block_size_candles:  Optional[int] = None,        # None = adaptive (recommended); set an int to override
        capital_usdt:          float = STRATEGY_CAPITAL_USDT,
        output_dir:              str   = PERMUTATION_OUTPUT_DIR,
        random_seed:              Optional[int] = None,        # Set for a reproducible shuffle sequence
        resample_hours:            Optional[int] = None,        # claude code changed: new — threaded into the loader so df_real (and therefore every shuffle, which reshuffles df_real) is already at this candle width
    ) -> None:
        self.n_permutations     = n_permutations
        self.block_size          = block_size_candles       # May be None here — resolved to a real value in run()
        self.capital_usdt           = capital_usdt
        self.output_dir               = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.rng                        = np.random.default_rng(random_seed)   # Isolated RNG — doesn't disturb global numpy state
        self.resample_hours             = resample_hours       # claude code changed: new

        block_desc = f"{block_size_candles} candles (fixed override)" if block_size_candles is not None else "adaptive - derived from the real run's own avg hold time"
        logger.info("PermutationTestEngine initialised")
        logger.info(f"  Permutations  : {n_permutations}")
        logger.info(f"  Block size    : {block_desc}")


    # ─────────────────────────────────────────────────────────────────────────
    # PUBLIC: run()
    # ─────────────────────────────────────────────────────────────────────────

    def run(self, kalman_csv: str, pair_name: str) -> Dict:
        """
        Run the real strategy once, then N block-shuffled replicas, and
        return a verdict on whether the real result is statistically
        distinguishable from shuffled-noise performance.
        """

        logger.info("=" * 70)
        logger.info(f"PERMUTATION TEST — {pair_name}")
        logger.info("QuantiBot Pro — Missing Piece 4.5 of 6")
        logger.info("=" * 70)

        # claude code changed: new block — resolve this pair's identity and real
        # half-life ONCE here, stored as instance state for _run_engine_on_df()
        # to use on every EntryExitEngine it builds (the real run AND all 100
        # shuffles). Without this, every one of those engines silently used the
        # class default exit_time_stop_hours=240 and validated_half_life=None
        # regardless of which pair was actually being tested — harmless for
        # AVAX/ATOM (240h happens to equal 2x its own 119.9h half-life) but
        # wrong for any other pair, e.g. DOT_USDT/LINK_USDT at 196.8h, where a
        # fair test needs a ~394h time-stop, not a borrowed 240h one.
        from bot.research.entry_exit_engine import _parse_pair_from_kalman_filename
        from bot.research.kalman_filter_engine import load_pair_config

        try:
            slash_pair_name, symbol_a, symbol_b = _parse_pair_from_kalman_filename(kalman_csv)
        except ValueError:
            slash_pair_name = symbol_a = symbol_b = None

        self._pair_identity = {
            "pair_name": pair_name, "symbol_a": symbol_a, "symbol_b": symbol_b,
            "validated_half_life": None, "exit_time_stop_hours": None,
        }
        if slash_pair_name is not None:
            try:
                pair_config = load_pair_config(slash_pair_name, require_passes_filters=False)
                half_life = pair_config["half_life_h"]
                self._pair_identity["validated_half_life"]  = half_life
                self._pair_identity["exit_time_stop_hours"] = round(2 * half_life)
                if not pair_config["passes_filters"]:
                    logger.warning(
                        f"  '{pair_name}' did not pass cointegration_engine.py's own "
                        f"filters, but this permutation test is running anyway using "
                        f"its real half-life ({half_life}h) and a time-stop of "
                        f"{round(2 * half_life)}h derived from it — a deliberate "
                        f"test of a rejected pair."
                    )
            except (FileNotFoundError, ValueError) as e:
                logger.warning(
                    f"  Could not look up half-life for '{pair_name}' ({e}). "
                    f"Every engine in this run will fall back to the class "
                    f"default exit_time_stop_hours=240 and skip the half-life "
                    f"sanity check."
                )

        pair_dir = self.output_dir / pair_name                          # This pair's own output subfolder
        pair_dir.mkdir(parents=True, exist_ok=True)

        # ── Step 1: Load the real data once (reusing the already-fixed loader) ──
        loader = EntryExitEngine(                                                 # claude code changed: added resample_hours=
            capital_usdt=self.capital_usdt, output_dir=str(pair_dir / "_loader_scratch"),
            resample_hours=self.resample_hours,                                   # claude code changed: new — df_real (and every shuffle derived from it) comes out already at this candle width
        )
        df_real = loader._load_kalman_data(kalman_csv)                    # Same cleaned loader as everywhere else
        loader._validate_columns(df_real)
        shutil.rmtree(pair_dir / "_loader_scratch", ignore_errors=True)

        # ── Step 2: Run the REAL, unshuffled data through the real engine ────────
        logger.info("\nStep 2: Running the REAL (unshuffled) strategy...")
        real_result = self._run_engine_on_df(df_real, pair_dir / "real_run")   # This pair's actual performance

        # ── Step 2b: Resolve the block size from the REAL run's own hold time ────
        # Must happen AFTER the real run and BEFORE any shuffling — the whole
        # point is to size the block relative to how long this specific
        # pair's trades actually last, not a fixed guess. See the
        # ADAPTIVE_BLOCK_DIVISOR comment at the top of this file for why a
        # fixed block size silently breaks this test.
        if self.block_size is None:
            avg_hold = real_result.get("avg_hold_hours", np.nan)
            if pd.isna(avg_hold) or avg_hold <= 0:                              # No usable hold time to derive from
                self.block_size = MIN_BLOCK_SIZE_CANDLES                          # Fall back to the floor, safely small
                logger.warning(
                    f"  Real run produced no usable avg_hold_hours — "
                    f"falling back to the minimum block size ({MIN_BLOCK_SIZE_CANDLES} candles)"
                )
            else:
                # claude code changed: was `avg_hold / ADAPTIVE_BLOCK_DIVISOR` only —
                # that treats the result directly as a candle count, which is only
                # correct when each candle is 1h wide. df_real (and therefore every
                # shuffle) may now be resampled to self.resample_hours-wide candles
                # (see PermutationTestEngine.__init__), so the real-hours target must
                # be converted into a count of THOSE candles, not 1h ones — otherwise
                # a block at e.g. resample_hours=4 would span 4x the intended real time.
                candle_width_h = self.resample_hours if self.resample_hours else 1
                raw_block = int(round(avg_hold / ADAPTIVE_BLOCK_DIVISOR / candle_width_h))   # Target: well under a typical trade's lifespan
                self.block_size = int(np.clip(raw_block, MIN_BLOCK_SIZE_CANDLES, MAX_BLOCK_SIZE_CANDLES))
                logger.info(
                    f"  Adaptive block size: {self.block_size} candles "
                    f"({candle_width_h}h each -> {self.block_size * candle_width_h}h total; "
                    f"real avg hold time {avg_hold:.1f}h ÷ {ADAPTIVE_BLOCK_DIVISOR:.0f}, "
                    f"clipped to [{MIN_BLOCK_SIZE_CANDLES}, {MAX_BLOCK_SIZE_CANDLES}] candles)"
                )

        # ── Step 3: Run N block-shuffled replicas ────────────────────────────────
        logger.info(f"\nStep 3: Running {self.n_permutations} block-shuffled replicas...")
        shuffled_results: List[Dict] = []
        for i in range(1, self.n_permutations + 1):                         # One shuffled run per permutation
            df_shuffled = self._block_shuffle(df_real)                        # Scramble which week's data sits where
            result = self._run_engine_on_df(df_shuffled, pair_dir / f"shuffle_{i:03d}")
            result["permutation_id"] = i                                       # Tag for the results CSV
            shuffled_results.append(result)
            if i % 10 == 0 or i == self.n_permutations:                          # Log progress periodically, not every run
                logger.info(f"  Completed {i}/{self.n_permutations} shuffles")

        # ── Step 4: Compare real vs. the shuffled distribution ────────────────────
        verdict = self._compare(real_result, shuffled_results)

        # ── Step 5: Save + print ────────────────────────────────────────────────────
        self._save_outputs(pair_dir, pair_name, real_result, shuffled_results, verdict)
        self._print_verdict(pair_name, real_result, shuffled_results, verdict)

        return verdict


    # ─────────────────────────────────────────────────────────────────────────
    # BLOCK SHUFFLE
    # ─────────────────────────────────────────────────────────────────────────

    def _block_shuffle(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Chop the Kalman columns into contiguous blocks and reassemble them
        in a randomly shuffled order, while keeping the original timestamp
        index in its normal chronological sequence.

        This preserves each block's internal short-term texture (a given
        week's autocorrelation, volatility, and the z-score's own bounded
        shape are untouched) while destroying the long-range alignment
        between a given calendar period and what actually happened next —
        which is exactly the thing we're testing the importance of.

        IMPORTANT — kalman_zscore_lag1 is NOT shuffled independently.
        A first version of this method shuffled lag1 using the same index
        map as every other column, on the assumption that any mismatch
        between a relocated candle's zscore and its lag1 would only affect
        "a few candles at each block boundary." That assumption breaks
        down badly once the block size gets small (which it needs to, for
        pairs with short trade durations — see MIN_BLOCK_SIZE_CANDLES):
        at a 4-candle block, a full 25% of rows sit at a boundary, feeding
        the entry-confirmation filter a stale, non-adjacent lag value a
        quarter of the time — and that turned out to be enough to bias
        every single shuffle's entry IC consistently negative, which is
        exactly the signature that made the earlier permutation-test
        results look suspicious rather than random. Recomputing lag1 from
        the ALREADY-shuffled zscore series, below, makes every row
        internally self-consistent regardless of how small the block gets,
        including all the way down to a single-candle (fully IID) shuffle.
        """

        n = len(df)                                                          # Total candles being shuffled
        n_blocks = int(np.ceil(n / self.block_size))                          # How many blocks the data splits into
        block_bounds = [                                                       # Start/end index of each block
            (i * self.block_size, min((i + 1) * self.block_size, n))
            for i in range(n_blocks)
        ]

        order = self.rng.permutation(n_blocks)                                  # A random reordering of block indices
        shuffled_positions = np.concatenate([                                    # Every original row index, in shuffled-block order
            np.arange(start, end) for start, end in (block_bounds[i] for i in order)
        ])

        shuffled = df.copy()                                                      # Keep the original index/timestamps intact
        for col in REQUIRED_KALMAN_COLUMNS:                                        # Shuffle every required column, in lockstep,
            if col == "kalman_zscore_lag1":                                          # so a relocated candle's columns stay consistent —
                continue                                                              # EXCEPT lag1, which is derived below instead
            shuffled[col] = df[col].to_numpy()[shuffled_positions]

        # Derive lag1 from the shuffled zscore itself, so lag1[i] always
        # equals shuffled zscore[i-1] exactly — no boundary mismatch is
        # possible, at any block size. bfill() only ever touches the single
        # leading row, which has no real predecessor either way.
        shuffled["kalman_zscore_lag1"] = shuffled["kalman_zscore"].shift(1).bfill()

        return shuffled                                                             # Same shape, same timestamps, scrambled content


    # ─────────────────────────────────────────────────────────────────────────
    # RUN THE REAL ENGINE ON A GIVEN DATAFRAME
    # ─────────────────────────────────────────────────────────────────────────

    def _run_engine_on_df(self, df: pd.DataFrame, run_output_dir: Path) -> Dict:
        """
        Write a DataFrame (real or shuffled) to a scratch CSV and run it
        through the real, unmodified EntryExitEngine — the exact same
        entry/exit rules, cost model, and P&L math used everywhere else in
        this pipeline. Only the DATA changes between calls, never the logic.
        """

        run_output_dir.mkdir(parents=True, exist_ok=True)
        scratch_csv = run_output_dir / "shuffled_kalman.csv"                     # Where this run's data lives
        out = df.reset_index()                                                    # Restore "timestamp" as a plain column
        if out.columns[0] != "timestamp":                                          # Defensive rename, matching other modules
            out = out.rename(columns={out.columns[0]: "timestamp"})
        out.to_csv(scratch_csv, index=False)

        # claude code changed: was EntryExitEngine(capital_usdt=..., output_dir=...) only.
        # scratch_csv is named "shuffled_kalman.csv", which doesn't match the
        # "<SYMBOL_A>_<SYMBOL_B>_kalman.csv" convention EntryExitEngine.run() parses
        # pair identity from — without passing identity explicitly here, every real
        # AND shuffled run would silently fall back to the AVAX/ATOM module defaults
        # (pair_name, half-life, time-stop) regardless of which pair is actually
        # being permutation-tested. self._pair_identity is resolved once in run().
        identity = getattr(self, "_pair_identity", {})
        engine_kwargs = {
            "capital_usdt": self.capital_usdt,
            "output_dir":   str(run_output_dir),
            "pair_name":    identity.get("pair_name"),
            "symbol_a":     identity.get("symbol_a"),
            "symbol_b":     identity.get("symbol_b"),
            "validated_half_life": identity.get("validated_half_life"),
        }
        if identity.get("exit_time_stop_hours") is not None:
            engine_kwargs["exit_time_stop_hours"] = identity["exit_time_stop_hours"]

        engine = EntryExitEngine(**engine_kwargs)
        trade_log, summary, _ = engine.run(kalman_csv=str(scratch_csv))              # Same engine, same rules, different data

        if summary.empty:                                                              # No trades fired on this replica
            return {"n_trades": 0, "win_rate": np.nan, "sharpe_ratio": np.nan,
                    "profit_factor": np.nan, "entry_ic": np.nan}

        s = summary.iloc[0]                                                              # This run's summary row
        return {                                                                           # Only the metrics the verdict needs
            "n_trades":       int(s["n_total_trades"]),
            "win_rate":        float(s["win_rate"]),
            "sharpe_ratio":     float(s["sharpe_ratio"]),
            "profit_factor":     float(s["profit_factor"]) if not np.isinf(s["profit_factor"]) else 999.0,
            "entry_ic":            float(s["entry_ic"]) if not pd.isna(s["entry_ic"]) else np.nan,
            "avg_hold_hours":         float(s["avg_hold_hours"]) if not pd.isna(s["avg_hold_hours"]) else np.nan,
        }


    # ─────────────────────────────────────────────────────────────────────────
    # COMPARE REAL VS. SHUFFLED DISTRIBUTION
    # ─────────────────────────────────────────────────────────────────────────

    def _compare(self, real: Dict, shuffled: List[Dict]) -> Dict:
        """
        Rank the real result against the shuffled distribution for each
        metric and compute an empirical permutation p-value: the fraction
        of shuffled replicas that matched or beat the real result.

        A small p-value (real result rarely matched by shuffled noise)
        means the edge is statistically distinguishable from the
        mechanical bounded-oscillator effect. A large p-value means it
        isn't — shuffled history already produces results this good.
        """

        verdict: Dict = {}
        for metric in ["win_rate", "sharpe_ratio", "profit_factor"]:                     # entry_ic handled separately below
            shuffled_values = np.array([r[metric] for r in shuffled if not pd.isna(r[metric])])
            real_value = real[metric]

            if len(shuffled_values) == 0 or pd.isna(real_value):                          # Nothing usable to compare against
                verdict[f"{metric}_percentile"] = np.nan
                verdict[f"{metric}_p_value"]      = np.nan
                continue

            percentile = float(np.mean(shuffled_values <= real_value))                     # Where the real result ranks
            p_value    = float((np.sum(shuffled_values >= real_value) + 1) / (len(shuffled_values) + 1))  # Standard +1 correction
            verdict[f"{metric}_percentile"] = percentile
            verdict[f"{metric}_p_value"]      = p_value

        # Entry IC is judged differently: we're not asking "is the real IC
        # higher than shuffled IC" (shuffled IC is meaningless noise around
        # zero either way) — we're asking "is the real IC even POSITIVE and
        # meaningfully different from zero," since IC near/below zero on
        # the REAL, unshuffled data is disqualifying on its own, regardless
        # of what shuffling shows.
        verdict["real_entry_ic"] = real.get("entry_ic", np.nan)
        verdict["real_entry_ic_meaningfully_positive"] = (
            not pd.isna(real.get("entry_ic", np.nan)) and real["entry_ic"] > 0.1
        )

        verdict["sharpe_significant"]  = (not pd.isna(verdict.get("sharpe_ratio_p_value", np.nan))
                                           and verdict["sharpe_ratio_p_value"] < (1 - SIGNIFICANCE_PERCENTILE))
        verdict["win_rate_significant"] = (not pd.isna(verdict.get("win_rate_p_value", np.nan))
                                            and verdict["win_rate_p_value"] < (1 - SIGNIFICANCE_PERCENTILE))

        verdict["edge_appears_real"] = bool(
            verdict["sharpe_significant"] and verdict["win_rate_significant"]
            and verdict["real_entry_ic_meaningfully_positive"]
        )

        return verdict


    # ─────────────────────────────────────────────────────────────────────────
    # SAVE + PRINT
    # ─────────────────────────────────────────────────────────────────────────

    def _save_outputs(
        self, pair_dir: Path, pair_name: str,
        real: Dict, shuffled: List[Dict], verdict: Dict,
    ) -> None:
        pd.DataFrame(shuffled).to_csv(pair_dir / "permutation_results.csv", index=False)
        pd.DataFrame([{**{"pair_name": pair_name}, **real, **verdict}]).to_csv(
            pair_dir / "permutation_verdict.csv", index=False
        )
        logger.info(f"\n  Saved permutation results : {pair_dir / 'permutation_results.csv'}")
        logger.info(f"  Saved verdict              : {pair_dir / 'permutation_verdict.csv'}")

    def _print_verdict(self, pair_name: str, real: Dict, shuffled: List[Dict], verdict: Dict) -> None:
        sep = "=" * 70
        print(f"\n{sep}")
        print(f"PERMUTATION TEST — {pair_name}")
        print("QuantiBot Pro — Missing Piece 4.5 of 6")
        print(sep)

        print(f"\n  Real (unshuffled) result:")
        print(f"    Win rate       : {real['win_rate']:.1%}")
        print(f"    Sharpe         : {real['sharpe_ratio']:.2f}")
        print(f"    Profit factor  : {real['profit_factor']:.2f}")
        print(f"    Entry IC       : {real['entry_ic']:.4f}")

        print(f"\n  Ranked against {len(shuffled)} block-shuffled replicas "
              f"(smallest possible p-value at this N: {1/(len(shuffled)+1):.4f}):")
        print(f"    Win rate       : beats {verdict['win_rate_percentile']:.0%} of shuffles   "
              f"(p = {verdict['win_rate_p_value']:.3f})")
        print(f"    Sharpe         : beats {verdict['sharpe_ratio_percentile']:.0%} of shuffles   "
              f"(p = {verdict['sharpe_ratio_p_value']:.3f})")
        print(f"    Profit factor  : beats {verdict['profit_factor_percentile']:.0%} of shuffles   "
              f"(p = {verdict['profit_factor_p_value']:.3f})")

        print(f"\n{sep}")
        print("  VERDICT")
        print(sep)
        if verdict["edge_appears_real"]:
            print("  ✓✓ Edge appears statistically real — beats shuffled noise AND entry IC is positive")
        else:
            print("  ✗✗ EDGE DOES NOT APPEAR STATISTICALLY REAL")
            if not verdict["real_entry_ic_meaningfully_positive"]:
                print("     - Real entry IC is not meaningfully positive on the ACTUAL data")
            if not verdict["win_rate_significant"]:
                print("     - Win rate is not distinguishable from shuffled-noise performance")
            if not verdict["sharpe_significant"]:
                print("     - Sharpe is not distinguishable from shuffled-noise performance")
            print("\n  This strongly suggests the strategy is harvesting the mechanical")
            print("  bounded-oscillator effect, not a genuine AVAX/ATOM relationship.")
            print("  Do NOT proceed to walk-forward or live monitoring on this basis.")
        print(sep)


# ── Entry point ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    """
    Run from project root:
        python -m bot.research.permutation_test_engine
    """
    engine = PermutationTestEngine()
    engine.run(
        kalman_csv="research_data/AVAX_USDT_ATOM_USDT_kalman.csv",
        pair_name="AVAX_USDT_ATOM_USDT",
    )