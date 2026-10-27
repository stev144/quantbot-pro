# Steph Quant Academy — Curriculum Roadmap (2026-10-26)

*claude code changed: 2026-10-26 — new document (Academy redesign, Phase E). A plan, not a promise: no lesson is listed
as available unless it is published in the database.*

## 1. Where the Academy stands today

| | Count |
|---|---|
| Learning paths in the catalogue | 9 (plus one empty, nameless record — now hidden; worth deleting by hand) |
| Courses in the catalogue | 73 |
| **Published courses** | **2** — *Information Coefficient* (4 lessons, 5-question quiz) and *From Correlation to Cointegration* (4 lessons, 4-question quiz) |
| Learning tools | Research Explainer (14 concepts, 3 levels), Z-Score Lab, Leverage Reality |

Every other course is a **planned** catalogue entry: it shows as "planned" and cannot be joined.

## 2. The Quant Learning Journey (configured in `bot/academy/journey.py`)

| Stage | Level | Existing paths mapped | Available now | Biggest gaps |
|---|---|---|---|---|
| 1 · Foundations | Beginner | Quant Foundations | — | Markets & trading basics; returns, percentages and compounding; risk, drawdown and capital |
| 2 · Mathematics for Quant Finance | Beginner | *(no path yet)* | — | Percentages & logs; mean, median, dispersion; variance & standard deviation; Z-scores; distributions; expected value; correlation & covariance; regression |
| 3 · Statistics & Research | Intermediate | Quantitative Research; Backtesting & Robustness | Information Coefficient | Hypotheses & null hypotheses; significance & power; multiple testing; leakage & survivorship bias; in-sample vs out-of-sample; walk-forward; permutation tests; confidence intervals |
| 4 · Python & Quant Engineering | Intermediate | Python for Quant Trading; Production Quant Systems | — | Python fundamentals; NumPy; pandas; financial data handling; backtesting architecture; testing & reproducibility |
| 5 · Quantitative Strategies | Advanced | Strategy Engineering; Execution; Risk; Multi-Asset | From Correlation to Cointegration | Regimes; trend vs mean reversion; cross-sectional ranking; OU half-life in depth; execution costs; portfolio risk; strategy evaluation |
| 6 · AI & Machine Learning | Advanced | *(no path yet)* | — | Features & labels; train / validation / test; overfitting; evaluation; drift; responsible AI |

## 3. Recommended order (each step = a small, fully authored course with checked examples and a quiz)

1. **Foundations I — Prices, returns and risk** (stage 1). The true starting point; every later course assumes it.
2. **Maths I — Averages, spread and Z-scores** (stage 2). Pairs naturally with the Z-Score Lab.
3. **Statistics I — Hypotheses, p-values and why significance is not profit** (stage 3).
4. **Statistics II — In-sample, out-of-sample and walk-forward** (stage 3). Uses the platform's own research zones.
5. **Python I — From a CSV of prices to returns with pandas** (stage 4).
6. **Strategies I — Market regimes, the way our detector classifies them** (stage 5).
7. **AI I — Features, labels and overfitting** (stage 6).

Each new course should come with: lesson files under `bot/academy/content/`, an entry in `curriculum_seed.AUTHORED_COURSES`,
known-answer tests for every worked number (pattern: `bot/tests/test_academy_cointegration_course.py`), and where useful
an Explainer concept with prerequisites.

## 4. Principles every course follows

- Start from zero at the stated level; define every term before using it.
- Intuition and an everyday example before any formula.
- Worked examples with exact, tested arithmetic, labelled illustrative.
- Say what a result does **not** mean.
- Facts about the platform only from verified sources; evidence statuses from the registries, never upgraded.
- No course implies that completing it makes anyone a profitable trader.
