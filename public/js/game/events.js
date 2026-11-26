// Log entries: their text in the log, the notifications they raise, and who they involve.
import { nameOf } from './session.js';

const names = (ids, sep) => ids.map(nameOf).join(sep);

/** One line of the game log. */
export function describe(e) {
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
    case 'jury_award': return `The jury awarded +1 AP to ${names(d.winners, ', ')}.`;
    case 'game_over': return `🏆 Game over: ${names(d.winners, ' & ')} won${d.reason === 'time_limit' ? ' on the time limit' : ''}.`;
    default: return e.event_type;
  }
}

/** The [text, kind] notification a new log entry raises for player `myId`, or null. */
export function notificationFor(e, myId) {
  const a = nameOf(e.actor_id), t = nameOf(e.target_id), d = e.details;
  const toMe = e.target_id === myId;
  switch (e.event_type) {
    case 'ap_tick': return d.recipients.includes(myId) ? [`You received +${e.amount} AP.`, 'good'] : null;
    case 'shoot': return toMe ? [`${a} shot you! You have ${d.target_hp_after} HP left.`, 'error'] : null;
    case 'give_ap': return toMe ? [`${a} gave you ${e.amount} AP.`, 'good'] : null;
    case 'give_hp': return toMe ? [`${a} gave you ${e.amount} HP.`, 'good'] : null;
    case 'loot': return e.actor_id === myId ? [`You looted ${e.amount} AP.`, 'good'] : null;
    case 'jury_award': return d.winners.includes(myId) ? ['The jury awarded you +1 AP.', 'good'] : null;
    case 'kill': return [toMe ? `${a} destroyed your tank.` : `${a} destroyed ${t}.`, 'error'];
    case 'removed': return [`${t} was removed by the host.`];
    case 'game_start': return ['The game has started!', 'good'];
    case 'game_over': return [d.winners.includes(myId) ? 'You won! 🏆' : `Game over. ${names(d.winners, ' & ')} won.`, 'good'];
    default: return null;
  }
}

/** Whether a log entry involves player `id` (for the log's player filter). */
export const involves = (e, id) => e.actor_id === id || e.target_id === id ||
  (e.event_type === 'ap_tick' && e.details.recipients.includes(id)) ||
  (e.event_type === 'jury_award' && e.details.winners.includes(id));
