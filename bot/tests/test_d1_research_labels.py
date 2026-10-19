# claude code changed: new file — 2026-10-03, owner decision D-1 (signal at bar t's close, fill at bar t+1's
# open). Proves for every research-label engine: (1) known-answer open-to-open label, (2) the label never reads
# the signal close (perturbing close_t leaves it unchanged), (3) no silent fallback to close when 'open' is absent.
import numpy as np
import pandas as pd
from django.test import SimpleTestCase

from bot.quant_core.timing import (
    LABEL_TIMING_BASIS, RESEARCH_LABEL_CONTRACT, SENSITIVITY_CONTRACT_D2, research_forward_return,
)


def _ohlcv(n=60, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2025-01-01", periods=n, freq="h", tz="UTC")
    opens = 100 * np.exp(np.cumsum(0.01 * rng.standard_normal(n)))
    closes = opens * np.exp(0.005 * rng.standard_normal(n))
    return pd.DataFrame({"open": opens, "high": np.maximum(opens, closes) * 1.001, "low": np.minimum(opens, closes) * 0.999,
                         "close": closes, "volume": 1000.0}, index=idx)


def _expected(df, h, t):
    return df["open"].iloc[t + 1 + h] / df["open"].iloc[t + 1] - 1


class ContractTests(SimpleTestCase):
    def test_contract_is_the_signed_decision(self):
        self.assertEqual((RESEARCH_LABEL_CONTRACT.execution_delay_bars, RESEARCH_LABEL_CONTRACT.execution_price), (1, "open"))
        self.assertEqual(SENSITIVITY_CONTRACT_D2.execution_delay_bars, 2)
        self.assertEqual(LABEL_TIMING_BASIS, "D1_SIGNAL_CLOSE_t__FILL_OPEN_t+1")

    def test_known_answer_and_d2_sensitivity_differ(self):
        df = _ohlcv()
        self.assertAlmostEqual(research_forward_return(df, 4).iloc[10], _expected(df, 4, 10), places=15)
        d2 = research_forward_return(df, 4, SENSITIVITY_CONTRACT_D2).iloc[10]
        self.assertAlmostEqual(d2, df["open"].iloc[16] / df["open"].iloc[12] - 1, places=15)

    def test_no_fallback_to_close(self):
        with self.assertRaisesRegex(ValueError, "no fallback to close"):
            research_forward_return(_ohlcv().drop(columns="open"), 4)


class EngineLabelTests(SimpleTestCase):
    """Each engine's label equals the D-1 contract and is unaffected by the signal bar's close."""

    def _assert_d1(self, make_label, h, t=10):
        df = _ohlcv()
        lab = make_label(df)
        self.assertAlmostEqual(float(lab.iloc[t]), _expected(df, h, t), places=12)
        df2 = df.copy()
        df2.iloc[t, df2.columns.get_loc("close")] *= 1.5            # the signal print itself
        self.assertAlmostEqual(float(make_label(df2).iloc[t]), float(lab.iloc[t]), places=15)
        with self.assertRaises(ValueError):
            make_label(df.drop(columns="open"))

    def test_feature_calculator(self):
        from bot.research.feature_calculator import FeatureCalculator
        fc = FeatureCalculator()
        self._assert_d1(lambda d: fc._calculate_forward_return(d, periods=4), h=4)

    def test_derivatives_engine(self):
        from bot.research.derivatives_engine import DerivativesEngine
        eng = DerivativesEngine(forward_horizons={"forward_return_4h": 4})
        self._assert_d1(lambda d: eng._add_forward_returns(d.copy())["forward_return_4h"], h=4)

    def test_contagion_engine(self):
        from bot.research.contagion_engine import ContagionEngine
        eng = ContagionEngine(forward_horizons={"forward_return_4h": 4})
        self._assert_d1(lambda d: eng._calculate_forward_returns(d.copy(), "TEST/USDT")["forward_return_4h"], h=4)

    def test_cross_section_engine(self):
        from bot.research.cross_section_engine import CrossSectionEngine
        eng = CrossSectionEngine()
        def label(d):
            rm = pd.DataFrame({"X_USDT": d["close"].pct_change()})
            out = eng._calculate_forward_returns(pd.DataFrame(index=d.index), rm, {"X_USDT": d})
            return out["X_USDT__forward_return_1h"]
        self._assert_d1(label, h=1)

    def test_cross_section_refuses_without_ohlcv(self):
        from bot.research.cross_section_engine import CrossSectionEngine
        d = _ohlcv()
        with self.assertRaisesRegex(ValueError, "needs its OHLCV frame"):
            CrossSectionEngine()._calculate_forward_returns(pd.DataFrame(index=d.index),
                                                            pd.DataFrame({"X_USDT": d["close"].pct_change()}))
