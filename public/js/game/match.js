// The match screen (active or finished game): board on one side, tabs on the other.
import { $, h, tabButtons } from '../dom.js';
import { renderBoard } from './board.js';
import { chatPanel, renderChat, unreadTotal } from './chat.js';
import { renderGameOver } from './game-over.js';
import { G, me, updateCountdowns } from './session.js';
import { juryTab, logTab, meTab, playersTab } from './tabs.js';

function buildMatch() {
  const st = G.state, my = me();
  // A dead juror lands on the jury tab.
  if (my && !my.alive && st.game.config.jury.enabled && st.game.status === 'active') G.tab = 'jury';
  const n = G.nodes = {
    banner: h('div'),
    over: h('div'),
    board: h('div', { class: 'board-wrap' }),
    tabs: h('div', { class: 'tabs', role: 'tablist' }),
    body: h('div', { class: 'tab-body' }),
    chat: chatPanel(),
    logFilter: h('select', { onchange: (e) => { G.logFilter = e.target.value; renderTab(); } }),
    logList: h('ul', { class: 'log' }),
  };
  $('#app').replaceChildren(h('div', { class: 'game' },
    h('div', {}, n.over, n.banner, n.board),
    h('aside', { class: 'side' }, n.tabs, n.body)));
}

export function renderMatch() {
  if (G.layout !== 'match') {
    G.layout = 'match';
    buildMatch();
  }
  renderBanner();
  renderGameOver();
  renderBoard();
  renderTabs();
  renderTab();
}

function renderBanner() {
  const st = G.state, my = me();
  let banner = null;
  if (!my) {
    banner = h('div', { class: 'banner' }, 'You are watching this game.');
  } else if (st.game.status === 'active' && !my.alive) {
    banner = h('div', { class: 'banner dead' },
      h('b', {}, 'Your tank was destroyed. '),
      st.game.config.jury.enabled
        ? 'You are now a juror: vote each cycle to give a living player +1 AP (see the Jury tab).'
        : 'You are a spectator: you can watch the board and log, and use the public chat.');
  }
  G.nodes.banner.replaceChildren(...(banner ? [banner] : []));
}

const TABS = { me: meTab, players: playersTab, log: logTab, jury: juryTab };

function tabList() {
  const tabs = [['me', 'Me'], ['players', 'Players'], ['log', 'Log'], ['chat', 'Chat']];
  if (G.state.game.config.jury.enabled) tabs.push(['jury', 'Jury']);
  return tabs;
}

export function renderTabs() {
  const tabs = tabList();
  if (!tabs.some(([k]) => k === G.tab)) G.tab = 'me';
  G.nodes.tabs.replaceChildren(...tabButtons(tabs, G.tab, showTab, unreadTotal()));
}

export function renderTab() {
  if (G.tab === 'chat') {
    // Re-inserting the panel on every state push would blur the message box (and close a phone's keyboard).
    if (G.nodes.chat.parentNode !== G.nodes.body) G.nodes.body.replaceChildren(G.nodes.chat);
    renderChat();
  } else {
    G.nodes.body.replaceChildren((TABS[G.tab] || meTab)());
  }
  updateCountdowns();
}

export function showTab(tab) {
  G.tab = tab;
  renderTabs();
  renderTab();
}

/** Open the DM channel with player `id` in the chat tab. */
export function openDm(id) {
  G.chat = id;
  showTab('chat');
}
