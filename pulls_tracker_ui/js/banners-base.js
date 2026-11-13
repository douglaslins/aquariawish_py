/* When the page is opened with ?data=<folder> (window.PT_DATA_BASE), the art and icon files of window.PT_BANNERS are in that folder:
   their relative paths ("assets/art/genshin/x.png") get the folder in front. Remote addresses and missing files (null) stay as they are. */
(function () {
  'use strict';
  const base = window.PT_DATA_BASE, B = window.PT_BANNERS;
  if (!base || !B || !B.slides) return;
  const fix = (p) => (typeof p === 'string' && p && !/^(?:[a-z][a-z0-9+.-]*:|\/)/i.test(p) ? base + p : p);
  Object.keys(B.slides).forEach((game) => (B.slides[game] || []).forEach((s) => {
    s.art = fix(s.art); s.icon = fix(s.icon);
    (s.rateUp || []).forEach((r) => { r.icon = fix(r.icon); });
  }));
})();
