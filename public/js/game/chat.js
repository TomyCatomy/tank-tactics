// Chat: the public channel and DMs between living players, with unread counts.
import { h, nameEl } from '../dom.js';
import { fmtTime, isolate, last, sum } from '../format.js';
import { notify } from '../notify.js';
import { renderLobbyTabs } from './lobby.js';
import { renderTabs } from './match.js';
import { G, byId, me, nameOf, postAndRefresh } from './session.js';

/** The channel a message is in: 'public', or the other player's id for a DM. */
const channelOf = (m, myId) => (m.to_id === null ? 'public' : (m.from_id === myId ? m.to_id : m.from_id));

/** Must match the phone-lobby media query in style.css. */
export const phoneLobby = window.matchMedia('(max-width: 899px), (max-height: 500px)');

/** Whether the chat panel is visible right now (the lobby shows it beside the rest except on phones). */
export function chatOnScreen() {
  if (G.layout === 'lobby') return !phoneLobby.matches || G.lobbyTab === 'chat';
  return G.tab === 'chat';
}

export const unreadTotal = () => sum(Object.values(G.unread));

/** Count new messages from others that aren't on screen, and notify about DMs. */
export function trackUnread(st) {
  for (const m of st.messages) {
    if (m.id <= G.lastMsgId || m.from_id === st.viewer_id) continue;
    const ch = channelOf(m, st.viewer_id);
    const viewing = chatOnScreen() && G.chat === ch && !document.hidden;
    if (viewing) continue;
    G.unread[ch] = (G.unread[ch] || 0) + 1;
    if (ch !== 'public') notify(`DM from ${nameOf(m.from_id)}: ${m.text.slice(0, 80)}`);
  }
  if (st.messages.length) G.lastMsgId = last(st.messages).id;
}

/** The chat panel's elements; renderChat() fills them. Lives for as long as its screen. */
export function chatPanel() {
  const channel = h('select', { 'aria-label': 'Channel', onchange: (e) => { G.chat = e.target.value; renderChat(); } });
  const list = h('ul', { class: 'chat-msgs' });
  const input = h('textarea', { rows: 2, maxlength: 1000, placeholder: 'Message…', dir: 'auto' });
  const send = async () => {
    const text = input.value.trim();
    if (text && await postAndRefresh('chat', { to_id: G.chat === 'public' ? null : G.chat, text })) input.value = '';
  };
  input.addEventListener('keydown', (e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); } });
  const root = h('div', { class: 'chat' }, channel, list, h('div', { class: 'row' }, input, h('button', { class: 'shrink primary', onclick: send }, 'Send')));
  root.parts = { channel, list, input };
  return root;
}

/** Scroll the chat to the newest message (after it comes into view: it can't scroll while hidden). */
export function scrollChatToEnd() {
  const { list } = G.nodes.chat.parts;
  list.scrollTop = list.scrollHeight;
}

export function renderChat() {
  const st = G.state, my = me(), { channel, list, input } = G.nodes.chat.parts;
  // Dead players keep access to the public channel only.
  const others = my?.alive ? st.players.filter((p) => p.id !== my.id && p.alive) : [];
  if (G.chat !== 'public' && !others.some((p) => p.id === G.chat)) G.chat = 'public';
  const option = (id, name) => h('option', { value: id, selected: G.chat === id }, `${name}${G.unread[id] ? ` (${G.unread[id]} new)` : ''}`);
  channel.replaceChildren(option('public', 'Public channel'), ...others.map((p) => option(p.id, `DM: ${isolate(p.name)}`)));
  if (chatOnScreen() && !document.hidden) delete G.unread[G.chat];

  const msgs = st.messages.filter((m) => channelOf(m, st.viewer_id) === G.chat);
  const atBottom = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  list.replaceChildren(...(msgs.length
    ? msgs.map((m) => h('li', { class: m.from_id === st.viewer_id ? 'mine' : '' },
      h('div', { class: 'who', style: { color: byId(m.from_id)?.color } }, byId(m.from_id) ? nameEl(byId(m.from_id).name) : 'someone', h('span', { class: 'when' }, fmtTime(m.ts))),
      h('div', { class: 'text', dir: 'auto' }, m.text)))
    : [h('li', { class: 'muted' }, G.chat === 'public' ? 'No messages yet. Say hi (or make threats).' : 'Private messages are only visible to the two of you.')]));
  if (atBottom || !list.dataset.init) { list.scrollTop = list.scrollHeight; list.dataset.init = '1'; }
  input.placeholder = G.chat === 'public' ? 'Message everyone…' : `Message ${nameOf(G.chat)} privately…`;

  // The unread counts show on the tabs too.
  if (G.layout === 'match') renderTabs();
  else if (G.layout === 'lobby') renderLobbyTabs();
}
