// Action sheets: move, upgrade (your own tank) and shoot / give / DM (another tank).
// The buttons are disabled with a reason when an action looks impossible; the server re-checks everything.
import { dot, h, wholeNumberInput } from '../dom.js';
import { fmtDist, inRange, rangeDist } from '../format.js';
import { closeSheet, openSheet } from '../notify.js';
import { openDm } from './match.js';
import { G, byId, me, post, refresh } from './session.js';

/** Send an action; on success close the sheet and refresh. */
export async function doAction(body) {
  if (await post('action', body)) { closeSheet(); refresh(); }
}

/** Why an action is unavailable, or null if it looks fine. */
export function blockers({ cost = 0, target = null, needRange = false } = {}) {
  const st = G.state, my = me();
  if (st.game.status !== 'active') return st.game.status === 'lobby' ? 'Game not started' : 'Game over';
  if (!my) return 'You are not in this game';
  if (!my.alive) return 'Your tank is destroyed';
  if (target && !target.alive) return 'Target is destroyed';
  if (needRange && target && !inRange(my, target, my.action_range)) {
    return `Out of range (distance ${fmtDist(rangeDist(my, target))}, your range ${my.action_range})`;
  }
  if (cost && my.ap < cost) return `Needs ${cost} AP (you have ${my.ap})`;
  return null;
}

/** Why upgrading range is unavailable, or null. */
function upgradeBlocker() {
  const my = me(), { max_range: max, range_upgrade_cost: cost } = G.state.game.config.actions;
  if (max !== null && my.action_range >= max) return `Already at max range (${max})`;
  return blockers({ cost });
}

/** Why giving HP to `target` is unavailable, or null. */
function giveHpBlocker(target) {
  if (!G.state.game.config.actions.allow_give_hp) return 'Disabled in this game';
  return blockers({ target, needRange: true }) || (me()?.hp <= 1 ? 'You must keep at least 1 HP' : null);
}

function actRow(label, cost, why, onclick, extra = null) {
  return h('div', { class: 'act' },
    h('div', {}, h('b', {}, label), ' ', h('span', { class: 'muted small' }, cost)),
    h('div', { class: 'row' }, extra, h('button', { class: label === 'Shoot' ? 'danger' : 'primary', disabled: !!why, onclick }, label)),
    why ? h('div', { class: 'why' }, why) : null);
}

const amountInput = (what) => wholeNumberInput({ class: 'amount', min: 1, value: 1, 'aria-label': `${what} amount` });

// Each sheet is opened with a builder, so it is redrawn from every new state (AP arriving, a target moving).

export function openMoveSheet(x, y) {
  openSheet(() => {
    const my = me();
    return [h('h2', {}, `Move to (${x}, ${y})`),
      h('p', { class: 'muted' }, `From (${my.x}, ${my.y}). Moves cost 1 AP each.`),
      actRow('Move', '1 AP', blockers({ cost: 1 }), () => doAction({ type: 'move', to: [x, y] }))];
  });
}

export function openSelfSheet() {
  openSheet(() => {
    const my = me(), cost = G.state.game.config.actions.range_upgrade_cost;
    return [h('h2', {}, dot(my.color), `${my.name} (you)`),
      h('p', { class: 'muted' }, `HP ${my.hp} · AP ${my.ap} · Range ${my.action_range}. Tap a highlighted tile next to you to move.`),
      actRow('Upgrade range', `${cost} AP → range ${my.action_range + 1}`, upgradeBlocker(), () => doAction({ type: 'upgrade' }))];
  });
}

export function openTargetSheet(id) {
  if (byId(id)) openSheet(() => targetSheet(id));
}

function targetSheet(id) {
  const st = G.state, t = byId(id), my = me();
  if (!t) return [];
  const d = my && my.x >= 0 ? rangeDist(my, t) : null;
  const apIn = amountInput('AP'), hpIn = amountInput('HP');
  const rangeBlocker = blockers({ cost: 1, target: t, needRange: true });
  const removePlayer = async () => {
    if (confirm(`Remove ${t.name}? Their tank will be destroyed.`) && await post('remove', { player_id: t.id })) { closeSheet(); refresh(); }
  };
  return [
    h('h2', {}, dot(t.color), t.name),
    h('p', { class: 'muted' }, `HP ${t.hp} · Range ${t.action_range}`, t.ap !== undefined ? ` · AP ${t.ap}` : '', d !== null ? ` · distance ${fmtDist(d)}` : '',
      my && d !== null && inRange(t, my, t.action_range) ? ' · you are in their range' : ''),
    actRow('Shoot', '1 AP · 1 damage', rangeBlocker, () => doAction({ type: 'shoot', target_id: t.id })),
    actRow('Give AP', 'costs the AP you give', rangeBlocker,
      () => doAction({ type: 'give_ap', target_id: t.id, amount: Number(apIn.value) }), apIn),
    actRow('Give HP', 'costs the HP you give', giveHpBlocker(t),
      () => doAction({ type: 'give_hp', target_id: t.id, amount: Number(hpIn.value) }), hpIn),
    my?.alive && t.alive ? h('div', { class: 'act' }, h('div', {}, h('b', {}, 'Private message')),
      h('button', { onclick: () => { closeSheet(); openDm(t.id); } }, 'Open DM')) : null,
    my?.is_host && st.game.status === 'active' ? h('div', { class: 'act' },
      h('div', { class: 'why' }, 'Host: removing a player destroys their tank.'),
      h('button', { class: 'danger small', onclick: removePlayer }, 'Remove player')) : null];
}
