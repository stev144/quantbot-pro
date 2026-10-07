# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_decision_journal_phase1.py
#
# claude code changed: new file — 2026-10-07, Trading Decision Journal Phase 1 (backtester-only recorder).
# Owner sign-off: TRADING_DECISION_JOURNAL_PHASE1_OWNER_SIGNOFF_2026-10-07.md (D1–D8, test requirements 1–14).
#
# Oracles are INDEPENDENT of the recorder wherever possible:
#   * the backtester's own counters (rejections dict, signals_seen, unfilled_signals_at_data_end) count every
#     evaluation exactly once, so they bound the decision denominator;
#   * the owner's D4 table is re-typed here verbatim, not imported from the taxonomy;
#   * the backtest result with the recorder OFF is the reference for "no behaviour change".
# Real data follows the established Backtester(df.tail(2000)) pattern (test_backtester_venue_selection.py).
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import json
import re
import tempfile
import uuid
from pathlib import Path
from unittest import mock, skipUnless

import pandas as pd
from django.db import IntegrityError, transaction
from django.test import TestCase

from bot.backtesting.backtester import BACKTEST_LOOP_START, Backtester
from bot.journal import taxonomy as T
from bot.journal.decision_recorder import BacktestDecisionRecorder, RecorderDefect
from bot.journal.models import (DecisionEvent, JournalGap, JournalRecordIsImmutableError, JournalRun, MarketSnapshot,
                                TradeRecord, EXECUTION_MODES)

DATA = Path("data/BTC_USDT_1h.csv")
HOUR = pd.Timedelta(hours=1)


def _df(n=2000):
    df = pd.read_csv(DATA)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df.set_index("timestamp").tail(n)


def _canon(results: dict) -> str:
    return json.dumps(results, sort_keys=True, default=str)


def _run(df, *, journal=True, allow_unvalidated=True, recorder_cls=BacktestDecisionRecorder, patch=None):
    rec = recorder_cls("BTC_USDT") if journal else None
    bt = Backtester(df=df, allow_unvalidated_strategies=allow_unvalidated, journal_recorder=rec)
    if patch:
        patch(bt)
    results = bt.run()
    return bt, results, rec


def _state(bt) -> str:
    return _canon({"rejections": bt.rejections, "signals_seen": bt.signals_seen, "balance": bt.balance,
                   "unfilled": bt.unfilled_signals_at_data_end, "equity": bt.equity_curve, "mtm": bt.mtm_points,
                   "max_dd": bt.max_drawdown, "fees": bt.total_fees})


# owner D4, re-typed verbatim (raw reason, path) → (status, stage, code)
OWNER_D4 = {
    ("high_volatility_blocked", "HIGH_VOLATILITY"): ("REGIME_REJECTED", "REGIME", "WRONG_REGIME"),
    ("confidence_too_low_medium", "TRENDING_UP"): ("REGIME_REJECTED", "REGIME", "REGIME_CONFIDENCE_TOO_LOW"),
    ("adx_below_minimum_for_trend", "TRENDING_DOWN"): ("REGIME_REJECTED", "REGIME", "ADX_TOO_LOW"),
    ("strategy_not_validated_untested", "RANGING"): ("SYSTEM_REJECTED", "GOVERNANCE", "STRATEGY_NOT_VALIDATED"),
    ("longs_disabled", "TRENDING_UP"): ("SYSTEM_REJECTED", "CONFIGURATION", "LONGS_DISABLED"),
    ("shorts_disabled", "RANGING"): ("SYSTEM_REJECTED", "CONFIGURATION", "SHORTS_DISABLED"),
    ("sell_rejected_in_uptrend", "TRENDING_UP"): ("REJECTED", "REGIME", "DIRECTION_CONFLICTS_REGIME"),
    ("buy_rejected_in_downtrend", "TRENDING_DOWN"): ("REJECTED", "REGIME", "DIRECTION_CONFLICTS_REGIME"),
    ("mean_reversion_returned_none", "RANGING"): ("SYSTEM_REJECTED", "STRATEGY_RUNTIME", "STRATEGY_OUTPUT_INVALID"),
    ("missing_data", "TRENDING_UP"): ("DATA_REJECTED", "DATA", "DATA_QUALITY_FAILURE"),
    ("structure_analysis_failed", "TRENDING_UP"): ("SYSTEM_REJECTED", "STRATEGY_RUNTIME", "STRUCTURE_ANALYSIS_FAILURE"),
    ("flat_market", "TRENDING_UP"): ("NO_SIGNAL", "STRATEGY", "FLAT_MARKET"),
    ("no_ema_trend", "TRENDING_DOWN"): ("NO_SIGNAL", "STRATEGY", "NO_TREND"),
    ("structure_not_uptrend_ranging", "TRENDING_UP"): ("NO_SIGNAL", "STRATEGY", "STRUCTURE_NOT_CONFIRMED"),
    ("structure_not_downtrend_unknown", "TRENDING_DOWN"): ("NO_SIGNAL", "STRATEGY", "STRUCTURE_NOT_CONFIRMED"),
    ("no_pullback", "TRENDING_UP"): ("NO_SIGNAL", "STRATEGY", "NO_PULLBACK"),
    ("no_momentum", "TRENDING_DOWN"): ("NO_SIGNAL", "STRATEGY", "NO_MOMENTUM"),
    ("non_ranging_market", "RANGING"): ("NO_SIGNAL", "STRATEGY", "NON_RANGING_MARKET"),
    ("range_too_wide", "RANGING"): ("NO_SIGNAL", "STRATEGY", "RANGE_TOO_WIDE"),
    ("no_extreme", "RANGING"): ("NO_SIGNAL", "STRATEGY", "NO_EXTREME"),
    ("buy_no_confirmation", "RANGING"): ("NO_SIGNAL", "STRATEGY", "BUY_NO_CONFIRMATION"),
    ("sell_no_confirmation", "RANGING"): ("NO_SIGNAL", "STRATEGY", "SELL_NO_CONFIRMATION"),
}


class TaxonomyMappingTests(TestCase):
    """Requirement 4/5 and the 'incorrect reason mapping' negative test."""

    def test_every_owner_d4_row_maps_exactly(self):
        for (raw, regime), expected in OWNER_D4.items():
            m = T.map_no_signal_reason(raw, regime, warmup_open=False)
            self.assertEqual((m.status, m.stage, m.code), expected, raw)

    def test_structured_detail_not_in_the_code(self):
        self.assertEqual(T.map_no_signal_reason("confidence_too_low_medium", "TRENDING_UP", warmup_open=False).detail,
                         {"confidence": "medium"})
        self.assertEqual(T.map_no_signal_reason("strategy_not_validated_untested", "RANGING", warmup_open=False).detail,
                         {"verdict": "untested"})
        m = T.map_no_signal_reason("structure_not_downtrend_unknown", "TRENDING_DOWN", warmup_open=False)
        self.assertEqual(m.detail, {"label": "unknown", "required": "DOWNTREND"})

    def test_warmup_vs_post_warmup_data_failure(self):
        for raw, regime in (("not_enough_data", "TRENDING_UP"), ("indicator_warmup", "TRENDING_DOWN"),
                            ("not_enough_data", "RANGING"), ("rsi_not_ready", "RANGING"),
                            ("atr_unavailable", "RANGING"), ("bb_unavailable", "RANGING")):
            self.assertIsInstance(T.map_no_signal_reason(raw, regime, warmup_open=True), T.Warmup)
            m = T.map_no_signal_reason(raw, regime, warmup_open=False)
            self.assertEqual((m.status, m.stage, m.code), ("DATA_REJECTED", "DATA", "DATA_QUALITY_FAILURE"))

    def test_direction_toggle_lifecycle_depends_on_where_the_router_checks_it(self):
        pre = T.map_no_signal_reason("longs_disabled", "TRENDING_UP", warmup_open=False)
        post = T.map_no_signal_reason("longs_disabled", "RANGING", warmup_open=False)
        self.assertEqual((pre.candidate, pre.gate), (False, T.G_DIR_PRE))       # before MovingAverageStrategy ran
        self.assertEqual((post.candidate, post.direction, post.gate), (True, "LONG", T.G_DIR_POST))   # after MR signalled

    def test_unknown_or_out_of_context_reasons_are_never_classified(self):
        for raw, regime in (("unknown", "TRENDING_UP"), ("", "RANGING"), (None, "RANGING"), ("NO_PULLBACK", "TRENDING_UP"),
                            ("no_pullback", "RANGING"), ("no_extreme", "TRENDING_UP"), ("high_volatility_blocked", "RANGING"),
                            ("unhandled_regime_X", "TRENDING_UP"), ("confidence_too_low_", "TRENDING_UP")):
            with self.assertRaises(T.UnmappedReason, msg=raw):
                T.map_no_signal_reason(raw, regime, warmup_open=False)
        with self.assertRaises(T.UnmappedReason):
            T.path_for_regime("SIDEWAYS")

    def test_fill_refusals(self):
        self.assertEqual(T.map_fill_refusal("size_not_positive", "LONG").code, "SIZE_BELOW_MINIMUM")
        m = T.map_fill_refusal("zero_stop_distance", "SHORT")
        self.assertEqual((m.status, m.stage, m.code), ("SYSTEM_REJECTED", "STRATEGY_RUNTIME", "INVALID_SIGNAL"))
        with self.assertRaises(T.UnmappedReason):
            T.map_fill_refusal(None, "LONG")

    def test_transition_table(self):
        T.validate_terminal(["EVALUATED"], "NO_SIGNAL", "NO_PULLBACK", "STRATEGY")
        T.validate_terminal(["EVALUATED", "SIGNAL_GENERATED"], "CENSORED_END_OF_DATA", None, None)
        bad = ((["EVALUATED"], "EXECUTED", None, None),                                    # no execution attempt
               (["EVALUATED", "SIGNAL_GENERATED"], "EXECUTION_FAILED", None, None),         # D5: censoring is not a failure
               (["EVALUATED"], "NO_SIGNAL", "NO_PULLBACK", "REGIME"),                       # wrong stage for the code
               (["EVALUATED"], "NO_SIGNAL", "WRONG_REGIME", "REGIME"),                      # code belongs to another status
               (["SIGNAL_GENERATED"], "CENSORED_END_OF_DATA", None, None),                  # must start at EVALUATED
               (["EVALUATED", "GATES_EVALUATED"], "REJECTED", "SIZE_BELOW_MINIMUM", "RISK"))  # skipped a stage
        for args in bad:
            with self.assertRaises(ValueError, msg=args):
                T.validate_terminal(*args)


class SourceAuditTests(TestCase):
    """Every reason string the router/strategies can emit is mapped, and the documented gate order is the order
    the checks appear in the source (a re-ordered or new check fails here, not silently in the journal)."""

    SOURCES = {"router": "bot/engines/strategy_router.py", "ma": "bot/strategies/moving_average.py",
               "mr": "bot/strategies/mean_reversion_strategy.py"}

    def _reasons(self, key):
        src = Path(self.SOURCES[key]).read_text(encoding="utf-8")
        return [m.group(2) for m in re.finditer(r"_no_signal\(\s*(reason=)?f?\"([a-z_{}.()]+)\"", src)]

    def test_every_emitted_reason_is_mapped_or_explicitly_unmapped(self):
        known_unmapped = {"unhandled_regime_{regime}"}   # router fallback for an unknown regime → MAPPING_FAILURE by design
        prefixes = [p[0] for p in T._PREFIX]
        exact = set(T._EXACT) | {"longs_disabled", "shorts_disabled", "sell_rejected_in_uptrend",
                                 "buy_rejected_in_downtrend"} | {r for d in T._WARMUP_CLASS.values() for r in d}
        found = [r for k in self.SOURCES for r in self._reasons(k)]
        self.assertGreater(len(found), 25)
        for r in found:
            if "{" in r:
                base = r.split("{")[0]
                self.assertTrue(r in known_unmapped or base in prefixes, r)
            else:
                self.assertIn(r, exact, r)

    def test_gate_order_matches_source_order(self):
        # The branch predicates (MA: `if bullish:` / `if bearish:`; MR: oversold / overbought) are evaluated BEFORE
        # the checks inside their branches, but their refusal (no_ema_trend / no_extreme) is the fall-through
        # return at the very END of the method. So: every other gate's first appearance follows the documented
        # order, and the branch-predicate refusal is the last reason in the source.
        for key, gates, warm, branch in (("ma", T.MA_GATES, T._WARMUP_CLASS["TREND"], "no_ema_trend"),
                                         ("mr", T.MR_GATES, T._WARMUP_CLASS["RANGING"], "no_extreme")):
            reasons = self._reasons(key)
            self.assertEqual(reasons[-1], branch)
            order = [T._EXACT.get(r, (None, None))[1] or warm.get(r) or
                     ("MA_STRUCTURE_CONFIRMED" if r.startswith("structure_not_") else None) for r in reasons[:-1]]
            seen = [g for g in dict.fromkeys(order) if g]
            branch_gate = T._EXACT[branch][1]
            self.assertEqual(seen, [g for g in gates if g in seen and g != branch_gate])
            self.assertLess(gates.index(branch_gate), gates.index(seen[-1]))   # predicate precedes the branch checks

    def test_mr_previous_candle_check_is_unreachable_after_min_candles(self):
        from bot.strategies.mean_reversion_strategy import MeanReversionStrategy
        self.assertGreaterEqual(MeanReversionStrategy().min_candles, 2)


@skipUnless(DATA.exists(), "data/BTC_USDT_1h.csv not present")
class RecorderOnRealBacktestTests(TestCase):
    """Requirements 1–4, 6, 8, 10, 12, 13 and the timing tests, on one real backtest (recorder ON vs OFF)."""

    @classmethod
    def setUpTestData(cls):
        cls.df = _df()
        cls.bt_off, cls.res_off, _ = _run(cls.df, journal=False)
        cls.bt_on, cls.res_on, cls.rec = _run(cls.df)
        cls.jrun = JournalRun.objects.get(run_id=cls.rec.run_id)
        cls.dec = list(DecisionEvent.objects.filter(run=cls.jrun).order_by("candle_index"))

    def test_recorder_on_off_identical_results_and_state(self):
        self.assertEqual(_canon(self.res_on), _canon(self.res_off))
        self.assertEqual(_state(self.bt_on), _state(self.bt_off))
        self.assertGreater(len(self.res_off["trades"]), 3)        # the test exercises real fills

    def test_run_closed_complete(self):
        self.assertEqual(self.jrun.status, "CLOSED", self.jrun.incomplete_reasons)
        self.assertEqual(self.jrun.n_decisions, len(self.dec))
        self.assertFalse(JournalGap.objects.filter(run=self.jrun).exists())

    def test_denominator_equals_the_backtesters_own_evaluation_count(self):
        evaluations = sum(self.bt_off.rejections.values()) + self.bt_off.signals_seen
        warm = sum(self.jrun.coverage["warmup_raw_reasons"].values())
        self.assertEqual(len(self.dec) + warm, evaluations)
        self.assertEqual(self.jrun.coverage["counts"]["EVALUATED"], len(self.dec))

    def test_per_raw_reason_counts_preserve_existing_no_signal_semantics(self):
        by_raw = {}
        for d in self.dec:
            if d.status not in ("EXECUTED", "CENSORED_END_OF_DATA") and d.raw_reason not in T.FILL_REFUSALS:
                by_raw[d.raw_reason] = by_raw.get(d.raw_reason, 0) + 1
        for raw, n in self.jrun.coverage["warmup_raw_reasons"].items():
            by_raw[raw] = by_raw.get(raw, 0) + n
        nonzero = {k: v for k, v in self.bt_off.rejections.items() if v}
        self.assertEqual(by_raw, nonzero)
        self.assertEqual(sum(1 for d in self.dec if d.lifecycle_stages[:2] == ["EVALUATED", "SIGNAL_GENERATED"]
                             and d.reason_code != "DIRECTION_CONFLICTS_REGIME" and d.reason_code not in
                             ("LONGS_DISABLED", "SHORTS_DISABLED")), self.bt_off.signals_seen)

    def test_no_signal_is_recorded_with_specific_reasons(self):
        ns = [d for d in self.dec if d.status == "NO_SIGNAL"]
        self.assertGreater(len(ns), 100)
        self.assertTrue(all(d.reason_code in T.REASON_CODES and d.reason_stage == "STRATEGY" for d in ns))
        self.assertGreater(len({d.reason_code for d in ns}), 2)

    def test_no_decision_where_the_engine_did_not_evaluate(self):
        idx = {d.candle_index for d in self.dec}
        self.assertEqual(BACKTEST_LOOP_START, 100)                            # owner D1: the existing candle-100 boundary
        self.assertEqual(min(idx), 100)                                       # first evaluation exactly at the boundary
        for t in self.res_off["trades"]:
            blocked = set(range(t["entry_index"], t["exit_index"] + 1))       # in position + the exit candle
            self.assertFalse(idx & blocked, (t["entry_index"], t["exit_index"]))
        cov = self.jrun.coverage
        self.assertEqual(sum(cov["counts"].values()), cov["n_candles"])
        self.assertEqual(cov["counts"]["PRE_LOOP"], BACKTEST_LOOP_START)
        exit_idx = {i for cat, a, b, _, _ in cov["intervals"] if cat == "NOT_EVALUATED_EXIT_CANDLE" for i in range(a, b + 1)}
        trades = self.res_off["trades"]
        self.assertTrue(exit_idx <= {t["exit_index"] for t in trades})      # only real in-loop exit candles
        self.assertIn(len(exit_idx), (len(trades), len(trades) - 1))       # the last trade may be force-closed after the loop
        self.assertEqual(cov["counts"]["NOT_EVALUATED_ENTRY_PENDING"], 0)
        rebuilt = []
        for cat, a, b, _, _ in cov["intervals"]:
            rebuilt += [cat] * (b - a + 1)
        self.assertEqual(len(rebuilt), cov["n_candles"])
        self.assertEqual([i for i, c in enumerate(rebuilt) if c == "EVALUATED"], sorted(idx))

    def test_executed_links_to_exactly_one_trade_at_the_next_open(self):
        ex = [d for d in self.dec if d.status == "EXECUTED"]
        trades = self.res_off["trades"]
        self.assertEqual(len(ex), len(trades))
        self.assertEqual(sorted(d.backtest_trade_seq for d in ex), list(range(1, len(ex) + 1)))
        for d, t in zip(sorted(ex, key=lambda d: d.backtest_trade_seq), trades):
            self.assertEqual(d.backtest_trade_id, f"{self.jrun.run_id}:T{d.backtest_trade_seq:06d}")
            self.assertEqual(t["entry_index"], d.candle_index + 1)              # next-open fill (altered timing fails)
            self.assertEqual(d.fill_candle_index, d.candle_index + 1)
            self.assertEqual(d.lifecycle_stages, list(T.STAGE_ORDER))
            self.assertIsNone(d.reason_code)
            self.assertTrue(d.execution_eligible)
        self.assertTrue(all(d.backtest_trade_seq is None for d in self.dec if d.status != "EXECUTED"))

    def test_gate_vector_is_observed_control_flow_and_separate_from_status(self):
        for d in self.dec:
            v = d.gate_vector
            self.assertEqual(set(v), set(T.GATE_ORDER))
            fails = [g for g, r in v.items() if r == "FAIL"]
            self.assertEqual(v[T.G_CONF], "NOT_EVALUATED")                    # require_high_confidence=False in the backtester
            if d.status in ("EXECUTED", "CENSORED_END_OF_DATA"):
                self.assertEqual(fails, [])
            else:
                self.assertEqual(len(fails), 1, d.reason_code)
                path = T.PATHS[T.path_for_regime(d.snapshot.values["regime_regime"])]
                k = path.index(fails[0])
                self.assertTrue(all(v[g] in ("PASS", "NOT_EVALUATED") for g in path[:k]))
                self.assertTrue(all(v[g] == "NOT_EVALUATED" for g in path[k + 1:]))
        pull = next(d for d in self.dec if d.reason_code == "NO_PULLBACK")
        self.assertEqual((pull.gate_vector["MA_STRUCTURE_CONFIRMED"], pull.gate_vector["MA_PULLBACK"],
                          pull.gate_vector["MA_MOMENTUM"], pull.gate_vector[T.G_MATCH]),
                         ("PASS", "FAIL", "NOT_EVALUATED", "NOT_EVALUATED"))

    def test_information_time_and_snapshot_are_causal(self):
        closes = self.df["close"].to_numpy()
        for d in self.dec:
            s = d.snapshot
            self.assertEqual(d.information_time - d.candle_open_time, HOUR)
            self.assertEqual(pd.Timestamp(d.candle_open_time), self.df.index[d.candle_index])
            self.assertEqual(s.window_end_time, d.candle_open_time)
            self.assertEqual(s.values["candle_close"], float(closes[d.candle_index]))   # the decision candle, not i+1
            self.assertTrue(all(pd.Timestamp(a) <= pd.Timestamp(s.information_time) for a in s.available_at.values()))
            self.assertEqual(s.snapshot_kind, "SIMULATED_CAUSAL")

    def test_truncation_invariance_no_future_information(self):
        k = len(self.df) - 400
        _, _, rec_k = _run(self.df.iloc[:k])
        short = {d.candle_index: d for d in DecisionEvent.objects.filter(run__run_id=rec_k.run_id)}
        compared = 0
        for d in self.dec:
            if d.candle_index >= k - 1:
                break
            e = short[d.candle_index]
            self.assertEqual((e.snapshot.values, e.snapshot.input_window_sha256), (d.snapshot.values, d.snapshot.input_window_sha256))
            self.assertEqual((e.status, e.reason_code, e.gate_vector), (d.status, d.reason_code, d.gate_vector))
            compared += 1
        self.assertGreater(compared, 1000)

    def test_provenance_complete_and_versions_preserved(self):
        from bot.quant_core.provenance import RESEARCH_ENGINE_METHODOLOGY, data_version
        r = self.jrun
        self.assertEqual((r.environment, r.execution_mode), ("BACKTEST", "RESEARCH"))
        self.assertIn(r.execution_mode, EXECUTION_MODES)
        self.assertEqual((r.taxonomy_version, r.schema_version, r.gate_order_version, r.feature_set_version),
                         (T.TAXONOMY_VERSION, T.SCHEMA_VERSION, T.GATE_ORDER_VERSION, T.FEATURE_SET_VERSION))
        self.assertEqual(r.methodology_version, RESEARCH_ENGINE_METHODOLOGY)
        for h in (r.config_hash, r.data_version, r.zones_hash, r.market_profile_hash):
            self.assertRegex(h, r"^[0-9a-f]{64}$")
        self.assertTrue(r.code_commit and r.strategy_version.startswith("UNVERSIONED@"))
        frame = self.df[["open", "high", "low", "close", "volume"]]
        self.assertEqual(r.data_version, data_version({"BTC_USDT": frame}, timeframe="1h",
                                                      columns=["open", "high", "low", "close", "volume"]).data_version)
        for d in self.dec[:50]:
            self.assertEqual((d.environment, d.execution_mode, d.data_version, d.config_hash, d.taxonomy_version),
                             (r.environment, r.execution_mode, r.data_version, r.config_hash, r.taxonomy_version))
            self.assertTrue(d.research_zone)

    def test_journal_rows_are_immutable(self):
        d = DecisionEvent.objects.get(pk=self.dec[0].pk)
        original = d.reason_code
        d.reason_code = "SOMETHING_ELSE"
        with self.assertRaises(JournalRecordIsImmutableError):
            d.save()
        with self.assertRaises(JournalRecordIsImmutableError):
            DecisionEvent.objects.filter(pk=d.pk).update(reason_code="SOMETHING_ELSE")
        with self.assertRaises(JournalRecordIsImmutableError):
            DecisionEvent.objects.filter(pk=d.pk).delete()
        with self.assertRaises(JournalRecordIsImmutableError):
            d.snapshot.delete()
        run = JournalRun.objects.get(pk=self.jrun.pk)
        run.status = "OPEN"
        with self.assertRaises(JournalRecordIsImmutableError):
            run.save()                                                     # closed runs never re-open
        self.assertEqual(DecisionEvent.objects.get(pk=d.pk).reason_code, original)   # nothing was changed

    def test_duplicate_decision_is_rejected(self):
        d = DecisionEvent.objects.get(pk=self.dec[0].pk)
        d._state.adding = True
        with self.assertRaises(IntegrityError), transaction.atomic():
            d.save()                                                       # same primary key
        d.pk = uuid.uuid4()
        with self.assertRaises(IntegrityError), transaction.atomic():
            d.save()                                                       # same (run, symbol, candle)

    def test_export_round_trip(self):
        from bot.journal.decision_export import export_run, read_export
        with tempfile.TemporaryDirectory() as tmp:
            path = export_run(self.jrun, tmp)
            df, prov = read_export(path)
            self.assertEqual(list(df["decision_id"]), [str(d.decision_id) for d in self.dec])
            self.assertEqual(set(df["run_id"]), {self.jrun.run_id})
            self.assertEqual(prov.kind, "DECISION_STREAM")
            self.assertEqual(prov.data_version, self.jrun.data_version)
            self.assertEqual(prov.extra["config"]["journal_run_id"], self.jrun.run_id)
            self.assertEqual(prov.code_commit, self.jrun.code_commit)
            self.assertEqual(list(df["gate_vector"]), [d.gate_vector for d in self.dec])
            with self.assertRaises(FileExistsError):
                export_run(self.jrun, tmp)                                  # never overwrites


@skipUnless(DATA.exists(), "data/BTC_USDT_1h.csv not present")
class RecorderEdgeCaseTests(TestCase):

    def test_governance_block_is_system_rejected_never_no_signal(self):
        bt, res, rec = _run(_df(), allow_unvalidated=False)
        _, res_off, _ = _run(_df(), journal=False, allow_unvalidated=False)
        self.assertEqual(_canon(res), _canon(res_off))
        dec = DecisionEvent.objects.filter(run__run_id=rec.run_id)
        gov = dec.filter(reason_code="STRATEGY_NOT_VALIDATED")
        self.assertTrue(gov.exists())
        self.assertTrue(all(d.status == "SYSTEM_REJECTED" and d.reason_stage == "GOVERNANCE" and d.origin == "SYSTEM"
                            and d.reason_detail.get("verdict") for d in gov))
        n_raw = sum(v for k, v in bt.rejections.items() if k.startswith("strategy_not_validated_"))
        self.assertEqual(gov.count(), n_raw)

    def test_final_candle_signal_is_censored_not_execution_failed(self):
        df = _df()
        _, _, rec = _run(df)
        cand = DecisionEvent.objects.filter(run__run_id=rec.run_id, status="EXECUTED").order_by("candle_index").first()
        cut = df.iloc[:cand.candle_index + 1]                             # the candidate is now on the last candle
        bt, res, rec2 = _run(cut)
        last = DecisionEvent.objects.get(run__run_id=rec2.run_id, candle_index=cand.candle_index)
        self.assertEqual(bt.unfilled_signals_at_data_end, 1)
        self.assertEqual((last.status, last.lifecycle_stages, last.reason_code), ("CENSORED_END_OF_DATA",
                                                                                  ["EVALUATED", "SIGNAL_GENERATED"], None))
        self.assertIsNone(last.backtest_trade_seq)
        self.assertFalse(DecisionEvent.objects.filter(run__run_id=rec2.run_id, status="EXECUTION_FAILED").exists())
        self.assertEqual(JournalRun.objects.get(run_id=rec2.run_id).status, "CLOSED")

    def test_fill_time_size_refusal_is_rejected_risk(self):
        zero = lambda bt: setattr(bt.position_sizer, "calculate", lambda **kw: 0.0)
        bt, res, rec = _run(_df(), patch=zero)
        _, res_off, _ = _run(_df(), journal=False, patch=zero)
        self.assertEqual(_canon(res), _canon(res_off))
        self.assertEqual(res["trades"], [])
        ref = DecisionEvent.objects.filter(run__run_id=rec.run_id, reason_code="SIZE_BELOW_MINIMUM")
        self.assertTrue(ref.exists())
        d = ref.first()
        self.assertEqual((d.status, d.reason_stage, d.raw_reason), ("REJECTED", "RISK", "size_not_positive"))
        self.assertEqual(d.lifecycle_stages, ["EVALUATED", "SIGNAL_GENERATED", "GATES_EVALUATED"])
        self.assertEqual((d.gate_vector["BT_FILL_SIGNAL_VALUE"], d.gate_vector["BT_FILL_SIZE_POSITIVE"],
                          d.gate_vector["BT_FILL_STOP_DISTANCE_NONZERO"]), ("PASS", "FAIL", "NOT_EVALUATED"))
        self.assertEqual(ref.count(), bt.signals_seen - bt.unfilled_signals_at_data_end)

    def test_unmapped_reason_makes_the_run_incomplete_and_is_never_classified(self):
        def patch(bt):
            orig = bt.router.route
            def route(df, rr):
                s = orig(df, rr)
                if s.get("signal") == "NO_SIGNAL" and s.get("reason") == "no_pullback":
                    s = dict(s, reason="brand_new_reason")
                return s
            bt.router.route = route
        bt, res, rec = _run(_df(), patch=patch)
        _, res_off, _ = _run(_df(), journal=False, patch=patch)
        self.assertEqual(_canon(res), _canon(res_off))
        run = JournalRun.objects.get(run_id=rec.run_id)
        self.assertEqual(run.status, "INCOMPLETE")
        self.assertIn("MAPPING_FAILURE", run.incomplete_reasons)
        gaps = JournalGap.objects.filter(run=run, kind="MAPPING_FAILURE")
        self.assertEqual(gaps.count(), bt.rejections["brand_new_reason"])
        self.assertTrue(all(g.raw_reason == "brand_new_reason" for g in gaps))
        self.assertFalse(DecisionEvent.objects.filter(run=run, raw_reason="brand_new_reason").exists())
        self.assertEqual(run.coverage["counts"]["EVALUATED_UNMAPPED"], gaps.count())

    def test_recorder_failure_cannot_change_results(self):
        class Exploding(BacktestDecisionRecorder):
            calls = 0
            def on_evaluation(self, *a):
                Exploding.calls += 1
                if Exploding.calls == 300:
                    raise RuntimeError("simulated journal failure")
                return super().on_evaluation(*a)
        with self.assertLogs("bot.journal.decision_recorder", level="ERROR"):
            bt, res, rec = _run(_df(), recorder_cls=Exploding)
        _, res_off, _ = _run(_df(), journal=False)
        self.assertEqual(_canon(res), _canon(res_off))
        run = JournalRun.objects.get(run_id=rec.run_id)
        self.assertEqual(run.status, "INCOMPLETE")
        self.assertTrue(JournalGap.objects.filter(run=run, kind="RECORDER_FAILURE").exists())

    def test_recorder_that_mutates_strategy_input_is_detected(self):
        """Negative test of the equivalence oracle itself: a recorder that touches a signal changes results,
        and the ON/OFF comparison catches it."""
        class Meddling(BacktestDecisionRecorder):
            def on_evaluation(self, i, current_df, regime_result, signal, disposition):
                if disposition == "CANDIDATE":
                    signal["sl"] = float(signal["sl"]) * 0.97
                return super().on_evaluation(i, current_df, regime_result, signal, disposition)
        _, res, _ = _run(_df(), recorder_cls=Meddling)
        _, res_off, _ = _run(_df(), journal=False)
        self.assertNotEqual(_canon(res), _canon(res_off))

    def test_empty_run(self):
        bt, res, rec = _run(_df(50))
        run = JournalRun.objects.get(run_id=rec.run_id)
        self.assertEqual((run.status, run.n_decisions, run.coverage["counts"]["PRE_LOOP"]), ("CLOSED", 0, 50))

    def test_recorder_refuses_internal_inconsistency(self):
        rec = BacktestDecisionRecorder("BTC_USDT")
        with self.assertRaises(RecorderDefect):
            rec.on_censored_end_of_data()


class SchemaTests(TestCase):

    def test_reconstructed_snapshot_cannot_masquerade_as_point_in_time(self):
        run = JournalRun.objects.create(run_id="T-RUN", environment="BACKTEST", execution_mode="RESEARCH", symbol="X",
                                        asset_class="CRYPTO", venue="binance", timeframe="1h",
                                        started_at=pd.Timestamp("2026-01-01", tz="UTC"), methodology_version="m",
                                        config_hash="c", data_version="d", zones_version="z", zones_hash="h",
                                        strategy_set="s", strategy_version="v", taxonomy_version=T.TAXONOMY_VERSION,
                                        schema_version="s", gate_order_version="g", feature_set_version="f",
                                        coverage_version="c", evidence_status="e")
        t0 = pd.Timestamp("2025-01-01 00:00", tz="UTC")
        d = DecisionEvent.objects.create(
            decision_id=uuid.uuid4(), run=run, environment="BACKTEST", execution_mode="RESEARCH", symbol="X",
            asset_class="CRYPTO", venue="binance", timeframe="1h", candle_index=100, candle_open_time=t0,
            information_time=t0 + HOUR, research_zone="VALIDATION", zones_hash="h", strategy="s", strategy_version="v",
            status="NO_SIGNAL", reason_code="NO_PULLBACK", reason_stage="STRATEGY", gate_order_version="g",
            methodology_version="m", config_hash="c", data_version="d", taxonomy_version="t")
        base = dict(decision=d, feature_set_version="f", information_time=t0 + HOUR, window_start_time=t0,
                    window_end_time=t0, window_rows=1, input_window_sha256="x")
        with self.assertRaises(IntegrityError), transaction.atomic():
            MarketSnapshot.objects.create(snapshot_kind="RECONSTRUCTED_POST_HOC", **base)
        with self.assertRaises(IntegrityError), transaction.atomic():           # a window that ends at/after the decision
            MarketSnapshot.objects.create(snapshot_kind="SIMULATED_CAUSAL", **dict(base, window_end_time=t0 + HOUR))
        with self.assertRaises(IntegrityError), transaction.atomic():           # decision timestamp before its candle closes
            DecisionEvent.objects.create(**{**{f.attname: getattr(d, f.attname) for f in DecisionEvent._meta.concrete_fields},
                                            "decision_id": uuid.uuid4(), "candle_index": 101,
                                            "candle_open_time": t0 + HOUR, "information_time": t0 + HOUR})
        with self.assertRaises(IntegrityError), transaction.atomic():           # EXECUTED without a trade link
            DecisionEvent.objects.create(**{**{f.attname: getattr(d, f.attname) for f in DecisionEvent._meta.concrete_fields},
                                            "decision_id": uuid.uuid4(), "candle_index": 102,
                                            "candle_open_time": t0 + 2 * HOUR, "information_time": t0 + 3 * HOUR,
                                            "status": "EXECUTED", "reason_code": None, "reason_stage": None})

    def test_legacy_trade_records_are_not_decisions(self):
        self.assertTrue(TradeRecord._meta.get_field("decision_id").null)
        self.assertFalse(DecisionEvent.objects.exists())

    def test_migration_is_additive(self):
        from importlib import import_module
        from django.db import migrations as m
        mig = import_module("bot.migrations.0016_decision_journal_phase1").Migration
        allowed = (m.AddField, m.CreateModel, m.AddConstraint)
        self.assertTrue(all(isinstance(op, allowed) for op in mig.operations), [type(o).__name__ for o in mig.operations])
        adds = [op for op in mig.operations if isinstance(op, m.AddField)]
        self.assertEqual([(op.model_name, op.name, op.field.null) for op in adds], [("traderecord", "decision_id", True)])
