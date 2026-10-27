# claude code changed: 2026-10-26 — new file: Academy redesign (dashboard, journey, paths page, enrolment, lesson page,
# quiz page, Explainer pre-fill). Real views through the test client; no mocking.
from pathlib import Path

from django.conf import settings
from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from bot.academy.models import Course, Enrollment, LearningPath, Lesson, LessonProgress, Module, Quiz, StudentProfile
from bot.academy.views.dashboard_data import get_student_dashboard


def _course(path, slug, published=True, n_lessons=4):
    c = Course.objects.create(path=path, slug=slug, title=slug.title(), is_published=published)
    m = Module.objects.create(course=c, title="M1", order=1)
    for i in range(n_lessons):
        Lesson.objects.create(module=m, slug=f"l{i + 1}", title=f"Lesson {i + 1}", body=f"# L{i + 1}\n\nBody {i + 1}", order=i + 1)
    return c


class AcademyRedesignTest(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user("student1", password="pw-12345")
        self.client.login(username="student1", password="pw-12345")
        self.path = LearningPath.objects.create(slug="strategy-engineering", title="Strategy Engineering", order=4)

    def test_stat_tile_styles_are_defined(self):
        css = (Path(settings.BASE_DIR) / "static" / "css" / "terminal.css").read_text(encoding="utf-8")
        for cls in (".term-stat-row", ".term-stat ", ".term-stat-label", ".term-stat-value"):
            self.assertIn(cls, css)

    def test_new_student_sees_honest_empty_states(self):
        r = self.client.get(reverse("academy_dashboard"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Welcome, student1")
        self.assertContains(r, "Enroll in a course to start.")
        self.assertContains(r, "Not yet available")              # progress and average, never an invented 0%
        self.assertNotContains(r, "Lessons Completed0")

    def test_progress_counts_only_enrolled_lessons(self):
        enrolled = _course(self.path, "cointegration")
        other = _course(self.path, "momentum")
        Enrollment.objects.create(student=self.user, course=enrolled, status="active")
        first = enrolled.modules.first().lessons.order_by("order").first()
        LessonProgress.objects.create(student=self.user, lesson=first, status="completed")
        for l in Lesson.objects.filter(module__course=other):   # progress in a course the student is not enrolled in
            LessonProgress.objects.create(student=self.user, lesson=l, status="completed")
        d = get_student_dashboard(self.user)
        self.assertEqual((d["completed_lessons"], d["total_lessons_enrolled"], d["progress_pct"]), (1, 4, 25))
        self.assertEqual(d["next_lesson"].slug, "l2")
        r = self.client.get(reverse("academy_dashboard"))
        self.assertContains(r, "Welcome back, student1")
        self.assertContains(r, "Continue learning")

    def test_journey_counts_published_and_planned(self):
        _course(self.path, "cointegration", published=True)
        _course(self.path, "momentum", published=False, n_lessons=0)
        stage = next(s for s in get_student_dashboard(self.user)["journey"] if s["key"] == "strategies")
        self.assertEqual((stage["n_available"], stage["n_planned"]), (1, 1))
        self.assertEqual(stage["first_course"].slug, "cointegration")
        maths = next(s for s in get_student_dashboard(self.user)["journey"] if s["key"] == "mathematics")
        self.assertEqual((maths["n_available"], maths["n_planned"], maths["first_course"]), (0, 0, None))

    def test_paths_page_survives_a_nameless_path(self):
        LearningPath.objects.create(slug="", title="", order=0)   # the row found in the real database
        _course(self.path, "momentum", published=False, n_lessons=0)
        r = self.client.get(reverse("academy_path_list"))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "1 planned")

    def test_planned_course_cannot_be_joined(self):
        planned = _course(self.path, "momentum", published=False, n_lessons=0)
        r = self.client.post(reverse("academy_enroll", kwargs={"slug": planned.slug}))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(Enrollment.objects.filter(student=self.user, course=planned).exists())
        self.assertContains(self.client.get(reverse("academy_course_detail", kwargs={"slug": planned.slug})), "Planned")

    def test_lesson_page_outline_and_completion_rules(self):
        c = _course(self.path, "cointegration")
        Enrollment.objects.create(student=self.user, course=c, status="active")
        url = reverse("academy_lesson_detail", kwargs={"course_slug": "cointegration", "lesson_slug": "l2"})
        r = self.client.get(url)
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Lesson 2 of 4")
        self.assertContains(r, "Ask the tutor about this lesson")       # cointegration course → tutor concept
        self.assertContains(r, "Mark complete & continue")
        self.assertFalse(LessonProgress.objects.filter(student=self.user).exists())   # opening is not completing
        self.client.post(reverse("academy_complete_lesson", kwargs={"course_slug": "cointegration", "lesson_slug": "l2"}))
        done = LessonProgress.objects.get(student=self.user, lesson__slug="l2")
        r = self.client.get(url)
        self.assertContains(r, "✓ Completed")
        self.assertNotContains(r, "Mark complete")                        # a link now: the date is never overwritten
        self.assertEqual(LessonProgress.objects.get(pk=done.pk).completed_at, done.completed_at)

    def test_quiz_page_has_instructions_and_counter(self):
        from bot.academy.models import Question, QuizQuestion
        c = _course(self.path, "cointegration")
        Enrollment.objects.create(student=self.user, course=c, status="active")
        quiz = Quiz.objects.create(course=c, title="Q", passing_score=70)
        q = Question.objects.create(question_type="true_false", prompt="Is 2 > 1?", payload={"correct": True})
        QuizQuestion.objects.create(quiz=quiz, question=q, order=1)
        r = self.client.get(reverse("academy_quiz_detail", kwargs={"quiz_id": quiz.id}))
        self.assertEqual(r.status_code, 200)
        self.assertContains(r, "Question 1 of 1")
        self.assertContains(r, "pass mark 70%")

    def test_explainer_prefill_is_not_sent(self):
        r = self.client.get(reverse("academy_explainer"), {"concept": "spread", "mode": "new", "q": "Explain spreads simply"})
        self.assertContains(r, "Explain spreads simply</textarea>")
        self.assertContains(r, "Before this, make sure you know")
        from bot.academy.models import ExplainerRequest
        self.assertFalse(ExplainerRequest.objects.exists())               # a GET never calls the AI
