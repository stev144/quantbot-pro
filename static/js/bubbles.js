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

// claude code changed: 2026-10-25 — owner request: instead of dots, Bitcoin logos and forex currency flags fall in
// the background. Images are the project's bundled, licensed assets (static/img/coins/btc.svg — cryptocurrency-icons;
// static/img/flags/*.svg — flag-icons, MIT), drawn as round badges like the forex cards' flags. Until an image has
// loaded (or if one fails) that item is drawn as the old accent-coloured dot, so the background never goes blank.

(function () {
    var script = document.currentScript;   // claude code changed: 2026-10-25 — derive /static/ from this script's URL
    var canvas = document.getElementById("bubble-canvas");
    if (!canvas) return;
    canvas.style.filter = "saturate(0.5) brightness(0.95)";   // claude code changed: 2026-10-25 — owner: dull but clearly visible — no blur (it made them unreadable), muted colours instead   // claude code changed: 2026-10-25 — owner request: soften the logos so they stay in the background (one GPU filter on the whole canvas)
    var ctx = canvas.getContext("2d");
    var bubbles = [];
    var animationId = null;
    var running = false;
    var bubbleRgb = "240, 185, 11"; // fallback (gold); overwritten from --accent in start()
    var BUBBLE_THEMES = ["midnight", "navy"];

    // claude code changed: 2026-10-25 — the falling logos, in three groups (owner request: bolder; more forex and crypto;
    // US stocks as whole-market INDEX portfolios, not single stocks). Crypto and forex use bundled licensed SVGs; the
    // indices are drawn as text badges (no fund/brand logos are copied).
    var STATIC = script && script.src ? script.src.replace(/js\/bubbles\.js(\?.*)?$/, "") : "/static/";
    // claude code changed: 2026-10-26 — owner request: only Bitcoin (the owner's gold-coin image, img/coins/btc_gold.png),
    // the Nasdaq 100 and S&P 500 badges, and two flags (US, UK). Was 24 coins, 21 flags/metals and 5 indices.
    var CRYPTO = ["img/coins/btc_gold.png"];
    var FOREX = ["us", "gb"];
    var INDICES = [["S&P", "500"], ["NASDAQ", "100"]];
    var GROUP_SHARE = { crypto: 0.4, forex: 0.3, index: 0.3 };   // share of the falling items per group   // claude code changed: 2026-10-26 — was 0.4 / 0.4 / 0.2
    function loadImg(path) {
        var img = new Image();
        img.decoding = "async";
        img.src = STATIC + path;
        return img;
    }
    var logos = {
        crypto: CRYPTO.map(function (c) { return { img: loadImg(c), square: true }; }),   // claude code changed: 2026-10-26 — full path (PNG)
        forex: FOREX.map(function (f) { return { img: loadImg("img/flags/" + f + ".svg"), square: false }; }),
        index: INDICES.map(function (lines) { return { lines: lines }; }),
    };
    function ready(img) { return img && img.complete && img.naturalWidth > 0; }
    function pick(list) { return list[Math.floor(Math.random() * list.length)]; }

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
        // claude code changed: 2026-10-25 — each item is a crypto logo, a forex flag or a US index badge (GROUP_SHARE)
        var u = Math.random();
        var group = u < GROUP_SHARE.crypto ? "crypto" : u < GROUP_SHARE.crypto + GROUP_SHARE.forex ? "forex" : "index";
        var r = group === "index" ? 17 + Math.random() * 6 : 9 + Math.random() * 9;   // claude code changed: 2026-10-25 — a little larger (9–18 px, indices 17–23) so blurred logos read
        return {
            x: Math.random() * canvas.width,
            y: spawnAtTop ? -2 * r - Math.random() * 120 : Math.random() * canvas.height,
            r: r,
            speed: 0.2 + Math.random() * 0.4,   // claude code changed: 2026-10-25 — slower fall, less distracting
            drift: (Math.random() - 0.5) * 0.4,
            alpha: 0.5 + Math.random() * 0.2,              // claude code changed: 2026-10-25 — 50–70 % opaque, sharp; the muted colours (canvas filter) keep them dull
            logo: pick(logos[group]),
            angle: group === "index" ? 0 : Math.random() * Math.PI * 2,   // index text stays upright
            spin: group === "index" ? 0 : (Math.random() - 0.5) * 0.01,
        };
    }

    function initBubbles() {
        // claude code changed: 2026-10-25 — up to 60 items (logos are larger than the old dots)
        var count = Math.min(60, Math.floor((canvas.width * canvas.height) / 22000));   // claude code changed: 2026-10-25 — back to the original quantity (up to 60), owner request
        bubbles = [];
        for (var i = 0; i < count; i++) bubbles.push(makeBubble(false));
    }

    function drawLogo(b) {
        // claude code changed: 2026-10-25 — round badge: the image clipped to a circle with a faint accent ring
        var L = b.logo, img = L.img;
        ctx.save();
        ctx.globalAlpha = b.alpha;
        ctx.translate(b.x, b.y);
        ctx.rotate(b.angle);
        if (L.lines) {   // US index badge: dark disc, accent ring, two lines of bold text
            ctx.beginPath();
            ctx.arc(0, 0, b.r, 0, Math.PI * 2);
            ctx.fillStyle = "rgba(4, 14, 36, 0.92)";
            ctx.fill();
            ctx.lineWidth = 1.8;
            ctx.strokeStyle = "rgba(" + bubbleRgb + ", 0.95)";
            ctx.stroke();
            ctx.textAlign = "center";
            ctx.textBaseline = "middle";
            function fitText(text, y, maxSize, color) {   // largest bold size whose width fits inside the disc at height y
                var room = 2 * Math.sqrt(Math.max(b.r * b.r - y * y, 1)) * 0.86;
                var fs = maxSize;
                ctx.font = "800 " + fs.toFixed(1) + "px system-ui, Segoe UI, sans-serif";
                var w = ctx.measureText(text).width;
                if (w > room) {
                    fs = Math.max(5, fs * room / w);
                    ctx.font = "800 " + fs.toFixed(1) + "px system-ui, Segoe UI, sans-serif";
                }
                ctx.fillStyle = color;
                ctx.fillText(text, 0, y);
            }
            fitText(L.lines[0], -b.r * 0.24, b.r * 0.5, "#ffffff");
            fitText(L.lines[1], b.r * 0.3, b.r * 0.55, "rgb(" + bubbleRgb + ")");
        } else if (ready(img)) {
            ctx.beginPath();
            ctx.arc(0, 0, b.r, 0, Math.PI * 2);
            ctx.closePath();
            ctx.save();
            ctx.clip();
            var w = L.square ? 2 * b.r : 2 * b.r * (img.naturalWidth / img.naturalHeight);   // flags 4:3 → cover the circle
            ctx.drawImage(img, -w / 2, -b.r, w, 2 * b.r);
            ctx.restore();
            ctx.lineWidth = 1.8;                                       // bolder ring
            ctx.strokeStyle = "rgba(" + bubbleRgb + ", 0.9)";
            ctx.stroke();
        } else {   // not loaded yet / failed: the original dot
            ctx.beginPath();
            ctx.arc(0, 0, Math.min(b.r, 6), 0, Math.PI * 2);
            ctx.fillStyle = "rgb(" + bubbleRgb + ")";
            ctx.fill();
        }
        ctx.restore();
    }

    function step() {
        if (!running) return;
        ctx.clearRect(0, 0, canvas.width, canvas.height);
        for (var i = 0; i < bubbles.length; i++) {
            var b = bubbles[i];
            b.y += b.speed;
            b.x += b.drift;
            b.angle += b.spin;
            if (b.y - b.r > canvas.height) {
                bubbles[i] = makeBubble(true);
                continue;
            }
            drawLogo(b);   // claude code changed: 2026-10-25 — logos instead of plain dots
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
