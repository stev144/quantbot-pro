// ============================================================
// static/js/market_workspace.js — Market Intelligence workspace (templates/market_workspace.html)
// claude code changed: 2026-10-21 — new.
//
// Security: every server/AI/news string is inserted with textContent (never innerHTML); links are rendered only for
// http(s) URLs with rel="noopener noreferrer nofollow". Each section loads independently with its own loading,
// empty, error and retry states, so a failing section (e.g. AI news) never blocks the rest.
// ============================================================
(function () {
    "use strict";
    var root = document.getElementById("mi-root");
    if (!root) return;
    var D = root.dataset;
    var state = { tf: new URLSearchParams(location.search).get("tf") || "1h", core: null, charts: {}, histToken: null, hyp: null };
    if (["1h", "4h", "1d"].indexOf(state.tf) < 0) state.tf = "1h";

    // ── tiny DOM helpers (text only) ─────────────────────────────────────────────────────────────────────────
    function el(tag, attrs, kids) {
        var n = document.createElement(tag);
        if (attrs) Object.keys(attrs).forEach(function (k) {
            if (k === "class") n.className = attrs[k];
            else if (k === "text") n.textContent = attrs[k];
            else if (attrs[k] !== null && attrs[k] !== undefined) n.setAttribute(k, attrs[k]);
        });
        (kids || []).forEach(function (c) { if (c != null) n.appendChild(typeof c === "string" ? document.createTextNode(c) : c); });
        return n;
    }
    function clear(n) { while (n.firstChild) n.removeChild(n.firstChild); return n; }
    function $(id) { return document.getElementById(id); }
    function safeUrl(u) { try { var p = new URL(u); return (p.protocol === "https:" || p.protocol === "http:") ? p.href : null; } catch (e) { return null; } }
    function link(u, text) {
        var s = safeUrl(u);
        return s ? el("a", { href: s, target: "_blank", rel: "noopener noreferrer nofollow", text: text }) : el("span", { text: text });
    }
    function tag(cls, text, title) { return el("span", { class: "mi-tag " + cls, text: text, title: title || null }); }
    function num(v, d) {
        if (v === null || v === undefined || !isFinite(v)) return "—";
        var a = Math.abs(v), dd = d !== undefined ? d : (a >= 1000 ? 2 : a >= 10 ? 3 : a >= 1 ? 4 : 6);
        return Number(v).toLocaleString(undefined, { minimumFractionDigits: Math.min(dd, 2), maximumFractionDigits: dd });
    }
    function pct(v, d) { return (v === null || v === undefined || !isFinite(v)) ? "—" : (v > 0 ? "+" : "") + Number(v).toFixed(d === undefined ? 2 : d) + "%"; }
    function money(v) { return (v === null || v === undefined || !isFinite(v)) ? "—" : (v < 0 ? "−$" : "$") + Math.abs(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
    function cls(v) { return v > 0 ? "pos" : v < 0 ? "neg" : ""; }
    function ago(iso) {
        var s = (Date.now() - Date.parse(iso)) / 1000;
        if (!isFinite(s)) return "unknown time";
        if (s < 90) return Math.round(s) + "s ago"; if (s < 5400) return Math.round(s / 60) + " min ago";
        if (s < 172800) return Math.round(s / 3600) + " h ago"; return Math.round(s / 86400) + " days ago";
    }
    function css(v) { return getComputedStyle(document.documentElement).getPropertyValue(v).trim(); }
    function errorBox(target, msg, retry) {
        clear(target).appendChild(el("div", { class: "mi-banner bad" }, [msg + " ",
            retry ? el("button", { class: "mi-btn ghost", type: "button", text: "Retry" }) : null]));
        if (retry) target.querySelector("button").addEventListener("click", retry);
    }
    function skeleton(target, n) { clear(target); for (var i = 0; i < (n || 3); i++) target.appendChild(el("div", { class: "mi-skel" })); }
    function getJSON(url) {
        return fetch(url, { headers: { "Accept": "application/json" }, credentials: "same-origin" }).then(function (r) {
            return r.json().catch(function () { return {}; }).then(function (j) { if (!r.ok) { var e = new Error(j.error || j.message || ("HTTP " + r.status)); e.body = j; throw e; } return j; });
        });
    }
    function stateTag(s) { var k = (s || "unavailable").toLowerCase(); return tag(k, s || "UNAVAILABLE", "LIVE = live source, last closed bar ≤ 1 bar old; DELAYED / STALE = saved or late data; MARKET CLOSED = forex weekend/holiday, data reach the close"); }   // claude code changed: 2026-10-25 — MARKET CLOSED explained

    // ── core: header, regime, chart, evidence, alerts, lessons ───────────────────────────────────────────────
    function loadCore() {
        skeleton($("mi-regime")); skeleton($("mi-evidence"), 5); skeleton($("mi-alerts"), 2); skeleton($("mi-lessons"), 3);
        getJSON(D.coreUrl + "?tf=" + encodeURIComponent(state.tf)).then(function (j) {
            state.core = j;
            renderHeader(j.overview); renderRegime(j.regime, j.overview); renderChart(j.chart, j.evidence, j.overview);
            renderEvidence(j.evidence); renderAlerts(j.exposure, j.alerts); renderLessons(j.lessons); fillRiskDefaults(j.risk_defaults);
        }).catch(function (e) {
            var m = "Market data could not be loaded (" + e.message + ").";
            ["mi-regime", "mi-evidence", "mi-alerts", "mi-lessons"].forEach(function (id) { errorBox($(id), m, loadCore); });
            $("mi-price-meta").textContent = m;
        });
    }

    function renderHeader(o) {
        $("mi-price").textContent = num(o.price);
        var st = $("mi-price-state"); st.className = "mi-tag " + (o.price_state || "unavailable").toLowerCase(); st.textContent = o.price_state || "UNAVAILABLE";
        var ch = clear($("mi-changes"));
        ["1h", "24h", "7d", "30d"].forEach(function (k) {
            var v = (o.changes_pct || {})[k];
            ch.appendChild(el("span", { class: "mi-chip" }, [k + " ", el("span", { class: cls(v), text: pct(v) })]));
        });
        var src = { BINANCE_LIVE: "Binance (live)", MT5_LIVE: "MT5 terminal (live tick)", MT5_CSV: "saved MT5 hourly candles", RESEARCH_CSV: "saved research data" }[o.price_source] || o.price_source || "unknown source";
        var bm = o.bars_meta || {};
        $("mi-price-meta").textContent = "Price: " + src + " · as of " + (o.price_as_of ? ago(o.price_as_of) : "—") +
            (o.bid ? " · bid " + num(o.bid) + " / ask " + num(o.ask) : "") + " · " + o.session.state +
            " · analysis timeframe " + o.timeframe + " from " + (bm.source || "?") + " (" + ((bm.freshness || {}).state || "?") + ")" +
            " · changes: " + o.changes_basis;
    }

    var REGIME_COLOR = { TRENDING_UP: "--positive", TRENDING_DOWN: "--negative", RANGING: "--accent", HIGH_VOLATILITY: "--warning" };
    function renderRegime(r, o) {
        var t = clear($("mi-regime"));
        if (!r || !r.available) { t.appendChild(el("div", { class: "mi-banner", text: "Regime assessment unavailable: " + ((r && r.reason) || "insufficient data") })); return; }
        var name = el("div", { class: "mi-regime-name", text: r.regime.replace("_", " ") }); name.style.color = css(REGIME_COLOR[r.regime] || "--text-primary");
        t.appendChild(el("div", { class: "mi-regime" }, [el("div", null, [name, el("div", { class: "mi-sub", text: r.agreement_text + " (" + r.agreement + ")" })]),
            el("div", { class: "mi-text", style: "flex:1;min-width:220px" }, [el("p", { text: r.what }), el("p", null, [el("strong", { text: "What it may mean: " }), r.meaning])])]));
        t.appendChild(el("div", { class: "mi-kv" }, [
            kv("ADX (trend strength)", num(r.adx, 1)), kv("ATR ratio (volatility vs normal)", num(r.atr_ratio, 2)),
            kv("Timeframe", r.timeframe), kv("Bars used", String(r.bars_used)), kv("As of (bar open)", new Date(r.as_of).toUTCString().slice(5, 22))]));
        t.appendChild(el("p", { class: "mi-note", text: "Method: " + r.method + ". " + r.agreement_note + ". " + r.evidence_class + "." }));
    }
    function kv(k, v) { return el("div", null, [el("div", { class: "k", text: k }), el("div", { class: "v", text: v })]); }

    function renderChart(ch, ev, o) {
        if (typeof Chart === "undefined") { setTimeout(function () { renderChart(ch, ev, o); }, 300); return; }
        var ctx = $("mi-chart"); var old = Chart.getChart(ctx); if (old) old.destroy();
        var pts = function (arr) { return ch.t.map(function (t, i) { return { x: t, y: arr[i] }; }); };
        var ds = [
            { label: "Close", data: pts(ch.close), borderColor: css("--accent"), borderWidth: 1.6, pointRadius: 0, tension: 0 },
            { label: "EMA20", data: pts(ch.ema20), borderColor: css("--positive"), borderWidth: 1, pointRadius: 0, borderDash: [4, 3] },
            { label: "EMA50", data: pts(ch.ema50), borderColor: css("--warning"), borderWidth: 1, pointRadius: 0, borderDash: [2, 3] }];
        var m = {}; ((ev && ev.metrics) || []).forEach(function (x) { m[x.key] = x.value; });
        if (m.range_hi) ds.push({ label: "Range high (observed)", data: [{ x: ch.t[Math.max(0, ch.t.length - 50)], y: m.range_hi }, { x: ch.t[ch.t.length - 1], y: m.range_hi }], borderColor: css("--text-dim"), borderWidth: 1, pointRadius: 0, borderDash: [6, 4] });
        if (m.range_lo) ds.push({ label: "Range low (observed)", data: [{ x: ch.t[Math.max(0, ch.t.length - 50)], y: m.range_lo }, { x: ch.t[ch.t.length - 1], y: m.range_lo }], borderColor: css("--text-dim"), borderWidth: 1, pointRadius: 0, borderDash: [6, 4] });
        var dim = css("--text-dim"), grid = css("--border");
        new Chart(ctx, { type: "line", data: { datasets: ds }, options: { responsive: true, maintainAspectRatio: false, animation: false,
            interaction: { mode: "nearest", intersect: false, axis: "x" },
            plugins: { legend: { labels: { color: dim, boxWidth: 10, font: { size: 10 } } } },
            scales: { x: { type: "time", ticks: { color: dim, maxTicksLimit: 8, font: { size: 10 } }, grid: { color: grid } },
                      y: { ticks: { color: dim, font: { size: 10 } }, grid: { color: grid } } } } });
        $("mi-chart-meta").textContent = ch.t.length + " closed " + o.timeframe + " bars · range lines = observed " + "extremes, not guaranteed levels";
    }

    function renderEvidence(ev) {
        var t = clear($("mi-evidence"));
        if (!ev || !ev.available) { t.appendChild(el("div", { class: "mi-banner", text: "Evidence unavailable: " + ((ev && ev.reason) || "insufficient data") })); return; }
        var tb = el("tbody");
        var SIGNED = { ret_window: 1, ema_spread: 1, drawdown_from_high: 1 };   // only directional measures get +/− and colour
        ev.metrics.forEach(function (m) {
            var signed = SIGNED[m.key] === 1;
            var val = m.value === null ? "—" : m.unit === "price" ? num(m.value) : signed ? pct(m.value)
                : m.unit === "percentile" ? num(m.value, 0) + " pctl" : num(m.value, m.unit === "%" || m.unit === "% of bars" || m.unit === "% of range" ? 1 : 3) + (m.unit ? " " + m.unit : "");
            var d = el("details", null, [el("summary", { text: "details" }),
                el("div", { class: "mi-note" }, [el("div", { text: "Measured: " + m.measured }), el("div", { text: "Sample: " + m.sample }),
                    el("div", { text: "Limitation: " + m.limitation })])]);
            tb.appendChild(el("tr", null, [el("td", null, [el("div", { text: m.label }), d]),
                el("td", { class: "num " + (signed ? cls(m.value) : ""), text: val }),
                el("td", { class: "mi-text", style: "font-size:12px", text: m.meaning }), el("td", null, [tag("edu", m.kind)])]));
        });
        t.appendChild(el("div", { class: "mi-table-wrap" }, [el("table", { class: "mi-table" }, [el("thead", null, [el("tr", null,
            ["Metric", "Value", "What it means", "Status"].map(function (h) { return el("th", { text: h }); }))]), tb])]));
        t.appendChild(el("p", { class: "mi-note", text: ev.note + " Sample: " + ev.sample + "." }));
    }

    function renderAlerts(exp, alerts) {
        var t = clear($("mi-alerts"));
        if (exp && exp.available) {
            if (exp.positions.length) {
                exp.positions.forEach(function (p) {
                    t.appendChild(el("div", { class: "mi-alert REVIEW" }, [tag("verified", "recorded"), " ", p.side + " " + num(p.quantity) + " " + p.symbol + " @ " + num(p.entry_price) + " on " + p.venue + (p.stop_loss ? " · stop " + num(p.stop_loss) : "")]));
                });
            } else t.appendChild(el("div", { class: "mi-banner ok", text: "No open position in this instrument in the bot's journal (" + exp.open_positions_all_symbols + " open across all symbols)." }));
            t.appendChild(el("p", { class: "mi-note", text: exp.source + ". " + exp.note }));
        } else t.appendChild(el("div", { class: "mi-banner info", text: (exp && exp.reason) || "Exposure unavailable." }));
        if (!alerts || !alerts.length) t.appendChild(el("p", { class: "mi-note", text: "No alerts for this asset right now." }));
        (alerts || []).forEach(function (a) {
            t.appendChild(el("div", { class: "mi-alert " + a.level }, [tag(a.level === "REVIEW" ? "warn" : "edu", a.level), " ",
                el("strong", { text: a.instrument }), el("div", { text: a.reason }), el("div", { class: "mi-note", text: "Source: " + a.source + " · " + a.confidence + " · " + ago(a.timestamp) }),
                el("div", { text: "Suggested review: " + a.action })]));
        });
        t.appendChild(el("p", { class: "mi-note", text: "Alerts are informational. Risk-limit breaches are decided by the live risk manager, never by this page; nothing here changes positions or limits." }));
    }

    function renderLessons(list) {
        var t = clear($("mi-lessons"));
        (list || []).forEach(function (l, i) {
            t.appendChild(el("details", { class: "mi-lesson" }, [el("summary", { text: l.title }), el("p", { text: l.body }), el("div", { class: "mi-note", text: l.disclaimer })]));
        });
    }

    // ── risk scenario ────────────────────────────────────────────────────────────────────────────────────────
    function fillRiskDefaults(d) {
        var f = $("mi-risk-form"); if (!d || !f) return;
        ["side", "entry", "stop", "target", "balance", "risk_pct"].forEach(function (k) {
            var inp = f.elements[k]; if (inp && !inp.dataset.touched) inp.value = k === "side" ? d[k] : +Number(d[k]).toPrecision(8);
        });
        $("mi-risk-hint").textContent = d.note;
    }
    function renderRisk(r) {
        var t = clear($("mi-risk"));
        var rows = [["Quantity", num(r.quantity, 4) + " " + r.quantity_unit + (r.lots ? " (" + num(r.lots, 3) + " lots)" : "")],
            ["Notional", money(r.notional_usd)], ["Planned risk (gross, to stop)", money(r.planned_risk_usd)],
            ["Reward : risk (gross)", r.rr_gross ? num(r.rr_gross, 2) + " : 1" : "—"], ["Reward : risk (net of costs)", r.rr_net ? num(r.rr_net, 2) + " : 1" : "—"],
            ["Risk applied", num(r.assumptions.risk_pct_applied, 2) + " %" + (r.assumptions.risk_cap_note ? " — " + r.assumptions.risk_cap_note : "")]];
        if (r.stop_in_atr) rows.push(["Stop distance", num(r.stop_in_atr, 2) + " × ATR(14)"]);
        t.appendChild(el("table", { class: "mi-table", style: "margin-top:10px" }, [el("tbody", null, rows.map(function (x) { return el("tr", null, [el("td", { text: x[0] }), el("td", { class: "num", text: x[1] })]); }))]));
        var ob = el("tbody");
        ["target", "stop"].forEach(function (k) {
            var o = r.outcomes[k];
            ob.appendChild(el("tr", null, [el("td", { text: "At " + k }), el("td", { class: "num " + cls(o.gross_usd), text: money(o.gross_usd) }),
                el("td", { class: "num", text: money(o.fees_usd + o.slippage_usd + o.spread_usd) }), el("td", { class: "num " + cls(o.net_usd), text: money(o.net_usd) })]));
        });
        t.appendChild(el("table", { class: "mi-table", style: "margin-top:8px" }, [el("thead", null, [el("tr", null, ["Scenario", "Gross", "Costs", "Net"].map(function (h) { return el("th", { text: h }); }))]), ob]));
        var lab = r.cost_labels;
        t.appendChild(el("div", { class: "mi-note" }, [tag(r.costs_measured ? "verified" : "warn", r.costs_measured ? "partly measured" : "estimated"),
            " Fees: " + lab.fee + ". Slippage: " + lab.slippage + ". Spread: " + lab.spread + "."]));
        if (r.adverse_context) {
            var a = r.adverse_context;
            t.appendChild(el("div", { class: "mi-block verified" }, ["In this sample, price moved " + num(a.distance_pct, 2) + " % against a " +
                r.assumptions.side.toLowerCase() + " within " + a.horizon_bars + " bars in " +
                num(r.assumptions.side === "LONG" ? a.long_stop_hit_rate_pct : a.short_stop_hit_rate_pct, 1) + " % of " + a.samples + " starting points. " + a.note + "."]));
        }
        t.appendChild(el("p", { class: "mi-note", text: r.note + " Sizing: " + r.sizer + ". FX conversion: " + r.assumptions.conversion + "." }));
    }
    var form = $("mi-risk-form");
    if (form) {
        form.addEventListener("input", function (e) { if (e.target.name) e.target.dataset.touched = "1"; });
        form.addEventListener("submit", function (e) {
            e.preventDefault();
            var p = new URLSearchParams({ tf: state.tf });
            ["side", "entry", "stop", "target", "balance", "risk_pct"].forEach(function (k) { p.set(k, form.elements[k].value.trim()); });
            skeleton($("mi-risk"), 3);
            getJSON(D.riskUrl + "?" + p.toString()).then(renderRisk).catch(function (er) { errorBox($("mi-risk"), "Scenario not calculated: " + er.message); });
        });
    }

    // ── AI news ──────────────────────────────────────────────────────────────────────────────────────────────
    var NEWS_TEXT = {
        NOT_CONFIGURED: ["info", "AI news intelligence is not configured on this server."],
        PENDING: ["info", "An analysis for this asset is in progress…"],
        RATE_LIMITED: ["", "The AI budget or rate limit was reached."],
        INSUFFICIENT_EVIDENCE: ["info", "Insufficient evidence."],
        DECLINED: ["", "Claude declined this request."],
        TIMEOUT: ["bad", "The AI request timed out."], UNAVAILABLE: ["bad", "The AI service could not be reached."],
        ERROR: ["bad", "The AI service returned an error."], INVALID_AI_RESPONSE: ["bad", "The AI response failed validation and was discarded."],
        FORBIDDEN: ["", "Sign in to request a fresh analysis."]
    };
    function loadNews(force) {
        skeleton($("mi-news"), 3);
        getJSON(D.newsUrl + (force ? "?refresh=1" : "")).then(renderNews).catch(function (e) {
            if (e.body && e.body.status) renderNews(e.body); else errorBox($("mi-news"), "News could not be loaded (" + e.message + ").", function () { loadNews(false); });
        });
    }
    function renderNews(j) {
        var t = clear($("mi-news"));
        var st = j.status || "ERROR", info = NEWS_TEXT[st];
        if (info && st !== "INSUFFICIENT_EVIDENCE") t.appendChild(el("div", { class: "mi-banner " + info[0] }, [el("strong", { text: info[1] + " " }), j.message || ""]));
        if (st === "PENDING") setTimeout(function () { loadNews(false); }, 8000);
        if (st === "INSUFFICIENT_EVIDENCE") t.appendChild(el("div", { class: "mi-banner info" }, [el("strong", { text: "Insufficient evidence. " }), j.message || j.overall_assessment || ""]));
        if (j.overall_assessment && st === "OK") t.appendChild(el("div", { class: "mi-block ai" }, [tag("ai", "AI interpretation"), " ", el("span", { text: j.overall_assessment })]));
        (j.items || []).forEach(function (it) {
            var head = el("h3", null, [link(it.url, it.headline)]);
            var meta = el("div", { class: "mi-sub" }, [it.publisher + (it.published ? " · " + it.published : "") + " · ", tag(it.status === "NEW" ? "fact" : it.status === "POSSIBLE_DUPLICATE" ? "warn" : "edu", (it.status || "").replace("_", " "))]);
            var facts = el("div", { class: "mi-block fact" }, [tag("fact", "Observed facts"), el("ul", null, it.observed_facts.map(function (f) { return el("li", { text: f }); }))]);
            var sev = (j.severity_definitions || {})[it.severity] || "";
            var ai = el("div", { class: "mi-block ai" }, [tag("ai", "AI interpretation"), " ",
                el("span", { class: "mi-sub", text: "direction " + it.impact_direction + " · severity " + it.severity + " · confidence " + it.confidence, title: "severity " + it.severity + ": " + sev + ". " + (j.confidence_definition || "") }),
                el("p", { text: it.ai_interpretation }), el("p", { class: "mi-note", text: "Reasoning: " + it.reasoning }),
                it.alternative_interpretations.length ? el("div", null, [el("div", { class: "mi-note", text: "Alternative interpretations:" }), el("ul", null, it.alternative_interpretations.map(function (a) { return el("li", { text: a }); }))]) : null,
                it.research_questions.length ? el("div", null, [el("div", { class: "mi-note", text: "Research questions / risk checks:" }), el("ul", null, it.research_questions.map(function (a) { return el("li", { text: a }); }))]) : null,
                it.affected_assets.length ? el("div", { class: "mi-note", text: "Possibly affected: " + it.affected_assets.join(", ") }) : null]);
            var srcs = el("div", { class: "mi-note" }, ["Sources: "].concat(it.sources.map(function (s, i) { return el("span", null, [i ? " · " : "", link(s.url, s.publisher)]); })));
            t.appendChild(el("article", { class: "mi-news" }, [head, meta, facts, ai, srcs]));
        });
        var foot = [];
        if (j.model) foot.push("Model " + j.model);
        if (j.generated_at) foot.push((j.cached ? "cached · " : "") + "generated " + ago(new Date(j.generated_at * 1000).toISOString()));
        if (j.dropped_items) foot.push(j.dropped_items + " AI item(s) discarded for citing no valid source");
        t.appendChild(el("p", { class: "mi-note", text: foot.join(" · ") + (foot.length ? ". " : "") +
            "News and AI text are not verified by the platform; numbers on this page come only from the system-verified sections. Proximity in time between news and a price move is not evidence of cause." }));
        if (j.domains) t.appendChild(el("details", { class: "mi-note" }, [el("summary", { text: "Allowed sources (" + j.domains.length + ")" }), el("div", { text: j.domains.join(", ") })]));
    }
    var rf = $("mi-news-refresh"); if (rf) rf.addEventListener("click", function () { loadNews(true); });

    // ── research evidence ────────────────────────────────────────────────────────────────────────────────────
    var CLASS_TAG = { CANONICAL: "verified", LEGACY_DESCRIPTIVE: "edu", SUPERSEDED: "warn", INVALIDATED: "bad", UNVERIFIED: "warn" };
    function loadResearch() {
        getJSON(D.researchUrl).then(function (j) {
            var t = clear($("mi-research"));
            t.appendChild(el("div", { class: "mi-banner", text: j.headline }));
            if (!j.entries.length) t.appendChild(el("p", { class: "mi-note", text: j.empty_message }));
            j.entries.forEach(function (e) {
                var tb = el("tbody", null, e.metrics.map(function (m) { return el("tr", null, [el("td", { text: m.label }), el("td", { class: "num", text: typeof m.value === "number" ? num(m.value, 3) : String(m.value) })]); }));
                t.appendChild(el("div", { class: "mi-news" }, [
                    el("h3", { text: e.strategy }),
                    el("div", { class: "mi-sub" }, [tag(CLASS_TAG[e.evidence_class] || "warn", e.evidence_label || e.evidence_class, e.evidence_reason), " ", tag("edu", e.result_type), " sample size: " + (e.sample_size === null || e.sample_size === undefined ? "—" : e.sample_size)]),
                    el("table", { class: "mi-table", style: "margin-top:6px" }, [tb]),
                    el("p", { class: "mi-note", text: "Validation: " + e.validation + ". " + e.limitations + " Evidence note: " + (e.evidence_reason || "") + " Artifact: " + e.artifact }) ]));
            });
            t.appendChild(el("p", { class: "mi-note", text: j.live_results + " Past results do not guarantee future returns." }));
        }).catch(function (e) { errorBox($("mi-research"), "Research evidence could not be loaded (" + e.message + ").", loadResearch); });
    }

    // ── history lab ──────────────────────────────────────────────────────────────────────────────────────────
    function miniChart(canvas, series, color) {
        if (typeof Chart === "undefined") return;
        new Chart(canvas, { type: "line", data: { datasets: [{ data: series.map(function (p) { return { x: p[0], y: p[1] }; }), borderColor: color, borderWidth: 1.5, pointRadius: 0 }] },
            options: { responsive: true, maintainAspectRatio: false, animation: false, plugins: { legend: { display: false } },
                scales: { x: { type: "time", ticks: { color: css("--text-dim"), maxTicksLimit: 6, font: { size: 10 } }, grid: { color: css("--border") } },
                          y: { ticks: { color: css("--text-dim"), font: { size: 10 } }, grid: { color: css("--border") } } } } });
    }
    var hs = $("mi-h-start");
    if (hs) hs.addEventListener("click", function () {
        var t = $("mi-history"); skeleton(t, 3); state.hyp = null;
        var p = new URLSearchParams({ date: $("mi-h-date").value + ":00Z", horizon: $("mi-h-horizon").value });
        getJSON(D.historyStartUrl + "?" + p.toString()).then(function (j) {
            state.histToken = j.token; clear(t);
            var cv = el("canvas", { "aria-label": "Closes up to the observation time", role: "img" });
            t.appendChild(el("div", { class: "mi-block verified", style: "margin-top:10px" }, [tag("verified", "known at the time"), " Observation at " + new Date(j.observed_at).toUTCString().slice(5, 22) + " UTC · last close " + num(j.last_close) +
                " · 24h " + pct(j.change_24h_pct) + " · 7d " + pct(j.change_7d_pct) + " · regime " + ((j.regime && j.regime.regime) || "n/a") + " (" + ((j.regime && j.regime.agreement) || "") + ")"]));
            t.appendChild(el("div", { class: "mi-chart sm" }, [cv])); miniChart(cv, j.closes, css("--accent"));
            var hyp = el("div", { class: "mi-hyp", role: "group", "aria-label": "Your hypothesis" }, ["Your hypothesis for the next " + j.horizon_bars + " h: "]);
            ["up", "down", "flat"].forEach(function (h) {
                var b = el("button", { class: "mi-btn ghost", type: "button", "aria-pressed": "false", text: h + (h === "flat" ? " (±" + j.flat_band_pct + " %)" : "") });
                b.addEventListener("click", function () { state.hyp = h; hyp.querySelectorAll("button").forEach(function (x) { x.setAttribute("aria-pressed", "false"); }); b.setAttribute("aria-pressed", "true"); rv.disabled = false; });
                hyp.appendChild(b);
            });
            var rv = el("button", { class: "mi-btn", type: "button", disabled: "disabled", text: "Record & reveal what happened" });
            t.appendChild(hyp); t.appendChild(rv);
            t.appendChild(el("ul", { class: "mi-note" }, j.limitations.map(function (l) { return el("li", { text: l }); })));
            var out = el("div", { "aria-live": "polite" }); t.appendChild(out);
            rv.addEventListener("click", function () {
                rv.disabled = true;
                fetch(D.historyRevealUrl, { method: "POST", credentials: "same-origin", headers: { "Content-Type": "application/json", "X-CSRFToken": D.csrf },
                    body: JSON.stringify({ token: state.histToken, hypothesis: state.hyp }) })
                    .then(function (r) { return r.json().then(function (x) { if (!r.ok) throw new Error(x.error || r.status); return x; }); })
                    .then(function (x) {
                        var c2 = el("canvas", { role: "img", "aria-label": "What happened next" });
                        clear(out).appendChild(el("div", { class: "mi-block " + (x.matched ? "verified" : "ai"), style: "margin-top:10px" }, [
                            tag(x.matched ? "verified" : "warn", x.matched ? "hypothesis matched" : "hypothesis missed"),
                            " You said " + x.hypothesis + "; price went " + x.outcome + " (" + pct(x.move_pct) + "; best " + pct(x.max_up_pct) + ", worst " + pct(x.max_down_pct) + ").",
                            el("p", { text: "What this shows: " + x.what_it_shows }), el("p", { text: "What it does not show: " + x.what_it_does_not_show })]));
                        out.appendChild(el("div", { class: "mi-chart sm" }, [c2])); miniChart(c2, x.path, x.move_pct >= 0 ? css("--positive") : css("--negative"));
                    }).catch(function (e) { errorBox(out, "Could not reveal: " + e.message); });
            });
        }).catch(function (e) { errorBox(t, e.message); });
    });

    // ── controls ─────────────────────────────────────────────────────────────────────────────────────────────
    document.querySelectorAll(".mi-seg button").forEach(function (b) {
        b.setAttribute("aria-pressed", b.dataset.tf === state.tf ? "true" : "false");
        b.addEventListener("click", function () {
            state.tf = b.dataset.tf;
            document.querySelectorAll(".mi-seg button").forEach(function (x) { x.setAttribute("aria-pressed", x === b ? "true" : "false"); });
            try { history.replaceState(null, "", "?tf=" + state.tf); } catch (e) {}
            loadCore();
        });
    });
    var sw = $("mi-switch");
    if (sw) sw.addEventListener("change", function () {
        var v = sw.value;
        if (/^(crypto|forex)\/[A-Z0-9]{2,12}_[A-Z0-9]{2,6}$/.test(v)) location.href = D.workspaceBase + v + "/?tf=" + state.tf;
    });

    function start() { loadCore(); loadNews(false); loadResearch(); setInterval(loadCore, 60000); }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
