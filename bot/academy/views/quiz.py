# claude code changed: new file — quiz attempt view.

from django.contrib.auth.decorators import login_required
from django.http import Http404
from django.shortcuts import render, redirect

from bot.academy.views.quiz_data import get_quiz, student_can_access_quiz, submit_quiz_attempt


def _parse_answer(question, post_data):
    field = f"q{question.id}"
    if question.question_type in ("multiple_choice", "code_comprehension"):
        raw = post_data.get(field)
        return {"selected_index": int(raw)} if raw not in (None, "") else {}
    if question.question_type == "true_false":
        raw = post_data.get(field)
        return {"answer": raw == "true"} if raw is not None else {}
    if question.question_type == "numerical":
        raw = post_data.get(field)
        try:
            return {"value": float(raw)}
        except (TypeError, ValueError):
            return {}
    if question.question_type in ("research_interpretation", "research_decision"):
        raw = post_data.get(field)
        return {"verdict": raw} if raw else {}
    if question.question_type == "debugging":
        return {"selected_issues": post_data.getlist(field)}
    return {}


@login_required
def quiz_detail(request, quiz_id):
    quiz = get_quiz(quiz_id)
    if quiz is None:
        raise Http404("Quiz not found")
    if not student_can_access_quiz(request.user, quiz):
        raise Http404("Quiz not found")

    if request.method == "POST":
        questions = list(quiz.questions.all())
        answers = {str(q.id): _parse_answer(q, request.POST) for q in questions}
        attempt = submit_quiz_attempt(request.user, quiz, answers)
        return render(request, "academy/quiz_result.html", {"quiz": quiz, "attempt": attempt})

    return render(request, "academy/quiz.html", {"quiz": quiz, "questions": quiz.questions.all()})
