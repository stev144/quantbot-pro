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
// Content: claude code changed — corrected per direct user request
// ("remove only the parts that are unrealistic, leave the ones that are
// real, add animated sentences our platform actually does"). Two lines
// were removed outright: "Research 200+ crypto pairs simultaneously"
// (the real tracked universe is 20 symbols / 190 pairs — verified
// against bot/fetch_all_symbols.py's SYMBOLS list) and "Monitor
// crypto-wide crash and liquidation risk" (bot/research/contagion_engine.py
// exists but bot/views/terminal_data.py's own crash_risk field explicitly
// reports {"available": False, "reason": "...not wired into any
// pipeline"} — the UI itself already tells the truth here, the tagline
// should too). One line was reworded, not cut, to drop the marketing
// voice ("Use our Cross-Section Engine to...") while keeping the real
// capability. Every remaining and newly-added line maps to a real,
// registered capability (bot/research_lab/capability_registry.py) or a
// real, tested module — see the inline citations below.
// ============================================================

(function () {
    var messages = [
        "Analyze market structure in real-time.",
        "Detect market regimes before they shift.",
        "Measure trading edge, not emotions.",

        "Track structure and regime across 20 tracked crypto assets.",   // bot/fetch_all_symbols.py
        "Identify relative-value opportunities across correlated assets.",   // cross_sectional_research
        "Score cointegrated pairs with a Kalman-filtered hedge ratio.",   // kalman_dynamic_hedge_ratio

        "Test features for stability across market regimes.",   // feature_stability_research
        "Separate genuine alpha from noise and overfitting.",
        "Prove whether indicators contain predictive information.",
        "Challenge trading ideas with statistics, mathematics and data science.",

        "Validate every hypothesis out-of-sample before it's trusted.",   // bot/research/oos_validator.py
        "Run permutation tests to separate skill from luck.",   // permutation_robustness_testing
        "Never let a strategy see the data it's tested against.",   // walk_forward_validation / oos_validator purge-embargo

        "Track why trades were rejected.",
        "Turn rejected trades into intelligence.",
        "Optimize strategies with evidence, not assumptions.",

        "Backtest with realistic fees, slippage and risk constraints.",
        "Measure expectancy, drawdown, profit factor and R-multiples.",
        "Adapt exposure when market conditions become dangerous.",   // bot/risk/drawdown_guard.py
        "Route orders through Binance and Kraken at venue-real cost.",   // bot/engines/exchange_adapter.py + siblings

        "Keep every experiment on an append-only research ledger.",   // bot/research_lab/models.py HypothesisFamily/ResearchExperiment

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
