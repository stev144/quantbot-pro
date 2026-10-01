# ============================================================
# bot/pairs/monitor_engine.py
# claude code changed: new file — pairs-pilot universe expansion
# (2026-09-28). Extends live monitoring from the 3 entry-eligible pairs
# to all 10 pairs the dashboard's pair-selector dropdown lists, per the
# user's explicit request: "include all those pairs... monitor their
# dynamic hedge ratios, but put valid tags if a pair is invalidated or
# degraded."
#
# WHY THIS IS A SEPARATE, LIGHTER CLASS FROM PairSignalEngine
#
# PairSignalEngine.__init__() unconditionally constructs a full
# EntryExitEngine, which unconditionally constructs a KalmanPositionSizer,
# which REQUIRES a real validated_win_rate and raises if one isn't
# supplied (bot/research/entry_exit_engine.py's own documented
# contract). 8 of the 10 requested pairs have no real, current win rate:
# AVAX_USDT/ATOM_USDT, AVA_USDT/PHA_USDT, FET_USDT/DASH_USDT,
# ONT_USDT/RIF_USDT, SOL_USDT/MASK_USDT fail the CURRENT cointegration
# scan outright (confirmed directly against research_data/
# cointegration_pairs.csv, not assumed); DEXE_USDT/ALGO_USDT,
# DOT_USDT/LINK_USDT, FET_USDT/BCH_USDT were never in that scan at all.
#
# All 10 DO have a permutation_verdict.csv claiming edge_appears_real=True
# — but that's stale data, not a real validation: a pair that isn't even
# cointegrated cannot have a genuine permutation-tested edge on its
# spread. These verdicts almost certainly predate the out-of-sample
# persistence check this project added later (which retroactively
# rejected 76 of 84 previously-passing pairs — see
# cointegration_engine.py's own OOS-persistence comment). Loading a win
# rate from them would silently resurrect exactly the kind of stale-CSV
# trap this project has been burned by before. bot/pairs/config.py's
# _load_validated_win_rate() now checks the CURRENT passes_filters
# status FIRST and refuses to even look at the permutation file for a
# pair that currently fails cointegration — see that function's own
# comment.
#
# Rather than construct a full trading engine for a pair that
# structurally can never trade (cointegration_gate alone permanently
# blocks it — see bot/pairs/health.py) and feed it a fabricated
# placeholder win rate just to satisfy KalmanPositionSizer's constructor,
# this class does ONLY what a permanently-non-tradeable pair actually
# needs: real, live, causal Kalman beta tracking, for evaluate_pair_health()
# and the dashboard's DRIFT tile. No EntryExitEngine, no position sizer,
# no fabricated data anywhere.
# ============================================================

from __future__ import annotations

from typing import Optional

import numpy as np

from bot.pairs.config import PairConfig
from bot.pairs.kalman_online import kalman_step
from bot.research.kalman_filter_engine import KalmanState


class PairMonitorEngine:
    """Real, live Kalman beta tracking for a pair that is not currently
    entry-eligible (fails the live cointegration gate). Structurally
    incapable of sizing or executing a trade — it never constructs an
    EntryExitEngine/KalmanPositionSizer at all, not just gated at the
    call site — so a bug elsewhere that mistakenly tried to trade one of
    these pairs would hit a real AttributeError, not silently proceed on
    fabricated data.
    """

    def __init__(self, pair_config: PairConfig):
        self.pair_config = pair_config
        theta_0 = np.array([pair_config.hedge_ratio, pair_config.intercept])
        P_0 = np.eye(2) * 1.0  # matches PairSignalEngine/KalmanFilterEngine._initialise_state()'s own seed exactly
        self._state = KalmanState(theta=theta_0, P=P_0)
        self._candle_index = 0

    @property
    def current_beta(self) -> float:
        return float(self._state.theta[0])

    def update(self, price_a: float, price_b: float) -> None:
        """Advances the Kalman state by one real candle. No z-score, no
        lag buffer, no decision logic — this pair is monitoring-only."""
        self._state, _native = kalman_step(self._state, price_a, price_b, self._candle_index)
        self._candle_index += 1
