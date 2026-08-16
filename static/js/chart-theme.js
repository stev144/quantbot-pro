// ============================================================
// static/js/chart-theme.js
// claude code changed: new file — extracted from 3 near-identical
// copies that used to live inline in dashboard.html, feature_detail.html,
// and feature_comparison.html (each independently reading the same CSS
// custom properties, building the same scales shape, and re-implementing
// destroy-before-recreate). One shared file now; pages call these
// instead of redefining them.
//
// Theme note: base.html's own theme-setter <script> tag always renders
// after {% block content %}/{% block scripts %} in the DOM, so it
// always executes last — a page-level redefinition of the theme setter
// would be silently clobbered by base.html's version (confirmed
// earlier by inspecting rendered output when this was a toggleTheme()
// function: two declarations existed, base.html's won). onThemeChange()
// below sidesteps this entirely by watching the actual data-theme
// attribute instead of hooking the setter — use it for any chart.
// ============================================================

function themeColor(varName) {
    return getComputedStyle(document.documentElement).getPropertyValue(varName).trim();
}

function chartCommonScales(opts) {
    opts = opts || {};
    var textColor = themeColor('--text-dim');
    var gridColor = themeColor('--border');
    var scales = {
        x: {
            ticks: { color: textColor, font: { size: 10 }, maxTicksLimit: opts.maxTicksLimit || 10 },
            grid: { color: gridColor },
        },
        y: {
            ticks: { color: textColor, font: { size: 10 } },
            grid: { color: gridColor },
        },
    };
    if (opts.xTitle) {
        scales.x.title = { display: true, text: opts.xTitle, color: textColor, font: { size: 10 } };
    }
    return scales;
}

function hexToRgba(hex, alpha) {
    hex = hex.replace('#', '');
    var r = parseInt(hex.substring(0, 2), 16);
    var g = parseInt(hex.substring(2, 4), 16);
    var b = parseInt(hex.substring(4, 6), 16);
    return 'rgba(' + r + ',' + g + ',' + b + ',' + alpha + ')';
}

// Returns a Chart.js backgroundColor callback that paints a vertical
// gradient fill under a line dataset, built from a resolved --var hex
// color (not a raw var() string — canvas fillStyle/gradient stops don't
// resolve CSS custom properties without an element context).
function chartAreaFill(hexColor, topAlpha, bottomAlpha) {
    topAlpha = topAlpha === undefined ? 0.38 : topAlpha;
    bottomAlpha = bottomAlpha === undefined ? 0.02 : bottomAlpha;
    return function (context) {
        var chart = context.chart;
        var ctx = chart.ctx, chartArea = chart.chartArea;
        if (!chartArea) return hexToRgba(hexColor, topAlpha * 0.5);
        var gradient = ctx.createLinearGradient(0, chartArea.top, 0, chartArea.bottom);
        gradient.addColorStop(0, hexToRgba(hexColor, topAlpha));
        gradient.addColorStop(1, hexToRgba(hexColor, bottomAlpha));
        return gradient;
    };
}

function destroyChart(instance) {
    if (instance) instance.destroy();
    return null;
}

// Fires `callback` whenever document.documentElement's data-theme
// attribute changes — the reliable way to re-render a chart on theme
// change (see module note above for why shadowing the theme setter
// doesn't work here).
function onThemeChange(callback) {
    new MutationObserver(callback).observe(document.documentElement, {
        attributes: true,
        attributeFilter: ['data-theme'],
    });
}
