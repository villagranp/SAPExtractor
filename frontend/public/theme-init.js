// Aplica tema y densidad guardados antes del primer pintado (sin parpadeo).
// Archivo externo porque la CSP no admite scripts en línea. Ver src/shared/theme/preferences.ts.
(function () {
  try {
    var prefs = JSON.parse(window.localStorage.getItem('sx-preferences') || '{}');
    var root = document.documentElement;
    if (prefs.theme === 'light' || prefs.theme === 'dark')
      root.setAttribute('data-theme', prefs.theme);
    if (prefs.density === 'compact') root.setAttribute('data-density', 'compact');
  } catch (e) {
    // sin almacenamiento: tema del sistema y densidad cómoda
  }
})();
