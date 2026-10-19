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

// iOS keeps fixed elements under the on-screen keyboard: track its height as --kb and raise the pill.
if (window.visualViewport) {
  const vv = window.visualViewport;
  const update = () => {
    const kb = Math.max(0, window.innerHeight - vv.height - vv.offsetTop);
    document.documentElement.style.setProperty('--kb', kb + 'px');
    document.body.classList.toggle('kb-open', kb > 80);
  };
  vv.addEventListener('resize', update);
  vv.addEventListener('scroll', update);
}
