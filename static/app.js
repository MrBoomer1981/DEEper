/* DEEper web app: small client-side helpers (NiceGUI renders the pages). */
// External links open outside the app (in the Safari web app: in Safari).
document.addEventListener('click', e => {
  const a = e.target.closest && e.target.closest('a[href^="http"]');
  if (a && a.host !== location.host) { a.target = '_blank'; a.rel = 'noopener'; }
}, true);

// «Назад»: the previous screen of this app when there is one, otherwise the link's own target.
document.addEventListener('click', e => {
  const back = e.target.closest && e.target.closest('.js-back');
  if (back && document.referrer.startsWith(location.origin) && history.length > 1) { e.preventDefault(); history.back(); }
});
