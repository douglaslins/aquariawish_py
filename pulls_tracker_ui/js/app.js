/*
  Pulls Tracker, start screen logic.
  Front end only: no network calls except loading images.
  Banners come from js/banners.js (window.PT_BANNERS, written by banner_tracker_test.py); the rest from js/data.js (window.PT_DATA).
  Calculation follows the IA (Main screen, section S0) and research v2 section 9.2:
    worst case pulls = copies * 2 * hardPity - currentPity - (guarantee ? hardPity : 0)
    owned pulls      = floor(currency / 160) + tickets
    needed           = max(0, worst case - owned pulls)
  Character + weapon goal (both dropdowns on a value): the worst cases of the two banners are added, each with its own pity counter
  and guarantee (the character banner's and the weapon banner's); the owned pulls are counted once.
*/
(() => {
  'use strict';

  const DATA = window.PT_DATA;
  const BANNERS = (window.PT_BANNERS && window.PT_BANNERS.slides) || {};
  const qs = window.PT_PARAMS;
  const reduceMotion = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
  const clockOffset = qs.get('now') ? Date.parse(qs.get('now')) - Date.now() : 0;
  const now = () => Date.now() + clockOffset;
  const $ = (id) => document.getElementById(id);
  const fmt = (n) => Number(n).toLocaleString('en-US');
  const PULL_COST = 160;
  const synced = qs.get('state') === 'synced';
  /* Star Rail character banners: the title on the light panel. The banner data has no name for these banners yet (bannerTitle is null), so the text
     written in index.html inside <h2 id="hbTitle"> is read once here and used instead. Edit that text in index.html. When the data gets a
     bannerTitle, the data wins and this text is not used. If the text in index.html is empty, the title stays hidden. */
  const HSR_TITLE_FALLBACK = (($('hbTitle') && $('hbTitle').textContent) || '').trim();

  /* ---- Per-game configuration ------------------------------------------------------------------ */
  const el = (name, icon, h, c, bg) => ({ name, icon, h, c, bg });
  const GAMES = {
    genshin: {
      title: 'Genshin Impact',
      tier: { prefix: 'C', phrase: 'Select your desired constellation tier', aria: 'Constellation tier' },
      rank: { prefix: 'R', phrase: 'Select your desired weapon rank', aria: 'Weapon rank' },
      currencyName: 'Primogems',
      owned: [
        { key: 'currency', name: 'Primogems', icon: 'assets/genshin/Item_Primogem.webp', kind: 'currency' },
        { key: 'paid', name: 'Genesis Crystals', icon: 'assets/genshin/Item_Genesis_Crystal.webp', kind: 'currency' },
        { key: 'tickets', name: 'Intertwined Fates', icon: 'assets/genshin/Item_Intertwined_Fate.webp', kind: 'tickets' }
      ],
      stars: { src: 'assets/genshin/Icon_5_Stars.webp', alt: '5 stars' },
      artClass: 'art-wide',
      guaranteeWords: { yes: '50/50 lost, next 5-star is the featured one', no: 'no guarantee' },
      sharedNote: { character: 'Wish 1 and Wish 2 share this counter.' },
      elements: (() => {
        const f = (n, h, c) => el(n, `assets/genshin/Element_${n}.webp`, h, c, `assets/genshin/Character_Event_Wish_${n}.webp`);
        return { anemo: f('Anemo', 164, 0.12), cryo: f('Cryo', 199, 0.09), dendro: f('Dendro', 126, 0.14), electro: f('Electro', 310, 0.14),
                 geo: f('Geo', 89, 0.14), hydro: f('Hydro', 213, 0.13), pyro: f('Pyro', 52, 0.14) };
      })()
    },
    hsr: {
      title: 'Honkai: Star Rail',
      tier: { prefix: 'E', phrase: 'Select your desired eidolon tier', aria: 'Eidolon tier' },
      rank: { prefix: 'S', phrase: 'Select your desired weapon superimposition', aria: 'Light cone superimposition' },
      currencyName: 'Stellar Jade',
      owned: [
        { key: 'currency', name: 'Stellar Jade', icon: 'assets/hsr/star_jade.png', kind: 'currency' },
        { key: 'paid', name: 'Oneiric Shards', icon: 'assets/hsr/oneiric_shard.png', kind: 'currency' },
        { key: 'tickets', name: 'Star Rail Special Passes', icon: 'assets/hsr/Item_Star_Rail_Special_Pass.webp', kind: 'tickets' }
      ],
      stars: { src: 'assets/hsr/Icon_5_Stars.png', alt: '5 stars' },
      artClass: 'art-square',
      guaranteeWords: { yes: '50/50 lost, next 5-star is the featured one', no: 'no guarantee' },
      sharedNote: {},
      elements: (() => {
        const f = (n, file, h, c) => el(n, `assets/hsr/Type_${file}.png`, h, c);
        return { fire: f('Fire', 'Fire', 28, 0.15), ice: f('Ice', 'Ice', 238, 0.12), imaginary: f('Imaginary', 'Imaginary', 102, 0.15),
                 lightning: f('Lightning', 'Lightning', 318, 0.15), physical: f('Physical', 'Physical', 145, 0.02),
                 quantum: f('Quantum', 'Quantum', 282, 0.14), wind: f('Wind', 'Wind', 159, 0.13) };
      })()
    },
    zzz: {
      title: 'Zenless Zone Zero',
      tier: { prefix: 'M', phrase: 'Select your desired mindscape cinema tier', aria: 'Mindscape cinema tier' },
      rank: { prefix: 'P', phrase: 'Select your desired weapon phase', aria: 'W-Engine phase' },
      currencyName: 'Polychrome',
      owned: [
        { key: 'currency', name: 'Polychrome', icon: 'assets/zzz/Item_Polychrome.webp', kind: 'currency' },
        { key: 'tickets', name: 'Encrypted Master Tapes', icon: 'assets/zzz/Item_Encrypted_Master_Tape.webp', kind: 'tickets' }
      ],
      stars: { src: 'assets/zzz/Icon_AgentRank_S.webp', alt: 'S-Rank' },
      artClass: 'art-bust',
      guaranteeWords: { yes: '50/50 lost, next S-Rank is the featured one', no: 'no guarantee' },
      sharedNote: {},
      elements: (() => {
        const f = (n, file, h, c) => el(n, `assets/zzz/Icon_${file}.webp`, h, c);
        return { auric_ink: f('Auric Ink', 'Auric_Ink', 77, 0.12), electric: f('Electric', 'Electric', 241, 0.15), ether: f('Ether', 'Ether', 320, 0.15),
                 fire: f('Fire', 'Fire', 36, 0.15), frost: f('Frost', 'Frost', 206, 0.13), honed_edge: f('Honed Edge', 'Honed_Edge', 269, 0.12),
                 ice: f('Ice', 'Ice', 194, 0.13), lumiflux: f('Lumiflux', 'Lumiflux', 340, 0.11), physical: f('Physical', 'Physical', 85, 0.15),
                 wind: f('Wind', 'Wind', 250, 0.10) };
      })()
    }
  };
  GAMES.genshin.comboTitle = 'Event Wish'; GAMES.hsr.comboTitle = 'Event Warp'; GAMES.zzz.comboTitle = 'Signal Search';
  GAMES.genshin.sharedNote.characterCombined = 'Wish 1 and Wish 2 share the character counter.';
  const elementOf = (game, name) => name ? GAMES[game].elements[String(name).trim().toLowerCase().replace(/\s+/g, '_')] || null : null;

  /* ---- State and storage ----------------------------------------------------------------------- */
  const state = { game: 'genshin', index: 0, slides: [], slide: null, token: 0, store: {},
                  chars: [], weapons: [], ci: 0, wi: 0, combo: false };   // combo: character + weapon display; ci / wi: the banner shown in each half
  const API = window.PT_API;
  /* Stored pulls (Python application only): the dashboard answer and the UID of each game's account. The sample provider keeps using
     the values of js/data.js. Before the answer arrives (or with no account) a game counts as having no history. */
  const hist = { backend: false, dash: {}, uid: {}, loaded: {} };
  const acctOf = (game) => (hist.backend ? hist.uid[game] || 'none' : synced ? DATA.sampleHistory.account : 'none');

  const storeKey = (game) => `pt.v1.${acctOf(game)}.${game}`;
  function loadStore(game) {
    try { return JSON.parse(localStorage.getItem(storeKey(game))) || {}; } catch (e) { return {}; }
  }
  function saveStore() {
    try { localStorage.setItem(storeKey(state.game), JSON.stringify(state.store)); } catch (e) { /* storage blocked: values stay in memory */ }
  }
  function lastGame() {
    const q = qs.get('game'); if (q && GAMES[q]) return q;
    try { const g = localStorage.getItem('pt.v1.lastGame'); if (g && GAMES[g]) return g; } catch (e) { /* ignore */ }
    return 'genshin';
  }

  /* ---- Slides ----------------------------------------------------------------------------------- */
  function buildSlides(game) {
    const t = now();
    const live = (BANNERS[game] || []).filter((s) => !s.end || Date.parse(s.end) > t);
    const current = live.filter((s) => Date.parse(s.start) <= t);
    const upcoming = live.filter((s) => Date.parse(s.start) > t).sort((a, b) => Date.parse(a.start) - Date.parse(b.start));
    const list = [...current, ...upcoming];
    if (qs.get('preview') === 'newest') list.push(...((DATA.previewNewest && DATA.previewNewest[game]) || []));
    return list;
  }

  function setSlides(list) {
    state.slides = list;
    state.chars = list.filter((s) => s.kind === 'character');
    state.weapons = list.filter((s) => s.kind === 'weapon');
    state.ci = Math.min(state.ci, Math.max(0, state.chars.length - 1));
    state.wi = Math.min(state.wi, Math.max(0, state.weapons.length - 1));
  }

  /* ---- Calculation (pure) ------------------------------------------------------------------------ */
  function calc({ copies, hard, pity, guarantee, currency, tickets }) {
    const full = copies * 2 * hard;
    const cover = Math.min(full, Math.max(0, pity) + (guarantee ? hard : 0));
    const total = full - cover;
    const owned = Math.floor(currency / PULL_COST) + tickets;
    const short = Math.max(0, total - owned);
    return { full, cover, total, owned, short, ownedUsed: Math.min(owned, total) };
  }
  window.PT_calc = calc;

  function pityFor(game, group) {
    if (hist.backend) {
      const d = hist.dash[game], g = d && !d.empty && d.groups.find((x) => x.key === group);
      if (!g) return { pity: 0, guarantee: false, known: false };
      // guarantee null: the stored pulls do not show whether the last 50/50 was lost (the history starts after it, or the banner data is missing)
      return { pity: g.pity, guarantee: g.guarantee === true, known: true, lowerBound: !!g.lowerBound, guaranteeUnknown: g.guarantee === null && g.pulls > 0 };
    }
    if (!synced) return { pity: 0, guarantee: false, known: false };
    const h = DATA.sampleHistory.pity[game] && DATA.sampleHistory.pity[game][group];
    return h ? { ...h, known: true } : { pity: 0, guarantee: false, known: false };
  }

  // Read the stored pity of a game from the Python side, then show it (the account may differ from the one before: another storage key).
  function loadHist(game) {
    if (!hist.backend) return Promise.resolve();
    hist.loaded[game] = true;
    return Promise.all([API.state(), API.dashboard(game)]).then(([st, dash]) => {
      const before = storeKey(game), a = ((st && st.accounts) || []).find((x) => x.game === game);
      hist.uid[game] = a ? a.uid : null; hist.dash[game] = dash;
      if (state.game !== game) return;
      if (storeKey(game) !== before) setGame(game, { silent: true, keepIndex: true });
      else if (state.slide) update();
    }).catch(() => { /* the screen keeps "no history" */ });
  }

  /* ---- Rendering --------------------------------------------------------------------------------- */
  const selC = $('selC'), selW = $('selW');

  function fillSelect(sel, prefix, from, to, isRank) {
    sel.innerHTML = '';
    const none = document.createElement('option'); none.value = 'none'; none.textContent = 'None'; sel.appendChild(none);
    for (let i = from; i <= to; i++) {
      const copies = isRank ? i : i + 1;
      const o = document.createElement('option');
      o.value = String(i);
      o.textContent = `${prefix}${i} (${copies} ${copies === 1 ? 'copy' : 'copies'})`;
      sel.appendChild(o);
    }
  }

  function setGame(game, opts = {}) {
    state.game = game;
    document.body.dataset.game = game;
    document.title = `Pulls Tracker, ${GAMES[game].title}`;
    document.querySelectorAll('.tab').forEach((t) => {
      const on = t.dataset.game === game;
      t.setAttribute('aria-selected', String(on));
      t.tabIndex = on ? 0 : -1;
    });
    try { localStorage.setItem('pt.v1.lastGame', game); } catch (e) { /* ignore */ }
    state.store = loadStore(game);
    const g = GAMES[game];
    fillSelect(selC, g.tier.prefix, 0, 6, false);
    fillSelect(selW, g.rank.prefix, 1, 5, true);
    selC.setAttribute('aria-label', g.tier.aria);
    selW.setAttribute('aria-label', g.rank.aria);
    $('phraseC').textContent = g.tier.phrase; $('phraseW').textContent = g.rank.phrase;
    buildOwnedFields();
    state.combo = false; state.ci = state.wi = 0;
    setSlides(buildSlides(game));
    state.index = opts.keepIndex ? Math.min(state.index, Math.max(0, state.slides.length - 1)) : 0;
    renderSlide(!opts.silent);
    document.dispatchEvent(new CustomEvent('pt:game', { detail: { game } }));
    if (hist.backend && !hist.loaded[game]) loadHist(game);
  }

  function buildOwnedFields() {
    const box = $('ownedFields');
    box.innerHTML = '';
    GAMES[state.game].owned.forEach((f) => {
      const wrap = document.createElement('label');
      wrap.className = f.icon ? 'own has-icon' : 'own';
      if (f.icon) { const im = document.createElement('img'); im.className = 'own-icon'; im.src = f.icon; im.alt = ''; wrap.appendChild(im); }
      const nm = document.createElement('span'); nm.className = 'own-name'; nm.textContent = f.name; wrap.appendChild(nm);
      const input = document.createElement('input');
      input.type = 'text'; input.inputMode = 'numeric'; input.autocomplete = 'off'; input.placeholder = '0';
      input.dataset.key = f.key; input.setAttribute('aria-label', `${f.name} owned`);
      const v = state.store.owned && state.store.owned[f.key];
      input.value = v ? fmt(v) : '';
      input.addEventListener('focus', () => { input.value = (input.value || '').replace(/,/g, ''); input.select(); });
      input.addEventListener('input', () => {
        input.value = input.value.replace(/\D/g, '').slice(0, 9);
        state.store.owned = { ...(state.store.owned || {}), [f.key]: input.value };
        saveStore(); update();
      });
      input.addEventListener('blur', () => { input.value = input.value ? fmt(input.value) : ''; });
      wrap.appendChild(input);
      box.appendChild(wrap);
    });
  }

  // Tries each URL in order on one <img>; calls fail() when none loads. A stale call (token changed) stops quietly.
  function loadChain(img, urls, token, fail, alt) {
    const list = urls.filter(Boolean);
    let i = 0;
    const next = () => {
      if (token !== state.token) return;
      if (i >= list.length) { img.onerror = null; if (fail) fail(); return; }
      img.src = list[i++];
    };
    img.referrerPolicy = 'no-referrer';
    img.alt = '';                                   // no alt text flashes while a URL is tried
    img.onload = () => { if (token === state.token) img.alt = alt || ''; };
    img.onerror = next;
    next();
  }

  // Banner change: the old layer slides out while the new one slides in (260 ms, fast start, soft stop); the name plate fades in from the same side.
  const SLIDE_MS = 260, SLIDE_EASE = 'cubic-bezier(0.22, 0.8, 0.28, 1)';
  function slideStage(dir) {
    const stage = $('stage'), layer = $('stageLayer'), plate = $('plate');
    stage.querySelectorAll('.stage-ghost').forEach((g) => g.remove());
    layer.getAnimations().forEach((a) => a.cancel());
    plate.getAnimations().forEach((a) => a.cancel());
    const ghost = layer.cloneNode(true);                       // a copy of the old banner art, removed when it has left
    ghost.removeAttribute('id'); ghost.querySelectorAll('[id]').forEach((e) => e.removeAttribute('id'));
    ghost.classList.add('stage-ghost'); ghost.setAttribute('aria-hidden', 'true');
    stage.insertBefore(ghost, layer);
    const timing = { duration: SLIDE_MS, easing: SLIDE_EASE };
    ghost.animate([{ transform: 'translateX(0)' }, { transform: `translateX(${-dir * 100}%)` }], timing).onfinish = () => ghost.remove();
    layer.animate([{ transform: `translateX(${dir * 100}%)` }, { transform: 'translateX(0)' }], timing);
    plate.animate([{ opacity: 0, transform: `translateX(${dir * 32}px)` }, { opacity: 1, transform: 'translateX(0)' }],
      { duration: 220, delay: 40, easing: SLIDE_EASE, fill: 'backwards' });
  }

  function renderSlide(animate, dir = 0) {
    const empty = state.slides.length === 0;
    $('banner').hidden = empty; $('info').hidden = empty; $('empty').hidden = !empty;
    if (empty) { state.slide = null; document.body.style.removeProperty('--ah'); document.body.style.removeProperty('--ac'); return; }      // null: the 30 s timers below have nothing to redraw

    state.combo = false; $('combo').hidden = true; $('banner').removeAttribute('data-mode');
    if (animate && dir && !reduceMotion && state.slide) slideStage(dir);
    const s = state.slide = state.slides[state.index];
    if (s.kind === 'character') state.ci = Math.max(0, state.chars.indexOf(s)); else state.wi = Math.max(0, state.weapons.indexOf(s));
    const g = GAMES[state.game];
    const element = s.kind === 'character' ? elementOf(state.game, s.element) : null;
    const token = ++state.token;
    // Star Rail character banners have their own layout (base image, element gradient, text on the panel)
    const hsrChar = state.game === 'hsr' && s.kind === 'character';
    const hsrLc = state.game === 'hsr' && s.kind === 'weapon';   // Star Rail light cone banners: light base, tilted glass card
    const zzz = state.game === 'zzz';                            // Zenless Zone Zero: base image, element effect, in-game style name plate
    const layout = hsrChar ? 'hsr-char' : hsrLc ? 'hsr-lc' : zzz ? 'zzz' : '';
    ['stageLayer', 'banner'].forEach((id) => { if (layout) $(id).dataset.layout = layout; else delete $(id).dataset.layout; });

    // accent follows the element; weapon slides use the game default
    if (element) { document.body.style.setProperty('--ah', element.h); document.body.style.setProperty('--ac', element.c); }
    else { document.body.style.removeProperty('--ah'); document.body.style.removeProperty('--ac'); }

    // stage
    const stage = $('stage'), bg = $('stageBg'), art = $('stageArt'), chip = $('bannerChip');
    let bgSrc = null;
    if (state.game === 'genshin') bgSrc = (s.kind === 'character' && element) ? element.bg : s.kind === 'weapon' ? 'assets/genshin/Epitome_Invocation.webp' : null;
    const baked = state.game === 'genshin' && !!bgSrc; // supplied backgrounds carry their own label
    chip.textContent = s.banner; chip.hidden = baked || hsrChar || hsrLc;   // the Star Rail base images carry their own tab
    bg.onerror = () => { if (token === state.token) { bg.hidden = true; chip.hidden = false; } };
    bg.onload = () => { if (token === state.token && baked) chip.hidden = true; };
    bg.hidden = !bgSrc; bg.removeAttribute('src'); if (bgSrc) bg.src = bgSrc;

    // images: the saved file first, then the URL it came from, then (for art) the square icon
    const showIcon = () => {
      if (token !== state.token) return;
      art.className = 'stage-art art-icon'; $('banner').dataset.art = 'art-icon'; 
      loadChain(art, [s.icon, s.iconRemote], token, null, `${s.name}, ${s.kind === 'character' ? 'character icon' : 'item icon'}`);
    };
    if (s.art || s.artRemote) {   // characters: the character art; weapons: the full-size weapon art
      const cls = s.kind === 'character' ? g.artClass : 'art-weapon';
      art.className = `stage-art ${cls}`; $('banner').dataset.art = cls;
      loadChain(art, [s.art, s.artRemote], token, showIcon, `${s.name}, ${s.kind === 'character' ? 'character art' : 'weapon art'}`);
    } else showIcon();

    if (animate && !dir && !reduceMotion) { stage.classList.remove('is-changing'); void stage.offsetWidth; stage.classList.add('is-changing'); }   // no direction (first load, game tab): fade

    // identity
    $('itemNameText').textContent = s.name;
    const ei = $('elIcon');
    if (element) { ei.src = element.icon; ei.alt = element.name; ei.hidden = false; } else { ei.hidden = true; ei.removeAttribute('src'); }
    $('plate').classList.toggle('no-el', !element);
    const st = $('stars'); st.src = g.stars.src; st.alt = g.stars.alt;
    // plate text: the character title where the game has one (Genshin: "Void Star"), else the description
    const plateText = s.title || s.desc || '';
    $('desc').textContent = plateText;
    $('descWrap').hidden = hsrChar || zzz || !plateText;
    // Star Rail: element gradient, banner name (when known) and the full description on the panel; a card under the banner when it is narrow
    const hb = $('hsrBase'); hb.hidden = !hsrChar; $('hsrFlow').hidden = !hsrChar;
    $('hsrLcBase').hidden = !hsrLc; $('zzzBase').hidden = !zzz;
    { const bg = $('hlBgArt'); bg.hidden = true; bg.removeAttribute('src'); }   // the enlarged copy of the art is filled in when the art has loaded
    if (hsrLc) {
      const bt = s.bannerTitle || ''; $('hlTitle').textContent = bt; $('hlTitle').hidden = !bt;
      // the small cards at the bottom left: the icons of the rate-up light cones (saved file first, then the URL); a banner without any keeps the empty cards
      const ups = (s.rateUp || []).filter((r) => r && (r.icon || r.iconRemote)).slice(0, 3);
      $('hlStack').querySelectorAll('.hl-mini').forEach((card) => {
        const img = card.querySelector('img'), r = ups[Number(card.dataset.n)];
        img.hidden = true; img.removeAttribute('src'); img.onload = img.onerror = null;
        card.hidden = !!ups.length && !r;
        if (!r) return;
        loadChain(img, [r.icon, r.iconRemote], token, null, r.name);
        img.addEventListener('load', () => { if (token === state.token) img.hidden = false; }, { once: true });
      });
    }
    if (hsrChar) {
      hb.dataset.element = element ? element.name : 'Physical';
      const bt = s.bannerTitle || HSR_TITLE_FALLBACK;
      ['hbTitle', 'hfTitle'].forEach((id) => { $(id).textContent = bt; $(id).hidden = !bt; });
      $('hbDesc').textContent = s.desc || ''; $('hfDesc').textContent = s.desc || '';
    }
    $('descWrap').classList.remove('is-open');
    $('descMore').setAttribute('aria-expanded', 'false'); $('descMore').textContent = 'Read more';
    requestAnimationFrame(() => { fitPlate(); checkClamp(); fitHsrText(); });

    // goal controls: a character banner opens with the weapon dropdown on None, a weapon banner with the character dropdown on None
    syncSelects();
    renderPager(); renderTime(); update();
  }

  // Dropdown values follow the display: single banner = its own dropdown on a value and the other on None; combined = both on a value.
  // A dropdown on None keeps the other off None (both can never be None); a dropdown whose kind has no banner right now stays on None.
  function syncSelects() {
    const s = state.slide, isChar = s.kind === 'character';
    const tier = String(state.store.tier ?? 0), rank = String(state.store.rank ?? 1);
    if (state.combo) { selC.value = tier; selW.value = rank; }
    else if (isChar) { selC.value = tier; selW.value = 'none'; }
    else { selC.value = 'none'; selW.value = rank; }
    const cNone = selC.value === 'none', wNone = selW.value === 'none';
    selC.querySelector('option[value="none"]').disabled = wNone;
    selW.querySelector('option[value="none"]').disabled = cNone;
    selC.disabled = cNone && state.chars.length === 0;
    selW.disabled = wNone && state.weapons.length === 0;
  }

  function onGoalChange() {
    const cv = selC.value, wv = selW.value;
    if (cv !== 'none') state.store.tier = Number(cv);
    if (wv !== 'none') state.store.rank = Number(wv);
    saveStore();
    const both = cv !== 'none' && wv !== 'none';
    if (both && !state.combo) { renderCombo(true); }                                   // the other dropdown left None: character + weapon display
    else if (!both && state.combo) {                                                   // a dropdown went to None: back to the single banner display
      const keep = wv === 'none' ? state.chars[state.ci] : state.weapons[state.wi];     // weapon rank None = the character, character None = the weapon
      state.index = Math.max(0, state.slides.indexOf(keep));
      renderSlide(true);
    } else update();
  }

  function renderPager() {
    const p = $('pager'), s = state.slide, t = now();
    if (!s) return;                                  // this game has no banner in the data (the Main screen shows its empty state)
    p.textContent = `${state.index + 1} / ${state.slides.length}`;
    let tag = '';
    if (s.preview) tag = 'PREVIEW'; else if (s.start && Date.parse(s.start) > t) tag = 'UPCOMING';
    if (tag) { const sp = document.createElement('span'); sp.className = 'tag'; sp.textContent = tag; p.appendChild(sp); }
    $('nextBtn').hidden = $('prevBtn').hidden = state.slides.length < 2;
    p.hidden = state.slides.length < 2 && !tag;
  }

  function dur(ms) {
    const m = Math.floor(ms / 60000), d = Math.floor(m / 1440), h = Math.floor((m % 1440) / 60), mm = m % 60;
    if (d > 0) return `${d}d ${h}h`;
    if (h > 0) return `${h}h ${mm}m`;
    return `${Math.max(1, mm)}m`;
  }
  const stamp = (iso) => new Intl.DateTimeFormat('sv-SE', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(iso));

  // Combined display: the earlier end and the later start of the two banners (the pair holds only while both run).
  function timeSlide() {
    if (!state.combo) return state.slide;
    const a = state.chars[state.ci], b = state.weapons[state.wi];
    const starts = [a, b].map((x) => (x.start ? Date.parse(x.start) : 0));
    const ends = [a, b].filter((x) => x.end).map((x) => Date.parse(x.end));
    const start = Math.max(...starts), end = ends.length ? Math.min(...ends) : 0;
    return { id: a.id, preview: !!(a.preview || b.preview), start: start ? new Date(start).toISOString() : null, end: end ? new Date(end).toISOString() : null };
  }

  function renderTime() {
    const s = timeSlide(); if (!s) return;
    const t = now(), L = $('timeLabel'), V = $('timeValue'), chip = $('timeChip');
    chip.removeAttribute('title');
    if (s.preview) { L.textContent = 'Preview'; V.textContent = 'not on a banner'; return; }
    if (s.start && Date.parse(s.start) > t) { L.textContent = 'Starts in'; V.textContent = dur(Date.parse(s.start) - t); chip.title = `Starts ${stamp(s.start)} (your time)`; return; }
    if (!s.end) { L.textContent = 'Time left'; V.textContent = 'End not announced'; return; }
    const left = Date.parse(s.end) - t;
    if (left <= 0) { const keep = s.id; setSlides(buildSlides(state.game)); const i = state.slides.findIndex((x) => x.id === keep); state.index = i >= 0 ? i : 0; renderSlide(true); return; }
    L.textContent = 'Time left'; V.textContent = dur(left); chip.title = `Ends ${stamp(s.end)} (your time)`;
  }

  // The goal(s) of the display: one part for a single banner, two (character + weapon) for the combined display.
  function goalParts() {
    if (state.combo) {
      return [{ kind: 'character', slide: state.chars[state.ci], hard: 90, copies: Number(selC.value) + 1 },
              { kind: 'weapon', slide: state.weapons[state.wi], hard: 80, copies: Number(selW.value) }];
    }
    const s = state.slide, isChar = s.kind === 'character';
    return [{ kind: s.kind, slide: s, hard: isChar ? 90 : 80, copies: isChar ? Number(selC.value || 0) + 1 : Number(selW.value || 1) }];
  }

  // Worst cases of the parts added up, each with the pity and guarantee of its own banner type; owned pulls counted once.
  function calcGoal(parts, currency, tickets) {
    let full = 0, cover = 0;
    parts.forEach((p) => {
      const h = pityFor(state.game, p.slide.group);
      p.hist = h;
      p.r = calc({ copies: p.copies, hard: p.hard, pity: Math.min(h.pity, p.hard - 1), guarantee: h.guarantee, currency: 0, tickets: 0 });
      full += p.r.full; cover += p.r.cover;
    });
    const total = full - cover, owned = Math.floor(currency / PULL_COST) + tickets;
    return { full, cover, total, owned, short: Math.max(0, total - owned), ownedUsed: Math.min(owned, total) };
  }
  window.PT_calcGoal = (parts, currency, tickets) => calcGoal(parts, currency, tickets);

  function update() {
    const s = state.slide; if (!s) return;
    const g = GAMES[state.game];
    if (selC.value !== 'none') state.store.tier = Number(selC.value);
    if (selW.value !== 'none') state.store.rank = Number(selW.value);
    saveStore();

    const parts = goalParts();
    const sum = { currency: 0, tickets: 0 };
    document.querySelectorAll('#ownedFields input').forEach((inp) => {
      const f = g.owned.find((x) => x.key === inp.dataset.key); const n = Number((inp.value || '').replace(/,/g, '')) || 0;
      sum[f.kind] += n;
    });
    const r = calcGoal(parts, sum.currency, sum.tickets);

    const fromCurrency = Math.floor(sum.currency / PULL_COST);
    $('ownedSum').innerHTML = '';
    const strong = document.createElement('strong'); strong.textContent = `${fmt(r.owned)} ${r.owned === 1 ? 'pull' : 'pulls'}`;
    $('ownedSum').append('= ', strong, ` (${fmt(fromCurrency)} from currency + ${fmt(sum.tickets)} tickets)`);

    const needed = $('needed');
    if (r.short === 0) { needed.textContent = 'Guaranteed!'; needed.classList.add('is-ok'); }
    else {
      needed.textContent = '';
      const cur = document.createElement('span'); cur.className = 'needed-cur'; cur.textContent = `(${fmt(r.short * PULL_COST)} ${g.currencyName})`;
      needed.append(`Needed: ${fmt(r.short)} ${r.short === 1 ? 'pull' : 'pulls'} `, cur); needed.classList.remove('is-ok');
    }
    $('totalNum').textContent = fmt(r.total);

    // ruler: a gap after every hard-pity block (blocks of 90 for the character part, of 80 for the weapon part)
    const ruler = $('ruler');
    ruler.style.setProperty('--cover', `${(r.cover / r.full) * 100}%`);
    ruler.style.setProperty('--own', `${(r.ownedUsed / r.full) * 100}%`);
    ruler.querySelectorAll('.ruler-tick').forEach((e) => e.remove());
    let at = 0;
    parts.forEach((p) => { for (let i = 0; i < p.copies * 2; i++) { at += p.hard; const tk = document.createElement('i'); tk.className = 'ruler-tick'; tk.style.left = `${(at / r.full) * 100}%`; ruler.appendChild(tk); } });
    ruler.setAttribute('aria-label', `Worst case ${fmt(r.full)} pulls in total: ${fmt(r.cover)} already covered by pity and guarantee, ${fmt(r.ownedUsed)} covered by owned pulls, ${fmt(r.short)} still needed.`);

    renderHint(parts);
  }

  function renderHint(parts) {
    const g = GAMES[state.game], box = $('hintText'); box.textContent = '';
    if (!parts.every((p) => p.hist.known)) {
      box.append('No history synced yet. Calculating from pity 0 with no guarantee. ');
      const b = document.createElement('button'); b.type = 'button'; b.textContent = 'Open Sync';
      b.addEventListener('click', () => document.dispatchEvent(new CustomEvent('pt:open', { detail: { modal: 'sync' } }))); box.append(b, '.');
      return;
    }
    const two = parts.length > 1;
    const text = parts.map((p) => {
      const gw = p.hist.guarantee ? g.guaranteeWords.yes : p.hist.guaranteeUnknown ? 'guarantee unknown, counted as none' : g.guaranteeWords.no;
      return `${two ? (p.kind === 'character' ? 'Character pity' : 'Weapon pity') : 'Pity'} ${p.hist.lowerBound ? 'at least ' : ''}${p.hist.pity} / ${p.hard}, ${gw}`;
    }).join('. ');
    const hasChar = parts.some((p) => p.kind === 'character');
    const note = hasChar && g.sharedNote.character ? ` ${two ? g.sharedNote.characterCombined : g.sharedNote.character}` : '';
    const lower = parts.some((p) => p.hist.lowerBound) ? ' The stored history has no 5-star of that banner type, so the real pity may be higher.' : '';
    box.textContent = `${text} (${hist.backend ? 'from your stored pulls' : 'from history, sample values'}).${note}${lower}`;
  }

  /* ---- Character + weapon display ------------------------------------------------------------------ */
  const RANK_MARKUP = {
    genshin: () => `<img src="${GAMES.genshin.stars.src}" alt="">`,
    hsr: () => '<svg viewBox="0 0 66.4 12" aria-hidden="true">' + [0, 13.6, 27.2, 40.8, 54.4].map((x) => `<use href="#hsr-star" x="${x}" width="12" height="12"/>`).join('') + '</svg>',
    zzz: () => ''                       // the S badge sits on the corner of the plate (.cb-zbadge)
  };

  // Loads one picture unless it already shows this slide's picture (cycling one half leaves the other half alone).
  function pic(img, key, urls, token, onFail, alt) {
    if (img.dataset.k === key && img.complete && img.naturalWidth > 0) return;
    img.dataset.k = key; img.hidden = true; img.removeAttribute('src');
    img.addEventListener('load', () => { if (token === state.token) img.hidden = false; }, { once: true });
    loadChain(img, urls, token, onFail, alt);
  }

  function renderCombo(animate, half = 'cw') {
    const c = state.chars[state.ci], w = state.weapons[state.wi];
    if (!c || !w) return;
    state.combo = true; state.slide = c;
    const g = GAMES[state.game], token = ++state.token, game = state.game;
    const stage = $('stage');
    stage.querySelectorAll('.stage-ghost').forEach((e) => e.remove());
    $('stageLayer').getAnimations().forEach((a) => a.cancel()); $('plate').getAnimations().forEach((a) => a.cancel());
    $('banner').dataset.mode = 'combo'; $('combo').hidden = false;

    const element = elementOf(game, c.element);
    if (element) { document.body.style.setProperty('--ah', element.h); document.body.style.setProperty('--ac', element.c); }
    else { document.body.style.removeProperty('--ah'); document.body.style.removeProperty('--ac'); }
    $('cbTitle').textContent = g.comboTitle;

    // plates: the banner name on the tab, the item name on the plate, element and stars on the strip
    const role = game === 'hsr' && c.role ? ` (${c.role === 'Hunt' ? 'The Hunt' : c.role})` : '';
    $('cbCName').textContent = c.name + role; $('cbCTab').textContent = c.bannerTitle || c.banner || '';
    $('cbWName').textContent = w.name; $('cbWTab').textContent = w.bannerTitle || w.banner || '';
    const ei = $('cbCEl'); if (element) { ei.src = element.icon; ei.alt = element.name; ei.hidden = false; } else { ei.hidden = true; ei.removeAttribute('src'); }
    $('cbWEl').hidden = true;
    $('cbPlateC').classList.toggle('no-el', !element); $('cbPlateW').classList.add('no-el');
    ['cbCStars', 'cbWStars'].forEach((id) => { $(id).innerHTML = RANK_MARKUP[game](); });
    [['cbCPrev', 'cbCNext', state.chars.length], ['cbWPrev', 'cbWNext', state.weapons.length]].forEach(([a, b, n]) => { $(a).hidden = $(b).hidden = n < 2; });
    $('cbLive').textContent = `Character banner ${state.ci + 1} of ${state.chars.length}: ${c.name}. Weapon banner ${state.wi + 1} of ${state.weapons.length}: ${w.name}.`;

    // character art: the saved file, then its URL, then the icon
    const art = $('cbArt');
    if (c.art || c.artRemote) {
      art.className = `cb-art ${g.artClass}`;
      if (art.dataset.k === `${c.id}|art` && art.complete && art.naturalWidth > 0) classifyComboArt();      // already shown: no load event will come
      pic(art, `${c.id}|art`, [c.art, c.artRemote], token, () => { art.className = 'cb-art art-icon'; pic(art, `${c.id}|icon`, [c.icon, c.iconRemote], token, null, `${c.name}, character icon`); }, `${c.name}, character art`);
    } else { art.className = 'cb-art art-icon'; pic(art, `${c.id}|icon`, [c.icon, c.iconRemote], token, null, `${c.name}, character icon`); }

    // weapon card: Star Rail light cone art fills the card; other games' weapon art (a cutout) sits inside it
    const card = $('cbCard'), wimg = $('cbWArt');
    card.classList.toggle('is-contain', game !== 'hsr');
    const wIcon = () => { card.classList.add('is-contain'); pic(wimg, `${w.id}|icon`, [w.icon, w.iconRemote], token, null, `${w.name}, item icon`); };
    if (w.art || w.artRemote) pic(wimg, `${w.id}|art`, [w.art, w.artRemote], token, wIcon, `${w.name}, weapon art`); else wIcon();

    if (animate && !reduceMotion) {
      const fade = (el) => el.animate([{ opacity: 0 }, { opacity: 1 }], { duration: 220, easing: SLIDE_EASE });
      if (half.includes('c')) { fade(document.querySelector('.cb-char')); fade($('cbPlateC')); }
      if (half.includes('w')) { fade(document.querySelector('.cb-wpn')); fade($('cbPlateW')); }
    }
    syncSelects();
    renderTime(); update();
    requestAnimationFrame(fitCombo);
  }

  function cycleCombo(half, d) {
    if (half === 'c') state.ci = (state.ci + d + state.chars.length) % state.chars.length;
    else state.wi = (state.wi + d + state.weapons.length) % state.weapons.length;
    renderCombo(true, half);
  }

  // A long name shrinks (down to 60%) so it stays on the plate.
  function fitCombo() {
    if (!state.combo) return;
    ['cbCName', 'cbWName'].forEach((id) => {
      const n = $(id); n.style.setProperty('--fit', '1');
      if (n.scrollWidth > n.clientWidth + 1) n.style.setProperty('--fit', Math.max(0.6, n.clientWidth / n.scrollWidth - 0.02).toFixed(3));
    });
  }

  /* The name stays on one line while the plate floats over the art; a long name shrinks the whole plate. */
  function fitPlate() {
    const plate = $('plate'), name = $('itemName');
    plate.style.setProperty('--fit', '1');
    if (name.scrollWidth > name.clientWidth + 1) plate.style.setProperty('--fit', Math.max(0.55, name.clientWidth / name.scrollWidth - 0.01).toFixed(3));
  }

  // Zenless Zone Zero: a wide crop (the HoYoLAB banner crop, a 16:9 cinema art) fills the stage; upright art (a character-select portrait) is placed at the right instead
  $('stageArt').addEventListener('load', () => {
    const a = $('stageArt');
    if (state.game === 'zzz' && /\bart-(bust|tall)\b/.test(a.className) && a.naturalWidth) {
      const cls = a.naturalWidth / a.naturalHeight < 1.4 ? 'art-tall' : 'art-bust';
      a.className = `stage-art ${cls}`; $('banner').dataset.art = cls;
    }
  });

  // the same for the character art of the combined display. renderCombo calls it too: cycling only the weapon leaves a picture that is
  // already shown alone (pic() skips it, so no load event comes), and renderCombo has just set the class of a wide crop again.
  function classifyComboArt() {
    const a = $('cbArt');
    if (state.game === 'zzz' && /\bart-(bust|tall)\b/.test(a.className) && a.naturalWidth) a.className = `cb-art ${a.naturalWidth / a.naturalHeight < 1.4 ? 'art-tall' : 'art-bust'}`;
  }
  $('cbArt').addEventListener('load', classifyComboArt);

  // Star Rail light cone banner: the art is also shown enlarged and faded behind the card, once the real art (not the icon) has loaded
  $('stageArt').addEventListener('load', () => {
    const a = $('stageArt'), b = $('hlBgArt');
    if ($('stageLayer').dataset.layout === 'hsr-lc' && a.classList.contains('art-weapon') && a.currentSrc && !a.currentSrc.startsWith('data:')) { b.src = a.currentSrc; b.hidden = false; }
    else b.hidden = true;
  });

  /* Star Rail panel text is shown in full: a long description shrinks (down to 9.5 px) before it would scroll. */
  function fitHsrText() {
    const d = $('hbDesc');
    d.style.removeProperty('--fs');
    if (!d.offsetParent) return;
    let fs = parseFloat(getComputedStyle(d).fontSize);
    while (d.scrollHeight > d.clientHeight + 1 && fs > 9.5) { fs -= 0.5; d.style.setProperty('--fs', fs + 'px'); }
  }

  function checkClamp() {
    const d = $('desc'), more = $('descMore'), open = $('descWrap').classList.contains('is-open');
    if (open) { more.hidden = false; return; }
    more.hidden = !(d.scrollHeight > d.clientHeight + 1);
  }

  /* ---- Toast -------------------------------------------------------------------------------------- */
  /* The messages for functions only the Python side can do say "works in the desktop application" (right for the browser preview).
     Inside the application the same function is one the Python side does not have yet. */
  const inApp = (msg) => (window.PT_API && window.PT_API.mode === 'backend'
    ? msg.replace(' works in the desktop application.', ' is not available yet in this version of the application.').replace(' In this browser preview, type the path.', ' Type the path instead.') : msg);
  function toast(msg) {
    msg = inApp(msg);
    const open = Array.from(document.querySelectorAll('dialog[open]')).pop();
    const box = open ? open.querySelector('.toasts') : $('toasts'), t = document.createElement('div'); t.className = 'toast'; t.textContent = msg; box.appendChild(t);
    while (box.children.length > 3) box.firstChild.remove();
    setTimeout(() => {
      if (reduceMotion) { t.remove(); return; }
      t.classList.add('is-leaving'); t.addEventListener('animationend', () => t.remove(), { once: true });
    }, 3600);
  }

  /* ---- Events -------------------------------------------------------------------------------------- */
  document.querySelectorAll('.tab').forEach((tab) => {
    tab.addEventListener('click', () => { if (tab.dataset.game !== state.game) setGame(tab.dataset.game); });
    tab.addEventListener('keydown', (e) => {
      const tabs = Array.from(document.querySelectorAll('.tab')); let i = tabs.indexOf(tab);
      if (e.key === 'ArrowRight') i = (i + 1) % tabs.length; else if (e.key === 'ArrowLeft') i = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === 'Home') i = 0; else if (e.key === 'End') i = tabs.length - 1; else return;
      e.preventDefault(); tabs[i].focus(); setGame(tabs[i].dataset.game);
    });
  });
  $('nextBtn').addEventListener('click', () => { state.index = (state.index + 1) % state.slides.length; renderSlide(true, 1); });
  $('prevBtn').addEventListener('click', () => { state.index = (state.index - 1 + state.slides.length) % state.slides.length; renderSlide(true, -1); });
  $('brand').addEventListener('click', () => { state.index = 0; renderSlide(true); $('main').scrollTop = 0; $('main').focus({ preventScroll: true }); });
  selC.addEventListener('change', onGoalChange);
  selW.addEventListener('change', onGoalChange);
  [['cbCPrev', 'c', -1], ['cbCNext', 'c', 1], ['cbWPrev', 'w', -1], ['cbWNext', 'w', 1]].forEach(([id, half, d]) => $(id).addEventListener('click', () => cycleCombo(half, d)));
  $('descMore').addEventListener('click', () => {
    const w = $('descWrap'), open = !w.classList.contains('is-open'); w.classList.toggle('is-open', open);
    $('descMore').setAttribute('aria-expanded', String(open)); $('descMore').textContent = open ? 'Show less' : 'Read more';
  });
  let rz; window.addEventListener('resize', () => { clearTimeout(rz); rz = setTimeout(() => { fitPlate(); checkClamp(); fitHsrText(); fitCombo(); }, 120); });
  if (document.fonts && document.fonts.ready) document.fonts.ready.then(() => { fitPlate(); checkClamp(); fitCombo(); });
  setInterval(() => { renderTime(); renderPager(); }, 30000);

  /* ---- Failures and drops --------------------------------------------------------------------------- */
  // A call to the application that failed (js/api.js raises the Python error and announces it): say so, whether or not a screen catches it.
  window.addEventListener('pt:failed', (ev) => { console.error(ev.detail); toast(`Something went wrong: ${(ev.detail && ev.detail.message) || 'the application could not do that.'}`); });
  // A file or a link dropped on the window would open in it, and that page would get the application's methods (export, import, remove account):
  // dropping does nothing.
  ['dragover', 'drop'].forEach((t) => window.addEventListener(t, (ev) => ev.preventDefault()));

  /* ---- Start --------------------------------------------------------------------------------------- */
  setGame(lastGame(), { silent: true });
  API.ready().then(() => {
    if (API.mode !== 'backend') return;
    hist.backend = true;
    loadHist(state.game);
  });
  document.addEventListener('pt:data', () => {      // a sync, an import or a removed account changed the stored pulls
    if (!hist.backend) return;
    hist.loaded = {};
    loadHist(state.game);
  });

  /* ---- Shared with js/views.js ---------------------------------------------------------------------- */
  // Show a banner of the current game on the Main screen (used by "Plan for this banner").
  function goBanner(id) {
    const i = state.slides.findIndex((s) => s.id === id);
    state.combo = false;
    if (i >= 0) state.index = i;
    renderSlide(true);
  }
  window.PT_APP = { GAMES, state, now, fmt, toast, elementOf, setGame, goBanner, dur, stamp, qs, reduceMotion, buildSlides, PULL_COST };
})();
