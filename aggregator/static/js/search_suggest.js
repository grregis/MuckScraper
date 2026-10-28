// Live search extracted from dev feed_header.js. Scraped text uses textContent.
(function () {
    const form = document.querySelector('.ft-search');
    const input = document.getElementById('feed-search');
    const panel = document.getElementById('feed-suggest');
    if (!form || !input || !panel || !form.dataset.suggestUrl) return;

    // ── Live results ──
    const DEBOUNCE_MS = 180;
    const MIN_CHARS = 2;
    let timer = 0;
    let requestSeq = 0;
    let activeIndex = -1;

    function el(tag, className, text) {
        const node = document.createElement(tag);
        if (className) node.className = className;
        if (text !== undefined && text !== null) node.textContent = text;
        return node;
    }

    // L / C / R counts, then the bar at the far right.
    function lcr(left, center, right) {
        const total = left + center + right;
        const wrap = el('span', 'lcr ft-right-group');
        wrap.setAttribute('aria-label', 'Left ' + left + ', center ' + center + ', right ' + right);
        wrap.append(el('span', 'lcr-l', 'L ' + left), el('span', 'lcr-c', 'C ' + center), el('span', 'lcr-r', 'R ' + right));
        const bar = el('span', 'lcr-bar');
        bar.setAttribute('aria-hidden', 'true');
        [['seg-l', left], ['seg-c', center], ['seg-r', right]].forEach(function (pair) {
            const seg = el('span', pair[0]);
            seg.style.width = (total ? (pair[1] / total) * 100 : 0) + '%';
            bar.append(seg);
        });
        wrap.append(bar);
        return wrap;
    }

    function hitRow(item, metaNodes) {
        const link = el('a', 'ft-hit');
        link.href = item.url;
        link.setAttribute('role', 'option');
        link.id = 'ft-hit-' + Math.random().toString(36).slice(2, 9);
        const body = el('span', 'ft-hit-body');
        const top = el('span', 'ft-hit-top');
        top.append(el('span', 'ft-hit-title', item.title));
        if (item.date) top.append(el('span', 'ft-hit-date', item.date));
        const meta = el('span', 'ft-hit-meta');
        metaNodes.forEach(function (node) { meta.append(node); });
        body.append(top, meta);
        link.append(body);
        return link;
    }

    function render(data) {
        panel.replaceChildren();
        activeIndex = -1;
        const stories = data.stories || [];
        const articles = data.articles || [];
        if (stories.length) {
            panel.append(el('div', 'ft-group-label', 'Stories'));
            const group = el('div', 'ft-hits');
            stories.forEach(function (s) {
                const counts = s.articles + ' article' + (s.articles === 1 ? '' : 's') + ' · ' + s.sources + ' outlet' + (s.sources === 1 ? '' : 's');
                group.append(hitRow(s, [el('span', null, counts), lcr(s.left, s.center, s.right)]));
            });
            panel.append(group);
        }
        if (articles.length) {
            panel.append(el('div', 'ft-group-label', 'Articles'));
            const group = el('div', 'ft-hits');
            articles.forEach(function (a) {
                group.append(hitRow(a, [el('span', null, a.outlet), el('span', 'ft-lean ' + a.lean_class, a.lean)]));
            });
            panel.append(group);
        }
        if (!stories.length && !articles.length) {
            panel.append(el('div', 'ft-empty', 'No stories or articles match “' + data.q + '”.'));
        }
        const foot = el('div', 'ft-suggest-foot');
        foot.append(el('span', null, '↑ ↓ to move · Enter to open · Esc to close'));
        if (data.all_url) {
            const all = el('a', null, data.story_total > 1 ? 'See all ' + data.story_total + ' stories →' : 'Open full search →');
            all.href = data.all_url;
            foot.append(all);
        }
        panel.append(foot);
        openSuggest();
    }

    function openSuggest() {
        panel.hidden = false;
        input.setAttribute('aria-expanded', 'true');

    }
    function closeSuggest() {
        clearTimeout(timer);
        requestSeq++;
        panel.hidden = true;
        input.setAttribute('aria-expanded', 'false');
        input.removeAttribute('aria-activedescendant');
        activeIndex = -1;
    }

    function fetchSuggestions() {
        const q = input.value.trim();
        if (q.length < MIN_CHARS) { closeSuggest(); return; }
        const seq = ++requestSeq;
        const params = new URLSearchParams({ q: q });
        ['range', 'start', 'end'].forEach(function (key) {
            const control = form.elements[key];
            const value = control ? control.value : form.dataset[key];
            if (value) params.set(key, value);
        });
        fetch(form.dataset.suggestUrl + '?' + params.toString(), { headers: { 'Accept': 'application/json' } })
            .then(function (r) { return r.ok ? r.json() : null; })
            .then(function (data) {
                // Ignore a slow answer to an older query.
                if (data && seq === requestSeq && input.value.trim() === q) render(data);
            })
            .catch(function () {
                if (seq === requestSeq) closeSuggest();
            });
    }

    form.addEventListener('change', closeSuggest);
    const filter = document.getElementById('feed-filter-btn');
    if (filter) filter.addEventListener('click', closeSuggest);
    const phoneToggle = document.getElementById('ft-search-toggle');
    if (phoneToggle) phoneToggle.addEventListener('click', function () {
        if (!form.closest('.feed-topbar').classList.contains('search-open')) closeSuggest();
    });
    form.addEventListener('focusout', function () {
        setTimeout(function () { if (!form.contains(document.activeElement)) closeSuggest(); }, 0);
    });

    function hits() { return Array.prototype.slice.call(panel.querySelectorAll('.ft-hit')); }
    function highlight(index) {
        const list = hits();
        list.forEach(function (h) { h.classList.remove('is-active'); h.removeAttribute('aria-selected'); });
        if (!list.length) return;
        activeIndex = (index + list.length) % list.length;
        const current = list[activeIndex];
        current.classList.add('is-active');
        current.setAttribute('aria-selected', 'true');
        input.setAttribute('aria-activedescendant', current.id);
        current.scrollIntoView({ block: 'nearest' });
    }

    input.addEventListener('input', function () {
        closeSuggest();
        timer = setTimeout(fetchSuggestions, DEBOUNCE_MS);
    });
    input.addEventListener('focus', function () {
        if (input.value.trim().length >= MIN_CHARS) fetchSuggestions();
    });
    input.addEventListener('keydown', function (e) {
        if (e.key === 'ArrowDown') { e.preventDefault(); if (panel.hidden) fetchSuggestions(); else highlight(activeIndex + 1); }
        else if (e.key === 'ArrowUp') { e.preventDefault(); if (!panel.hidden) highlight(activeIndex - 1); }
        else if (e.key === 'Escape') { closeSuggest(); }
        else if (e.key === 'Enter' && !panel.hidden && activeIndex >= 0) {
            // Open the highlighted result; plain Enter still submits the search.
            e.preventDefault();
            window.location.href = hits()[activeIndex].href;
        }
    });
    document.addEventListener('click', function (e) {
        if (!form.contains(e.target)) closeSuggest();
    });

    // "/" focuses search from anywhere but a text field.
    document.addEventListener('keydown', function (e) {
        if (e.key !== '/' || e.metaKey || e.ctrlKey || e.altKey) return;
        const t = e.target;
        if (t && (t.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName))) return;
        e.preventDefault();
        const bar = form.closest('.feed-topbar');
        bar.classList.add('search-open');
        const toggle = document.getElementById('ft-search-toggle');
        if (toggle) toggle.setAttribute('aria-expanded', 'true');
        input.focus();
        input.select();
    });
})();
