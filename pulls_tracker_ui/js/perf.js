/* Responsiveness probe. Loaded only by `python pulls_tracker.py --perf` (the page gets #perf=1).
   Every 5 s, when something was measured, it sends a short report to the log (data/pulls_tracker.log, lines "perf {...}"):
     env        window size, pixel ratio, number of page elements (first report only)
     stalls     [ms since start, ms] the page's own timer ran late by 100 ms or more: the page thread was blocked
     longTasks  [start, ms] tasks over 50 ms (Long Tasks API)
     slowEvents [type, start, ms, ms before the handler began] events that took 50 ms or more to be handled and drawn (Event Timing API)
     frames     [start, ms, blocking ms] frames that took 100 ms or more (Long Animation Frames, newer Chromium only)
     afterClick for each click: [frames drawn in the next second, longest gap between frames in ms]
     slowCalls  [method, ms] PT_API calls that took 100 ms or more to answer (the Python bridge)
   Idle, the probe does almost nothing: one timer every 100 ms. If the window "freezes" after a click, the log shows whether the page
   was blocked (stalls, longTasks), the drawing was slow (afterClick, frames), or a Python call was slow (slowCalls). When none of these
   shows anything while the window is not responding, the cause is in the window layer, outside the page. */
(function () {
  'use strict';
  const API = window.PT_API;
  const T0 = performance.now(), MAX = 20;
  const r = { stalls: [], longTasks: [], slowEvents: [], frames: [], afterClick: [], slowCalls: [] };
  const keep = (list, item) => { list.push(item); if (list.length > MAX) list.shift(); };
  const watch = (type, fn, extra) => {
    try { new PerformanceObserver((l) => l.getEntries().forEach(fn)).observe(Object.assign({ type, buffered: true }, extra)); } catch (e) { /* this Chromium does not have it */ }
  };
  const ms = (n) => Math.round(n);
  watch('longtask', (e) => keep(r.longTasks, [ms(e.startTime - T0), ms(e.duration)]));
  let lastEvent = '';
  watch('event', (e) => {      // one click gives several entries (pointerdown, mousedown, ...) with the same start: one line per start
    const id = `${ms(e.startTime)}|${ms(e.duration)}`; if (id === lastEvent) return; lastEvent = id;
    keep(r.slowEvents, [e.name, ms(e.startTime - T0), ms(e.duration), ms(e.processingStart - e.startTime)]);
  }, { durationThreshold: 50 });
  watch('long-animation-frame', (e) => keep(r.frames, [ms(e.startTime - T0), ms(e.duration), ms(e.blockingDuration || 0)]));

  let last = performance.now();
  setInterval(() => { const t = performance.now(), late = t - last - 100; last = t; if (late >= 100) keep(r.stalls, [ms(t - T0), ms(late)]); }, 100);

  document.addEventListener('pointerdown', () => {      // the next second of drawing after each click
    let n = 0, prev = performance.now(), worst = 0; const end = prev + 1000;
    const tick = (t) => { n += 1; worst = Math.max(worst, t - prev); prev = t; if (t < end) requestAnimationFrame(tick); else keep(r.afterClick, [n, ms(worst)]); };
    requestAnimationFrame(tick);
  }, true);

  Object.keys(API).forEach((k) => {      // time the calls to the Python side
    if (typeof API[k] !== 'function' || k === 'ready' || k === 'perfReport') return;
    const f = API[k];
    API[k] = (...a) => { const t = performance.now(); const p = f(...a); Promise.resolve(p).then(() => { const d = performance.now() - t; if (d >= 100) keep(r.slowCalls, [k, ms(d)]); }, () => {}); return p; };
  });

  let first = true;
  setInterval(() => {
    const out = {};
    Object.keys(r).forEach((k) => { if (r[k].length) { out[k] = r[k].slice(); r[k].length = 0; } });
    if (!Object.keys(out).length && !first) return;
    if (first) { out.env = { w: innerWidth, h: innerHeight, dpr: devicePixelRatio, nodes: document.getElementsByTagName('*').length }; first = false; }
    Promise.resolve(API.perfReport(out)).catch(() => {});
  }, 5000);
})();
