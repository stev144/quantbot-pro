# claude code changed: new file — DB-independent coverage for Academy's
# question grading (Question.grade()), Markdown rendering/sanitization,
# and the diagnostic-scoring logic. All pure Python/in-memory model
# instances — no database required, consistent with this project's
# SimpleTestCase convention for logic that doesn't need persistence.

from django.test import SimpleTestCase

from bot.academy.content_render import render_lesson_body
from bot.academy.models import Question
from bot.academy import diagnostics


def _question(question_type, payload):
    # Unsaved instance — grade() is a pure method, needs no DB row.
    return Question(question_type=question_type, prompt="test", payload=payload)


class QuestionGradeTest(SimpleTestCase):

    def test_multiple_choice_correct(self):
        q = _question("multiple_choice", {"choices": ["a", "b", "c"], "correct_index": 1})
        self.assertTrue(q.grade({"selected_index": 1}))

    def test_multiple_choice_incorrect(self):
        q = _question("multiple_choice", {"choices": ["a", "b", "c"], "correct_index": 1})
        self.assertFalse(q.grade({"selected_index": 0}))

    def test_code_comprehension_uses_same_grading_as_multiple_choice(self):
        q = _question("code_comprehension", {"choices": ["a", "b"], "correct_index": 0})
        self.assertTrue(q.grade({"selected_index": 0}))
        self.assertFalse(q.grade({"selected_index": 1}))

    def test_true_false_correct(self):
        q = _question("true_false", {"correct": False})
        self.assertTrue(q.grade({"answer": False}))
        self.assertFalse(q.grade({"answer": True}))

    def test_numerical_within_tolerance(self):
        q = _question("numerical", {"correct_value": 10.0, "tolerance": 0.5})
        self.assertTrue(q.grade({"value": 10.3}))
        self.assertTrue(q.grade({"value": 9.6}))

    def test_numerical_outside_tolerance(self):
        q = _question("numerical", {"correct_value": 10.0, "tolerance": 0.5})
        self.assertFalse(q.grade({"value": 11.0}))

    def test_research_interpretation_correct(self):
        q = _question("research_interpretation", {"correct_verdict": "no"})
        self.assertTrue(q.grade({"verdict": "no"}))
        self.assertFalse(q.grade({"verdict": "yes"}))

    def test_research_decision_correct(self):
        q = _question("research_decision", {"correct_verdict": "REVIEW"})
        self.assertTrue(q.grade({"verdict": "REVIEW"}))
        self.assertFalse(q.grade({"verdict": "KEEP"}))

    def test_debugging_exact_set_match(self):
        q = _question("debugging", {"correct_issues": ["look_ahead_bias", "incorrect_indexing"]})
        self.assertTrue(q.grade({"selected_issues": ["look_ahead_bias", "incorrect_indexing"]}))
        self.assertTrue(q.grade({"selected_issues": ["incorrect_indexing", "look_ahead_bias"]}))  # order-independent

    def test_debugging_partial_selection_is_incorrect(self):
        q = _question("debugging", {"correct_issues": ["look_ahead_bias", "incorrect_indexing"]})
        self.assertFalse(q.grade({"selected_issues": ["look_ahead_bias"]}))

    def test_debugging_extra_selection_is_incorrect(self):
        q = _question("debugging", {"correct_issues": ["look_ahead_bias"]})
        self.assertFalse(q.grade({"selected_issues": ["look_ahead_bias", "data_snooping"]}))

    def test_malformed_response_grades_incorrect_not_raises(self):
        q = _question("numerical", {"correct_value": 10.0, "tolerance": 0.5})
        self.assertFalse(q.grade({}))  # missing "value" key
        self.assertFalse(q.grade({"value": "not-a-number"}))

    def test_unknown_question_type_grades_incorrect(self):
        q = _question("some_future_type", {})
        self.assertFalse(q.grade({"anything": "goes"}))


class ContentRenderTest(SimpleTestCase):

    def test_basic_markdown_renders(self):
        html = render_lesson_body("# Title\n\nSome **bold** text.")
        self.assertIn("<h1>Title</h1>", html)
        self.assertIn("<strong>bold</strong>", html)

    def test_script_tags_are_stripped(self):
        html = render_lesson_body("Hello\n\n<script>alert('xss')</script>")
        self.assertNotIn("<script>", html)
        self.assertNotIn("alert(", html)

    def test_tables_extension_works(self):
        md = "| A | B |\n|---|---|\n| 1 | 2 |\n"
        html = render_lesson_body(md)
        self.assertIn("<table>", html)

    def test_fenced_code_blocks_work(self):
        md = "```python\nx = 1\n```"
        html = render_lesson_body(md)
        self.assertIn("<pre>", html)

    def test_empty_body_does_not_raise(self):
        self.assertEqual(render_lesson_body(""), "")
        self.assertEqual(render_lesson_body(None), "")

    def test_disallowed_attributes_stripped(self):
        html = render_lesson_body('<a href="https://example.com" onclick="evil()">link</a>')
        self.assertNotIn("onclick", html)


class DiagnosticScoringTest(SimpleTestCase):

    def test_trading_experience_beginner_when_never_traded(self):
        level = diagnostics.score_trading_experience({"has_traded": {"value": False}})
        self.assertEqual(level, "beginner")

    def test_trading_experience_advanced_when_seasoned_and_automated(self):
        level = diagnostics.score_trading_experience({
            "has_traded": {"value": True},
            "years_experience": {"value": "3_plus_years"},
            "manual_or_automated": {"value": "automated"},
        })
        self.assertEqual(level, "advanced")

    def test_programming_beginner_by_default(self):
        self.assertEqual(diagnostics.score_programming({}), "beginner")

    def test_programming_advanced_when_built_a_bot(self):
        level = diagnostics.score_programming({
            "built_trading_bot": {"value": True},
            "used_apis": {"value": True},
            "used_pandas": {"value": True},
        })
        self.assertEqual(level, "advanced")

    def test_quantitative_knowledge_check_all_correct_is_advanced(self):
        questions = diagnostics.DIAGNOSTIC_QUESTIONS["quantitative"]
        responses = {q["key"]: {"selected_index": q["correct_index"]} for q in questions}
        self.assertEqual(diagnostics.score_quantitative(responses), "advanced")

    def test_quantitative_knowledge_check_all_wrong_is_beginner(self):
        questions = diagnostics.DIAGNOSTIC_QUESTIONS["quantitative"]
        responses = {q["key"]: {"selected_index": -1} for q in questions}
        self.assertEqual(diagnostics.score_quantitative(responses), "beginner")

    def test_research_knowledge_check_scores_by_correctness_not_self_report(self):
        # Direct proof this is a real knowledge check, not a self-assessment:
        # answering everything "wrong" on purpose still scores beginner.
        questions = diagnostics.DIAGNOSTIC_QUESTIONS["research"]
        wrong = {q["key"]: {"selected_index": (q["correct_index"] + 1) % len(q["choices"])} for q in questions}
        self.assertEqual(diagnostics.score_research(wrong), "beginner")

    def test_build_profile_scores_returns_all_four_fields(self):
        scores = diagnostics.build_profile_scores({})
        self.assertEqual(
            set(scores.keys()),
            {"trading_experience_level", "programming_level", "quant_level", "research_level"},
        )

    def test_recommend_starting_path_beginner_goes_to_foundations(self):
        scores = {"programming_level": "beginner", "quant_level": "beginner"}
        self.assertEqual(diagnostics.recommend_starting_path_slug(scores), "quant-foundations")

    def test_recommend_starting_path_quant_beginner_takes_priority_over_programming(self):
        # Quant fundamentals are needed regardless of coding skill, so a
        # quant-beginner routes to Quant Foundations even if their
        # programming level is already advanced.
        scores = {"programming_level": "advanced", "quant_level": "beginner"}
        self.assertEqual(diagnostics.recommend_starting_path_slug(scores), "quant-foundations")

    def test_recommend_starting_path_knows_quant_not_python_goes_to_python_course(self):
        scores = {"programming_level": "beginner", "quant_level": "advanced"}
        self.assertEqual(diagnostics.recommend_starting_path_slug(scores), "python-for-quant-trading")

    def test_recommend_starting_path_advanced_goes_to_research(self):
        scores = {"programming_level": "advanced", "quant_level": "advanced"}
        self.assertEqual(diagnostics.recommend_starting_path_slug(scores), "quantitative-research")
