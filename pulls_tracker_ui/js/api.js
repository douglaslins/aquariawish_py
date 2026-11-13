/*
  Data layer of the Pulls Tracker screens.

  The screens (js/views.js) never read pulls, settings or accounts themselves: they call window.PT_API, which returns Promises.
  Two providers sit behind it:
    backend  the Python application (pywebview js_api). Used when the page is opened with ?backend=1 (see README, "Backend contract").
    sample   this file. Runs in a browser with no backend: with ?state=synced it serves SAMPLE data (made up, not the user's pulls,
             matching the pity of js/data.js sampleHistory); without it the account has no pulls yet. Settings are kept in localStorage.

  PT_RULES   game rules the screens need (pity caps, group names, rarity labels). The backend has its own copy of the numbers.
  PT_EVENTS  one-way channel for events that arrive while a call runs (sync progress): PT_EVENTS.on(fn) and PT_EVENTS.emit(event).
             The backend calls window.PT_EVENTS.emit({...}) through evaluate_js.

  Sample-only URL options: ?demo=sync (a sync run works with no pulls stored), ?fail=expired|cache|rate|uid (a sync run ends with
  that error), ?speed=0.1 (sync steps run 10 times faster).
*/
(() => {
  'use strict';

  const qs = window.PT_PARAMS;
  const synced = qs.get('state') === 'synced';
  const clockOffset = qs.get('now') ? Date.parse(qs.get('now')) - Date.now() : 0;
  const now = () => Date.now() + clockOffset;
  const MIN = 60000, DAY = 86400000;
  const DATA = window.PT_DATA;
  const BANNERS = () => (window.PT_BANNERS && window.PT_BANNERS.slides) || {};
  const speed = Number(qs.get('speed')) > 0 ? Number(qs.get('speed')) : 1;

  /* ---- Events ------------------------------------------------------------------------------------ */
  const listeners = [];
  window.PT_EVENTS = {
    on(fn) { listeners.push(fn); },
    emit(ev) { listeners.slice().forEach((fn) => { try { fn(ev); } catch (e) { console.error(e); } }); }
  };

  /* ---- Rules ------------------------------------------------------------------------------------- */
  const grp = (key, label, short, hard, flip, extra = {}) => ({ key, label, short, hard, flip, ...extra });
  const RULES = {
    genshin: {
      title: 'Genshin Impact', currency: 'Primogems', pull: 160, rarity: { 5: '5★', 4: '4★', 3: '3★' },
      official: { character: { pct: 1.6, pulls: 62.5 }, weapon: { pct: 1.85, pulls: 54.1 } },
      groups: [
        grp('character', 'Character Event Wish', 'Character Event', 90, '50/50', { soft: true, note: 'Wish 1 and Wish 2 share one pity counter.' }),
        grp('weapon', 'Weapon Event Wish', 'Weapon Event', 80, '75/25', { soft: true }),
        grp('standard', 'Standard Wish', 'Standard', 90, null)
      ]
    },
    hsr: {
      title: 'Honkai: Star Rail', currency: 'Stellar Jade', pull: 160, rarity: { 5: '5★', 4: '4★', 3: '3★' }, official: null,
      groups: [
        grp('character', 'Character Warp', 'Character Warp', 90, '50/50', { soft: true }),
        grp('lightcone', 'Light Cone Warp', 'Light Cone Warp', 80, '75/25', { soft: true }),
        grp('standard', 'Stellar Warp', 'Stellar Warp', 90, null)
      ]
    },
    zzz: {
      title: 'Zenless Zone Zero', currency: 'Polychrome', pull: 160, rarity: { 5: 'S', 4: 'A', 3: 'B' }, official: null,
      groups: [
        grp('exclusive', 'Exclusive Channel', 'Exclusive', 90, '50/50', { soft: true }),
        grp('wengine', 'W-Engine Channel', 'W-Engine', 80, '75/25', { soft: true }),
        grp('bangboo', 'Bangboo Channel', 'Bangboo', 80, null, { note: 'Every S-Rank is the selected Bangboo.' }),
        grp('standard', 'Stable Channel', 'Stable', 90, null)
      ]
    }
  };
  window.PT_RULES = RULES;
  const groupOf = (game, key) => RULES[game].groups.find((g) => g.key === key);

  /* ---- Settings ---------------------------------------------------------------------------------- */
  const DEFAULT_SETTINGS = {
    language: 'English', times: 'Local',
    track: { genshin: true, hsr: true, zzz: true },
    folders: { genshin: '', hsr: '', zzz: '' },
    soft: { genshin: { character: 74, weapon: 63 }, hsr: { character: 74, lightcone: 66 }, zzz: { exclusive: 74, wengine: 65 } },
    pityColors: { green: 50, gold: 75 },
    grade: { ss: 50, a: 60, b: 73, c: 76 },
    lossPool: ['Himeko', 'Welt', 'Bronya', 'Gepard', 'Clara', 'Yanqing', 'Bailu'],
    maskUid: true, backupFolder: '', autoBackup: true,
    beta: false, hoyolab: false, server: ''
  };
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const merge = (base, over) => {
    const out = clone(base);
    if (over && typeof over === 'object') Object.keys(out).forEach((k) => {
      if (over[k] === undefined) return;
      out[k] = (out[k] && typeof out[k] === 'object' && !Array.isArray(out[k])) ? merge(out[k], over[k]) : over[k];
    });
    return out;
  };
  const SETTINGS_KEY = 'pt.v1.settings';
  const loadSettings = () => { try { return merge(DEFAULT_SETTINGS, JSON.parse(localStorage.getItem(SETTINGS_KEY))); } catch (e) { return clone(DEFAULT_SETTINGS); } };

  /* ---- Sample history (only with ?state=synced) --------------------------------------------------- */
  // One entry per pity group, oldest first: [pity of the 5-star, name, featured item?]; trail = pulls since the last 5-star.
  // The trailing values and guarantee are the ones of data.js sampleHistory.pity, so the Main screen and the Dashboard agree.
  const SPEC = {
    genshin: {
      character: { seg: [[77, 'Mona', 0], [62, 'Sample 5★ A', 1], [31, 'Sample 5★ B', 1], [88, 'Diluc', 0], [80, 'Sample 5★ C', 1]], trail: 62 },
      weapon:    { seg: [[62, 'Sample 5★ D', 0], [47, 'Sample 5★ E', 1]], trail: 31 },
      standard:  { seg: [[51, 'Sample 5★ F', 0]], trail: 17 }
    },
    hsr: {
      character: { seg: [[64, 'Sample 5★ A', 1], [78, 'Sample 5★ B', 1], [52, 'Welt', 0], [71, 'Sample 5★ C', 1], [40, 'Himeko', 0]], trail: 18 },
      lightcone: { seg: [[55, 'Sample 5★ D', 1], [66, 'Sample 5★ E', 0], [49, 'Sample 5★ F', 1]], trail: 9 },
      standard:  { seg: [[33, 'Clara', 0]], trail: 12 }
    },
    zzz: {
      exclusive: { seg: [[70, 'Sample S-Rank A', 1], [58, 'Sample S-Rank B', 0], [83, 'Sample S-Rank C', 1], [34, 'Sample S-Rank D', 1]], trail: 44 },
      wengine:   { seg: [[51, 'Sample S-Rank E', 1], [72, 'Sample S-Rank F', 0], [44, 'Sample S-Rank G', 1]], trail: 12 },
      bangboo:   { seg: [[38, 'Sample S-Rank Bangboo', 1]], trail: 9 },
      standard:  { seg: [[60, 'Sample S-Rank H', 0]], trail: 52 }
    }
  };
  const NAMES = {
    genshin: {
      four: { all: ['Bennett', 'Xiangling', 'Barbara', 'Fischl', 'Noelle', 'Sucrose'], weapon: ['The Flute', 'Favonius Sword', 'Rainslasher', 'Sacrificial Bow', 'Favonius Codex'] },
      three: ['Cool Steel', 'Harbinger of Dawn', 'Debate Club', 'Slingshot', 'Raven Bow', 'Magic Guide', 'Emerald Orb', 'Skyrider Sword', 'Ferrous Shadow']
    },
    hsr: {
      four: { all: ['Natasha', 'Hook', 'Pela', 'Sampo', 'Asta', 'Serval', 'Dan Heng', 'Tingyun', 'Qingque', 'Yukong'], lightcone: ['Post-Op Conversation', 'Planetary Rendezvous', 'Boundless Choreo', 'Good Night and Sleep Well', 'Make the World Clamor'] },
      three: ['Arrows', 'Cornucopia', 'Collapsing Sky', 'Amber', 'Void', 'Chorus', 'Data Bank', 'Darting Arrow', 'Fine Fruit', 'Shattered Home', 'Defense', 'Multiplication', 'Mutual Demise', 'Pioneering', 'Sagacity']
    },
    zzz: {
      four: { all: ['Anby', 'Nicole', 'Billy', 'Corin', 'Ben', 'Lucy', 'Piper', 'Anton'], wengine: ['Sample A-Rank W-Engine 1', 'Sample A-Rank W-Engine 2', 'Sample A-Rank W-Engine 3'] },
      three: ['Sample B-Rank item 1', 'Sample B-Rank item 2', 'Sample B-Rank item 3', 'Sample B-Rank item 4']
    }
  };
  const EXTRA = {   // sample values for the estimated or game-specific counters shown on the Dashboard
    genshin: { character: { radiance: 1 }, weapon: { path: { target: 'Azurelight', point: 0, max: 1 } }, standard: { done: 'Beginner Wish: completed' } },
    hsr: { standard: { done: 'Departure Warp: completed' } },
    zzz: { bangboo: { selected: 'the selected Bangboo' } }
  };

  const rng = (seed) => { let s = seed >>> 0; return () => ((s = (Math.imul(s, 1664525) + 1013904223) >>> 0) / 4294967296); };
  const pick = (list, r) => list[Math.floor(r() * list.length)];

  const sample = { built: false, pulls: {}, accounts: [], derived: {}, settings: null, imported: {} };

  function makePulls(game, key, spec, seed) {
    const r = rng(1000 + seed * 77), N = NAMES[game];
    const four = N.four[key] || N.four.all;
    const seq = []; let since4 = 0;
    const fill = (count) => {
      for (let i = 0; i < count; i++) {
        since4++;
        if (since4 >= 10 || r() < 0.1) { since4 = 0; seq.push({ rarity: 4, name: pick(four, r) }); } else seq.push({ rarity: 3, name: pick(N.three, r) });
      }
    };
    spec.seg.forEach(([pity, name]) => { fill(pity - 1); since4 = 0; seq.push({ rarity: 5, name }); });
    fill(spec.trail);
    // times: sessions of 10 pulls (sometimes fewer), counted back from 40 minutes before now
    let t = now() - 40 * MIN, left = 0;
    const gi = Object.keys(RULES).indexOf(game), ki = RULES[game].groups.findIndex((g) => g.key === key);
    for (let i = seq.length - 1; i >= 0; i--) {
      if (left === 0) { if (i !== seq.length - 1) t -= (0.5 + r() * 1.5) * DAY; left = r() < 0.6 ? 10 : 1 + Math.floor(r() * 9); }
      seq[i].time = t; t -= 1500; left--;
      seq[i].n = i + 1;
      seq[i].id = `17${gi}${ki}${String(i + 1).padStart(7, '0')}`;
      if (game === 'genshin' && key === 'character') seq[i].sub = r() < 0.6 ? 1 : 2;   // Wish 1 or Wish 2 (banner types 301 and 400)
    }
    return seq;
  }

  function build() {
    if (sample.built) return;
    sample.built = true; sample.settings = loadSettings();
    if (!synced) return;
    Object.keys(SPEC).forEach((game, gi) => {
      sample.pulls[game] = {};
      Object.keys(SPEC[game]).forEach((key, ki) => { sample.pulls[game][key] = makePulls(game, key, SPEC[game][key], gi * 10 + ki); });
      sample.accounts.push({ game, uid: DATA.sampleHistory.account, region: 'Asia', nickname: game === 'genshin' ? 'Main' : '' });
    });
  }

  // Pity, guarantee and the result of every 5-star, oldest to newest (rebuild analysis section 5).
  function derive(game, key) {
    const id = `${game}/${key}`;
    if (sample.derived[id]) return sample.derived[id];
    const raw = (sample.pulls[game] && sample.pulls[game][key]) || [], rule = groupOf(game, key), spec = SPEC[game][key];
    let pity = 0, seg = 0, guar = null;   // null: the guarantee before the first stored 5-star is unknown
    const rows = raw.map((p) => {
      pity++;
      const row = { ...p, group: key, pity, lb: seg === 0 && !rule.noLb, result: null };
      if (p.rarity === 5) {
        const featured = spec.seg[seg] ? spec.seg[seg][2] : 1;
        if (rule.flip) {
          if (guar === true) { row.result = { text: 'WON guaranteed', kind: 'wonG', flip: false, assumed: false }; guar = false; }
          else {
            const assumed = guar === null;
            row.result = featured ? { text: `WON ${rule.flip}`, kind: 'won', flip: true, assumed } : { text: `LOST ${rule.flip}`, kind: 'lost', flip: true, assumed };
            guar = !featured;
          }
        }
        seg++; pity = 0;
      }
      return row;
    });
    return (sample.derived[id] = { rows, pity, guarantee: rule.flip ? guar : null, fives: rows.filter((r) => r.rarity === 5).length });
  }
  const allRows = (game, keys) => {
    const out = [];
    (keys || RULES[game].groups.map((g) => g.key)).forEach((k) => { if (sample.pulls[game]) out.push(...derive(game, k).rows); });
    return out;
  };
  const toRow = (r) => ({ id: r.id, n: r.n, time: new Date(r.time).toISOString(), name: r.name, rarity: r.rarity, group: r.group, pity: r.pity, lowerBound: r.lb, result: r.result, sub: r.sub || null });
  const sinceOf = (dates) => ({ all: 0, '12m': now() - 365 * DAY, '90d': now() - 90 * DAY, '30d': now() - 30 * DAY }[dates] || 0);
  const hasPulls = (game) => !!sample.pulls[game];

  /* ---- Stats ------------------------------------------------------------------------------------- */
  function computeStats(game, { group = 'all', dates = 'all' } = {}) {
    const rules = RULES[game], S = sample.settings, since = sinceOf(dates);
    const keys = group === 'all' ? rules.groups.map((g) => g.key) : [group];
    const rows = allRows(game, keys).filter((r) => r.time >= since);
    const fives = rows.filter((r) => r.rarity === 5);
    const total = fives.reduce((a, r) => a + r.pity, 0);
    const avg = fives.length ? total / fives.length : null;
    let grade = null;
    if (avg != null) grade = avg < S.grade.ss ? 'SS' : avg <= S.grade.a ? 'A' : avg <= S.grade.b ? 'B' : avg <= S.grade.c ? 'C' : 'F';
    const step = 10, hardMax = Math.max(...keys.map((k) => groupOf(game, k).hard));
    const bins = [];
    for (let lo = 1; lo <= hardMax; lo += step) bins.push({ label: `${lo} - ${Math.min(lo + step - 1, hardMax)}`, from: lo, to: lo + step - 1, n: fives.filter((r) => r.pity >= lo && r.pity <= lo + step - 1).length });
    const flips = keys.map((k) => groupOf(game, k)).filter((g) => g.flip).map((g) => {
      const f = fives.filter((r) => r.group === g.key && r.result && r.result.flip);
      return { group: g.key, label: g.short, odds: g.flip, won: f.filter((r) => r.result.kind === 'won').length, lost: f.filter((r) => r.result.kind === 'lost').length };
    });
    const official = rules.official ? Object.keys(rules.official).map((k) => ({ kind: k, ...rules.official[k] })) : null;
    return {
      group, dates, pulls: rows.length, fives: fives.length, avg, avgLowerBound: fives.some((r) => r.lb), best: fives.length ? Math.min(...fives.map((r) => r.pity)) : null,
      worst: fives.length ? Math.max(...fives.map((r) => r.pity)) : null, grade, bins, flips, official, spent: rows.length * rules.pull, currency: rules.currency, pullCost: rules.pull
    };
  }

  /* ---- Sample provider ---------------------------------------------------------------------------- */
  const delay = (v, ms = 40) => new Promise((res) => setTimeout(() => res(v), ms));
  const sampleApi = {
    mode: 'sample',

    state() {
      build();
      const acc = sample.accounts.map((a) => ({ ...a }));
      const last = {};
      acc.forEach((a) => { last[a.game] = new Date(now() - DATA.sampleHistory.syncedMinutesAgo * MIN).toISOString(); });
      return delay({ synced: acc.length > 0, accounts: acc, lastSync: last });
    },

    dashboard(game) {
      build();
      if (!hasPulls(game)) return delay({ game, empty: true });
      const groups = RULES[game].groups.map((g) => {
        const d = derive(game, g.key);
        return { key: g.key, pity: d.pity, guarantee: d.guarantee, pulls: d.rows.length, lowerBound: d.fives === 0, extra: (EXTRA[game] && EXTRA[game][g.key]) || null };
      });
      const st = computeStats(game);
      const oldest = Math.min(...allRows(game).map((r) => r.time));
      return delay({
        game, empty: false, groups,
        summary: { pulls: st.pulls, fives: st.fives, avg: st.avg, avgLowerBound: st.avgLowerBound, grade: st.grade, flips: st.flips, spent: st.spent, currency: st.currency },
        oldest: new Date(oldest).toISOString(),
        bannerSource: { label: 'public calendar API', at: window.PT_BANNERS && window.PT_BANNERS.generatedAt || null }
      });
    },

    // counts of the pulls of one pity group inside a banner window; items: [{id, group, start, end}]
    windowStats(game, items) {
      build();
      const out = {};
      items.forEach((it) => {
        const a = it.start ? Date.parse(it.start) : 0, b = it.end ? Date.parse(it.end) : Infinity;
        const rows = hasPulls(game) ? derive(game, it.group).rows.filter((r) => r.time >= a && r.time <= b) : null;
        out[it.id] = rows ? { pulls: rows.length, fives: rows.filter((r) => r.rarity === 5).length, fours: rows.filter((r) => r.rarity === 4).length,
          sub: rows.some((r) => r.sub) ? { 1: rows.filter((r) => r.sub === 1).length, 2: rows.filter((r) => r.sub === 2).length } : null } : null;
      });
      return delay(out);
    },

    // 4-star and 5-star pulls of one pity group inside a window, newest first
    windowPulls(game, { group, start, end, limit = 8 }) {
      build();
      if (!hasPulls(game)) return delay({ total: 0, rows: [] });
      const a = start ? Date.parse(start) : 0, b = end ? Date.parse(end) : Infinity;
      const rows = derive(game, group).rows.filter((r) => r.time >= a && r.time <= b && r.rarity >= 4).sort((x, y) => y.time - x.time);
      return delay({ total: rows.length, rows: rows.slice(0, limit).map(toRow) });
    },

    // banners that ended, from the local banner history (sample: three per game, ending before the current banners start)
    pastBanners(game) {
      build();
      const cur = (BANNERS()[game] || []).map((s) => Date.parse(s.start)).filter(Number.isFinite);
      const base = cur.length ? Math.min(...cur) : now();
      const V = { genshin: ['7.0', '6.8', '6.7'], hsr: ['4.5', '4.4', '4.3'], zzz: ['3.2', '3.1', '3.0'] }[game];
      const rows = [];
      V.forEach((v, i) => {
        const end = base - i * 21 * DAY, start = end - 21 * DAY;
        RULES[game].groups.filter((g) => g.key !== 'standard' && g.key !== 'bangboo').forEach((g, gi) => {
          rows.push({ id: `past-${game}-${v}-${g.key}`, version: v, title: g.label, group: g.key, kind: gi === 0 ? 'character' : 'weapon',
            items: [{ name: `Sample ${game === 'zzz' ? 'S-Rank' : '5★'} ${String.fromCharCode(75 + i * 2 + gi)}`, rank: 5 }],
            rateUp: [], start: new Date(start).toISOString(), end: new Date(end).toISOString(), sample: true });
        });
      });
      return delay(rows);
    },

    history(game, q = {}) {
      build();
      if (!hasPulls(game)) return delay({ empty: true, total: 0, rows: [], page: 1, pages: 1, pageSize: 20 });
      const keys = !q.group || q.group === 'all' ? null : [q.group];
      const wantRarity = { '5': [5], '4': [4], '45': [4, 5] }[q.rarity || '5'] || [5];
      const from = q.from ? Date.parse(q.from + 'T00:00:00') : 0, to = q.to ? Date.parse(q.to + 'T23:59:59') : Infinity;
      const name = (q.name || '').trim().toLowerCase();
      let rows = allRows(game, keys).filter((r) => (wantRarity.includes(r.rarity) || (q.include3 && r.rarity === 3)) && r.time >= from && r.time <= to && (!name || r.name.toLowerCase().includes(name)));
      const col = (q.sort && q.sort.col) || 'time', dir = q.sort && q.sort.dir === 'asc' ? 1 : -1;
      const val = (r) => ({ n: r.n, time: r.time, name: r.name.toLowerCase(), rarity: r.rarity, group: r.group, pity: r.pity, result: r.result ? r.result.text : '' }[col]);
      rows.sort((a, b) => { const x = val(a), y = val(b); return (x < y ? -1 : x > y ? 1 : b.time - a.time) * dir; });
      const pageSize = q.pageSize || 20, pages = Math.max(1, Math.ceil(rows.length / pageSize)), page = Math.min(Math.max(1, q.page || 1), pages);
      return delay({
        empty: false, total: rows.length, page, pages, pageSize, rows: rows.slice((page - 1) * pageSize, page * pageSize).map(toRow),
        hasLowerBound: rows.some((r) => r.lb && r.rarity === 5), hasAssumed: rows.some((r) => r.result && r.result.assumed),
        timeline: rows.filter((r) => r.rarity === 5).sort((a, b) => a.time - b.time).map(toRow).map((r) => ({ n: r.n, time: r.time, name: r.name, pity: r.pity, lowerBound: r.lowerBound, group: r.group, kind: r.result ? r.result.kind : null }))
      });
    },

    // all rows of a filtered history as CSV text; the desktop application saves a file instead and returns { path }
    historyExport(game, q = {}) {
      return sampleApi.history(game, { ...q, page: 1, pageSize: 100000 }).then((h) => {
        const lines = [['number', 'time', 'name', 'rarity', 'banner', 'pity', 'result'].join(',')];
        h.rows.forEach((r) => lines.push([r.n, r.time, `"${r.name.replace(/"/g, '""')}"`, RULES[game].rarity[r.rarity], groupOf(game, r.group).short, r.pity, r.result ? r.result.text : ''].join(',')));
        return { name: `history_${game}_${new Date(now()).toISOString().slice(0, 10)}.csv`, mime: 'text/csv', text: lines.join('\n') };
      });
    },

    stats(game, q = {}) { build(); return delay(hasPulls(game) ? { empty: false, ...computeStats(game, q) } : { empty: true }); },

    getSettings() { build(); return delay(clone(sample.settings)); },
    saveSettings(s) {
      build(); sample.settings = merge(DEFAULT_SETTINGS, s);
      try { localStorage.setItem(SETTINGS_KEY, JSON.stringify(sample.settings)); } catch (e) { /* storage blocked */ }
      return delay(clone(sample.settings));
    },
    saveNickname(game, uid, nickname) { build(); const a = sample.accounts.find((x) => x.game === game && x.uid === uid); if (a) a.nickname = nickname; return delay(true); },
    removeAccount(game, uid) {
      build();
      sample.accounts = sample.accounts.filter((a) => !(a.game === game && a.uid === uid));
      delete sample.pulls[game]; Object.keys(sample.derived).filter((k) => k.startsWith(game + '/')).forEach((k) => delete sample.derived[k]);
      return delay(true);
    },
    storage() { return delay({ imageCacheMB: 0, note: impl.mode === 'backend' ? 'Not available yet.' : 'The browser preview has no image cache.' }); },
    clearImageCache() { return delay({ unavailable: true }); },
    chooseFolder() { return delay({ unavailable: true }); },
    setActiveAccount() { return delay({ unavailable: true }); },
    backupNow() { return delay({ unavailable: true }); },

    firstRun() {   // S1: what the scan will look at (the browser preview cannot see the disk)
      return delay({ games: RULES && Object.keys(RULES).map((game) => ({ game, folder: { state: 'unknown', path: '' }, link: { state: 'unknown', ageHours: null } })), unavailable: true });
    },

    syncStart(game, opts) {
      build();
      sampleApi._cancel = false;
      const fail = qs.get('fail');
      if (!synced && !qs.get('demo')) { window.PT_EVENTS.emit({ type: 'error', game, code: 'no_backend' }); return delay({ started: true }); }
      const ms = (x) => x * speed, E = (ev) => { if (!sampleApi._cancel) window.PT_EVENTS.emit({ game, ...ev }); };
      const g = RULES[game], groups = g.groups.filter((x) => x.key !== 'bangboo');
      const steps = [];
      let t = 0; const at = (d, fn) => { t += ms(d); steps.push(setTimeout(fn, t)); };
      E({ type: 'step', n: 1, state: 'running' });
      at(700, () => E({ type: 'step', n: 1, state: fail === 'cache' ? 'failed' : 'ok', text: 'cache file changed 3 min ago' }));
      if (fail === 'cache') { at(100, () => E({ type: 'error', code: 'cache_missing' })); return delay({ started: true }); }
      at(100, () => E({ type: 'step', n: 2, state: 'running' }));
      at(800, () => E({ type: 'step', n: 2, state: fail === 'expired' ? 'failed' : 'ok', text: fail === 'expired' ? '' : '1 of 4 candidates tested' }));
      if (fail === 'expired') { at(100, () => E({ type: 'error', code: 'link_expired' })); return delay({ started: true }); }
      at(100, () => E({ type: 'step', n: 3, state: 'running' }));
      groups.forEach((x) => {
        for (let p = 1; p <= 3; p++) at(450, () => E({ type: 'fetch', group: x.key, label: x.label, page: p, pages: 3, items: p * 20 }));
        at(150, () => E({ type: 'fetch', group: x.key, label: x.label, page: 3, pages: 3, items: 60, done: true }));
        if (fail === 'rate' && x === groups[0]) at(200, () => E({ type: 'wait', sec: 15, retry: 5, of: 5, stop: true }));
      });
      if (fail === 'rate') { at(100, () => E({ type: 'step', n: 3, state: 'failed' })); at(100, () => E({ type: 'error', code: 'rate_limited' })); return delay({ started: true }); }
      if (fail === 'uid' && !(opts && opts.newAccount)) { at(100, () => E({ type: 'step', n: 3, state: 'ok' })); at(100, () => E({ type: 'error', code: 'uid_mismatch', found: '6xxxx9999', active: DATA.sampleHistory.account })); return delay({ started: true }); }
      at(100, () => E({ type: 'step', n: 3, state: 'ok' }));
      at(100, () => E({ type: 'step', n: 4, state: 'running' }));
      at(600, () => E({ type: 'step', n: 4, state: 'ok' }));
      at(100, () => E({ type: 'done', result: { at: new Date(now()).toISOString(), added: { total: 63, groups: groups.map((x, i) => ({ key: x.key, label: x.short, n: [40, 20, 3][i] || 0 })) }, stored: 579, new5: 0, accountCheck: opts && opts.newAccount ? 'Another account, added as a new one' : 'UID matches the stored account' } }));
      sampleApi._timers = steps;
      return delay({ started: true });
    },
    syncCancel() { sampleApi._cancel = true; (sampleApi._timers || []).forEach(clearTimeout); return delay(true); },

    pickImportFile() { return delay({ browser: true }); },   // the page uses a file input; the desktop application opens its own dialog
    importPreview({ name, text }) {
      build();
      const base = { name, game: null, uid: null, format: 'Unknown', records: 0, newRecords: 0, stored: 0, conflicts: [], mismatch: null, ok: false };
      let data = null; try { data = JSON.parse(text); } catch (e) { /* not JSON */ }
      if (data && data.info && (data.hk4e || data.hkrpg || data.nap)) {
        const key = data.hk4e ? 'hk4e' : data.hkrpg ? 'hkrpg' : 'nap', game = { hk4e: 'genshin', hkrpg: 'hsr', nap: 'zzz' }[key];
        const entry = data[key][0] || {}, list = data[key].flatMap((e) => e.list || []);
        const have = new Map(); if (hasPulls(game)) allRows(game).forEach((r) => have.set(r.id, r.time));
        const conflicts = list.filter((x) => have.has(String(x.id)) && Math.abs(Date.parse(String(x.time).replace(' ', 'T')) - have.get(String(x.id))) > 1000).map((x) => ({ id: String(x.id), reason: 'same item, time differs' }));
        const stored = list.filter((x) => have.has(String(x.id))).length;
        const acct = sample.accounts.find((a) => a.game === game);
        return delay({ ...base, format: `UIGF ${data.info.version || 'v4'}`, game, uid: String(entry.uid || ''), records: list.length, stored: stored - conflicts.length, newRecords: list.length - stored, conflicts,
          mismatch: acct && entry.uid && String(entry.uid) !== acct.uid ? { found: String(entry.uid), active: acct.uid } : null, ok: true });
      }
      if (/\.csv$/i.test(name || '')) return delay({ ...base, format: 'CSV', records: Math.max(0, text.split(/\r?\n/).filter(Boolean).length - 1), newRecords: 0, ok: true, note: 'The CSV layout is matched by the desktop application when it imports.' });
      return delay({ ...base, error: 'The file is not a UIGF v4 JSON export or a CSV file.' });
    },
    importApply() { return delay({ unavailable: true }); },

    exportRun({ game, format = 'uigf', dates = 'all' }) {
      build();
      if (!hasPulls(game)) return delay({ empty: true });
      const since = sinceOf(dates), rows = allRows(game).filter((r) => r.time >= since).sort((a, b) => a.time - b.time);
      const stamp = new Date(now()).toISOString().slice(0, 10).replace(/-/g, '');
      if (format === 'csv') {
        return sampleApi.history(game, { rarity: '45', include3: true, from: '', pageSize: 100000, sort: { col: 'time', dir: 'asc' } }).then((h) => ({ name: `export_${game}_${stamp}.csv`, mime: 'text/csv', count: h.total,
          text: ['id,time,name,rarity,banner'].concat(rows.map((r) => `${r.id},${new Date(r.time).toISOString()},"${r.name.replace(/"/g, '""')}",${r.rarity},${groupOf(game, r.group).short}`)).join('\n') }));
      }
      const key = { genshin: 'hk4e', hsr: 'hkrpg', zzz: 'nap' }[game], acct = sample.accounts.find((a) => a.game === game);
      const doc = { info: { export_timestamp: Math.floor(now() / 1000), export_app: 'Pulls Tracker (sample)', version: 'v4.0' },
        [key]: [{ uid: acct ? acct.uid : '', timezone: 8, lang: 'en-us', list: rows.map((r) => ({ id: r.id, gacha_type: r.group, name: r.name, rank_type: String(r.rarity), time: new Date(r.time).toISOString().replace('T', ' ').slice(0, 19) })) }] };
      return delay({ name: `uigf_${game}_${stamp}.json`, mime: 'application/json', count: rows.length, text: JSON.stringify(doc, null, 2) });
    },

    sources() {
      const B = window.PT_BANNERS, when = B && B.generatedAt ? Date.parse(B.generatedAt) : NaN;
      return delay({
        server: B && B.source ? B.source : '',
        banners: B ? { at: Number.isFinite(when) ? new Date(when).toISOString() : null, source: B.source || null, games: B.games || {} } : null,
        images: [
          ['act-webstatic.hoyoverse.com', 'Item icons from the calendar, Zenless Zone Zero portraits'],
          ['static.nanoka.cc', 'Character art, descriptions, Star Rail light cone art'],
          ['enka.network', 'Second host for character and weapon art'],
          ['gi.yatta.moe', 'Third host for Genshin weapon art'],
          ['cdn.prydwen.gg', 'Zenless Zone Zero character art (a fan site CDN), tried first'],
          ['raw.githubusercontent.com (genshin-db)', 'Genshin character titles']
        ]
      });
    },
    sourceTest() { return delay({ unavailable: true }); },
    saveCookie() { return delay({ unavailable: true }); },
    uiReady() { return delay({ ok: true }); },
    windowMinimize() { return delay({ unavailable: true }); },
    windowClose() { return delay({ unavailable: true }); },
    windowGetOnTop() { return delay({ unavailable: true }); },
    windowSetOnTop() { return delay({ unavailable: true }); },
    windowStartResize() { return delay({ unavailable: true }); },
    windowGetRect() { return delay({ unavailable: true }); },
    windowSetRect() { return delay({ unavailable: true }); },
    perfReport() { return delay({ ok: true }); }
  };

  /* ---- Backend provider (pywebview) --------------------------------------------------------------- */
  /* A Python method that raised answers its fallback with "failed": true and the error text (pt/api.py, safe). It is raised here, so a screen
     shows the error instead of drawing the fallback ("no pulls yet", "works in the desktop application") as if it were the real answer.
     It is also announced (pt:failed, js/app.js shows a message): a handler that does not catch it would otherwise fail without a word,
     and a page opened from a file never gets the unhandledrejection event for a rejection made in another script file. */
  const raised = (r) => {
    if (r && r.failed === true) {
      const err = new Error(r.error || 'The application could not do that.');
      window.dispatchEvent(new CustomEvent('pt:failed', { detail: err }));
      throw err;
    }
    return r;
  };
  const METHODS = Object.keys(sampleApi).filter((k) => typeof sampleApi[k] === 'function' && !k.startsWith('_'));
  let impl = sampleApi;
  const wantBackend = qs.get('backend') === '1';
  const ready = new Promise((resolve) => {
    if (!wantBackend) { resolve(); return; }
    const attach = () => {
      const api = window.pywebview && window.pywebview.api;
      if (!api) return false;
      impl = { mode: 'backend' };
      /* A method the Python side does not have yet: the read methods answer like an account with no pulls (the screens show their empty
         states), the others answer { unavailable: true } and the screen says the function is not available yet. */
      const READ = ['state', 'getSettings', 'dashboard', 'windowStats', 'windowPulls', 'pastBanners', 'history', 'stats', 'storage', 'firstRun', 'sources'];
      METHODS.forEach((m) => {
        impl[m] = (...a) => (typeof api[m] === 'function' ? Promise.resolve(api[m](...a)).then(raised)
          : READ.includes(m) ? sampleApi[m](...a) : Promise.resolve({ unavailable: true }));
      });
      resolve(); return true;
    };
    if (!attach()) window.addEventListener('pywebviewready', attach, { once: true });
  });

  const facade = { ready: () => ready, get mode() { return impl.mode; }, synced };
  METHODS.forEach((m) => { facade[m] = (...a) => ready.then(() => impl[m](...a)); });
  window.PT_API = facade;
})();
