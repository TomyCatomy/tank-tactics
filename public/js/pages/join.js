// The join page for an invite link: #/join/<game id>.
import { accountApi, api, authConfig } from '../api.js';
import { claimSeat } from '../auth.js';
import { $, h, messageCard } from '../dom.js';
import { account, saved } from '../storage.js';
import { friendlyWarning, nameInput, spinner } from './shared.js';

export async function renderJoin(gid) {
  const app = $('#app');
  if (saved.get(gid)?.token) { location.hash = `#/g/${gid}`; return; }
  app.replaceChildren(spinner());
  // Signed in and already seated in this game (e.g. from another device): just open it.
  if (account.token() && (await authConfig()).google_client_id) {
    try { await claimSeat(gid); location.hash = `#/g/${gid}`; return; } catch { /* not seated here */ }
  }
  let info;
  try {
    info = await api('GET', `/api/games/${encodeURIComponent(gid)}`);
  } catch (e) {
    app.replaceChildren(messageCard('Game not found', h('p', {}, e.message)));
    return;
  }
  if (info.status === 'closed') {
    app.replaceChildren(messageCard('This lobby was closed', 'The host cancelled this game before it started.'));
    return;
  }
  if (info.status !== 'lobby') {
    app.replaceChildren(messageCard('This game has already started',
      'New players can only join before the host starts the game. If you are already in it, open your personal rejoin link.'));
    return;
  }

  const nameIn = nameInput(account.get()?.user, { required: true });
  const err = h('div', { class: 'error-text' });
  const onsubmit = async (ev) => {
    ev.preventDefault();
    err.textContent = '';
    try {
      const res = await accountApi('POST', `/api/games/${gid}/join`, { name: nameIn.value });
      saved.set(gid, { token: res.token, playerId: res.player_id, name: nameIn.value.trim() });
      location.hash = `#/g/${gid}`;
    } catch (e) { err.textContent = e.message; }
  };
  app.replaceChildren(h('form', { class: 'card stack', style: { maxWidth: '480px', margin: '0 auto' }, onsubmit },
    h('h2', {}, `Join ${info.host}’s game`),
    h('p', { class: 'muted' }, `${info.player_count} / ${info.max_players} players so far.`),
    friendlyWarning(),
    nameIn, err, h('button', { class: 'primary', type: 'submit' }, 'Join game')));
  nameIn.focus();
}
