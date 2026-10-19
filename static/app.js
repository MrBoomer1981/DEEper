/* DEEper web app: small client-side helpers (NiceGUI renders the pages). */
// External links open outside the app (in the Safari web app: in Safari).
document.addEventListener('click', e => {
  const a = e.target.closest && e.target.closest('a[href^="http"]');
  if (a && a.host !== location.host) { a.target = '_blank'; a.rel = 'noopener'; }
}, true);
