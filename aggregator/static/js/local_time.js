// Shows times as "3 hours ago" in the reader's own time zone, with the exact
// local date and time on hover, in the aggregator's DISPLAY_TIMEZONE (passed as
// data-timezone on the script tag; the browser's own zone if missing). Markup: <span class="local-date"
// data-utc="2026-10-04T14:06:00Z">fallback text</span>. Times older than a
// week show the date instead. Refreshes every minute so "just now" ages.
(function () {
    const script = document.currentScript;
    const timeZone = (script && script.dataset.timezone) || undefined;
    const MINUTE = 60 * 1000, HOUR = 60 * MINUTE, DAY = 24 * HOUR;
    const exact = { weekday: 'short', year: 'numeric', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit', timeZoneName: 'short', timeZone: timeZone };
    const dateOnly = { year: 'numeric', month: 'short', day: 'numeric', timeZone: timeZone };

    function relative(date, now) {
        const ago = now - date;
        if (ago < MINUTE) return 'just now';
        if (ago < HOUR) { const m = Math.floor(ago / MINUTE); return m + (m === 1 ? ' minute ago' : ' minutes ago'); }
        if (ago < DAY) { const h = Math.floor(ago / HOUR); return h + (h === 1 ? ' hour ago' : ' hours ago'); }
        if (ago < 2 * DAY) return 'yesterday';
        if (ago < 7 * DAY) return Math.floor(ago / DAY) + ' days ago';
        return date.toLocaleDateString(undefined, dateOnly);
    }

    function render() {
        const now = Date.now();
        document.querySelectorAll('.local-date[data-utc]').forEach(function (el) {
            const date = new Date(el.getAttribute('data-utc'));
            if (isNaN(date)) return;
            el.textContent = relative(date, now);
            el.title = date.toLocaleString(undefined, exact);
            if (el.tagName !== 'TIME') el.setAttribute('aria-label', el.title);
        });
    }

    window.renderLocalTimes = render;
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', render);
    else render();
    setInterval(render, MINUTE);
})();
