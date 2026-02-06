// Dark / Light mode toggle — runs immediately to avoid FOUC
(function () {
    var STORAGE_KEY = 'irc-chat-theme';

    var SUN_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="5"/><line x1="12" y1="1" x2="12" y2="3"/><line x1="12" y1="21" x2="12" y2="23"/><line x1="4.22" y1="4.22" x2="5.64" y2="5.64"/><line x1="18.36" y1="18.36" x2="19.78" y2="19.78"/><line x1="1" y1="12" x2="3" y2="12"/><line x1="21" y1="12" x2="23" y2="12"/><line x1="4.22" y1="19.78" x2="5.64" y2="18.36"/><line x1="18.36" y1="5.64" x2="19.78" y2="4.22"/></svg>';

    var MOON_SVG = '<svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z"/></svg>';

    function getPreference() {
        var stored = localStorage.getItem(STORAGE_KEY);
        if (stored) return stored;
        return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    }

    function applyTheme(theme) {
        if (theme === 'dark') {
            document.documentElement.classList.add('dark');
        } else {
            document.documentElement.classList.remove('dark');
        }
        localStorage.setItem(STORAGE_KEY, theme);

        var btn = document.getElementById('btn-theme');
        if (btn) {
            btn.innerHTML = theme === 'dark' ? SUN_SVG : MOON_SVG;
            btn.title = theme === 'dark' ? 'Switch to light mode' : 'Switch to dark mode';
        }
    }

    // Apply immediately
    applyTheme(getPreference());

    // Expose for use by other scripts
    window.ThemeManager = {
        toggle: function () {
            var current = document.documentElement.classList.contains('dark') ? 'dark' : 'light';
            applyTheme(current === 'dark' ? 'light' : 'dark');
        },
        apply: applyTheme,
        getPreference: getPreference
    };

    // Bind click handler after DOM ready
    $(function () {
        applyTheme(getPreference());
        $('#btn-theme').on('click', function () {
            window.ThemeManager.toggle();
        });
    });
})();
