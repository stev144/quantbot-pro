// ============================================================
// static/js/bubbles.js
// claude code changed: new — decorative falling-bubble background
// canvas, originally for the "midnight" theme only. This is a
// deliberate, explicit exception to the rest of this app's
// no-decorative-animation convention (every other page/theme has none)
// — added per direct user request. Self-contained (no dependency on
// chart-theme.js) since it needs to run on every page via base.html,
// not just the pages that happen to load Chart.js. Fully stops (rAF
// cancelled, canvas cleared) whenever data-theme isn't one of
// BUBBLE_THEMES, so it costs nothing on dark/light/gold.
//
// claude code changed: extended to the "navy" theme too, and the bubble
// color now reads the theme's own --accent variable at start() instead
// of a hardcoded gold — Midnight's accent is gold, Navy's is cyan-green
// (#00FF9F), so bubbles match whichever theme is actually active
// instead of looking like a leftover gold effect on a blue theme.
// Increased size/opacity range slightly per feedback that the first
// pass wasn't visible enough.
// ============================================================

(function () {
    var canvas = document.getElementById("bubble-canvas");
    if (!canvas) return;
    var ctx = canvas.getContext("2d");
    var bubbles = [];
    var animationId = null;
    var running = false;
    var bubbleRgb = "240, 185, 11"; // fallback (gold); overwritten from --accent in start()
    var BUBBLE_THEMES = ["midnight", "navy"];

    function hexToRgbTriple(hex) {
        hex = (hex || "").trim().replace("#", "");
        if (hex.length !== 6) return null;
        var r = parseInt(hex.substring(0, 2), 16);
        var g = parseInt(hex.substring(2, 4), 16);
        var b = parseInt(hex.substring(4, 6), 16);
        if (isNaN(r) || isNaN(g) || isNaN(b)) return null;
        return r + ", " + g + ", " + b;
    }

    function updateBubbleColor() {
        var accentHex = getComputedStyle(document.documentElement).getPropertyValue("--accent");
        var triple = hexToRgbTriple(accentHex);
        if (triple) bubbleRgb = triple;
    }

    function resize() {
        canvas.width = window.innerWidth;
        canvas.height = window.innerHeight;
    }

    function makeBubble(spawnAtTop) {
        return {
            x: Math.random() * canvas.width,
            y: spawnAtTop ? -10 - Math.random() * 100 : Math.random() * canvas.height,
            r: 1.5 + Math.random() * 4.5,
            speed: 0.3 + Math.random() * 0.7,
            drift: (Math.random() - 0.5) * 0.4,
            alpha: 0.12 + Math.random() * 0.28,
        };
    }

    function initBubbles() {
        var count = Math.min(90, Math.floor((canvas.width * canvas.height) / 20000));
        bubbles = [];
        for (var i = 0; i < count; i++) bubbles.push(makeBubble(false));
    }

    function step() {
        if (!running) return;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        for (var i = 0; i < bubbles.length; i++) {
            var b = bubbles[i];
            b.y += b.speed;
            b.x += b.drift;
            if (b.y - b.r > canvas.height) {
                bubbles[i] = makeBubble(true);
                continue;
            }
            ctx.beginPath();
            ctx.arc(b.x, b.y, b.r, 0, Math.PI * 2);
            ctx.fillStyle = "rgba(" + bubbleRgb + ", " + b.alpha + ")";
            ctx.fill();
        }
        animationId = requestAnimationFrame(step);
    }

    function start() {
        updateBubbleColor();
        if (running) return;
        running = true;
        resize();
        initBubbles();
        step();
    }

    function stop() {
        running = false;
        if (animationId) cancelAnimationFrame(animationId);
        animationId = null;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
    }

    function syncToTheme() {
        var theme = document.documentElement.getAttribute("data-theme");
        if (BUBBLE_THEMES.indexOf(theme) !== -1) {
            canvas.style.display = "block";
            start();
        } else {
            canvas.style.display = "none";
            stop();
        }
    }

    window.addEventListener("resize", function () {
        if (running) { resize(); initBubbles(); }
    });

    document.addEventListener("DOMContentLoaded", function () {
        syncToTheme();
        new MutationObserver(syncToTheme).observe(document.documentElement, {
            attributes: true,
            attributeFilter: ["data-theme"],
        });
    });
})();
