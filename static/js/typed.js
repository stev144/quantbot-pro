// ============================================================
// static/js/typed.js
// claude code changed: full rewrite. Previously this file was dead code
// — it referenced messages/typedText/index/isDeleting/charIndex as bare
// globals that were never declared anywhere in this file, and nothing
// ever loaded it via a <script src> tag (base.html had a second, fully
// separate, working copy of this same feature inline instead — see
// architecture audit for the duplication finding). This is now the one
// real implementation: self-contained, loaded explicitly by the
// dashboard template.
//
// Content: real capability descriptions matching what this codebase
// actually does (regime detection, structure analysis, Kalman-filtered
// pairs research, feature validation, walk-forward testing) — not
// marketing copy.
// ============================================================

(function () {
    var messages = [
        "Detecting market regimes via ADX / ATR / EMA spread / Bollinger width.",
        "Routing signals by regime: trend-following vs. mean-reversion.",
        "Backtesting with modeled slippage, fees, and fixed-fractional risk sizing.",
        "Scoring strategies across profitability, consistency, risk, and cost efficiency.",
        "Validating features for predictive power (IC) and cross-regime stability.",
        "Testing cointegration and Kalman-filtered spreads across pairs.",
        "Running walk-forward splits to check for in-sample overfitting.",
        "Tracking why signals were rejected, not just why trades were taken.",
    ];

    var typedIndex = 0;
    var charIndex = 0;
    var isDeleting = false;
    var typeSpeed = 45;
    var deleteSpeed = 25;
    var pauseAtFull = 1600;
    var pauseAtEmpty = 500;

    function tick() {
        var el = document.getElementById("typed-text");
        if (!el) return;   // element not on this page — stop quietly, no error

        var current = messages[typedIndex];
        var delay;

        if (isDeleting) {
            charIndex--;
            delay = deleteSpeed;
        } else {
            charIndex++;
            delay = typeSpeed;
        }

        el.textContent = current.substring(0, charIndex);

        if (!isDeleting && charIndex === current.length) {
            isDeleting = true;
            delay = pauseAtFull;
        } else if (isDeleting && charIndex === 0) {
            isDeleting = false;
            typedIndex = (typedIndex + 1) % messages.length;
            delay = pauseAtEmpty;
        }

        setTimeout(tick, delay);
    }

    document.addEventListener("DOMContentLoaded", tick);
})();
