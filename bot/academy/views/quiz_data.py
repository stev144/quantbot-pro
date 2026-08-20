# claude code changed: new file — quiz attempt + scoring data layer.

from django.utils import timezone

from bot.academy.models import Quiz, QuizAttempt, QuestionResponse, Enrollment


def get_quiz(quiz_id):
    return Quiz.objects.prefetch_related("questions").filter(id=quiz_id).first()


def get_quiz_course(quiz):
    return quiz.course or (quiz.lesson.module.course if quiz.lesson else None)


def student_can_access_quiz(user, quiz):
    if not user.is_authenticated:
        return False
    course = get_quiz_course(quiz)
    if course is None:
        return False
    return Enrollment.objects.filter(student=user, course=course, status="active").exists()


def submit_quiz_attempt(user, quiz, answers_by_question_id):
    """
    Real, question-type-aware grading via Question.grade() — every
    response is actually checked, not assumed correct. Returns the saved
    QuizAttempt.
    """
    attempt = QuizAttempt.objects.create(student=user, quiz=quiz)

    questions = list(quiz.questions.all())
    correct_count = 0
    for question in questions:
        response_payload = answers_by_question_id.get(str(question.id), {})
        is_correct = question.grade(response_payload)
        if is_correct:
            correct_count += 1

        QuestionResponse.objects.create(
            attempt=attempt, question=question,
            response_payload=response_payload, is_correct=is_correct,
        )

    score = round((correct_count / len(questions)) * 100, 1) if questions else 0.0
    attempt.score = score
    attempt.passed = score >= quiz.passing_score
    attempt.completed_at = timezone.now()
    attempt.save(update_fields=["score", "passed", "completed_at"])

    return attempt
