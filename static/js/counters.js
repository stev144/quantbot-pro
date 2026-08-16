// ============================================================
// static/js/counters.js
// claude code changed: new file — single canonical count-up animation,
// extracted from 5 near-duplicate copies (dashboard.html, research_lab.html,
// feature_leaderboard.html, feature_detail.html, feature_correlation.html)
// that had drifted into 3 inconsistent variants: some re-animated every
// 5s (setInterval) and some didn't, some read data-suffix and some
// silently ignored it. This is the superset: data-suffix always
// supported, 5s re-animate always on (matches the majority of the
// existing copies).
//
// Usage: any element with class="stat-number" and a numeric
// data-target="123.45" attribute (optionally data-decimals="2" and
// data-suffix="%") animates from 0 to the target on load and every 5s
// after. Non-numeric data-target (e.g. missing/placeholder values) are
// left untouched rather than animated to 0.
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
setInterval(animateAllCounters, 5000);
