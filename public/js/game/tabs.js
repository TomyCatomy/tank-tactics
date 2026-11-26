// The match screen's tabs (chat lives in chat.js).
import { dot, h } from '../dom.js';
import { countBy, dist, fmtTime } from '../format.js';
import { freeNeighbours, selectPlayer } from './board.js';
import { describe, involves } from './events.js';
import { renderTab } from './match.js';
import { G, me, postAndRefresh } from './session.js';
import { blockers, doAction } from './sheets.js';

const scheduleSummary = () => G.state.game.schedule?.effective_summary;
const voteTally = () => countBy(G.state.jury?.votes || [], (v) => v.candidate_id);

function notificationsButton() {
  if (!('Notification' in window) || Notification.permission !== 'default') return null;
  const ask = async () => { try { await Notification.requestPermission(); } catch { } renderTab(); };
  return h('div', { class: 'shrink' }, h('button', { class: 'small', onclick: ask }, 'Enable notifications'));
}

export function meTab() {
  const st = G.state, my = me(), c = st.game.config, active = st.game.status === 'active';
  if (!my) return h('p', {}, 'Spectating.');
  const inRange = st.players.filter((p) => p.alive && p.id !== my.id && dist(my, p) <= my.action_range).length;
  const canMove = freeNeighbours(my, st.players, st.game.board_size, c).length > 0;
  const upgradeCost = c.actions.range_upgrade_cost, maxRange = c.actions.max_range;
  const capped = maxRange !== null && my.action_range >= maxRange;
  const item = (label, cost, why, btn = null) => h('li', {},
    h('div', {}, h('div', {}, label), h('div', { class: 'why' }, why || '')),
    h('div', { class: 'row shrink' }, h('span', { class: 'cost' }, cost), btn));
  const stat = (value, label) => h('div', { class: 'stat' }, h('b', {}, value), h('span', {}, label));

  return h('div', {},
    h('div', { class: 'row', style: { marginBottom: '10px' } },
      h('h2', { style: { margin: 0 } }, dot(my.color), ' ', my.name),
      notificationsButton()),
    h('div', { class: 'stats' }, stat(my.hp, 'HP'), stat(my.ap, 'AP'), stat(my.action_range, 'Range')),
    active && my.alive
      ? h('p', {}, 'Next AP in ', h('b', { 'data-until': st.game.next_ap_tick_at }, '…'),
        st.game.ends_at ? [' · game ends in ', h('b', { 'data-until': st.game.ends_at }, '…')] : null)
      : null,
    scheduleSummary() ? h('p', { class: 'muted small sched-line' }, 'AP schedule: ', scheduleSummary()) : null,
    active && my.alive ? h('ul', { class: 'actions-list' },
      item('Move', '1 AP', blockers({ cost: 1 }) || (canMove ? 'Tap a highlighted tile next to your tank.' : 'No free adjacent tiles.')),
      item('Shoot', '1 AP', blockers({ cost: 1 }) || (inRange ? `${inRange} tank(s) in range: tap one.` : 'No tanks in range.')),
      item('Give AP', 'the AP given', blockers({ cost: 1 }) || 'Tap a tank in range.'),
      item('Give HP', 'the HP given', !c.actions.allow_give_hp ? 'Disabled in this game.'
        : blockers() || (my.hp <= 1 ? 'You must keep at least 1 HP.' : 'Tap a tank in range.')),
      item('Upgrade range', `${upgradeCost} AP`,
        capped ? `At max range (${maxRange}).` : blockers({ cost: upgradeCost }) || `Range ${my.action_range} → ${my.action_range + 1}`,
        h('button', { class: 'small primary', disabled: capped || !!blockers({ cost: upgradeCost }), onclick: () => doAction({ type: 'upgrade' }) }, 'Upgrade')))
      : null,
    h('p', { class: 'muted small', style: { marginTop: '12px' } },
      'AP builds up over time; there are no turns. Spend it whenever you like. ',
      c.fog_of_war === 'alliance' ? 'Fog of war is on: only you can see your AP, and gifts are private.' : 'Everything, including AP and gifts, is public.'));
}

export function playersTab() {
  const my = me(), votes = voteTally();
  const note = (text) => h('span', { class: 'muted small' }, text);
  // Sorted by alive then name; never by AP (it may be hidden).
  const list = [...G.state.players].sort((a, b) => (b.alive - a.alive) || a.name.localeCompare(b.name));
  return h('ul', { class: 'plist' }, list.map((p) => h('li', {
    class: p.alive ? 'clickable' : 'dead', // living tanks open their action sheet
    onclick: () => { if (p.alive) selectPlayer(p); },
  },
    dot(p.color),
    h('span', { class: 'pname' }, p.name, p.id === my?.id ? ' (you)' : '',
      p.is_host ? note(' · host') : null,
      p.is_juror ? note(' · juror') : null,
      !p.alive && !p.is_juror ? note(' · destroyed') : null),
    h('span', { class: 'pstats' }, p.alive ? `♥${p.hp} · R${p.action_range}` : '',
      p.ap !== undefined && p.alive ? ` · ⚡${p.ap}` : '',
      votes[p.id] ? ` · ${votes[p.id]} vote(s)` : ''))));
}

const VISIBILITY_NOTE = { actor_only: 'only you can see this', participants: 'only you and the other player can see this' };

export function logTab() {
  const st = G.state, sel = G.nodes.logFilter;
  sel.replaceChildren(h('option', { value: '' }, 'All players'),
    ...st.players.map((p) => h('option', { value: p.id, selected: G.logFilter === p.id }, p.name)));
  const entries = st.log.filter((e) => !G.logFilter || involves(e, G.logFilter)).reverse().slice(0, 500);
  const cls = (e) => (e.event_type === 'kill' || e.event_type === 'game_over' ? 'kill' : e.event_type === 'ap_tick' ? 'tick' : '');
  G.nodes.logList.replaceChildren(...entries.map((e) => h('li', { class: cls(e) },
    h('time', {}, fmtTime(e.ts)),
    h('div', {}, describe(e),
      e.visibility !== 'public' ? h('div', { class: 'private' }, VISIBILITY_NOTE[e.visibility] || VISIBILITY_NOTE.participants) : null))));
  return h('div', { class: 'stack' }, sel, G.nodes.logList);
}

export function juryTab() {
  const st = G.state, my = me(), jury = st.jury, rules = st.game.config.jury;
  if (!jury) return h('p', {}, 'The jury is not enabled in this game.');
  const tally = voteTally();
  const myVote = jury.votes.find((v) => v.juror_id === my?.id)?.candidate_id;
  const isJuror = my?.is_juror && st.game.status === 'active';
  const voteButton = (p) => h('button', {
    class: myVote === p.id ? 'small primary' : 'small',
    onclick: () => postAndRefresh('vote', { candidate_id: p.id }),
  }, myVote === p.id ? 'Voted ✓' : 'Vote');
  return h('div', { class: 'stack' },
    h('p', {}, 'Each cycle, every juror (destroyed player) may vote for one living player. The most-voted player gets +1 AP',
      rules.tie_rule === 'all' ? ' (ties: everyone tied gets it).' : ' (ties: one random tied player gets it).'),
    st.game.status === 'active' ? h('p', {}, 'This cycle closes in ', h('b', { 'data-until': jury.closes_at }, '…'), '.') : null,
    scheduleSummary() ? h('p', { class: 'muted small' }, 'Cycles close on the AP schedule, right after each AP tick: ', scheduleSummary(), '.') : null,
    !rules.votes_public ? h('p', { class: 'muted small' }, 'Votes are secret in this game: you only see your own.') : null,
    h('ul', { class: 'plist' }, st.players.filter((p) => p.alive).map((p) => h('li', {},
      dot(p.color),
      h('span', { class: 'pname' }, p.name, p.id === my?.id ? ' (you)' : ''),
      h('span', { class: 'pstats' }, tally[p.id] ? `${tally[p.id]} vote(s)` : ''),
      isJuror ? voteButton(p) : null))),
    h('p', { class: 'muted small' }, isJuror ? 'You can change your vote until the cycle closes.' : my?.alive ? 'You can vote once your tank is destroyed.' : ''));
}
