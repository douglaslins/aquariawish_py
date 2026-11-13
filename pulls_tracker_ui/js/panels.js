/*
  Pulls Tracker, Settings modal: Settings (IA S11), Sync (S1 first run, S2 progress and result, S12 errors), Import / Export (S9), Data sources (S10).
  Pop-ups opened from here (confirmations, loss pool, errors) use the shared dialog of js/views.js, so they are modals too.
*/
(() => {
  'use strict';

  const A = window.PT_APP, API = window.PT_API, R = window.PT_RULES, EV = window.PT_EVENTS;
  const U = window.PT_UI, { h, svg, core, fmtT, ago, maskUid, accountOf, openDialog, confirmDialog, showErrorDialog, errorInfo, showView, saveOrDownload, topName } = U;
  const $ = (id) => document.getElementById(id);
  const clone = (o) => JSON.parse(JSON.stringify(o));
  const modal = $('settingsModal');
  const TABS = { settings: 'Settings', sync: 'Sync', transfer: 'Import / Export', data: 'Data sources' };
  const GAMES = Object.keys(R);
  let tab = 'settings', draft = null, dirty = false, storageInfo = null, opener = null, winOnTop = null;

  /* ---- Modal frame ------------------------------------------------------------------------------------ */
  function selectTab(name, focus) {
    if (!TABS[name]) name = 'settings';
    tab = name;
    modal.querySelectorAll('.mtab').forEach((t) => { const on = t.dataset.tab === name; t.setAttribute('aria-selected', String(on)); t.tabIndex = on ? 0 : -1; if (on && focus) t.focus(); });
    modal.querySelectorAll('.mpanel').forEach((p) => { p.hidden = p.id !== `mp-${name}`; });
    $('modalTitle').textContent = TABS[name];
    modal.querySelector('.modal-panels').scrollTop = 0;
    return renderTab(name);
  }
  function renderTab(name) { return ({ settings: renderSettings, sync: renderSync, transfer: renderTransfer, data: renderSources }[name] || renderSettings)(); }
  async function openModal(name, opts = {}) {
    if (!core.settings) await U.refreshCore();
    if (!modal.open) { opener = document.activeElement; if (!dirty) draft = null; modal.showModal(); }
    await selectTab(name);
    if (name === 'sync' && opts.auto && syncState.state !== 'running') startSync();
  }
  async function requestClose() {
    if (tab === 'settings' && dirty) {
      const ok = await confirmDialog({ title: 'Discard changes?', message: 'Settings you changed have not been saved.', ok: 'Discard', cancel: 'Keep editing', danger: true });
      if (!ok) return;
      draft = null; dirty = false;
    }
    modal.close();
  }
  modal.querySelectorAll('.mtab').forEach((t) => {
    t.addEventListener('click', () => selectTab(t.dataset.tab));
    t.addEventListener('keydown', (e) => {
      const tabs = Array.from(modal.querySelectorAll('.mtab')); let i = tabs.indexOf(t);
      if (e.key === 'ArrowDown' || e.key === 'ArrowRight') i = (i + 1) % tabs.length; else if (e.key === 'ArrowUp' || e.key === 'ArrowLeft') i = (i - 1 + tabs.length) % tabs.length;
      else if (e.key === 'Home') i = 0; else if (e.key === 'End') i = tabs.length - 1; else return;
      e.preventDefault(); selectTab(tabs[i].dataset.tab, true);
    });
  });
  $('modalClose').addEventListener('click', requestClose);
  modal.addEventListener('click', (e) => { if (e.target === modal) requestClose(); });
  modal.addEventListener('cancel', (e) => { e.preventDefault(); requestClose(); });
  modal.addEventListener('close', () => { if (opener && document.contains(opener)) opener.focus(); });
  document.addEventListener('pt:open', (e) => openModal(e.detail.modal, { auto: e.detail.auto }));

  /* ---- Form helpers ----------------------------------------------------------------------------------------- */
  const row = (name, ...ctl) => h('div', { class: 'set-row' }, h('span', { class: 'set-name' }, name), ...ctl);
  const group = (title, ...kids) => h('div', { class: 'set-group' }, h('h3', null, title), ...kids);
  const note = (t) => h('p', { class: 'set-note' }, t);
  function selectBox(opts, val, on, label) {
    return h('span', { class: 'select set-ctl' }, h('select', { 'aria-label': label, onchange: (e) => on(e.target.value) }, opts.map((o) => h('option', { selected: o === val }, o))), svg('i-chevron-down', 'ico select-chev'));
  }
  const textIn = (val, on, ph, label) => h('input', { class: 'in', type: 'text', value: val || '', placeholder: ph || '', autocomplete: 'off', spellcheck: 'false', 'aria-label': label, oninput: (e) => on(e.target.value) });
  const numIn = (obj, key, min, max, label) => h('input', { class: 'in in-num', type: 'number', min, max, value: obj[key], 'aria-label': label, oninput: (e) => { obj[key] = e.target.value === '' ? NaN : Number(e.target.value); markDirty(); } });
  const check = (obj, key, text, on) => h('label', { class: 'set-row set-check' }, h('input', { type: 'checkbox', checked: !!obj[key], onchange: (e) => { obj[key] = e.target.checked; if (on) on(e.target.checked); markDirty(); } }), h('span', null, text));
  function folderPick(game, input, obj) {
    return h('button', { class: 'btn', type: 'button', onclick: async () => {
      const r = await API.chooseFolder(game);
      if (!r || r.unavailable) { A.toast('Choosing a folder works in the desktop application. In this browser preview, type the path.'); return; }
      if (r.path) { input.value = r.path; obj[game] = r.path; markDirty(); }
    } }, 'Choose folder');
  }

  /* ---- Settings (S11) ---------------------------------------------------------------------------------------------- */
  const SOFT_LABEL = { genshin: { character: 'Character', weapon: 'Weapon' }, hsr: { character: 'Character', lightcone: 'Light cone' }, zzz: { exclusive: 'Agent', wengine: 'W-Engine' } };
  function markDirty() { dirty = true; const b = $('setSave'); if (b) { const errs = validate(draft); b.disabled = errs.length > 0; $('setErr').textContent = errs[0] || ''; } }
  function validate(d) {
    const e = [];
    GAMES.forEach((g) => Object.keys(d.soft[g]).forEach((k) => { const v = d.soft[g][k], hard = R[g].groups.find((x) => x.key === k).hard; if (!Number.isInteger(v) || v < 1 || v > hard) e.push(`Soft pity for ${R[g].title}, ${SOFT_LABEL[g][k]}, must be a whole number from 1 to ${hard}.`); }));
    const pc = d.pityColors; if (!(pc.green >= 1 && pc.gold > pc.green && pc.gold <= 90)) e.push('Pity colors: green must be below gold, both from 1 to 90.');
    const gr = d.grade; if (!(gr.ss >= 1 && gr.a > gr.ss && gr.b > gr.a && gr.c > gr.b && gr.c <= 90)) e.push('Luck grade: SS, A, B and C must increase, from 1 to 90.');
    if (d.lossPool.some((x) => !String(x).trim())) e.push('The Star Rail loss pool has an empty name.');
    return e;
  }
  /* "Keep the window above other windows": a setting of the window, not of the saved settings: it applies at once and does not wait for Save. */
  const onTopRow = () => h('label', { class: 'set-row set-check' },
    h('input', { type: 'checkbox', checked: !!winOnTop, onchange: async (e) => {
      const r = await API.windowSetOnTop(e.target.checked);
      if (!r || !r.ok) { e.target.checked = !!winOnTop; A.toast(r && r.unavailable ? 'Keeping the window on top is not available yet in this version of the application.' : 'The window could not be changed.'); return; }
      winOnTop = !!r.onTop;
    } }),
    h('span', null, 'Keep the window above other windows (applies at once, off by default)'));
  async function renderSettings() {
    const panel = $('mp-settings');
    if (!draft) { draft = clone(core.settings); dirty = false; }
    storageInfo = storageInfo || await API.storage();
    if (API.mode === 'backend' && winOnTop === null) { const w = await API.windowGetOnTop(); winOnTop = w && !w.unavailable ? !!w.onTop : false; }
    const nick = {};
    const acctRows = core.accounts.length ? core.accounts.map((a) => {
      nick[`${a.game}|${a.uid}`] = a.nickname || '';
      return h('div', { class: 'set-row acct-row' },
        h('span', { class: 'set-name' }, R[a.game].title, h('small', { class: 'acct-uid' }, `UID ${maskUid(a.uid)} (${a.region})`)),
        h('div', { class: 'set-inline' },
          core.accounts.filter((x) => x.game === a.game).length > 1 ? (accountOf(a.game) === a ? h('span', { class: 'pill ok' }, 'Shown') : h('button', { class: 'btn', type: 'button', onclick: async () => {
            const r = await API.setActiveAccount(a.game, a.uid);
            if (!r || r.unavailable) { A.toast(API.mode === 'backend' ? 'That account could not be shown.' : 'Switching accounts works in the desktop application.'); return; }
            document.dispatchEvent(new CustomEvent('pt:data')); await U.refreshCore(); renderSettings();
          } }, 'Show')) : null,
          h('label', null, 'Nickname ', h('input', { class: 'in in-nick', type: 'text', value: a.nickname || '', maxlength: 24, 'aria-label': `Nickname for ${R[a.game].title}`, oninput: (e) => { draft.nicknames = draft.nicknames || {}; draft.nicknames[`${a.game}|${a.uid}`] = e.target.value; markDirty(); } })),
          h('button', { class: 'btn btn-danger-o', type: 'button', onclick: async () => {
            const ok = await confirmDialog({ title: 'Remove this account?', message: `This deletes the stored pulls of ${R[a.game].title}, UID ${maskUid(a.uid)}, from this computer. The game keeps its own history. This cannot be undone.`, ok: 'Remove account and pulls', danger: true });
            if (!ok) return; await API.removeAccount(a.game, a.uid); A.toast('Account removed.'); document.dispatchEvent(new CustomEvent('pt:data')); await U.refreshCore(); renderSettings();
          } }, 'Remove')));
    }) : [h('div', { class: 'set-row' }, h('span', { class: 'set-name' }, 'Accounts'), h('span', { class: 'set-value' }, 'No account yet. It appears here after the first sync.'))];

    const folders = GAMES.map((g) => { const inp = textIn(draft.folders[g], (v) => { draft.folders[g] = v; markDirty(); }, { genshin: 'D:/Games/Genshin Impact', hsr: 'D:/Games/Star Rail', zzz: 'D:/Games/ZenlessZoneZero Game' }[g], `${R[g].title} folder`); return h('div', { class: 'set-row' }, h('span', { class: 'set-name' }, R[g].title), h('div', { class: 'set-inline set-inline-fill' }, inp, folderPick(g, inp, draft.folders))); });
    const soft = GAMES.map((g) => row(`Soft pity start, ${R[g].title}`, h('div', { class: 'set-inline' }, Object.keys(draft.soft[g]).map((k) => h('label', null, `${SOFT_LABEL[g][k]} `, numIn(draft.soft[g], k, 1, R[g].groups.find((x) => x.key === k).hard, `${R[g].title} ${SOFT_LABEL[g][k]} soft pity`))))));

    panel.replaceChildren(
      group('Accounts', ...acctRows, note('Show chooses the account the screens display for that game. Remove deletes the account\'s stored pulls and asks first.')),
      group('Games to track', h('div', { class: 'set-row set-checks' }, GAMES.map((g) => h('label', { class: 'radio' }, h('input', { type: 'checkbox', checked: !!draft.track[g], onchange: (e) => { draft.track[g] = e.target.checked; markDirty(); } }), ` ${R[g].title}`)))),
      group('Display',
        row('Language', selectBox(['English'], draft.language, (v) => { draft.language = v; markDirty(); }, 'Language')),
        row('Times', selectBox(['Local', 'Server', 'UTC'], draft.times, (v) => { draft.times = v; markDirty(); }, 'Time zone for dates')),
        API.mode === 'backend' ? onTopRow() : null),
      group('Game folders', ...folders, note('Set a folder only when the application does not find the game by itself.')),
      group('Thresholds', ...soft,
        row('Pity colors', h('div', { class: 'set-inline' }, h('label', null, 'Green below ', numIn(draft.pityColors, 'green', 1, 90, 'Green below')), h('label', null, 'Gold below ', numIn(draft.pityColors, 'gold', 1, 90, 'Gold below')), h('span', { class: 'set-value dim' }, 'red from there'))),
        row('Luck grade', h('div', { class: 'set-inline' }, h('label', null, 'SS below ', numIn(draft.grade, 'ss', 1, 90, 'SS below')), h('label', null, 'A up to ', numIn(draft.grade, 'a', 1, 90, 'A up to')), h('label', null, 'B up to ', numIn(draft.grade, 'b', 1, 90, 'B up to')), h('label', null, 'C up to ', numIn(draft.grade, 'c', 1, 90, 'C up to')))),
        note('Soft pity values are community estimates. HoYoverse does not publish them, so every value can be changed. The weapon value for Genshin (63) is a placeholder.')),
      group('Star Rail', row('50/50 loss pool', h('div', { class: 'set-inline' }, h('span', { class: 'set-value' }, `${draft.lossPool.length} characters: ${draft.lossPool.join(', ')}`), h('button', { class: 'btn', type: 'button', onclick: editLossPool }, 'Edit the 7 characters in your pool')))),
      group('Privacy and storage',
        check(draft, 'maskUid', 'Mask the UID on screens and in exports to images'),
        row('Backup folder', h('div', { class: 'set-inline set-inline-fill' }, (() => { const i = textIn(draft.backupFolder, (v) => { draft.backupFolder = v; markDirty(); }, 'D:/Backups/PullsTracker', 'Backup folder'); return [i, folderPick('backup', i, { set backup(v) { draft.backupFolder = v; } })]; })())),
        check(draft, 'autoBackup', 'Back up automatically before a sync, before an account is removed and before an import replaces pulls'),
        storageInfo && storageInfo.backups ? backupRow(storageInfo.backups) : null,
        storageInfo && storageInfo.dataFolder ? row('Data folder', h('span', { class: 'set-value' }, storageInfo.dataFolder)) : null,
        storageInfo && storageInfo.warning ? h('p', { class: 'vnote', role: 'alert' }, svg('i-info'), storageInfo.warning) : null,
        row('Image cache', h('div', { class: 'set-inline' }, h('span', { class: 'set-value' }, storageInfo && storageInfo.note ? storageInfo.note : `${storageInfo ? storageInfo.imageCacheMB : 0} MB`),
          h('button', { class: 'btn', type: 'button', onclick: async () => {
            const ok = await confirmDialog({ title: 'Clear the image cache?', message: 'Downloaded pictures are removed from this computer and downloaded again when needed. Pulls are not affected.', ok: 'Clear image cache', danger: true });
            if (!ok) return; const r = await API.clearImageCache(); if (r && r.unavailable) A.toast('Clearing the cache works in the desktop application.'); else if (r && r.error) A.toast(r.error); else { storageInfo = null; A.toast(r && r.failed ? `Image cache cleared, but ${A.fmt(r.failed)} file${r.failed === 1 ? '' : 's'} could not be removed.` : 'Image cache cleared.'); renderSettings(); }
          } }, 'Clear image cache')))),
      h('div', { class: 'panel-foot' }, h('p', { class: 'form-err', id: 'setErr', role: 'alert' }),
        h('button', { class: 'btn', type: 'button', onclick: async () => { if (dirty) { const ok = await confirmDialog({ title: 'Discard changes?', message: 'Settings you changed have not been saved.', ok: 'Discard', cancel: 'Keep editing', danger: true }); if (!ok) return; } draft = null; dirty = false; modal.close(); } }, 'Cancel'),
        h('button', { class: 'btn btn-primary', type: 'button', id: 'setSave', onclick: saveSettings }, 'Save')));
    markDirty(); dirty = false;
    panel.querySelectorAll('input[type=number]').forEach((n) => n.addEventListener('input', () => { dirty = true; }));
  }
  async function saveSettings() {
    if (validate(draft).length) return;
    const names = draft.nicknames || {}, toSave = { ...draft }; delete toSave.nicknames;      // the draft keeps the nicknames until everything is saved
    await API.saveSettings(toSave);
    for (const k of Object.keys(names)) { const [g, uid] = k.split('|'); await API.saveNickname(g, uid, names[k].trim()); }
    draft = null; dirty = false; A.toast('Settings saved.');
    await U.refreshCore(); document.dispatchEvent(new CustomEvent('pt:data'));
    renderSettings();
  }
  async function editLossPool() {
    const list = clone(draft.lossPool), DEFAULT = ['Himeko', 'Welt', 'Bronya', 'Gepard', 'Clara', 'Yanqing', 'Bailu'];
    const inputs = list.map((n, i) => h('label', { class: 'pool-i' }, h('span', null, i + 1), h('input', { class: 'in', type: 'text', value: n, maxlength: 40, 'aria-label': `Character ${i + 1}`, autocomplete: 'off' })));
    const body = h('div', null, h('p', { class: 'dlg-text' }, 'After a lost 50/50 the 5-star is one of these characters. Edit the list when the game changes it.'), h('div', { class: 'pool' }, inputs));
    const r = await openDialog({ title: 'Star Rail 50/50 loss pool', body, size: 'sm', actions: [
      { label: 'Cancel', value: 'cancel' },
      { label: 'Reset to default', value: 'reset', onClick: () => inputs.forEach((l, i) => { if (i < DEFAULT.length) l.querySelector('input').value = DEFAULT[i]; else l.remove(); }) },   // a longer saved list (up to 30 names are kept) loses its extra rows
      { label: 'Apply', value: 'ok', kind: 'primary', focus: true, onClick: () => { const v = inputs.filter((l) => l.isConnected).map((l) => l.querySelector('input').value.trim()); if (v.some((x) => !x)) { A.toast('Every character needs a name.'); return; } draft.lossPool = v; U.finish('ok'); } }] });
    if (r === 'ok') { markDirty(); renderSettings(); }
  }

  /* Settings > Storage: what the backup folder holds, and a button for a copy now. */
  function backupRow(b) {
    const text = b.count ? `${b.count} file${b.count === 1 ? '' : 's'}, ${b.mb} MB, newest ${fmtT(Date.parse(b.newest), A.state.game)}` : 'No backup yet.';
    return row('Backups', h('div', { class: 'set-inline' }, h('span', { class: 'set-value' }, text),
      h('button', { class: 'btn', type: 'button', onclick: async () => {
        const r = await API.backupNow();
        if (!r || r.unavailable) A.toast('Backing up works in the desktop application.');
        else if (r.empty) A.toast('Nothing to back up yet.');
        else if (r.error) A.toast('The backup could not be written.');
        else A.toast(`Backup written: ${r.name}`);
        storageInfo = null; renderSettings();
      } }, 'Back up now')));
  }

  /* ---- Sync (S1, S2, S12) ------------------------------------------------------------------------------------------------ */
  const STEPS = ['Find the game cache', 'Find a valid link', 'Fetch the history', 'Merge into the local database'];
  const syncState = { state: 'idle', game: null, steps: [], fetch: {}, result: null, error: null, wait: null, firstRun: null };
  const freshSteps = () => STEPS.map(() => ({ state: 'idle', text: '' }));
  const stepLabel = { idle: 'Not started', running: 'Running', ok: 'OK', failed: 'Failed' };

  EV.on((ev) => {
    if (ev.game && syncState.game && ev.game !== syncState.game) return;
    const s = syncState;
    if (ev.type === 'step') { s.steps[ev.n - 1] = { state: ev.state, text: ev.text || '' }; if (ev.n === 3 && ev.state === 'running') s.fetch = {}; }
    else if (ev.type === 'fetch') { s.wait = null; s.fetch[ev.group] = { label: ev.label, page: ev.page, pages: ev.pages, items: ev.items, done: !!ev.done }; }
    else if (ev.type === 'wait') s.wait = ev;
    else if (ev.type === 'done') { s.state = 'done'; s.result = ev.result; document.dispatchEvent(new CustomEvent('pt:data')); }
    else if (ev.type === 'error') { s.state = 'error'; s.error = { code: ev.code, params: ev }; }
    if (!modal.open || tab !== 'sync') return;
    renderSync(true);
  });

  /* opts.newAccount: accept a UID that is not stored yet as another account (the "Add as new account" button). The argument is also what a
     click handler passes, so only { newAccount: true } counts. */
  async function startSync(opts) {
    const game = syncState.state === 'error' && syncState.game ? syncState.game : A.state.game;      // Retry and Add as new account repeat the game that stopped
    const newAccount = !!opts && opts.newAccount === true;
    Object.assign(syncState, { state: 'running', game, steps: freshSteps(), fetch: {}, result: null, error: null, wait: null });
    renderSync();
    let r;
    try { r = await API.syncStart(game, newAccount ? { newAccount: true } : undefined); } catch (err) {
      console.error(err); Object.assign(syncState, { state: 'error', error: { code: 'internal', params: {} } }); renderSync(); return;
    }
    if (r && r.busy) { syncState.state = 'idle'; renderSync(); A.toast('A sync is still running or stopping. Try again in a few seconds.'); }
    else if (r && r.unavailable && syncState.state === 'running') { Object.assign(syncState, { state: 'error', error: { code: 'no_backend', params: {} } }); renderSync(); }
  }
  async function cancelSync() {
    await API.syncCancel(); syncState.state = 'cancelled';
    syncState.steps = syncState.steps.map((s) => (s.state === 'running' ? { state: 'failed', text: 'cancelled' } : s));
    renderSync();
  }

  async function renderSync(keepFocus) {
    const panel = $('mp-sync'), game = syncState.state === 'idle' || !syncState.game ? A.state.game : syncState.game, acct = accountOf(game), s = syncState;
    if (s.state === 'idle') s.game = null;
    const focusId = keepFocus && panel.contains(document.activeElement) ? document.activeElement.id : null;
    let nodes;
    if (s.state === 'running' || s.state === 'error' || s.state === 'cancelled') nodes = syncRun(game, acct);
    else if (s.state === 'done') nodes = syncDone(game);
    else if (!acct) nodes = await firstRun(game);
    else nodes = syncIdle(game, acct);
    panel.replaceChildren(...nodes.filter(Boolean));      // a null in the list would be written as the text "null"
    if (focusId && $(focusId)) $(focusId).focus();
  }
  const meta = (k, v) => h('div', { class: 'card-row' }, h('span', { class: 'set-name' }, k), h('span', null, v));
  const stepList = (steps) => h('ol', { class: 'steps', 'aria-label': 'Sync steps', 'aria-live': 'polite' },
    STEPS.map((n, i) => h('li', { class: `st-${steps[i].state}` }, h('span', { class: 'step-name' }, n), h('span', { class: 'step-state' }, steps[i].text ? `${stepLabel[steps[i].state]}, ${steps[i].text}` : stepLabel[steps[i].state]))));

  function syncIdle(game, acct) {
    const last = core.lastSync[game];
    return [h('div', { class: 'card' }, meta('Game', R[game].title), meta('Account', `UID ${maskUid(acct.uid)} (${acct.region})`), meta('Last sync', last ? `${fmtT(Date.parse(last), game)} (${ago(A.now() - Date.parse(last))} ago)${API.mode === 'sample' ? ', sample' : ''}` : 'Never')),
      stepList(freshSteps()),
      h('p', { class: 'set-note' }, 'A sync reads the saved link of the game, fetches the history page by page and adds only the pulls that are not stored yet. Open the pull history page in the game first.'),
      h('div', { class: 'panel-actions' }, h('button', { class: 'btn btn-primary', type: 'button', id: 'syncGo', onclick: () => startSync() }, 'Sync now'))];
  }

  async function firstRun(game) {
    const info = await API.firstRun();
    const byGame = Object.fromEntries((info.games || []).map((g) => [g.game, g]));
    const tracked = GAMES.filter((g) => core.settings.track[g]);
    const folderText = (g) => { const f = byGame[g] && byGame[g].folder; return f && f.state === 'found' ? `Found: ${f.path}` : f && f.state === 'missing' ? 'Not found. Choose the game folder.' : (API.mode === 'backend' ? 'Not checked yet.' : 'Not checked: the browser preview cannot see the disk.'); };
    const linkText = (g) => { const l = byGame[g] && byGame[g].link; return !l || l.state === 'unknown' ? 'not checked' : l.state === 'missing' ? 'not found' : `found, ${l.ageHours < 1 ? `${Math.round(l.ageHours * 60)} min` : `${Math.round(l.ageHours)} h`} old`; };
    const stale = tracked.filter((g) => byGame[g] && byGame[g].link && byGame[g].link.state === 'found' && byGame[g].link.ageHours > 24);
    return [
      h('p', { class: 'fr-lead' }, 'No pull history yet. Three steps import your history.'),
      h('ol', { class: 'fr-steps' },
        h('li', null, h('h3', null, 'Games to track'), h('div', { class: 'set-inline' }, GAMES.map((g) => h('label', { class: 'radio' }, h('input', { type: 'checkbox', checked: !!core.settings.track[g], onchange: async (e) => { const s = clone(core.settings); s.track[g] = e.target.checked; await API.saveSettings(s); await U.refreshCore(); draft = null; renderSync(); } }), ` ${R[g].title}`)))),
        h('li', null, h('h3', null, 'Open the pull history in each game'), h('p', null, 'Open each game, open its pull history page once (Wish, Warp or Signal Search history) and wait until it loads. Then return here.'), h('p', { class: 'set-note' }, 'The game saves a temporary link on this computer. The application reads that link.')),
        h('li', null, h('h3', null, 'Game folder'), tracked.length ? tracked.map((g) => h('div', { class: 'fr-folder' }, h('strong', null, R[g].title), h('span', { class: 'dim' }, folderText(g)), h('button', { class: 'btn', type: 'button', onclick: async () => { const r = await API.chooseFolder(g); if (!r || r.unavailable) A.toast('Choosing a folder works in the desktop application.'); else if (r.path) { const s = clone(core.settings); s.folders[g] = r.path; await API.saveSettings(s); await U.refreshCore(); renderSync(); } } }, 'Change'))) : h('p', { class: 'dim' }, 'No game is selected to track.'))),
      h('div', { class: 'card fr-link' }, h('div', { class: 'card-row' }, h('span', { class: 'set-name' }, 'Link check'), h('span', null, tracked.map((g) => `${R[g].title}: ${linkText(g)}`).join('. ') || '-')),
        stale.length ? h('div', { class: 'card-row' }, h('span', { class: 'set-name' }, 'Warning'), h('span', { class: 'warn-t' }, `${stale.map((g) => R[g].title).join(', ')}: the link is older than 24 h and is probably expired. Open its history page again, then scan.`)) : null),
      h('div', { class: 'panel-actions' }, h('button', { class: 'btn btn-primary', type: 'button', id: 'syncGo', disabled: !tracked.length, onclick: () => startSync() }, 'Scan and import'))
    ];
  }

  function syncRun(game, acct) {
    const s = syncState, running = s.state === 'running';
    const fetchRows = Object.keys(s.fetch).map((k) => { const f = s.fetch[k], pct = f.pages ? Math.round((f.page / f.pages) * 100) : 0; return h('li', { class: 'fetch-row' }, h('span', null, f.label), h('span', { class: 'fbar', role: 'progressbar', 'aria-valuemin': 0, 'aria-valuemax': 100, 'aria-valuenow': f.done ? 100 : pct, 'aria-label': `${f.label} progress` }, h('span', { style: `width:${f.done ? 100 : pct}%` })), h('span', { class: 'dim' }, f.done ? `${f.items} items, done` : `page ${f.page}, ${f.items} items`)); });
    const nodes = [
      h('div', { class: 'run-head' }, h('div', null, h('h3', null, `Sync: ${R[game].title}`), acct ? h('p', { class: 'dim' }, `UID ${maskUid(acct.uid)} (${acct.region})`) : null),
        running ? h('button', { class: 'btn', type: 'button', id: 'syncCancel', onclick: cancelSync }, 'Cancel') : null),
      stepList(s.steps)];
    if (fetchRows.length) nodes.push(h('ul', { class: 'fetch', 'aria-label': 'Fetch progress' }, fetchRows));
    if (s.wait && running) nodes.push(h('p', { class: 'set-note' }, svg('i-info'), `The server asked to slow down. Waiting ${s.wait.sec} s (retry ${s.wait.retry} of ${s.wait.of}).`));
    if (s.state === 'cancelled') nodes.push(h('p', { class: 'vnote' }, svg('i-info'), 'Sync cancelled. Pulls already merged stay stored.'), h('div', { class: 'panel-actions' }, h('button', { class: 'btn btn-primary', type: 'button', id: 'syncGo', onclick: () => startSync() }, 'Sync again')));
    if (s.state === 'error') {
      const e = errorInfo(s.error.code, s.error.params), steps = e.steps ? h('ol', { class: 'err-steps', id: 'errSteps', hidden: true }, e.steps.map((x) => h('li', null, x))) : null;
      const act = (k) => ({
        retry: h('button', { class: 'btn btn-primary', type: 'button', id: 'syncRetry', onclick: () => startSync() }, 'Retry'),
        steps: h('button', { class: 'btn', type: 'button', id: 'errToggle', 'aria-expanded': 'false', onclick: (ev) => { steps.hidden = !steps.hidden; ev.target.setAttribute('aria-expanded', String(!steps.hidden)); } }, 'Show steps'),
        folder: h('button', { class: 'btn', type: 'button', onclick: async () => {
          const r = await API.chooseFolder(game);
          if (!r || r.unavailable) { A.toast('Choosing a folder works in the desktop application.'); return; }
          if (!r.path) return;
          const st = clone(core.settings); st.folders[game] = r.path; await API.saveSettings(st); await U.refreshCore(); draft = null;
          startSync();       // the folder was the problem: read it again at once
        } }, 'Choose folder'),
        later: h('button', { class: 'btn', type: 'button', onclick: () => { syncState.state = 'idle'; renderSync(); } }, 'Try again later'),
        add: h('button', { class: 'btn', type: 'button', onclick: () => (API.mode === 'backend' ? startSync({ newAccount: true }) : A.toast('Adding the account works in the desktop application.')) }, 'Add as new account'),
        cancel: h('button', { class: 'btn', type: 'button', onclick: () => { syncState.state = 'idle'; renderSync(); } }, 'Cancel'),
        close: h('button', { class: 'btn', type: 'button', onclick: () => { syncState.state = 'idle'; renderSync(); } }, 'Close'),
        dismiss: h('button', { class: 'btn', type: 'button', onclick: () => { syncState.state = 'idle'; renderSync(); } }, 'Dismiss'),
        import: h('button', { class: 'btn', type: 'button', onclick: () => selectTab('transfer') }, 'Import file')
      }[k]);
      nodes.push(h('div', { class: 'err-box', role: 'alert' }, h('h4', null, e.title), h('p', null, e.text), steps, e.note ? h('p', { class: 'dlg-note' }, svg('i-info'), e.note) : null,
        h('div', { class: 'panel-actions' }, e.actions.map(act))));
    }
    return nodes;
  }

  function syncDone(game) {
    const r = syncState.result, tg = core.settings && topName(game);
    return [
      h('div', { class: 'run-head' }, h('div', null, h('h3', null, `Sync finished: ${R[game].title}`), h('p', { class: 'dim' }, fmtT(Date.parse(r.at), game)))),
      h('div', { class: 'card' },
        meta('New pulls stored', h('span', null, h('strong', null, A.fmt(r.added.total)), r.added.groups.length ? `   ${r.added.groups.map((x) => `${x.label} ${x.n}`).join(' | ')}` : '')),
        meta('Already stored', `${A.fmt(r.stored)}  skipped, no duplicates created`),
        meta(`New ${tg}`, String(r.new5)),
        meta('Account check', r.accountCheck)),
      ...(r.notes || []).map((n) => h('p', { class: 'vnote' }, svg('i-info'), n)),
      API.mode === 'sample' ? h('p', { class: 'vnote' }, svg('i-info'), 'Sample run: nothing was read from the game or stored.') : null,
      h('div', { class: 'panel-actions' },
        h('button', { class: 'btn', type: 'button', onclick: () => { syncState.state = 'idle'; modal.close(); showView('history'); } }, 'View history'),
        h('button', { class: 'btn btn-primary', type: 'button', id: 'syncBack', onclick: () => { syncState.state = 'idle'; modal.close(); showView('dashboard'); } }, 'Back to dashboard'))
    ];
  }

  /* ---- Import / Export (S9) --------------------------------------------------------------------------------------------------- */
  const xfer = { preview: null, choice: 'keep', file: null, written: null };
  async function readFile(file) { return { name: file.name, text: await file.text() }; }
  async function renderTransfer() {
    const panel = $('mp-transfer'), game = A.state.game, p = xfer.preview;
    const fileIn = h('input', { class: 'visually-hidden', type: 'file', id: 'importFile', accept: '.json,.csv,application/json,text/csv', tabindex: '-1',
      onchange: async (e) => { const f = e.target.files && e.target.files[0]; if (!f) return; xfer.file = f.name; xfer.preview = await API.importPreview({ ...(await readFile(f)), game }); xfer.choice = 'keep'; renderTransfer(); if (xfer.preview.mismatch) showErrorDialog('uid_mismatch', xfer.preview.mismatch); } });
    const detected = !p ? h('span', { class: 'set-value dim' }, 'Choose a file. The format, game, UID and record count show here before anything is written.')
      : p.error ? h('span', { class: 'set-value warn-t' }, p.error)
        : h('span', { class: 'set-value' }, [p.format, p.game ? R[p.game].title : 'game unknown', p.uid ? `UID ${maskUid(p.uid)}` : null, `${A.fmt(p.records)} records`].filter(Boolean).join(' | '));
    const result = p && p.ok ? [
      row('Result', h('span', { class: 'set-value' }, `New ${A.fmt(p.newRecords)} | Already stored ${A.fmt(p.stored)} | Conflicts ${p.conflicts.length}`)),
      p.note ? row('Note', h('span', { class: 'set-value dim' }, p.note)) : null,
      p.mismatch ? row('Blocked', h('span', { class: 'set-value warn-t' }, `The file belongs to UID ${maskUid(p.mismatch.found)}; the active account is ${maskUid(p.mismatch.active)}. Import is blocked for the active account.`)) : null,
      ...p.conflicts.slice(0, 5).map((c, i) => row(i ? '' : 'Conflict', h('span', { class: 'set-value' }, `id ${c.id}: ${c.reason}`))),
      p.conflicts.length ? h('fieldset', { class: 'set-row set-fieldset' }, h('legend', { class: 'set-name' }, 'Conflicts'), h('div', { class: 'set-inline' },
        [['keep', 'Keep stored'], ['file', 'Use file']].map(([v, t]) => h('label', { class: 'radio' }, h('input', { type: 'radio', name: 'cchoice', checked: xfer.choice === v, onchange: () => { xfer.choice = v; } }), ` ${t}`)))) : null] : [];
    const canImport = p && p.ok && !p.mismatch;
    panel.replaceChildren(
      group('Import',
        row('File', h('div', { class: 'set-inline set-inline-fill' }, h('input', { class: 'in', type: 'text', readonly: true, value: xfer.file || 'No file chosen', 'aria-label': 'Chosen file' }),
          h('button', { class: 'btn', type: 'button', onclick: async () => { const r = await API.pickImportFile(); if (r && r.browser) fileIn.click(); else if (r && r.path) { xfer.file = r.name || r.path; xfer.preview = await API.importPreview({ ...r, game }); renderTransfer(); } } }, 'Choose file'), fileIn)),
        row('Detected', detected), ...result.filter(Boolean),
        h('div', { class: 'panel-actions' }, h('button', { class: 'btn btn-primary', type: 'button', disabled: !canImport, onclick: async (ev) => {
          const btn = ev.currentTarget; btn.disabled = true;      // a second click must not apply the same preview again
          try {
            const r = await API.importApply(xfer.preview, xfer.choice);
            if (!r || r.unavailable) { A.toast('Importing works in the desktop application. Nothing was stored.'); return; }
            if (r.error) { A.toast(`Nothing was imported. ${r.error}`); return; }
            A.toast(`Imported ${A.fmt(r.added)} pulls.`); xfer.preview = null; xfer.file = null; document.dispatchEvent(new CustomEvent('pt:data')); renderTransfer();
          } finally { btn.disabled = false; }
        } }, 'Import'))),
      group(`Export, ${R[game].title}`,
        h('fieldset', { class: 'set-row set-fieldset' }, h('legend', { class: 'set-name' }, 'Format'), h('div', { class: 'set-inline' },
          [['uigf', 'UIGF v4.0 JSON'], ['csv', 'CSV']].map(([v, t], i) => h('label', { class: 'radio' }, h('input', { type: 'radio', name: 'expFmt', value: v, checked: i === 0 }), ` ${t}`)))),
        row('Scope', selectBox(['This account'], 'This account', () => {}, 'Scope')),
        row('Dates', h('span', { class: 'select set-ctl' }, h('select', { id: 'expDates', 'aria-label': 'Dates' }, [['all', 'All time'], ['12m', 'Last 12 months'], ['90d', 'Last 90 days'], ['30d', 'Last 30 days']].map(([v, t]) => h('option', { value: v }, t))), svg('i-chevron-down', 'ico select-chev'))),
        row('Written to', h('span', { class: 'set-value dim', id: 'expWritten' }, xfer.written || 'Nothing exported yet.')),
        h('div', { class: 'panel-actions' }, h('button', { class: 'btn btn-primary', type: 'button', onclick: async () => {
          const fmt = panel.querySelector('input[name=expFmt]:checked').value, res = await API.exportRun({ game, format: fmt, scope: 'account', dates: $('expDates').value });
          await saveOrDownload(res); if (res && res.path) xfer.written = res.path; else if (res && res.name) xfer.written = `${res.name} (${A.fmt(res.count)} pulls, downloaded by the browser)`;
          $('expWritten').textContent = xfer.written || 'Nothing exported yet.';
        } }, 'Export'))),
      note('The UIGF v4.0 layout still has to be checked against the specification and its validator before files are shared with other tools.'));
  }

  /* ---- Data sources (S10) ---------------------------------------------------------------------------------------------------------- */
  const pill = (kind, text) => h('span', { class: `pill ${kind}` }, text);
  function table(head, rows, cls) {
    return h('div', { class: 'table-wrap' }, h('table', { class: `dtable ${cls || ''}` }, h('thead', null, h('tr', null, head.map((t) => h('th', { scope: 'col' }, t)))),
      h('tbody', null, rows.map((r) => h('tr', null, r.map((c) => h('td', { class: c && c.dim ? 'dim' : null }, c && c.dim ? c.text : c)))))));
  }
  async function renderSources() {
    const panel = $('mp-data'), src = await API.sources(), S = core.settings, B = src.banners;
    const at = B && B.at ? Date.parse(B.at) : NaN, updated = Number.isFinite(at) ? fmtT(at, A.state.game) : '-', age = Number.isFinite(at) ? ago(A.now() - at) : '-';
    const test = (id) => h('button', { class: 'btn btn-s', type: 'button', onclick: async () => { const r = await API.sourceTest(id); A.toast(r && r.unavailable ? 'Testing a source works in the desktop application.' : (r && r.ok ? 'The source answered.' : 'The source did not answer.')); } }, 'Test');
    const gameRows = GAMES.map((k) => { const i = B && B.games && B.games[k]; return [{ text: `${R[k].title}: ${i ? `${i.banners} current banner${i.banners === 1 ? '' : 's'}` : '-'}`, dim: true }, i ? (i.status === 'ok' ? pill('ok', 'OK') : pill('warn', i.keptPrevious ? 'Error, kept older data' : 'Error')) : pill('off', 'No data'), updated, age, '']; });
    const local = { server: S.server || src.server || '', beta: !!S.beta, hoyolab: !!S.hoyolab };
    const cookie = h('input', { class: 'in', type: 'password', id: 'srcCookie', autocomplete: 'off', disabled: !local.hoyolab, 'aria-describedby': 'srcCookieHint' });
    panel.replaceChildren(
      group('Banner data', table(['Source', 'Status', 'Updated', 'Age', ''], [
        [`Public calendar API (${(B && B.source ? B.source : local.server || 'not set').replace(/^https?:\/\//, '')})`, B ? pill('ok', 'Used') : pill('warn', 'Not loaded'), updated, age, test('calendar')], ...gameRows,
        ['Last-good local file (js/banners.js)', B ? pill('ok', 'OK') : pill('warn', 'Missing'), updated, age, ''],
        ['Your own server', local.server && S.server ? pill('ok', 'Set') : pill('off', 'Not set'), '-', '-', S.server ? test('own') : '']]),
        row('Server address', textIn(local.server, (v) => { local.server = v; }, 'https://', 'Server address')), note('Change the address to use your own server. The application keeps working from the last-good file when a server does not answer.')),
      group('Images and text', table(['Host', 'Used for'], src.images.map(([a, b]) => [a, { text: b, dim: true }]))),
      group('Options',
        h('label', { class: 'set-row set-check' }, h('input', { type: 'checkbox', checked: local.beta, onchange: (e) => { local.beta = e.target.checked; } }), h('span', null, 'Show beta data (content found in builds ahead of the live game)')),
        h('label', { class: 'set-row set-check' }, h('input', { type: 'checkbox', checked: local.hoyolab, onchange: (e) => { local.hoyolab = e.target.checked; cookie.disabled = !e.target.checked; if (e.target.checked) cookie.focus(); } }), h('span', null, 'Use my HoYoLAB account (advanced)')),
        row('Cookie', cookie), h('p', { class: 'set-note', id: 'srcCookieHint' }, 'Treat the cookie like a password. It is stored in the system keyring and never shown, logged or sent anywhere else.'),
        h('div', { class: 'panel-actions' }, h('button', { class: 'btn btn-primary', type: 'button', onclick: async () => {
          const s = clone(core.settings); Object.assign(s, { server: local.server.trim() === (src.server || '') ? '' : local.server.trim(), beta: local.beta, hoyolab: local.hoyolab });
          await API.saveSettings(s);
          if (local.hoyolab && cookie.value) { const r = await API.saveCookie(cookie.value); cookie.value = ''; if (r && r.unavailable) A.toast('Saved the options. Storing the cookie works in the desktop application.'); else A.toast('Saved. The cookie is stored in the system keyring.'); } else A.toast('Saved.');
          await U.refreshCore(); draft = null;
        } }, 'Save options'))));
  }

  /* What js/window.js asks before the application closes. */
  window.PT_PANELS = { syncRunning: () => syncState.state === 'running', settingsDirty: () => dirty && tab === 'settings' };

  /* ---- Start ---------------------------------------------------------------------------------------------------------------------------- */
  if (TABS[A.qs.get('modal')]) openModal(A.qs.get('modal'), { auto: A.qs.get('auto') === '1' });   // ?modal=settings|sync|transfer|data
})();
