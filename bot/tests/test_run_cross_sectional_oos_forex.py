# claude code changed: new file — Forex Integration Stage 3 groundwork.
# Covers bot/research/run_cross_sectional_oos.py's new asset_class
# parameter — Stage 1's own audit named this file (the real Type C OOS
# orchestrator, the actual entry point for every cross-sectional
# experiment) as the top Stage 3 priority: it only ever reached the
# crypto-only engine, with cost_rate/fingerprint-provenance/
# min_train_periods all silently crypto-shaped regardless of what was
# actually being tested.
#
# Real, not mocked (this project's own convention) — but deliberately
# minimal (n_permutations=2, top_k_values=(1,), checkpoint=False) since
# the full pipeline is genuinely expensive (a real walk-forward +
# permutation sweep). These are slow tests (Forex ~28 symbols: ~1-2 min;
# Crypto ~100 symbols: several minutes) by nature of testing a real
# research pipeline end to end, not a design flaw.

import os

from django.test import SimpleTestCase

from bot.instruments import ASSET_CLASS_CRYPTO, ASSET_CLASS_FOREX
from bot.research.run_cross_sectional_oos import run_cross_sectional_research


def _tmp_verdict_path(name):
    return os.path.join(os.environ.get("TEMP", "/tmp"), f"test_stage3_verdict_{name}.json")


class AssetClassDispatchTest(SimpleTestCase):

    def test_invalid_asset_class_fails_closed_before_any_computation(self):
        # claude code changed: cheap — this check happens before any real
        # data is loaded, so this test must return near-instantly, not
        # run the expensive pipeline.
        with self.assertRaises(ValueError):
            run_cross_sectional_research(
                hypothesis_name="cross_sectional_zscore",
                asset_class="NOT_A_REAL_ASSET_CLASS",
            )

    def test_forex_run_uses_real_forex_provenance_and_cost(self):
        result = run_cross_sectional_research(
            hypothesis_name="cross_sectional_zscore",
            asset_class=ASSET_CLASS_FOREX,
            top_k_values=(1,), n_permutations=2, checkpoint=False,
            verdict_output_path=_tmp_verdict_path("forex_dispatch"),
        )
        self.assertEqual(result["asset_class"], "FOREX")
        self.assertGreaterEqual(result["n_assets"], 8)  # the full registered Forex universe, never a crypto-scoped count
        # claude code changed: real Forex round-trip cost is roughly
        # 1-3 bps (confirmed empirically against bot.config.cost_model.
        # ForexCostModel) — an order of magnitude below crypto's 15bps
        # default. Asserting a real, evidence-based range rather than an
        # exact literal, since it's computed live from whatever real
        # instruments are in the panel.
        self.assertGreater(result["cost_rate"], 0.0)
        self.assertLess(result["cost_rate"], 0.001)  # << crypto's 0.0015

    def test_crypto_default_cost_rate_is_byte_identical_to_before(self):
        # claude code changed: the one real regression guard that
        # matters most here — every existing crypto caller of this
        # function must see EXACTLY the same cost_rate as before this
        # asset_class parameter was added.
        result = run_cross_sectional_research(
            hypothesis_name="cross_sectional_zscore",
            top_k_values=(1,), n_permutations=2, checkpoint=False,
            verdict_output_path=_tmp_verdict_path("crypto_dispatch"),
        )
        self.assertEqual(result["asset_class"], "CRYPTO")
        self.assertEqual(result["cost_rate"], 0.0015)


class CollisionNamespacingTest(SimpleTestCase):
    """claude code changed: new — real bug found and fixed alongside the
    main asset_class wiring: checkpoint_dir and the default
    verdict_output_path were namespaced by hypothesis_name (and
    rebalance_frequency, for checkpoints) but NOT by asset_class, so
    testing the same hypothesis_name for both CRYPTO and FOREX would
    have silently collided — FOREX loading CRYPTO's cached checkpoint,
    or overwriting CRYPTO's verdict file. Checked via source inspection
    (fast) rather than two real expensive runs — the property being
    tested is "does the code path include asset_class in the computed
    path," not the pipeline's numerical output."""

    def test_checkpoint_dir_is_namespaced_by_asset_class(self):
        import inspect
        source = inspect.getsource(run_cross_sectional_research)
        checkpoint_line = next(l for l in source.splitlines() if "cross_sectional_checkpoints" in l)
        self.assertIn("asset_class", checkpoint_line)

    def test_default_verdict_path_is_namespaced_by_asset_class(self):
        import inspect
        source = inspect.getsource(run_cross_sectional_research)
        self.assertIn("asset_class.lower()", source)
        self.assertIn("verdict_output_path is None", source)
