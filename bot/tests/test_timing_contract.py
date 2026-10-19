# claude code changed: new file — 2026-10-03, remediation F-04 (zero-latency labels).
import numpy as np
import pandas as pd
from django.test import SimpleTestCase
from scipy import stats

from bot.quant_core.timing import TimingContract, forward_label


def _bars(opens, closes):
    idx = pd.date_range("2024-01-01", periods=len(opens), freq="h", tz="UTC")
    return pd.DataFrame({"open": opens, "close": closes}, index=idx)


class TimingContractTests(SimpleTestCase):
    def test_no_default_delay_and_same_close_is_refused(self):
        with self.assertRaises(TypeError):
            TimingContract()                                         # d must be stated
        with self.assertRaisesRegex(ValueError, "F-04"):
            TimingContract(execution_delay_bars=0, execution_price="close")
        with self.assertRaisesRegex(ValueError, "impossible"):
            TimingContract(execution_delay_bars=0, execution_price="open", allow_same_close_diagnostic=True)

    def test_label_known_answer(self):
        df = _bars([10, 11, 12, 13, 14, 15], [10.5, 11.5, 12.5, 13.5, 14.5, 15.5])
        lab = forward_label(df, 2, TimingContract(execution_delay_bars=1))
        # t=0: fill at open[1]=11, exit at open[3]=13 → 13/11 − 1
        self.assertAlmostEqual(lab.iloc[0], 13 / 11 - 1)
        self.assertTrue(np.isnan(lab.iloc[3]))                       # open[6] does not exist

    def test_label_uses_no_information_before_the_fill(self):
        df = _bars(list(range(10, 30)), [c + 0.5 for c in range(10, 30)])
        lab1 = forward_label(df, 3, TimingContract(execution_delay_bars=1))
        df2 = df.copy()
        df2.iloc[5, df2.columns.get_loc("close")] = 999.0               # bar 5's close (information at t=5)
        lab2 = forward_label(df2, 3, TimingContract(execution_delay_bars=1))
        pd.testing.assert_series_equal(lab1, lab2)                   # open-to-open label never reads a close

    def test_bid_ask_bounce_creates_fake_reversal_only_under_same_close(self):
        # Prices print alternately at bid/ask around a random-walk mid: the true mid has no
        # reversal, but close-to-close returns are negatively autocorrelated (bounce).
        rng = np.random.default_rng(0)
        n, half_spread = 5000, 0.001
        mid = 100 * np.exp(np.cumsum(0.002 * rng.standard_normal(n)))
        side = rng.choice([-1.0, 1.0], size=n)
        close = mid * (1 + side * half_spread)
        opens = mid * (1 + rng.choice([-1.0, 1.0], size=n) * half_spread)   # fresh, independent print at the next open
        df = _bars(opens, close)
        signal = -df["close"].pct_change()                           # reversal: buy after a down print
        same_close = forward_label(df, 1, TimingContract(execution_delay_bars=0, execution_price="close",
                                                         allow_same_close_diagnostic=True))
        next_open = forward_label(df, 1, TimingContract(execution_delay_bars=1))
        m = signal.notna() & same_close.notna() & next_open.notna()
        ic_same = stats.spearmanr(signal[m], same_close[m])[0]
        ic_next = stats.spearmanr(signal[m], next_open[m])[0]
        # analytic: bounce gives close-to-close returns lag-1 autocorrelation −s²/(σ²+2s²) =
        # −1e-6/(4e-6+2e-6) ≈ −0.167, so the reversal IC under same-close ≈ +0.167 (observed ≈ 0.157)
        self.assertGreater(ic_same, 0.10)                            # spurious 'predictability' from the bounce
        self.assertLess(abs(ic_next), 0.05)                          # gone once execution is causal
