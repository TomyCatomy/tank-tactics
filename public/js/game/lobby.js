// The lobby screen: invite links, players, chat and settings. On phones one panel shows at a time.
import { api, authConfig } from '../api.js';
import { configForm } from '../config-form.js';
import { $, dot, fill, h, helpTip, tabButtons } from '../dom.js';
import { copyRow, toast } from '../notify.js';
import { route } from '../router.js';
import { saved } from '../storage.js';
import { rejoinBlock } from './rejoin.js';
import { chatPanel, renderChat, scrollChatToEnd, unreadTotal } from './chat.js';
import { G, me, post, postAndRefresh, refresh } from './session.js';
import { render, switchSeat } from './view.js';

/** Build the lobby's panels once; later renders only refresh their contents. */
function buildLobby() {
  const id = G.state.game.id;
  const n = G.nodes = {
    players: h('div'),
    config: h('div'),
    discord: h('div', { class: 'discord-connect stack' }),
    start: h('div', { class: 'lobby-start' }),
    intro: h('p', { class: 'muted' }),
    seats: h('div'),
    chat: chatPanel(),
    lobbyTabs: h('div', { class: 'tabs lobby-tabs', role: 'tablist' }),
  };
  n.lobbyPanels = {
    overview: h('div', { class: 'card stack lobby-panel lobby-overview' },
      h('h2', {}, 'Lobby'),
      n.intro,
      copyRow('Invite link', `${location.origin}/#/join/${id}`),
      rejoinBlock(),
      n.start),
    players: h('div', { class: 'stack lobby-panel lobby-players' }, n.seats, h('div', { class: 'card' }, n.players)),
    chat: h('div', { class: 'card lobby-panel lobby-chat' }, h('h2', {}, 'Chat'), n.chat),
    settings: h('div', { class: 'card stack lobby-panel lobby-settings' }, h('h2', {}, 'Game settings'), n.discord, n.config),
  };
  const p = n.lobbyPanels;
  $('#app').replaceChildren(h('div', { class: 'lobby' },
    n.lobbyTabs,
    h('div', { class: 'stack lobby-col' }, p.overview, p.players, p.chat),
    p.settings));
}

export function renderLobby() {
  if (G.layout !== 'lobby') {
    G.layout = 'lobby';
    G.lobbyTab = 'overview';
    G.configKey = null;
    buildLobby();
  }
  const need = Math.max(2, G.state.game.config.players.minimum);
  G.nodes.intro.textContent = `Share the invite link. The host starts the game once everyone is in (at least ${need} players).`;
  renderPlayers();
  renderSeatControls(need);
  renderStart(need);
  renderSettings();
  renderDiscord();
  renderChat(); // also draws the tabs
}

export function renderLobbyTabs() {
  const tabs = [['overview', 'Lobby'], ['players', `Players (${G.state.players.length})`], ['chat', 'Chat'], ['settings', 'Settings']];
  const select = (k) => {
    G.lobbyTab = k;
    renderChat(); // clears unread if the chat just came into view, and redraws these tabs
    if (k === 'chat') scrollChatToEnd();
  };
  G.nodes.lobbyTabs.replaceChildren(...tabButtons(tabs, G.lobbyTab, select, unreadTotal()));
  for (const [k, panel] of Object.entries(G.nodes.lobbyPanels)) panel.classList.toggle('active', G.lobbyTab === k);
}

function renderPlayers() {
  const st = G.state, my = me(), isHost = !!my?.is_host;
  const mySeats = new Set(saved.seats(G.gid).map((x) => x.playerId));
  const remove = async (p) => {
    if (confirm(`Remove ${p.name} from the lobby?`)) { await post('remove', { player_id: p.id }); refresh(); }
  };
  fill(G.nodes.players, h('h2', {}, `Players (${st.players.length}/${st.game.config.players.maximum})`),
    h('ul', { class: 'plist' }, st.players.map((p) => h('li', {},
      dot(p.color),
      h('span', { class: 'pname' }, p.name, p.id === my?.id ? ' (you)' : '', p.is_host ? ' · host' : ''),
      p.id !== my?.id && mySeats.has(p.id) ? h('button', { class: 'small', onclick: () => switchSeat(p.id) }, 'Play as') : null,
      isHost && !p.is_host ? h('button', { class: 'small danger', onclick: () => remove(p) }, 'Remove') : null))));
}

/** Host: start or close the lobby. Others: leave it. */
function renderStart(need) {
  const count = G.state.players.length;
  const start = async () => {
    if (G.configDirty) { toast('Save or discard your settings changes first.', 'error'); return; }
    await postAndRefresh('start');
  };
  const close = async () => {
    if (confirm('Close this lobby? The game is cancelled for everyone and the invite link stops working.')) await postAndRefresh('close');
  };
  const leave = async () => {
    if (!confirm('Leave this lobby? You can rejoin with the invite link while it is open.')) return;
    const { gid, token } = G; // capture first: the socket's "you're out" message may re-route meanwhile
    if (!(await post('leave'))) return;
    if (saved.dropSeat(gid, token)) route(); // still have another local seat here
    else location.hash = '#/';
  };
  fill(G.nodes.start, me()?.is_host
    ? h('div', { class: 'stack' },
      h('button', { class: 'primary', disabled: count < need, onclick: start }, `Start game (${count}/${need}+ players)`),
      h('button', { class: 'danger', onclick: close }, 'Close lobby'))
    : h('div', { class: 'stack' },
      h('p', { class: 'muted' }, 'Waiting for the host to start the game…'),
      h('button', { class: 'danger', onclick: leave }, 'Leave lobby')));
}

/** The settings form, rebuilt only if the config changed and the host isn't mid-edit. */
function renderSettings() {
  const st = G.state, isHost = !!me()?.is_host;
  const key = JSON.stringify([st.game.config, st.allow_debug]);
  if (key === G.configKey || G.configDirty) return;
  G.configKey = key;
  const form = configForm(st.game.config, { editable: isHost, allowDebug: st.allow_debug, onDirty: () => { G.configDirty = true; } });
  const reset = () => { G.configDirty = false; G.configKey = null; };
  const save = async () => {
    if (await post('config', { config: form.getValues() })) { reset(); toast('Settings saved.', 'good'); refresh(); }
  };
  fill(G.nodes.config, form, isHost ? h('div', { class: 'row config-actions' },
    h('button', { class: 'primary', onclick: save }, 'Save settings'),
    h('button', { onclick: () => { reset(); render(); } }, 'Discard changes')) : null);
}

/**
 * Host: connect the lobby to a Discord channel. Discord shows a server and channel picker,
 * creates the webhook and sends the host back here (main.js shows how it went).
 */
async function renderDiscord() {
  const st = G.state, node = G.nodes.discord;
  const connected = !!st.game.config.notifications.discord_webhook_url; // only the host can see it
  const available = me()?.is_host && (connected || (await authConfig()).discord_connect);
  if (G.state !== st) return; // a newer render has run meanwhile
  if (!available) { fill(node); node.hidden = true; return; }
  node.hidden = false;
  // Leaving for Discord, or a config change, would lose unsaved edits in the form below.
  const editing = () => G.configDirty && (toast('Save or discard your settings changes first.', 'error'), true);
  const connect = async (ev) => {
    if (editing()) return;
    const btn = ev.currentTarget;
    btn.disabled = true;
    try {
      const res = await api('POST', `/api/games/${G.gid}/discord/connect`, { origin: location.origin }, G.token);
      location.assign(res.url);
    } catch (e) { toast(e.message, 'error'); btn.disabled = false; }
  };
  const disconnect = async () => {
    if (editing() || !confirm('Stop posting this game’s updates to Discord?')) return;
    if (await postAndRefresh('config', { config: { notifications: { discord_webhook_url: null } } })) toast('Disconnected from Discord.', 'good');
  };
  fill(node, connected
    ? h('div', { class: 'row' },
      h('span', {}, '✅ Connected to Discord: game updates are posted there.'),
      h('button', { class: 'small shrink', onclick: disconnect }, 'Disconnect'))
    : h('div', { class: 'row discord-row' },
      h('button', { class: 'small discord-button', onclick: connect }, 'Connect to Discord'),
      helpTip('Pick a Discord server and channel, and this game’s public updates (the start, AP handouts, kills and the winner) are posted there. Hidden moves never are.')));
}

/** Debug: join extra players from this browser (only when the game enables debug.local_seats). */
function renderSeatControls(need) {
  const st = G.state;
  if (!st.game.config.debug.local_seats) { fill(G.nodes.seats); return; }
  const full = st.players.length >= st.game.config.players.maximum;
  const taken = new Set(st.players.map((p) => p.name.toLowerCase()));
  const nextName = () => {
    for (let n = taken.size + 1; ; n++) {
      if (!taken.has(`player ${n}`)) { taken.add(`player ${n}`); return `Player ${n}`; }
    }
  };
  const add = (count) => async (ev) => {
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
    h('p', { class: 'muted small' }, 'Add players controlled from this browser, then switch between them with “Playing as” in the top bar.'),
    h('div', { class: 'row' },
      h('button', { disabled: full, onclick: add(1) }, 'Add player'),
      missing > 0 ? h('button', { disabled: full, onclick: add(missing) }, `Fill to minimum (+${missing})`) : null)));
}
