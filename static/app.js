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

// A thin line at the top while the next screen loads (html.nav-pending in app.css): the installed iPhone app
// shows no loading indicator of its own, and a screen takes a round trip to the server. Links of this app,
// and, where the browser has the Navigation API, navigations started by the server (ui.navigate).
// Not for downloads (ui.download clicks a temporary <a download>), new tabs, or changes within the page
// (history.replaceState in the library); and it goes away by itself if no screen comes after all.
let pendingTimer = 0;
const settled = () => { clearTimeout(pendingTimer); document.documentElement.classList.remove('nav-pending'); };
const pending = () => {
  document.documentElement.classList.add('nav-pending');
  clearTimeout(pendingTimer);
  pendingTimer = setTimeout(settled, 15000);
};
document.addEventListener('click', e => {
  const a = e.target.closest && e.target.closest('a[href]');
  if (!a || a.target === '_blank' || a.hasAttribute('download') || e.metaKey || e.ctrlKey || e.shiftKey) return;
  const url = new URL(a.href, location.href);
  if (url.origin === location.origin && !(url.pathname === location.pathname && url.search === location.search)) pending();
});
if (window.navigation) {
  navigation.addEventListener('navigate', e => { if (!e.destination.sameDocument && e.downloadRequest === null) pending(); });
}
addEventListener('pageshow', settled);  // a page restored by «Назад» from the browser's cache
document.addEventListener('visibilitychange', () => { if (document.hidden) settled(); });

// iOS applies :active (the tap feedback in app.css) only when the page listens to touches.
document.addEventListener('touchstart', () => {}, {passive: true});

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

// Calls back once the page (rendered by Vue after this script) has an element matching the selector.
const whenPresent = (selector, callback) => {
  const found = () => document.querySelector(selector);
  if (found()) return callback(found());
  const watcher = new MutationObserver(() => { if (found()) { watcher.disconnect(); callback(found()); } });
  watcher.observe(document.documentElement, {childList: true, subtree: true});
  setTimeout(() => watcher.disconnect(), 15000);
};

// Report: «3 / 9 ▾» follows the scroll; deeperScrollTo(i) goes to the i-th section, deeperScrollTo('#id') to an anchor.
window.deeperScrollTo = target => {
  const el = typeof target === 'number' ? document.querySelectorAll('.report.main h2')[target] : document.querySelector(target);
  if (el) el.scrollIntoView({behavior: 'smooth', block: 'start'});
};
// The current section is the last heading above a line at 30% of the screen. The headings' positions are
// measured once and again only when the text changes size (fonts, the window), so a scroll frame only compares
// numbers: no layout work while scrolling, and a jump across several sections still lands on the right one.
whenPresent('.report.main h2', () => {
  const label = document.querySelector('.toc-label');
  const report = document.querySelector('.report.main');
  const headings = [...report.querySelectorAll('h2')];
  if (!label) return;
  let tops = [], frame = 0;
  const show = () => {
    frame = 0;
    const line = scrollY + innerHeight * 0.3;
    let current = 0;
    while (current + 1 < tops.length && tops[current + 1] < line) current++;
    const text = `${current + 1} / ${headings.length} ▾`;
    if (label.textContent !== text) label.textContent = text;
  };
  const measure = () => { tops = headings.map(h => h.getBoundingClientRect().top + scrollY); show(); };
  addEventListener('scroll', () => { if (!frame) frame = requestAnimationFrame(show); }, {passive: true});
  new ResizeObserver(measure).observe(report);
  addEventListener('resize', measure);
  measure();
});

// Job page: the percent counts up to each new value with the bar under it (.6 s) instead of jumping. The
// server's text is the value; the steps are written into the same text node, which Vue keeps updating.
whenPresent('.pct', el => {
  if (matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  let shown = Number(el.textContent) || 0, written = el.textContent, frame = 0;
  const put = value => { written = String(value); if (el.firstChild) el.firstChild.nodeValue = written; };
  new MutationObserver(() => {
    const text = el.textContent, target = Number(text);
    if (text === written) return;  // a step of ours
    if (!text || !Number.isFinite(target)) { written = text; return; }
    const from = shown, start = performance.now();
    cancelAnimationFrame(frame);
    put(from);
    const step = now => {
      const t = Math.min(1, (now - start) / 600);
      shown = Math.round(from + (target - from) * (1 - Math.pow(1 - t, 3)));
      put(shown);
      if (t < 1) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
  }).observe(el, {characterData: true, childList: true, subtree: true});
});

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
