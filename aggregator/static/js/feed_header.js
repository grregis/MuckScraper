// The feed's top bar (templates/_feed_topbar.html): live search results under
// the search box, and the Filters popover. Loaded with `defer`.
//
// Results come from /admin/search/suggest as JSON and are built with
// textContent only: titles and outlet names are scraped text, never HTML.
(function () {
    const form = document.querySelector('.ft-search');
    const input = document.getElementById('feed-search');
    const panel = document.getElementById('feed-suggest');
    const filterBtn = document.getElementById('feed-filter-btn');
    const popover = document.getElementById('feed-filters');

    // ── Filters popover ──
    function setPopover(open) {
        if (!filterBtn || !popover) return;
        popover.hidden = !open;
        filterBtn.setAttribute('aria-expanded', String(open));

    }
    if (filterBtn && popover) {
        filterBtn.addEventListener('click', function () { setPopover(popover.hidden); });
        document.addEventListener('click', function (e) {
            if (!popover.hidden && !popover.contains(e.target) && !filterBtn.contains(e.target)) setPopover(false);
        });
        popover.querySelectorAll('a[role="switch"]').forEach(function (el) {
            el.addEventListener('keydown', function (e) { if (e.key === ' ') { e.preventDefault(); el.click(); } });
        });
    }

    document.addEventListener('keydown', function(e) { if(e.key === 'Escape') setPopover(false); });

    // ── Phone search button: slides the search and Filters out below ──
    const bar = document.querySelector('.feed-topbar');
    const searchToggle = document.getElementById('ft-search-toggle');
    if (bar && searchToggle) {
        searchToggle.addEventListener('click', function () {
            const open = !bar.classList.contains('search-open');
            bar.classList.toggle('search-open', open);
            searchToggle.setAttribute('aria-expanded', String(open));
            if (open && input) input.focus();
            else { setPopover(false); }
        });
    }



})();
// The ⋯ menu on compact story cards (story_card_macros.html): one open at a
// time; a click outside or Escape closes it.
(function () {
    let openMenu = null;
    function setMenu(menu, open) {
        const btn = menu.querySelector('.card-menu-btn');
        const pop = menu.querySelector('.card-menu-pop');
        pop.hidden = !open;
        btn.setAttribute('aria-expanded', String(open));
        // Lift the card so the menu covers the cards below it.
        menu.closest('.story').classList.toggle('menu-open', open);
        // Open upward when there is no room below and there is room above.
        pop.classList.remove('up');
        if (open) {
            const r = pop.getBoundingClientRect();
            if (r.bottom > window.innerHeight && btn.getBoundingClientRect().top > r.height + 8) pop.classList.add('up');
        }
        openMenu = open ? menu : null;
    }
    document.addEventListener('click', function (e) {
        const btn = e.target.closest('.card-menu-btn');
        if (btn) {
            const menu = btn.closest('.card-menu');
            const wasOpen = menu === openMenu;
            if (openMenu) setMenu(openMenu, false);
            if (!wasOpen) {
                setMenu(menu, true);
                const first = menu.querySelector('.card-menu-item');
                if (first) first.focus();
            }
            return;
        }
        if (openMenu && !openMenu.contains(e.target)) setMenu(openMenu, false);
    });
    document.addEventListener('keydown', function (e) {
        if (e.key !== 'Escape' || !openMenu) return;
        const btn = openMenu.querySelector('.card-menu-btn');
        setMenu(openMenu, false);
        btn.focus();
    });
})();
