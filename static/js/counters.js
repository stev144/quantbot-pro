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
// claude code changed: restored setInterval(animateAllCounters, 5000) —
// an earlier "institutional-UI" pass had removed this re-trigger (every
// number resetting to 0 and counting back up every 5s regardless of
// whether the underlying data changed, reasoned to read as decorative on
// a server-rendered page). Explicit, deliberate reversal per direct
// request: every .stat-number on every page counts up from 0 again every
// 5 seconds, exactly as it worked before that pass.
//
// Usage: any element with class="stat-number" and a numeric
// data-target="123.45" attribute (optionally data-decimals="2" and
// data-suffix="%") animates from 0 to the target, on page load and every
// 5 seconds thereafter. Non-numeric data-target (e.g. missing/placeholder
// values) are left untouched rather than animated to 0.
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
    var startTime = null;   // claude code changed: 2026-10-21 — same first-frame clock + clamp as animateText (could briefly show a wrong-signed value)

    function step(now) {
        if (startTime === null) startTime = now;
        var progress = Math.max(0, Math.min((now - startTime) / duration, 1));
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

// claude code changed: 2026-10-21 — text mode for formatted values that are not a single number
// ("−$4,920 (−65.4%)", "+$6,856.41", "46 (8.9%)"). Any LEAF element with class="count-text" has every
// number in its final text counted up from 0 with its own decimals and thousands separators; signs,
// currency symbols, % and words stay as they are. The final text is captured once in data-final.
// Mark only value cells — never dates/timestamps (their digits would animate too).
var COUNT_TEXT_RE = /\d[\d,]*(?:\.\d+)?/g;

function formatLike(sample, value) {
    var decimals = (sample.split(".")[1] || "").length;
    var s = value.toFixed(decimals);
    if (sample.indexOf(",") >= 0) {
        var parts = s.split(".");
        parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
        s = parts.join(".");
    }
    return s;
}

function animateText(el) {
    if (el.children.length) return;   // leaf elements only: textContent would erase child markup
    var finalText = el.getAttribute("data-final");
    if (finalText === null) {
        finalText = el.textContent;
        el.setAttribute("data-final", finalText);
    }
    if (!/\d/.test(finalText)) return;
    var duration = 900;
    var startTime = null;   // set from the first frame's own timestamp: rAF times can precede performance.now() (negative progress)

    function step(now) {
        if (startTime === null) startTime = now;
        var progress = Math.max(0, Math.min((now - startTime) / duration, 1));
        if (!el.isConnected) return;   // re-rendered meanwhile: stop quietly
        el.textContent = progress < 1
            ? finalText.replace(COUNT_TEXT_RE, function (m) { return formatLike(m, parseFloat(m.replace(/,/g, "")) * progress); })
            : finalText;
        if (progress < 1) requestAnimationFrame(step);
    }
    requestAnimationFrame(step);
}

function animateAllCounters() {
    document.querySelectorAll(".stat-number").forEach(animateCounter);
    document.querySelectorAll(".count-text").forEach(animateText);   // claude code changed: 2026-10-21
}

document.addEventListener("DOMContentLoaded", animateAllCounters);
setInterval(animateAllCounters, 5000);
