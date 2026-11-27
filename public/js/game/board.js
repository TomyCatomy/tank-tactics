// The SVG board. Tap a tank to open its action sheet, or a free tile next to yours to move.
import { h, sv } from '../dom.js';
import { adjacent, initials, isolate, neighbours } from '../format.js';
import { G, byId, me } from './session.js';
import { openMoveSheet, openSelfSheet, openTargetSheet } from './sheets.js';

/** Whether tank `p` takes up its tile (a wreck only does if the game says so). */
export const blocksTile = (p, config) => p.x >= 0 && (p.alive || config.board.wreckage_blocks_tile);

/** The tiles next to `p` that it could move onto. */
export function freeNeighbours(p, players, size, config) {
  return neighbours(p, size).filter(({ x, y }) => !players.some((q) => q.x === x && q.y === y && blocksTile(q, config)));
}

/** Select a living tank on the board and open its action sheet. */
export function selectPlayer(p) {
  G.selected = p.id;
  renderBoard();
  if (p.id === me()?.id) openSelfSheet(); else openTargetSheet(p.id);
}

function onTileTap(x, y) {
  const st = G.state, my = me();
  const tank = st.players.find((p) => p.alive && p.x === x && p.y === y);
  if (tank) return selectPlayer(tank);
  const free = !st.players.some((p) => p.x === x && p.y === y && blocksTile(p, st.game.config));
  if (my?.alive && st.game.status === 'active' && free && adjacent(my, { x, y })) return openMoveSheet(x, y);
  G.selected = null;
  renderBoard();
}

/** Live state, or a replay frame while the replay slider is away from the end. */
const boardModel = () => G.replay?.frame || { size: G.state.game.board_size, players: G.state.players };

const ring = (p, cls) => sv('rect', { class: cls, x: p.x + 0.06, y: p.y + 0.06, width: 0.88, height: 0.88, rx: 0.12 });

function tankShape(p, { maxHp, isMine, isSelected }) {
  const cx = p.x + 0.5;
  if (!p.alive) {
    return sv('g', { class: 'tank' }, sv('path', {
      class: 'wreck', d: `M${p.x + 0.25} ${p.y + 0.25}L${p.x + 0.75} ${p.y + 0.75}M${p.x + 0.75} ${p.y + 0.25}L${p.x + 0.25} ${p.y + 0.75}`,
    }));
  }
  const tag = (x, text, anchor, y = p.y + 0.14) => sv('text', { class: 'ap-tag', x, y, 'text-anchor': anchor }, text);
  // HP pips (at most 10; above that the number shows underneath).
  const pips = Math.max(p.hp, maxHp), shown = Math.min(pips, 10), w = Math.min(0.14, 0.7 / pips);
  const pipRects = Array.from({ length: shown }, (_, i) => sv('rect', {
    class: i < p.hp ? 'hp-pip' : 'hp-pip empty', x: cx - (shown * w) / 2 + i * w + 0.01, y: p.y + 0.72, width: w - 0.02, height: 0.1, rx: 0.02,
  }));
  return sv('g', { class: 'tank' },
    sv('title', {}, `${isolate(p.name)}: ${p.hp} HP, range ${p.action_range}${p.ap !== undefined ? `, ${p.ap} AP` : ''}`),
    sv('rect', { x: p.x + 0.14, y: p.y + 0.16, width: 0.72, height: 0.5, rx: 0.12, fill: p.color }),
    sv('text', { class: 'tank-label', x: cx, y: p.y + 0.52, 'text-anchor': 'middle' }, initials(p.name)),
    pipRects,
    p.hp > 10 ? tag(cx, `${p.hp}♥`, 'middle', p.y + 0.95) : null,
    tag(p.x + 0.1, `R${p.action_range}`),
    p.ap !== undefined ? tag(p.x + 0.9, `${p.ap}⚡`, 'end') : null,
    isMine ? ring(p, 'me-ring') : null,
    isSelected ? ring(p, 'sel-ring') : null);
}

export function renderBoard() {
  const st = G.state, config = st.game.config, { size: N, players } = boardModel(), my = me();
  const live = !G.replay?.frame;
  const onBoard = players.filter((p) => blocksTile(p, config));
  const sel = live ? (byId(G.selected) || (my?.alive ? my : null)) : null;

  const svg = sv('svg', { class: 'board', viewBox: `-0.6 -0.6 ${N + 0.7} ${N + 0.7}`, role: 'img', 'aria-label': `Board ${N} by ${N}` });
  const tiles = sv('g', { class: 'tiles' }), hl = sv('g', { class: 'hl' }), tanks = sv('g', { class: 'tanks' }), hits = sv('g', { class: 'hits' });

  for (let y = 0; y < N; y++) {
    for (let x = 0; x < N; x++) {
      tiles.append(sv('rect', { class: (x + y) % 2 ? 'tile alt' : 'tile', x: x + 0.03, y: y + 0.03, width: 0.94, height: 0.94, rx: 0.08 }));
    }
  }
  for (let i = 0; i < N; i++) {
    tiles.append(sv('text', { class: 'coord', x: i + 0.5, y: -0.18, 'text-anchor': 'middle' }, i),
      sv('text', { class: 'coord', x: -0.3, y: i + 0.6, 'text-anchor': 'middle' }, i));
  }

  // Range highlight of the selected tank; move targets for my own tank.
  if (sel?.alive) {
    // Range is straight-line distance, so the reach is a disc of tiles: one strip per row.
    const r = sel.action_range, mine = sel.id === my?.id;
    for (let y = Math.max(0, sel.y - r); y <= Math.min(N - 1, sel.y + r); y++) {
      const reach = Math.floor(Math.sqrt(r * r - (y - sel.y) ** 2));
      const x0 = Math.max(0, sel.x - reach), x1 = Math.min(N - 1, sel.x + reach);
      hl.append(sv('rect', { class: mine ? 'range' : 'range other', x: x0, y, width: x1 - x0 + 1, height: 1 }));
    }
    if (mine && st.game.status === 'active') {
      for (const { x, y } of freeNeighbours(sel, players, N, config)) {
        hl.append(sv('rect', { class: 'move-target', x: x + 0.12, y: y + 0.12, width: 0.76, height: 0.76, rx: 0.12 }));
      }
    }
  }

  for (const p of onBoard) {
    tanks.append(tankShape(p, {
      maxHp: config.start.hp,
      isMine: live && p.id === my?.id,
      isSelected: live && sel && p.id === sel.id && p.id !== my?.id,
    }));
  }

  // One transparent hit target for the whole board: simple, touch friendly, no hover needed.
  if (live) {
    hits.append(sv('rect', { class: 'tile-hit', x: 0, y: 0, width: N, height: N }));
    svg.addEventListener('click', (ev) => {
      const pt = svg.createSVGPoint();
      pt.x = ev.clientX; pt.y = ev.clientY;
      const loc = pt.matrixTransform(svg.getScreenCTM().inverse());
      const x = Math.floor(loc.x), y = Math.floor(loc.y);
      if (x >= 0 && y >= 0 && x < N && y < N) onTileTap(x, y);
    });
  }
  svg.append(tiles, hl, tanks, hits);

  const swatch = (style) => h('i', { style });
  const legend = h('div', { class: 'board-legend' },
    h('span', {}, swatch({ background: 'var(--range)', outline: '1px solid var(--accent)' }), 'your range'),
    h('span', {}, swatch({ background: 'var(--move)' }), 'tap to move (1 AP)'),
    h('span', {}, swatch({ background: 'var(--range-other)' }), 'selected tank’s range'),
    h('span', {}, 'R = range · ⚡ = AP · red pips = HP'));
  G.nodes.board.replaceChildren(svg, legend);
}
