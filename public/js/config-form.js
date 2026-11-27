// The game settings form (create form and lobby settings).
import { fill, h, wholeNumberInput } from './dom.js';
import { scheduleEditor } from './schedule/editor.js';

const DAY = 86_400;

// `k` is a dotted path into the nested server config (e.g. 'jury.enabled'). `main` fields show
// up front, the rest under "Advanced rules"; `wide` ones span a whole row of the grid. An int
// field with `scale` is shown in bigger units: the server value is the shown value × scale.
const FIELDS = [
  { k: 'fog_of_war', label: 'Fog of war', type: 'select', main: true, wide: true,
    options: [['off', 'Off: everything public'], ['alliance', 'Alliance: hide AP counts & gifts']] },
  { k: 'ap.schedule', type: 'schedule', main: true }, // writes ap.rrule + ap.timezone (see scheduleEditor)
  { k: 'jury.enabled', label: 'Jury of dead players (vote for +1 AP)', type: 'bool', main: true },
  { k: 'jury.schedule', type: 'jury-note', main: true },
  { k: 'debug.fast_ticks', label: 'Debug: fast ticks (for testing)', type: 'bool', main: true },
  { k: 'debug.local_seats', label: 'Debug: play several players from one page', type: 'bool', main: true },
  { k: 'debug.tick_seconds', label: 'Debug tick length (seconds)', type: 'int' },
  { k: 'board.size', label: 'Board size', type: 'int', nullable: true, hint: 'Auto from player count' },
  { k: 'board.min_size', label: 'Auto board: minimum size', type: 'int' },
  { k: 'start.hp', label: 'Starting HP', type: 'int' },
  { k: 'start.ap', label: 'Starting AP', type: 'int' },
  { k: 'start.action_range', label: 'Starting range', type: 'int' },
  { k: 'ap.cap', label: 'AP cap', type: 'int', nullable: true, hint: 'No cap' },
  { k: 'actions.range_upgrade_cost', label: 'Range upgrade cost (AP)', type: 'int' },
  { k: 'actions.max_range', label: 'Max range', type: 'int', nullable: true, hint: 'Unlimited' },
  { k: 'actions.allow_give_hp', label: 'Allow giving HP', type: 'bool' },
  { k: 'actions.loot_on_kill', label: 'Killer loots victim’s AP', type: 'bool' },
  { k: 'board.wreckage_blocks_tile', label: 'Wrecks block tiles', type: 'bool' },
  { k: 'jury.tie_rule', label: 'Jury tie rule', type: 'select', options: [['all', 'All tied get +1'], ['random', 'One random tied player']] },
  { k: 'jury.votes_public', label: 'Jury votes are public', type: 'bool' },
  { k: 'max_duration_seconds', label: 'Time limit (days)', type: 'int', nullable: true, hint: 'None', scale: DAY },
  { k: 'players.minimum', label: 'Min players', type: 'int' },
  { k: 'players.maximum', label: 'Max players', type: 'int' },
  { k: 'notifications.discord_webhook_url', label: 'Discord webhook URL (or use “Connect to Discord” in the lobby)', type: 'text', nullable: true, hint: 'Optional' },
];

const getPath = (obj, path) => path.split('.').reduce((o, k) => (o == null ? undefined : o[k]), obj);
function setPath(obj, path, value) {
  const keys = path.split('.'), lastKey = keys.pop();
  let o = obj;
  for (const k of keys) o = (o[k] ??= {});
  o[lastKey] = value;
}

/** The input for one plain field, wrapped in its label. */
function fieldInput(f, value, disabled) {
  if (f.type === 'bool') {
    const input = h('input', { type: 'checkbox', checked: !!value, disabled });
    return { input, el: h('label', { class: 'check' }, input, h('span', {}, f.label)) };
  }
  const placeholder = f.nullable ? (f.hint || '') : '';
  const input = f.type === 'select'
    ? h('select', { disabled }, f.options.map(([val, lab]) => h('option', { value: val, selected: value === val }, lab)))
    : f.type === 'int'
      ? wholeNumberInput({ value: value == null ? '' : value / (f.scale || 1), disabled, placeholder })
      : h('input', { type: 'text', value: value ?? '', disabled, placeholder });
  const hint = f.hint && !f.nullable ? h('div', { class: 'hint' }, f.hint) : null; // nullable fields show it as the placeholder
  return { input, el: h('label', { class: f.wide ? 'wide' : null }, h('span', {}, f.label), input, hint) };
}

/** A plain field's current value, typed for the server. */
function fieldValue(f, el) {
  if (f.type === 'bool') return el.checked;
  if (f.type === 'select') return el.value;
  if (el.value.trim() === '') return f.nullable ? null : el.value;
  return f.type === 'int' ? Math.round(Number(el.value) * (f.scale || 1)) : el.value.trim();
}

/**
 * The settings form for `values` (a full config). Returns an element with getValues(), the
 * config overrides it shows. `onDirty` fires on any edit.
 */
export function configForm(values, { editable = true, onDirty, allowDebug = false } = {}) {
  // Hidden fields are also left out of getValues, so saving keeps their current value.
  const fields = allowDebug ? FIELDS : FIELDS.filter((f) => !f.k.startsWith('debug.'));
  const plain = fields.filter((f) => f.type !== 'schedule' && f.type !== 'jury-note');
  const schedule = scheduleEditor(values.ap, { editable, debug: values.debug });
  const juryNote = h('div', { class: 'wide jury-note muted small', role: 'note' });

  const inputs = {}, elements = { 'ap.schedule': schedule, 'jury.schedule': juryNote };
  for (const f of plain) {
    const { input, el } = fieldInput(f, getPath(values, f.k), !editable);
    inputs[f.k] = input;
    elements[f.k] = el;
  }
  const grid = (list) => h('div', { class: 'config-grid' }, list.map((f) => elements[f.k]));
  const root = h('div', { class: 'stack' },
    grid(fields.filter((f) => f.main)),
    h('details', { class: 'adv' }, h('summary', {}, 'Advanced rules'), grid(fields.filter((f) => !f.main))));
  if (onDirty) root.addEventListener('input', onDirty);

  // The jury closes a cycle on every AP tick, so its note repeats the schedule summary.
  const showJuryNote = () => {
    juryNote.hidden = !inputs['jury.enabled']?.checked;
    fill(juryNote, 'Jury cycles close on the same schedule as AP: ', h('b', {}, schedule.summaryText()));
  };
  schedule.onSummary = showJuryNote;
  inputs['jury.enabled']?.addEventListener('change', showJuryNote);
  showJuryNote();

  // Debug fast ticks replace the schedule; say so next to it.
  const fast = inputs['debug.fast_ticks'], seconds = inputs['debug.tick_seconds'];
  const debugChanged = () => schedule.setDebug({
    fast_ticks: fast ? fast.checked : values.debug?.fast_ticks,
    tick_seconds: seconds ? Number(seconds.value) : values.debug?.tick_seconds,
  });
  fast?.addEventListener('change', debugChanged);
  seconds?.addEventListener('input', debugChanged);

  root.getValues = () => {
    const out = { ap: schedule.getValues() };
    for (const f of plain) setPath(out, f.k, fieldValue(f, inputs[f.k]));
    return out;
  };
  return root;
}
