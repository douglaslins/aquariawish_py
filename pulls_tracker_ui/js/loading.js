/* Loading panel script. The Python application calls
     PT_LOAD.set({ done, total, fraction, label })
   done      load tasks finished so far
   total     load tasks to run (it can shrink while loading, when tasks are skipped)
   fraction  0 to 1, progress inside the running task (optional)
   label     the running task, as a short text under the donut
   The donut shows (done + fraction) / total. */
(function () {
  'use strict';
  const $ = (id) => document.getElementById(id);
  const bar = $('lpBar'), pct = $('lpPct'), label = $('lpLabel'), donut = $('lpDonut'), banner = $('lpBanner');
  const qs = new URLSearchParams(location.search);

  function set(s) {
    const total = Math.max(1, Number(s.total) || 1);
    const p = Math.max(0, Math.min(1, ((Number(s.done) || 0) + (Number(s.fraction) || 0)) / total)) * 100;
    bar.style.strokeDasharray = `${p.toFixed(2)} 100`;
    const n = Math.round(p);
    pct.textContent = `${n}%`; donut.setAttribute('aria-valuenow', String(n));
    if (typeof s.label === 'string') label.textContent = s.label;
  }
  window.PT_LOAD = { set };

  /* the banner: the first of these files that exists. The application's data folder (#data=..., the folder next to the program) comes
     first, so the banner can be changed in a build without touching the files inside it; then the one shipped with the interface. */
  (function () {
    let hash = new URLSearchParams(location.hash.replace(/^#/, '')).get('data');
    // same rule as index.html: file://... or a relative path, made of safe characters only (anything else could end the CSS url("...") below or reach another computer)
    if (hash && !(/^[\w.~%!$&()*+,;=:@/-]+$/.test(hash) && (/^file:\/\//i.test(hash) || !(/^[A-Za-z][A-Za-z0-9+.-]*:/.test(hash) || /^\/\//.test(hash))))) hash = null;
    const bases = [hash ? hash.replace(/\/*$/, '/') + 'assets/loading/' : null, 'assets/loading/'].filter(Boolean);
    const files = [];
    bases.forEach((b) => ['png', 'webp', 'jpg', 'jpeg'].forEach((e) => files.push(`${b}banner.${e}`)));
    (function tryNext(i) {
      if (i >= files.length) return;
      const im = new Image();
      im.onload = () => { banner.style.backgroundImage = `url("${files[i]}")`; };
      im.onerror = () => tryNext(i + 1);
      im.src = files[i];
    })(0);
  })();

  /* preview in a browser: loading.html?demo=1 (these labels are for the preview only; the real ones come from pt/loader.py) */
  if (qs.get('demo')) {
    const L = ['Checking the active banners', 'Looking for stored data', 'Checking that stored data is up to date', 'Downloading Genshin Impact assets',
      'Downloading Honkai: Star Rail assets', 'Downloading Zenless Zone Zero assets', 'Syncing accounts', 'Opening the application'];
    const ms = 900 * (Number(qs.get('speed')) || 1);
    let i = 0;
    const step = () => {
      if (i < L.length) { set({ done: i, total: L.length, label: L[i] }); i += 1; setTimeout(step, ms); } else set({ done: L.length, total: L.length, label: L[L.length - 1] });
    };
    step();
  }
})();
