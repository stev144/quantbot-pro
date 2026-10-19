# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
# bot/tests/test_decision_journal_phase7.py
# claude code changed: new file — 2026-10-07, Decision Journal Phase 7 (statsmodels-logistic hypothesis discovery).
# Known answer (a planted ADX effect is found; pure noise yields no candidate), zone-S only, episode units, refusals,
# never-overwrite + provenance, and the import boundary: no strategy / engine / execution / live module can reach it.
# ═══════════════════════════════════════════════════════════════════════════════════════════════════════════
import ast
import tempfile
import uuid
from pathlib import Path

import numpy as np
import pandas as pd
from django.test import TestCase

from bot.journal import discovery as D
from bot.journal.models import DecisionEvent, DecisionLabel, JournalRun, MarketSnapshot


def build_run(n=420, *, effect=True, seed=3, zone_v_extra=0, environment="BACKTEST"):
    rng = np.random.default_rng(seed)
    run = JournalRun.objects.create(
        run_id=f"BT-SYN-{uuid.uuid4().hex[:8]}", environment=environment, execution_mode="RESEARCH", symbol="X",
        asset_class="CRYPTO", venue="binance", timeframe="1h", status="CLOSED", started_at=pd.Timestamp("2026-01-01", tz="UTC"),
        methodology_version="m", config_hash="c", data_version="d", zones_version="z", zones_hash="h",
        strategy_set="Synthetic", strategy_version="v", taxonomy_version="QBP-JRNL-REASON-1", schema_version="s",
        gate_order_version="g", feature_set_version="f", coverage_version="c", evidence_status="e", coverage={"intervals": []})
    t0 = pd.Timestamp("2022-01-01", tz="UTC")
    decs, snaps, labs = [], [], []
    for i in range(n + zone_v_extra):
        t = t0 + pd.Timedelta(hours=30 * i) if i < n else pd.Timestamp("2024-06-01", tz="UTC") + pd.Timedelta(hours=30 * i)
        zone = "SELECTION" if i < n else "VALIDATION"
        adx = float(rng.uniform(10, 50))
        y = (adx + rng.normal(0, 6) > 30) if effect else bool(rng.integers(0, 2))
        did = uuid.uuid4()
        decs.append(DecisionEvent(
            decision_id=did, run=run, environment=environment, execution_mode="RESEARCH", symbol="X", asset_class="CRYPTO",
            venue="binance", timeframe="1h", candle_index=i * 30, candle_open_time=t, information_time=t + pd.Timedelta(hours=1),
            research_zone=zone, zones_hash="h", strategy="s", strategy_version="v", direction="LONG" if i % 2 else "SHORT",
            status="REJECTED", reason_code="DIRECTION_CONFLICTS_REGIME", reason_stage="REGIME", gate_order_version="g",
            methodology_version="m", config_hash="c", data_version="d", taxonomy_version="t",
            lifecycle_stages=["EVALUATED", "SIGNAL_GENERATED", "GATES_EVALUATED"]))
        snaps.append(MarketSnapshot(decision_id=did, snapshot_kind="SIMULATED_CAUSAL", feature_set_version="f",
                                    information_time=t + pd.Timedelta(hours=1), window_start_time=t, window_end_time=t,
                                    window_rows=1, input_window_sha256="x",
                                    values={"regime_adx": adx, "regime_atr_ratio": float(rng.uniform(0.5, 2)),
                                            "regime_ema_spread_pct": float(rng.normal(0, 1)), "regime_bb_width_pct": float(rng.uniform(1, 5)),
                                            "regime_volatility_extreme": False}))
        labs.append(DecisionLabel(decision_id=did, run=run, outcome_kind="COUNTERFACTUAL_NOT_EXECUTED",
                                  label_type="COUNTERFACTUAL_RULE", label_version="L", variant="BASELINE_STOP_FIRST",
                                  horizon_candles=24, information_time=t + pd.Timedelta(hours=1),
                                  label_window_start=t + pd.Timedelta(hours=1), status="OK", research_zone=zone,
                                  net_r=1.0 if y else -1.0, gross_r=0.0, data_version="d"))
    DecisionEvent.objects.bulk_create(decs); MarketSnapshot.objects.bulk_create(snaps); DecisionLabel.objects.bulk_create(labs)
    return run


class DiscoveryTests(TestCase):

    def test_planted_effect_is_found_and_labelled_as_a_candidate_only(self):
        out = D.discover(build_run(), write=False)
        self.assertTrue(out["model_beats_base_rate_out_of_fold"])
        self.assertGreater(out["oof_auc"], 0.75)
        adx = [c for c in out["candidates"] if c["feature"] == "regime_adx"]
        self.assertEqual(len(adx), 1)
        self.assertEqual(adx[0]["sign"], "+")
        self.assertIn("not registered, not evidence", out["status"])

    def test_pure_noise_yields_no_candidate(self):
        out = D.discover(build_run(effect=False, seed=5), write=False)
        self.assertEqual(out["candidates"], [])

    def test_only_zone_s_episodes_are_used(self):
        out = D.discover(build_run(zone_v_extra=60), write=False)
        self.assertEqual(out["n_episodes"], 420)                            # the 60 zone-V decisions never enter

    def test_refusals(self):
        self.assertEqual(D.MIN_EPISODES, 200)
        with self.assertRaisesRegex(D.DiscoveryRefused, "too few for a model"):
            D.discover(build_run(n=150), write=False)                       # refused for the RIGHT reason (P7-4)
        with self.assertRaises(D.DiscoveryRefused):
            D.discover(build_run(environment="DRY_RUN"), write=False)       # live outcomes are sealed

    def test_output_has_provenance_and_is_never_overwritten(self):
        from bot.quant_core.provenance import read_provenance
        run = build_run()
        with tempfile.TemporaryDirectory() as d:
            out = D.discover(run, out_dir=Path(d))
            p = Path(out["output_path"])
            self.assertTrue(p.exists())
            prov = read_provenance(p)
            self.assertEqual(prov.kind, "JOURNAL_DISCOVERY")
            self.assertIn("never evidence", prov.extra["evidence_status"])
            with self.assertRaises(FileExistsError):
                open(p, "x").close()

    def test_import_boundary(self):
        """No strategy / engine / execution / risk / live / backtest module imports the journal's analytics or discovery,
        and discovery imports none of them — an ML output cannot reach trading code."""
        forbidden_targets = ("bot.journal.discovery", "bot.journal.analytics")
        for folder in ("bot/strategies", "bot/engines", "bot/core", "bot/risk", "bot/backtesting", "bot/pairs"):
            for f in Path(folder).rglob("*.py"):
                tree = ast.parse(f.read_text(encoding="utf-8"))
                mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
                mods |= {a.name for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
                self.assertFalse(mods & set(forbidden_targets), f"{f} imports {mods & set(forbidden_targets)}")
        tree = ast.parse(Path("bot/journal/discovery.py").read_text(encoding="utf-8"))
        mods = {n.module for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        for m in mods:
            self.assertFalse(m.startswith(("bot.strategies", "bot.engines", "bot.core", "bot.risk", "bot.pairs")), m)
        self.assertNotIn("bot.journal.annotations", mods)                 # human text is never a feature
        self.assertNotIn("sklearn", Path("bot/journal/discovery.py").read_text(encoding="utf-8"))
