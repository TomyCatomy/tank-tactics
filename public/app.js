/* Tank Tactics client. Plain JS, no build step.
 * The server is the source of truth: this file only renders the per-viewer
 * state the server sends and posts intents back. Never trust anything here. */
'use strict';
(() => {
  // ---------------------------------------------------------------------------
  // Tiny DOM helpers (no innerHTML with user data, ever)
  // ---------------------------------------------------------------------------
  const $ = (sel, root = document) => root.querySelector(sel);
  const SVGNS = 'http://www.w3.org/2000/svg';

  function build(el, attrs, kids) {
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k.startsWith('on') && typeof v === 'function') el.addEventListener(k.slice(2), v);
      else if (k === 'class') el.setAttribute('class', v);
      else if (k === 'style' && typeof v === 'object') Object.assign(el.style, v);
      else if (k in el && !(el instanceof SVGElement) && typeof v !== 'string') el[k] = v;
      else el.setAttribute(k, v === true ? '' : String(v));
    }
    for (const kid of kids.flat(Infinity)) {
      if (kid === null || kid === undefined || kid === false) continue;
      el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
    }
    return el;
  }
  const h = (tag, attrs, ...kids) => build(document.createElement(tag), attrs, kids);
  const sv = (tag, attrs, ...kids) => build(document.createElementNS(SVGNS, tag), attrs, kids);
  /** Like el.replaceChildren(), but skips null/false (replaceChildren would render them as text). */
  const fill = (el, ...kids) => { el.replaceChildren(); return build(el, {}, kids); };

  // ---------------------------------------------------------------------------
  // Local storage (per-device convenience only; tokens can be re-shared via rejoin link)
  // ---------------------------------------------------------------------------
  const saved = {
    all() {
      try { return JSON.parse(localStorage.getItem('tt.games') || '{}'); } catch { return {}; }
    },
    get(gid) { return this.all()[gid] || null; },
    set(gid, v) {
      try {
        const all = this.all();
        all[gid] = { ...v, savedAt: Date.now() };
        localStorage.setItem('tt.games', JSON.stringify(all));
      } catch { /* storage unavailable: session still works until reload */ }
    },
    del(gid) {
      try { const all = this.all(); delete all[gid]; localStorage.setItem('tt.games', JSON.stringify(all)); } catch { }
    },
    // Debug "local seats": several players of one game controlled from this browser.
    // The active seat is the entry's own token/playerId/name; `seats` holds all of them.
    seats(gid) {
      const v = this.get(gid);
      if (!v?.token) return [];
      const seats = { ...(v.seats || {}) };
      if (v.playerId) seats[v.playerId] = { token: v.token, name: v.name };
      return Object.entries(seats).map(([playerId, s]) => ({ playerId, ...s }));
    },
    addSeat(gid, seat) {
      const v = this.get(gid) || {};
      const seats = { ...(v.seats || {}) };
      if (v.playerId) seats[v.playerId] = { token: v.token, name: v.name };
      seats[seat.playerId] = { token: seat.token, name: seat.name };
      this.set(gid, { ...v, seats });
    },
    /**
     * Forget the seat with this token (idempotent: the WebSocket "you're out" message and the
     * Leave button can both report the same departure). If it was the active seat, another
     * local seat becomes active. Returns true if any seat for this game remains.
     */
    dropSeat(gid, token) {
      const v = this.get(gid);
      if (!v) return false;
      const rest = this.seats(gid).filter((x) => x.token !== token);
      if (!rest.length) { this.del(gid); return false; }
      const seats = Object.fromEntries(rest.map((x) => [x.playerId, { token: x.token, name: x.name }]));
      const active = rest.find((x) => x.token === v.token) || rest[0];
      this.set(gid, { ...v, seats, token: active.token, playerId: active.playerId, name: active.name });
      return true;
    },
    useSeat(gid, playerId) {
      const v = this.get(gid);
      const seat = this.seats(gid).find((x) => x.playerId === playerId);
      if (v && seat) this.set(gid, { ...v, token: seat.token, playerId, name: seat.name });
    },
  };

  // ---------------------------------------------------------------------------
  // API
  // ---------------------------------------------------------------------------
  async function api(method, path, body, token) {
    const headers = {};
    if (body !== undefined) headers['Content-Type'] = 'application/json';
    if (token) headers.Authorization = `Bearer ${token}`;
    let res;
    try {
      res = await fetch(path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    } catch {
      throw Object.assign(new Error('Network error. Is the server running?'), { code: 'network' });
    }
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const e = data.error || {};
      throw Object.assign(new Error(e.message || `Request failed (${res.status})`), { code: e.code, status: res.status });
    }
    return data;
  }

  // ---------------------------------------------------------------------------
  // Account (optional "Sign in with Google")
  // ---------------------------------------------------------------------------
  // The session token only identifies the account (to list and claim seats, and to
  // link new seats). Game requests keep using the per-seat tokens in `saved`.
  const account = {
    get() {
      try { return JSON.parse(localStorage.getItem('tt.session') || 'null'); } catch { return null; }
    },
    set(v) { try { localStorage.setItem('tt.session', JSON.stringify(v)); } catch { } },
    clear() { try { localStorage.removeItem('tt.session'); } catch { } },
    token() { return this.get()?.token || null; },
  };

  /** api() with the session token. An expired session signs you out and rethrows. */
  async function accountApi(method, path, body) {
    try {
      return await api(method, path, body, account.token());
    } catch (e) {
      if (e.status === 401 && account.token()) {
        account.clear();
        e.message = 'Your sign-in has expired. Sign in again.';
      }
      throw e;
    }
  }

  let authConfigPromise = null;
  function authConfig() {
    authConfigPromise ||= api('GET', '/api/auth/config').catch(() => { authConfigPromise = null; return { google_client_id: null }; });
    return authConfigPromise;
  }

  let gisPromise = null;
  function loadGoogleScript() {
    gisPromise ||= new Promise((resolve, reject) => {
      const s = h('script', { src: 'https://accounts.google.com/gsi/client', async: true });
      s.onload = () => resolve(window.google);
      s.onerror = () => { gisPromise = null; reject(new Error('Could not load Google sign-in.')); };
      document.head.append(s);
    });
    return gisPromise;
  }

  /** Render Google's sign-in button into `el`; on success store the session and re-render. */
  async function mountGoogleButton(el, clientId) {
    try {
      const google = await loadGoogleScript();
      google.accounts.id.initialize({
        client_id: clientId,
        callback: async (resp) => {
          try {
            const res = await api('POST', '/api/auth/google', { credential: resp.credential });
            account.set({ token: res.token, user: res.user });
            toast(`Signed in as ${res.user.name}`, 'good');
            route();
          } catch (e) { toast(e.message, 'error'); }
        },
      });
      // The standard dark pill, in English like the rest of the site. `medium` matters: at `large`, Google swaps in its
      // personalized "Sign in as <name>" button whenever the browser has a Google session.
      google.accounts.id.renderButton(el, { type: 'standard', theme: 'filled_black', shape: 'pill', size: 'medium', text: 'signin_with', locale: 'en' });
    } catch (e) { fill(el, h('span', { class: 'muted small' }, e.message)); }
  }

  async function signOut() {
    const token = account.token();
    account.clear();
    try { window.google?.accounts.id.disableAutoSelect(); } catch { }
    if (token) await api('POST', '/api/auth/logout', undefined, token).catch(() => { });
    route();
  }

  /** Get a seat token for the account's seat in `gid` and remember it on this device. */
  async function claimSeat(gid, name) {
    const res = await accountApi('POST', `/api/me/games/${encodeURIComponent(gid)}/claim`);
    saved.set(gid, { token: res.token, playerId: res.player_id, name });
    return res;
  }

  // ---------------------------------------------------------------------------
  // Toasts & notifications
  // ---------------------------------------------------------------------------
  function toast(text, kind = 'info', ms = 4000) {
    const el = h('div', { class: `toast ${kind}` }, text);
    $('#toasts').append(el);
    setTimeout(() => el.remove(), ms);
  }

  function notify(text, kind) {
    toast(text, kind);
    try {
      if ('Notification' in window && Notification.permission === 'granted' && document.hidden) {
        new Notification('Tank Tactics', { body: text, tag: 'tt-' + Date.now() });
      }
    } catch { /* some mobile browsers only allow notifications from a service worker */ }
  }

  // ---------------------------------------------------------------------------
  // Formatting
  // ---------------------------------------------------------------------------
  function fmtDur(sec) {
    sec = Math.max(0, Math.floor(sec));
    const d = Math.floor(sec / 86400), hh = Math.floor((sec % 86400) / 3600);
    const m = Math.floor((sec % 3600) / 60), s = sec % 60;
    if (d) return `${d}d ${hh}h`;
    if (hh) return `${hh}h ${m}m`;
    if (m) return `${m}m ${s}s`;
    return `${s}s`;
  }
  function fmtTime(ts) {
    const d = new Date(ts * 1000);
    const today = new Date().toDateString() === d.toDateString();
    return today
      ? d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
      : d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
  }
  const dist = (a, b) => Math.max(Math.abs(a.x - b.x), Math.abs(a.y - b.y));
  const initials = (name) => name.split(/\s+/).map((w) => w[0]).join('').slice(0, 2).toUpperCase() || '?';

  // ---------------------------------------------------------------------------
  // Config form
  // ---------------------------------------------------------------------------
  const FIELDS = [
    { k: 'fog_of_war', label: 'Fog of war', type: 'select', main: true, wide: true,
      options: [['off', 'Off: everything public'], ['alliance', 'Alliance: hide AP counts & gifts']] },
    { k: 'ap.schedule', type: 'schedule', main: true }, // writes ap.rrule + ap.timezone (see scheduleEditor)
    { k: 'jury.enabled', label: 'Jury of dead players (vote for +1 AP)', type: 'bool', main: true },
    { k: 'jury.schedule', type: 'jury-note', main: true },
    { k: 'debug.fast_ticks', label: 'Debug: fast ticks (for testing)', type: 'bool', main: true },
    { k: 'debug.local_seats', label: 'Debug: play several players from one page', type: 'bool', main: true },
    { k: 'debug.tick_seconds', label: 'Debug tick length (seconds)', type: 'int' },
    { k: 'board.size', label: 'Board size', type: 'int', nullable: true, hint: 'Blank = auto from player count' },
    { k: 'board.min_size', label: 'Auto board: minimum size', type: 'int' },
    { k: 'board.size_multiplier', label: 'Auto board: multiplier', type: 'int', hint: 'ceil(sqrt(players) × this)' },
    { k: 'start.hp', label: 'Starting HP', type: 'int' },
    { k: 'start.ap', label: 'Starting AP', type: 'int' },
    { k: 'start.action_range', label: 'Starting range', type: 'int' },
    { k: 'ap.cap', label: 'AP cap', type: 'int', nullable: true, hint: 'Blank = no cap' },
    { k: 'actions.range_upgrade_cost', label: 'Range upgrade cost (AP)', type: 'int' },
    { k: 'actions.max_range', label: 'Max range', type: 'int', nullable: true, hint: 'Blank = unlimited' },
    { k: 'actions.allow_give_hp', label: 'Allow giving HP', type: 'bool' },
    { k: 'actions.loot_on_kill', label: 'Killer loots victim’s AP', type: 'bool' },
    { k: 'board.wreckage_blocks_tile', label: 'Wrecks block tiles', type: 'bool' },
    { k: 'jury.tie_rule', label: 'Jury tie rule', type: 'select', options: [['all', 'All tied get +1'], ['random', 'One random tied player']] },
    { k: 'jury.votes_public', label: 'Jury votes are public', type: 'bool' },
    { k: 'max_duration_seconds', label: 'Time limit (seconds)', type: 'int', nullable: true, hint: 'Blank = none' },
    { k: 'players.minimum', label: 'Min players', type: 'int' },
    { k: 'players.maximum', label: 'Max players', type: 'int' },
    { k: 'notifications.discord_webhook_url', label: 'Discord webhook (public events only)', type: 'text', nullable: true, hint: 'Optional' },
  ];

  // Config fields are dotted paths into the nested config, e.g. 'jury.enabled'.
  const getPath = (obj, path) => path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
  function setPath(obj, path, value) {
    const keys = path.split('.'), last = keys.pop();
    let o = obj;
    for (const k of keys) o = (o[k] ??= {});
    o[last] = value;
  }

  function configForm(values, { editable = true, onDirty, allowDebug = false } = {}) {
    // Hidden fields are also left out of getValues, so saving keeps their current value.
    const fields = allowDebug ? FIELDS : FIELDS.filter((f) => !f.k.startsWith('debug.'));
    const inputs = {};
    const schedule = scheduleEditor(values.ap, { editable, debug: values.debug });
    const juryNote = h('div', { class: 'wide jury-note muted small', role: 'note' });
    const field = (f) => {
      if (f.type === 'schedule') return schedule;
      if (f.type === 'jury-note') return juryNote;
      const v = getPath(values, f.k);
      let input;
      if (f.type === 'bool') {
        input = h('input', { type: 'checkbox', checked: !!v, disabled: !editable });
        inputs[f.k] = input;
        return h('label', { class: 'check' }, input, h('span', {}, f.label));
      }
      if (f.type === 'select') {
        input = h('select', { disabled: !editable }, f.options.map(([val, lab]) => h('option', { value: val, selected: v === val }, lab)));
      } else {
        input = h('input', {
          type: f.type === 'int' ? 'number' : 'text', inputmode: f.type === 'int' ? 'numeric' : null,
          value: v === null || v === undefined ? '' : String(v), disabled: !editable,
          placeholder: f.nullable ? (f.hint || '') : '',
        });
      }
      inputs[f.k] = input;
      // `wide` fields span the whole row, for options too long for one grid column.
      return h('label', { class: f.wide ? 'wide' : null }, h('span', {}, f.label), input, f.hint && !f.nullable ? h('div', { class: 'hint' }, f.hint) : null);
    };
    const main = h('div', { class: 'config-grid' }, fields.filter((f) => f.main).map(field));
    const adv = h('details', { class: 'adv' }, h('summary', {}, 'Advanced rules'),
      h('div', { class: 'config-grid' }, fields.filter((f) => !f.main).map(field)));
    const root = h('div', { class: 'stack' }, main, adv);
    if (onDirty) root.addEventListener('input', onDirty);

    // The jury closes a cycle on every AP tick, so its note repeats the schedule summary.
    const juryOn = () => !!inputs['jury.enabled']?.checked;
    const showJuryNote = () => {
      juryNote.hidden = !juryOn();
      fill(juryNote, 'Jury cycles close on the same schedule as AP: ', h('b', {}, schedule.summaryText()));
    };
    schedule.onSummary = showJuryNote;
    inputs['jury.enabled']?.addEventListener('change', showJuryNote);
    showJuryNote();
    // Debug fast ticks replace the schedule; say so next to it.
    const debugChanged = () => schedule.setDebug({
      fast_ticks: inputs['debug.fast_ticks'] ? inputs['debug.fast_ticks'].checked : values.debug?.fast_ticks,
      tick_seconds: inputs['debug.tick_seconds'] ? Number(inputs['debug.tick_seconds'].value) : values.debug?.tick_seconds,
    });
    inputs['debug.fast_ticks']?.addEventListener('change', debugChanged);
    inputs['debug.tick_seconds']?.addEventListener('input', debugChanged);

    root.getValues = () => {
      const out = {};
      for (const f of fields) {
        if (f.type === 'schedule') { Object.assign((out.ap ??= {}), schedule.getValues()); continue; }
        if (f.type === 'jury-note') continue;
        const el = inputs[f.k];
        let v;
        if (f.type === 'bool') v = el.checked;
        else if (f.type === 'select') v = el.value;
        else if (el.value.trim() === '') v = f.nullable ? null : el.value;
        else v = f.type === 'int' ? Number(el.value) : el.value.trim();
        setPath(out, f.k, v);
      }
      return out;
    };
    return root;
  }

  // ---------------------------------------------------------------------------
  // AP schedule editor (RFC 5545 RRULE + IANA time zone)
  // ---------------------------------------------------------------------------
  // Three layers over one rule string, like a calendar's "Custom recurrence":
  //  1. presets (derived from today's date in the chosen zone, plus a time of day),
  //  2. "Custom…": a structured builder (repeat every N hours/days/weeks/months, weekday
  //     chips, monthly by day-of-month or nth/last weekday, time of day),
  //  3. "Advanced": the raw RRULE, validated live by the server.
  // The rule string is the source of truth. A rule a preset or the builder can express
  // is shown there; anything else shows as "Custom rule" and is never rewritten unless
  // the host changes it. Rules are parsed and written with rrule.js (public/vendor); the
  // server validates, summarises and previews them (POST /api/schedule/preview).
  const RRule = window.rrule.RRule;
  const WEEKDAYS = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'];
  const WD_LONG = { MO: 'Monday', TU: 'Tuesday', WE: 'Wednesday', TH: 'Thursday', FR: 'Friday', SA: 'Saturday', SU: 'Sunday' };
  const WD_CHIP = { MO: 'M', TU: 'T', WE: 'W', TH: 'T', FR: 'F', SA: 'S', SU: 'S' };
  const ORDINAL = { 1: 'first', 2: 'second', 3: 'third', 4: 'fourth', '-1': 'last' };
  const ORD_SHORT = { 1: '1st', 2: '2nd', 3: '3rd', 4: '4th', '-1': 'last' };
  const UNIT_FREQ = { hour: RRule.HOURLY, day: RRule.DAILY, week: RRule.WEEKLY, month: RRule.MONTHLY };
  // The schedule must fire every calendar month (the server checks): these are the longest steps that always do.
  const MAX_EVERY = { hour: 672, day: 28, week: 4, month: 1 };
  const FREQ_UNIT = Object.fromEntries(Object.entries(UNIT_FREQ).map(([unit, freq]) => [freq, unit]));
  const pad2 = (n) => String(n).padStart(2, '0');
  const dayOrdinal = (d) => `${d}${(d % 100 >= 11 && d % 100 <= 13) ? 'th' : ({ 1: 'st', 2: 'nd', 3: 'rd' }[d % 10] || 'th')}`;

  let defaultsPromise = null;
  /** GET /api/defaults once per page (default config, allow_debug, accepted time zones). */
  function serverDefaults() {
    defaultsPromise ||= api('GET', '/api/defaults').catch((e) => { defaultsPromise = null; throw e; });
    return defaultsPromise;
  }

  /** The browser's IANA zone, or null if it has none. */
  function browserZone() {
    try { return Intl.DateTimeFormat().resolvedOptions().timeZone || null; } catch { return null; }
  }

  /** RRULE body -> rrule.js options (only the parts it gives), or null if it doesn't parse or has no FREQ. */
  function parseRule(text) {
    try {
      const o = RRule.parseString(String(text ?? '').trim().toUpperCase()); // RRULE is case-insensitive; rrule.js isn't
      return o.freq === undefined ? null : o;
    } catch { return null; }
  }

  /** The builder's model for parsed rule options, or null if the builder can't express it exactly. */
  function ruleToModel(o) {
    if (!o) return null;
    const allowed = ['freq', 'interval', 'byhour', 'byminute', 'bysecond', 'byweekday', 'bymonthday', 'bysetpos'];
    if (Object.keys(o).some((k) => !allowed.includes(k))) return null;
    const list = (k) => (o[k] == null ? [] : [].concat(o[k]));
    const one = (k) => (list(k).length === 1 ? list(k)[0] : null);
    const days = list('byweekday'); // rrule.js Weekday objects: {weekday: 0 (MO) .. 6 (SU), n}
    const unit = FREQ_UNIT[o.freq], every = o.interval ?? 1;
    if (!unit || every < 1 || every > MAX_EVERY[unit]) return null;
    if (list('bysecond').some((x) => x !== 0)) return null;
    const m = { unit, every, days: [], monthMode: 'day', monthDay: null, nth: null, weekday: null, hour: 0, minute: one('byminute') };
    if (m.minute === null) return null; // the builder always sets the minute
    if (unit === 'hour') return list('byhour').length || days.length || list('bymonthday').length || list('bysetpos').length ? null : m;
    m.hour = one('byhour');
    if (m.hour === null) return null;
    if (unit === 'day') return days.length || list('bymonthday').length || list('bysetpos').length ? null : m;
    if (unit === 'week') {
      if (!days.length || days.some((d) => d.n) || list('bymonthday').length || list('bysetpos').length) return null;
      m.days = WEEKDAYS.filter((_, i) => days.some((d) => d.weekday === i));
      return m.days.length === days.length ? m : null;
    }
    if (list('bymonthday').length) {
      const d = one('bymonthday');
      if (days.length || list('bysetpos').length || d === null) return null;
      if (d === -1) return { ...m, monthMode: 'lastday' };
      if (d < 1 || d > 28) return null; // 29-31 skip months: shown as a custom rule (and refused by the server)
      return { ...m, monthMode: 'day', monthDay: d };
    }
    if (days.length !== 1 || (days[0].n && list('bysetpos').length)) return null;
    const n = days[0].n || one('bysetpos');
    if (![1, 2, 3, 4, -1].includes(n)) return null;
    return { ...m, monthMode: n === -1 ? 'last' : 'nth', nth: n, weekday: WEEKDAYS[days[0].weekday] };
  }

  /** The builder's model -> its RRULE (one canonical spelling). */
  function modelToRule(m) {
    const o = { freq: UNIT_FREQ[m.unit] };
    if (m.every > 1) o.interval = m.every;
    if (m.unit === 'week') o.byweekday = m.days.map((d) => RRule[d]);
    if (m.unit === 'month') {
      if (m.monthMode === 'day') o.bymonthday = m.monthDay;
      else if (m.monthMode === 'lastday') o.bymonthday = -1;
      else { o.byweekday = [RRule[m.weekday]]; o.bysetpos = m.monthMode === 'last' ? -1 : m.nth; }
    }
    if (m.unit !== 'hour') o.byhour = m.hour;
    o.byminute = m.minute;
    return RRule.optionsToString(o).replace(/^RRULE:/, '');
  }

  /** Today's weekday, day of month and position in the month, in `tz`. */
  function todayIn(tz) {
    let parts;
    try {
      parts = new Intl.DateTimeFormat('en-US', { timeZone: tz, weekday: 'short', day: 'numeric', month: 'numeric', year: 'numeric' }).formatToParts(new Date());
    } catch {
      parts = new Intl.DateTimeFormat('en-US', { weekday: 'short', day: 'numeric', month: 'numeric', year: 'numeric' }).formatToParts(new Date());
    }
    const get = (t) => parts.find((x) => x.type === t)?.value;
    const weekday = { Mon: 'MO', Tue: 'TU', Wed: 'WE', Thu: 'TH', Fri: 'FR', Sat: 'SA', Sun: 'SU' }[get('weekday')] || 'MO';
    const day = Number(get('day')), month = Number(get('month')), year = Number(get('year'));
    const inMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
    return { weekday, day, nth: Math.ceil(day / 7), isLast: day + 7 > inMonth, isLastDay: day === inMonth };
  }

  /** Which preset (if any) shows this rule. */
  function presetOf(options, model) {
    if (options && Object.keys(options).length === 1 && options.freq === RRule.DAILY) return 'start';
    if (!model) return 'rule';
    if (model.unit === 'hour') return 'hours';
    if (model.every !== 1) return 'custom';
    if (model.unit === 'day') return 'daily';
    if (model.unit === 'week') return model.days.join() === 'MO,TU,WE,TH,FR' ? 'weekdays' : model.days.length === 1 ? 'weekly' : 'custom';
    return { day: 'monthday', lastday: 'monthlastday', nth: 'monthnth', last: 'monthlast' }[model.monthMode];
  }

  /** Time zones grouped by region, so long names don't overflow a phone-width dropdown. */
  function zoneOptions(zones, current) {
    const groups = new Map(), loose = [];
    for (const z of zones) {
      const i = z.indexOf('/');
      if (i < 0) { loose.push(z); continue; }
      const region = z.slice(0, i);
      if (!groups.has(region)) groups.set(region, []);
      groups.get(region).push(z);
    }
    const opt = (z, text) => h('option', { value: z, selected: z === current }, text.replaceAll('_', ' '));
    return [
      ...loose.map((z) => opt(z, z)),
      ...[...groups].map(([region, list]) => h('optgroup', { label: region }, list.map((z) => opt(z, z.slice(region.length + 1))))),
    ];
  }

  /**
   * The schedule part of the config form. `ap` is the config's ap section. Returns an element
   * with getValues() -> {rrule, timezone}, summaryText(), setDebug(debug) and an onSummary hook.
   */
  function scheduleEditor(ap, { editable = true, debug = null } = {}) {
    const st = {
      rule: ap?.rrule || 'FREQ=DAILY', tz: ap?.timezone || 'UTC', summary: '', debug,
      // Context for preset labels and builder defaults: today in the zone, overridden by the rule's own values.
      ctx: null, model: null, preset: null, hoursEvery: 6,
      lastTime: { hour: 9, minute: 0 }, // the time of day to reuse when switching back from an hourly schedule
    };
    const uid = `sched-${Math.random().toString(36).slice(2, 8)}`;
    const dis = !editable;
    const changed = () => root.dispatchEvent(new Event('input', { bubbles: true })); // marks the form dirty

    // -- layer 1: presets + time of day
    const presetSel = h('select', { id: `${uid}-preset`, disabled: dis, 'aria-describedby': `${uid}-summary` });
    // No `required`/min/max checks by the browser: a hidden invalid field would silently block the create form.
    const timeIn = h('input', { type: 'time', id: `${uid}-time`, step: 60, disabled: dis });
    const timeLabel = h('label', { class: 'sched-inline', for: `${uid}-time` }, h('span', {}, 'at'), timeIn);
    const hoursIn = h('input', { type: 'number', min: 1, max: MAX_EVERY.hour, inputmode: 'numeric', disabled: dis, 'aria-label': 'Hours between ticks' });
    const minuteIn = h('input', { type: 'number', min: 0, max: 59, inputmode: 'numeric', disabled: dis, 'aria-label': 'At minute' });
    const hoursRow = h('div', { class: 'sched-inline' }, h('span', {}, 'every'), hoursIn, h('span', {}, 'hours at minute'), minuteIn);

    // -- layer 2: builder
    const everyIn = h('input', { type: 'number', min: 1, max: MAX_EVERY.week, inputmode: 'numeric', disabled: dis, id: `${uid}-every` });
    const unitSel = h('select', { disabled: dis, 'aria-label': 'Repeat unit' },
      ['hour', 'day', 'week', 'month'].map((u) => h('option', { value: u }, `${u}(s)`)));
    const chips = WEEKDAYS.map((d) => h('button', {
      type: 'button', class: 'chip', disabled: dis, 'aria-pressed': 'false', 'aria-label': WD_LONG[d], title: WD_LONG[d], 'data-day': d,
      onclick: () => {
        const days = new Set(st.model.days);
        if (days.has(d)) { if (days.size > 1) days.delete(d); } else days.add(d); // keep at least one day
        st.model.days = WEEKDAYS.filter((x) => days.has(x));
        fromBuilder();
      },
    }, WD_CHIP[d]));
    const chipRow = h('div', { class: 'chips', role: 'group', 'aria-label': 'Repeat on' }, chips);
    const radio = (mode) => h('input', { type: 'radio', name: `${uid}-month`, value: mode, disabled: dis });
    const monthRadios = { day: radio('day'), lastday: radio('lastday'), nth: radio('nth'), last: radio('last') };
    // Days 29-31 would skip months; "the last day" covers the end of every month.
    const monthDayIn = h('input', { type: 'number', min: 1, max: 28, inputmode: 'numeric', disabled: dis, 'aria-label': 'Day of the month' });
    const nthText = h('span'), lastText = h('span');
    const monthBox = h('fieldset', { class: 'sched-month' }, h('legend', {}, 'Monthly on'),
      h('label', { class: 'radio' }, monthRadios.day, h('span', {}, 'Day'), monthDayIn),
      h('label', { class: 'radio' }, monthRadios.lastday, h('span', {}, 'The last day')),
      h('label', { class: 'radio' }, monthRadios.nth, nthText),
      h('label', { class: 'radio' }, monthRadios.last, lastText),
      h('div', { class: 'hint' }, 'Days 29–31 and a fifth weekday aren’t in every month, so they aren’t offered: use the last day or the last weekday.'));
    const bTimeIn = h('input', { type: 'time', step: 60, disabled: dis, id: `${uid}-btime` });
    const bTimeRow = h('label', { class: 'sched-inline', for: `${uid}-btime` }, h('span', {}, 'at'), bTimeIn);
    const bMinuteIn = h('input', { type: 'number', min: 0, max: 59, inputmode: 'numeric', disabled: dis, id: `${uid}-bmin` });
    const bMinuteRow = h('label', { class: 'sched-inline', for: `${uid}-bmin` }, h('span', {}, 'at minute'), bMinuteIn);
    const builder = h('fieldset', { class: 'sched-builder' }, h('legend', {}, 'Custom schedule'),
      h('div', { class: 'sched-inline' }, h('label', { for: `${uid}-every` }, 'Repeat every'), everyIn, unitSel),
      chipRow, monthBox, bTimeRow, bMinuteRow);

    // -- layer 3: raw RRULE
    const rawIn = h('input', {
      type: 'text', id: `${uid}-raw`, class: 'mono', disabled: dis, spellcheck: 'false', autocapitalize: 'characters',
      autocomplete: 'off', 'aria-describedby': `${uid}-err`, placeholder: 'FREQ=DAILY;BYHOUR=9;BYMINUTE=0',
    });
    const rawErr = h('div', { class: 'error-text small', id: `${uid}-err`, 'aria-live': 'polite' });
    const advanced = h('details', { class: 'sched-adv' }, h('summary', {}, 'Advanced: RRULE'),
      h('label', { for: `${uid}-raw` }, h('span', {}, 'Recurrence rule (RFC 5545 RRULE, without DTSTART, COUNT or UNTIL)')),
      rawIn, rawErr,
      h('div', { class: 'hint' }, 'The schedule starts when the game starts and runs until it ends. Ticks are on whole minutes, at most one a minute, and at least one every calendar month.'));

    // -- shared: time zone and summary
    const tzSel = h('select', { id: `${uid}-tz`, disabled: dis }, h('option', { value: st.tz, selected: true }, st.tz));
    const summaryEl = h('p', { class: 'sched-summary', id: `${uid}-summary`, 'aria-live': 'polite' });
    const debugEl = h('p', { class: 'notice small' });

    const root = h('div', { class: 'wide sched stack', role: 'group', 'aria-labelledby': `${uid}-label` },
      h('label', { id: `${uid}-label`, for: `${uid}-preset`, class: 'sched-title' }, h('span', {}, 'AP schedule: +1 AP for every living tank')),
      h('div', { class: 'sched-row' }, presetSel, timeLabel, hoursRow),
      builder, advanced,
      h('label', { for: `${uid}-tz`, class: 'sched-tz' }, h('span', {}, 'Time zone'), tzSel),
      h('div', { class: 'sched-preview' }, summaryEl, debugEl));

    // -- state -> controls
    const timeOf = (m) => `${pad2(m?.hour ?? 9)}:${pad2(m?.minute ?? 0)}`;
    const readTime = (input, m) => {
      const [hh, mm] = (input.value || '09:00').split(':').map(Number);
      m.hour = Number.isFinite(hh) ? hh : 9;
      m.minute = Number.isFinite(mm) ? mm : 0;
    };
    const clampInt = (input, lo, hi, dflt) => {
      const n = Math.trunc(Number(input.value));
      const v = Number.isFinite(n) && input.value !== '' ? Math.min(hi, Math.max(lo, n)) : dflt;
      input.value = String(v); // show what was used (and never leave an out-of-range value in the form)
      return v;
    };

    function presetOptions() {
      const c = st.ctx, wd = WD_LONG[c.weekday];
      const opts = [
        ['start', 'Daily at the start time'],
        ['daily', 'Every day'],
        ['weekdays', 'Every weekday (Mon–Fri)'],
        ['weekly', `Weekly on ${wd}`],
      ];
      // Never offer a day that some months don't have: from the 29th on, offer the last day instead.
      if (c.day <= 28) opts.push(['monthday', `Monthly on the ${dayOrdinal(c.day)}`]);
      if (c.isLastDay || st.preset === 'monthlastday') opts.push(['monthlastday', 'Monthly on the last day']);
      if (c.nth <= 4) opts.push(['monthnth', `Monthly, ${ORD_SHORT[c.nth]} ${wd}`]);
      if (c.isLast || st.preset === 'monthlast') opts.push(['monthlast', `Monthly, last ${wd}`]);
      opts.push(['hours', 'Every few hours'], ['custom', 'Custom…']);
      if (st.preset === 'rule') opts.push(['rule', 'Custom rule']);
      return opts;
    }

    /** Re-derive every control from st.rule (after a raw edit, or on load). */
    function syncFromRule() {
      const options = parseRule(st.rule);
      st.model = ruleToModel(options);
      st.preset = presetOf(options, st.model);
      const today = todayIn(st.tz), m = st.model;
      st.ctx = { ...today };
      if (m?.unit === 'week' && m.days.length === 1) st.ctx.weekday = m.days[0];
      if (m?.unit === 'month' && m.monthMode === 'day') st.ctx.day = m.monthDay;
      if (m?.unit === 'month' && m.monthMode === 'lastday') st.ctx.isLastDay = true;
      if (m?.unit === 'month' && (m.monthMode === 'nth' || m.monthMode === 'last')) {
        st.ctx.weekday = m.weekday;
        if (m.monthMode === 'nth') st.ctx.nth = m.nth;
        else st.ctx.isLast = true;
      }
      if (m?.unit === 'hour') st.hoursEvery = m.every;
      if (m && m.unit !== 'hour') st.lastTime = { hour: m.hour, minute: m.minute };
      if (document.activeElement !== rawIn) rawIn.value = st.rule;
      render();
    }

    function render() {
      fill(presetSel, presetOptions().map(([v, label]) => h('option', { value: v, selected: v === st.preset }, label)));
      const m = st.model;
      const timed = ['daily', 'weekdays', 'weekly', 'monthday', 'monthlastday', 'monthnth', 'monthlast'].includes(st.preset);
      timeLabel.hidden = !timed;
      hoursRow.hidden = st.preset !== 'hours';
      if (timed) timeIn.value = timeOf(m);
      if (st.preset === 'hours') { hoursIn.value = String(st.hoursEvery); minuteIn.value = String(m?.minute ?? 0); }
      // The builder shows for "Custom…" (a rule it can express that no preset matches, or the host picked it).
      builder.hidden = st.preset !== 'custom';
      if (st.preset === 'custom' && m) {
        everyIn.value = String(m.every);
        everyIn.max = String(MAX_EVERY[m.unit]);
        everyIn.disabled = dis || m.unit === 'month'; // monthly is every month, or it would skip some
        unitSel.value = m.unit;
        chipRow.hidden = m.unit !== 'week';
        for (const c of chips) c.setAttribute('aria-pressed', String(m.days.includes(c.dataset.day)));
        monthBox.hidden = m.unit !== 'month';
        const wd = WD_LONG[m.weekday || st.ctx.weekday];
        nthText.textContent = `The ${ORDINAL[m.nth > 0 ? m.nth : Math.min(4, st.ctx.nth)]} ${wd}`;
        lastText.textContent = `The last ${wd}`;
        monthRadios[m.monthMode].checked = true;
        monthDayIn.value = String(m.monthDay ?? Math.min(28, st.ctx.day));
        bTimeRow.hidden = m.unit === 'hour';
        bMinuteRow.hidden = m.unit !== 'hour';
        bTimeIn.value = timeOf(m);
        bMinuteIn.value = String(m.minute);
      }
      // A rule nothing else can show: open the raw view so the host sees what it is.
      if (st.preset === 'rule') advanced.open = true;
      renderDebug();
      schedulePreview();
    }

    function renderDebug() {
      const d = st.debug;
      debugEl.hidden = !d?.fast_ticks;
      if (d?.fast_ticks) debugEl.textContent = `Debug fast ticks are on: AP (and the jury) tick every ${d.tick_seconds || 10} s instead of on this schedule.`;
    }

    /** Set a new rule from a preset or the builder (never from the raw field). */
    function setRule(rule, { keepControls = false } = {}) {
      st.rule = rule;
      rawIn.value = rule;
      rawErr.textContent = '';
      rawIn.removeAttribute('aria-invalid');
      if (keepControls) { schedulePreview(); } else syncFromRule();
      changed();
    }

    // A model for a preset, from the context and the current time of day.
    function presetModel(id) {
      const c = st.ctx, base = { every: 1, days: [], monthMode: 'day', monthDay: null, nth: null, weekday: null, ...st.lastTime };
      if (!timeLabel.hidden && timeIn.value) readTime(timeIn, base);
      switch (id) {
        case 'daily': return { ...base, unit: 'day' };
        case 'weekdays': return { ...base, unit: 'week', days: ['MO', 'TU', 'WE', 'TH', 'FR'] };
        case 'weekly': return { ...base, unit: 'week', days: [c.weekday] };
        case 'monthday': return { ...base, unit: 'month', monthMode: 'day', monthDay: Math.min(28, c.day) };
        case 'monthlastday': return { ...base, unit: 'month', monthMode: 'lastday' };
        case 'monthnth': return { ...base, unit: 'month', monthMode: 'nth', nth: Math.min(4, c.nth), weekday: c.weekday };
        case 'monthlast': return { ...base, unit: 'month', monthMode: 'last', nth: -1, weekday: c.weekday };
        case 'hours': return { ...base, unit: 'hour', every: st.hoursEvery, minute: st.model?.unit === 'hour' ? st.model.minute : 0 };
        default: return null;
      }
    }

    presetSel.addEventListener('change', () => {
      const id = presetSel.value;
      if (id === 'start') return setRule('FREQ=DAILY');
      if (id === 'rule') return; // keep the custom rule as it is
      if (id === 'custom') {
        // Open the builder on the current rule if it fits, else on "weekly on today's weekday".
        st.preset = 'custom';
        if (!st.model) { st.model = presetModel('weekly'); setRule(modelToRule(st.model), { keepControls: true }); }
        render();
        return;
      }
      const m = presetModel(id);
      st.model = m;
      setRule(modelToRule(m));
    });
    timeIn.addEventListener('change', () => {
      if (!st.model || timeLabel.hidden) return;
      readTime(timeIn, st.model);
      setRule(modelToRule(st.model));
    });
    const hoursChanged = () => {
      st.hoursEvery = clampInt(hoursIn, 1, MAX_EVERY.hour, st.hoursEvery);
      st.model = { ...presetModel('hours'), every: st.hoursEvery, minute: clampInt(minuteIn, 0, 59, 0) };
      setRule(modelToRule(st.model), { keepControls: true });
    };
    hoursIn.addEventListener('change', hoursChanged);
    minuteIn.addEventListener('change', hoursChanged);

    /** Builder edits: rebuild the rule, keep the builder open (presets don't take over). */
    function fromBuilder() {
      const m = st.model;
      st.preset = 'custom';
      if (m.unit !== 'hour') st.lastTime = { hour: m.hour, minute: m.minute };
      setRule(modelToRule(m), { keepControls: true });
      render();
    }
    everyIn.addEventListener('change', () => { st.model.every = clampInt(everyIn, 1, MAX_EVERY[st.model.unit], 1); fromBuilder(); });
    unitSel.addEventListener('change', () => {
      const m = st.model, unit = unitSel.value;
      m.unit = unit;
      m.every = Math.min(m.every, MAX_EVERY[unit]);
      if (unit === 'week' && !m.days.length) m.days = [st.ctx.weekday];
      if (unit === 'month' && m.monthMode === 'day' && !m.monthDay) m.monthDay = Math.min(28, st.ctx.day);
      if (unit === 'month' && m.monthMode !== 'day' && !m.weekday) m.weekday = st.ctx.weekday;
      fromBuilder();
    });
    for (const [mode, input] of Object.entries(monthRadios)) {
      input.addEventListener('change', () => {
        const m = st.model;
        m.monthMode = mode;
        if (mode === 'day') m.monthDay = clampInt(monthDayIn, 1, 28, Math.min(28, st.ctx.day));
        else if (mode === 'lastday') { /* BYMONTHDAY=-1 */ }
        else { m.weekday ||= st.ctx.weekday; m.nth = mode === 'last' ? -1 : (m.nth > 0 ? m.nth : Math.min(4, st.ctx.nth)); }
        fromBuilder();
      });
    }
    monthDayIn.addEventListener('change', () => { st.model.monthMode = 'day'; st.model.monthDay = clampInt(monthDayIn, 1, 28, Math.min(28, st.ctx.day)); fromBuilder(); });
    bTimeIn.addEventListener('change', () => { readTime(bTimeIn, st.model); fromBuilder(); });
    bMinuteIn.addEventListener('change', () => { st.model.minute = clampInt(bMinuteIn, 0, 59, 0); fromBuilder(); });

    // Raw edits: debounced; the rule string is kept exactly as typed.
    let rawTimer = null;
    rawIn.addEventListener('input', () => {
      clearTimeout(rawTimer);
      rawTimer = setTimeout(() => { st.rule = rawIn.value.trim(); syncFromRule(); }, 300);
    });
    rawIn.addEventListener('change', () => { clearTimeout(rawTimer); st.rule = rawIn.value.trim(); syncFromRule(); });

    tzSel.addEventListener('change', () => { st.tz = tzSel.value; syncFromRule(); changed(); });

    // -- preview (server-side; debounced, latest request wins)
    let previewTimer = null, previewSeq = 0;
    function schedulePreview() {
      clearTimeout(previewTimer);
      previewTimer = setTimeout(runPreview, 250);
    }
    async function runPreview() {
      const seq = ++previewSeq, rule = st.rule, tz = st.tz;
      let res;
      try {
        res = await api('POST', '/api/schedule/preview', { rrule: rule, timezone: tz });
      } catch (e) {
        if (seq !== previewSeq) return;
        st.summary = 'Invalid schedule';
        summaryEl.textContent = e.code === 'invalid_schedule' ? `Invalid schedule: ${e.message}` : e.message;
        summaryEl.classList.add('bad');
        if (e.code === 'invalid_schedule') { rawErr.textContent = e.message; rawIn.setAttribute('aria-invalid', 'true'); }
        root.onSummary?.();
        return;
      }
      if (seq !== previewSeq) return;
      st.summary = res.summary;
      summaryEl.classList.remove('bad');
      fill(summaryEl, h('b', {}, res.summary));
      rawErr.textContent = '';
      rawIn.removeAttribute('aria-invalid');
      root.onSummary?.();
    }

    root.getValues = () => ({ rrule: st.rule, timezone: st.tz });
    root.summaryText = () => st.summary || '…';
    root.setDebug = (d) => { st.debug = d; renderDebug(); };
    root.onSummary = null;

    serverDefaults().then(({ timezones }) => {
      if (!timezones?.length) return;
      const list = timezones.includes(st.tz) ? timezones : [st.tz, ...timezones];
      fill(tzSel, zoneOptions(list, st.tz));
    }).catch(() => { });

    syncFromRule();
    return root;
  }

  // ---------------------------------------------------------------------------
  // Router
  // ---------------------------------------------------------------------------
  let G = null; // current game session

  function route() {
    stopGame();
    closeSheet();
    $('#topbar-info').replaceChildren();
    $('#topbar-game').replaceChildren();
    window.scrollTo(0, 0); // a new page starts at the top, not wherever the last one was scrolled to
    const raw = location.hash.slice(1) || '/';
    const [path, query] = raw.split('?');
    const params = new URLSearchParams(query || '');
    const parts = path.split('/').filter(Boolean);
    if (parts[0] === 'join' && parts[1]) return renderJoin(parts[1]);
    if (parts[0] === 'g' && parts[1]) {
      const t = params.get('t');
      if (t) {
        // Rejoin link: store the token, then drop it from the URL bar.
        saved.set(parts[1], { token: t });
        history.replaceState(null, '', `#/g/${parts[1]}`);
      }
      return startGame(parts[1]);
    }
    renderHome();
  }

  // ---------------------------------------------------------------------------
  // Home
  // ---------------------------------------------------------------------------
  async function renderHome() {
    document.title = 'Tank Tactics';
    const app = $('#app');
    app.replaceChildren(h('div', { class: 'spinner' }, 'Loading…'));
    let defaults, allowDebug, auth;
    try {
      let timezones;
      [{ config: defaults, allow_debug: allowDebug, timezones }, auth] = await Promise.all([serverDefaults(), authConfig()]);
      // A new game's schedule starts in this browser's time zone (the server default is UTC).
      defaults = structuredClone(defaults);
      const zone = browserZone();
      if (zone && timezones?.includes(zone)) defaults.ap.timezone = zone;
    } catch (e) {
      app.replaceChildren(h('div', { class: 'card' }, e.message));
      return;
    }
    let remote = [];
    if (auth.google_client_id && account.token()) {
      try { remote = await accountApi('GET', '/api/me/games'); } catch (e) { if (!account.token()) toast(e.message, 'error'); }
    }
    const me = auth.google_client_id ? account.get()?.user : null;
    const nameIn = h('input', { maxlength: 24, placeholder: 'Your display name', autocomplete: 'nickname', value: me?.name?.slice(0, 24) || '' });
    const form = configForm(defaults, { allowDebug });
    const err = h('div', { class: 'error-text' });
    const createBtn = h('button', { class: 'primary', type: 'submit' }, 'Create game');
    const create = h('form', {
      class: 'card home-create',
      onsubmit: async (ev) => {
        ev.preventDefault();
        err.textContent = '';
        createBtn.disabled = true;
        try {
          const res = await accountApi('POST', '/api/games', { name: nameIn.value, config: form.getValues() });
          saved.set(res.game_id, { token: res.token, playerId: res.player_id, name: nameIn.value.trim() });
          location.hash = `#/g/${res.game_id}`;
        } catch (e) { err.textContent = e.message; } finally { createBtn.disabled = false; }
      },
    },
      // Folded by default: most visitors come to join or reopen a game, and the form is long.
      h('details', { class: 'stack create-fold' },
        h('summary', {}, h('h2', {}, 'Create a game')),
        h('p', { class: 'notice' }, 'This game can get personal. Play with people who’ll still be your friends afterwards.'),
        h('label', {}, h('span', { class: 'muted small' }, 'Your name'), nameIn),
        form, h('div', { class: 'home-create-actions' }, err, createBtn)));

    const codeIn = h('input', { placeholder: 'Game code, e.g. k7mq2x', autocapitalize: 'off', autocomplete: 'off' });
    const join = h('form', {
      class: 'card stack home-join',
      onsubmit: (ev) => {
        ev.preventDefault();
        const code = codeIn.value.trim().replace(/^.*\/join\//, '').replace(/[^a-z0-9]/gi, '').toLowerCase();
        if (code) location.hash = `#/join/${code}`;
      },
    }, h('h2', {}, 'Join a game'), h('div', { class: 'row' }, codeIn, h('button', { class: 'shrink', type: 'submit' }, 'Join')));

    // Games saved on this device, then any other seats linked to the signed-in account.
    const local = saved.all();
    const mine = Object.entries(local).sort((a, b) => (b[1].savedAt || 0) - (a[1].savedAt || 0))
      .map(([gid, v]) => ({ gid, name: v.name, status: remote.find((g) => g.game_id === gid)?.status }));
    for (const g of remote) if (!local[g.game_id]) mine.push({ gid: g.game_id, name: g.name, status: g.status, remote: true });
    const open = (g) => async () => {
      if (g.remote) {
        try { await claimSeat(g.gid, g.name); } catch (e) { toast(e.message, 'error'); return; }
      }
      location.hash = `#/g/${g.gid}`;
    };
    const myGames = mine.length ? h('div', { class: 'card home-games' }, h('h2', {}, me ? 'Your games' : 'Your games on this device'),
      h('ul', { class: 'plist' }, mine.map((g) => h('li', { class: 'clickable', onclick: open(g) },
        h('span', { class: 'pname' }, `${g.gid}`, g.name ? h('span', { class: 'muted' }, ` · ${g.name}`) : null,
          g.status ? h('span', { class: 'muted small' }, ` · ${g.status}`) : null),
        h('span', { class: 'pstats' }, 'Open ›'))))) : null;

    let accountCard = null, gButton = null;
    if (auth.google_client_id) {
      if (me) {
        accountCard = h('div', { class: 'card row account' },
          h('span', {}, 'Signed in as ', h('b', {}, me.name)),
          h('button', { class: 'shrink', onclick: signOut }, 'Sign out'));
      } else {
        gButton = h('div', { class: 'gsi-button' });
        accountCard = h('div', { class: 'account account-signin' },
          h('p', { class: 'muted small' }, 'Optional: sign in to keep your games across devices.'), gButton);
      }
    }

    app.replaceChildren(h('div', { class: 'home' },
      h('section', { class: 'hero' }, h('h1', {}, 'Tank Tactics'),
        h('p', {}, 'Everyone gets one tank and one action point a day. Spend AP to move, shoot, upgrade range or gift it to a friend. Make alliances. Break them. Last tank standing wins.')),
      accountCard, myGames, join, create));
    if (gButton) mountGoogleButton(gButton, auth.google_client_id);
  }

  // ---------------------------------------------------------------------------
  // Join
  // ---------------------------------------------------------------------------
  async function renderJoin(gid) {
    const app = $('#app');
    if (saved.get(gid)?.token) { location.hash = `#/g/${gid}`; return; }
    app.replaceChildren(h('div', { class: 'spinner' }, 'Loading…'));
    // Signed in and already seated in this game (e.g. from another device): just open it.
    if (account.token() && (await authConfig()).google_client_id) {
      try { await claimSeat(gid); location.hash = `#/g/${gid}`; return; } catch { /* not seated here */ }
    }
    let info;
    try { info = await api('GET', `/api/games/${encodeURIComponent(gid)}`); } catch (e) {
      app.replaceChildren(h('div', { class: 'card' }, h('h2', {}, 'Game not found'), h('p', {}, e.message), h('a', { href: '#/' }, 'Back')));
      return;
    }
    if (info.status === 'closed') {
      app.replaceChildren(h('div', { class: 'card' }, h('h2', {}, 'This lobby was closed'),
        h('p', { class: 'muted' }, 'The host cancelled this game before it started.'), h('a', { href: '#/' }, 'Back')));
      return;
    }
    if (info.status !== 'lobby') {
      app.replaceChildren(h('div', { class: 'card' }, h('h2', {}, 'This game has already started'),
        h('p', { class: 'muted' }, 'New players can only join before the host starts the game. If you are already in it, open your personal rejoin link.'),
        h('a', { href: '#/' }, 'Back')));
      return;
    }
    const me = account.get()?.user;
    const nameIn = h('input', { maxlength: 24, placeholder: 'Your display name', autocomplete: 'nickname', required: true, value: me?.name?.slice(0, 24) || '' });
    const err = h('div', { class: 'error-text' });
    app.replaceChildren(h('form', {
      class: 'card stack', style: { maxWidth: '480px', margin: '0 auto' },
      onsubmit: async (ev) => {
        ev.preventDefault();
        err.textContent = '';
        try {
          const res = await accountApi('POST', `/api/games/${gid}/join`, { name: nameIn.value });
          saved.set(gid, { token: res.token, playerId: res.player_id, name: nameIn.value.trim() });
          location.hash = `#/g/${gid}`;
        } catch (e) { err.textContent = e.message; }
      },
    },
      h('h2', {}, `Join ${info.host}’s game`),
      h('p', { class: 'muted' }, `${info.player_count} / ${info.max_players} players so far.`),
      h('p', { class: 'notice' }, 'This game can get personal. Play with people who’ll still be your friends afterwards.'),
      nameIn, err, h('button', { class: 'primary', type: 'submit' }, 'Join game')));
    nameIn.focus();
  }

  // ---------------------------------------------------------------------------
  // Game session (WebSocket)
  // ---------------------------------------------------------------------------
  function startGame(gid) {
    const creds = saved.get(gid);
    if (!creds?.token) { location.hash = `#/join/${gid}`; return; }
    G = {
      gid, token: creds.token, state: null, ws: null, retry: 0, stopped: false, offset: 0,
      tab: 'me', selected: null, chat: 'public', logFilter: '', lastLogId: null, lastMsgId: null,
      unread: {}, nodes: null, layout: null, replay: null, configDirty: false, configKey: null,
    };
    $('#app').replaceChildren(h('div', { class: 'spinner' }, 'Connecting…'));
    connect();
    G.timer = setInterval(updateCountdowns, 1000);
  }

  function stopGame() {
    if (!G) return;
    G.stopped = true;
    clearInterval(G.timer);
    clearInterval(G.replay?.timer);
    try { G.ws?.close(); } catch { }
    G = null;
  }

  function connect() {
    const session = G;
    const proto = location.protocol === 'https:' ? 'wss' : 'ws';
    const ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(session.gid)}`);
    session.ws = ws;
    ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token: session.token }));
    ws.onmessage = (ev) => {
      if (session.stopped) return;
      let msg;
      try { msg = JSON.parse(ev.data); } catch { return; }
      if (msg.type === 'state') {
        session.retry = 0;
        session.reconnecting = false;
        onState(msg.state);
        renderConnectionBadge();
      }
      if (msg.type === 'error') {
        session.stopped = true;
        if (saved.dropSeat(session.gid, session.token)) { route(); return; } // continue as another local seat
        fill($('#topbar-game'));
        fill($('#topbar-info'));
        $('#app').replaceChildren(h('div', { class: 'card' }, h('h2', {}, 'Can’t open this game'),
          h('p', {}, msg.error.message), h('a', { href: '#/' }, 'Back to start')));
      }
    };
    ws.onclose = () => {
      if (session.stopped) return;
      const wait = Math.min(15000, 500 * 2 ** session.retry++);
      session.reconnecting = true;
      renderConnectionBadge();
      setTimeout(() => { if (!session.stopped) connect(); }, wait);
    };
  }

  /** Exactly one "reconnecting…" badge while the socket is down, however many retries fail. */
  function renderConnectionBadge() {
    const existing = document.getElementById('conn-badge');
    const show = !!(G && !G.stopped && G.reconnecting);
    if (show && !existing) {
      $('#topbar-info').prepend(h('span', { id: 'conn-badge', class: 'badge warn', role: 'status' }, 'reconnecting…'));
    } else if (!show && existing) {
      existing.remove();
    }
  }

  /** Re-fetch over HTTP (used right after an action so the UI never waits on the socket). */
  async function refresh() {
    if (!G) return;
    try { onState(await api('GET', `/api/games/${G.gid}/state`, undefined, G.token)); } catch { }
  }

  async function post(path, body) {
    try {
      await api('POST', `/api/games/${G.gid}/${path}`, body, G.token);
      return true;
    } catch (e) {
      toast(e.message, 'error');
      return false;
    }
  }

  // ---------------------------------------------------------------------------
  // State handling
  // ---------------------------------------------------------------------------
  const me = () => G.state.players.find((p) => p.id === G.state.viewer_id);
  const byId = (id) => G.state.players.find((p) => p.id === id);
  const nameOf = (id) => byId(id)?.name ?? 'someone';

  function onState(st) {
    if (!G) return;
    const prev = G.state;
    // Ignore stale snapshots (HTTP refresh and socket push can race).
    if (prev && st.log.length && prev.log.length && st.log[st.log.length - 1].id < prev.log[prev.log.length - 1].id) return;
    G.state = st;
    G.offset = st.server_now - Date.now() / 1000;
    const creds = saved.get(G.gid) || {};
    if (!creds.playerId || !creds.name) {
      const mine = st.players.find((p) => p.id === st.viewer_id);
      saved.set(G.gid, { ...creds, token: G.token, playerId: st.viewer_id, name: mine?.name });
    }
    if (prev) {
      notifyNew(prev, st);
      trackUnread(st);
    } else {
      G.lastMsgId = st.messages.length ? st.messages[st.messages.length - 1].id : 0;
    }
    G.lastLogId = st.log.length ? st.log[st.log.length - 1].id : 0;
    render();
  }

  function notifyNew(prev, st) {
    const myId = st.viewer_id;
    const fresh = st.log.filter((e) => e.id > (G.lastLogId ?? Infinity));
    for (const e of fresh) {
      const a = nameOf(e.actor_id), t = nameOf(e.target_id);
      if (e.event_type === 'ap_tick' && e.details.recipients.includes(myId)) notify(`You received +${e.amount} AP.`, 'good');
      else if (e.event_type === 'shoot' && e.target_id === myId) notify(`${a} shot you! You have ${e.details.target_hp_after} HP left.`, 'error');
      else if (e.event_type === 'give_ap' && e.target_id === myId) notify(`${a} gave you ${e.amount} AP.`, 'good');
      else if (e.event_type === 'give_hp' && e.target_id === myId) notify(`${a} gave you ${e.amount} HP.`, 'good');
      else if (e.event_type === 'loot' && e.actor_id === myId) notify(`You looted ${e.amount} AP.`, 'good');
      else if (e.event_type === 'jury_award' && e.details.winners.includes(myId)) notify('The jury awarded you +1 AP.', 'good');
      else if (e.event_type === 'kill') notify(e.target_id === myId ? `${a} destroyed your tank.` : `${a} destroyed ${t}.`, 'error');
      else if (e.event_type === 'removed') notify(`${t} was removed by the host.`);
      else if (e.event_type === 'game_start') notify('The game has started!', 'good');
      else if (e.event_type === 'game_over') {
        const w = e.details.winners.map(nameOf).join(' & ');
        notify(e.details.winners.includes(myId) ? 'You won! 🏆' : `Game over. ${w} won.`, 'good');
      }
    }
  }

  function chanOf(m, myId) { return m.to_id === null ? 'public' : (m.from_id === myId ? m.to_id : m.from_id); }

  // Must match the phone-lobby media query in style.css.
  const phoneLobby = window.matchMedia('(max-width: 899px), (max-height: 500px)');
  function chatOnScreen() {
    if (G.layout === 'lobby') return !phoneLobby.matches || G.lobbyTab === 'chat';
    return G.tab === 'chat';
  }

  function trackUnread(st) {
    const fresh = st.messages.filter((m) => m.id > G.lastMsgId);
    for (const m of fresh) {
      if (m.from_id === st.viewer_id) continue;
      const ch = chanOf(m, st.viewer_id);
      const viewing = chatOnScreen() && G.chat === ch && !document.hidden;
      if (!viewing) G.unread[ch] = (G.unread[ch] || 0) + 1;
      if (ch !== 'public' && !viewing) notify(`DM from ${nameOf(m.from_id)}: ${m.text.slice(0, 80)}`);
    }
    if (st.messages.length) G.lastMsgId = st.messages[st.messages.length - 1].id;
  }

  function updateCountdowns() {
    if (!G?.state) return;
    const now = Date.now() / 1000 + G.offset;
    document.querySelectorAll('[data-until]').forEach((el) => {
      const t = Number(el.dataset.until);
      el.textContent = t - now > 0 ? fmtDur(t - now) : 'any moment';
    });
  }

  // ---------------------------------------------------------------------------
  // Rendering
  // ---------------------------------------------------------------------------
  function render() {
    const st = G.state;
    document.title = `Tank Tactics · ${st.game.id}`;
    renderTopbar();
    if (st.game.status === 'lobby') renderLobby();
    else if (st.game.status === 'closed') renderClosed();
    else renderMatch();
    updateCountdowns();
  }

  function renderTopbar() {
    const g = G.state.game, c = g.config;
    const alive = G.state.players.filter((p) => p.alive).length;
    fill($('#topbar-game'), h('button', {
      class: 'game-code', type: 'button', title: 'Click to copy the game code',
      onclick: async () => {
        try { await navigator.clipboard.writeText(g.id); toast(`Game code ${g.id} copied.`, 'good', 1800); }
        catch { toast(`Game code: ${g.id}`, 'info'); }
      },
    }, h('span', { class: 'game-code-label' }, 'Game'), h('span', { class: 'game-code-id' }, g.id), h('span', { 'aria-hidden': 'true' }, '⧉')));
    const seats = saved.seats(G.gid);
    fill($('#topbar-info'),
      seats.length > 1 ? h('label', { class: 'seat-switch', title: 'Debug: switch which player you control' },
        h('span', {}, 'Playing as'),
        h('select', { onchange: (e) => { saved.useSeat(G.gid, e.target.value); route(); } },
          seats.map((x) => h('option', { value: x.playerId, selected: x.playerId === G.state.viewer_id },
            G.state.players.find((p) => p.id === x.playerId)?.name ?? x.name ?? '?')))) : null,
      g.status === 'active' ? h('span', { class: 'badge good' }, `${alive} alive`) : null,
      g.status === 'lobby' ? h('span', { class: 'badge warn' }, 'Lobby') : null,
      g.status === 'finished' ? h('span', { class: 'badge' }, 'Finished') : null,
      g.status === 'closed' ? h('span', { class: 'badge bad' }, 'Closed') : null,
      c.fog_of_war === 'alliance' ? h('span', { class: 'badge warn', title: 'AP counts and gifts are hidden' }, 'Fog: alliance') : null,
      c.jury.enabled ? h('span', { class: 'badge' }, 'Jury on') : null,
      c.debug.fast_ticks ? h('span', { class: 'badge bad' }, 'Debug ticks') : null,
      c.debug.local_seats ? h('span', { class: 'badge bad', title: 'Players can control several tanks from one browser' }, 'Debug seats') : null,
    );
    renderConnectionBadge(); // the fill above wiped it
  }

  // ----- Lobby -----------------------------------------------------------------

  function renderLobby() {
    const st = G.state, my = me(), isHost = !!my?.is_host;
    if (G.layout !== 'lobby') {
      G.layout = 'lobby';
      G.configKey = null;
      G.nodes = {
        players: h('div'),
        config: h('div'),
        start: h('div', { class: 'lobby-start' }),
        intro: h('p', { class: 'muted' }),
        seats: h('div'),
        chat: chatPanel(),
        lobbyTabs: h('div', { class: 'tabs lobby-tabs', role: 'tablist' }),
      };
      const inviteUrl = `${location.origin}/#/join/${st.game.id}`;
      const rejoinUrl = `${location.origin}/#/g/${st.game.id}?t=${encodeURIComponent(G.token)}`;
      // On phones only the active panel shows (see the lobby rules in style.css).
      G.lobbyTab = 'overview';
      G.nodes.lobbyPanels = {
        overview: h('div', { class: 'card stack lobby-panel lobby-overview' },
          h('h2', {}, 'Lobby'),
          G.nodes.intro,
          copyRow('Invite link', inviteUrl),
          copyRow('Your personal rejoin link (keep it secret: it controls your tank)', rejoinUrl),
          G.nodes.start),
        players: h('div', { class: 'stack lobby-panel lobby-players' },
          G.nodes.seats,
          h('div', { class: 'card' }, G.nodes.players)),
        chat: h('div', { class: 'card lobby-panel lobby-chat' }, h('h2', {}, 'Chat'), G.nodes.chat),
        settings: h('div', { class: 'card stack lobby-panel lobby-settings' }, h('h2', {}, 'Game settings'), G.nodes.config),
      };
      const panels = G.nodes.lobbyPanels;
      $('#app').replaceChildren(h('div', { class: 'lobby' },
        G.nodes.lobbyTabs,
        h('div', { class: 'stack lobby-col' }, panels.overview, panels.players, panels.chat),
        panels.settings));
    }

    // Players
    const mySeats = new Set(saved.seats(G.gid).map((x) => x.playerId));
    G.nodes.players.replaceChildren(h('h2', {}, `Players (${st.players.length}/${st.game.config.players.maximum})`),
      h('ul', { class: 'plist' }, st.players.map((p) => h('li', {},
        h('span', { class: 'dot', style: { background: p.color } }),
        h('span', { class: 'pname' }, p.name, p.id === my?.id ? ' (you)' : '', p.is_host ? ' · host' : ''),
        p.id !== my?.id && mySeats.has(p.id) ? h('button', { class: 'small', onclick: () => { saved.useSeat(G.gid, p.id); route(); } }, 'Play as') : null,
        isHost && !p.is_host ? h('button', {
          class: 'small danger', onclick: async () => {
            if (confirm(`Remove ${p.name} from the lobby?`)) { await post('remove', { player_id: p.id }); refresh(); }
          },
        }, 'Remove') : null))));

    // Start
    const need = Math.max(2, st.game.config.players.minimum);
    G.nodes.intro.textContent = `Share the invite link. The host starts the game once everyone is in (at least ${need} players).`;
    renderSeatControls(need);
    G.nodes.start.replaceChildren(isHost
      ? h('div', { class: 'stack' },
        h('button', {
          class: 'primary', disabled: st.players.length < need,
          onclick: async () => {
            if (G.configDirty) { toast('Save or discard your settings changes first.', 'error'); return; }
            if (await post('start')) refresh();
          },
        }, `Start game (${st.players.length}/${need}+ players)`),
        h('button', {
          class: 'danger', onclick: async () => {
            if (confirm('Close this lobby? The game is cancelled for everyone and the invite link stops working.')) {
              if (await post('close')) refresh();
            }
          },
        }, 'Close lobby'))
      : h('div', { class: 'stack' },
        h('p', { class: 'muted' }, 'Waiting for the host to start the game…'),
        h('button', {
          class: 'danger', onclick: async () => {
            if (!confirm('Leave this lobby? You can rejoin with the invite link while it is open.')) return;
            const { gid, token } = G; // capture first: the socket's "you're out" message may re-route meanwhile
            if (!(await post('leave'))) return;
            if (saved.dropSeat(gid, token)) route(); // still have another local seat here
            else location.hash = '#/';
          },
        }, 'Leave lobby')));

    // Config (rebuilt only if it changed and the host isn't mid-edit)
    const key = JSON.stringify([st.game.config, st.allow_debug]);
    if (key !== G.configKey && !G.configDirty) {
      G.configKey = key;
      const form = configForm(st.game.config, { editable: isHost, allowDebug: st.allow_debug, onDirty: () => { G.configDirty = true; } });
      const kids = [form];
      if (isHost) {
        kids.push(h('div', { class: 'row config-actions' },
          h('button', {
            class: 'primary', onclick: async () => {
              if (await post('config', { config: form.getValues() })) {
                G.configDirty = false; G.configKey = null; toast('Settings saved.', 'good'); refresh();
              }
            },
          }, 'Save settings'),
          h('button', { onclick: () => { G.configDirty = false; G.configKey = null; render(); } }, 'Discard changes')));
      }
      G.nodes.config.replaceChildren(...kids);
    }
    renderChat();
  }

  function renderLobbyTabs() {
    const unread = Object.values(G.unread).reduce((sum, n) => sum + n, 0);
    const tabs = [['overview', 'Lobby'], ['players', `Players (${G.state.players.length})`], ['chat', 'Chat'], ['settings', 'Settings']];
    G.nodes.lobbyTabs.replaceChildren(...tabs.map(([k, label]) => h('button', {
      type: 'button', role: 'tab', 'aria-selected': String(G.lobbyTab === k),
      onclick: () => {
        G.lobbyTab = k;
        renderChat(); // clears unread if the chat just came into view, and redraws these tabs
        if (k === 'chat') {
          const list = G.nodes.chat._parts.list;
          list.scrollTop = list.scrollHeight; // it couldn't scroll while hidden
        }
      },
    }, label, k === 'chat' && unread ? h('span', { class: 'count' }, String(unread)) : null)));
    for (const [k, panel] of Object.entries(G.nodes.lobbyPanels)) panel.classList.toggle('active', G.lobbyTab === k);
  }

  /** Debug: join extra players from this browser (only when the game enables debug.local_seats). */
  function renderSeatControls(need) {
    const st = G.state;
    if (!st.game.config.debug.local_seats) { fill(G.nodes.seats); return; }
    const full = st.players.length >= st.game.config.players.maximum;
    const names = new Set(st.players.map((p) => p.name.toLowerCase()));
    const nextName = () => {
      for (let n = names.size + 1; ; n++) {
        if (!names.has(`player ${n}`)) { names.add(`player ${n}`); return `Player ${n}`; }
      }
    };
    const run = (count) => async (ev) => {
      ev.currentTarget.disabled = true;
      try {
        for (let i = 0; i < count; i++) {
          const name = nextName();
          const res = await api('POST', `/api/games/${G.gid}/join`, { name });
          saved.addSeat(G.gid, { playerId: res.player_id, token: res.token, name });
        }
      } catch (e) { toast(e.message, 'error'); }
      refresh();
    };
    const missing = Math.max(0, need - st.players.length);
    fill(G.nodes.seats, h('div', { class: 'card stack' },
      h('h3', {}, 'Debug: local players'),
      h('p', { class: 'muted small' }, 'Add players controlled from this browser, then switch between them with \u201cPlaying as\u201d in the top bar.'),
      h('div', { class: 'row' },
        h('button', { disabled: full, onclick: run(1) }, 'Add player'),
        missing > 0 ? h('button', { disabled: full, onclick: run(missing) }, `Fill to minimum (+${missing})`) : null)));
  }

  function renderClosed() {
    G.layout = 'closed';
    fill($('#app'), h('div', { class: 'card stack', style: { maxWidth: '520px', margin: '0 auto' } },
      h('h2', {}, 'This lobby was closed'),
      h('p', { class: 'muted' }, 'The host cancelled this game before it started.'),
      h('a', { href: '#/' }, 'Back to start')));
  }

  function copyRow(label, value) {
    const input = h('input', { readonly: true, value });
    return h('label', {}, h('span', { class: 'muted small' }, label), h('div', { class: 'copy-row' }, input,
      h('button', {
        class: 'shrink', type: 'button', onclick: async () => {
          try { await navigator.clipboard.writeText(value); toast('Copied.', 'good', 1500); } catch { input.select(); }
        },
      }, 'Copy')));
  }

  // ----- Match (active or finished) ------------------------------------------------

  function tabsFor() {
    const tabs = [['me', 'Me'], ['players', 'Players'], ['log', 'Log'], ['chat', 'Chat']];
    if (G.state.game.config.jury.enabled) tabs.push(['jury', 'Jury']);
    return tabs;
  }

  function renderMatch() {
    const st = G.state;
    if (G.layout !== 'match') {
      G.layout = 'match';
      const my = me();
      if (my && !my.alive && st.game.config.jury.enabled && st.game.status === 'active') G.tab = 'jury';
      G.nodes = {
        banner: h('div'),
        over: h('div'),
        board: h('div', { class: 'board-wrap' }),
        tabs: h('div', { class: 'tabs', role: 'tablist' }),
        body: h('div', { class: 'tab-body' }),
        chat: chatPanel(),
        logFilter: h('select', { onchange: (e) => { G.logFilter = e.target.value; renderTab(); } }),
        logList: h('ul', { class: 'log' }),
      };
      $('#app').replaceChildren(h('div', { class: 'game' },
        h('div', {}, G.nodes.over, G.nodes.banner, G.nodes.board),
        h('aside', { class: 'side' }, G.nodes.tabs, G.nodes.body)));
    }
    renderBanner();
    renderGameOver();
    renderBoard();
    renderTabs();
    renderTab();
  }

  function renderBanner() {
    const st = G.state, my = me(), b = G.nodes.banner;
    if (!my) { b.replaceChildren(h('div', { class: 'banner' }, 'You are watching this game.')); return; }
    if (st.game.status === 'active' && !my.alive) {
      b.replaceChildren(h('div', { class: 'banner dead' },
        h('b', {}, 'Your tank was destroyed. '),
        st.game.config.jury.enabled
          ? 'You are now a juror: vote each cycle to give a living player +1 AP (see the Jury tab).'
          : 'You are a spectator: you can watch the board and log, and use the public chat.'));
      return;
    }
    b.replaceChildren();
  }

  // ----- Board -------------------------------------------------------------------

  function boardModel() {
    // Live state, or a replay frame when the replay slider is active.
    if (G.replay?.frame) return G.replay.frame;
    return { size: G.state.game.board_size, players: G.state.players };
  }

  function renderBoard() {
    const st = G.state, model = boardModel(), N = model.size, my = me();
    const live = !G.replay?.frame;
    const wreckBlocks = st.game.config.board.wreckage_blocks_tile;
    const players = model.players;
    const onBoard = players.filter((p) => p.x >= 0 && (p.alive || wreckBlocks));
    const sel = live ? (byId(G.selected) || (my?.alive ? my : null)) : null;

    const svg = sv('svg', { class: 'board', viewBox: `-0.6 -0.6 ${N + 0.7} ${N + 0.7}`, role: 'img', 'aria-label': `Board ${N} by ${N}` });
    const layer = (cls) => sv('g', { class: cls });
    const tiles = layer('tiles'), hl = layer('hl'), tanks = layer('tanks'), hits = layer('hits');

    for (let y = 0; y < N; y++) {
      for (let x = 0; x < N; x++) {
        tiles.append(sv('rect', { class: (x + y) % 2 ? 'tile alt' : 'tile', x: x + 0.03, y: y + 0.03, width: 0.94, height: 0.94, rx: 0.08 }));
      }
    }
    for (let i = 0; i < N; i++) {
      tiles.append(sv('text', { class: 'coord', x: i + 0.5, y: -0.18, 'text-anchor': 'middle' }, i));
      tiles.append(sv('text', { class: 'coord', x: -0.3, y: i + 0.6, 'text-anchor': 'middle' }, i));
    }

    // Range highlight of the selected tank; move targets for my own tank.
    if (sel?.alive) {
      const r = sel.action_range, mine = sel.id === my?.id;
      const x0 = Math.max(0, sel.x - r), x1 = Math.min(N - 1, sel.x + r);
      const y0 = Math.max(0, sel.y - r), y1 = Math.min(N - 1, sel.y + r);
      hl.append(sv('rect', { class: mine ? 'range' : 'range other', x: x0, y: y0, width: x1 - x0 + 1, height: y1 - y0 + 1, rx: 0.1 }));
      if (mine && st.game.status === 'active') {
        for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
          const x = sel.x + dx, y = sel.y + dy;
          if ((dx || dy) && x >= 0 && y >= 0 && x < N && y < N && !onBoard.some((p) => p.x === x && p.y === y)) {
            hl.append(sv('rect', { class: 'move-target', x: x + 0.12, y: y + 0.12, width: 0.76, height: 0.76, rx: 0.12 }));
          }
        }
      }
    }

    for (const p of onBoard) {
      const cx = p.x + 0.5, cy = p.y + 0.5;
      const g = sv('g', { class: 'tank' });
      if (!p.alive) {
        g.append(sv('path', { class: 'wreck', d: `M${p.x + 0.25} ${p.y + 0.25}L${p.x + 0.75} ${p.y + 0.75}M${p.x + 0.75} ${p.y + 0.25}L${p.x + 0.25} ${p.y + 0.75}` }));
        tanks.append(g);
        continue;
      }
      g.append(sv('title', {}, `${p.name}: ${p.hp} HP, range ${p.action_range}${p.ap !== undefined ? `, ${p.ap} AP` : ''}`));
      g.append(sv('rect', { x: p.x + 0.14, y: p.y + 0.16, width: 0.72, height: 0.5, rx: 0.12, fill: p.color }));
      g.append(sv('text', { class: 'tank-label', x: cx, y: p.y + 0.52, 'text-anchor': 'middle' }, initials(p.name)));
      // HP pips
      const pips = Math.max(p.hp, st.game.config.start.hp);
      const w = Math.min(0.14, 0.7 / pips);
      for (let i = 0; i < pips && i < 10; i++) {
        g.append(sv('rect', { class: i < p.hp ? 'hp-pip' : 'hp-pip empty', x: cx - (Math.min(pips, 10) * w) / 2 + i * w + 0.01, y: p.y + 0.72, width: w - 0.02, height: 0.1, rx: 0.02 }));
      }
      if (p.hp > 10) g.append(sv('text', { class: 'ap-tag', x: cx, y: p.y + 0.95, 'text-anchor': 'middle' }, `${p.hp}♥`));
      g.append(sv('text', { class: 'ap-tag', x: p.x + 0.1, y: p.y + 0.14 }, `R${p.action_range}`));
      if (p.ap !== undefined) g.append(sv('text', { class: 'ap-tag', x: p.x + 0.9, y: p.y + 0.14, 'text-anchor': 'end' }, `${p.ap}⚡`));
      if (live && p.id === my?.id) g.append(sv('rect', { class: 'me-ring', x: p.x + 0.06, y: p.y + 0.06, width: 0.88, height: 0.88, rx: 0.12 }));
      if (live && sel && p.id === sel.id && p.id !== my?.id) g.append(sv('rect', { class: 'sel-ring', x: p.x + 0.06, y: p.y + 0.06, width: 0.88, height: 0.88, rx: 0.12 }));
      tanks.append(g);
    }

    // One transparent hit target per tile: simple, touch friendly, no hover needed.
    if (live) {
      hits.append(sv('rect', { class: 'tile-hit', x: 0, y: 0, width: N, height: N }));
      svg.addEventListener('click', (ev) => {
        const pt = svg.createSVGPoint();
        pt.x = ev.clientX; pt.y = ev.clientY;
        const loc = pt.matrixTransform(svg.getScreenCTM().inverse());
        const x = Math.floor(loc.x), y = Math.floor(loc.y);
        if (x < 0 || y < 0 || x >= N || y >= N) return;
        onTileTap(x, y);
      });
    }
    svg.append(tiles, hl, tanks, hits);

    const legend = h('div', { class: 'board-legend' },
      h('span', {}, h('i', { style: { background: 'var(--range)', outline: '1px solid var(--accent)' } }), 'your range'),
      h('span', {}, h('i', { style: { background: 'var(--move)' } }), 'tap to move (1 AP)'),
      h('span', {}, h('i', { style: { background: 'var(--range-other)' } }), 'selected tank’s range'),
      h('span', {}, 'R = range · ⚡ = AP · red pips = HP'));
    G.nodes.board.replaceChildren(svg, legend);
  }

  function onTileTap(x, y) {
    const st = G.state, my = me();
    const p = st.players.find((q) => q.alive && q.x === x && q.y === y);
    if (p) {
      G.selected = p.id;
      renderBoard();
      if (p.id === my?.id) openSelfSheet(); else openTargetSheet(p.id);
      return;
    }
    const wreck = st.game.config.board.wreckage_blocks_tile && st.players.some((q) => !q.alive && q.x === x && q.y === y);
    if (my?.alive && st.game.status === 'active' && !wreck && dist(my, { x, y }) === 1) {
      openMoveSheet(x, y);
      return;
    }
    G.selected = null;
    renderBoard();
  }

  // ----- Action sheets -------------------------------------------------------------

  function closeSheet() { $('#sheet-root')?.replaceChildren(); document.removeEventListener('keydown', escClose); }
  function escClose(e) { if (e.key === 'Escape') closeSheet(); }
  function openSheet(...content) {
    const sheet = h('div', { class: 'sheet', role: 'dialog', 'aria-modal': 'true' }, ...content,
      h('div', { style: { marginTop: '12px' } }, h('button', { class: 'ghost', style: { width: '100%' }, onclick: closeSheet }, 'Close')));
    const bd = h('div', { class: 'sheet-backdrop', onclick: (e) => { if (e.target === bd) closeSheet(); } }, sheet);
    $('#sheet-root').replaceChildren(bd);
    document.addEventListener('keydown', escClose);
    sheet.querySelector('button:not(:disabled), input')?.focus();
  }

  async function doAction(body) {
    if (await post('action', body)) { closeSheet(); refresh(); }
  }

  /** Why an action is unavailable, or null if it looks fine (the server re-checks everything). */
  function blockers({ cost = 0, target = null, needRange = false } = {}) {
    const st = G.state, my = me();
    if (st.game.status !== 'active') return st.game.status === 'lobby' ? 'Game not started' : 'Game over';
    if (!my) return 'You are not in this game';
    if (!my.alive) return 'Your tank is destroyed';
    if (target && !target.alive) return 'Target is destroyed';
    if (needRange && target && dist(my, target) > my.action_range) return `Out of range (distance ${dist(my, target)}, your range ${my.action_range})`;
    if (cost && my.ap < cost) return `Needs ${cost} AP (you have ${my.ap})`;
    return null;
  }

  function actRow(label, cost, why, onclick, extra) {
    return h('div', { class: 'act' },
      h('div', {}, h('b', {}, label), ' ', h('span', { class: 'muted small' }, cost)),
      h('div', { class: 'row' }, extra || null, h('button', { class: label === 'Shoot' ? 'danger' : 'primary', disabled: !!why, onclick }, label)),
      why ? h('div', { class: 'why' }, why) : null);
  }

  function openMoveSheet(x, y) {
    const my = me(), why = blockers({ cost: 1 });
    openSheet(h('h2', {}, `Move to (${x}, ${y})`),
      h('p', { class: 'muted' }, `From (${my.x}, ${my.y}). Moves cost 1 AP each.`),
      actRow('Move', '1 AP', why, () => doAction({ type: 'move', to: [x, y] })));
  }

  function openSelfSheet() {
    const my = me(), c = G.state.game.config;
    const capped = c.actions.max_range !== null && my.action_range >= c.actions.max_range;
    const why = capped ? `Already at max range (${c.actions.max_range})` : blockers({ cost: c.actions.range_upgrade_cost });
    openSheet(h('h2', {}, h('span', { class: 'dot', style: { background: my.color } }), `${my.name} (you)`),
      h('p', { class: 'muted' }, `HP ${my.hp} · AP ${my.ap} · Range ${my.action_range}. Tap a highlighted tile next to you to move.`),
      actRow('Upgrade range', `${c.actions.range_upgrade_cost} AP → range ${my.action_range + 1}`, why, () => doAction({ type: 'upgrade' })));
  }

  function openTargetSheet(id) {
    const st = G.state, t = byId(id), my = me(), c = st.game.config;
    if (!t) return;
    const d = my && my.x >= 0 ? dist(my, t) : null;
    const apIn = h('input', { class: 'amount', type: 'number', min: 1, value: 1, inputmode: 'numeric', 'aria-label': 'AP amount' });
    const hpIn = h('input', { class: 'amount', type: 'number', min: 1, value: 1, inputmode: 'numeric', 'aria-label': 'HP amount' });
    const giveHpWhy = !c.actions.allow_give_hp ? 'Disabled in this game'
      : blockers({ target: t, needRange: true }) || (my && my.hp <= 1 ? 'You must keep at least 1 HP' : null);
    openSheet(
      h('h2', {}, h('span', { class: 'dot', style: { background: t.color } }), t.name),
      h('p', { class: 'muted' }, `HP ${t.hp} · Range ${t.action_range}`, t.ap !== undefined ? ` · AP ${t.ap}` : '', d !== null ? ` · distance ${d}` : '',
        my && d !== null && d <= t.action_range ? ' · you are in their range' : ''),
      actRow('Shoot', '1 AP · 1 damage', blockers({ cost: 1, target: t, needRange: true }), () => doAction({ type: 'shoot', target_id: t.id })),
      actRow('Give AP', 'costs the AP you give', blockers({ cost: 1, target: t, needRange: true }),
        () => doAction({ type: 'give_ap', target_id: t.id, amount: Number(apIn.value) }), apIn),
      actRow('Give HP', 'costs the HP you give', giveHpWhy,
        () => doAction({ type: 'give_hp', target_id: t.id, amount: Number(hpIn.value) }), hpIn),
      my?.alive && t.alive ? h('div', { class: 'act' }, h('div', {}, h('b', {}, 'Private message')),
        h('button', { onclick: () => { closeSheet(); G.chat = t.id; G.tab = 'chat'; renderTabs(); renderTab(); } }, 'Open DM')) : null,
      my?.is_host && st.game.status === 'active' ? h('div', { class: 'act' }, h('div', { class: 'why' }, 'Host: removing a player destroys their tank.'),
        h('button', {
          class: 'danger small', onclick: async () => {
            if (confirm(`Remove ${t.name}? Their tank will be destroyed.`)) { if (await post('remove', { player_id: t.id })) { closeSheet(); refresh(); } }
          },
        }, 'Remove player')) : null);
  }

  // ----- Tabs ------------------------------------------------------------------------

  function renderTabs() {
    const tabs = tabsFor();
    if (!tabs.some(([k]) => k === G.tab)) G.tab = 'me';
    const unread = Object.values(G.unread).reduce((a, b) => a + b, 0);
    G.nodes.tabs.replaceChildren(...tabs.map(([k, label]) => h('button', {
      role: 'tab', 'aria-selected': String(G.tab === k),
      onclick: () => { G.tab = k; renderTabs(); renderTab(); },
    }, label, k === 'chat' && unread ? h('span', { class: 'count' }, unread) : null)));
  }

  function renderTab() {
    const body = G.nodes.body;
    switch (G.tab) {
      case 'players': body.replaceChildren(playersTab()); break;
      case 'log': body.replaceChildren(logTab()); break;
      case 'chat': body.replaceChildren(G.nodes.chat); renderChat(); break;
      case 'jury': body.replaceChildren(juryTab()); break;
      default: body.replaceChildren(meTab());
    }
    updateCountdowns();
  }

  function meTab() {
    const st = G.state, my = me(), c = st.game.config;
    if (!my) return h('p', {}, 'Spectating.');
    const enemiesInRange = st.players.filter((p) => p.alive && p.id !== my.id && dist(my, p) <= my.action_range);
    const freeAdj = [];
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      const x = my.x + dx, y = my.y + dy;
      if ((dx || dy) && x >= 0 && y >= 0 && x < st.game.board_size && y < st.game.board_size &&
        !st.players.some((p) => p.x === x && p.y === y && (p.alive || c.board.wreckage_blocks_tile))) freeAdj.push(1);
    }
    const capped = c.actions.max_range !== null && my.action_range >= c.actions.max_range;
    const item = (label, cost, why, btn) => h('li', {},
      h('div', {}, h('div', {}, label), h('div', { class: 'why' }, why || '')), h('div', { class: 'row shrink' }, h('span', { class: 'cost' }, cost), btn || null));

    const notifBtn = 'Notification' in window && Notification.permission === 'default'
      ? h('button', { class: 'small', onclick: async () => { try { await Notification.requestPermission(); } catch { } renderTab(); } }, 'Enable notifications')
      : null;

    return h('div', {},
      h('div', { class: 'row', style: { marginBottom: '10px' } },
        h('h2', { style: { margin: 0 } }, h('span', { class: 'dot', style: { background: my.color } }), ' ', my.name),
        notifBtn ? h('div', { class: 'shrink' }, notifBtn) : null),
      h('div', { class: 'stats' },
        h('div', { class: 'stat' }, h('b', {}, my.hp), h('span', {}, 'HP')),
        h('div', { class: 'stat' }, h('b', {}, my.ap), h('span', {}, 'AP')),
        h('div', { class: 'stat' }, h('b', {}, my.action_range), h('span', {}, 'Range'))),
      st.game.status === 'active' && my.alive
        ? h('p', {}, 'Next AP in ', h('b', { 'data-until': st.game.next_ap_tick_at }, '…'),
          st.game.ends_at ? [' · game ends in ', h('b', { 'data-until': st.game.ends_at }, '…')] : null)
        : null,
      st.game.schedule ? h('p', { class: 'muted small sched-line' }, 'AP schedule: ', st.game.schedule.effective_summary) : null,
      my.alive && st.game.status === 'active' ? h('ul', { class: 'actions-list' },
        item('Move', '1 AP', blockers({ cost: 1 }) || (freeAdj.length ? 'Tap a highlighted tile next to your tank.' : 'No free adjacent tiles.')),
        item('Shoot', '1 AP', blockers({ cost: 1 }) || (enemiesInRange.length ? `${enemiesInRange.length} tank(s) in range: tap one.` : 'No tanks in range.')),
        item('Give AP', 'the AP given', blockers({ cost: 1 }) || 'Tap a tank in range.'),
        item('Give HP', 'the HP given', !c.actions.allow_give_hp ? 'Disabled in this game.' : blockers() || (my.hp <= 1 ? 'You must keep at least 1 HP.' : 'Tap a tank in range.')),
        item('Upgrade range', `${c.actions.range_upgrade_cost} AP`, capped ? `At max range (${c.actions.max_range}).` : blockers({ cost: c.actions.range_upgrade_cost }) || `Range ${my.action_range} → ${my.action_range + 1}`,
          h('button', { class: 'small primary', disabled: capped || !!blockers({ cost: c.actions.range_upgrade_cost }), onclick: () => doAction({ type: 'upgrade' }) }, 'Upgrade')))
        : null,
      h('p', { class: 'muted small', style: { marginTop: '12px' } },
        'AP builds up over time; there are no turns. Spend it whenever you like. ',
        c.fog_of_war === 'alliance' ? 'Fog of war is on: only you can see your AP, and gifts are private.' : 'Everything, including AP and gifts, is public.'));
  }

  function playersTab() {
    const st = G.state, my = me(), juryVotes = {};
    for (const v of st.jury?.votes || []) juryVotes[v.candidate_id] = (juryVotes[v.candidate_id] || 0) + 1;
    // Sorted by alive then name; never by AP (it may be hidden).
    const list = [...st.players].sort((a, b) => (b.alive - a.alive) || a.name.localeCompare(b.name));
    return h('ul', { class: 'plist' }, list.map((p) => h('li', {
      class: p.alive ? 'clickable' : 'dead',  // living tanks open their action sheet
      onclick: () => {
        if (!p.alive) return;
        G.selected = p.id; renderBoard();
        if (p.id === my?.id) openSelfSheet(); else openTargetSheet(p.id);
      },
    },
      h('span', { class: 'dot', style: { background: p.color } }),
      h('span', { class: 'pname' }, p.name, p.id === my?.id ? ' (you)' : '',
        p.is_host ? h('span', { class: 'muted small' }, ' · host') : null,
        p.is_juror ? h('span', { class: 'muted small' }, ' · juror') : null,
        !p.alive && !p.is_juror ? h('span', { class: 'muted small' }, ' · destroyed') : null),
      h('span', { class: 'pstats' }, p.alive ? `♥${p.hp} · R${p.action_range}` : '',
        p.ap !== undefined && p.alive ? ` · ⚡${p.ap}` : '',
        juryVotes[p.id] ? ` · ${juryVotes[p.id]} vote(s)` : ''))));
  }

  function describe(e) {
    const a = nameOf(e.actor_id), t = nameOf(e.target_id), d = e.details || {};
    switch (e.event_type) {
      case 'join': return `${d.name} joined.`;
      case 'leave': return d.removed_by ? `${d.name} was removed by the host.` : `${d.name} left the lobby.`;
      case 'lobby_closed': return 'The host closed the lobby.';
      case 'config': return 'The host changed the settings.';
      case 'game_start': return `The game started on a ${d.board_size}×${d.board_size} board.`;
      case 'move': return `${a} moved (${e.from_xy}) → (${e.to_xy}).`;
      case 'shoot': return `${a} shot ${t}. ${t} has ${d.target_hp_after} HP.`;
      case 'kill': return `💥 ${a} destroyed ${t}!`;
      case 'loot': return `${a} looted ${e.amount} AP from ${t}.`;
      case 'give_ap': return `${a} gave ${e.amount} AP to ${t}${d.received !== undefined ? ` (${d.received} fit under the cap)` : ''}.`;
      case 'give_hp': return `${a} gave ${e.amount} HP to ${t}.`;
      case 'upgrade': return `${a} upgraded range to ${e.amount}.`;
      case 'removed': return `${t} was removed by the host; their tank is destroyed.`;
      case 'ap_tick': return d.ticks ? `${d.ticks} AP ticks were applied (+${d.ticks} AP each).` : 'AP tick: every living tank gained 1 AP.';
      case 'jury_vote': return `Juror ${a} voted for ${t}.`;
      case 'jury_award': return `The jury awarded +1 AP to ${d.winners.map(nameOf).join(', ')}.`;
      case 'game_over': return `🏆 Game over: ${d.winners.map(nameOf).join(' & ')} won${d.reason === 'time_limit' ? ' on the time limit' : ''}.`;
      default: return e.event_type;
    }
  }

  function logTab() {
    const st = G.state, sel = G.nodes.logFilter;
    sel.replaceChildren(h('option', { value: '' }, 'All players'),
      ...st.players.map((p) => h('option', { value: p.id, selected: G.logFilter === p.id }, p.name)));
    const entries = st.log.filter((e) => !G.logFilter || e.actor_id === G.logFilter || e.target_id === G.logFilter ||
      (e.event_type === 'ap_tick' && e.details.recipients.includes(G.logFilter)) ||
      (e.event_type === 'jury_award' && e.details.winners.includes(G.logFilter)));
    G.nodes.logList.replaceChildren(...entries.slice().reverse().slice(0, 500).map((e) => h('li', {
      class: e.event_type === 'kill' || e.event_type === 'game_over' ? 'kill' : e.event_type === 'ap_tick' ? 'tick' : '',
    }, h('time', {}, fmtTime(e.ts)), h('div', {}, describe(e),
      e.visibility !== 'public' ? h('div', { class: 'private' }, e.visibility === 'actor_only' ? 'only you can see this' : 'only you and the other player can see this') : null))));
    return h('div', { class: 'stack' }, sel, G.nodes.logList);
  }

  // ----- Chat --------------------------------------------------------------------

  function chatPanel() {
    const channel = h('select', { 'aria-label': 'Channel', onchange: (e) => { G.chat = e.target.value; renderChat(); } });
    const list = h('ul', { class: 'chat-msgs' });
    const input = h('textarea', { rows: 2, maxlength: 1000, placeholder: 'Message…' });
    const send = async () => {
      const text = input.value.trim();
      if (!text) return;
      const ok = await post('chat', { to_id: G.chat === 'public' ? null : G.chat, text });
      if (ok) { input.value = ''; refresh(); }
    };
    input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
    const root = h('div', { class: 'chat' }, channel, list, h('div', { class: 'row' }, input, h('button', { class: 'shrink primary', onclick: send }, 'Send')));
    root._parts = { channel, list, input };
    return root;
  }

  function renderChat() {
    const st = G.state, my = me(), panel = G.nodes.chat, { channel, list, input } = panel._parts;
    // Dead players keep access to the public channel only.
    const canDm = my?.alive;
    const others = canDm ? st.players.filter((p) => p.id !== my.id && p.alive) : [];
    if (G.chat !== 'public' && !others.some((p) => p.id === G.chat)) G.chat = 'public';
    const label = (id, name) => `${name}${G.unread[id] ? ` (${G.unread[id]} new)` : ''}`;
    channel.replaceChildren(h('option', { value: 'public', selected: G.chat === 'public' }, label('public', 'Public channel')),
      ...others.map((p) => h('option', { value: p.id, selected: G.chat === p.id }, label(p.id, `DM: ${p.name}`))));
    if (chatOnScreen() && !document.hidden) delete G.unread[G.chat];
    const msgs = st.messages.filter((m) => chanOf(m, st.viewer_id) === G.chat);
    const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
    list.replaceChildren(...(msgs.length ? msgs : [null]).map((m) => m
      ? h('li', { class: m.from_id === st.viewer_id ? 'mine' : '' },
        h('div', { class: 'who', style: { color: byId(m.from_id)?.color } }, nameOf(m.from_id), h('span', { class: 'when' }, fmtTime(m.ts))),
        h('div', {}, m.text))
      : h('li', { class: 'muted' }, G.chat === 'public' ? 'No messages yet. Say hi (or make threats).' : 'Private messages are only visible to the two of you.')));
    if (atBottom || !list.dataset.init) { list.scrollTop = list.scrollHeight; list.dataset.init = '1'; }
    input.placeholder = G.chat === 'public' ? 'Message everyone…' : `Message ${nameOf(G.chat)} privately…`;
    if (G.layout === 'match') renderTabs();
    else if (G.layout === 'lobby') renderLobbyTabs();
  }

  // ----- Jury ----------------------------------------------------------------------

  function juryTab() {
    const st = G.state, my = me(), jury = st.jury;
    if (!jury) return h('p', {}, 'The jury is not enabled in this game.');
    const living = st.players.filter((p) => p.alive);
    const tally = {};
    for (const v of jury.votes) tally[v.candidate_id] = (tally[v.candidate_id] || 0) + 1;
    const myVote = jury.votes.find((v) => v.juror_id === my?.id)?.candidate_id;
    const isJuror = my?.is_juror && st.game.status === 'active';
    return h('div', { class: 'stack' },
      h('p', {}, 'Each cycle, every juror (destroyed player) may vote for one living player. The most-voted player gets +1 AP',
        st.game.config.jury.tie_rule === 'all' ? ' (ties: everyone tied gets it).' : ' (ties: one random tied player gets it).'),
      st.game.status === 'active' ? h('p', {}, 'This cycle closes in ', h('b', { 'data-until': jury.closes_at }, '…'), '.') : null,
      st.game.schedule ? h('p', { class: 'muted small' }, 'Cycles close on the AP schedule, right after each AP tick: ', st.game.schedule.effective_summary, '.') : null,
      !st.game.config.jury.votes_public ? h('p', { class: 'muted small' }, 'Votes are secret in this game: you only see your own.') : null,
      h('ul', { class: 'plist' }, living.map((p) => h('li', {},
        h('span', { class: 'dot', style: { background: p.color } }),
        h('span', { class: 'pname' }, p.name, p.id === my?.id ? ' (you)' : ''),
        h('span', { class: 'pstats' }, tally[p.id] ? `${tally[p.id]} vote(s)` : ''),
        isJuror ? h('button', {
          class: myVote === p.id ? 'small primary' : 'small',
          onclick: async () => { if (await post('vote', { candidate_id: p.id })) refresh(); },
        }, myVote === p.id ? 'Voted ✓' : 'Vote') : null))),
      isJuror ? h('p', { class: 'muted small' }, 'You can change your vote until the cycle closes.')
        : h('p', { class: 'muted small' }, my?.alive ? 'You can vote once your tank is destroyed.' : ''));
  }

  // ----- Game over + replay ------------------------------------------------------------

  function replayFrames() {
    // Rebuild board snapshots from the (viewer-visible) log. HP changes from gifts you
    // couldn't see are corrected at the next public shot via target_hp_after.
    const st = G.state, start = st.log.find((e) => e.event_type === 'game_start');
    if (!start) return [];
    const ps = new Map(st.players.map((p) => [p.id, {
      ...p, ap: undefined, alive: true, hp: start.details.hp, action_range: start.details.action_range,
      x: start.details.positions[p.id]?.[0] ?? -1, y: start.details.positions[p.id]?.[1] ?? -1,
    }]));
    const snap = (e) => ({ size: start.details.board_size, players: [...ps.values()].map((p) => ({ ...p })), entry: e });
    const frames = [snap(start)];
    for (const e of st.log) {
      if (e.id <= start.id) continue;
      const a = ps.get(e.actor_id), t = ps.get(e.target_id);
      switch (e.event_type) {
        case 'move': a.x = e.to_xy[0]; a.y = e.to_xy[1]; break;
        case 'shoot': t.hp = e.details.target_hp_after; break;
        case 'kill': case 'removed': t.alive = false; t.hp = 0; break;
        case 'upgrade': a.action_range = e.amount; break;
        case 'give_hp': a.hp -= e.amount; t.hp += e.amount; break;
        case 'game_over': break;
        default: continue;
      }
      frames.push(snap(e));
    }
    return frames;
  }

  function renderGameOver() {
    const st = G.state, node = G.nodes.over;
    if (st.game.status !== 'finished') { node.replaceChildren(); return; }
    if (node.childElementCount) return; // static once rendered (keeps replay controls alive)
    const my = me(), winners = st.game.winner_ids.map(nameOf);
    const reason = { last_standing: 'Last tank standing.', time_limit: 'Time limit reached (HP, then AP, then range).', simultaneous: 'The last tanks fell together.' }[st.game.end_reason] || '';
    const rows = [...st.players].sort((a, b) => (b.alive - a.alive) || ((b.died_at || 0) - (a.died_at || 0)));
    const frames = replayFrames();
    const slider = h('input', { type: 'range', min: 0, max: Math.max(0, frames.length - 1), value: Math.max(0, frames.length - 1), 'aria-label': 'Replay position' });
    const caption = h('div', { class: 'muted small' });
    const show = (i) => {
      const f = frames[i];
      G.replay.frame = i === frames.length - 1 ? null : f;
      caption.textContent = f ? `${fmtTime(f.entry.ts)} · ${describe(f.entry)}` : '';
      renderBoard();
    };
    const play = h('button', {
      class: 'small', onclick: () => {
        if (G.replay.timer) { clearInterval(G.replay.timer); G.replay.timer = null; play.textContent = '▶ Play'; return; }
        if (Number(slider.value) >= frames.length - 1) slider.value = 0;
        play.textContent = '❚❚ Pause';
        G.replay.timer = setInterval(() => {
          const i = Number(slider.value) + 1;
          if (i >= frames.length) { clearInterval(G.replay.timer); G.replay.timer = null; play.textContent = '▶ Play'; return; }
          slider.value = i; show(i);
        }, 400);
      },
    }, '▶ Play');
    G.replay = { frame: null, timer: null };
    slider.addEventListener('input', () => show(Number(slider.value)));

    node.replaceChildren(h('div', { class: 'banner win stack' },
      h('h2', {}, st.game.winner_ids.includes(my?.id) ? '🏆 You won!' : `🏆 ${winners.join(' & ')} won`),
      h('p', { class: 'muted' }, reason),
      h('table', { class: 'final-table' },
        h('thead', {}, h('tr', {}, h('th', {}, 'Player'), h('th', {}, 'Result'), h('th', {}, 'HP'), h('th', {}, 'Range'))),
        h('tbody', {}, rows.map((p) => h('tr', {},
          h('td', {}, h('span', { class: 'dot', style: { background: p.color } }), ' ', p.name),
          h('td', {}, st.game.winner_ids.includes(p.id) ? 'Winner' : p.alive ? 'Survived' : `Destroyed ${fmtTime(p.died_at)}`),
          h('td', {}, p.hp), h('td', {}, p.action_range))))),
      frames.length > 1 ? h('div', {}, h('h3', {}, 'Replay'), h('div', { class: 'replay-controls' }, play, slider), caption) : null,
      h('a', { href: '#/' }, 'Back to start')));
  }

  // ----- Rules modal -----------------------------------------------------------
  // Lives outside #app and the top-bar slots, so state pushes never close or reset it.

  const rulesModal = $('#rules-modal');
  let rulesPressedBackdrop = false;
  $('#rules-open').addEventListener('click', () => {
    rulesModal.showModal();
    $('#rules-body').scrollTop = 0;
  });
  $('#rules-close').addEventListener('click', () => rulesModal.close());
  // Require press and release on the backdrop, so dragging a text selection out doesn't close it.
  rulesModal.addEventListener('pointerdown', (e) => { rulesPressedBackdrop = e.target === rulesModal; });
  rulesModal.addEventListener('click', (e) => {
    if (e.target === rulesModal && rulesPressedBackdrop) rulesModal.close();
    rulesPressedBackdrop = false;
  });

  // ---------------------------------------------------------------------------
  window.addEventListener('hashchange', route);
  document.addEventListener('visibilitychange', () => { if (!document.hidden && G?.state && G.nodes?.chat && chatOnScreen()) renderChat(); });
  // Rotating or resizing can bring the lobby chat into view without a tab click.
  phoneLobby.addEventListener('change', () => { if (G?.state && G.layout === 'lobby') renderChat(); });
  route();
})();
