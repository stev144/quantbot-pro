# claude code changed: 2026-10-26 — new file. Z-Score Exploration Lab (Academy): the maths, used to render the page's
# starting state on the server (so it works without JavaScript) and as the reference the page's JavaScript must match.
# z = (x − mean) / sd with the SAMPLE standard deviation (n − 1), the same convention as the platform's z-scores.
# Illustrative numbers only — never market data.
import math
import re
from typing import Dict, List, Optional

DEFAULT_SAMPLE = [98.0, 101.0, 100.0, 103.0, 99.0, 102.0, 97.0, 100.0]   # mean 100, sample sd 2
DEFAULT_X = 104.0                                                        # → z = 2.0
MAX_POINTS = 50


def parse_sample(text: str) -> List[float]:
    """Numbers from free text ("98, 101 100;103"), finite only, at most MAX_POINTS."""
    out = []
    for tok in re.split(r"[\s,;]+", str(text or "").strip()):
        if not tok:
            continue
        try:
            v = float(tok)
        except ValueError:
            continue
        if math.isfinite(v):
            out.append(v)
    return out[:MAX_POINTS]


def describe(z: Optional[float]) -> str:
    if z is None:
        return ""
    a = abs(z)
    side = "above" if z > 0 else "below" if z < 0 else "at"
    if a == 0:
        return "exactly at the sample average"
    if a < 1:
        return f"{a:.2f} standard deviations {side} the average — ordinary for this sample"
    if a < 2:
        return f"{a:.2f} standard deviations {side} the average — noticeably away, but not rare"
    if a < 3:
        return f"{a:.2f} standard deviations {side} the average — unusual for this sample"
    return f"{a:.2f} standard deviations {side} the average — very unusual for this sample"


def compute(sample: List[float], x: float) -> Dict:
    n = len(sample)
    if n < 2:
        return {"n": n, "mean": None, "sd": None, "z": None, "reason": "Enter at least two numbers in the sample."}
    mean = sum(sample) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in sample) / (n - 1))
    if sd == 0:
        return {"n": n, "mean": mean, "sd": 0.0, "z": None,
                "reason": "Every number in the sample is the same, so the standard deviation is 0 and z is undefined."}
    z = (x - mean) / sd
    return {"n": n, "mean": mean, "sd": sd, "z": z, "reason": "", "interpretation": describe(z)}
