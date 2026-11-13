/*
  Pulls Tracker, screens: Dashboard, Banners (with the banner detail), History, Stats, the sidebar and the shared dialog.
  The Settings modal (Settings, Sync, Import / Export, Data sources) is in js/panels.js.
  Everything is read through window.PT_API (js/api.js); nothing here touches pulls or settings directly.
  Pop-ups are modals: the Settings modal and the one shared <dialog id="dlg"> (confirmations, errors, banner detail, loss pool).
*/
(() => {
  'use strict';

  const A = window.PT_APP, API = window.PT_API, R = window.PT_RULES;
  const $ = (id) => document.getElementById(id);
  const NS = 'http://www.w3.org/2000/svg';

  /* ---- DOM helpers ------------------------------------------------------------------------------- */
  function h(tag, props, ...kids) {
    const e = document.createElement(tag);
    if (props) Object.keys(props).forEach((k) => {
      const v = props[k];
      if (v == null || v === false) return;
      if (k === 'class') e.className = v;
      else if (k.startsWith('on')) e.addEventListener(k.slice(2), v);
      else if (v === true) e.setAttribute(k, '');
      else e.setAttribute(k, v);
    });
    kids.flat(Infinity).forEach((k) => { if (k != null && k !== false) e.append(k instanceof Node ? k : document.createTextNode(String(k))); });
    return e;
  }
  function svg(id, cls = 'ico') {
    const s = document.createElementNS(NS, 'svg'), u = document.createElementNS(NS, 'use');
    s.setAttribute('class', cls); s.setAttribute('aria-hidden', 'true'); u.setAttribute('href', `#${id}`); s.appendChild(u); return s;
  }
  // Picture with a fallback chain (local file, then remote URL); a neutral box when nothing loads (no dialog, per the IA).
  function pic(srcs, cls = 'pic', alt = '') {
    const list = srcs.filter(Boolean), im = h('img', { class: cls, alt, referrerpolicy: 'no-referrer', decoding: 'async', loading: 'lazy' });
    let k = 0;
    const next = () => { if (k < list.length) im.src = list[k++]; else { im.removeAttribute('src'); im.classList.add('is-missing'); } };
    im.addEventListener('error', next); next(); return im;
  }
  const topName = (game) => (game === 'zzz' ? 'S-Rank' : '5★');
  const rarityLabel = (game, r) => R[game].rarity[r];
  const groupRule = (game, key) => R[game].groups.find((g) => g.key === key);

  /* ---- Core state: settings, accounts ------------------------------------------------------------- */
  const core = { settings: null, accounts: [], lastSync: {} };
  const OFFSETS = { Asia: 8, Europe: 1, America: -5, 'TW/HK/MO': 8, CN: 8 };      // the region labels of pt/games.py _REGIONS (an unknown label counts as +8)
  async function refreshCore() {
    const [s, st] = await Promise.all([API.getSettings(), API.state()]);
    core.settings = s; core.accounts = st.accounts || []; core.lastSync = st.lastSync || {};
    renderRail();
    return core;
  }
  const accountOf = (game) => core.accounts.find((a) => a.game === game) || null;
  const maskUid = (uid) => (core.settings && core.settings.maskUid && uid && uid.length > 5 ? uid[0] + 'x'.repeat(uid.length - 5) + uid.slice(-4) : uid);

  /* ---- Time ---------------------------------------------------------------------------------------- */
  const p2 = (n) => String(n).padStart(2, '0');
  function serverOffset(game) { const a = accountOf(game); return OFFSETS[a ? a.region : 'Asia'] ?? 8; }
  function fmtT(ms, game, label = false) {
    const mode = core.settings.times; let d, tag;
    if (mode === 'Local') { const x = new Date(ms); d = [x.getFullYear(), x.getMonth() + 1, x.getDate(), x.getHours(), x.getMinutes()]; tag = 'local'; }
    else {
      const off = mode === 'UTC' ? 0 : serverOffset(game), x = new Date(ms + off * 3600000);
      d = [x.getUTCFullYear(), x.getUTCMonth() + 1, x.getUTCDate(), x.getUTCHours(), x.getUTCMinutes()];
      tag = mode === 'UTC' ? 'UTC' : `server, UTC${off >= 0 ? '+' : ''}${off}`;
    }
    return `${d[0]}-${p2(d[1])}-${p2(d[2])} ${p2(d[3])}:${p2(d[4])}${label ? ` ${tag}` : ''}`;
  }
  const utcHM = (ms) => { const x = new Date(ms); return `${p2(x.getUTCHours())}:${p2(x.getUTCMinutes())} UTC`; };
  const fmtWhen = (ms, game) => (core.settings.times === 'UTC' ? fmtT(ms, game, true) : `${fmtT(ms, game, true)} (${utcHM(ms)})`);
  const ago = (ms) => { const m = Math.max(0, Math.round(ms / 60000)); return m < 60 ? `${m} min` : m < 2880 ? `${Math.round(m / 60)} h` : `${Math.round(m / 1440)} days`; };

  /* ---- Banners (from js/banners.js) ----------------------------------------------------------------- */
  function bannerList(game) {
    const t = A.now(), all = (window.PT_BANNERS && window.PT_BANNERS.slides && window.PT_BANNERS.slides[game]) || [];
    const map = new Map();
    all.filter((s) => !s.end || Date.parse(s.end) > t).forEach((s) => {
      const key = `${s.group}|${s.banner}|${s.start}|${s.end}`;
      if (!map.has(key)) map.set(key, { id: s.id, key, title: s.bannerName || s.banner, version: s.version, group: s.group, kind: s.kind, items: [], rateUp: [], start: s.start, end: s.end, status: Date.parse(s.start) > t ? 'soon' : 'live', past: false });
      const b = map.get(key); b.items.push(s);
      (s.rateUp || []).forEach((r) => { if (!b.rateUp.some((x) => x.name === r.name)) b.rateUp.push(r); });
    });
    return Array.from(map.values()).sort((a, b) => Date.parse(a.start) - Date.parse(b.start) || a.group.localeCompare(b.group));
  }
  const bannerItemName = (it) => it.name || 'Unannounced';
  const bannerSource = () => {
    const B = window.PT_BANNERS, at = B && B.generatedAt ? Date.parse(B.generatedAt) : NaN;
    if (!B || !Number.isFinite(at)) return { text: 'Banner data: not loaded', stale: true };
    const age = A.now() - at;
    return { text: `Banner data: ${B.source ? B.source.replace(/^https?:\/\//, '') : 'file'}, ${ago(age)} old`, stale: age > 24 * 3600000, title: `js/banners.js, written ${fmtT(at, A.state.game, true)}` };
  };
  const chip = (text, kind, title) => h('span', { class: `chip${kind ? ` chip-${kind}` : ''}`, title }, text);
  const sourceChip = () => { const s = bannerSource(); return chip(s.text, s.stale ? 'warn' : '', s.title); };
  const sampleChip = () => (API.mode === 'sample' && API.synced ? chip('Sample data, not your pulls', 'sample') : null);

  /* ---- Dialog (the one shared modal) ------------------------------------------------------------------ */
  const dlg = $('dlg');
  let dlgResolve = null;
  function finish(v) { if (dlgResolve) { const r = dlgResolve; dlgResolve = null; dlg.close(); r(v); } }
  function openDialog({ title, body, actions = [], size = 'sm', dismissible = true }) {
    if (dlg.open) { if (dlgResolve) { const r = dlgResolve; dlgResolve = null; r(null); } dlg.close(); }
    return new Promise((resolve) => {
      dlgResolve = resolve; dlg.dataset.size = size; dlg.dataset.dismiss = String(dismissible);
      $('dlgTitle').textContent = title;
      $('dlgBody').replaceChildren(typeof body === 'string' ? h('p', { class: 'dlg-text' }, body) : body);
      const ft = $('dlgFoot'); ft.hidden = !actions.length;
      ft.replaceChildren(...actions.map((a) => h('button', {
        class: `btn${a.kind === 'primary' ? ' btn-primary' : a.kind === 'danger' ? ' btn-danger' : ''}`, type: 'button', 'data-focus': a.focus ? '1' : null,
        onclick: () => (a.onClick ? a.onClick() : finish(a.value))
      }, a.label)));
      $('dlgClose').hidden = !dismissible;
      dlg.showModal();
      const f = ft.querySelector('[data-focus]') || (dismissible ? null : ft.querySelector('.btn'));
      if (f) f.focus(); else if (!ft.hidden) ft.querySelector('.btn').focus();
    });
  }
  $('dlgClose').addEventListener('click', () => finish(null));
  dlg.addEventListener('close', () => { if (dlgResolve) { const r = dlgResolve; dlgResolve = null; r(null); } });
  dlg.addEventListener('cancel', (e) => { if (dlg.dataset.dismiss === 'false') e.preventDefault(); });
  dlg.addEventListener('click', (e) => { if (e.target === dlg && dlg.dataset.dismiss !== 'false') finish(null); });
  const confirmDialog = ({ title, message, ok = 'OK', danger = false, cancel = 'Cancel' }) =>
    openDialog({ title, body: message, actions: [{ label: cancel, value: false, focus: true }, { label: ok, value: true, kind: danger ? 'danger' : 'primary' }] }).then((v) => v === true);

  /* ---- Errors (IA S12) ------------------------------------------------------------------------------- */
  const GAME_STEPS = ['Open the game.', 'Open the pull history page (Wish, Warp or Signal Search history).', 'Wait until the list loads.', 'Come back here and retry.'];
  const ERRORS = {
    link_expired: (p) => ({ title: 'The saved link has expired', text: 'The link from the game has expired (links last about one day). Open the pull history page in the game, wait for it to load, then retry.', steps: GAME_STEPS, note: 'Your stored history is unchanged and still available.', actions: ['retry', 'steps'] }),
    cache_missing: () => ({ title: 'No saved link found', text: 'No saved link found. Open the pull history in the game at least once.', steps: GAME_STEPS, note: 'Your stored history is unchanged and still available.', actions: ['folder', 'steps'] }),
    rate_limited: (p) => ({ title: 'Stopped by the server', text: p && p.sec ? `The server asked to slow down. Waiting ${p.sec} s (retry ${p.retry} of ${p.of}).` : 'Stopped. Try again in a few minutes.', note: 'Pulls already merged stay stored.', actions: ['later'] }),
    uid_mismatch: (p) => ({ title: 'This data belongs to another account', text: `This data belongs to UID ${maskUid((p && p.found) || '?')}. The active account is ${maskUid((p && p.active) || '?')}.`, note: 'Sync and import are blocked for the active account.', actions: ['add', 'cancel'] }),
    no_backend: () => (API.mode === 'backend'
      ? { title: 'Not available yet', text: 'This function is not available yet in this version of the application.', actions: ['close'] }
      : { title: 'Available in the desktop application', text: 'This preview runs in a browser. It cannot read the game files or reach the game servers; syncing works in the desktop application.', actions: ['close'] }),
    network: (p) => ({ title: 'The game server could not be reached', text: 'The history server did not answer. Check the internet connection, then retry.', note: p && p.message ? `Detail: ${p.message}. Your stored history is unchanged.` : 'Your stored history is unchanged and still available.', actions: ['retry', 'later'] }),
    server_error: (p) => ({ title: 'The server returned an error', text: p && p.message ? `The history server answered: ${p.message}` : 'The history server returned an error.', note: 'Pulls already merged stay stored.', actions: ['retry', 'later'] }),
    cache_unreadable: () => ({ title: 'The game cache could not be read', text: 'The file that holds the saved link could not be opened. Check that the game folder is the right one and that the file is not blocked, then retry.', note: 'Your stored history is unchanged and still available.', actions: ['retry', 'folder'] }),
    internal: () => ({ title: 'The sync stopped unexpectedly', text: 'Something went wrong inside the application. The details are in the file pulls_tracker.log in the data folder.', note: 'Pulls already merged stay stored.', actions: ['retry', 'close'] }),
    unknown_type: (p) => ({ title: 'Unknown banner type', text: `Found pulls of banner type ${(p && p.type) || '?'} that the app does not know. They are stored and shown as Unknown.`, actions: ['dismiss'] }),
    history_window: (p) => ({ title: 'Limited history window', text: `The server returns a limited window. Pulls before ${(p && p.date) || 'the oldest stored pull'} are not available unless imported from another tracker.`, actions: ['import', 'dismiss'] })
  };
  const ERROR_ACTIONS = { retry: { label: 'Retry', kind: 'primary' }, steps: { label: 'Show steps' }, folder: { label: 'Choose folder' }, later: { label: 'Try again later' }, add: { label: 'Add as new account' },
    cancel: { label: 'Cancel' }, close: { label: 'Close' }, dismiss: { label: 'Dismiss' }, import: { label: 'Import file' } };
  const errorInfo = (code, params) => (ERRORS[code] || ERRORS.no_backend)(params);
  function showErrorDialog(code, params) {
    const e = errorInfo(code, params);
    const steps = e.steps ? h('ol', { class: 'err-steps', hidden: true }, e.steps.map((s) => h('li', null, s))) : null;
    const body = h('div', { class: 'err' }, h('p', { class: 'dlg-text' }, e.text), steps, e.note ? h('p', { class: 'dlg-note' }, svg('i-info'), e.note) : null);
    return openDialog({ title: e.title, body, actions: e.actions.map((k, i) => ({ ...ERROR_ACTIONS[k], value: k, focus: i === 0,
      onClick: k === 'steps' && steps ? () => { steps.hidden = !steps.hidden; } : null })) });
  }

  /* ---- Views: switching ------------------------------------------------------------------------------ */
  const view = $('view'), mainCol = $('mainCol');
  const VIEWS = { dashboard: 'Dashboard', banners: 'Banners', history: 'History', stats: 'Stats' };
  let current = 'main';
  const hist = { game: null, group: 'all', rarity: '5', from: '', to: '', name: '', include3: false, sort: { col: 'time', dir: 'desc' }, page: 1 };
  const stat = { game: null, group: 'all', dates: 'all' };
  const bnr = { tab: 'current', version: 'all', type: 'all' };
  let renderToken = 0;

  function showView(name, opts = {}) {
    current = VIEWS[name] ? name : 'main';
    document.body.dataset.view = current;
    mainCol.hidden = current !== 'main'; view.hidden = current === 'main';
    document.querySelectorAll('.nav-btn[data-screen]').forEach((b) => {
      const on = b.dataset.screen.toLowerCase() === current;
      if (on) b.setAttribute('aria-current', 'page'); else b.removeAttribute('aria-current');
    });
    $('main').setAttribute('aria-labelledby', current === 'main' ? 'itemName' : 'viewTitle');
    $('main').scrollTop = 0;
    if (current === 'main') { window.dispatchEvent(new Event('resize')); if (opts.focus !== false) $('main').focus({ preventScroll: true }); return Promise.resolve(); }
    return renderView(opts.focus !== false);
  }
  async function renderView(focus = false) {
    const tok = ++renderToken, game = A.state.game;
    view.setAttribute('aria-busy', 'true');
    let node;
    try {
      if (!core.settings) await refreshCore();
      node = await ({ dashboard: dashboardView, banners: bannersView, history: historyView, stats: statsView }[current])(game);
    } catch (err) {
      console.error(err); node = h('div', { class: 'empty' }, h('h1', { class: 'empty-title', id: 'viewTitle', tabindex: '-1' }, 'This screen could not be drawn'), h('p', null, String(err && err.message || err)));
    }
    if (tok !== renderToken) return;
    view.replaceChildren(node); view.removeAttribute('aria-busy');
    if (focus) { const t = $('viewTitle'); if (t) t.focus({ preventScroll: true }); }
  }

  function vhead(title, game, extra) {
    return h('header', { class: 'vhead' },
      h('div', { class: 'vtitles' }, h('h1', { class: 'vtitle', id: 'viewTitle', tabindex: '-1' }, title), h('p', { class: 'vsub' }, R[game].title)),
      h('div', { class: 'vmeta' }, extra, sampleChip()));
  }
  function emptyHistory(game, what) {
    return h('div', { class: 'empty-card' },
      h('h2', null, 'No pull history yet'),
      h('p', null, `${what} needs the pulls of ${R[game].title}. Three steps import them: choose the games to track, open each game's pull history page once, then scan.`),
      h('button', { class: 'btn btn-primary', type: 'button', onclick: () => openSync(false) }, 'Set up sync'));
  }

  /* ---- Dashboard (IA S3, S3b) -------------------------------------------------------------------------- */
  const pityLevel = (pity) => (pity < core.settings.pityColors.green ? 'low' : pity < core.settings.pityColors.gold ? 'mid' : 'high');
  function guaranteeText(game, g, d) {
    const top = topName(game), rule = groupRule(game, g.key);
    if (g.key === 'bangboo') return `Every ${top} is the selected Bangboo.`;
    if (!rule.flip) return 'No guarantee on this banner type.';
    if (d.guarantee === true) return `YES, the next ${top} is the featured one (${rule.flip} lost).`;
    if (d.guarantee === false) return `NO, the next ${top} is a ${rule.flip}.`;
    if (d.fives > 0) return `Unknown: the banner data does not show which ${top} was featured, ${rule.flip} assumed.`;
    return `Unknown: no ${top} stored yet, ${rule.flip} assumed.`;
  }
  function extraText(game, g, d) {
    const x = d.extra; if (!x) return null;
    if (x.radiance != null) return `Capturing Radiance counter ${x.radiance} (estimated)`;
    if (x.path) return `Epitomized Path target: ${x.path.target}. Fate Point ${x.path.point} / ${x.path.max}`;
    if (x.done) return x.done;
    if (game === 'hsr' && g.key === 'character') return `50/50 loss pool: ${core.settings.lossPool.length} characters`;
    return null;
  }
  function pityBar(game, g, d) {
    const hard = g.hard, soft = g.soft ? core.settings.soft[game][g.key] : null, lv = pityLevel(d.pity);
    const inSoft = soft != null && d.pity >= soft;
    return h('div', { class: 'pmeter' },
      h('div', { class: `pbar lv-${lv}`, role: 'meter', 'aria-valuemin': 0, 'aria-valuemax': hard, 'aria-valuenow': d.pity, 'aria-label': `Pity ${d.pity} of ${hard}` },
        h('span', { class: 'pbar-fill', style: `width:${Math.min(100, (d.pity / hard) * 100)}%` }),
        soft != null ? h('span', { class: 'pbar-soft', style: `left:${(soft / hard) * 100}%`, title: `Soft pity starts near ${soft} (estimated)` }) : null),
      h('span', { class: 'pstate' }, inSoft ? 'In the soft pity range (estimated)' : soft != null ? `Soft pity from about ${soft} (estimated)` : ''));
  }
  function liveLine(game, b) {
    const t = A.now();
    if (b.status === 'soon') return h('span', { class: 'live-left' }, `Starts ${fmtWhen(Date.parse(b.start), game)}, in `, h('b', { 'data-left': b.start }, A.dur(Date.parse(b.start) - t)));
    if (!b.end) return h('span', { class: 'live-left' }, 'End not announced');
    return h('span', { class: 'live-left' }, `Ends ${fmtWhen(Date.parse(b.end), game)}, in `, h('b', { 'data-left': b.end }, A.dur(Date.parse(b.end) - t)));
  }
  function itemChip(game, it) {
    const el = A.elementOf(game, it.element);
    return h('span', { class: 'pb-item' }, pic([it.icon, it.iconRemote], 'pic pic-s'), h('span', null, bannerItemName(it)), el ? h('img', { class: 'el-s', src: el.icon, alt: el.name, title: el.name }) : null);
  }
  /* "Account 8xxxx0001 (Main)"; with more than one stored account of the game it is a list that switches the account the screens show. */
  function accountMeta(game, acct) {
    const list = core.accounts.filter((a) => a.game === game), name = (a) => `${maskUid(a.uid)}${a.nickname ? ` (${a.nickname})` : ''}`;
    if (list.length < 2) return h('span', { class: 'meta-i' }, 'Account ', h('strong', null, maskUid(acct.uid)), acct.nickname ? ` (${acct.nickname})` : '');
    return h('label', { class: 'meta-i meta-acct' }, 'Account ', h('span', { class: 'select' },
      h('select', { id: 'acctSwitch', 'aria-label': 'Account shown', onchange: async (e) => {
        const r = await API.setActiveAccount(game, e.target.value);
        if (!r || r.unavailable) { A.toast(API.mode === 'backend' ? 'That account could not be shown.' : 'Switching accounts works in the desktop application.'); e.target.value = acct.uid; return; }
        document.dispatchEvent(new CustomEvent('pt:data'));
      } }, list.map((a) => h('option', { value: a.uid, selected: a.uid === acct.uid }, name(a)))), svg('i-chevron-down', 'ico select-chev')));
  }

  async function dashboardView(game) {
    const [d] = await Promise.all([API.dashboard(game), refreshCore()]);
    const rules = R[game], all = bannerList(game), liveNow = all.filter((b) => b.status === 'live'), next = all.filter((b) => b.status === 'soon');
    const acct = accountOf(game), last = core.lastSync[game];
    const meta = [
      acct ? accountMeta(game, acct) : null,
      last ? h('span', { class: 'meta-i' }, `Last sync ${fmtT(Date.parse(last), game)} (${ago(A.now() - Date.parse(last))} ago)`) : h('span', { class: 'meta-i' }, 'Never synced'),
      sourceChip()
    ];
    const root = h('div', { class: 'vroot' }, vhead('Dashboard', game, meta));
    if (d.empty) root.append(emptyHistory(game, 'The Dashboard'));

    const cards = h('div', { class: 'pcards' }), collapsed = [];
    rules.groups.forEach((g) => {
      const gd = d.empty ? null : d.groups.find((x) => x.key === g.key);
      if (gd && gd.pulls === 0) { collapsed.push(g.label); return; }
      const banners = liveNow.filter((b) => b.group === g.key);
      if (d.empty && !banners.length) return;
      const card = h('article', { class: 'pcard', 'aria-labelledby': `pc-${g.key}` },
        h('header', { class: 'pcard-head' }, h('h2', { id: `pc-${g.key}` }, g.label), g.note ? h('span', { class: 'pnote' }, g.note) : null));
      if (banners.length) {
        const items = banners.flatMap((b) => b.items);
        card.append(h('div', { class: 'pbanners' }, h('div', { class: 'pb-items' }, items.map((it) => itemChip(game, it))), liveLine(game, banners[0])));
      }
      if (gd) {
        card.append(
          h('div', { class: 'prow' }, h('span', { class: 'plabel' }, 'Pity'), h('strong', { class: 'pnum' }, `${gd.lowerBound ? '≥' : ''}${gd.pity}`), h('span', { class: 'pcap' }, `/ ${g.hard}`), pityBar(game, g, gd)),
          h('div', { class: 'prow prow-g' }, h('span', { class: 'plabel' }, 'Guarantee'), h('span', { class: 'pg' }, guaranteeText(game, g, gd), extraText(game, g, gd) ? h('span', { class: 'pextra' }, extraText(game, g, gd)) : null)));
      }
      if (banners.length) card.append(h('div', { class: 'pact' },
        h('button', { class: 'btn btn-primary', type: 'button', onclick: () => { showView('main'); A.goBanner(banners[0].items[0].id); } }, 'Plan for this banner'),
        h('button', { class: 'btn', type: 'button', onclick: () => openBannerDetail(game, banners[0]) }, 'Banner detail')));
      cards.append(card);
    });
    if (cards.children.length) root.append(cards);
    if (collapsed.length) root.append(h('p', { class: 'vnote' }, svg('i-info'), `No pulls yet: ${collapsed.join(', ')}.`));
    if (!liveNow.length) root.append(h('p', { class: 'vnote' }, svg('i-info'), 'No banner is running now in the banner data.'));

    root.append(h('section', { class: 'nextcard', 'aria-labelledby': 'nb-h' }, h('h2', { id: 'nb-h' }, 'Next banner'),
      next.length ? h('div', { class: 'nb-body' }, h('div', { class: 'pb-items' }, next[0].items.map((it) => itemChip(game, it))), liveLine(game, next[0]), h('span', { class: 'nb-v' }, `v${next[0].version}`))
        : h('p', { class: 'nb-none' }, 'Not announced yet.'), sourceChip()));

    if (!d.empty) {
      const s = d.summary, flip = (x) => `${x.label} ${x.odds}: ${x.won} won, ${x.lost} lost`;
      root.append(h('dl', { class: 'sumbar', 'aria-label': 'Summary' },
        [['Pulls', A.fmt(s.pulls)], [`${topName(game)} found`, A.fmt(s.fives)], ['Average pity', s.avg == null ? '-' : `${s.avgLowerBound ? '≥' : ''}${s.avg.toFixed(2)}`], ['Luck grade', s.grade || '-'],
         ['Flips', s.flips.length ? s.flips.map(flip).join('; ') : '-'], ['Spent (estimated)', `${A.fmt(s.spent)} ${s.currency}`]]
          .map(([k, v]) => h('div', { class: 'sum-i' }, h('dt', null, k), h('dd', null, v)))));
      root.append(h('p', { class: 'vnote' }, svg('i-info'), `Oldest stored pull: ${fmtT(Date.parse(d.oldest), game).slice(0, 10)}. Servers keep a limited history window (about 6 months, not confirmed); older pulls are present only because earlier syncs stored them. Soft pity values are estimates you can change in Settings.`));
    }
    return root;
  }

  /* ---- Banners (IA S4) and banner detail (IA S5) ---------------------------------------------------------- */
  const TABS = [['current', 'Current'], ['upcoming', 'Upcoming'], ['past', 'Past']];
  const statusChip = (st) => h('span', { class: `status st-${st}` }, { live: 'LIVE', soon: 'SOON', ended: 'ENDED' }[st]);
  async function bannersView(game) {
    await refreshCore();
    const all = bannerList(game);
    const root = h('div', { class: 'vroot' }, vhead('Banners', game, [sourceChip()]));
    const tabs = h('div', { class: 'stabs', role: 'tablist', 'aria-label': 'Banner list' }, TABS.map(([k, label]) => h('button', {
      class: 'stab', role: 'tab', type: 'button', id: `bt-${k}`, 'aria-selected': String(bnr.tab === k), 'aria-controls': 'bpanel', tabindex: bnr.tab === k ? 0 : -1,
      onclick: () => { bnr.tab = k; renderView(false).then(() => $(`bt-${k}`).focus()); },
      onkeydown: (e) => { const i = TABS.findIndex((x) => x[0] === bnr.tab), n = e.key === 'ArrowRight' ? i + 1 : e.key === 'ArrowLeft' ? i - 1 : e.key === 'Home' ? 0 : e.key === 'End' ? 2 : null; if (n == null) return; e.preventDefault(); bnr.tab = TABS[(n + 3) % 3][0]; renderView(false).then(() => $(`bt-${bnr.tab}`).focus()); }
    }, label)));
    const panel = h('div', { class: 'bpanel', id: 'bpanel', role: 'tabpanel', 'aria-labelledby': `bt-${bnr.tab}`, tabindex: 0 });
    root.append(tabs, panel);
    if (bnr.tab === 'past') { await pastPanel(game, panel); return root; }
    const list = all.filter((b) => (bnr.tab === 'current' ? b.status === 'live' : b.status === 'soon'));
    if (!list.length) {
      panel.append(h('p', { class: 'empty-line' }, bnr.tab === 'current' ? 'No banner is running now in the banner data.' : 'No upcoming banner announced yet.'));
      return root;
    }
    const stats = await API.windowStats(game, list.map((b) => ({ id: b.id, group: b.group, start: b.start, end: b.end })));
    panel.append(h('div', { class: 'bcards' }, list.map((b) => bannerCard(game, b, stats[b.id]))));
    return root;
  }
  function myPulls(game, b, st) {
    if (!st) return null;
    if (st.sub) { const n = /\b2$/.test(b.title) ? 2 : 1; return st.sub[n]; }
    return st.pulls;
  }
  function bannerCard(game, b, st) {
    const mine = myPulls(game, b, st);
    return h('article', { class: 'bcard' },
      h('header', { class: 'bcard-head' }, statusChip(b.status), h('h2', { class: 'bcard-title' }, h('button', { class: 'bcard-open', type: 'button', onclick: () => openBannerDetail(game, b, st) }, b.title)), b.version ? h('span', { class: 'bver' }, `v${b.version}`) : null),
      h('div', { class: 'pb-items' }, b.items.map((it) => itemChip(game, it))),
      b.rateUp.length ? h('p', { class: 'bsub' }, `Rate-up ${rarityLabel(game, 4)}: ${b.rateUp.map((r) => r.name).join(', ')}`) : null,
      h('p', { class: 'bmeta' }, liveLine(game, b), h('span', { class: 'bmine' }, `My pulls here: ${mine == null ? '-' : A.fmt(mine)}`)));
  }
  async function pastPanel(game, panel) {
    const rows = await API.pastBanners(game);
    const versions = Array.from(new Set(rows.map((r) => r.version)));
    const sel = (id, label, opts, val, on) => h('label', { class: 'fld' }, h('span', null, label), h('span', { class: 'select' }, h('select', { id, onchange: (e) => on(e.target.value) }, opts.map(([v, t]) => h('option', { value: v, selected: v === val }, t))), svg('i-chevron-down', 'ico select-chev')));
    const types = [['all', 'All banner types'], ...R[game].groups.filter((g) => g.key !== 'standard' && g.key !== 'bangboo').map((g) => [g.key, g.label])];
    panel.append(h('div', { class: 'filters' },
      sel('fVer', 'Version', [['all', 'All versions'], ...versions.map((v) => [v, `v${v}`])], bnr.version, (v) => { bnr.version = v; renderView(false); }),
      sel('fType', 'Banner type', types, bnr.type, (v) => { bnr.type = v; renderView(false); })));
    const shown = rows.filter((r) => (bnr.version === 'all' || r.version === bnr.version) && (bnr.type === 'all' || r.group === bnr.type)).sort((a, b) => Date.parse(b.start) - Date.parse(a.start));
    if (!shown.length) { panel.append(h('p', { class: 'empty-line' }, 'No past banner matches. Past banners are built from the local banner history, which grows from the first sync.')); return; }
    const stats = await API.windowStats(game, shown.map((r) => ({ id: r.id, group: r.group, start: r.start, end: r.end })));
    const tb = h('tbody', null, shown.map((r) => {
      const st = stats[r.id], vm = { ...r, status: 'ended', past: true };
      return h('tr', null,
        h('td', null, `v${r.version}`),
        h('td', null, h('button', { class: 'link-btn', type: 'button', onclick: () => openBannerDetail(game, vm, st) }, r.title)),
        h('td', null, r.items.map((i) => i.name).join(', ')),
        h('td', { class: 'dim' }, `${fmtT(Date.parse(r.start), game).slice(0, 10)} to ${fmtT(Date.parse(r.end), game).slice(0, 10)}`),
        h('td', { class: 'num' }, st ? A.fmt(st.pulls) : '-'), h('td', { class: 'num' }, st ? A.fmt(st.fives) : '-'));
    }));
    panel.append(h('div', { class: 'table-wrap' }, h('table', { class: 'dtable' }, h('caption', { class: 'visually-hidden' }, 'Past banners'),
      h('thead', null, h('tr', null, ['Version', 'Banner', `${topName(game)}`, 'Dates', 'My pulls', `My ${topName(game)}`].map((t, i) => h('th', { scope: 'col', class: i > 3 ? 'num' : null }, t)))), tb)));
    panel.append(h('p', { class: 'vnote' }, svg('i-info'), rows.some((r) => r.sample) ? 'Past banners shown here are sample entries.' : 'Past banners come from the banner data this application has stored since it first saw them; the list grows as banners end.'));
  }

  async function openBannerDetail(game, b, st) {
    const body = h('div', { class: 'bdet' }, h('p', { class: 'bdet-loading' }, 'Loading'));
    openDialog({ title: `${b.title}${b.version ? `  v${b.version}` : ''}`, body, size: 'md', actions: [{ label: 'Close', value: null }] });
    if (!st) st = (await API.windowStats(game, [{ id: b.id, group: b.group, start: b.start, end: b.end }]))[b.id];
    const w = await API.windowPulls(game, { group: b.group, start: b.start, end: b.end });
    const t = A.now(), rule = groupRule(game, b.group);
    const rows = [
      ['Status', h('span', null, statusChip(b.status), ' ', b.status === 'live' ? (b.end ? `ends in ${A.dur(Date.parse(b.end) - t)}` : 'end not announced') : b.status === 'soon' ? `starts in ${A.dur(Date.parse(b.start) - t)}` : '')],
      ['Start', fmtWhen(Date.parse(b.start), game)], ['End', b.end ? fmtWhen(Date.parse(b.end), game) : 'End not announced'],
      [`Featured ${rarityLabel(game, 5)}`, h('span', { class: 'pb-items' }, b.items.map((it) => (it.icon !== undefined || it.iconRemote ? itemChip(game, it) : h('span', { class: 'pb-item' }, it.name))))],
      b.rateUp.length ? [`Rate-up ${rarityLabel(game, 4)}`, h('span', { class: 'pb-items' }, b.rateUp.map((r) => h('span', { class: 'pb-item' }, pic([r.icon, r.iconRemote], 'pic pic-s'), r.name)))] : null
    ].filter(Boolean);
    const dl = h('dl', { class: 'bdet-dl' }, rows.map(([k, v]) => [h('dt', null, k), h('dd', null, v)]));
    const mine = h('section', { class: 'bdet-mine' }, h('h3', null, 'My pulls in this window'));
    if (!st) mine.append(h('p', { class: 'dim' }, 'No pulls stored for this game yet.'));
    else {
      mine.append(h('p', null, `${A.fmt(st.pulls)} pulls in ${rule.label}${st.sub ? ` (Wish 1: ${st.sub[1]}, Wish 2: ${st.sub[2]}; one shared counter)` : ''}. ${topName(game)} in this window: ${st.fives || 'none yet'}.`));
      if (w.rows.length) mine.append(h('div', { class: 'table-wrap' }, h('table', { class: 'dtable dtable-s' }, h('thead', null, h('tr', null, ['Time', 'Name', 'Rarity', 'Pity'].map((x) => h('th', { scope: 'col' }, x)))),
        h('tbody', null, w.rows.map((r) => h('tr', null, h('td', { class: 'dim' }, fmtT(Date.parse(r.time), game)), h('td', null, r.name), h('td', null, rarityLabel(game, r.rarity)), h('td', { class: 'num' }, r.pity)))))));
      mine.append(h('button', { class: 'btn', type: 'button', onclick: () => { finish(null); hist.game = game; Object.assign(hist, { group: b.group, rarity: '45', include3: true, from: fmtT(Date.parse(b.start), game).slice(0, 10), to: b.end ? fmtT(Date.parse(b.end), game).slice(0, 10) : '', name: '', page: 1 }); showView('history'); } }, 'Show all pulls'));
    }
    body.replaceChildren(dl, mine, h('p', { class: 'dlg-note' }, svg('i-info'), 'Pulls are matched to a banner by banner type and order first. Start times follow the provider\'s server region, so a difference of a few hours is tolerated.'),
      h('p', { class: 'dlg-note' }, sourceChip()));
  }

  /* ---- History (IA S6) ----------------------------------------------------------------------------------------- */
  const HIST_COLS = [['n', '#'], ['time', 'Time'], ['name', 'Name'], ['rarity', 'Rarity'], ['group', 'Banner'], ['pity', 'Pity'], ['result', 'Result']];
  const field = (label, ctl) => h('label', { class: 'fld' }, h('span', null, label), ctl);
  const selectCtl = (id, opts, val) => h('span', { class: 'select' }, h('select', { id }, opts.map(([v, t]) => h('option', { value: v, selected: String(v) === String(val) }, t))), svg('i-chevron-down', 'ico select-chev'));
  function download(name, mime, text) {
    const a = h('a', { href: URL.createObjectURL(new Blob([text], { type: mime })), download: name });
    document.body.append(a); a.click(); setTimeout(() => { URL.revokeObjectURL(a.href); a.remove(); }, 500);
  }
  async function saveOrDownload(res) {
    if (res && res.cancelled) return;
    if (res && res.error && !res.text) { A.toast(`The file could not be saved: ${res.error}`); return; }
    if (!res || res.unavailable) { A.toast('Saving a file works in the desktop application.'); return; }
    if (res.empty) { A.toast('No pulls stored to export.'); return; }
    if (res.path) { A.toast(`Written to ${res.path}`); return; }
    download(res.name, res.mime, res.text); A.toast(`Downloaded ${res.name}`);
  }
  /* The 5-stars of the filter on a time axis, height = pity (the table below has the same data). */
  function timelineCard(game, tl) {
    const rules = R[game], cap = Math.max(...rules.groups.map((g) => g.hard)), W = 1000, H = 118, L = 38, Rt = 18, T = 12, B = 26;
    const se = (tag, attrs, ...kids) => { const e = document.createElementNS(NS, tag); Object.keys(attrs || {}).forEach((k) => e.setAttribute(k, attrs[k])); kids.forEach((k) => e.append(k)); return e; };
    const ms = tl.map((x) => Date.parse(x.time)), t0 = Math.min(...ms), t1 = Math.max(...ms);
    const xOf = (t) => (t1 === t0 ? (L + W - Rt) / 2 : L + ((t - t0) / (t1 - t0)) * (W - L - Rt)), yOf = (p) => T + (1 - Math.min(p, cap) / cap) * (H - T - B);
    const day = (t) => fmtT(t, game).slice(0, 10);
    const box = se('svg', { class: 'tl-svg', viewBox: `0 0 ${W} ${H}`, role: 'img', 'aria-label': `${tl.length} five-star pulls from ${day(t0)} to ${day(t1)}, pity from ${Math.min(...tl.map((x) => x.pity))} to ${Math.max(...tl.map((x) => x.pity))}.` });
    for (let p = 0; p <= cap; p += 30) {
      box.append(se('line', { class: p ? 'tl-grid' : 'tl-axis', x1: L, x2: W - Rt, y1: yOf(p), y2: yOf(p) }));
      const lab = se('text', { class: 'tl-txt', x: L - 8, y: yOf(p) + 4, 'text-anchor': 'end' }); lab.textContent = String(p); box.append(lab);
    }
    const l0 = se('text', { class: 'tl-txt', x: L, y: H - 8 }); l0.textContent = day(t0); box.append(l0);
    if (t1 !== t0) { const l1 = se('text', { class: 'tl-txt', x: W - Rt, y: H - 8, 'text-anchor': 'end' }); l1.textContent = day(t1); box.append(l1); }
    tl.forEach((x, i) => {
      const tip = se('title'); tip.textContent = `${x.name}: pity ${x.lowerBound ? 'at least ' : ''}${x.pity}, ${groupRule(game, x.group).short}, ${fmtT(ms[i], game)}${x.kind ? `, ${{ won: 'won', wonG: 'won (guaranteed)', lost: 'lost' }[x.kind] || ''}` : ''}`;
      if (x.kind === 'lost') box.append(se('circle', { class: 'tl-ring', cx: xOf(ms[i]), cy: yOf(x.pity), r: 10 }));
      box.append(se('circle', { class: `tl-dot tl-${pityLevel(x.pity)}${x.lowerBound ? ' is-lb' : ''}`, cx: xOf(ms[i]), cy: yOf(x.pity), r: 5.5 }, tip));
    });
    return h('section', { class: 'tl-card', 'aria-label': '5-star timeline' },
      h('div', { class: 'tl-head' }, h('strong', null, `${game === 'zzz' ? 'S-Rank' : rarityLabel(game, 5)} timeline`), h('span', null, `${A.fmt(tl.length)} found. Height is the pity of each one; green, amber and red follow the pity colors of Settings. Hollow: lower bound. Ring: lost the 50/50 or 75/25.`)),
      box);
  }

  async function historyView(game) {
    if (hist.game !== game) Object.assign(hist, { game, group: 'all', rarity: '5', from: '', to: '', name: '', include3: false, sort: { col: 'time', dir: 'desc' }, page: 1 });
    const [res] = await Promise.all([API.history(game, hist), refreshCore()]);
    const rules = R[game], root = h('div', { class: 'vroot' }, vhead('History', game, [sourceChip()]));
    if (res.empty) { root.append(emptyHistory(game, 'History')); return root; }
    const rar = game === 'zzz' ? [['5', 'S-Rank'], ['4', 'A-Rank'], ['45', 'S-Rank and A-Rank']] : [['5', '5★'], ['4', '4★'], ['45', '4★ and 5★']];
    const read = () => ({ group: $('hGroup').value, rarity: $('hRar').value, from: $('hFrom').value, to: $('hTo').value, name: $('hName').value, include3: $('hInc').checked });
    const apply = () => { Object.assign(hist, read(), { page: 1 }); renderView(false).then(() => { const n = $('hApply'); if (n) n.focus(); }); };
    const form = h('form', { class: 'filters', onsubmit: (e) => { e.preventDefault(); apply(); } },
      field('Banner', selectCtl('hGroup', [['all', 'All banners'], ...rules.groups.map((g) => [g.key, g.label])], hist.group)),
      field('Rarity', selectCtl('hRar', rar, hist.rarity)),
      field('From', h('input', { class: 'in', type: 'date', id: 'hFrom', value: hist.from })),
      field('To', h('input', { class: 'in', type: 'date', id: 'hTo', value: hist.to })),
      field('Name', h('input', { class: 'in', type: 'search', id: 'hName', value: hist.name, placeholder: 'Search', autocomplete: 'off' })),
      h('label', { class: 'fld fld-check' }, h('input', { type: 'checkbox', id: 'hInc', checked: hist.include3 }), h('span', null, `Include ${rarityLabel(game, 3)}`)),
      h('div', { class: 'fld-act' }, h('button', { class: 'btn btn-primary', type: 'submit', id: 'hApply' }, 'Apply'),
        h('button', { class: 'btn', type: 'button', onclick: () => { Object.assign(hist, { group: 'all', rarity: '5', from: '', to: '', name: '', include3: false, page: 1 }); renderView(false); } }, 'Reset')));
    root.append(form);
    if (res.timeline && res.timeline.length) root.append(timelineCard(game, res.timeline));

    const sortBtn = (col, label) => {
      const on = hist.sort.col === col;
      return h('th', { scope: 'col', 'aria-sort': on ? (hist.sort.dir === 'asc' ? 'ascending' : 'descending') : 'none', class: ['pity', 'n'].includes(col) ? 'num' : null },
        h('button', { class: 'sortbtn', type: 'button', onclick: () => { hist.sort = { col, dir: on ? (hist.sort.dir === 'asc' ? 'desc' : 'asc') : (['name', 'group', 'result'].includes(col) ? 'asc' : 'desc') }; hist.page = 1; renderView(false).then(() => { const b = view.querySelector(`th[data-col="${col}"] button`); if (b) b.focus(); }); } },
          label, on ? svg('i-chevron-down', `ico sort-ico ${hist.sort.dir === 'asc' ? 'is-up' : ''}`) : null));
    };
    const head = h('tr', null, HIST_COLS.map(([c, l]) => { const th = sortBtn(c, l); th.dataset.col = c; return th; }));
    const body = h('tbody', null, res.rows.map((r) => h('tr', { class: `rar-${r.rarity}` },
      h('td', { class: 'num dim' }, r.n), h('td', { class: 'dim nowrap' }, fmtT(Date.parse(r.time), game)), h('td', { class: 'nm' }, r.name),
      h('td', null, rarityLabel(game, r.rarity)), h('td', { class: 'dim' }, groupRule(game, r.group).short),
      h('td', { class: 'num' }, `${r.lowerBound ? '≥' : ''}${r.pity}`),
      h('td', null, r.result ? h('span', { class: `res res-${r.result.kind}`, title: r.result.assumed ? 'The guarantee before the first stored 5-star is unknown, so a 50/50 is assumed.' : null }, r.result.text, r.result.assumed ? ' (?)' : '') : ''))));
    if (!res.rows.length) root.append(h('p', { class: 'empty-line' }, 'No pull matches these filters.'));
    else root.append(h('div', { class: 'table-wrap hist-wrap' }, h('table', { class: 'dtable hist' }, h('caption', { class: 'visually-hidden' }, `Pull history of ${rules.title}`), h('thead', null, head), body)));
    const from = res.total ? (res.page - 1) * res.pageSize + 1 : 0, to = Math.min(res.total, res.page * res.pageSize);
    const go = (p) => { hist.page = p; renderView(false).then(() => { $('main').scrollTop = 0; }); };
    root.append(h('div', { class: 'pager-row' },
      h('span', { class: 'pr-count', 'aria-live': 'polite' }, `Rows ${from}-${to} of ${A.fmt(res.total)}`),
      h('div', { class: 'pr-nav' }, h('button', { class: 'btn', type: 'button', disabled: res.page <= 1, onclick: () => go(res.page - 1), 'aria-label': 'Previous page' }, svg('i-arrow-left')),
        h('span', { class: 'pr-page' }, `Page ${res.page} of ${res.pages}`),
        h('button', { class: 'btn', type: 'button', disabled: res.page >= res.pages, onclick: () => go(res.page + 1), 'aria-label': 'Next page' }, svg('i-arrow-right'))),
      h('button', { class: 'btn', type: 'button', disabled: !res.total, onclick: async () => saveOrDownload(await API.historyExport(game, hist)) }, 'Export filtered rows')));
    const notes = [];
    if (res.hasLowerBound) notes.push('≥NN: lower bound. The oldest stored segment may be incomplete.');
    if (res.hasAssumed) notes.push('(?): the guarantee before the first stored 5-star is unknown, so a 50/50 is assumed.');
    notes.push('Click a column header to sort. Pity is counted in the segment since the last 5-star of the same banner type.');
    root.append(h('p', { class: 'vnote' }, svg('i-info'), notes.join(' ')));
    return root;
  }

  /* ---- Stats (IA S7) ------------------------------------------------------------------------------------------------ */
  async function statsView(game) {
    if (stat.game !== game) Object.assign(stat, { game, group: 'all', dates: 'all' });
    const [s] = await Promise.all([API.stats(game, stat), refreshCore()]);
    const rules = R[game], root = h('div', { class: 'vroot' }, vhead('Stats', game, [sourceChip()]));
    if (s.empty) { root.append(emptyHistory(game, 'Stats')); return root; }
    root.append(h('div', { class: 'filters' },
      field('Scope', h('span', { class: 'select' }, h('select', { id: 'sGroup', onchange: (e) => { stat.group = e.target.value; renderView(false).then(() => $('sGroup').focus()); } }, [['all', 'All banners'], ...rules.groups.map((g) => [g.key, g.label])].map(([v, t]) => h('option', { value: v, selected: v === stat.group }, t))), svg('i-chevron-down', 'ico select-chev'))),
      field('Dates', h('span', { class: 'select' }, h('select', { id: 'sDates', onchange: (e) => { stat.dates = e.target.value; renderView(false).then(() => $('sDates').focus()); } }, [['all', 'All time'], ['12m', 'Last 12 months'], ['90d', 'Last 90 days'], ['30d', 'Last 30 days']].map(([v, t]) => h('option', { value: v, selected: v === stat.dates }, t))), svg('i-chevron-down', 'ico select-chev')))));
    if (!s.fives) { root.append(h('p', { class: 'empty-line' }, `No ${topName(game)} in this scope and period.`), h('p', { class: 'vnote' }, svg('i-info'), `${A.fmt(s.pulls)} pulls, ${A.fmt(s.spent)} ${s.currency} (estimated).`)); return root; }
    const lb = s.avgLowerBound ? '≥' : '';
    const tile = (k, v, sub) => h('div', { class: 'tile' }, h('dt', null, k), h('dd', null, v), sub ? h('small', null, sub) : null);
    root.append(h('dl', { class: 'tiles' },
      tile(`${topName(game)} found`, A.fmt(s.fives)), tile('Average pity', `${lb}${s.avg.toFixed(2)}`), tile('Best', s.best), tile('Worst', s.worst), tile('Luck grade', s.grade)));
    root.append(h('p', { class: 'vnote' }, svg('i-info'),
      s.official ? `Official consolidated average for comparison: ${s.official.map((o) => `${o.kind} banner ${o.pulls} pulls per ${topName(game)} (${o.pct}%)`).join(', ')}.` : `No official average is stored in the app for ${rules.title} yet.`,
      s.avgLowerBound ? ' Segments that start before the oldest stored pull are lower bounds (≥) and are included.' : ''));
    const max = Math.max(1, ...s.bins.map((b) => b.n));
    root.append(h('section', { class: 'panel', 'aria-labelledby': 'dist-h' }, h('h2', { id: 'dist-h' }, `${topName(game)} by pity range`),
      h('ul', { class: 'bars' }, s.bins.map((b) => h('li', { class: 'bar-row' }, h('span', { class: 'bar-l' }, b.label),
        h('span', { class: 'bar-t', 'aria-hidden': 'true' }, h('span', { class: 'bar-f', style: `width:${(b.n / max) * 100}%` })), h('span', { class: 'bar-n' }, b.n))))));
    root.append(h('section', { class: 'panel', 'aria-labelledby': 'flip-h' }, h('h2', { id: 'flip-h' }, 'Flips'),
      s.flips.length ? h('ul', { class: 'flips' }, s.flips.map((f) => h('li', null, h('b', null, `${f.odds}, ${f.label}`), `  Won ${f.won}  Lost ${f.lost}  (${f.won + f.lost} ${f.won + f.lost === 1 ? 'flip' : 'flips'})`))) : h('p', { class: 'dim' }, 'No banner type with a flip in this scope.'),
      h('p', { class: 'set-note' }, `Guaranteed ${topName(game)} are not counted as flips. The first stored ${topName(game)} of a group is counted as a flip because its guarantee state is unknown.`)));
    const g = core.settings.grade;
    root.append(h('section', { class: 'panel', 'aria-labelledby': 'spend-h' }, h('h2', { id: 'spend-h' }, 'Spent (estimated)'),
      h('p', null, `${A.fmt(s.pulls)} pulls x ${s.pullCost} = ${A.fmt(s.spent)} ${s.currency}`),
      h('p', { class: 'set-note' }, `Grade scale (editable in Settings), by average pity: under ${g.ss} SS, up to ${g.a} A, up to ${g.b} B, up to ${g.c} C, above ${g.c} F.`)));
    return root;
  }

  /* ---- Sidebar: screens, account, sync -------------------------------------------------------------------------------- */
  function renderRail() {
    const game = A.state.game, a = accountOf(game), label = $('accountLabel'), acct = $('accountBtn'), btn = $('syncBtn');
    if (a) {
      label.textContent = maskUid(a.uid); acct.title = `Account ${maskUid(a.uid)}${a.nickname ? ` (${a.nickname})` : ''}. Open the account settings.`;
      const last = core.lastSync[game];
      btn.title = last ? `Synced ${ago(A.now() - Date.parse(last))} ago${API.mode === 'sample' ? ' (sample)' : ''}` : 'Never synced'; btn.classList.toggle('is-attention', !last);
    } else { label.textContent = 'No account'; acct.title = 'No account yet. It appears after the first sync.'; btn.title = 'Never synced'; btn.classList.add('is-attention'); }
  }
  function openSettings(tab) { document.dispatchEvent(new CustomEvent('pt:open', { detail: { modal: tab } })); }
  function openSync(auto) { document.dispatchEvent(new CustomEvent('pt:open', { detail: { modal: 'sync', auto: !!auto } })); }

  document.querySelectorAll('.nav-btn[data-screen]').forEach((b) => b.addEventListener('click', () => {
    const s = b.dataset.screen.toLowerCase();
    if (s === 'settings') openSettings('settings'); else showView(s);
  }));
  $('brand').addEventListener('click', () => showView('main', { focus: false }));
  $('syncBtn').addEventListener('click', () => openSync(!!accountOf(A.state.game)));
  $('accountBtn').addEventListener('click', () => openSettings('settings'));
  document.addEventListener('pt:game', () => { renderRail(); if (current !== 'main') renderView(false); });
  document.addEventListener('pt:data', () => { refreshCore().then(() => { if (current !== 'main') renderView(false); }); });   // settings saved, sync done, account removed
  setInterval(() => {   // keep the "in 11d 10h" figures current without redrawing
    const t = A.now(); document.querySelectorAll('[data-left]').forEach((n) => { n.textContent = A.dur(Math.max(0, Date.parse(n.dataset.left) - t)); });
  }, 30000);

  /* ---- Shared with js/panels.js ---------------------------------------------------------------------------------------- */
  window.PT_UI = { h, svg, pic, core, refreshCore, fmtT, fmtWhen, ago, maskUid, accountOf, openDialog, confirmDialog, showErrorDialog, errorInfo, finish, showView, openSync, saveOrDownload, topName, chip, sourceChip, field, selectCtl, groupRule };

  /* ---- Start ------------------------------------------------------------------------------------------------------------ */
  refreshCore().then(() => {
    const v = A.qs.get('view');
    if (VIEWS[v]) showView(v, { focus: false });
  });
})();
