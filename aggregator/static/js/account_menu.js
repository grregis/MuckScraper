// Opens and closes the account menu (templates/account_menu.html).
// Closes on a click outside it and on Escape.
(function () {
    document.querySelectorAll('.account-menu').forEach(function (menu) {
        var button = menu.querySelector('.account-button');
        var dropdown = menu.querySelector('.account-dropdown');
        function setOpen(open) {
            dropdown.hidden = !open;
            button.setAttribute('aria-expanded', open ? 'true' : 'false');
        }
        button.addEventListener('click', function (event) {
            event.stopPropagation();
            setOpen(dropdown.hidden);
        });
        document.addEventListener('click', function (event) {
            if (!menu.contains(event.target)) setOpen(false);
        });
        document.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && !dropdown.hidden) { setOpen(false); button.focus(); }
        });
    });
})();
