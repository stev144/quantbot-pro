// ============================================================
// static/js/markets_live.js
// claude code changed: 2026-10-21 — new. Live display prices for the Markets and Watchlist cards.
//
// Every card carrying data-live-symbol / data-live-class first shows its last CSV close, labelled with its real
// age ("STALE · as of 18 days ago"), then is updated from /markets/live/ (Binance 24 h ticker for crypto, MT5 ticks
// for forex) on load and every 30 s. Display only: the server never stores these prices in data/, so the research
// holdouts stay sealed. A source that is down leaves its cards on the CSV price, still labelled STALE, with the
// reason in the tooltip — a price is never shown without saying how old it is.
//
// Card hooks:  [data-live-symbol][data-live-class][data-decimals][data-asof]
//              .live-price  .live-change  .live-asof  (.live-spark optional — gets a tooltip)
// Script tag:  <script src=".../markets_live.js" data-url="{% url 'markets_live' %}" defer></script>
// ============================================================
(function () {
    var script = document.currentScript;
    var URL_ = script && script.dataset.url;
    var POLL_MS = 30000;
    var STALE_AFTER_S = 2 * 3600;   // older than 2 h = STALE (hourly CSV refresh + slack)
    var status = {};
    var fxSession = null;           // claude code changed: 2026-10-25 — /markets/live/ forex_session (owner FX calendar)

    function cards() { return document.querySelectorAll("[data-live-symbol]"); }

    function ago(iso) {
        var s = (Date.now() - Date.parse(iso)) / 1000;
        if (!isFinite(s)) return "unknown time";
        if (s < 90) return Math.max(0, Math.round(s)) + "s ago";
        if (s < 5400) return Math.round(s / 60) + " min ago";
        if (s < 172800) return Math.round(s / 3600) + " h ago";
        return Math.round(s / 86400) + " days ago";
    }

    function fmtPrice(p, dec) {
        if (p == null || !isFinite(p)) return "—";
        return String(Number(p.toFixed(dec)));
    }

    // claude code changed: 2026-10-25 — one badge rule for paint() and refreshAges(). A forex price that reaches the
    // weekend/holiday close (within 2 h) is MARKET CLOSED, not STALE; data that stopped before the close stay STALE.
    function badge(card, asof, source) {
        var ageS = (Date.now() - Date.parse(asof)) / 1000;
        var where = source === "BINANCE_LIVE" ? "Binance" : source === "MT5_LIVE" ? "MT5" : "saved data";
        if (card.dataset.liveClass === "forex" && fxSession && !fxSession.is_open && fxSession.closed_since &&
            Date.parse(asof) >= Date.parse(fxSession.closed_since) - 2 * 3600 * 1000) {
            var r = new Date(fxSession.reopens);
            var reopens = r.toUTCString().slice(0, 3) + " " + r.toISOString().slice(11, 16) + " UTC";
            return { cls: "closed", text: "MARKET CLOSED", rest: " reopens " + reopens,
                     title: "Forex market closed (weekend / holiday) — last price before the close, " + ago(asof) +
                            " · closed since " + fxSession.closed_since + " · reopens " + fxSession.reopens };
        }
        if (card.dataset.liveClass === "forex" && !fxSession && (!source || source.indexOf("LIVE") < 0)) {
            return { cls: "closed", text: "…", rest: " " + ago(asof) + " · " + where, title: "checking the forex session" };
        }
        var live = source && source.indexOf("LIVE") >= 0 && ageS < STALE_AFTER_S;
        return { cls: live ? "live" : "stale", text: live ? "LIVE" : "STALE", rest: " " + ago(asof) + " · " + where,
                 title: (live ? "Live display price" : "Last saved candle — not live") + " · as of " + asof };
    }

    function paint(card, price, chg, asof, source) {
        var dec = parseInt(card.dataset.decimals || "6", 10);
        var priceEl = card.querySelector(".live-price");
        var chgEl = card.querySelector(".live-change");
        var asofEl = card.querySelector(".live-asof");
        if (priceEl && price != null) priceEl.textContent = fmtPrice(price, dec);
        if (chgEl) {
            if (chg == null || !isFinite(chg)) {
                chgEl.textContent = "24 h change n/a";
                chgEl.classList.remove("pos", "neg");
                chgEl.style.color = "var(--text-dim)";
            } else {
                var up = chg >= 0;
                chgEl.textContent = (up ? "▲ +" : "▼ ") + chg.toFixed(2) + "%";
                chgEl.classList.toggle("pos", up);
                chgEl.classList.toggle("neg", !up);
                chgEl.style.color = up ? "var(--positive)" : "var(--negative)";
            }
        }
        card.dataset.liveAsof = asof;
        card.dataset.liveSource = source || "CSV";
        if (asofEl) {
            var b = badge(card, asof, source);
            var why = b.cls === "live" ? "" : (status[card.dataset.liveClass] || "");
            asofEl.innerHTML = '<span class="live-badge ' + b.cls + '">' + b.text + "</span>" + b.rest;
            asofEl.style.whiteSpace = b.cls === "closed" ? "normal" : "";   // claude code changed: 2026-10-25 — reopen time may wrap, never cut
            asofEl.title = b.title + (why ? " · " + why : "");
        }
    }

    function initial() {
        cards().forEach(function (card) {
            var spark = card.querySelector(".live-spark");
            if (spark) spark.title = "Hourly closes from saved data up to " + card.dataset.asof;
            paint(card, null, NaN, card.dataset.asof, "CSV");
        });
    }

    function refreshAges() {
        cards().forEach(function (card) {
            var el = card.querySelector(".live-asof .live-badge");
            if (!el || !card.dataset.liveAsof) return;
            var b = badge(card, card.dataset.liveAsof, card.dataset.liveSource);
            card.querySelector(".live-asof").innerHTML = '<span class="live-badge ' + b.cls + '">' + b.text + "</span>" + b.rest;
            card.querySelector(".live-asof").style.whiteSpace = b.cls === "closed" ? "normal" : "";
        });
    }

    function poll() {
        if (!URL_) return;
        fetch(URL_, { cache: "no-store" })
            .then(function (r) { return r.ok ? r.json() : Promise.reject(r.status); })
            .then(function (j) {
                status = { crypto: (j.status || {}).crypto || "", forex: (j.status || {}).forex || "" };
                fxSession = j.forex_session || null;   // claude code changed: 2026-10-25
                cards().forEach(function (card) {
                    var q = (j[card.dataset.liveClass] || {})[card.dataset.liveSymbol];
                    if (q) paint(card, q.price, q.change_pct, q.as_of, q.source);
                    else {   // keep the saved price, but say why it is not live
                        var el = card.querySelector(".live-asof");
                        if (el && card.dataset.liveAsof) {   // claude code changed: 2026-10-25 — re-badge only (price/change untouched)
                            var b = badge(card, card.dataset.liveAsof, card.dataset.liveSource);
                            el.innerHTML = '<span class="live-badge ' + b.cls + '">' + b.text + "</span>" + b.rest;
                            el.style.whiteSpace = b.cls === "closed" ? "normal" : "";
                        }
                        if (el) el.title = "Not live: " + (status[card.dataset.liveClass] || "symbol not in the live feed");
                    }
                });
            })
            .catch(function () {   // network hiccup: cards keep their last labelled state
                if (!fxSession) { fxSession = { is_open: true }; refreshAges(); }   // claude code changed: 2026-10-25 — session unknown → plain STALE, never a stuck "…"
            });
    }

    function start() {
        initial();
        poll();
        setInterval(poll, POLL_MS);
        setInterval(refreshAges, 10000);
    }
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", start); else start();
})();
