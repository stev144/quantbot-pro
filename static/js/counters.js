// ============================================================
// static/js/counters.js
// claude code changed: new file — single canonical count-up animation,
// extracted from 5 near-duplicate copies (dashboard.html, research_lab.html,
// feature_leaderboard.html, feature_detail.html, feature_correlation.html)
// that had drifted into 3 inconsistent variants: some re-animated every
// 5s (setInterval) and some didn't, some read data-suffix and some
// silently ignored it. This is the superset: data-suffix always
// supported.
//
// claude code changed: removed the setInterval(animateAllCounters, 5000)
// re-trigger — per the institutional-UI pass, every number on the page
// (Sharpe, drawdown, individual trade rows, everything) was resetting to
// 0 and counting back up every 5 seconds forever, on every page, even
// though these pages are server-rendered on load/form-submit and the
// underlying data never actually changes between reloads. An
// institutional terminal shows static numbers that update only when the
// data does; a number that visibly flickers and re-animates on a fixed
// timer with no data change reads as decorative, not informational —
// exactly the retail-SaaS pattern this design system's own header
// comment ("no gradients, no glassmorphism, no decorative animation")
// already commits to avoiding. The one-time reveal animation on load is
// kept — it's a common, unobtrusive professional touch — only the
// infinite repeat is removed.
//
// Usage: any element with class="stat-number" and a numeric
// data-target="123.45" attribute (optionally data-decimals="2" and
// data-suffix="%") animates from 0 to the target once, on page load.
// Non-numeric data-target (e.g. missing/placeholder values) are left
// untouched rather than animated to 0.
// ============================================================

function formatNumber(num, decimals) {
    if (!isFinite(num)) return "0";
    return Number(num).toFixed(decimals);
}

function animateCounter(el) {
    var target = parseFloat(el.getAttribute("data-target"));
    if (isNaN(target)) return;

    var decimals = parseInt(el.getAttribute("data-decimals") || "0", 10);
    var suffix = el.getAttribute("data-suffix") || "";
    var duration = 900;
    var startTime = performance.now();

    function step(now) {
        var progress = Math.min((now - startTime) / duration, 1);
        var current = target * progress;
        el.textContent = formatNumber(current, decimals) + suffix;
        if (progress < 1) {
            requestAnimationFrame(step);
        } else {
            el.textContent = formatNumber(target, decimals) + suffix;
        }
    }
    requestAnimationFrame(step);
}

function animateAllCounters() {
    document.querySelectorAll(".stat-number").forEach(animateCounter);
}

document.addEventListener("DOMContentLoaded", animateAllCounters);
