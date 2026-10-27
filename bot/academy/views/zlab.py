# claude code changed: 2026-10-26 — new file: Z-Score Exploration Lab page (public, illustrative numbers only).
from django.shortcuts import render

from bot.academy import zlab


def zscore_lab(request):
    sample = zlab.parse_sample(request.GET["sample"]) if "sample" in request.GET else list(zlab.DEFAULT_SAMPLE)
    try:
        x = float(request.GET.get("x", zlab.DEFAULT_X))
    except ValueError:
        x = zlab.DEFAULT_X
    return render(request, "academy/zscore_lab.html", {
        "sample_text": ", ".join(f"{v:g}" for v in sample),
        "x": x,
        "result": zlab.compute(sample, x),
    })
