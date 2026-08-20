# ============================================================
# bot/management/commands/load_academy_content.py
# claude code changed: new file — loads bot/academy/curriculum_seed.py's
# data (+ the Markdown files it references under bot/academy/content/)
# into the database. Same convention as this project's other loader
# scripts (fetch_all_symbols.py, run_research_all.py): content lives in
# version-controlled files, this command is the one place that turns
# them into DB rows.
#
# Idempotent by design — safe to re-run after editing curriculum_seed.py
# or a content file. Every write is get_or_create/update_or_create keyed
# on a stable slug, never a blind create, so re-running never duplicates
# a LearningPath/Course/Module/Lesson/Quiz.
# ============================================================

from pathlib import Path

from django.core.management.base import BaseCommand

from bot.academy.curriculum_seed import LEARNING_PATHS, FULLY_AUTHORED_COURSE
from bot.academy.models import (
    LearningPath, Course, Module, Lesson, Question, Quiz, QuizQuestion,
)

CONTENT_DIR = Path(__file__).resolve().parent.parent.parent / "academy" / "content"


class Command(BaseCommand):
    help = "Load the Academy curriculum (bot/academy/curriculum_seed.py) into the database."

    def handle(self, *args, **options):
        path_count = course_count = 0

        for path_data in LEARNING_PATHS:
            path_obj, _ = LearningPath.objects.update_or_create(
                slug=path_data["slug"],
                defaults={
                    "title": path_data["title"],
                    "description": path_data["description"],
                    "order": path_data["order"],
                },
            )
            path_count += 1

            for order, (slug, title, difficulty, description) in enumerate(path_data["courses"], start=1):
                Course.objects.update_or_create(
                    slug=slug,
                    defaults={
                        "path": path_obj,
                        "title": title,
                        "difficulty": difficulty,
                        "description": description,
                        "order": order,
                    },
                )
                course_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Loaded {path_count} learning path(s), {course_count} course(s) (catalog)."
        ))

        self._load_fully_authored_course()

    def _load_fully_authored_course(self):
        spec = FULLY_AUTHORED_COURSE
        try:
            course = Course.objects.get(slug=spec["course_slug"])
        except Course.DoesNotExist:
            self.stderr.write(self.style.ERROR(
                f"FULLY_AUTHORED_COURSE references course slug "
                f"'{spec['course_slug']}' which wasn't found — check "
                f"curriculum_seed.py's path/course data matches."
            ))
            return

        course.is_published = True
        course.save(update_fields=["is_published"])

        lesson_count = 0
        for module_data in spec["modules"]:
            module_obj, _ = Module.objects.update_or_create(
                course=course,
                title=module_data["title"],
                defaults={"order": module_data["order"]},
            )

            for lesson_data in module_data["lessons"]:
                content_path = CONTENT_DIR / lesson_data["content_file"]
                if not content_path.exists():
                    self.stderr.write(self.style.ERROR(f"Missing content file: {content_path}"))
                    continue
                body = content_path.read_text(encoding="utf-8")

                Lesson.objects.update_or_create(
                    module=module_obj,
                    slug=lesson_data["slug"],
                    defaults={
                        "title": lesson_data["title"],
                        "body": body,
                        "order": lesson_data["order"],
                    },
                )
                lesson_count += 1

        quiz_data = spec["quiz"]
        quiz_obj, _ = Quiz.objects.update_or_create(
            course=course,
            title=quiz_data["title"],
            defaults={"passing_score": quiz_data["passing_score"]},
        )

        question_count = 0
        for order, q in enumerate(quiz_data["questions"], start=1):
            question_obj, _ = Question.objects.update_or_create(
                lesson=None,
                question_type=q["question_type"],
                prompt=q["prompt"],
                defaults={
                    "payload": q["payload"],
                    "explanation": q.get("explanation", ""),
                    "order": order,
                },
            )
            QuizQuestion.objects.update_or_create(
                quiz=quiz_obj, question=question_obj, defaults={"order": order},
            )
            question_count += 1

        self.stdout.write(self.style.SUCCESS(
            f"Fully authored course '{course.title}': {lesson_count} lesson(s), "
            f"{question_count} quiz question(s)."
        ))
