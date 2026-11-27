// The open game: its WebSocket, the latest per-viewer state from the server, and UI state.
// The server is the source of truth: the client only renders what it sends and posts intents.
import { api } from '../api.js';
import { $, h, messageCard } from '../dom.js';
import { fmtDur, isolate, last } from '../format.js';
import { notify, toast } from '../notify.js';
import { route } from '../router.js';
import { saved } from '../storage.js';
import { trackUnread } from './chat.js';
import { notificationFor } from './events.js';
import { render, renderConnectionBadge } from './view.js';

/** The current game session, or null outside a game page. Other modules read it, only this one replaces it. */
export let G = null;

export function startGame(gid) {
  const creds = saved.get(gid);
  if (!creds?.token) { location.hash = `#/join/${gid}`; return; }
  G = {
    gid, token: creds.token,
    state: null,        // the latest view from the server
    offset: 0,          // server clock minus ours, in seconds
    ws: null, retry: 0, reconnecting: false, stopped: false,
    resetting: false,   // a link reset is in flight: the server is about to drop this socket
    layout: null,       // 'lobby' | 'match' | 'closed': which screen is built
    nodes: null,        // that screen's long-lived elements
    tab: 'me', lobbyTab: 'overview', selected: null, logFilter: '', replay: null,
    chat: 'public', unread: {}, lastLogId: null, lastMsgId: null,
    configDirty: false, configKey: null,
  };
  $('#app').replaceChildren(h('div', { class: 'spinner' }, 'Connecting…'));
  connect(G);
  G.timer = setInterval(updateCountdowns, 1000);
}

export function stopGame() {
  if (!G) return;
  G.stopped = true;
  clearInterval(G.timer);
  clearInterval(G.replay?.timer);
  try { G.ws?.close(); } catch { }
  G = null;
}

function connect(session) {
  const proto = location.protocol === 'https:' ? 'wss' : 'ws';
  const ws = new WebSocket(`${proto}://${location.host}/ws/${encodeURIComponent(session.gid)}`);
  session.ws = ws;
  ws.onopen = () => ws.send(JSON.stringify({ type: 'auth', token: session.token }));
  ws.onmessage = (ev) => {
    if (session.stopped || session.ws !== ws) return;
    let msg;
    try { msg = JSON.parse(ev.data); } catch { return; }
    if (msg.type === 'state') {
      session.retry = 0;
      session.reconnecting = false;
      onState(msg.state);
      renderConnectionBadge();
    } else if (msg.type === 'error') {
      if (session.resetting) return; // our own reset closing this socket; replaceToken reconnects
      session.stopped = true;
      if (saved.dropSeat(session.gid, session.token)) { route(); return; } // continue as another local seat
      $('#topbar-game').replaceChildren();
      $('#topbar-info').replaceChildren();
      $('#app').replaceChildren(messageCard('Can’t open this game', h('p', {}, msg.error.message), { back: 'Back to start' }));
    }
  };
  ws.onclose = () => {
    if (session.stopped || session.ws !== ws || session.resetting) return;
    const wait = Math.min(15000, 500 * 2 ** session.retry++);
    session.reconnecting = true;
    renderConnectionBadge();
    setTimeout(() => { if (!session.stopped) connect(session); }, wait);
  };
}

/**
 * Switch a session to a new seat token (after a link reset): remember it on this device, then, if
 * the session is still open, reconnect with it. The old socket is ignored from here on.
 */
export function replaceToken(session, token) {
  saved.replaceToken(session.gid, session.token, token);
  session.token = token;
  if (session === G) reconnectNow(session);
}

/** Drop the session's socket (if any) and open a fresh one with its current token. */
export function reconnectNow(session) {
  const old = session.ws;
  session.ws = null;
  session.resetting = false;
  session.retry = 0;
  try { old?.close(); } catch { }
  if (!session.stopped) connect(session);
}

/** Re-fetch over HTTP (used right after an action so the UI never waits on the socket). */
export async function refresh() {
  if (!G) return;
  try { onState(await api('GET', `/api/games/${G.gid}/state`, undefined, G.token)); } catch { }
}

/** POST to this game as the current seat; errors become a toast. Returns whether it worked. */
export async function post(path, body) {
  try {
    await api('POST', `/api/games/${G.gid}/${path}`, body, G.token);
    return true;
  } catch (e) {
    toast(e.message, 'error');
    return false;
  }
}

/** post(), then refresh() if it worked. */
export async function postAndRefresh(path, body) {
  const ok = await post(path, body);
  if (ok) refresh();
  return ok;
}

// ----- Player lookups (in the latest state)

export const byId = (id) => G.state.players.find((p) => p.id === id);
export const me = () => byId(G.state.viewer_id);
export const nameOf = (id) => { const p = byId(id); return p ? isolate(p.name) : 'someone'; };
export const isMe = (p) => p.id === G.state.viewer_id;

// ----- Incoming state

function onState(st) {
  if (!G) return;
  const prev = G.state;
  // Ignore stale snapshots (HTTP refresh and socket push can race).
  if (prev && st.log.length && prev.log.length && last(st.log).id < last(prev.log).id) return;
  G.state = st;
  G.offset = st.server_now - Date.now() / 1000;
  const creds = saved.get(G.gid) || {};
  if (!creds.playerId || !creds.name) {
    saved.set(G.gid, { ...creds, token: G.token, playerId: st.viewer_id, name: me()?.name });
  }
  if (prev) {
    for (const e of st.log) {
      const n = e.id > (G.lastLogId ?? Infinity) && notificationFor(e, st.viewer_id);
      if (n) notify(...n);
    }
    trackUnread(st);
  } else {
    G.lastMsgId = st.messages.length ? last(st.messages).id : 0;
  }
  G.lastLogId = st.log.length ? last(st.log).id : 0;
  render();
}

/** Refresh every `data-until` countdown (epoch seconds, in server time). */
export function updateCountdowns() {
  if (!G?.state) return;
  const now = Date.now() / 1000 + G.offset;
  for (const el of document.querySelectorAll('[data-until]')) {
    const t = Number(el.dataset.until);
    el.textContent = t - now > 0 ? fmtDur(t - now) : 'any moment';
  }
}
