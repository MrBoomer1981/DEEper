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

// Report: «3 / 9 ▾» follows the scroll; deeperScrollTo(i) goes to the i-th section, deeperScrollTo('#id') to an anchor.
window.deeperScrollTo = target => {
  const el = typeof target === 'number' ? document.querySelectorAll('.report.main h2')[target] : document.querySelector(target);
  if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
};
(() => {
  let frame = 0;
  const tick = () => {
    frame = 0;
    const label = document.querySelector('.toc-label');
    const headings = label ? [...document.querySelectorAll('.report.main h2')] : [];
    if (!headings.length) return;
    let current = 0;
    headings.forEach((h, i) => { if (h.getBoundingClientRect().top < innerHeight * 0.3) current = i; });
    const text = `${current + 1} / ${headings.length} ▾`;
    if (label.textContent !== text) label.textContent = text;
  };
  const schedule = () => { if (!frame) frame = requestAnimationFrame(tick); };
  addEventListener('scroll', schedule, {passive: true});
  new MutationObserver(schedule).observe(document.documentElement, {childList: true, subtree: true});
})();

// Report: a toolbar for selected text — save, ask, dig deeper (DESIGN_HANDOFF.md §3.5); the page handles 'deeper_selection'.
(() => {
  let bar = null, picked = null, timer = 0;
  const touch = matchMedia('(pointer: coarse)').matches;
  const make = () => {
    bar = document.createElement('div');
    bar.className = 'sel-bar' + (touch ? ' docked' : '');
    for (const [action, label] of [['save', 'Сохранить'], ['ask', 'Спросить'], ['dig', 'Глубже']]) {
      const b = document.createElement('button');
      b.type = 'button'; b.textContent = label;
      b.addEventListener('mousedown', e => e.preventDefault());  // keep the selection
      b.addEventListener('click', () => {
        if (picked && window.emitEvent) emitEvent('deeper_selection', {action, ...picked});
        hide(); document.getSelection().removeAllRanges();
      });
      bar.appendChild(b);
    }
    document.body.appendChild(bar);
  };
  const hide = () => { if (bar) bar.classList.remove('on'); picked = null; };
  const sectionOf = node => {
    let el = node.nodeType === 1 ? node : node.parentElement;
    while (el && el.parentElement && !el.parentElement.classList.contains('main')) el = el.parentElement;
    for (; el; el = el.previousElementSibling) if (el.tagName === 'H2') return el.textContent.trim();
    return '';
  };
  const update = () => {
    const sel = document.getSelection();
    const report = document.querySelector('.report.main');
    if (!report || !sel || !sel.rangeCount || !report.contains(sel.anchorNode)) return hide();
    const copy = sel.getRangeAt(0).cloneContents();
    copy.querySelectorAll('sup').forEach(s => s.remove());  // citation numbers and fact-check marks
    const text = copy.textContent.replace(/\s+/g, ' ').replace(/ ([.,;:!?])/g, '$1').trim();
    if (text.length < 3) return hide();
    if (!bar) make();
    picked = {text: text.slice(0, 2000), section: sectionOf(sel.anchorNode)};
    if (!touch) {
      const r = sel.getRangeAt(0).getBoundingClientRect();
      bar.style.left = Math.max(12, Math.min(innerWidth - 300, r.left + r.width / 2 - 140)) + 'px';
      bar.style.top = Math.max(12, r.top - 56) + 'px';
    }
    bar.classList.add('on');
  };
  document.addEventListener('selectionchange', () => { clearTimeout(timer); timer = setTimeout(update, 220); });
  addEventListener('scroll', () => { if (!touch) hide(); }, {passive: true});
})();
