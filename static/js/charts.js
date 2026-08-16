// ================= DASHBOARD ENGINE =================
document.addEventListener("DOMContentLoaded", function () {

    // ================= CHARTS =================
    try {
        const equityData = window.equityData || [];
        const drawdownData = window.drawdownData || [];

        const equityCtx = document.getElementById('equityChart');
        const drawdownCtx = document.getElementById('drawdownChart');

        if (equityCtx && equityData.length > 0) {
            new Chart(equityCtx, {
                type: 'line',
                data: {
                    labels: equityData.map((_, i) => i + 1),
                    datasets: [{
                        label: 'Equity Curve',
                        data: equityData,
                        borderWidth: 2,
                    }]
                }
            });
        }

        if (drawdownCtx && drawdownData.length > 0) {
            new Chart(drawdownCtx, {
                type: 'line',
                data: {
                    labels: drawdownData.map((_, i) => i + 1),
                    datasets: [{
                        label: 'Drawdown %',
                        data: drawdownData,
                        borderWidth: 2,
                    }]
                }
            });
        }
    } catch (err) {
        console.error("Chart error:", err);
    }

    // ================= COUNTERS =================
    try {
        function formatNumber(num, decimals) {                          // claude code changed: moved above animateCounter so it's in scope for repeat calls too
            if (!isFinite(num)) return "0";
            return Number(num).toFixed(decimals);
        }

        // claude code changed: extracted from the old forEach body into a standalone function
        // so the same count-up animation can be re-triggered later (see the repeat-every-5s
        // block below), not just once on page load.
        function animateCounter(counter) {
            const target = parseFloat(counter.getAttribute("data-target")) || 0;
            const decimals = parseInt(counter.getAttribute("data-decimals") || "0", 10);

            const duration = 1000;
            const startTime = performance.now();

            function animate(now) {
                const progress = Math.min((now - startTime) / duration, 1);
                const current = target * progress;

                counter.textContent = formatNumber(current, decimals);

                if (progress < 1) {
                    requestAnimationFrame(animate);
                } else {
                    counter.textContent = formatNumber(target, decimals);
                }
            }

            requestAnimationFrame(animate);
        }

        const counters = document.querySelectorAll(".stat-number");
        counters.forEach(animateCounter);   // claude code changed: was an inline forEach body — now calls the extracted function once on load, same as before

        // claude code changed: re-plays the count-up animation for every .stat-number
        // element every 5 seconds, per user request — this now includes the Market
        // Intelligence / Structure Analysis / Rejection Analysis tables and the trade
        // table's PnL/R/Held columns, not just the summary cards. Purely a visual
        // repeat of the existing animation (same data-target values already in the
        // DOM); it does not re-fetch data from the server.
        // claude code changed: was a hardcoded list of 12 summary-card ids. Narrowing to
        // named ids was a mistake — those id-less table cells used to APPEAR to animate
        // repeatedly too, but only as a side effect of the old real_time.js's buggy
        // live-poll error handler re-triggering its own separate id-less-only animation
        // on every failed/slow fetch. Removing that script (see dashboard.html) removed
        // the accidental repeat along with it. Re-querying all .stat-number elements
        // here reproduces the same visual effect deliberately, in the one animation
        // system that's actually confirmed working.
        setInterval(function () {
            document.querySelectorAll(".stat-number").forEach(animateCounter);
        }, 5000);

    } catch (err) {
        console.error("Counter error:", err);
    }

});