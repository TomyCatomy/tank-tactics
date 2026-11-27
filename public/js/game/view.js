// Top-level rendering of a game page: the top bar, then the lobby, match or closed screen.
import { $, fill, h, messageCard } from '../dom.js';
import { copyText, refreshSheet, toast } from '../notify.js';
import { route } from '../router.js';
import { saved } from '../storage.js';
import { renderLobby } from './lobby.js';
import { renderMatch } from './match.js';
import { G, updateCountdowns } from './session.js';

/** Redraw everything from G.state. Each screen keeps its long-lived nodes and refreshes their contents. */
export function render() {
  const status = G.state.game.status;
  document.title = `Tank Tactics · ${G.state.game.id}`;
  renderTopbar();
  if (status === 'lobby') renderLobby();
  else if (status === 'closed') renderClosed();
  else renderMatch();
  refreshSheet();
  updateCountdowns();
}

function renderClosed() {
  G.layout = 'closed';
  fill($('#app'), messageCard('This lobby was closed', 'The host cancelled this game before it started.', { back: 'Back to start', narrow: true }));
}

const STATUS_BADGES = {
  lobby: () => h('span', { class: 'badge warn' }, 'Lobby'),
  active: (alive) => h('span', { class: 'badge good' }, `${alive} alive`),
  finished: () => h('span', { class: 'badge' }, 'Finished'),
  closed: () => h('span', { class: 'badge bad' }, 'Closed'),
};

function renderTopbar() {
  const { game: g, players } = G.state, c = g.config;
  const copyCode = async () => {
    if (await copyText(g.id)) toast(`Game code ${g.id} copied.`, 'good', 1800);
    else toast(`Game code: ${g.id}`, 'info');
  };
  fill($('#topbar-game'), h('button', { class: 'game-code', type: 'button', title: 'Click to copy the game code', onclick: copyCode },
    h('span', { class: 'game-code-label' }, 'Game'), h('span', { class: 'game-code-id' }, g.id), h('span', { 'aria-hidden': 'true' }, '⧉')));
  fill($('#topbar-info'),
    seatSwitch(),
    STATUS_BADGES[g.status]?.(players.filter((p) => p.alive).length),
    c.fog_of_war === 'alliance' ? h('span', { class: 'badge warn', title: 'AP counts and gifts are hidden' }, 'Fog: alliance') : null,
    c.jury.enabled ? h('span', { class: 'badge' }, 'Jury on') : null,
    c.debug.fast_ticks ? h('span', { class: 'badge bad' }, 'Debug ticks') : null,
    c.debug.local_seats ? h('span', { class: 'badge bad', title: 'Players can control several tanks from one browser' }, 'Debug seats') : null);
  renderConnectionBadge(); // the fill above wiped it
}

/** Debug local seats: a "Playing as" picker when this browser controls several players. */
function seatSwitch() {
  const seats = saved.seats(G.gid);
  if (seats.length < 2) return null;
  const nameFor = (x) => G.state.players.find((p) => p.id === x.playerId)?.name ?? x.name ?? '?';
  return h('label', { class: 'seat-switch', title: 'Debug: switch which player you control' },
    h('span', {}, 'Playing as'),
    h('select', { onchange: (e) => switchSeat(e.target.value) },
      seats.map((x) => h('option', { value: x.playerId, selected: x.playerId === G.state.viewer_id }, nameFor(x)))));
}

/** Debug local seats: reopen the game as another player controlled from this browser. */
export function switchSeat(playerId) {
  saved.useSeat(G.gid, playerId);
  route();
}

/** Exactly one "reconnecting…" badge while the socket is down, however many retries fail. */
export function renderConnectionBadge() {
  const existing = document.getElementById('conn-badge');
  const show = !!(G && !G.stopped && G.reconnecting);
  if (show && !existing) $('#topbar-info').prepend(h('span', { id: 'conn-badge', class: 'badge warn', role: 'status' }, 'reconnecting…'));
  else if (!show && existing) existing.remove();
}
