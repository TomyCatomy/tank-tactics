// AP schedule editor (RFC 5545 RRULE + IANA time zone), part of the config form.
//
// Three layers over one rule string, like a calendar's "Custom recurrence":
//  1. presets (derived from today's date in the chosen zone, plus a time of day),
//  2. "Custom…": a structured builder (repeat every N hours/days/weeks/months, weekday
//     chips, monthly by day-of-month or nth/last weekday, time of day),
//  3. "Advanced": the raw RRULE, validated live by the server.
// The rule string is the source of truth. A rule a preset or the builder can express is
// shown there; anything else shows as "Custom rule" and is never rewritten unless the host
// changes it. The server validates and summarises rules (POST /api/schedule/preview).
import { api, serverDefaults } from '../api.js';
import { fill, h, wholeNumberInput } from '../dom.js';
import { dayOrdinal, pad2 } from '../format.js';
import {
  MAX_EVERY, ORDINAL, ORD_SHORT, WD_CHIP, WD_LONG, WEEKDAYS, WORKWEEK,
  modelToRule, parseRule, presetOf, ruleToModel, todayIn,
} from './rules.js';

const TIMED_PRESETS = ['daily', 'weekdays', 'weekly', 'monthday', 'monthlastday', 'monthnth', 'monthlast'];

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

const timeOf = (m) => `${pad2(m?.hour ?? 9)}:${pad2(m?.minute ?? 0)}`;

/** Read an <input type=time> into m.hour / m.minute (09:00 if it's empty or broken). */
function readTime(input, m) {
  const [hh, mm] = (input.value || '09:00').split(':').map(Number);
  m.hour = Number.isFinite(hh) ? hh : 9;
  m.minute = Number.isFinite(mm) ? mm : 0;
}

/** The input's whole number, clamped to [lo, hi] (`dflt` if blank), written back so the form shows what was used. */
function clampInt(input, lo, hi, dflt) {
  const n = Math.trunc(Number(input.value));
  const v = Number.isFinite(n) && input.value !== '' ? Math.min(hi, Math.max(lo, n)) : dflt;
  input.value = String(v);
  return v;
}

/**
 * The schedule part of the config form. `ap` is the config's ap section. Returns an element
 * with getValues() -> {rrule, timezone}, summaryText(), setDebug(debug) and an onSummary hook.
 */
export function scheduleEditor(ap, { editable = true, debug = null } = {}) {
  const st = {
    rule: ap?.rrule || 'FREQ=DAILY', tz: ap?.timezone || 'UTC', summary: '', debug,
    // Context for preset labels and builder defaults: today in the zone, overridden by the rule's own values.
    ctx: null, model: null, preset: null, hoursEvery: 6,
    lastTime: { hour: 9, minute: 0 }, // the time of day to reuse when switching back from an hourly schedule
  };
  const uid = `sched-${Math.random().toString(36).slice(2, 8)}`;
  const dis = !editable;
  const num = (attrs) => wholeNumberInput({ disabled: dis, ...attrs });

  // ----- Layer 1: presets + time of day
  const presetSel = h('select', { id: `${uid}-preset`, disabled: dis, 'aria-describedby': `${uid}-summary` });
  // No `required`/min/max checks by the browser: a hidden invalid field would silently block the create form.
  const timeIn = h('input', { type: 'time', id: `${uid}-time`, step: 60, disabled: dis });
  const timeLabel = h('label', { class: 'sched-inline', for: `${uid}-time` }, h('span', {}, 'at'), timeIn);
  const hoursIn = num({ min: 1, max: MAX_EVERY.hour, 'aria-label': 'Hours between ticks' });
  const minuteIn = num({ min: 0, max: 59, 'aria-label': 'At minute' });
  const hoursRow = h('div', { class: 'sched-inline' }, h('span', {}, 'every'), hoursIn, h('span', {}, 'hours at minute'), minuteIn);

  // ----- Layer 2: builder
  const everyIn = num({ min: 1, max: MAX_EVERY.week, id: `${uid}-every` });
  const unitSel = h('select', { disabled: dis, 'aria-label': 'Repeat unit' },
    ['hour', 'day', 'week', 'month'].map((u) => h('option', { value: u }, `${u}(s)`)));
  const chips = WEEKDAYS.map((d) => h('button', {
    type: 'button', class: 'chip', disabled: dis, 'aria-pressed': 'false', 'aria-label': WD_LONG[d], title: WD_LONG[d], 'data-day': d,
    onclick: () => toggleDay(d),
  }, WD_CHIP[d]));
  const chipRow = h('div', { class: 'chips', role: 'group', 'aria-label': 'Repeat on' }, chips);
  const radio = (mode) => h('input', { type: 'radio', name: `${uid}-month`, value: mode, disabled: dis });
  const monthRadios = { day: radio('day'), lastday: radio('lastday'), nth: radio('nth'), last: radio('last') };
  // Days 29-31 would skip months; "the last day" covers the end of every month.
  const monthDayIn = num({ min: 1, max: 28, 'aria-label': 'Day of the month' });
  const nthText = h('span'), lastText = h('span');
  const monthBox = h('fieldset', { class: 'sched-month' }, h('legend', {}, 'Monthly on'),
    h('label', { class: 'radio' }, monthRadios.day, h('span', {}, 'Day'), monthDayIn),
    h('label', { class: 'radio' }, monthRadios.lastday, h('span', {}, 'The last day')),
    h('label', { class: 'radio' }, monthRadios.nth, nthText),
    h('label', { class: 'radio' }, monthRadios.last, lastText),
    h('div', { class: 'hint' }, 'Days 29–31 and a fifth weekday aren’t in every month, so they aren’t offered: use the last day or the last weekday.'));
  const bTimeIn = h('input', { type: 'time', step: 60, disabled: dis, id: `${uid}-btime` });
  const bTimeRow = h('label', { class: 'sched-inline', for: `${uid}-btime` }, h('span', {}, 'at'), bTimeIn);
  const bMinuteIn = num({ min: 0, max: 59, id: `${uid}-bmin` });
  const bMinuteRow = h('label', { class: 'sched-inline', for: `${uid}-bmin` }, h('span', {}, 'at minute'), bMinuteIn);
  const builder = h('fieldset', { class: 'sched-builder' }, h('legend', {}, 'Custom schedule'),
    h('div', { class: 'sched-inline' }, h('label', { for: `${uid}-every` }, 'Repeat every'), everyIn, unitSel),
    chipRow, monthBox, bTimeRow, bMinuteRow);

  // ----- Layer 3: raw RRULE
  const rawIn = h('input', {
    type: 'text', id: `${uid}-raw`, class: 'mono', disabled: dis, spellcheck: 'false', autocapitalize: 'characters',
    autocomplete: 'off', 'aria-describedby': `${uid}-err`, placeholder: 'FREQ=DAILY;BYHOUR=9;BYMINUTE=0',
  });
  const rawErr = h('div', { class: 'error-text small', id: `${uid}-err`, 'aria-live': 'polite' });
  const advanced = h('details', { class: 'sched-adv' }, h('summary', {}, 'Advanced: RRULE'),
    h('label', { for: `${uid}-raw` }, h('span', {}, 'Recurrence rule (RFC 5545 RRULE, without DTSTART, COUNT or UNTIL)')),
    rawIn, rawErr,
    h('div', { class: 'hint' }, 'The schedule starts when the game starts and runs until it ends. Ticks are on whole minutes, at most one a minute, and at least one every calendar month.'));

  // ----- Shared: time zone and summary
  const tzSel = h('select', { id: `${uid}-tz`, disabled: dis }, h('option', { value: st.tz, selected: true }, st.tz));
  const summaryEl = h('p', { class: 'sched-summary', id: `${uid}-summary`, 'aria-live': 'polite' });
  const debugEl = h('p', { class: 'notice small' });

  const root = h('div', { class: 'wide sched stack', role: 'group', 'aria-labelledby': `${uid}-label` },
    h('label', { id: `${uid}-label`, for: `${uid}-preset`, class: 'sched-title' }, h('span', {}, 'AP schedule: +1 AP for every living tank')),
    h('div', { class: 'sched-row' }, presetSel, timeLabel, hoursRow),
    builder, advanced,
    h('label', { for: `${uid}-tz`, class: 'sched-tz' }, h('span', {}, 'Time zone'), tzSel),
    h('div', { class: 'sched-preview' }, summaryEl, debugEl));

  // ----- State -> controls

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
    opts.push(['hours', 'Every few hours'], ['minute', 'Every minute'], ['custom', 'Custom…']);
    if (st.preset === 'rule') opts.push(['rule', 'Custom rule']);
    return opts;
  }

  /** Today in the zone, with the rule's own weekday / day / position taking precedence. */
  function contextFor(m) {
    const ctx = todayIn(st.tz);
    if (m?.unit === 'week' && m.days.length === 1) ctx.weekday = m.days[0];
    if (m?.unit === 'month') {
      if (m.monthMode === 'day') ctx.day = m.monthDay;
      if (m.monthMode === 'lastday') ctx.isLastDay = true;
      if (m.monthMode === 'nth') { ctx.weekday = m.weekday; ctx.nth = m.nth; }
      if (m.monthMode === 'last') { ctx.weekday = m.weekday; ctx.isLast = true; }
    }
    return ctx;
  }

  /** Re-derive every control from st.rule (after a raw edit, or on load). */
  function syncFromRule() {
    const options = parseRule(st.rule);
    const m = st.model = ruleToModel(options);
    st.preset = presetOf(options, m);
    st.ctx = contextFor(m);
    if (m?.unit === 'hour') st.hoursEvery = m.every;
    if (m && m.unit !== 'hour') st.lastTime = { hour: m.hour, minute: m.minute };
    if (document.activeElement !== rawIn) rawIn.value = st.rule;
    render();
  }

  function render() {
    fill(presetSel, presetOptions().map(([v, label]) => h('option', { value: v, selected: v === st.preset }, label)));
    const m = st.model;
    const timed = TIMED_PRESETS.includes(st.preset);
    timeLabel.hidden = !timed;
    hoursRow.hidden = st.preset !== 'hours';
    if (timed) timeIn.value = timeOf(m);
    if (st.preset === 'hours') { hoursIn.value = String(st.hoursEvery); minuteIn.value = String(m?.minute ?? 0); }
    // The builder shows for "Custom…" (a rule it can express that no preset matches, or the host picked it).
    builder.hidden = st.preset !== 'custom';
    if (st.preset === 'custom' && m) renderBuilder(m);
    // A rule nothing else can show: open the raw view so the host sees what it is.
    if (st.preset === 'rule') advanced.open = true;
    renderDebug();
    schedulePreview();
  }

  function renderBuilder(m) {
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

  function renderDebug() {
    const d = st.debug;
    debugEl.hidden = !d?.fast_ticks;
    if (d?.fast_ticks) debugEl.textContent = `Debug fast ticks are on: AP (and the jury) tick every ${d.tick_seconds || 10} s instead of on this schedule.`;
  }

  // ----- Controls -> rule

  const changed = () => root.dispatchEvent(new Event('input', { bubbles: true })); // marks the form dirty

  /** Set a new rule from a preset or the builder (never from the raw field). */
  function setRule(rule, { keepControls = false } = {}) {
    st.rule = rule;
    rawIn.value = rule;
    clearRawError();
    if (keepControls) schedulePreview(); else syncFromRule();
    changed();
  }

  /** A model for a preset, from the context and the current time of day. */
  function presetModel(id) {
    const c = st.ctx, base = { every: 1, days: [], monthMode: 'day', monthDay: null, nth: null, weekday: null, ...st.lastTime };
    if (!timeLabel.hidden && timeIn.value) readTime(timeIn, base);
    switch (id) {
      case 'daily': return { ...base, unit: 'day' };
      case 'weekdays': return { ...base, unit: 'week', days: [...WORKWEEK] };
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
    if (id === 'minute') return setRule('FREQ=MINUTELY');
    if (id === 'rule') return; // keep the custom rule as it is
    if (id === 'custom') {
      // Open the builder on the current rule if it fits, else on "weekly on today's weekday".
      st.preset = 'custom';
      if (!st.model) { st.model = presetModel('weekly'); setRule(modelToRule(st.model), { keepControls: true }); }
      render();
      return;
    }
    st.model = presetModel(id);
    setRule(modelToRule(st.model));
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

  /** Builder edits: rebuild the rule and keep the builder open (presets don't take over). */
  function fromBuilder() {
    const m = st.model;
    st.preset = 'custom';
    if (m.unit !== 'hour') st.lastTime = { hour: m.hour, minute: m.minute };
    setRule(modelToRule(m), { keepControls: true });
    render();
  }
  /** Builder edit: change the model with `edit(model)`, then rebuild the rule. */
  const onBuilder = (input, event, edit) => input.addEventListener(event, () => { edit(st.model); fromBuilder(); });
  const monthDay = () => clampInt(monthDayIn, 1, 28, Math.min(28, st.ctx.day));

  function toggleDay(d) {
    const days = new Set(st.model.days);
    if (!days.has(d)) days.add(d);
    else if (days.size > 1) days.delete(d); // keep at least one day
    st.model.days = WEEKDAYS.filter((x) => days.has(x));
    fromBuilder();
  }
  onBuilder(everyIn, 'change', (m) => { m.every = clampInt(everyIn, 1, MAX_EVERY[m.unit], 1); });
  onBuilder(unitSel, 'change', (m) => {
    m.unit = unitSel.value;
    m.every = Math.min(m.every, MAX_EVERY[m.unit]);
    if (m.unit === 'week' && !m.days.length) m.days = [st.ctx.weekday];
    if (m.unit === 'month' && m.monthMode === 'day' && !m.monthDay) m.monthDay = Math.min(28, st.ctx.day);
    if (m.unit === 'month' && m.monthMode !== 'day' && !m.weekday) m.weekday = st.ctx.weekday;
  });
  for (const [mode, input] of Object.entries(monthRadios)) {
    onBuilder(input, 'change', (m) => {
      m.monthMode = mode;
      if (mode === 'day') m.monthDay = monthDay();
      if (mode === 'nth' || mode === 'last') {
        m.weekday ||= st.ctx.weekday;
        m.nth = mode === 'last' ? -1 : (m.nth > 0 ? m.nth : Math.min(4, st.ctx.nth));
      }
      // 'lastday' needs nothing more: it's BYMONTHDAY=-1.
    });
  }
  onBuilder(monthDayIn, 'change', (m) => { m.monthMode = 'day'; m.monthDay = monthDay(); });
  onBuilder(bTimeIn, 'change', (m) => readTime(bTimeIn, m));
  onBuilder(bMinuteIn, 'change', (m) => { m.minute = clampInt(bMinuteIn, 0, 59, 0); });

  // Raw edits: debounced; the rule string is kept exactly as typed.
  let rawTimer = null;
  const fromRaw = () => { clearTimeout(rawTimer); st.rule = rawIn.value.trim(); syncFromRule(); };
  rawIn.addEventListener('input', () => { clearTimeout(rawTimer); rawTimer = setTimeout(fromRaw, 300); });
  rawIn.addEventListener('change', fromRaw);

  tzSel.addEventListener('change', () => { st.tz = tzSel.value; syncFromRule(); changed(); });

  // ----- Server summary (debounced; the latest request wins)

  function clearRawError() {
    rawErr.textContent = '';
    rawIn.removeAttribute('aria-invalid');
  }

  let previewTimer = null, previewSeq = 0;
  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(runPreview, 250);
  }
  async function runPreview() {
    const seq = ++previewSeq;
    try {
      const res = await api('POST', '/api/schedule/preview', { rrule: st.rule, timezone: st.tz });
      if (seq !== previewSeq) return;
      st.summary = res.summary;
      summaryEl.classList.remove('bad');
      fill(summaryEl, h('b', {}, res.summary));
      clearRawError();
    } catch (e) {
      if (seq !== previewSeq) return;
      const invalid = e.code === 'invalid_schedule';
      st.summary = 'Invalid schedule';
      summaryEl.textContent = invalid ? `Invalid schedule: ${e.message}` : e.message;
      summaryEl.classList.add('bad');
      if (invalid) { rawErr.textContent = e.message; rawIn.setAttribute('aria-invalid', 'true'); }
    }
    root.onSummary?.();
  }

  // ----- Public interface

  root.getValues = () => ({ rrule: st.rule, timezone: st.tz });
  root.summaryText = () => st.summary || '…';
  root.setDebug = (d) => { st.debug = d; renderDebug(); };
  root.onSummary = null;

  serverDefaults().then(({ timezones }) => {
    if (!timezones?.length) return;
    fill(tzSel, zoneOptions(timezones.includes(st.tz) ? timezones : [st.tz, ...timezones], st.tz));
  }).catch(() => { });

  syncFromRule();
  return root;
}
