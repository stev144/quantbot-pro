# claude code changed: new file — Django admin registration for Academy
# content models. Two real purposes, not decorative: (1) lets an operator
# author/edit courses, modules, lessons, and questions through the admin
# UI without writing a management command for every edit; (2) Django's
# admin.autodiscover() (which runs automatically because
# 'django.contrib.admin' is in INSTALLED_APPS) is what actually IMPORTS
# this module during django.setup() — that import is what makes Django's
# app registry aware bot/academy/models.py exists at all, the same way
# bot/journal/models.py's TradeRecord only becomes visible to
# manage.py makemigrations because something in the urls->views import
# chain happens to import it. Registering here is the explicit,
# intentional version of that mechanism rather than an accidental one.

from django.contrib import admin

from bot.academy.models import (
    LearningPath,
    Course,
    Module,
    Lesson,
    Question,
    Quiz,
    QuizQuestion,
    StudentProfile,
    DiagnosticResponse,
    Enrollment,
    LessonProgress,
    QuizAttempt,
    QuestionResponse,
)


@admin.register(LearningPath)
class LearningPathAdmin(admin.ModelAdmin):
    list_display = ("title", "slug", "order")
    ordering = ("order",)


class ModuleInline(admin.TabularInline):
    model = Module
    extra = 0


@admin.register(Course)
class CourseAdmin(admin.ModelAdmin):
    list_display = ("title", "path", "difficulty", "is_published", "order")
    list_filter = ("path", "difficulty", "is_published")
    filter_horizontal = ("prerequisites",)
    inlines = [ModuleInline]


class LessonInline(admin.TabularInline):
    model = Lesson
    extra = 0


@admin.register(Module)
class ModuleAdmin(admin.ModelAdmin):
    list_display = ("title", "course", "order")
    list_filter = ("course",)
    inlines = [LessonInline]


@admin.register(Lesson)
class LessonAdmin(admin.ModelAdmin):
    list_display = ("title", "module", "order")
    list_filter = ("module__course",)


@admin.register(Question)
class QuestionAdmin(admin.ModelAdmin):
    list_display = ("__str__", "question_type", "lesson")
    list_filter = ("question_type",)


@admin.register(Quiz)
class QuizAdmin(admin.ModelAdmin):
    list_display = ("title", "lesson", "course", "passing_score")


admin.site.register(QuizQuestion)
admin.site.register(StudentProfile)
admin.site.register(DiagnosticResponse)
admin.site.register(Enrollment)
admin.site.register(LessonProgress)
admin.site.register(QuizAttempt)
admin.site.register(QuestionResponse)
