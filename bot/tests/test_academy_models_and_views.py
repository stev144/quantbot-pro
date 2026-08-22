# claude code changed: new file — DB-backed coverage for Academy
# enrollment, progress tracking, prerequisite enforcement, quiz scoring,
# and the full view-level flow (section 20's explicit test list). Uses
# Django TestCase (real Postgres test DB), unlike this file's siblings —
# consistent with this project's "no mocking" convention: real ORM
# operations, real Django test client requests, no mocked models.

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from bot.academy.models import (
    LearningPath, Course, Module, Lesson, Question, Quiz, QuizQuestion,
    Enrollment, LessonProgress, StudentProfile,
)
from bot.academy.views.courses_data import enroll_student, get_unmet_prerequisites, get_enrollment
from bot.academy.views.lessons_data import student_can_access_lesson, mark_lesson_complete
from bot.academy.views.quiz_data import submit_quiz_attempt


def _make_course(slug, path=None, prerequisites=None):
    path = path or LearningPath.objects.create(slug=f"{slug}-path", title=f"{slug} path", order=1)
    course = Course.objects.create(path=path, slug=slug, title=slug, is_published=True)
    if prerequisites:
        course.prerequisites.set(prerequisites)
    return course


class EnrollmentTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="student1", password="x")

    def test_enroll_with_no_prerequisites_succeeds(self):
        course = _make_course("intro")
        enrollment = enroll_student(self.user, course)
        self.assertIsNotNone(enrollment)
        self.assertEqual(enrollment.status, "active")
        self.assertEqual(enrollment.access_source, "free")

    def test_enroll_is_idempotent(self):
        course = _make_course("intro2")
        first = enroll_student(self.user, course)
        second = enroll_student(self.user, course)
        self.assertEqual(first.id, second.id)
        self.assertEqual(Enrollment.objects.filter(student=self.user, course=course).count(), 1)

    def test_enroll_blocked_by_unmet_prerequisite(self):
        prereq = _make_course("prereq-course")
        advanced = _make_course("advanced-course", prerequisites=[prereq])

        self.assertEqual(enroll_student(self.user, advanced), None)
        self.assertFalse(Enrollment.objects.filter(student=self.user, course=advanced).exists())

    def test_enroll_succeeds_once_prerequisite_completed(self):
        prereq = _make_course("prereq-course-2")
        advanced = _make_course("advanced-course-2", prerequisites=[prereq])

        Enrollment.objects.create(student=self.user, course=prereq, status="completed")

        enrollment = enroll_student(self.user, advanced)
        self.assertIsNotNone(enrollment)

    def test_get_unmet_prerequisites_empty_for_anonymous_shows_all(self):
        from django.contrib.auth.models import AnonymousUser
        prereq = _make_course("prereq-course-3")
        advanced = _make_course("advanced-course-3", prerequisites=[prereq])
        unmet = get_unmet_prerequisites(AnonymousUser(), advanced)
        self.assertEqual(list(unmet), [prereq])


class LessonProgressTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="student2", password="x")
        self.course = _make_course("progress-course")
        self.module = Module.objects.create(course=self.course, title="Module 1", order=1)
        self.lesson = Lesson.objects.create(module=self.module, slug="lesson-1", title="Lesson 1", order=1)

    def test_cannot_access_lesson_without_enrollment(self):
        self.assertFalse(student_can_access_lesson(self.user, self.lesson))

    def test_can_access_lesson_once_enrolled(self):
        enroll_student(self.user, self.course)
        self.assertTrue(student_can_access_lesson(self.user, self.lesson))

    def test_mark_lesson_complete_creates_progress(self):
        progress = mark_lesson_complete(self.user, self.lesson)
        self.assertEqual(progress.status, "completed")
        self.assertIsNotNone(progress.completed_at)

    def test_mark_lesson_complete_twice_does_not_duplicate(self):
        mark_lesson_complete(self.user, self.lesson)
        mark_lesson_complete(self.user, self.lesson)
        self.assertEqual(LessonProgress.objects.filter(student=self.user, lesson=self.lesson).count(), 1)


class QuizScoringTest(TestCase):

    def setUp(self):
        self.user = User.objects.create_user(username="student3", password="x")
        self.course = _make_course("quiz-course")
        self.quiz = Quiz.objects.create(course=self.course, title="Quiz", passing_score=70)

        self.q1 = Question.objects.create(
            question_type="multiple_choice", prompt="1+1?",
            payload={"choices": ["1", "2", "3"], "correct_index": 1},
        )
        self.q2 = Question.objects.create(
            question_type="true_false", prompt="Sky is blue?", payload={"correct": True},
        )
        QuizQuestion.objects.create(quiz=self.quiz, question=self.q1, order=1)
        QuizQuestion.objects.create(quiz=self.quiz, question=self.q2, order=2)

    def test_all_correct_scores_100_and_passes(self):
        attempt = submit_quiz_attempt(self.user, self.quiz, {
            str(self.q1.id): {"selected_index": 1},
            str(self.q2.id): {"answer": True},
        })
        self.assertEqual(attempt.score, 100.0)
        self.assertTrue(attempt.passed)

    def test_half_correct_scores_50_and_fails_at_70_threshold(self):
        attempt = submit_quiz_attempt(self.user, self.quiz, {
            str(self.q1.id): {"selected_index": 1},
            str(self.q2.id): {"answer": False},
        })
        self.assertEqual(attempt.score, 50.0)
        self.assertFalse(attempt.passed)

    def test_response_rows_saved_with_correctness(self):
        attempt = submit_quiz_attempt(self.user, self.quiz, {
            str(self.q1.id): {"selected_index": 0},
            str(self.q2.id): {"answer": True},
        })
        responses = {r.question_id: r.is_correct for r in attempt.responses.all()}
        self.assertFalse(responses[self.q1.id])
        self.assertTrue(responses[self.q2.id])


class AcademyViewFlowTest(TestCase):
    """End-to-end via the Django test client — real HTTP requests, real
    session/auth, no mocking."""

    def setUp(self):
        self.user = User.objects.create_user(username="student4", password="x")
        self.course = _make_course("view-flow-course")
        self.module = Module.objects.create(course=self.course, title="M1", order=1)
        self.lesson = Lesson.objects.create(module=self.module, slug="l1", title="L1", body="# Hello", order=1)

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("academy_dashboard"))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)

    def test_dashboard_loads_for_authenticated_user(self):
        self.client.login(username="student4", password="x")
        response = self.client.get(reverse("academy_dashboard"))
        self.assertEqual(response.status_code, 200)

    def test_course_detail_is_public(self):
        response = self.client.get(reverse("academy_course_detail", kwargs={"slug": self.course.slug}))
        self.assertEqual(response.status_code, 200)

    def test_enroll_then_lesson_becomes_accessible(self):
        self.client.login(username="student4", password="x")
        self.client.post(reverse("academy_enroll", kwargs={"slug": self.course.slug}))
        self.assertTrue(Enrollment.objects.filter(student=self.user, course=self.course).exists())

        response = self.client.get(reverse(
            "academy_lesson_detail",
            kwargs={"course_slug": self.course.slug, "lesson_slug": self.lesson.slug},
        ))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Hello", response.content)

    def test_lesson_blocked_without_enrollment_redirects_to_course(self):
        self.client.login(username="student4", password="x")
        response = self.client.get(reverse(
            "academy_lesson_detail",
            kwargs={"course_slug": self.course.slug, "lesson_slug": self.lesson.slug},
        ))
        self.assertEqual(response.status_code, 302)

    def test_complete_lesson_via_post(self):
        self.client.login(username="student4", password="x")
        enroll_student(self.user, self.course)
        self.client.post(reverse(
            "academy_complete_lesson",
            kwargs={"course_slug": self.course.slug, "lesson_slug": self.lesson.slug},
        ))
        self.assertTrue(
            LessonProgress.objects.filter(student=self.user, lesson=self.lesson, status="completed").exists()
        )

    def _answer_data_for(self, question):
        """Encodes an answer the same way onboarding_step.html's answer
        cards would (index-string for scored choice questions, raw string/
        list otherwise) — correct_index answers submitted correctly so
        quant_level/research_level land on "advanced" for assertions below."""
        if question["type"] == "boolean":
            return {"value": "false"}
        if question["type"] == "multi_choice":
            return {"value": [question["choices"][0]]}
        if "correct_index" in question:
            return {"value": str(question["correct_index"])}
        return {"value": question["choices"][0]}

    def test_full_diagnostic_walkthrough_creates_profile_and_reaches_result(self):
        from bot.academy.diagnostics import FLAT_DIAGNOSTIC_QUESTIONS

        self.client.login(username="student4", password="x")
        response = None
        for step, question in enumerate(FLAT_DIAGNOSTIC_QUESTIONS, start=1):
            response = self.client.post(
                reverse("academy_onboarding_step", kwargs={"step": step}),
                data=self._answer_data_for(question),
            )
            self.assertEqual(response.status_code, 302)

        self.assertEqual(response.url, reverse("academy_onboarding_result"))

        # Visiting the result page is what actually finalizes scoring
        # (finish_onboarding_if_ready) — the last step's redirect target
        # alone doesn't create the StudentProfile.
        result_response = self.client.get(reverse("academy_onboarding_result"))
        self.assertEqual(result_response.status_code, 200)

        profile = StudentProfile.objects.get(student=self.user)
        self.assertIsNotNone(profile.onboarding_completed_at)
        self.assertEqual(profile.quant_level, "advanced")  # all 4 quant answers correct
        self.assertEqual(profile.research_level, "advanced")  # all 4 research answers correct

    def test_answering_a_step_advances_to_the_next_one(self):
        from bot.academy.diagnostics import FLAT_DIAGNOSTIC_QUESTIONS

        self.client.login(username="student4", password="x")
        response = self.client.post(
            reverse("academy_onboarding_step", kwargs={"step": 1}),
            data=self._answer_data_for(FLAT_DIAGNOSTIC_QUESTIONS[0]),
        )
        self.assertRedirects(response, reverse("academy_onboarding_step", kwargs={"step": 2}))

    def test_empty_submission_re_renders_same_step_with_error(self):
        self.client.login(username="student4", password="x")
        response = self.client.post(reverse("academy_onboarding_step", kwargs={"step": 1}), data={})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Please choose an answer")

    def test_result_page_redirects_to_first_unanswered_step_if_incomplete(self):
        from bot.academy.diagnostics import FLAT_DIAGNOSTIC_QUESTIONS

        self.client.login(username="student4", password="x")
        self.client.post(
            reverse("academy_onboarding_step", kwargs={"step": 1}),
            data=self._answer_data_for(FLAT_DIAGNOSTIC_QUESTIONS[0]),
        )
        response = self.client.get(reverse("academy_onboarding_result"))
        self.assertRedirects(response, reverse("academy_onboarding_step", kwargs={"step": 2}))

    def test_onboarding_entry_point_resumes_at_first_unanswered_step(self):
        from bot.academy.diagnostics import FLAT_DIAGNOSTIC_QUESTIONS

        self.client.login(username="student4", password="x")
        self.client.post(
            reverse("academy_onboarding_step", kwargs={"step": 1}),
            data=self._answer_data_for(FLAT_DIAGNOSTIC_QUESTIONS[0]),
        )
        response = self.client.get(reverse("academy_onboarding"))
        self.assertRedirects(response, reverse("academy_onboarding_step", kwargs={"step": 2}))

    def test_onboarding_step_requires_login(self):
        response = self.client.get(reverse("academy_onboarding_step", kwargs={"step": 1}))
        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response.url)
