// The game-over banner: winners, final table and a replay of the board.
import { dot, h, nameEl } from '../dom.js';
import { fmtTime } from '../format.js';
import { renderBoard } from './board.js';
import { describe } from './events.js';
import { G, me, nameOf } from './session.js';

const END_REASONS = {
  last_standing: 'Last tank standing.',
  time_limit: 'Time limit reached (HP, then AP, then range).',
  simultaneous: 'The last tanks fell together.',
};

/**
 * Board snapshots rebuilt from the (viewer-visible) log, one per board-changing entry.
 * HP changes from gifts you couldn't see are corrected at the next public shot via target_hp_after.
 */
function replayFrames() {
  const st = G.state, start = st.log.find((e) => e.event_type === 'game_start');
  if (!start) return [];
  const d = start.details;
  const ps = new Map(st.players.map((p) => [p.id, {
    ...p, ap: undefined, alive: true, hp: d.hp, action_range: d.action_range,
    x: d.positions[p.id]?.[0] ?? -1, y: d.positions[p.id]?.[1] ?? -1,
  }]));
  const snap = (entry) => ({ size: d.board_size, players: [...ps.values()].map((p) => ({ ...p })), entry });
  const frames = [snap(start)];
  for (const e of st.log) {
    if (e.id <= start.id) continue;
    const a = ps.get(e.actor_id), t = ps.get(e.target_id);
    switch (e.event_type) {
      case 'move': [a.x, a.y] = e.to_xy; break;
      case 'shoot': t.hp = e.details.target_hp_after; break;
      case 'kill': case 'removed': t.alive = false; t.hp = 0; break;
      case 'upgrade': a.action_range = e.amount; break;
      case 'give_hp': a.hp -= e.amount; t.hp += e.amount; break;
      case 'game_over': break;
      default: continue; // doesn't change the board
    }
    frames.push(snap(e));
  }
  return frames;
}

/** Slider + play button over the frames. Moving off the last frame shows that frame on the board. */
function replayControls(frames) {
  const end = frames.length - 1;
  const slider = h('input', { type: 'range', min: 0, max: end, value: end, 'aria-label': 'Replay position' });
  const caption = h('div', { class: 'muted small' });
  const play = h('button', { class: 'small', onclick: () => (G.replay.timer ? pause() : resume()) }, '▶ Play');
  const show = (i) => {
    const f = frames[i];
    G.replay.frame = i === end ? null : f;
    caption.textContent = `${fmtTime(f.entry.ts)} · ${describe(f.entry)}`;
    renderBoard();
  };
  const pause = () => { clearInterval(G.replay.timer); G.replay.timer = null; play.textContent = '▶ Play'; };
  const resume = () => {
    if (Number(slider.value) >= end) slider.value = 0;
    play.textContent = '❚❚ Pause';
    G.replay.timer = setInterval(() => {
      const i = Number(slider.value) + 1;
      if (i > end) return pause();
      slider.value = i;
      show(i);
    }, 400);
  };
  slider.addEventListener('input', () => show(Number(slider.value)));
  return h('div', {}, h('h3', {}, 'Replay'), h('div', { class: 'replay-controls' }, play, slider), caption);
}

export function renderGameOver() {
  const st = G.state, node = G.nodes.over;
  if (st.game.status !== 'finished') { node.replaceChildren(); return; }
  if (node.childElementCount) return; // static once rendered (keeps the replay controls alive)
  const winners = st.game.winner_ids;
  const rows = [...st.players].sort((a, b) => (b.alive - a.alive) || ((b.died_at || 0) - (a.died_at || 0)));
  const result = (p) => (winners.includes(p.id) ? 'Winner' : p.alive ? 'Survived' : `Destroyed ${fmtTime(p.died_at)}`);
  const frames = replayFrames();
  G.replay = { frame: null, timer: null };

  node.replaceChildren(h('div', { class: 'banner win stack' },
    h('h2', {}, winners.includes(me()?.id) ? '🏆 You won!' : `🏆 ${winners.map(nameOf).join(' & ')} won`),
    h('p', { class: 'muted' }, END_REASONS[st.game.end_reason] || ''),
    h('table', { class: 'final-table' },
      h('thead', {}, h('tr', {}, h('th', {}, 'Player'), h('th', {}, 'Result'), h('th', {}, 'HP'), h('th', {}, 'Range'))),
      h('tbody', {}, rows.map((p) => h('tr', {},
        h('td', {}, dot(p.color), ' ', nameEl(p.name)),
        h('td', {}, result(p)),
        h('td', {}, p.hp), h('td', {}, p.action_range))))),
    frames.length > 1 ? replayControls(frames) : null,
    h('a', { href: '#/' }, 'Back to start')));
}
