# claude code changed: new file — Phase B of the controlled remediation
# program (forensic-audit finding P1-1: cross-sectional winsorization used
# whole-history quantiles as clip boundaries, so a row's winsorized return
# was informed by that symbol's most extreme moves regardless of whether
# they happened before or after that row — a real look-ahead leak into
# every downstream cross-sectional feature). These tests prove the fix:
# "changing the future must not change the past."

import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.research.cross_section_engine import CrossSectionEngine, WINSOR_MIN_PERIODS


def _make_return_df(n, seed, spike_at=None, spike_magnitude=5.0):
    """
    Synthetic single-symbol OHLCV-shaped DataFrame with a 'return_1h'
    column already populated (the input _winsorise_returns() expects).
    If spike_at is given, a single huge outlier return is injected at that
    row index — used to simulate "a future flash crash".
    """
    rng = np.random.default_rng(seed)
    returns = rng.normal(0, 0.01, n)
    if spike_at is not None:
        returns[spike_at] = spike_magnitude   # a wildly extreme future return
    close = 100.0 * np.exp(np.cumsum(returns))
    df = pd.DataFrame({"close": close})
    df.index = pd.date_range("2020-01-01", periods=n, freq="1h")
    df["return_1h"] = df["close"].pct_change()
    return df


class WinsorizationLeakageTest(SimpleTestCase):

    def test_past_boundaries_unaffected_by_future_spike(self):
        """
        Two datasets share IDENTICAL history for the first `shared_rows`
        rows. Dataset B then has a huge outlier injected only AFTER that
        shared window (simulating a future flash crash). The winsorized
        values for the shared, earlier rows must be byte-identical between
        A and B — if a future value can change an earlier row's clipped
        value, the boundary was computed with look-ahead.
        """
        shared_rows = WINSOR_MIN_PERIODS + 500   # enough history for real boundaries to form
        tail_rows = 200

        df_a = _make_return_df(shared_rows + tail_rows, seed=1)

        df_b = df_a.copy()
        # Overwrite only the tail with a huge spike — shared_rows prefix
        # (including its underlying returns) is untouched.
        spike_idx = shared_rows + 50
        df_b.loc[df_b.index[spike_idx], "return_1h"] = 8.0  # +800% one-candle "crash recovery"

        engine = CrossSectionEngine()
        out_a = engine._winsorise_returns({"TEST_USDT": df_a.copy()})["TEST_USDT"]
        out_b = engine._winsorise_returns({"TEST_USDT": df_b.copy()})["TEST_USDT"]

        shared_a = out_a["return_1h_winsor"].iloc[:shared_rows]
        shared_b = out_b["return_1h_winsor"].iloc[:shared_rows]

        pd.testing.assert_series_equal(
            shared_a, shared_b,
            check_names=False,
            obj="return_1h_winsor for the shared (pre-spike) history",
        )

    def test_early_rows_kept_raw_not_clipped_from_insufficient_history(self):
        """
        Before winsor_min_periods observations exist, there is not enough
        history to trust a 1st/99th percentile estimate — those rows must
        keep their raw, unclipped return rather than being clipped against
        an unstable boundary.
        """
        n = WINSOR_MIN_PERIODS - 10
        df = _make_return_df(n, seed=2)

        engine = CrossSectionEngine()
        out = engine._winsorise_returns({"TEST_USDT": df.copy()})["TEST_USDT"]

        pd.testing.assert_series_equal(
            out["return_1h_winsor"].iloc[1:],   # skip row 0 (NaN return, no prior close)
            out["return_1h"].iloc[1:],
            check_names=False,
            obj="return_1h_winsor before enough history exists for a stable boundary",
        )

    def test_boundary_is_strictly_prior_not_inclusive_of_current_row(self):
        """
        A single extreme row must not be able to widen its OWN clip
        boundary enough to escape being clipped — the boundary at row t
        must be estimated from rows [0, t-1] only, never row t itself.
        """
        n = WINSOR_MIN_PERIODS + 100
        df = _make_return_df(n, seed=3)
        extreme_idx = n - 1   # last row: an extreme value with maximum
        df.loc[df.index[extreme_idx], "return_1h"] = 3.0  # +300% in one candle

        engine = CrossSectionEngine()
        out = engine._winsorise_returns({"TEST_USDT": df.copy()})["TEST_USDT"]

        winsorized_value = out["return_1h_winsor"].iloc[extreme_idx]
        self.assertLess(
            winsorized_value, 3.0,
            "an extreme row's own value must not inflate the boundary used to clip itself",
        )
