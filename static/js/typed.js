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
// Content: claude code changed — replaced with the tagline set supplied
// directly by the user. Most lines describe real, wired capabilities
// (regime detection, Kalman-filtered pairs, feature stability testing,
// rejection tracking, backtesting cost modeling); a couple lean more
// aspirational/marketing than the previous strictly-literal set (e.g.
// "200+ crypto pairs" — the real universe is 20 symbols / 190 pairs;
// "monitor crash and liquidation risk" — contagion_engine.py exists but
// has no persisted output and isn't wired into anything yet, per the
// architecture audit). Flagged to the user, not altered — this is
// display copy, not a computed metric, so the "never fabricate
// metrics" rule doesn't block it, but worth knowing.
// ============================================================

(function () {
    var messages = [
        "Analyze market structure in real-time.",
        "Detect market regimes before they shift.",
        "Measure trading edge, not emotions.",

        "Research 200+ crypto pairs simultaneously.",
        "Analyze cross-sectional opportunities across the market.",
        "Use our Cross-Section Engine to identify relative-value opportunities.",
        "Model dynamic relationships between assets with Kalman Filters.",
        "Measure cross-asset contagion and systemic market risk.",

        "Test features for stability across market regimes.",
        "Separate genuine alpha from noise and overfitting.",
        "Prove whether indicators contain predictive information.",
        "Challenge trading ideas with statistics, mathematics and data science.",

        "Track why trades were rejected.",
        "Turn rejected trades into intelligence.",
        "Optimize strategies with evidence, not assumptions.",

        "Backtest with realistic fees, slippage and risk constraints.",
        "Measure expectancy, drawdown, profit factor and R-multiples.",
        "Monitor crypto-wide crash and liquidation risk.",
        "Adapt exposure when market conditions become dangerous.",

        "Research first.",
        "Validate second.",
        "Deploy last.",
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
