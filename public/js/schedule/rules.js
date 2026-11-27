// Pure RRULE helpers for the schedule editor, on top of rrule.js (public/vendor, loaded as a
// classic script before the modules, so window.rrule exists). No DOM here.
//
// The editor's "model" is what its structured builder can express:
//   {unit: 'hour'|'day'|'week'|'month', every, hour, minute,
//    days: ['MO', ...]                         (weekly),
//    monthMode: 'day'|'lastday'|'nth'|'last', monthDay, nth, weekday   (monthly)}

export const RRule = window.rrule.RRule;

export const WEEKDAYS = ['MO', 'TU', 'WE', 'TH', 'FR', 'SA', 'SU'];
export const WORKWEEK = ['MO', 'TU', 'WE', 'TH', 'FR'];
export const WD_LONG = { MO: 'Monday', TU: 'Tuesday', WE: 'Wednesday', TH: 'Thursday', FR: 'Friday', SA: 'Saturday', SU: 'Sunday' };
export const WD_CHIP = { MO: 'M', TU: 'T', WE: 'W', TH: 'T', FR: 'F', SA: 'S', SU: 'S' };
export const ORDINAL = { 1: 'first', 2: 'second', 3: 'third', 4: 'fourth', '-1': 'last' };
export const ORD_SHORT = { 1: '1st', 2: '2nd', 3: '3rd', 4: '4th', '-1': 'last' };
const UNIT_FREQ = { hour: RRule.HOURLY, day: RRule.DAILY, week: RRule.WEEKLY, month: RRule.MONTHLY };
const FREQ_UNIT = Object.fromEntries(Object.entries(UNIT_FREQ).map(([unit, freq]) => [freq, unit]));
/** The schedule must fire every calendar month (the server checks): these are the longest steps that always do. */
export const MAX_EVERY = { hour: 672, day: 28, week: 4, month: 1 };

/** RRULE body -> rrule.js options (only the parts it gives), or null if it doesn't parse or has no FREQ. */
export function parseRule(text) {
  try {
    const o = RRule.parseString(String(text ?? '').trim().toUpperCase()); // RRULE is case-insensitive; rrule.js isn't
    return o.freq === undefined ? null : o;
  } catch { return null; }
}

const MODEL_KEYS = ['freq', 'interval', 'byhour', 'byminute', 'bysecond', 'byweekday', 'bymonthday', 'bysetpos'];

/** The builder's model for parsed rule options, or null if the builder can't express it exactly. */
export function ruleToModel(o) {
  if (!o || Object.keys(o).some((k) => !MODEL_KEYS.includes(k))) return null;
  const list = (k) => (o[k] == null ? [] : [].concat(o[k]));
  const one = (k) => (list(k).length === 1 ? list(k)[0] : null);
  const days = list('byweekday'); // rrule.js Weekday objects: {weekday: 0 (MO) .. 6 (SU), n}
  const hasMonthDay = list('bymonthday').length > 0, hasSetPos = list('bysetpos').length > 0;
  const unit = FREQ_UNIT[o.freq], every = o.interval ?? 1;
  if (!unit || every < 1 || every > MAX_EVERY[unit]) return null;
  if (list('bysecond').some((x) => x !== 0)) return null;
  const m = { unit, every, days: [], monthMode: 'day', monthDay: null, nth: null, weekday: null, hour: 0, minute: one('byminute') };
  if (m.minute === null) return null; // the builder always sets the minute

  if (unit === 'hour') return list('byhour').length || days.length || hasMonthDay || hasSetPos ? null : m;
  m.hour = one('byhour');
  if (m.hour === null) return null;
  if (unit === 'day') return days.length || hasMonthDay || hasSetPos ? null : m;
  if (unit === 'week') {
    if (!days.length || days.some((d) => d.n) || hasMonthDay || hasSetPos) return null;
    m.days = WEEKDAYS.filter((_, i) => days.some((d) => d.weekday === i));
    return m.days.length === days.length ? m : null;
  }
  // Monthly: on a day of the month, the last day, or the nth / last weekday.
  if (hasMonthDay) {
    const d = one('bymonthday');
    if (days.length || hasSetPos || d === null) return null;
    if (d === -1) return { ...m, monthMode: 'lastday' };
    if (d < 1 || d > 28) return null; // 29-31 skip months: shown as a custom rule (and refused by the server)
    return { ...m, monthMode: 'day', monthDay: d };
  }
  if (days.length !== 1 || (days[0].n && hasSetPos)) return null;
  const n = days[0].n || one('bysetpos');
  if (![1, 2, 3, 4, -1].includes(n)) return null;
  return { ...m, monthMode: n === -1 ? 'last' : 'nth', nth: n, weekday: WEEKDAYS[days[0].weekday] };
}

/** The builder's model -> its RRULE (one canonical spelling). */
export function modelToRule(m) {
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

/** Which preset (if any) shows this rule: a key of the editor's preset list, or 'rule' for none. */
export function presetOf(options, model) {
  if (options && Object.keys(options).length === 1 && options.freq === RRule.DAILY) return 'start';
  if (options && Object.keys(options).length === 1 && options.freq === RRule.MINUTELY) return 'minute';
  if (!model) return 'rule';
  if (model.unit === 'hour') return 'hours';
  if (model.every !== 1) return 'custom';
  if (model.unit === 'day') return 'daily';
  if (model.unit === 'week') {
    if (model.days.join() === WORKWEEK.join()) return 'weekdays';
    return model.days.length === 1 ? 'weekly' : 'custom';
  }
  return { day: 'monthday', lastday: 'monthlastday', nth: 'monthnth', last: 'monthlast' }[model.monthMode];
}

/** Today's weekday, day of month and position in the month, in time zone `tz`. */
export function todayIn(tz) {
  const opts = { weekday: 'short', day: 'numeric', month: 'numeric', year: 'numeric' };
  let parts;
  try {
    parts = new Intl.DateTimeFormat('en-US', { ...opts, timeZone: tz }).formatToParts(new Date());
  } catch {
    parts = new Intl.DateTimeFormat('en-US', opts).formatToParts(new Date());
  }
  const get = (t) => parts.find((x) => x.type === t)?.value;
  const weekday = { Mon: 'MO', Tue: 'TU', Wed: 'WE', Thu: 'TH', Fri: 'FR', Sat: 'SA', Sun: 'SU' }[get('weekday')] || 'MO';
  const day = Number(get('day')), month = Number(get('month')), year = Number(get('year'));
  const inMonth = new Date(Date.UTC(year, month, 0)).getUTCDate();
  return { weekday, day, nth: Math.ceil(day / 7), isLast: day + 7 > inMonth, isLastDay: day === inMonth };
}
