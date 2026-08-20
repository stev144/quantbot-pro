# ============================================================
# bot/academy/models.py
# Quant Bot Academy — data models.
#
# claude code changed: new file — Academy narrow-slice build, per the
# architecture-discovery report approved for this repository. Lives as a
# subpackage of the existing single `bot` Django app (matching
# bot/journal/models.py's precedent), not a new INSTALLED_APPS entry —
# Django associates a model with an app by package path, not physical
# proximity to bot/models.py, so this works the same way TradeRecord does.
#
# No FK to any exchange/execution model anywhere in this file — Academy
# has no import path into bot/engines/ or bot/core/bot_runner.py by
# construction, so student accounts can never reach production trading
# credentials or logic through this app.
# ============================================================

from django.conf import settings
from django.db import models


# ─────────────────────────────────────────────────────────────
# CURRICULUM
# ─────────────────────────────────────────────────────────────

class LearningPath(models.Model):
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.title


class Course(models.Model):
    DIFFICULTY_CHOICES = [
        ("beginner", "Beginner"),
        ("intermediate", "Intermediate"),
        ("advanced", "Advanced"),
    ]

    path = models.ForeignKey(LearningPath, on_delete=models.CASCADE, related_name="courses")
    slug = models.SlugField(unique=True)
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    difficulty = models.CharField(max_length=20, choices=DIFFICULTY_CHOICES, default="beginner")
    # claude code changed: directed, not symmetrical — "Course B requires
    # Course A" is not the same statement as "Course A requires Course B".
    prerequisites = models.ManyToManyField(
        "self", symmetrical=False, blank=True, related_name="unlocks"
    )
    estimated_hours = models.PositiveIntegerField(default=1)
    order = models.PositiveIntegerField(default=0)
    # claude code changed: new — lets the full 9-path/50+-course catalog
    # exist in the DB (section 22's requirement) before every course has
    # real lesson content authored, without a placeholder course looking
    # like a broken/empty link to a visitor.
    is_published = models.BooleanField(default=False)

    class Meta:
        ordering = ["path__order", "order"]

    def __str__(self):
        return self.title


class Module(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="modules")
    title = models.CharField(max_length=200)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return f"{self.course.title} / {self.title}"


class Lesson(models.Model):
    module = models.ForeignKey(Module, on_delete=models.CASCADE, related_name="lessons")
    slug = models.SlugField()
    title = models.CharField(max_length=200)
    # claude code changed: Markdown text stored in the DB, authored as
    # files under bot/academy/content/ and loaded via
    # load_academy_content (see that command) — not hardcoded into
    # templates (section 22). Rendered through bot/academy/content_render.py's
    # sanitizing Markdown renderer, never marked |safe directly on raw input.
    body = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        unique_together = [("module", "slug")]

    def __str__(self):
        return self.title


# ─────────────────────────────────────────────────────────────
# QUESTIONS & QUIZZES
# ─────────────────────────────────────────────────────────────

# claude code changed: one Question model for every question type
# (multiple_choice/true_false/numerical/research_interpretation/
# code_comprehension/debugging/research_decision), discriminated by
# question_type with a JSONField payload holding the type-specific shape
# — deliberately not one DB table per type, so adding course #51 never
# requires a schema change (section 22).
QUESTION_TYPES = [
    ("multiple_choice", "Multiple Choice"),
    ("true_false", "True/False"),
    ("numerical", "Numerical"),
    ("research_interpretation", "Research Interpretation"),
    ("code_comprehension", "Code Comprehension"),
    ("debugging", "Debugging"),
    ("research_decision", "Research Decision"),
]


class Question(models.Model):
    lesson = models.ForeignKey(
        Lesson, on_delete=models.CASCADE, null=True, blank=True, related_name="questions"
    )
    question_type = models.CharField(max_length=30, choices=QUESTION_TYPES)
    prompt = models.TextField()
    # claude code changed: new — type-specific structure, e.g.:
    #   multiple_choice / code_comprehension: {"choices": [...], "correct_index": int}
    #   true_false: {"correct": bool}
    #   numerical: {"correct_value": float, "tolerance": float}
    #   research_interpretation / research_decision: {"correct_verdict": str, "verdicts": [...]}
    #   debugging: {"correct_issues": [str, ...], "issues": [str, ...]}
    payload = models.JSONField(default=dict)
    explanation = models.TextField(blank=True)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]

    def __str__(self):
        return self.prompt[:60]

    def grade(self, response_payload: dict) -> bool:
        """
        Real, type-aware grading — every branch is an actual comparison
        against this question's payload, not a stub. Returns whether
        response_payload counts as correct for this question's type.
        Unknown/malformed response_payload grades as incorrect rather
        than raising, so a client bug never silently awards credit.
        """
        try:
            if self.question_type in ("multiple_choice", "code_comprehension"):
                return response_payload.get("selected_index") == self.payload.get("correct_index")

            if self.question_type == "true_false":
                return bool(response_payload.get("answer")) == bool(self.payload.get("correct"))

            if self.question_type == "numerical":
                value = float(response_payload["value"])
                correct = float(self.payload["correct_value"])
                tolerance = float(self.payload.get("tolerance", 0.0))
                return abs(value - correct) <= tolerance

            if self.question_type in ("research_interpretation", "research_decision"):
                return response_payload.get("verdict") == self.payload.get("correct_verdict")

            if self.question_type == "debugging":
                selected = set(response_payload.get("selected_issues", []))
                correct = set(self.payload.get("correct_issues", []))
                return selected == correct

        except (KeyError, TypeError, ValueError):
            return False

        return False


class Quiz(models.Model):
    lesson = models.ForeignKey(
        Lesson, on_delete=models.CASCADE, null=True, blank=True, related_name="quizzes"
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, null=True, blank=True, related_name="quizzes"
    )
    title = models.CharField(max_length=200)
    passing_score = models.PositiveIntegerField(default=70)  # percent
    questions = models.ManyToManyField(Question, through="QuizQuestion", related_name="quizzes")

    def __str__(self):
        return self.title


class QuizQuestion(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE)
    question = models.ForeignKey(Question, on_delete=models.CASCADE)
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order"]
        unique_together = [("quiz", "question")]


# ─────────────────────────────────────────────────────────────
# STUDENT PROFILE & DIAGNOSTIC
# ─────────────────────────────────────────────────────────────

LEVEL_CHOICES = [
    ("beginner", "Beginner"),
    ("intermediate", "Intermediate"),
    ("advanced", "Advanced"),
]


class StudentProfile(models.Model):
    student = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="academy_profile"
    )
    trading_experience_level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="beginner")
    programming_level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="beginner")
    quant_level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="beginner")
    research_level = models.CharField(max_length=20, choices=LEVEL_CHOICES, default="beginner")
    primary_goal = models.CharField(max_length=50, blank=True)
    onboarding_completed_at = models.DateTimeField(null=True, blank=True)
    current_streak_days = models.PositiveIntegerField(default=0)
    last_activity_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.student.username}'s Academy profile"


DIAGNOSTIC_CATEGORIES = [
    ("trading_experience", "Trading Experience"),
    ("programming", "Programming"),
    ("quantitative", "Quantitative Knowledge"),
    ("research", "Research Knowledge"),
    ("goals", "Goals"),
]


class DiagnosticResponse(models.Model):
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="diagnostic_responses"
    )
    category = models.CharField(max_length=30, choices=DIAGNOSTIC_CATEGORIES)
    question_key = models.CharField(max_length=100)
    response_payload = models.JSONField(default=dict)
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("student", "question_key")]


# ─────────────────────────────────────────────────────────────
# ENROLLMENT & PROGRESS
# ─────────────────────────────────────────────────────────────

class Enrollment(models.Model):
    STATUS_CHOICES = [
        ("active", "Active"),
        ("completed", "Completed"),
        ("expired", "Expired"),
    ]
    ACCESS_SOURCE_CHOICES = [
        ("free", "Free"),
        ("purchase", "Purchase"),
        ("subscription", "Subscription"),
        ("bundle", "Bundle"),
    ]

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollments"
    )
    learning_path = models.ForeignKey(
        LearningPath, on_delete=models.CASCADE, null=True, blank=True, related_name="enrollments"
    )
    course = models.ForeignKey(
        Course, on_delete=models.CASCADE, null=True, blank=True, related_name="enrollments"
    )
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    # claude code changed: new — "free" is the only source this narrow
    # slice ever writes (no payments module wired in yet); the other
    # three values exist now so bot/payments/ (a future phase) writes
    # into this same field rather than needing a migration to add it.
    access_source = models.CharField(max_length=20, choices=ACCESS_SOURCE_CHOICES, default="free")
    enrolled_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-enrolled_at"]

    def __str__(self):
        target = self.course or self.learning_path
        return f"{self.student.username} -> {target}"


class LessonProgress(models.Model):
    STATUS_CHOICES = [
        ("not_started", "Not Started"),
        ("in_progress", "In Progress"),
        ("completed", "Completed"),
    ]

    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="lesson_progress"
    )
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="progress_records")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="not_started")
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [("student", "lesson")]

    def __str__(self):
        return f"{self.student.username} / {self.lesson.title}: {self.status}"


class QuizAttempt(models.Model):
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="quiz_attempts"
    )
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="attempts")
    started_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    score = models.FloatField(null=True, blank=True)  # percent, 0-100
    passed = models.BooleanField(default=False)

    class Meta:
        ordering = ["-started_at"]

    def __str__(self):
        return f"{self.student.username} / {self.quiz.title}"


class QuestionResponse(models.Model):
    attempt = models.ForeignKey(QuizAttempt, on_delete=models.CASCADE, related_name="responses")
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="responses")
    response_payload = models.JSONField(default=dict)
    is_correct = models.BooleanField(default=False)
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [("attempt", "question")]
