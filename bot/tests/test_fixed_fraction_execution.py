# claude code changed: new file — Stage 2D Rev B step 8: live entry
# executability (stepSize flooring, minQty, minNotional) checked BEFORE any
# transfer or order. Offline: the adapter is an in-memory stand-in carrying
# exchange-shaped rules; nothing contacts an exchange. Expected quantities are
# plain arithmetic, not the production helpers.

from types import SimpleNamespace

from django.test import SimpleTestCase

from bot.pairs.execution import PairsExecutionEngine
from bot.pairs.margin_adapter import AmountRules


class _Adapter:
    def __init__(self, prices, rules, min_notional):
        self.prices, self.rules, self.mn, self.transfers = prices, rules, min_notional, []

    def get_ticker_price(self, s):
        return self.prices[s]

    def amount_rules(self, s):
        return self.rules[s]

    def min_notional(self, s):
        return self.mn[s]

    def transfer_in(self, s, q):            # must never be reached when the entry is refused
        self.transfers.append((s, q))
        raise AssertionError("transfer attempted for a non-executable entry")


def _engine(adapter, dry_run=False):
    eng = PairsExecutionEngine.__new__(PairsExecutionEngine)
    eng.adapter, eng.dry_run = adapter, dry_run
    return eng


TRADE = SimpleNamespace(leg_a_usdt=13.9, leg_b_usdt=11.1, direction="LONG_SPREAD")


class EntryQuantityTests(SimpleTestCase):
    def test_real_mode_floors_to_step_and_verifies(self):
        ad = _Adapter({"A/USDT": 0.7, "B/USDT": 3.0},
                      {"A/USDT": AmountRules(step=1.0, min_qty=1.0), "B/USDT": AmountRules(step=0.1, min_qty=0.1)},
                      {"A/USDT": 5.0, "B/USDT": 5.0})
        qa, qb, status = _engine(ad)._entry_quantities(TRADE, "A/USDT", "B/USDT")
        self.assertEqual(qa, 19.0)                 # floor(13.9 / 0.7 = 19.857…) to step 1
        self.assertAlmostEqual(qb, 3.7, places=12) # floor(11.1 / 3 = 3.7) to step 0.1
        self.assertEqual(status, "VERIFIED")

    def test_real_mode_rejects_below_min_notional_after_flooring(self):
        ad = _Adapter({"A/USDT": 0.7, "B/USDT": 3.0},
                      {"A/USDT": AmountRules(step=1.0, min_qty=1.0), "B/USDT": AmountRules(step=1.0, min_qty=1.0)},
                      {"A/USDT": 5.0, "B/USDT": 10.0})
        with self.assertRaisesRegex(ValueError, "minNotional"):   # B: floor(3.7) = 3 → $9 < $10
            _engine(ad)._entry_quantities(TRADE, "A/USDT", "B/USDT")

    def test_real_mode_rejects_below_min_qty(self):
        ad = _Adapter({"A/USDT": 0.7, "B/USDT": 3.0},
                      {"A/USDT": AmountRules(step=1.0, min_qty=25.0), "B/USDT": AmountRules(step=0.1, min_qty=0.1)},
                      {"A/USDT": 5.0, "B/USDT": 5.0})
        with self.assertRaisesRegex(ValueError, "minQty"):
            _engine(ad)._entry_quantities(TRADE, "A/USDT", "B/USDT")

    def test_open_pair_trade_refuses_before_any_transfer(self):
        ad = _Adapter({"A/USDT": 0.7, "B/USDT": 3.0},
                      {"A/USDT": AmountRules(step=1.0, min_qty=25.0), "B/USDT": AmountRules(step=0.1, min_qty=0.1)},
                      {"A/USDT": 5.0, "B/USDT": 5.0})
        eng = _engine(ad)
        eng.is_pair_tradeable = lambda name: True
        eng._open_positions = {}
        # claude code changed: Post-audit Phase 1 regression fix — open_pair_trade now runs the
        # pre-entry drawdown guard first (covered by test_pairs_live_safety). This test is about
        # the EXECUTABILITY refusal, so the guard is passed explicitly and the log is asserted —
        # otherwise a guard block (this SimpleTestCase has no DB) would make the test pass for
        # the wrong reason. capital_usdt added: the real PairConfig always carries it.
        eng.drawdown_entry_check = lambda capital: (True, "guard passed for this executability test")
        cfg = SimpleNamespace(pair_name="A_USDT/B_USDT", binance_symbol_a="A/USDT", binance_symbol_b="B/USDT",
                              capital_usdt=500.0)
        decision = SimpleNamespace(action="ENTER", trade=SimpleNamespace(**vars(TRADE), executability="UNVERIFIED"))
        with self.assertLogs("bot.pairs.execution", level="ERROR") as logs:
            self.assertIsNone(eng.open_pair_trade(cfg, decision))
        self.assertIn("not executable", logs.output[0])
        self.assertIn("minQty", logs.output[0])
        self.assertEqual(ad.transfers, [])

    def test_open_pair_trade_without_capital_fails_closed_not_raises(self):   # claude code changed: new — Phase 1 regression fix
        ad = _Adapter({"A/USDT": 0.7, "B/USDT": 3.0}, {}, {})
        eng = _engine(ad)
        eng.is_pair_tradeable = lambda name: True
        eng._open_positions = {}
        cfg = SimpleNamespace(pair_name="A_USDT/B_USDT", binance_symbol_a="A/USDT", binance_symbol_b="B/USDT")
        decision = SimpleNamespace(action="ENTER", trade=SimpleNamespace(**vars(TRADE), executability="UNVERIFIED"))
        with self.assertLogs("bot.pairs.execution", level="CRITICAL") as logs:
            self.assertIsNone(eng.open_pair_trade(cfg, decision))
        self.assertIn("not positive", logs.output[0])
        self.assertEqual(ad.transfers, [])

    def test_dry_run_is_not_claimed_executable(self):
        ad = _Adapter({"A/USDT": 0.7, "B/USDT": 3.0}, {}, {})
        qa, qb, status = _engine(ad, dry_run=True)._entry_quantities(TRADE, "A/USDT", "B/USDT")
        self.assertAlmostEqual(qa, 13.9 / 0.7, places=12)
        self.assertEqual(status, "UNVERIFIED (dry run)")
