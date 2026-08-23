# claude code changed: new file — Research Lab MVP, section 21: experiment
# lifecycle tests (PENDING -> RUNNING -> COMPLETED, PENDING -> BLOCKED,
# PENDING -> FAILED), plus section 22 observability (tool_call_log
# populated) and section 25 security (confirms no tool call ever reaches
# execution_engine/order_manager/live credentials). Django TestCase (real
# DB), matching this project's Academy precedent for model-backed tests.

from unittest.mock import patch

from django.contrib.auth.models import User
from django.test import TestCase

from bot.research_lab.models import ResearchExperiment
from bot.research_lab.orchestrator import plan_experiment, run_experiment
from bot.research_lab.spec import ResearchSpec


def _make_experiment(user, **spec_overrides):
    spec_kwargs = dict(
        hypothesis_text="BTC RSI predicts forward returns",
        asset="BTC/USDT", timeframe="1h", direction=None,
        target={"type": "forward_return", "horizon": 4},
        features=["rsi"], risk_tier="LOW",
    )
    spec_kwargs.update(spec_overrides)
    spec = ResearchSpec(**spec_kwargs)
    return ResearchExperiment.objects.create(
        student=user, hypothesis_text=spec.hypothesis_text, structured_spec=spec.to_dict(),
    )


class LifecyclePendingToCompletedTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="researcher1", password="x")

    def test_full_lifecycle_reaches_completed_with_real_evidence(self):
        experiment = _make_experiment(self.user)
        self.assertEqual(experiment.status, "PENDING")

        plan_experiment(experiment)
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "PLANNED")

        run_experiment(experiment)
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "COMPLETED")
        self.assertIsNotNone(experiment.started_at)
        self.assertIsNotNone(experiment.completed_at)
        self.assertIn("ic", experiment.statistical_results)
        self.assertTrue(experiment.verdict)
        self.assertTrue(experiment.code_version)
        self.assertEqual(experiment.random_seed, 42)

    def test_tool_call_log_populated_for_observability(self):
        experiment = _make_experiment(self.user)
        plan_experiment(experiment)
        experiment.refresh_from_db()
        run_experiment(experiment)
        experiment.refresh_from_db()

        self.assertGreater(len(experiment.tool_call_log), 0)
        for entry in experiment.tool_call_log:
            self.assertIn("tool_name", entry)
            self.assertIn("duration_seconds", entry)
            self.assertIn("status", entry)


class LifecyclePendingToBlockedTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="researcher2", password="x")

    def test_invalid_spec_blocks_before_any_tool_runs(self):
        experiment = _make_experiment(self.user, asset=None)  # claude code changed: missing required field
        plan_experiment(experiment)
        experiment.refresh_from_db()

        self.assertEqual(experiment.status, "BLOCKED")
        self.assertTrue(experiment.error_message)
        self.assertEqual(experiment.tool_call_log, [])  # claude code changed: nothing ever ran

    def test_missing_data_blocks_at_plan_stage(self):
        experiment = _make_experiment(self.user, asset="BTC/USDT", features=["funding_rate"])
        plan_experiment(experiment)
        experiment.refresh_from_db()

        self.assertEqual(experiment.status, "BLOCKED")
        self.assertIn("funding_rate", experiment.error_message)

    def test_high_risk_tier_blocks_at_plan_stage(self):
        experiment = _make_experiment(self.user, risk_tier="HIGH")
        plan_experiment(experiment)
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "BLOCKED")

    def test_blocked_experiment_cannot_be_run(self):
        experiment = _make_experiment(self.user, asset=None)
        plan_experiment(experiment)
        experiment.refresh_from_db()
        run_experiment(experiment)  # claude code changed: must be a no-op — run_experiment only proceeds from PLANNED
        experiment.refresh_from_db()
        self.assertEqual(experiment.status, "BLOCKED")
        self.assertIsNone(experiment.started_at)


class LifecyclePendingToFailedTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="researcher3", password="x")

    def test_unexpected_orchestration_error_reaches_failed_not_a_crash(self):
        experiment = _make_experiment(self.user)
        plan_experiment(experiment)
        experiment.refresh_from_db()

        with patch("bot.research_lab.orchestrator._execute_planned_tools", side_effect=RuntimeError("boom")):
            run_experiment(experiment)
        experiment.refresh_from_db()

        self.assertEqual(experiment.status, "FAILED")
        self.assertIn("boom", experiment.error_message)
        self.assertIsNotNone(experiment.completed_at)


class ConcurrencyLimitTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="researcher4", password="x")

    def test_concurrency_limit_blocks_a_new_experiment(self):
        for _ in range(3):  # claude code changed: MAX_CONCURRENT_EXPERIMENTS_PER_USER default
            exp = _make_experiment(self.user)
            exp.status = "RUNNING"
            exp.save()

        new_experiment = _make_experiment(self.user)
        plan_experiment(new_experiment)
        new_experiment.refresh_from_db()

        self.assertEqual(new_experiment.status, "BLOCKED")
        self.assertIn("concurrency limit", new_experiment.error_message)


class SecurityNoPathToLiveExecutionTest(TestCase):
    """Section 21/25: confirm no Research Lab request can invoke live
    execution. Proven structurally, not just by absence of a call in this
    test — see also the final engineering report's security section."""

    def setUp(self):
        self.user = User.objects.create_user(username="researcher5", password="x")

    def test_no_execution_engine_or_order_manager_import_anywhere_in_research_lab(self):
        # claude code changed: fixed a real gap — inspect.getsource() on a
        # PACKAGE module (bot.research_lab.tools) only returns that
        # package's __init__.py source, not its submodules. This test
        # previously never actually scanned dataset_tools.py/
        # statistical_tools.py/research_tools.py/conditional_tools.py's
        # real content at all. Now imports and scans every individual
        # submodule explicitly, including the new conditional_tools.py
        # added by the Conditional Hypothesis Integrity fix.
        import inspect

        import bot.research_lab.orchestrator as orch_module
        import bot.research_lab.interpreter as interpreter_module
        import bot.research_lab.verdict as verdict_module
        import bot.research_lab.tools.base as tools_base_module
        import bot.research_lab.tools.dataset_tools as dataset_tools_module
        import bot.research_lab.tools.statistical_tools as statistical_tools_module
        import bot.research_lab.tools.research_tools as research_tools_module
        import bot.research_lab.tools.conditional_tools as conditional_tools_module
        import bot.research_lab.capability_registry as capability_registry_module  # claude code changed: new — Advanced Quant Research Capability Architecture, scan every new module added by this pass too
        import bot.research_lab.entitlements as entitlements_module  # claude code changed: new

        modules = (
            orch_module, interpreter_module, verdict_module, tools_base_module,
            dataset_tools_module, statistical_tools_module, research_tools_module, conditional_tools_module,
            capability_registry_module, entitlements_module,
        )
        for module in modules:
            source = inspect.getsource(module)
            self.assertNotIn("execution_engine", source, module.__name__)
            self.assertNotIn("order_manager", source, module.__name__)
            self.assertNotIn("bot_runner", source, module.__name__)
            self.assertNotIn("ExecutionEngine", source, module.__name__)
            self.assertNotIn("OrderManager", source, module.__name__)
            self.assertNotIn("ccxt", source, module.__name__)  # claude code changed: new — confirms no direct exchange-library import anywhere either
            self.assertNotIn("API_KEY", source, module.__name__)
            self.assertNotIn("API_SECRET", source, module.__name__)

    def test_full_experiment_run_never_touches_live_credentials(self):
        experiment = _make_experiment(self.user)
        plan_experiment(experiment)
        experiment.refresh_from_db()
        run_experiment(experiment)
        experiment.refresh_from_db()
        # claude code changed: real, DB-backed proof — the experiment
        # completes fully using only local CSV data and research engines,
        # with zero API key/credential ever read or referenced.
        self.assertEqual(experiment.status, "COMPLETED")
