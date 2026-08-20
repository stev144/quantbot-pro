# claude code changed: new file — validates bot/academy/curriculum_seed.py's
# structural integrity directly against the data (no DB needed): every
# slug is unique, FULLY_AUTHORED_COURSE actually references a course that
# exists in LEARNING_PATHS, and every lesson content file it points to
# actually exists on disk. Catches a broken content reference before
# load_academy_content ever runs against a real database.

from pathlib import Path

from django.test import SimpleTestCase

from bot.academy.curriculum_seed import LEARNING_PATHS, FULLY_AUTHORED_COURSE

CONTENT_DIR = Path(__file__).resolve().parent.parent / "academy" / "content"


class CurriculumSeedIntegrityTest(SimpleTestCase):

    def test_path_slugs_are_unique(self):
        slugs = [p["slug"] for p in LEARNING_PATHS]
        self.assertEqual(len(slugs), len(set(slugs)))

    def test_course_slugs_are_globally_unique(self):
        # Course.slug is globally unique (models.py), not per-path — a
        # duplicate here would break the migration's unique constraint.
        all_slugs = []
        for path in LEARNING_PATHS:
            for slug, _title, _difficulty, _desc in path["courses"]:
                all_slugs.append(slug)
        self.assertEqual(len(all_slugs), len(set(all_slugs)), "duplicate course slug found")

    def test_every_course_tuple_has_four_fields(self):
        for path in LEARNING_PATHS:
            for course_tuple in path["courses"]:
                self.assertEqual(len(course_tuple), 4, f"malformed course tuple in {path['slug']}: {course_tuple}")

    def test_every_path_has_at_least_one_course(self):
        for path in LEARNING_PATHS:
            self.assertGreater(len(path["courses"]), 0, f"{path['slug']} has no courses")

    def test_nine_learning_paths_match_the_approved_curriculum(self):
        self.assertEqual(len(LEARNING_PATHS), 9)

    def test_fully_authored_course_path_reference_is_valid(self):
        path_slugs = {p["slug"] for p in LEARNING_PATHS}
        self.assertIn(FULLY_AUTHORED_COURSE["path_slug"], path_slugs)

    def test_fully_authored_course_slug_exists_in_its_path(self):
        path = next(p for p in LEARNING_PATHS if p["slug"] == FULLY_AUTHORED_COURSE["path_slug"])
        course_slugs = {c[0] for c in path["courses"]}
        self.assertIn(FULLY_AUTHORED_COURSE["course_slug"], course_slugs)

    def test_every_referenced_lesson_content_file_exists(self):
        for module in FULLY_AUTHORED_COURSE["modules"]:
            for lesson in module["lessons"]:
                content_path = CONTENT_DIR / lesson["content_file"]
                self.assertTrue(content_path.exists(), f"missing content file: {content_path}")

    def test_lesson_content_files_are_non_empty(self):
        for module in FULLY_AUTHORED_COURSE["modules"]:
            for lesson in module["lessons"]:
                content_path = CONTENT_DIR / lesson["content_file"]
                self.assertGreater(len(content_path.read_text(encoding="utf-8").strip()), 0)

    def test_quiz_questions_have_required_fields(self):
        for q in FULLY_AUTHORED_COURSE["quiz"]["questions"]:
            self.assertIn("question_type", q)
            self.assertIn("prompt", q)
            self.assertIn("payload", q)

    def test_multiple_choice_and_code_comprehension_questions_have_valid_correct_index(self):
        for q in FULLY_AUTHORED_COURSE["quiz"]["questions"]:
            if q["question_type"] in ("multiple_choice", "code_comprehension"):
                choices = q["payload"]["choices"]
                correct_index = q["payload"]["correct_index"]
                self.assertGreaterEqual(correct_index, 0)
                self.assertLess(correct_index, len(choices))
