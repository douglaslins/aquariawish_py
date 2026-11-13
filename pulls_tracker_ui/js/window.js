/* Window controls for the frameless pywebview window (minimize, close, the eight resize handles).
   Shown when the Python application runs the page (PT_API.mode === 'backend'), or in a browser with ?frameless=1 to preview them.
   ?frameless=0 hides them (for a window that has its own frame). The buttons call PT_API.windowMinimize() and PT_API.windowClose(),
   which the backend implements with the pywebview window (window.minimize(), window.destroy()). */
(function () {
  'use strict';
  const A = window.PT_APP, API = window.PT_API, U = window.PT_UI, P = () => window.PT_PANELS;
  const NS = 'http://www.w3.org/2000/svg';

  const icon = (id) => {
    const s = document.createElementNS(NS, 'svg'); s.setAttribute('class', 'ico'); s.setAttribute('aria-hidden', 'true');
    const u = document.createElementNS(NS, 'use'); u.setAttribute('href', `#${id}`); s.appendChild(u); return s;
  };
  const button = (cls, label, id, onClick) => {
    const b = document.createElement('button');
    b.type = 'button'; b.className = `wc ${cls}`; b.setAttribute('aria-label', label); b.title = label;
    b.appendChild(icon(id)); b.addEventListener('click', onClick); return b;
  };
  const controls = (extra) => {
    const d = document.createElement('div');
    d.className = `winctl${extra ? ` ${extra}` : ''}`; d.setAttribute('role', 'group'); d.setAttribute('aria-label', 'Window');
    d.append(button('wc-min', 'Minimize window', 'i-min', minimize), button('wc-close', 'Close window', 'i-x', close));
    return d;
  };

  async function call(name) {
    const r = await API[name]();
    if (r && r.unavailable) A.toast('Window buttons work in the desktop application.');
  }
  const minimize = () => call('windowMinimize');

  async function close() {
    const reasons = [];
    if (P() && P().syncRunning()) reasons.push('A sync is running. If you close now it stops, and you can run it again later.');
    if (P() && P().settingsDirty()) reasons.push('Settings you changed have not been saved.');
    if (reasons.length) {
      const ok = await U.confirmDialog({ title: 'Close Pulls Tracker?', message: reasons.join(' '), ok: 'Close', cancel: 'Keep open', danger: true });
      if (!ok) return;
      if (P().syncRunning()) await API.syncCancel();
    }
    await call('windowClose');
  }

  document.querySelector('.topbar').append(controls());
  ['settingsModal', 'dlg'].forEach((id) => { const d = document.getElementById(id); if (d) d.append(controls('winctl-float')); });

  /* ---- Resize handles ---------------------------------------------------------------------------------------------------
     Eight strips (.rz-h, css/app.css) along the edges and corners, repeated inside each dialog because a modal dialog blocks everything
     outside it. The page does not follow the mouse: one call to PT_API.windowStartResize(edge) when the button goes down is enough on
     Windows, where the Python side follows the cursor with Win32 calls until the button is released (pt/winsize.py), so no message
     crosses the WebView2 bridge while the mouse moves. When the answer is not native (other systems) this page does the resize with
     windowSetRect, at most one call in flight and one per drawn frame, always with the newest mouse position. */
  const EDGES = ['n', 's', 'e', 'w', 'ne', 'nw', 'se', 'sw'];
  const handles = () => {
    const d = document.createElement('div'); d.className = 'rz'; d.setAttribute('aria-hidden', 'true');
    EDGES.forEach((e) => { const h = document.createElement('div'); h.className = `rz-h rz-${e}`; h.dataset.edge = e; d.append(h); });
    return d;
  };
  document.body.append(handles());
  ['settingsModal', 'dlg'].forEach((id) => { const d = document.getElementById(id); if (d) d.append(handles()); });

  async function resizeByPage(edge, el, ev) {
    const base = await API.windowGetRect();
    if (!base || base.unavailable) return;
    const sx = ev.screenX, sy = ev.screenY;
    let newest = null, busy = false, queued = false;
    const apply = () => {
      queued = false;
      if (busy || !newest) return;
      const dx = newest.x - sx, dy = newest.y - sy;
      let { x, y, width: w, height: h } = base;
      if (edge.includes('e')) w = Math.max(base.minWidth, base.width + dx);
      if (edge.includes('w')) { w = Math.max(base.minWidth, base.width - dx); x = base.x + base.width - w; }
      if (edge.includes('s')) h = Math.max(base.minHeight, base.height + dy);
      if (edge.includes('n')) { h = Math.max(base.minHeight, base.height - dy); y = base.y + base.height - h; }
      newest = null; busy = true;
      API.windowSetRect(x, y, w, h).finally(() => { busy = false; if (newest && !queued) { queued = true; requestAnimationFrame(apply); } });
    };
    const move = (e) => { newest = { x: e.screenX, y: e.screenY }; if (!busy && !queued) { queued = true; requestAnimationFrame(apply); } };
    const end = () => { el.removeEventListener('pointermove', move); el.removeEventListener('pointerup', end); el.removeEventListener('pointercancel', end); };
    el.addEventListener('pointermove', move); el.addEventListener('pointerup', end); el.addEventListener('pointercancel', end);
  }
  document.addEventListener('pointerdown', (ev) => {
    const el = ev.target.closest && ev.target.closest('.rz-h');
    if (!el || ev.button !== 0) return;
    ev.preventDefault();
    const edge = el.dataset.edge, pid = ev.pointerId, sx = ev.screenX, sy = ev.screenY;
    try { el.setPointerCapture(pid); } catch (e) { /* the pointer is gone already */ }
    API.windowStartResize(edge).then((r) => { if (r && !r.unavailable && !r.native) resizeByPage(edge, el, { screenX: sx, screenY: sy }); });
  }, true);

  /* Tell the Python side the first screen is ready (it shows the window then). The window is hidden while the page loads, so
     requestAnimationFrame does not run: timers only. The banner art gets up to 2 s to arrive, so it does not pop in after the window shows. */
  const firstScreenReady = () => new Promise((resolve) => {
    const imgs = () => Array.from(document.querySelectorAll('#stage img')).filter((i) => !i.complete);
    setTimeout(() => {
      const pending = imgs().map((i) => new Promise((r) => { i.addEventListener('load', r, { once: true }); i.addEventListener('error', r, { once: true }); }));
      Promise.race([Promise.all(pending), new Promise((r) => setTimeout(r, 2000))]).then(resolve);
    }, 250);
  });
  API.ready().then(firstScreenReady).then(() => API.uiReady());

  if (A.qs.get('perf') === '1') document.head.append(Object.assign(document.createElement('script'), { src: 'js/perf.js' }));   // pulls_tracker.py --perf

  const param = A.qs.get('frameless');
  API.ready().then(() => {
    const on = param === '1' || (API.mode === 'backend' && param !== '0');
    if (on) document.body.dataset.frameless = '1';
  });
})();
